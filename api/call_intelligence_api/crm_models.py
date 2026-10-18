"""Local, audited CRM records; no external customer account is implied."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from call_intelligence_api.db import Base


class Rep(Base):
    __tablename__ = "rep"
    __table_args__ = (Index("ix_rep_org_name", "organization_id", "display_name"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    linked_user_id: Mapped[str | None] = mapped_column(String(255))
    identity_source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Contact(Base):
    __tablename__ = "contact"
    __table_args__ = (Index("ix_contact_org_name", "organization_id", "display_name"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContactIdentifier(Base):
    __tablename__ = "contact_identifier"
    __table_args__ = (Index("ix_identifier_org_kind_value", "organization_id", "kind", "normalized_value"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    contact_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("contact.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(255), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provenance: Mapped[str] = mapped_column(String(80), nullable=False)


class Opportunity(Base):
    __tablename__ = "opportunity"
    __table_args__ = (Index("ix_opportunity_org_stage", "organization_id", "stage"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    contact_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("contact.id"), nullable=False)
    service_interest: Mapped[str | None] = mapped_column(String(255))
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="New")
    owner_user_id: Mapped[str | None] = mapped_column(String(255))
    manual_lock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quoted_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    quote_currency: Mapped[str | None] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CallLink(Base):
    __tablename__ = "call_link"
    __table_args__ = (UniqueConstraint("call_id", "contact_id", name="uq_call_contact"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("contact.id"), nullable=False)
    opportunity_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("opportunity.id"))
    provenance: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="Verified by reviewer")
    verified_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineEvent(Base):
    __tablename__ = "pipeline_event"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_pipeline_idempotency"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    opportunity_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("opportunity.id", ondelete="CASCADE"), nullable=False)
    call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="SET NULL"))
    analysis_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("analysis_run.id", ondelete="SET NULL"))
    prior_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    new_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String(255))
    rule: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    effective_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FollowUp(Base):
    __tablename__ = "follow_up"
    __table_args__ = (
        UniqueConstraint("organization_id", "call_id", "logical_key", name="uq_followup_logical"),
        Index("ix_followup_org_status_due", "organization_id", "status", "due_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    analysis_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("analysis_run.id", ondelete="SET NULL"))
    opportunity_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("opportunity.id"))
    logical_key: Mapped[str] = mapped_column(String(200), nullable=False)
    proposed_action: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_text: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_approval")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetentionCase(Base):
    __tablename__ = "retention_case"
    __table_args__ = (UniqueConstraint("call_id", name="uq_retention_call"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("contact.id"))
    source_analysis_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("analysis_run.id", ondelete="SET NULL"))
    intent: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class ContactAddress(Base):
    __tablename__ = "contact_address"
    __table_args__ = (
        CheckConstraint("purpose IN ('service', 'billing', 'other')", name="ck_contact_address_purpose"),
        Index("ix_contact_address_contact", "contact_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    contact_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("contact.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    line1: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str | None] = mapped_column(String(2))
    provenance: Mapped[str] = mapped_column(String(80), nullable=False)
    source_call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AddressCheck(Base):
    """External address-component validation; never replaces transcript evidence."""

    __tablename__ = "address_check"
    __table_args__ = (
        CheckConstraint("purpose IN ('service', 'billing', 'other')", name="ck_address_check_purpose"),
        CheckConstraint(
            "status IN ('not_checked', 'validated_components', 'needs_confirmation', "
            "'insufficient_information', 'unsupported_region', 'check_failed')",
            name="ck_address_check_status",
        ),
        CheckConstraint("review_decision IS NULL OR review_decision IN ('accept', 'correct', 'reject')", name="ck_address_check_review"),
        Index("ix_address_check_call", "organization_id", "call_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("call_record.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("contact.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate: Mapped[dict] = mapped_column(JSONB, nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_checked")
    provider: Mapped[str | None] = mapped_column(String(40))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    review_decision: Mapped[str | None] = mapped_column(String(16))
    reviewed_fields: Mapped[dict | None] = mapped_column(JSONB)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContactAudit(Base):
    __tablename__ = "contact_audit"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    contact_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("contact.id", ondelete="CASCADE"), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    prior_version: Mapped[int] = mapped_column(Integer, nullable=False)
    new_version: Mapped[int] = mapped_column(Integer, nullable=False)
    change: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskEvent(Base):
    __tablename__ = "task_event"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    task_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("follow_up.id", ondelete="CASCADE"), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    prior_version: Mapped[int] = mapped_column(Integer, nullable=False)
    new_version: Mapped[int] = mapped_column(Integer, nullable=False)
    change: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
