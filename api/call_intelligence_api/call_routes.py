"""Authenticated upload, call evidence and review HTTP routes."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Select, func, or_, select, tuple_
from sqlalchemy.orm import Session

from call_intelligence_api.auth import (
    AuthenticatedMember,
    domain_engine,
    get_current_member,
)
from call_intelligence_api.call_models import (
    AnalysisRun,
    AudioAsset,
    CallRecord,
    CriterionDecision,
    IndexManifest,
    OutboxEvent,
    ProcessingStep,
    ReviewEvent,
    TranscriptSegment,
    TranscriptVersion,
)
from call_intelligence_api.crm_models import (
    AddressCheck,
    CallLink,
    Contact,
    Opportunity,
    Rep,
)
from call_intelligence_api.metrics import CallFilters, PrimaryType, apply_call_filters
from call_intelligence_api.processing import readable_review_reasons
from call_intelligence_api.scoring import load_rubric
from call_intelligence_api.storage import (
    original_key,
    r2_client,
    r2_settings,
    sign_playback,
    sign_upload,
    staging_key,
)

router = APIRouter(prefix="/api/v1", tags=["calls"])
Member = Annotated[AuthenticatedMember, Depends(get_current_member)]
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_DURATION_SECONDS = 60 * 60 * 3


class AudioToolSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    ffprobe_path: str = "ffprobe"


class UploadFileIntent(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0)
    content_type: Literal["audio/mpeg", "audio/mp3"]
    external_call_id: str | None = Field(default=None, max_length=120)
    call_at: datetime | None = None
    call_timezone: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def require_known_offset(self) -> UploadFileIntent:
        if self.call_at is not None and self.call_at.tzinfo is None:
            raise ValueError("Call date needs a UTC offset")
        return self


class UploadIntent(BaseModel):
    files: list[UploadFileIntent] = Field(min_length=1, max_length=20)


class UploadTicket(BaseModel):
    call_id: UUID
    upload_id: UUID
    upload_url: str
    expires_at: datetime
    headers: dict[str, str]


class UploadIntentResponse(BaseModel):
    items: list[UploadTicket]
    request_id: UUID


class CompleteUploadRequest(BaseModel):
    upload_id: UUID


class CallStatusResponse(BaseModel):
    call_id: UUID
    processing_status: str
    analysis_status: str
    index_status: str
    request_id: UUID


class CallSummary(BaseModel):
    id: UUID
    rep_id: UUID | None
    rep_name: str | None
    external_call_id: str | None
    filename: str
    call_at: datetime | None
    uploaded_at: datetime
    duration_seconds: float | None
    processing_status: str
    analysis_status: str
    index_status: str
    primary_type: str | None
    outcome: str | None
    type_reviewed: bool
    outcome_reviewed: bool
    confirmed_points: int | None
    nominal_maximum: int | None
    grade: str | None
    unresolved_count: int | None
    error_code: str | None
    review_status: str
    requested_generation: int


class CallListResponse(BaseModel):
    items: list[CallSummary]
    next_cursor: str | None
    request_id: UUID


class SegmentResponse(BaseModel):
    id: UUID
    ordinal: int
    start_seconds: float
    end_seconds: float
    speaker_label: str | None
    speaker_role: str
    role_uncertain: bool
    text: str


class TranscriptResponse(BaseModel):
    id: UUID
    version: int
    origin: str
    complete_coverage: bool
    segments: list[SegmentResponse]
    request_id: UUID


class CriterionResponse(BaseModel):
    criterion_id: str
    state: str
    points: int | None
    rationale: str
    evidence_refs: list[dict]
    credit_reason: str | None


class AnalysisResponse(BaseModel):
    id: UUID
    generation: int
    primary_type: str | None
    summary: str | None
    facts: list[dict]
    outcome: str | None
    needs_review: list[str]
    rubric_id: str | None
    rubric_version: str | None
    confirmed_points: int | None
    nominal_maximum: int | None
    unresolved_count: int | None
    grade: str | None
    coaching: dict | None
    criteria: list[CriterionResponse]
    review_version: int
    # Spoken address candidates (index = candidate_index for address-validation); older analyses have none.
    address_candidates: list[dict] = []


class AddressCheckResponse(BaseModel):
    id: UUID
    call_id: UUID
    purpose: str
    candidate: dict
    country_code: str | None
    status: str
    provider: str | None
    checked_at: datetime | None
    suggestion: dict | None
    review_decision: str | None
    reviewed_fields: dict | None
    version: int


def address_check_response(row: AddressCheck) -> AddressCheckResponse:
    """Public shape shared with address_routes; the suggestion is read from result["suggestion"]."""
    suggestion = (row.result or {}).get("suggestion")
    return AddressCheckResponse(
        id=row.id, call_id=row.call_id, purpose=row.purpose, candidate=row.candidate,
        country_code=row.country_code, status=row.status, provider=row.provider, checked_at=row.checked_at,
        suggestion=suggestion if isinstance(suggestion, dict) else None,
        review_decision=row.review_decision, reviewed_fields=row.reviewed_fields, version=row.version,
    )


class CrmLinkResponse(BaseModel):
    contact_id: UUID
    contact_name: str | None
    opportunity_id: UUID | None
    opportunity_stage: str | None


class ProcessingStepResponse(BaseModel):
    stage: str
    state: str
    attempt: int
    error_code: str | None


class CallDetailResponse(BaseModel):
    call: CallSummary
    transcript_version: int | None
    analysis: AnalysisResponse | None
    crm_links: list[CrmLinkResponse]
    address_checks: list[AddressCheckResponse]
    processing_steps: list[ProcessingStepResponse]
    analysis_stale: bool
    model_primary_type: str | None
    model_outcome: str | None
    request_id: UUID


class IndexStatusResponse(BaseModel):
    call_id: UUID
    index_status: str
    manifest_id: UUID | None
    transcript_version: int | None
    expected_count: int | None
    readable_count: int | None
    embedding_model: str | None
    error_code: str | None
    retryable: bool
    request_id: UUID


class PlaybackResponse(BaseModel):
    url: str
    expires_at: datetime
    request_id: UUID


class ReanalyzeRequest(BaseModel):
    expected_generation: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class RetryRequest(BaseModel):
    expected_generation: int = Field(ge=1)
    stage: Literal["transcription", "analysis", "indexing"]
    reason: str = Field(min_length=3, max_length=1000)


class SegmentChange(BaseModel):
    segment_id: UUID
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    speaker_role: Literal["employee", "customer", "other", "unknown"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> SegmentChange:
        if self.text is None and self.speaker_role is None:
            raise ValueError("A text or speaker-role correction is required")
        return self


class TranscriptRevisionRequest(BaseModel):
    base_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)
    changes: list[SegmentChange] = Field(min_length=1, max_length=500)


class RevisionResponse(BaseModel):
    call_id: UUID
    transcript_version: int
    requested_generation: int
    request_id: UUID


class CriterionReviewRequest(BaseModel):
    expected_review_version: int = Field(ge=0)
    state: Literal["met", "missed", "not_applicable", "not_observable"]
    evidence_segment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=3, max_length=2000)
    reason: str = Field(min_length=3, max_length=1000)


class CriterionReviewResponse(BaseModel):
    call_id: UUID
    analysis_id: UUID
    review_version: int
    confirmed_points: int
    unresolved_count: int
    grade: str | None
    request_id: UUID


CriterionState = Literal["met", "missed", "not_applicable", "not_observable"]


class CriterionChange(BaseModel):
    criterion_id: str = Field(min_length=1, max_length=100)
    state: CriterionState
    evidence_segment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=3, max_length=2000)


class ReviewChanges(BaseModel):
    primary_type: PrimaryType | None = None
    outcome: str | None = Field(default=None, pattern=r"^[a-z_]{2,40}$")
    rep_id: UUID | None = None  # explicit null clears the rep; an absent key leaves it unchanged
    criteria: list[CriterionChange] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_change(self) -> ReviewChanges:
        named = any(getattr(self, key) is not None for key in ("primary_type", "outcome", "criteria"))
        if not named and "rep_id" not in self.model_fields_set:
            raise ValueError("At least one change is required")
        if self.criteria and len({item.criterion_id for item in self.criteria}) != len(self.criteria):
            raise ValueError("Each criterion may change once per review")
        return self


class CallReviewRequest(BaseModel):
    expected_review_version: int = Field(ge=0)
    expected_generation: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)
    changes: ReviewChanges


class CallReviewResponse(BaseModel):
    call_id: UUID
    analysis_id: UUID
    review_version: int
    confirmed_points: int | None
    unresolved_count: int | None
    grade: str | None
    primary_type: str | None
    outcome: str | None
    rep_id: UUID | None
    request_id: UUID


class ReanalysisQueuedResponse(BaseModel):
    call_id: UUID
    requested_generation: int
    status: Literal["reanalysis_queued"] = "reanalysis_queued"
    request_id: UUID


def _safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    if not name.lower().endswith(".mp3") or not name or len(name) > 255:
        raise HTTPException(status_code=422, detail="An MP3 filename is required")
    return name


def _call(session: Session, member: AuthenticatedMember, call_id: UUID) -> CallRecord:
    call = session.scalar(
        select(CallRecord).where(
            CallRecord.id == call_id,
            CallRecord.organization_id == member.organization_id,
            CallRecord.deletion_status == "active",
        )
    )
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


def _analysis(session: Session, call: CallRecord) -> AnalysisRun | None:
    return session.get(AnalysisRun, call.active_analysis_id) if call.active_analysis_id else None


def _model_values(analysis: AnalysisRun | None) -> tuple[str | None, str | None]:
    """Original model primary type and outcome kind from the analysis intake."""
    intake = analysis.intake if analysis and analysis.intake else {}
    outcome = intake.get("outcome")
    if isinstance(outcome, dict):
        outcome = outcome.get("kind")
    primary_type = intake.get("primary_type")
    return (primary_type if isinstance(primary_type, str) else None, outcome if isinstance(outcome, str) else None)


def _summary(call: CallRecord, analysis: AnalysisRun | None, rep_name: str | None = None) -> CallSummary:
    # Effective columns first; rows analysed before those columns existed fall back to the intake.
    model_type, model_outcome = _model_values(analysis)
    return CallSummary(
        id=call.id,
        rep_id=call.rep_id,
        rep_name=rep_name,
        external_call_id=call.external_call_id,
        filename=call.original_filename,
        call_at=call.call_at,
        uploaded_at=call.uploaded_at,
        duration_seconds=call.duration_seconds,
        processing_status=call.processing_status,
        analysis_status=call.analysis_status,
        index_status=call.index_status,
        primary_type=call.primary_type or model_type,
        outcome=call.outcome or model_outcome,
        type_reviewed=call.type_override is not None,
        outcome_reviewed=call.outcome_override is not None,
        confirmed_points=analysis.confirmed_points if analysis else None,
        nominal_maximum=analysis.nominal_maximum if analysis else None,
        grade=analysis.grade if analysis else None,
        unresolved_count=analysis.unresolved_count if analysis else None,
        error_code=call.error_code,
        review_status=call.review_status,
        requested_generation=call.requested_generation,
    )


def summarize_calls(session: Session, calls: list[CallRecord]) -> list[CallSummary]:
    """Shared list rendering for other modules; batches analysis and rep lookups."""
    ids = [call.active_analysis_id for call in calls if call.active_analysis_id]
    runs = {run.id: run for run in session.scalars(select(AnalysisRun).where(AnalysisRun.id.in_(ids)))} if ids else {}
    rep_ids = {call.rep_id for call in calls if call.rep_id}
    names = dict(session.execute(select(Rep.id, Rep.display_name).where(Rep.id.in_(rep_ids))).tuples().all()) if rep_ids else {}
    return [_summary(call, runs.get(call.active_analysis_id), names.get(call.rep_id)) for call in calls]


def _probe_audio(path: Path) -> tuple[float, int | None]:
    try:
        executable = shutil.which("ffprobe") or AudioToolSettings().ffprobe_path
        probe = subprocess.run(
            [executable, "-v", "error", "-show_entries", "format=duration:stream=codec_name,channels", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        data = json.loads(probe.stdout)
        audio_streams = [s for s in data.get("streams", []) if s.get("codec_name") == "mp3"]
        duration = float(data.get("format", {}).get("duration", 0))
        if not audio_streams or not 0 < duration <= MAX_DURATION_SECONDS:
            raise ValueError("Invalid MP3 duration or codec")
        return duration, audio_streams[0].get("channels")
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="MP3 decoding or duration check failed") from exc


@router.post("/uploads", response_model=UploadIntentResponse, status_code=status.HTTP_201_CREATED)
def create_uploads(body: UploadIntent, member: Member) -> UploadIntentResponse:
    tickets: list[UploadTicket] = []
    with Session(domain_engine()) as session, session.begin():
        for item in body.files:
            if item.byte_size > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="MP3 exceeds the 25 MB upload limit")
            filename = _safe_filename(item.filename)
            call_id, upload_id = uuid4(), uuid4()
            key = staging_key(member.organization_id, call_id, upload_id)
            session.add(
                CallRecord(
                    id=call_id,
                    organization_id=member.organization_id,
                    external_call_id=item.external_call_id,
                    original_filename=filename,
                    call_at=item.call_at,
                    call_timezone=item.call_timezone,
                    upload_id=upload_id,
                    staging_key=key,
                    expected_bytes=item.byte_size,
                )
            )
            tickets.append(
                UploadTicket(
                    call_id=call_id,
                    upload_id=upload_id,
                    upload_url=sign_upload(key),
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    headers={"Content-Type": "audio/mpeg"},
                )
            )
    return UploadIntentResponse(items=tickets, request_id=uuid4())


@router.post("/calls/{call_id}/complete-upload", response_model=CallStatusResponse, status_code=status.HTTP_202_ACCEPTED)
def complete_upload(
    call_id: UUID,
    body: CompleteUploadRequest,
    member: Member,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> CallStatusResponse:
    if not idempotency_key or len(idempotency_key) > 120:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required")
    with Session(domain_engine()) as session:
        call = _call(session, member, call_id)
        if body.upload_id != call.upload_id:
            raise HTTPException(status_code=409, detail="Upload ID does not match")
        if call.processing_status != "uploading":
            return CallStatusResponse(
                call_id=call.id,
                processing_status=call.processing_status,
                analysis_status=call.analysis_status,
                index_status=call.index_status,
                request_id=uuid4(),
            )
        if not call.staging_key:
            raise HTTPException(status_code=409, detail="Upload is no longer active")
        bucket = r2_settings().r2_bucket_name
        client = r2_client()
        staging = call.staging_key
        try:
            head = client.head_object(Bucket=bucket, Key=staging)
            if int(head["ContentLength"]) != call.expected_bytes or call.expected_bytes > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=422, detail="Uploaded MP3 size differs from the request")
            if head.get("ContentType", "").split(";")[0] != "audio/mpeg":
                raise HTTPException(status_code=422, detail="Uploaded file must be audio/mpeg")
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                path = Path(tmp.name)
                digest = hashlib.sha256()
                byte_count = 0
                obj = client.get_object(Bucket=bucket, Key=staging)
                for chunk in obj["Body"].iter_chunks(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    byte_count += len(chunk)
                    if byte_count > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Uploaded MP3 exceeds the limit")
                    digest.update(chunk)
                    tmp.write(chunk)
            final_key = original_key(member.organization_id, call.id, digest.hexdigest())
            try:
                if byte_count != call.expected_bytes:
                    raise HTTPException(status_code=422, detail="Uploaded MP3 is incomplete")
                duration, channels = _probe_audio(path)
                # Write the bytes that were hashed and probed; the staging URL may still accept a re-PUT.
                client.upload_file(str(path), bucket, final_key, ExtraArgs={"ContentType": "audio/mpeg"})
            finally:
                path.unlink(missing_ok=True)
            final_head = client.head_object(Bucket=bucket, Key=final_key)
            if int(final_head["ContentLength"]) != byte_count:
                raise HTTPException(status_code=502, detail="Final audio copy could not be verified")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                raise HTTPException(status_code=409, detail="Uploaded MP3 was not found") from exc
            raise HTTPException(status_code=503, detail="Private audio storage is unavailable") from exc

        # Re-read under a row lock: another finalizer may have completed while the object was verified.
        session.rollback()
        with session.begin():
            locked = session.scalar(select(CallRecord).where(CallRecord.id == call.id).with_for_update())
            if locked is None or locked.deletion_status != "active":
                # Deleted while we verified: remove the copy we just wrote (the purge sweep is the backstop).
                for key in (final_key, staging):
                    try:
                        client.delete_object(Bucket=bucket, Key=key)
                    except ClientError:
                        pass  # the purge ledger's prefix sweep removes it on its next pass
                raise HTTPException(status_code=404, detail="Call not found")
            if locked.processing_status == "uploading":
                locked.original_audio_key = final_key
                locked.audio_sha256 = digest.hexdigest()
                locked.duration_seconds = duration
                locked.staging_key = None
                # Stored and verified, but AI processing waits for an explicit Process request (cost control).
                locked.processing_status = "uploaded"
                session.add(
                    AudioAsset(
                        organization_id=member.organization_id,
                        call_id=call.id,
                        object_key=final_key,
                        sha256=digest.hexdigest(),
                        byte_count=byte_count,
                        mime_type="audio/mpeg",
                        duration_seconds=duration,
                        channels=channels,
                    )
                )
        try:
            client.delete_object(Bucket=bucket, Key=staging)
        except ClientError:
            pass  # Staging cleanup is retryable; finalized evidence is immutable.
        return CallStatusResponse(
            call_id=call.id,
            processing_status="uploaded",
            analysis_status="not_started",
            index_status="not_started",
            request_id=uuid4(),
        )


class CallListQuery(CallFilters):
    limit: int = Field(default=30, ge=1, le=100)
    cursor: str | None = None
    q: str | None = None
    analysis_status: str | None = Field(default=None, pattern=r"^[a-z_]{2,24}$")
    index_status: str | None = Field(default=None, pattern=r"^[a-z_]{2,24}$")
    review_status: str | None = Field(default=None, pattern=r"^[a-z_]{2,24}$")
    processing_status: str | None = Field(default=None, pattern=r"^[a-z_]{2,24}$")
    date_known: bool | None = None


def call_list_statement(organization_id: UUID, query: CallListQuery) -> Select:
    """Active organization calls narrowed by the list filters (cursor and order applied by the route)."""
    stmt = select(CallRecord).where(
        CallRecord.organization_id == organization_id,
        CallRecord.deletion_status == "active",
    )
    if query.q:
        term = query.q[:100]
        stmt = stmt.where(or_(CallRecord.original_filename.icontains(term, autoescape=True),
                              CallRecord.external_call_id.icontains(term, autoescape=True)))
    for column, value in ((CallRecord.analysis_status, query.analysis_status), (CallRecord.index_status, query.index_status),
                          (CallRecord.review_status, query.review_status),
                          (CallRecord.processing_status, query.processing_status)):
        if value:
            stmt = stmt.where(column == value)
    if query.date_known is not None:
        stmt = stmt.where(CallRecord.call_at.is_not(None) if query.date_known else CallRecord.call_at.is_(None))
    return apply_call_filters(stmt, query)


@router.get("/calls", response_model=CallListResponse)
def list_calls(member: Member, query: Annotated[CallListQuery, Query()]) -> CallListResponse:
    limit, cursor = query.limit, query.cursor
    with Session(domain_engine()) as session:
        stmt = call_list_statement(member.organization_id, query)
        if cursor:
            if len(cursor) > 512:
                raise HTTPException(status_code=422, detail="Invalid call cursor")
            try:
                raw = json.loads(urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4)))
                before_at = datetime.fromisoformat(raw[0])
                before_id = UUID(raw[1])
                if before_at.tzinfo is None:
                    raise ValueError("Cursor must have a timezone")
            except (Base64Error, ValueError, TypeError, IndexError, UnicodeDecodeError) as exc:
                raise HTTPException(status_code=422, detail="Invalid call cursor") from exc
            stmt = stmt.where(tuple_(CallRecord.uploaded_at, CallRecord.id) < (before_at, before_id))
        calls = session.scalars(stmt.order_by(CallRecord.uploaded_at.desc(), CallRecord.id.desc()).limit(limit + 1)).all()
        items = summarize_calls(session, list(calls[:limit]))
        next_cursor = None
        if len(calls) > limit:
            last = calls[limit - 1]
            next_cursor = urlsafe_b64encode(json.dumps([last.uploaded_at.isoformat(), str(last.id)]).encode()).decode().rstrip("=")
    return CallListResponse(items=items, next_cursor=next_cursor, request_id=uuid4())


class ActivityItem(BaseModel):
    id: UUID
    label: str
    status: Literal["complete", "failed"]
    finished_at: datetime


class ActivityResponse(BaseModel):
    processing: int
    recent: list[ActivityItem]
    request_id: UUID


@router.get("/calls/activity", response_model=ActivityResponse)
def call_activity(member: Member) -> ActivityResponse:
    """Notification feed: how many calls are processing, and the latest that finished or failed."""
    with Session(domain_engine()) as session:
        active = (CallRecord.organization_id == member.organization_id, CallRecord.deletion_status == "active")
        processing = session.scalar(select(func.count()).select_from(CallRecord).where(
            *active, CallRecord.processing_status.in_(["queued", "running"]))) or 0
        failed_at = select(func.max(ProcessingStep.updated_at)).where(
            ProcessingStep.call_id == CallRecord.id, ProcessingStep.generation == CallRecord.requested_generation,
        ).scalar_subquery()
        finished_at = func.coalesce(CallRecord.processed_at, failed_at)
        rows = session.execute(select(CallRecord, finished_at).where(
            *active, CallRecord.processing_status.in_(["complete", "failed"]), finished_at.is_not(None),
        ).order_by(finished_at.desc()).limit(8)).all()
        return ActivityResponse(processing=processing, recent=[ActivityItem(
            id=call.id, label=call.external_call_id or call.original_filename,
            status=call.processing_status, finished_at=at,
        ) for call, at in rows], request_id=uuid4())


@router.get("/calls/{call_id}", response_model=CallDetailResponse)
def get_call(call_id: UUID, member: Member) -> CallDetailResponse:
    with Session(domain_engine()) as session:
        call = _call(session, member, call_id)
        analysis = _analysis(session, call)
        detail = None
        if analysis:
            intake = analysis.intake or {}
            outcome = _model_values(analysis)[1]
            decisions = session.scalars(
                select(CriterionDecision).where(CriterionDecision.analysis_id == analysis.id).order_by(CriterionDecision.criterion_id)
            ).all()
            detail = AnalysisResponse(
                id=analysis.id,
                generation=analysis.generation,
                primary_type=intake.get("primary_type"),
                summary=intake.get("short_summary"),
                facts=intake.get("facts", []),
                outcome=outcome,
                needs_review=readable_review_reasons(intake.get("needs_review", [])),
                rubric_id=analysis.rubric_id,
                rubric_version=analysis.rubric_version,
                confirmed_points=analysis.confirmed_points,
                nominal_maximum=analysis.nominal_maximum,
                unresolved_count=analysis.unresolved_count,
                grade=analysis.grade,
                coaching=analysis.coaching,
                criteria=[
                    CriterionResponse(
                        criterion_id=d.criterion_id,
                        state=d.state,
                        points=d.points,
                        rationale=d.rationale,
                        evidence_refs=d.evidence_refs,
                        credit_reason=d.credit_reason,
                    )
                    for d in decisions
                ],
                review_version=analysis.review_version,
                address_candidates=intake.get("address_candidates") or [],
            )
        transcript = session.get(TranscriptVersion, call.active_transcript_id) if call.active_transcript_id else None
        links = session.execute(
            select(CallLink.contact_id, Contact.display_name, CallLink.opportunity_id, Opportunity.stage)
            .join(Contact, Contact.id == CallLink.contact_id)
            .outerjoin(Opportunity, Opportunity.id == CallLink.opportunity_id)
            .where(CallLink.call_id == call.id, CallLink.organization_id == member.organization_id)
            .order_by(CallLink.created_at)
        ).all()
        checks = session.scalars(select(AddressCheck).where(
            AddressCheck.call_id == call.id, AddressCheck.organization_id == member.organization_id,
        ).order_by(AddressCheck.checked_at.desc().nulls_last(), AddressCheck.id)).all()
        steps = session.scalars(select(ProcessingStep).where(
            ProcessingStep.call_id == call.id, ProcessingStep.generation == call.requested_generation,
        ).order_by(ProcessingStep.stage)).all()
        model_type, model_outcome = _model_values(analysis)
        return CallDetailResponse(
            call=summarize_calls(session, [call])[0],
            transcript_version=transcript.version if transcript else None,
            analysis=detail,
            crm_links=[CrmLinkResponse(contact_id=row[0], contact_name=row[1], opportunity_id=row[2], opportunity_stage=row[3])
                       for row in links],
            address_checks=[address_check_response(row) for row in checks],
            processing_steps=[ProcessingStepResponse(stage=s.stage, state=s.state, attempt=s.attempt, error_code=s.error_code)
                              for s in steps],
            analysis_stale=analysis is not None and analysis.generation != call.requested_generation,
            model_primary_type=model_type,
            model_outcome=model_outcome,
            request_id=uuid4(),
        )


@router.get("/calls/{call_id}/transcript", response_model=TranscriptResponse)
def get_transcript(call_id: UUID, member: Member, version: int | None = None) -> TranscriptResponse:
    with Session(domain_engine()) as session:
        call = _call(session, member, call_id)
        if version is None:
            transcript = session.get(TranscriptVersion, call.active_transcript_id) if call.active_transcript_id else None
        else:
            transcript = session.scalar(
                select(TranscriptVersion).where(TranscriptVersion.call_id == call.id, TranscriptVersion.version == version)
            )
        if transcript is None:
            raise HTTPException(status_code=404, detail="Transcript not found")
        segments = session.scalars(
            select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id).order_by(TranscriptSegment.ordinal)
        ).all()
        return TranscriptResponse(
            id=transcript.id,
            version=transcript.version,
            origin=transcript.origin,
            complete_coverage=transcript.complete_coverage,
            segments=[SegmentResponse.model_validate(s, from_attributes=True) for s in segments],
            request_id=uuid4(),
        )


@router.get("/calls/{call_id}/audio-url", response_model=PlaybackResponse)
def audio_url(call_id: UUID, member: Member) -> PlaybackResponse:
    with Session(domain_engine()) as session:
        call = _call(session, member, call_id)
        if not call.original_audio_key:
            raise HTTPException(status_code=409, detail="Audio is not finalized")
        return PlaybackResponse(
            url=sign_playback(call.original_audio_key),
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
            request_id=uuid4(),
        )


class ProcessRequest(BaseModel):
    call_ids: list[UUID] = Field(min_length=1, max_length=50)


class ProcessResult(BaseModel):
    call_id: UUID
    started: bool
    processing_status: str | None
    reason: str | None


class ProcessResponse(BaseModel):
    items: list[ProcessResult]
    request_id: UUID


def process_block_reason(call: CallRecord | None) -> str | None:
    """None when AI processing may start: a verified upload that was never processed, or one whose hand-off failed."""
    if call is None or call.deletion_status != "active":
        return "Call not found"
    if call.processing_status == "uploaded" or (call.processing_status == "failed" and call.error_code == "dispatch_failed"):
        return None
    if call.processing_status == "uploading":
        return "Upload has not finished"
    if call.processing_status in {"queued", "running"}:
        return "Already processing"
    return "Already processed; use Re-analyze to run it again"


def _start_processing(session: Session, call: CallRecord, actor: str) -> None:
    event = session.scalar(select(OutboxEvent).where(
        OutboxEvent.call_id == call.id, OutboxEvent.generation == call.requested_generation,
        OutboxEvent.kind == "process_call",
    ).with_for_update())
    if event is None:
        session.add(OutboxEvent(organization_id=call.organization_id, call_id=call.id,
                                generation=call.requested_generation, kind="process_call"))
    else:  # a failed hand-off is retried from a clean slate
        event.state, event.attempts, event.dispatch_id, event.dispatched_at = "pending", 0, None, None
    call.processing_status = "queued"
    call.error_code = None
    session.add(ReviewEvent(
        organization_id=call.organization_id, call_id=call.id, actor_user_id=actor, kind="process_request",
        prior_version=call.requested_generation, new_version=call.requested_generation,
        change={"action": "process"}, reason="Processing requested",
    ))


@router.post("/calls/process", response_model=ProcessResponse)
def process_calls(body: ProcessRequest, member: Member) -> ProcessResponse:
    """Start AI processing (transcribe, analyze, index) for uploaded calls. Nothing is processed on upload."""
    with Session(domain_engine()) as session, session.begin():
        found = {call.id: call for call in session.scalars(select(CallRecord).where(
            CallRecord.id.in_(body.call_ids), CallRecord.organization_id == member.organization_id,
        ).with_for_update())}
        items = []
        for call_id in dict.fromkeys(body.call_ids):
            call = found.get(call_id)
            reason = process_block_reason(call)
            if reason is None:
                _start_processing(session, call, member.user_id)
            items.append(ProcessResult(call_id=call_id, started=reason is None, reason=reason,
                                       processing_status=call.processing_status if call else None))
        return ProcessResponse(items=items, request_id=uuid4())


@router.post("/calls/{call_id}/process", response_model=ProcessResponse)
def process_call_now(call_id: UUID, member: Member) -> ProcessResponse:
    return process_calls(ProcessRequest(call_ids=[call_id]), member)


@router.post("/calls/{call_id}/reanalyze", response_model=CallStatusResponse, status_code=202)
def reanalyze(call_id: UUID, body: ReanalyzeRequest, member: Member) -> CallStatusResponse:
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if call is None or call.organization_id != member.organization_id or call.deletion_status != "active":
            raise HTTPException(status_code=404, detail="Call not found")
        if not call.active_transcript_id:
            raise HTTPException(status_code=409, detail="Transcript is not ready")
        if call.requested_generation != body.expected_generation:
            raise HTTPException(status_code=409, detail="Call generation changed; refresh first")
        call.requested_generation += 1
        call.processing_status = "queued"
        call.analysis_status = "stale"
        call.error_code = None
        session.add(
            OutboxEvent(
                organization_id=call.organization_id,
                call_id=call.id,
                generation=call.requested_generation,
                kind="process_call",
            )
        )
        session.add(
            ReviewEvent(
                organization_id=call.organization_id,
                call_id=call.id,
                actor_user_id=member.user_id,
                kind="reanalyze",
                prior_version=body.expected_generation,
                new_version=call.requested_generation,
                change={"source_transcript_id": str(call.active_transcript_id)},
                reason=body.reason,
            )
        )
        return CallStatusResponse(
            call_id=call.id,
            processing_status=call.processing_status,
            analysis_status=call.analysis_status,
            index_status=call.index_status,
            request_id=uuid4(),
        )


@router.post("/calls/{call_id}/retry", response_model=CallStatusResponse, status_code=202)
def retry_failed_stage(call_id: UUID, body: RetryRequest, member: Member) -> CallStatusResponse:
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if call is None or call.organization_id != member.organization_id or call.deletion_status != "active":
            raise HTTPException(status_code=404, detail="Call not found")
        if call.requested_generation != body.expected_generation:
            raise HTTPException(status_code=409, detail="Call generation changed; refresh first")
        step = session.scalar(select(ProcessingStep).where(
            ProcessingStep.call_id == call.id, ProcessingStep.generation == body.expected_generation,
            ProcessingStep.stage == body.stage,
        ).with_for_update())
        if step is None or step.state != "failed":
            raise HTTPException(status_code=409, detail="The selected stage is not failed")
        step.attempt = 0
        step.error_code = None
        call.error_code = None
        if body.stage == "transcription":
            call.processing_status = "queued"
        elif body.stage == "analysis":
            call.processing_status = "queued"
            call.analysis_status = "stale"
        else:
            call.index_status = "stale"
        event = session.scalar(select(OutboxEvent).where(
            OutboxEvent.call_id == call.id,
            OutboxEvent.generation == body.expected_generation,
            OutboxEvent.kind == "process_call",
        ).with_for_update())
        if event is None:
            raise HTTPException(status_code=409, detail="Processing event is unavailable")
        event.state = "pending"
        event.attempts = 0
        event.dispatch_id = None
        event.dispatched_at = None
        session.add(ReviewEvent(
            organization_id=call.organization_id, call_id=call.id, actor_user_id=member.user_id,
            kind="manual_retry", prior_version=body.expected_generation,
            new_version=body.expected_generation, change={"stage": body.stage}, reason=body.reason,
        ))
        return CallStatusResponse(call_id=call.id, processing_status=call.processing_status,
                                  analysis_status=call.analysis_status, index_status=call.index_status,
                                  request_id=uuid4())


@router.post("/calls/{call_id}/transcript-revisions", response_model=RevisionResponse, status_code=202)
def revise_transcript(call_id: UUID, body: TranscriptRevisionRequest, member: Member) -> RevisionResponse:
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if call is None or call.organization_id != member.organization_id or call.deletion_status != "active":
            raise HTTPException(status_code=404, detail="Call not found")
        base = session.get(TranscriptVersion, call.active_transcript_id) if call.active_transcript_id else None
        if base is None or base.version != body.base_version:
            raise HTTPException(status_code=409, detail="Transcript version changed; refresh first")
        old_segments = session.scalars(
            select(TranscriptSegment).where(TranscriptSegment.transcript_id == base.id).order_by(TranscriptSegment.ordinal)
        ).all()
        changes = {change.segment_id: change for change in body.changes}
        if len(changes) != len(body.changes) or not set(changes).issubset({segment.id for segment in old_segments}):
            raise HTTPException(status_code=422, detail="Changes must name unique current transcript segments")
        revised = TranscriptVersion(
            organization_id=call.organization_id,
            call_id=call.id,
            version=base.version + 1,
            source_audio_sha256=base.source_audio_sha256,
            model_name=base.model_name,
            origin="human_revision",
            parent_id=base.id,
            editor_user_id=member.user_id,
            edit_reason=body.reason,
            complete_coverage=base.complete_coverage,
        )
        session.add(revised)
        session.flush()
        for segment in old_segments:
            change = changes.get(segment.id)
            session.add(
                TranscriptSegment(
                    organization_id=call.organization_id,
                    transcript_id=revised.id,
                    ordinal=segment.ordinal,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    speaker_label=segment.speaker_label,
                    speaker_role=change.speaker_role if change and change.speaker_role is not None else segment.speaker_role,
                    role_uncertain=False if change and change.speaker_role is not None else segment.role_uncertain,
                    text=change.text.strip() if change and change.text is not None else segment.text,
                )
            )
        call.active_transcript_id = revised.id
        call.requested_generation += 1
        call.processing_status = "queued"
        call.analysis_status = "stale"
        call.index_status = "stale"
        call.review_status = "reviewed"
        session.add(
            OutboxEvent(
                organization_id=call.organization_id,
                call_id=call.id,
                generation=call.requested_generation,
                kind="process_call",
            )
        )
        session.add(
            ReviewEvent(
                organization_id=call.organization_id,
                call_id=call.id,
                actor_user_id=member.user_id,
                kind="transcript_revision",
                prior_version=base.version,
                new_version=revised.version,
                change={"segment_ids": [str(identifier) for identifier in changes]},
                reason=body.reason,
            )
        )
        return RevisionResponse(
            call_id=call.id,
            transcript_version=revised.version,
            requested_generation=call.requested_generation,
            request_id=uuid4(),
        )


@router.patch("/calls/{call_id}/criteria/{criterion_id}", response_model=CriterionReviewResponse)
def review_criterion(call_id: UUID, criterion_id: str, body: CriterionReviewRequest, member: Member) -> CriterionReviewResponse:
    with Session(domain_engine()) as session, session.begin():
        call = _locked_call(session, member, call_id)
        run = _current_run(session, call, body.expected_review_version)
        rubric = _run_rubric(run)
        change = CriterionChange(criterion_id=criterion_id, state=body.state,
                                 evidence_segment_ids=body.evidence_segment_ids, rationale=body.rationale)
        audit = _apply_criterion(session, call, run, rubric, change)
        _rescore(session, call, run, rubric)
        run.review_version += 1
        call.review_status = "reviewed"
        session.add(ReviewEvent(
            organization_id=call.organization_id,
            call_id=call.id,
            actor_user_id=member.user_id,
            kind="criterion_review",
            prior_version=body.expected_review_version,
            new_version=run.review_version,
            change=audit,
            reason=body.reason,
        ))
        return CriterionReviewResponse(
            call_id=call.id, analysis_id=run.id, review_version=run.review_version,
            confirmed_points=run.confirmed_points, unresolved_count=run.unresolved_count,
            grade=run.grade, request_id=uuid4(),
        )


def _locked_call(session: Session, member: AuthenticatedMember, call_id: UUID) -> CallRecord:
    call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
    if call is None or call.organization_id != member.organization_id or call.deletion_status != "active":
        raise HTTPException(status_code=404, detail="Call not found")
    return call


def _current_run(session: Session, call: CallRecord, expected_review_version: int) -> AnalysisRun:
    run = session.get(AnalysisRun, call.active_analysis_id) if call.active_analysis_id else None
    if run is None or run.generation != call.requested_generation or run.transcript_id != call.active_transcript_id:
        raise HTTPException(status_code=409, detail="Current analysis is not ready")
    if run.review_version != expected_review_version:
        raise HTTPException(status_code=409, detail="Review version changed; refresh first")
    return run


def _run_rubric(run: AnalysisRun) -> dict | None:
    # The published rubric id reflects any type override; older runs fall back to the intake type.
    return load_rubric(run.rubric_id or (run.intake or {}).get("primary_type", ""))


def _apply_criterion(session: Session, call: CallRecord, run: AnalysisRun, rubric: dict | None,
                     change: CriterionChange) -> dict:
    """Validate and apply one reviewer criterion decision; returns the audit change payload."""
    if rubric is None or change.criterion_id not in {item["id"] for item in rubric["criteria"]}:
        raise HTTPException(status_code=404, detail="Criterion not found")
    decision = session.scalar(select(CriterionDecision).where(
        CriterionDecision.analysis_id == run.id,
        CriterionDecision.criterion_id == change.criterion_id,
    ))
    if decision is None:
        raise HTTPException(status_code=404, detail="Criterion not found")
    transcript = session.get(TranscriptVersion, run.transcript_id)
    if change.state == "missed" and not transcript.complete_coverage:
        raise HTTPException(status_code=422, detail="A missed criterion needs complete usable coverage")
    segments = session.scalars(select(TranscriptSegment).where(
        TranscriptSegment.transcript_id == transcript.id,
        TranscriptSegment.id.in_(change.evidence_segment_ids),
    )).all()
    if len(segments) != len(set(change.evidence_segment_ids)):
        raise HTTPException(status_code=422, detail="Evidence must name current transcript segments")
    if change.state == "met" and not segments:
        raise HTTPException(status_code=422, detail="A met criterion needs source evidence")
    if change.state in {"not_applicable", "not_observable"} and segments:
        raise HTTPException(status_code=422, detail="An unresolved criterion cannot claim source evidence")
    prior = {"state": decision.state, "points": decision.points, "rationale": decision.rationale, "evidence_refs": decision.evidence_refs}
    decision.state = change.state
    decision.points = 1 if change.state == "met" else 0 if change.state == "missed" else None
    decision.rationale = change.rationale
    decision.credit_reason = None
    decision.evidence_refs = [{
        "source_kind": "transcript",
        "call_id": str(call.id),
        "transcript_version": transcript.version,
        "segment_ids": [str(segment.id)],
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
        "quote": segment.text,
    } for segment in segments]
    return {"criterion_id": change.criterion_id, "prior": prior, "new_state": change.state,
            "new_evidence_segment_ids": [str(item.id) for item in segments]}


def _rescore(session: Session, call: CallRecord, run: AnalysisRun, rubric: dict) -> None:
    session.flush()
    decisions = session.scalars(select(CriterionDecision).where(CriterionDecision.analysis_id == run.id)).all()
    run.confirmed_points = sum(item.points or 0 for item in decisions)
    run.unresolved_count = sum(item.points is None for item in decisions)
    run.grade = next((threshold["grade"] for threshold in rubric["thresholds"]
                      if run.unresolved_count == 0 and threshold["minimum"] <= run.confirmed_points <= threshold["maximum"]), None)
    run.state = "ready" if run.grade and not (run.intake or {}).get("needs_review") else "needs_review"
    call.analysis_status = run.state


def plan_call_review(call: CallRecord, model_type: str | None, changes: ReviewChanges) -> tuple[list[dict], bool]:
    """Call-level field changes as audit payloads, and whether the effective type changes (re-analysis).

    Setting an override equal to the current effective value still records a reviewer confirmation
    (so reviewed-outcome metrics count it) but never triggers re-analysis.
    """
    planned: list[dict] = []
    effective_type = call.primary_type or model_type
    if changes.primary_type is not None and changes.primary_type != call.type_override:
        planned.append({"field": "primary_type", "prior": effective_type, "new": changes.primary_type,
                        "prior_override": call.type_override})
    if changes.outcome is not None and changes.outcome != call.outcome_override:
        planned.append({"field": "outcome", "prior": call.outcome, "new": changes.outcome,
                        "prior_override": call.outcome_override})
    if "rep_id" in changes.model_fields_set and changes.rep_id != call.rep_id:
        planned.append({"field": "rep_id", "prior": str(call.rep_id) if call.rep_id else None,
                        "new": str(changes.rep_id) if changes.rep_id else None})
    reanalyze = any(item["field"] == "primary_type" and item["prior"] != item["new"] for item in planned)
    if reanalyze and changes.criteria:
        raise HTTPException(status_code=422, detail="Criterion changes cannot be combined with a call type change")
    return planned, reanalyze


@router.post("/calls/{call_id}/review", response_model=CallReviewResponse | ReanalysisQueuedResponse,
             responses={202: {"model": ReanalysisQueuedResponse}})
def review_call(call_id: UUID, body: CallReviewRequest, member: Member, response: Response) -> CallReviewResponse | ReanalysisQueuedResponse:
    with Session(domain_engine()) as session, session.begin():
        call = _locked_call(session, member, call_id)
        if call.requested_generation != body.expected_generation:
            raise HTTPException(status_code=409, detail="Call generation changed; refresh first")
        run = _current_run(session, call, body.expected_review_version)
        planned, reanalyze = plan_call_review(call, _model_values(run)[0], body.changes)
        if body.changes.rep_id is not None and "rep_id" in {item["field"] for item in planned}:
            rep = session.scalar(select(Rep.id).where(Rep.id == body.changes.rep_id, Rep.organization_id == member.organization_id))
            if rep is None:
                raise HTTPException(status_code=404, detail="Rep not found")
        events: list[tuple[str, dict]] = []
        for item in planned:
            if item["field"] == "primary_type":
                call.type_override = call.primary_type = item["new"]
            elif item["field"] == "outcome":
                call.outcome_override = call.outcome = item["new"]
            else:
                call.rep_id = body.changes.rep_id
            events.append(({"primary_type": "type_review", "outcome": "outcome_review"}.get(item["field"], "rep_assignment"), item))
        if body.changes.criteria:
            rubric = _run_rubric(run)
            events += [("criterion_review", _apply_criterion(session, call, run, rubric, change)) for change in body.changes.criteria]
            _rescore(session, call, run, rubric)
        if events:
            call.review_status = "reviewed"
        if reanalyze:
            prior_generation = call.requested_generation
            call.requested_generation += 1
            call.processing_status = "queued"
            call.analysis_status = "stale"
            call.error_code = None
            session.add(OutboxEvent(organization_id=call.organization_id, call_id=call.id,
                                    generation=call.requested_generation, kind="process_call"))
            versions = (prior_generation, call.requested_generation)
        else:
            versions = (run.review_version, run.review_version + (1 if events else 0))
            run.review_version = versions[1]
        for kind, change in events:
            session.add(ReviewEvent(
                organization_id=call.organization_id, call_id=call.id, actor_user_id=member.user_id, kind=kind,
                prior_version=versions[0], new_version=versions[1], change=change, reason=body.reason,
            ))
        if reanalyze:
            response.status_code = status.HTTP_202_ACCEPTED
            return ReanalysisQueuedResponse(call_id=call.id, requested_generation=call.requested_generation, request_id=uuid4())
        return CallReviewResponse(
            call_id=call.id, analysis_id=run.id, review_version=run.review_version,
            confirmed_points=run.confirmed_points, unresolved_count=run.unresolved_count, grade=run.grade,
            primary_type=call.primary_type or _model_values(run)[0], outcome=call.outcome or _model_values(run)[1],
            rep_id=call.rep_id, request_id=uuid4(),
        )


@router.get("/calls/{call_id}/index-status", response_model=IndexStatusResponse)
def index_status(call_id: UUID, member: Member) -> IndexStatusResponse:
    with Session(domain_engine()) as session:
        call = _call(session, member, call_id)
        # Prefer the manifest being built for the current generation; else the active (last ready) one.
        manifest = session.scalar(select(IndexManifest).where(
            IndexManifest.call_id == call.id, IndexManifest.generation == call.requested_generation,
        ).order_by(IndexManifest.created_at.desc()).limit(1))
        if manifest is None and call.active_manifest_id:
            manifest = session.get(IndexManifest, call.active_manifest_id)
        transcript = session.get(TranscriptVersion, manifest.transcript_id) if manifest else None
        step = session.scalar(select(ProcessingStep).where(
            ProcessingStep.call_id == call.id, ProcessingStep.generation == call.requested_generation,
            ProcessingStep.stage == "indexing",
        ))
        failed = step is not None and step.state == "failed"
        return IndexStatusResponse(
            call_id=call.id, index_status=call.index_status, manifest_id=manifest.id if manifest else None,
            transcript_version=transcript.version if transcript else None,
            expected_count=manifest.expected_count if manifest else None,
            readable_count=manifest.readable_count if manifest else None,
            embedding_model=manifest.embedding_model if manifest else None,
            error_code=step.error_code if failed else None, retryable=failed, request_id=uuid4(),
        )
