"""Owner tombstones calls immediately; reconciler removes private copies after workers drain."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from call_intelligence_api.auth import AuthenticatedMember, domain_engine, require_owner
from call_intelligence_api.call_models import (
    AnalysisRun,
    AudioAsset,
    CallIndexChunk,
    CallRecord,
    ChatMessage,
    ChatThread,
    CriterionDecision,
    IndexManifest,
    OutboxEvent,
    ProcessingStep,
    PurgeRecord,
    ReviewEvent,
    TranscriptSegment,
    TranscriptVersion,
)
from call_intelligence_api.crm_models import (
    AddressCheck,
    CallLink,
    ContactAddress,
    FollowUp,
    PipelineEvent,
    RetentionCase,
)
from call_intelligence_api.indexing import vector_client
from call_intelligence_api.storage import r2_client, r2_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["privacy"])
Owner = Annotated[AuthenticatedMember, Depends(require_owner)]


class DeleteCallResponse(BaseModel):
    call_id: UUID
    deletion_status: str
    deletion_job_id: UUID | None
    request_id: UUID


class StoreProgress(BaseModel):
    store: Literal["r2", "vector", "database"]
    state: Literal["pending", "complete"]


class DeletionJobResponse(BaseModel):
    id: UUID
    call_id: UUID
    state: str
    attempts: int
    created_at: datetime
    completed_at: datetime | None
    stores: list[StoreProgress]
    request_id: UUID


@router.delete("/calls/{call_id}", response_model=DeleteCallResponse, status_code=202)
def delete_call(call_id: UUID, owner: Owner) -> DeleteCallResponse:
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if call is None or call.organization_id != owner.organization_id:
            raise HTTPException(status_code=404, detail="Call not found")
        if call.deletion_status != "active":
            job_id = session.scalar(select(PurgeRecord.id).where(PurgeRecord.call_id == call_id))
            return DeleteCallResponse(call_id=call_id, deletion_status=call.deletion_status,
                                      deletion_job_id=job_id, request_id=uuid4())
        assets = session.scalars(select(AudioAsset.object_key).where(AudioAsset.call_id == call_id)).all()
        keys = sorted({*assets, *([call.original_audio_key] if call.original_audio_key else []),
                       *([call.staging_key] if call.staging_key else [])})
        job = PurgeRecord(id=uuid4(), organization_id=owner.organization_id, call_id=call_id, object_keys=keys)
        session.add(job)
        call.deletion_status = "pending"
        call.processing_status = "deleted"
        call.analysis_status = "deleted"
        call.index_status = "deleted"
        call.requested_generation += 1
        for event in session.scalars(select(OutboxEvent).where(OutboxEvent.call_id == call_id)).all():
            if event.state != "complete":
                event.state = "superseded"
        return DeleteCallResponse(call_id=call_id, deletion_status="pending", deletion_job_id=job.id, request_id=uuid4())


def store_progress(ledger_state: str) -> list[StoreProgress]:
    """Derive per-store progress from PurgeRecord.state (the ledger records no per-store columns).

    - pending: nothing purged yet, so r2, vector and database are all pending.
    - rechecking: the first pass deleted R2 objects and vectors and purged database content, so all
      three report complete; the ledger keeps re-deleting late vectors from stale workers until done.
    - complete: all stores complete.
    """
    done = ledger_state in {"rechecking", "complete"}
    return [StoreProgress(store=store, state="complete" if done else "pending") for store in ("r2", "vector", "database")]


@router.get("/deletions/{job_id}", response_model=DeletionJobResponse)
def get_deletion(job_id: UUID, owner: Owner) -> DeletionJobResponse:
    with Session(domain_engine()) as session:
        job = session.get(PurgeRecord, job_id)
        if job is None or job.organization_id != owner.organization_id:
            raise HTTPException(status_code=404, detail="Deletion job not found")
        return DeletionJobResponse(id=job.id, call_id=job.call_id, state=job.state, attempts=job.attempts,
                                   created_at=job.created_at, completed_at=job.completed_at,
                                   stores=store_progress(job.state), request_id=uuid4())


def thread_uses_call(thread_call_id: UUID | None, selected_call_ids: list | None,
                     message_sources: list[list | None], call_id: UUID) -> bool:
    """A thread is purged with a call if it is that call's thread, selected it, or any answer drew on it."""
    target = str(call_id)
    if thread_call_id == call_id or target in (selected_call_ids or []):
        return True
    return any(isinstance(source, dict) and source.get("call_id") == target
               for sources in message_sources for source in sources or [])


def _purge_database(session: Session, call: CallRecord) -> None:
    call_id = call.id
    # Remove entire user-owned threads whose answers may contain this call's wording.
    thread_ids = []
    threads = session.scalars(select(ChatThread).where(ChatThread.organization_id == call.organization_id)).all()
    for thread in threads:
        sources = session.scalars(select(ChatMessage.source_versions).where(ChatMessage.thread_id == thread.id)).all() \
            if thread.scope == "insights" else []
        if thread_uses_call(thread.call_id, thread.selected_call_ids, sources, call_id):
            thread_ids.append(thread.id)
    if thread_ids:
        session.execute(delete(ChatMessage).where(ChatMessage.thread_id.in_(thread_ids)))
        session.execute(delete(ChatThread).where(ChatThread.id.in_(thread_ids)))
    for event in session.scalars(select(PipelineEvent).where(PipelineEvent.call_id == call_id)).all():
        event.call_id = None
        event.analysis_id = None
        event.source_refs = []
        event.reason = "Source call deleted; opportunity requires review"
        event.superseded = True
    session.execute(delete(CallLink).where(CallLink.call_id == call_id))
    session.execute(delete(FollowUp).where(FollowUp.call_id == call_id))
    session.execute(delete(RetentionCase).where(RetentionCase.call_id == call_id))
    session.execute(delete(ReviewEvent).where(ReviewEvent.call_id == call_id))
    session.execute(delete(AddressCheck).where(AddressCheck.call_id == call_id))
    # Addresses copied from this call's review are call-derived content; manually entered ones survive unlinked.
    session.execute(delete(ContactAddress).where(ContactAddress.source_call_id == call_id,
                                                 ContactAddress.provenance == "address_review"))
    session.execute(update(ContactAddress).where(ContactAddress.source_call_id == call_id).values(source_call_id=None))
    call.active_transcript_id = None
    call.active_analysis_id = None
    call.active_manifest_id = None
    session.flush()
    analysis_ids = session.scalars(select(AnalysisRun.id).where(AnalysisRun.call_id == call_id)).all()
    session.execute(delete(CriterionDecision).where(CriterionDecision.analysis_id.in_(analysis_ids)))
    session.execute(delete(CallIndexChunk).where(CallIndexChunk.call_id == call_id))
    session.execute(delete(IndexManifest).where(IndexManifest.call_id == call_id))
    session.execute(delete(AnalysisRun).where(AnalysisRun.call_id == call_id))
    transcript_ids = session.scalars(select(TranscriptVersion.id).where(TranscriptVersion.call_id == call_id)).all()
    session.execute(delete(TranscriptSegment).where(TranscriptSegment.transcript_id.in_(transcript_ids)))
    session.execute(delete(TranscriptVersion).where(TranscriptVersion.call_id == call_id))
    session.execute(delete(AudioAsset).where(AudioAsset.call_id == call_id))
    call.original_filename = "deleted.mp3"
    call.external_call_id = None
    call.call_at = None
    call.call_timezone = None
    call.duration_seconds = None
    call.rep_id = None
    call.staging_key = None
    call.expected_bytes = 0
    call.audio_sha256 = None
    call.original_audio_key = None
    call.error_code = None
    call.deletion_status = "purging"


def call_object_keys(client, bucket: str, organization_id: UUID, call_id: UUID, recorded: list[str]) -> list[str]:
    """Recorded keys plus everything under the call's storage prefixes, so an object written after the
    tombstone (e.g. an upload finalizing during deletion) is still purged on a later pass."""
    keys = set(recorded)
    for prefix in (f"staging/{organization_id}/{call_id}/", f"audio/{organization_id}/{call_id}/"):
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys.update(item["Key"] for item in page.get("Contents", []))
    return sorted(keys)


def purge_due_once() -> bool:
    now = datetime.now(UTC)
    with Session(domain_engine()) as session:
        ledgers = session.scalars(select(PurgeRecord).where(PurgeRecord.state.in_(["pending", "rechecking"]))
                                  .order_by(PurgeRecord.created_at).limit(20)).all()
        chosen = None
        for ledger in ledgers:
            if ledger.last_attempt_at and ledger.last_attempt_at > now - timedelta(minutes=5):
                continue
            active_lease = session.scalar(select(ProcessingStep.id).where(
                ProcessingStep.call_id == ledger.call_id,
                ProcessingStep.state == "running",
                ProcessingStep.lease_until > now,
            ).limit(1))
            if not active_lease:
                chosen = ledger
                break
        if chosen is None:
            return False
        call_id, organization_id, keys, ledger_id = chosen.call_id, chosen.organization_id, chosen.object_keys, chosen.id
    try:
        client = r2_client()
        bucket = r2_settings().r2_bucket_name
        for key in call_object_keys(client, bucket, organization_id, call_id, keys):
            client.delete_object(Bucket=bucket, Key=key)
        # Filter catches vectors written by a stale worker after the first purge pass.
        vector_client().delete(namespace=str(organization_id), filter=f"call_id = '{call_id}'")
    except Exception as exc:  # noqa: BLE001  # provider failures stay retryable
        logger.warning("call_purge_provider_failed call_id=%s error_code=%s", call_id, type(exc).__name__)
        return False
    with Session(domain_engine()) as session, session.begin():
        ledger = session.scalar(select(PurgeRecord).where(PurgeRecord.id == ledger_id).with_for_update())
        call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if ledger is None or call is None or call.deletion_status == "active":
            return False
        if ledger.state == "pending":
            _purge_database(session, call)
        ledger.attempts += 1
        ledger.last_attempt_at = now
        if now - ledger.created_at >= timedelta(minutes=20) and ledger.attempts >= 2:
            ledger.state = "complete"
            ledger.object_keys = []
            ledger.completed_at = now
            call.deletion_status = "purged"
        else:
            ledger.state = "rechecking"
    return True
