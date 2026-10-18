"""Signed Upstash Workflow ingress and a database-backed dispatch adapter."""

from __future__ import annotations

import asyncio
import hmac
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from qstash.asyncio.client import AsyncQStash
from qstash.receiver import Receiver
from sqlalchemy import select
from sqlalchemy.orm import Session
from upstash_workflow.fastapi import Serve

from call_intelligence_api.auth import domain_engine
from call_intelligence_api.call_models import OutboxEvent
from call_intelligence_api.indexing import indexing_stage
from call_intelligence_api.processing import (
    analysis_stage,
    process_call,
    transcribe_stage,
)


class WorkflowSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    upstash_workflow_url: str
    qstash_token: SecretStr
    qstash_current_signing_key: SecretStr
    qstash_next_signing_key: SecretStr
    processing_service_secret: SecretStr
    processing_public_url: str | None = None


@lru_cache
def settings() -> WorkflowSettings:
    return WorkflowSettings()


WORKFLOW_PATH = "/api/v1/internal/workflow/call"


def workflow_urls(config: WorkflowSettings) -> tuple[str | None, str | None]:
    """(base_url, url) for Serve. Explicit, because the SDK otherwise reads UPSTASH_WORKFLOW_URL
    (our QStash API host) as the endpoint's public base and sends every step there (404)."""
    if not config.processing_public_url:
        return None, None  # ponytail: local dev runs process_call in-process; no QStash delivery arrives
    base = config.processing_public_url.rstrip("/")
    return base, base + WORKFLOW_PATH


class WorkPayload(BaseModel):
    call_id: UUID
    generation: int


def register_workflow(app: FastAPI) -> None:
    config = settings()
    service = Serve(app)
    client = AsyncQStash(
        token=config.qstash_token.get_secret_value(),
        base_url=config.upstash_workflow_url,
    )
    receiver = Receiver(
        current_signing_key=config.qstash_current_signing_key.get_secret_value(),
        next_signing_key=config.qstash_next_signing_key.get_secret_value(),
    )

    base_url, url = workflow_urls(config)

    @service.post(
        WORKFLOW_PATH,
        qstash_client=client,
        receiver=receiver,
        base_url=base_url,
        url=url,
        retries=2,
    )
    async def process(context) -> None:
        provided = context.headers.get("authorization", "")
        expected = "Bearer " + config.processing_service_secret.get_secret_value()
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=403, detail="Workflow service authorization failed")
        payload = WorkPayload.model_validate(context.request_payload)
        async def transcription_result() -> str:
            result = await asyncio.to_thread(transcribe_stage, payload.call_id, payload.generation)
            return str(result) if result else "already_complete"

        async def analysis_result() -> str:
            result = await asyncio.to_thread(analysis_stage, payload.call_id, payload.generation)
            return str(result) if result else "already_complete"

        async def indexing_result() -> str:
            result = await asyncio.to_thread(indexing_stage, payload.call_id, payload.generation)
            return str(result) if result else "already_complete"

        await context.run(
            "transcription",
            transcription_result,
        )
        analysis_error = None
        try:
            await context.run("analysis", analysis_result)
        except Exception as exc:  # noqa: BLE001  # indexing must still run after analysis failure
            analysis_error = exc
        await context.run("indexing", indexing_result)
        if analysis_error:
            raise analysis_error
        await context.run("complete", lambda: asyncio.to_thread(process_call, payload.call_id, payload.generation))


def mark_dispatched(event_id: UUID, dispatch_id: str) -> None:
    with Session(domain_engine()) as session, session.begin():
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update())
        if event and event.state == "pending":
            event.state = "dispatched"
            event.dispatch_id = dispatch_id
            event.dispatched_at = datetime.now(UTC)
            event.attempts += 1
