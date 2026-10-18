"""Authenticated, content-free configuration status for the Connections screen."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text

from call_intelligence_api.auth import (
    AuthenticatedMember,
    domain_engine,
    get_current_member,
)

router = APIRouter(prefix="/api/v1", tags=["configuration"])


class ConfigFlags(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    upstash_vector_rest_url: str | None = None
    upstash_vector_rest_token: str | None = None
    qstash_token: str | None = None
    qstash_current_signing_key: str | None = None
    qstash_next_signing_key: str | None = None
    processing_public_url: str | None = None
    sentry_dsn: str | None = None
    google_maps_api_key: str | None = None
    google_address_validation_enabled: bool = False


class ConnectionStatus(BaseModel):
    name: str
    state: str
    note: str


class ConnectionsResponse(BaseModel):
    items: list[ConnectionStatus]
    request_id: UUID


def _neon_state() -> str:
    try:
        with domain_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001  # status only; never export database error details
        return "unavailable"
    return "connected"


@router.get("/connections", response_model=ConnectionsResponse)
def connections(_member: Annotated[AuthenticatedMember, Depends(get_current_member)]) -> ConnectionsResponse:
    flags = ConfigFlags()
    return ConnectionsResponse(items=[
        ConnectionStatus(name="Neon", state=_neon_state(), note="Separate auth and domain databases"),
        ConnectionStatus(name="Cloudflare R2", state="configured" if flags.r2_access_key_id and flags.r2_secret_access_key else "missing", note="Private call audio"),
        ConnectionStatus(name="OpenAI", state="configured" if flags.openai_api_key else "missing", note="Transcription, assessment, embeddings and answers"),
        ConnectionStatus(name="Upstash Vector", state="configured" if flags.upstash_vector_rest_url and flags.upstash_vector_rest_token else "missing", note="Versioned transcript search"),
        ConnectionStatus(name="Upstash Workflow", state="public_url_needed" if flags.qstash_token and flags.qstash_current_signing_key and flags.qstash_next_signing_key and not flags.processing_public_url else "configured" if flags.processing_public_url else "missing", note="Local outbox worker runs without a public deployment URL"),
        ConnectionStatus(name="Sentry API", state="configured" if flags.sentry_dsn else "missing", note="Content-free error metadata"),
        ConnectionStatus(name="Google Address Validation", state="configured" if flags.google_address_validation_enabled and flags.google_maps_api_key else "not_connected", note="Checks spoken address components; results need reviewer confirmation" if flags.google_address_validation_enabled and flags.google_maps_api_key else "Address checks are optional and unavailable without a key"),
        ConnectionStatus(name="External CRM", state="not_connected", note="Local contacts, pipeline and tasks remain available"),
    ], request_id=uuid4())
