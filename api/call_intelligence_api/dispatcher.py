"""Reconcile persisted upload work into signed Workflow or one local development worker."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from qstash import QStash
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from call_intelligence_api.auth import domain_engine
from call_intelligence_api.call_models import (
    CallRecord,
    OutboxEvent,
    ProcessingStep,
    ReviewEvent,
)
from call_intelligence_api.deletion import purge_due_once
from call_intelligence_api.limits import budget_exhausted
from call_intelligence_api.processing import failure_code, process_call
from call_intelligence_api.workflow import settings, workflow_urls

logger = logging.getLogger(__name__)
POLL_SECONDS = 8
MAX_DISPATCH_ATTEMPTS = 2
MAX_IN_FLIGHT = 2  # one small API instance: "Process all" works through calls two at a time
# ponytail: a hand-off whose workflow died silently frees its slot after this; a failure callback would free it sooner
IN_FLIGHT_EXPIRY = timedelta(minutes=30)


def dedup_id(call_id, generation: int, retries: int = 0) -> str:
    """QStash deduplication id; QStash rejects ':' in it (HTTP 400). QStash drops a repeated id for
    10 minutes, so each manual retry gets its own id while automatic re-publishes stay deduplicated."""
    return f"call-{call_id}-generation-{generation}-retry-{retries}"


def dispatch_verdict(call: CallRecord | None, event: OutboxEvent) -> str:
    """What to do with an event whose hand-off used every attempt without a delivery id."""
    if call is None or call.deletion_status != "active" or call.requested_generation != event.generation:
        return "superseded"
    if call.processing_status == "queued":
        return "call_failed"  # never started: show it, so Process can restart it
    return "leave" if call.processing_status == "running" else "close"


def awaits_slot(event: OutboxEvent, in_flight: int) -> bool:
    """A new hand-off waits for a free slot; re-publishing one already delivered does not add load."""
    return event.dispatch_id is None and in_flight >= MAX_IN_FLIGHT


def in_flight_count(session: Session, now: datetime) -> int:
    """Delivered hand-offs whose call is still being transcribed or analyzed."""
    return session.scalar(select(func.count()).select_from(OutboxEvent).join(CallRecord, CallRecord.id == OutboxEvent.call_id).where(
        OutboxEvent.state == "dispatched",
        OutboxEvent.dispatch_id.is_not(None),
        OutboxEvent.dispatched_at > now - IN_FLIGHT_EXPIRY,
        CallRecord.deletion_status == "active",
        CallRecord.requested_generation == OutboxEvent.generation,
        CallRecord.processing_status.in_(["queued", "running"]),
    )) or 0


def surface_exhausted_dispatches(session: Session, now: datetime) -> None:
    exhausted = session.scalars(select(OutboxEvent).where(
        OutboxEvent.state.in_(["pending", "dispatched"]),
        OutboxEvent.attempts >= MAX_DISPATCH_ATTEMPTS,
        OutboxEvent.dispatch_id.is_(None),
        OutboxEvent.dispatched_at < now - timedelta(minutes=2),
    ).with_for_update(skip_locked=True).limit(20)).all()
    for event in exhausted:
        call = session.get(CallRecord, event.call_id)
        verdict = dispatch_verdict(call, event)
        if verdict == "superseded":
            event.state = "superseded"
        elif verdict == "call_failed":
            event.state = "failed"
            call.processing_status = "failed"
            call.error_code = "dispatch_failed"
            logger.warning("call_dispatch_exhausted event_id=%s", event.id)
        elif verdict == "close":
            event.state = "failed"


def dispatch_once() -> bool:
    """Reserve one due event, then run or publish outside its transaction."""
    now = datetime.now(UTC)
    with Session(domain_engine()) as session, session.begin():
        surface_exhausted_dispatches(session, now)
        events = session.scalars(select(OutboxEvent).where(
            OutboxEvent.state.in_(["pending", "dispatched"]),
            OutboxEvent.attempts < MAX_DISPATCH_ATTEMPTS,
        ).order_by(OutboxEvent.created_at, OutboxEvent.id).with_for_update(skip_locked=True).limit(20)).all()
        reason = budget_exhausted(session) if events else None
        if reason:
            logger.warning("call_dispatch_deferred reason=%s", reason)  # events stay pending until the budget resets
            return False
        selected = None
        in_flight = in_flight_count(session, now) if events else 0
        for event in events:
            call = session.get(CallRecord, event.call_id)
            if call is None or call.deletion_status != "active" or call.requested_generation != event.generation:
                event.state = "superseded"
                continue
            if event.dispatched_at and event.dispatched_at > now - timedelta(minutes=2 * event.attempts):
                continue
            if awaits_slot(event, in_flight):
                continue
            running = session.scalar(select(ProcessingStep.id).where(
                ProcessingStep.call_id == event.call_id,
                ProcessingStep.generation == event.generation,
                ProcessingStep.state == "running",
                ProcessingStep.lease_until > now,
            ).limit(1))
            if running:
                continue
            selected = event
            event.state = "dispatched"
            event.dispatched_at = now
            event.attempts += 1
            break
        if selected is None:
            return False
        call_id, generation, event_id = selected.call_id, selected.generation, selected.id
        retries = session.scalar(select(func.count()).select_from(ReviewEvent).where(
            ReviewEvent.call_id == call_id, ReviewEvent.kind == "manual_retry", ReviewEvent.new_version == generation,
        )) or 0

    config = settings()
    try:
        if config.processing_public_url:
            result = QStash(config.qstash_token.get_secret_value(), base_url=config.upstash_workflow_url).message.publish_json(
                url=workflow_urls(config)[1],
                body={"call_id": str(call_id), "generation": generation},
                headers={"Authorization": "Bearer " + config.processing_service_secret.get_secret_value()},
                deduplication_id=dedup_id(call_id, generation, retries),
                retries=2,
                timeout=300,
            )
            with Session(domain_engine()) as session, session.begin():
                event = session.get(OutboxEvent, event_id)
                if event and event.state == "dispatched":
                    event.dispatch_id = str(result.message_id)
        else:
            process_call(call_id, generation)
        return True
    except Exception as exc:  # noqa: BLE001  # durable outbox retains retry state across provider errors
        # A retry remains visible in the database. Never log request bodies or provider responses.
        logger.warning("call_dispatch_failed event_id=%s error_code=%s", event_id, failure_code(exc))
        return False


async def reconcile_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(dispatch_once)
            await asyncio.to_thread(purge_due_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  # keep reconciliation alive after transient failures
            logger.warning("call_reconcile_failed error_code=%s", type(exc).__name__)
        await asyncio.sleep(POLL_SECONDS)
