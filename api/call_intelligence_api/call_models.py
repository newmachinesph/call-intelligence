"""Organization-scoped, versioned call evidence and processing records."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from call_intelligence_api.db import Base


class CallRecord(Base):
    __tablename__ = "call_record"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_call_id", name="uq_call_external_id"),
        Index("ix_call_org_uploaded", "organization_id", "uploaded_at", "id"),
        Index("ix_call_org_status", "organization_id", "processing_status"),
        Index("ix_call_org_type", "organization_id", "primary_type"),
        Index("ix_call_org_outcome", "organization_id", "outcome"),
        Index("ix_call_org_rep", "organization_id", "rep_id"),
        Index("ix_call_org_call_at", "organization_id", "call_at"),
        CheckConstraint("requested_generation >= 1", name="ck_call_generation"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    external_call_id: Mapped[str | None] = mapped_column(String(120))
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_timezone: Mapped[str | None] = mapped_column(String(100))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    rep_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("rep.id"))
    upload_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    staging_key: Mapped[str | None] = mapped_column(Text)
    expected_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    audio_sha256: Mapped[str | None] = mapped_column(String(64))
    original_audio_key: Mapped[str | None] = mapped_column(Text)
    processing_status: Mapped[str] = mapped_column(String(24), nullable=False, default="uploading")
    analysis_status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_started")
    index_status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_started")
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unreviewed")
    deletion_status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    requested_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_transcript_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    active_analysis_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    active_manifest_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Effective (reviewed override, else current model) values for filtering; originals stay in analysis_run.intake.
    primary_type: Mapped[str | None] = mapped_column(String(24))
    outcome: Mapped[str | None] = mapped_column(String(40))
    # Reviewer overrides survive re-analysis; a type override also selects the rubric.
    type_override: Mapped[str | None] = mapped_column(String(24))
    outcome_override: Mapped[str | None] = mapped_column(String(40))


class AudioAsset(Base):
    __tablename__ = "audio_asset"
    __table_args__ = (UniqueConstraint("call_id", "role", name="uq_audio_call_role"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    channels: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="original")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TranscriptVersion(Base):
    __tablename__ = "transcript_version"
    __table_args__ = (UniqueConstraint("call_id", "version", name="uq_transcript_call_version"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_audio_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False, default="automatic")
    parent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    editor_user_id: Mapped[str | None] = mapped_column(String(255))
    edit_reason: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    complete_coverage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TranscriptSegment(Base):
    __tablename__ = "transcript_segment"
    __table_args__ = (
        UniqueConstraint("transcript_id", "ordinal", name="uq_segment_transcript_ordinal"),
        Index("ix_segment_transcript_time", "transcript_id", "start_seconds"),
        CheckConstraint("start_seconds >= 0 AND end_seconds >= start_seconds", name="ck_segment_time"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    transcript_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("transcript_version.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    speaker_label: Mapped[str | None] = mapped_column(String(40))
    speaker_role: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    role_uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class AnalysisRun(Base):
    __tablename__ = "analysis_run"
    __table_args__ = (UniqueConstraint("call_id", "generation", name="uq_analysis_call_generation"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    transcript_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("transcript_version.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    rubric_id: Mapped[str | None] = mapped_column(String(30))
    rubric_version: Mapped[str | None] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    intake: Mapped[dict | None] = mapped_column(JSONB)
    raw_evaluation: Mapped[dict | None] = mapped_column(JSONB)
    coaching: Mapped[dict | None] = mapped_column(JSONB)
    confirmed_points: Mapped[int | None] = mapped_column(Integer)
    nominal_maximum: Mapped[int | None] = mapped_column(Integer)
    unresolved_count: Mapped[int | None] = mapped_column(Integer)
    grade: Mapped[str | None] = mapped_column(String(30))
    review_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CriterionDecision(Base):
    __tablename__ = "criterion_decision"
    __table_args__ = (UniqueConstraint("analysis_id", "criterion_id", name="uq_decision_analysis_criterion"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    analysis_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("analysis_run.id", ondelete="CASCADE"), nullable=False)
    criterion_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    points: Mapped[int | None] = mapped_column(Integer)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    credit_reason: Mapped[str | None] = mapped_column(Text)


class ProcessingStep(Base):
    __tablename__ = "processing_step"
    __table_args__ = (UniqueConstraint("call_id", "generation", "stage", name="uq_step_call_generation_stage"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OutboxEvent(Base):
    __tablename__ = "outbox_event"
    __table_args__ = (UniqueConstraint("call_id", "generation", "kind", name="uq_outbox_call_generation_kind"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    dispatch_id: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewEvent(Base):
    __tablename__ = "review_event"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    prior_version: Mapped[int] = mapped_column(Integer, nullable=False)
    new_version: Mapped[int] = mapped_column(Integer, nullable=False)
    change: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IndexManifest(Base):
    __tablename__ = "index_manifest"
    __table_args__ = (UniqueConstraint("call_id", "generation", "transcript_id", name="uq_manifest_source"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    transcript_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("transcript_version.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    readable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="indexing")
    usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallIndexChunk(Base):
    __tablename__ = "call_index_chunk"
    __table_args__ = (
        UniqueConstraint("manifest_id", "ordinal", name="uq_chunk_manifest_ordinal"),
        UniqueConstraint("vector_id", name="uq_chunk_vector_id"),
        Index("ix_chunk_call_manifest", "call_id", "manifest_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    manifest_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("index_manifest.id", ondelete="CASCADE"), nullable=False)
    transcript_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("transcript_version.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    vector_id: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)


class ChatThread(Base):
    __tablename__ = "chat_thread"
    __table_args__ = (Index("ix_thread_author_scope", "organization_id", "author_user_id", "scope"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    author_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(200))
    selected_call_ids: Mapped[list | None] = mapped_column(JSONB)
    # Organization-scope Insights filters, re-resolved on every question.
    scope_filters: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (
        UniqueConstraint("thread_id", "client_message_id", name="uq_message_client_id"),
        Index("ix_message_thread_created", "thread_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("chat_thread.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_status: Mapped[str | None] = mapped_column(String(24))
    citations: Mapped[list | None] = mapped_column(JSONB)
    source_versions: Mapped[list | None] = mapped_column(JSONB)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurgeRecord(Base):
    __tablename__ = "purge_record"
    __table_args__ = (UniqueConstraint("call_id", name="uq_purge_call"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id"), nullable=False)
    object_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
