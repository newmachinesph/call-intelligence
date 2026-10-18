"""Organization-scoped local contacts, call links and audited opportunities."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from call_intelligence_api.auth import (
    AuthenticatedMember,
    domain_engine,
    get_current_member,
)
from call_intelligence_api.call_models import (
    AnalysisRun,
    CallRecord,
    TranscriptSegment,
    TranscriptVersion,
)
from call_intelligence_api.crm_automation import (
    PROPOSAL_PREFIX,
    apply_analysis_automation,
    call_label,
)
from call_intelligence_api.crm_models import (
    CallLink,
    Contact,
    ContactAddress,
    ContactAudit,
    ContactIdentifier,
    FollowUp,
    Opportunity,
    PipelineEvent,
    RetentionCase,
)
from call_intelligence_api.crm_rules import (
    OpportunityStage,
    can_transition,
    normalize_identifier,
)
from call_intelligence_api.task_routes import (
    TaskResponse,
    decode_cursor,
    encode_cursor,
    task_responses,
)

router = APIRouter(prefix="/api/v1/crm", tags=["crm"])
Member = Annotated[AuthenticatedMember, Depends(get_current_member)]


class IdentifierInput(BaseModel):
    kind: Literal["email", "phone"]
    value: str = Field(min_length=3, max_length=255)


class ContactCreate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    identifiers: list[IdentifierInput] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def require_identity_hint(self) -> ContactCreate:
        if not self.display_name and not self.identifiers:
            raise ValueError("A name or identifier is required")
        return self


class ContactUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, max_length=200)
    reason: str = Field(min_length=3, max_length=1000)


class IdentifierResponse(BaseModel):
    kind: str
    value: str
    verified: bool
    provenance: str


class ContactResponse(BaseModel):
    id: UUID
    display_name: str | None
    identifiers: list[IdentifierResponse]
    version: int
    identity_candidate_ids: list[UUID]
    request_id: UUID


class ContactListResponse(BaseModel):
    items: list[ContactResponse]
    next_cursor: str | None
    request_id: UUID


class OpportunityCreate(BaseModel):
    contact_id: UUID
    service_interest: str | None = Field(default=None, max_length=255)


class OpportunityResponse(BaseModel):
    id: UUID
    contact_id: UUID
    contact_name: str | None
    service_interest: str | None
    stage: str
    owner_user_id: str | None
    manual_lock: bool
    version: int
    quoted_amount: str | None
    quote_currency: str | None
    request_id: UUID


class OpportunityListResponse(BaseModel):
    items: list[OpportunityResponse]
    next_cursor: str | None
    request_id: UUID


class CallLinkCreate(BaseModel):
    call_id: UUID
    contact_id: UUID
    opportunity_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=1000)
    match_provenance: str = Field(min_length=3, max_length=100)


class CallLinkResponse(BaseModel):
    id: UUID
    call_id: UUID
    contact_id: UUID
    opportunity_id: UUID | None
    request_id: UUID


class StageTransition(BaseModel):
    expected_version: int = Field(ge=1)
    target_stage: OpportunityStage
    reason: str = Field(min_length=3, max_length=1000)
    automation_lock: bool | None = None
    source_call_id: UUID | None = None
    source_segment_ids: list[UUID] = Field(default_factory=list, max_length=20)


class PipelineEventResponse(BaseModel):
    id: UUID
    prior_stage: str
    new_stage: str
    rule: str
    reason: str
    effective_call_at: datetime | None
    recorded_at: datetime


class LinkedCall(BaseModel):
    call_id: UUID
    call_label: str
    call_at: datetime | None
    primary_type: str | None
    outcome: str | None


class OpportunityDetailResponse(BaseModel):
    opportunity: OpportunityResponse
    linked_call_ids: list[UUID]
    linked_calls: list[LinkedCall]
    events: list[PipelineEventResponse]
    tasks: list[TaskResponse]
    request_id: UUID


class AddressCreate(BaseModel):
    purpose: Literal["service", "billing", "other"]
    line1: str | None = Field(default=None, max_length=255)
    unit: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    reason: str = Field(min_length=3, max_length=1000)


class AddressResponse(BaseModel):
    id: UUID
    purpose: str
    line1: str | None
    unit: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    country_code: str | None
    provenance: str
    source_call_id: UUID | None


class RetentionCaseResponse(BaseModel):
    id: UUID
    call_id: UUID
    call_label: str | None
    call_at: datetime | None
    contact_id: UUID | None
    intent: str
    outcome: str
    version: int
    source_refs: list[dict]
    request_id: UUID


class RetentionCaseListResponse(BaseModel):
    items: list[RetentionCaseResponse]
    next_cursor: str | None
    request_id: UUID


class RetentionUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    outcome: Literal["retained", "cancellation_reported", "open", "unknown"]
    reason: str = Field(min_length=3, max_length=1000)


class TimelineItem(BaseModel):
    kind: Literal["call_link", "pipeline_event", "task", "retention", "audit"]
    at: datetime
    summary: str
    call_id: UUID | None


class ContactDetailResponse(ContactResponse):
    addresses: list[AddressResponse]
    linked_calls: list[LinkedCall]
    opportunities: list[OpportunityResponse]
    retention_cases: list[RetentionCaseResponse]
    timeline: list[TimelineItem]


class ProposalResponse(BaseModel):
    id: UUID
    opportunity_id: UUID
    opportunity_version: int
    contact_id: UUID
    contact_name: str | None
    call_id: UUID | None
    call_label: str | None
    current_stage: str
    target_stage: str
    reason: str
    pending: bool
    source_refs: list[dict]
    effective_call_at: datetime | None
    recorded_at: datetime
    request_id: UUID


class ProposalListResponse(BaseModel):
    items: list[ProposalResponse]
    next_cursor: str | None
    request_id: UUID


class ProposalApply(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class ProposalDismiss(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


def _contact(session: Session, member: AuthenticatedMember, contact_id: UUID) -> Contact:
    contact = session.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.organization_id == member.organization_id)
    )
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def _opportunity(session: Session, member: AuthenticatedMember, opportunity_id: UUID) -> Opportunity:
    opportunity = session.scalar(
        select(Opportunity).where(Opportunity.id == opportunity_id, Opportunity.organization_id == member.organization_id)
    )
    if opportunity is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opportunity


def _contact_response(session: Session, contact: Contact) -> ContactResponse:
    identifiers = session.scalars(
        select(ContactIdentifier).where(ContactIdentifier.contact_id == contact.id)
    ).all()
    return ContactResponse(
        id=contact.id,
        display_name=contact.display_name,
        identifiers=[
            IdentifierResponse(
                kind=item.kind,
                value=item.normalized_value,
                verified=item.verified,
                provenance=item.provenance,
            )
            for item in identifiers
        ],
        version=contact.version,
        identity_candidate_ids=[],
        request_id=uuid4(),
    )


def _opportunity_response(session: Session, opportunity: Opportunity) -> OpportunityResponse:
    contact = session.get(Contact, opportunity.contact_id)
    return OpportunityResponse(
        id=opportunity.id,
        contact_id=opportunity.contact_id,
        contact_name=contact.display_name if contact else None,
        service_interest=opportunity.service_interest,
        stage=opportunity.stage,
        owner_user_id=opportunity.owner_user_id,
        manual_lock=opportunity.manual_lock,
        version=opportunity.version,
        quoted_amount=str(opportunity.quoted_amount) if opportunity.quoted_amount is not None else None,
        quote_currency=opportunity.quote_currency,
        request_id=uuid4(),
    )


def _linked_calls(session: Session, organization_id: UUID, *link_filters) -> list[CallRecord]:
    return list(session.scalars(
        select(CallRecord)
        .where(
            CallRecord.organization_id == organization_id,
            CallRecord.deletion_status == "active",
            CallRecord.id.in_(select(CallLink.call_id).where(CallLink.organization_id == organization_id, *link_filters)),
        )
        .order_by(CallRecord.uploaded_at.desc(), CallRecord.id.desc())
    ))


def _linked_call(call: CallRecord) -> LinkedCall:
    return LinkedCall(
        call_id=call.id, call_label=call_label(call), call_at=call.call_at,
        primary_type=call.primary_type, outcome=call.outcome,
    )


def _retention_response(case: RetentionCase, call: CallRecord | None) -> RetentionCaseResponse:
    return RetentionCaseResponse(
        id=case.id,
        call_id=case.call_id,
        call_label=call_label(call) if call else None,
        call_at=call.call_at if call else None,
        contact_id=case.contact_id,
        intent=case.intent,
        outcome=case.outcome,
        version=case.version,
        source_refs=case.source_refs,
        request_id=uuid4(),
    )


def _proposal_response(session: Session, event: PipelineEvent) -> ProposalResponse:
    opportunity = session.get(Opportunity, event.opportunity_id)
    contact = session.get(Contact, opportunity.contact_id)
    call = session.get(CallRecord, event.call_id) if event.call_id else None
    return ProposalResponse(
        id=event.id,
        opportunity_id=opportunity.id,
        opportunity_version=opportunity.version,
        contact_id=contact.id,
        contact_name=contact.display_name,
        call_id=event.call_id,
        call_label=call_label(call) if call else None,
        current_stage=opportunity.stage,
        target_stage=event.rule.removeprefix(PROPOSAL_PREFIX),
        reason=event.reason,
        pending=not event.superseded,
        source_refs=event.source_refs,
        effective_call_at=event.effective_call_at,
        recorded_at=event.recorded_at,
        request_id=uuid4(),
    )


def _identity_candidates(session: Session, contact: Contact) -> list[UUID]:
    """Other contacts sharing an exact normalized identifier; they stay separate until a reviewer decides."""
    own = select(ContactIdentifier.kind, ContactIdentifier.normalized_value).where(ContactIdentifier.contact_id == contact.id)
    ids = session.scalars(
        select(ContactIdentifier.contact_id).where(
            ContactIdentifier.organization_id == contact.organization_id,
            ContactIdentifier.contact_id != contact.id,
            tuple_(ContactIdentifier.kind, ContactIdentifier.normalized_value).in_(own),
        )
    ).all()
    return sorted(set(ids), key=str)


def _contact_detail(session: Session, contact: Contact) -> ContactDetailResponse:
    org = contact.organization_id
    base = _contact_response(session, contact)
    base.identity_candidate_ids = _identity_candidates(session, contact)
    addresses = session.scalars(
        select(ContactAddress).where(ContactAddress.contact_id == contact.id, ContactAddress.organization_id == org)
        .order_by(ContactAddress.created_at, ContactAddress.id)
    ).all()
    calls = _linked_calls(session, org, CallLink.contact_id == contact.id)
    call_by_id = {call.id: call for call in calls}
    links = session.scalars(select(CallLink).where(CallLink.contact_id == contact.id, CallLink.organization_id == org)).all()
    opportunities = session.scalars(
        select(Opportunity).where(Opportunity.contact_id == contact.id, Opportunity.organization_id == org)
        .order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
    ).all()
    opportunity_ids = [item.id for item in opportunities]
    cases = session.scalars(
        select(RetentionCase).where(
            RetentionCase.organization_id == org,
            or_(RetentionCase.contact_id == contact.id, RetentionCase.call_id.in_(list(call_by_id))),
        )
    ).all()
    events = session.scalars(
        select(PipelineEvent).where(PipelineEvent.organization_id == org, PipelineEvent.opportunity_id.in_(opportunity_ids))
    ).all()
    tasks = session.scalars(
        select(FollowUp).where(FollowUp.organization_id == org, FollowUp.call_id.in_(list(call_by_id)))
    ).all()
    audits = session.scalars(select(ContactAudit).where(ContactAudit.contact_id == contact.id)).all()
    case_calls = {case.call_id: call_by_id.get(case.call_id) or session.get(CallRecord, case.call_id) for case in cases}
    timeline = [
        TimelineItem(kind="call_link", at=link.created_at, call_id=link.call_id,
                     summary=f"Call linked: {call_label(call_by_id[link.call_id])}")
        for link in links if link.call_id in call_by_id
    ]
    for event in events:
        if event.rule.startswith(PROPOSAL_PREFIX):
            state = "pending review" if not event.superseded else "closed"
            summary = f"Proposed {event.rule.removeprefix(PROPOSAL_PREFIX)} ({state}): {event.reason}"
        else:
            summary = f"Stage {event.prior_stage} to {event.new_stage}{' (superseded)' if event.superseded else ''}: {event.reason}"
        timeline.append(TimelineItem(kind="pipeline_event", at=event.recorded_at, summary=summary, call_id=event.call_id))
    timeline += [
        TimelineItem(kind="task", at=task.created_at, summary=f"Task {task.status}: {task.proposed_action}", call_id=task.call_id)
        for task in tasks
    ]
    for case in cases:
        call = case_calls.get(case.call_id)
        if call is None or call.deletion_status != "active":
            continue
        timeline.append(TimelineItem(kind="retention", at=call.call_at or call.uploaded_at, call_id=case.call_id,
                                     summary=f"Retention {case.intent}: {case.outcome}"))
    timeline += [TimelineItem(kind="audit", at=audit.created_at, summary=audit.reason, call_id=None) for audit in audits]
    timeline.sort(key=lambda item: item.at, reverse=True)
    return ContactDetailResponse(
        **base.model_dump(),
        addresses=[AddressResponse.model_validate(address, from_attributes=True) for address in addresses],
        linked_calls=[_linked_call(call) for call in calls],
        opportunities=[_opportunity_response(session, item) for item in opportunities],
        retention_cases=[
            _retention_response(case, case_calls.get(case.call_id))
            for case in cases
            if case_calls.get(case.call_id) and case_calls[case.call_id].deletion_status == "active"
        ],
        timeline=timeline[:200],
    )


@router.post("/contacts", response_model=ContactResponse, status_code=201)
def create_contact(body: ContactCreate, member: Member) -> ContactResponse:
    normalized = []
    for item in body.identifiers:
        try:
            normalized.append((item.kind, normalize_identifier(item.kind, item.value)))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    with Session(domain_engine()) as session, session.begin():
        candidate_ids = set()
        for kind, value in normalized:
            candidate_ids.update(
                session.scalars(
                    select(ContactIdentifier.contact_id).where(
                        ContactIdentifier.organization_id == member.organization_id,
                        ContactIdentifier.kind == kind,
                        ContactIdentifier.normalized_value == value,
                    )
                ).all()
            )
        contact = Contact(organization_id=member.organization_id, display_name=body.display_name)
        session.add(contact)
        session.flush()
        for kind, value in normalized:
            session.add(
                ContactIdentifier(
                    organization_id=member.organization_id,
                    contact_id=contact.id,
                    kind=kind,
                    normalized_value=value,
                    verified=False,
                    provenance="manual_unverified",
                )
            )
        session.flush()
        result = _contact_response(session, contact)
        result.identity_candidate_ids = sorted(candidate_ids, key=str)
        return result


@router.get("/contacts", response_model=ContactListResponse)
def list_contacts(
    member: Member,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ContactListResponse:
    with Session(domain_engine()) as session:
        stmt = select(Contact).where(Contact.organization_id == member.organization_id)
        if q and q.strip():
            term = q.strip()
            stmt = stmt.where(or_(
                Contact.display_name.icontains(term, autoescape=True),
                Contact.id.in_(select(ContactIdentifier.contact_id).where(
                    ContactIdentifier.organization_id == member.organization_id,
                    ContactIdentifier.normalized_value.icontains(term, autoescape=True),
                )),
            ))
        if cursor:
            stmt = stmt.where(tuple_(Contact.created_at, Contact.id) < decode_cursor(cursor))
        contacts = session.scalars(stmt.order_by(Contact.created_at.desc(), Contact.id.desc()).limit(limit + 1)).all()
        return ContactListResponse(
            items=[_contact_response(session, contact) for contact in contacts[:limit]],
            next_cursor=encode_cursor(contacts[limit - 1].created_at, contacts[limit - 1].id) if len(contacts) > limit else None,
            request_id=uuid4(),
        )


@router.get("/contacts/{contact_id}", response_model=ContactDetailResponse)
def get_contact(contact_id: UUID, member: Member) -> ContactDetailResponse:
    with Session(domain_engine()) as session:
        return _contact_detail(session, _contact(session, member, contact_id))


@router.post("/contacts/{contact_id}/addresses", response_model=AddressResponse, status_code=201)
def add_contact_address(contact_id: UUID, body: AddressCreate, member: Member) -> AddressResponse:
    fields = body.model_dump(exclude={"reason"})
    if not any(value for key, value in fields.items() if key != "purpose"):
        raise HTTPException(status_code=422, detail="An address needs at least one component")
    with Session(domain_engine()) as session, session.begin():
        contact = session.scalar(select(Contact).where(Contact.id == contact_id).with_for_update())
        if not contact or contact.organization_id != member.organization_id:
            raise HTTPException(status_code=404, detail="Contact not found")
        address = ContactAddress(organization_id=member.organization_id, contact_id=contact.id, provenance="manual", **fields)
        session.add(address)
        contact.version += 1
        session.add(ContactAudit(
            organization_id=member.organization_id,
            contact_id=contact.id,
            actor_user_id=member.user_id,
            prior_version=contact.version - 1,
            new_version=contact.version,
            change={"address_added": fields},
            reason=body.reason,
        ))
        session.flush()
        return AddressResponse.model_validate(address, from_attributes=True)


@router.patch("/contacts/{contact_id}", response_model=ContactResponse)
def update_contact(contact_id: UUID, body: ContactUpdate, member: Member) -> ContactResponse:
    with Session(domain_engine()) as session, session.begin():
        contact = session.scalar(select(Contact).where(Contact.id == contact_id).with_for_update())
        if not contact or contact.organization_id != member.organization_id:
            raise HTTPException(status_code=404, detail="Contact not found")
        if contact.version != body.expected_version:
            raise HTTPException(status_code=409, detail="Contact version changed; refresh first")
        contact.display_name = body.display_name
        contact.version += 1
        session.add(ContactAudit(
            organization_id=member.organization_id,
            contact_id=contact.id,
            actor_user_id=member.user_id,
            prior_version=body.expected_version,
            new_version=contact.version,
            change={"display_name": body.display_name},
            reason=body.reason,
        ))
        return _contact_response(session, contact)


@router.post("/opportunities", response_model=OpportunityResponse, status_code=201)
def create_opportunity(body: OpportunityCreate, member: Member) -> OpportunityResponse:
    with Session(domain_engine()) as session, session.begin():
        _contact(session, member, body.contact_id)
        opportunity = Opportunity(
            organization_id=member.organization_id,
            contact_id=body.contact_id,
            service_interest=body.service_interest,
            stage=OpportunityStage.NEW.value,
        )
        session.add(opportunity)
        session.flush()
        return _opportunity_response(session, opportunity)


@router.get("/opportunities", response_model=OpportunityListResponse)
def list_opportunities(
    member: Member,
    stage: OpportunityStage | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> OpportunityListResponse:
    with Session(domain_engine()) as session:
        stmt = select(Opportunity).where(Opportunity.organization_id == member.organization_id)
        if stage:
            stmt = stmt.where(Opportunity.stage == stage.value)
        if cursor:
            # ponytail: keyed on updated_at, so a record edited mid-scroll can move pages; stable enough for a local CRM list.
            stmt = stmt.where(tuple_(Opportunity.updated_at, Opportunity.id) < decode_cursor(cursor))
        opportunities = session.scalars(
            stmt.order_by(Opportunity.updated_at.desc(), Opportunity.id.desc()).limit(limit + 1)
        ).all()
        last = opportunities[limit - 1] if len(opportunities) > limit else None
        return OpportunityListResponse(
            items=[_opportunity_response(session, item) for item in opportunities[:limit]],
            next_cursor=encode_cursor(last.updated_at, last.id) if last else None,
            request_id=uuid4(),
        )


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityDetailResponse)
def get_opportunity(opportunity_id: UUID, member: Member) -> OpportunityDetailResponse:
    with Session(domain_engine()) as session:
        opportunity = _opportunity(session, member, opportunity_id)
        links = session.scalars(
            select(CallLink).where(CallLink.opportunity_id == opportunity.id, CallLink.organization_id == member.organization_id)
        ).all()
        events = session.scalars(
            select(PipelineEvent)
            .where(PipelineEvent.opportunity_id == opportunity.id, PipelineEvent.organization_id == member.organization_id)
            .order_by(PipelineEvent.recorded_at, PipelineEvent.id)
        ).all()
        calls = _linked_calls(session, member.organization_id, CallLink.opportunity_id == opportunity.id)
        tasks = session.scalars(
            select(FollowUp)
            .where(FollowUp.organization_id == member.organization_id, FollowUp.call_id.in_([call.id for call in calls]))
            .order_by(FollowUp.created_at.desc(), FollowUp.id.desc())
        ).all()
        return OpportunityDetailResponse(
            opportunity=_opportunity_response(session, opportunity),
            linked_call_ids=[link.call_id for link in links],
            linked_calls=[_linked_call(call) for call in calls],
            tasks=task_responses(session, list(tasks)),
            events=[
                PipelineEventResponse(
                    id=event.id,
                    prior_stage=event.prior_stage,
                    new_stage=event.new_stage,
                    rule=event.rule,
                    reason=event.reason,
                    effective_call_at=event.effective_call_at,
                    recorded_at=event.recorded_at,
                )
                for event in events
            ],
            request_id=uuid4(),
        )


@router.post("/call-links", response_model=CallLinkResponse, status_code=201)
def link_call(body: CallLinkCreate, member: Member) -> CallLinkResponse:
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(
            select(CallRecord).where(
                CallRecord.id == body.call_id,
                CallRecord.organization_id == member.organization_id,
                CallRecord.deletion_status == "active",
            )
        )
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        _contact(session, member, body.contact_id)
        opportunity = _opportunity(session, member, body.opportunity_id) if body.opportunity_id else None
        if opportunity and opportunity.contact_id != body.contact_id:
            raise HTTPException(status_code=409, detail="Opportunity belongs to another contact")
        existing = session.scalar(
            select(CallLink).where(CallLink.call_id == call.id, CallLink.contact_id == body.contact_id)
        )
        if existing:
            if existing.opportunity_id != body.opportunity_id:
                raise HTTPException(status_code=409, detail="Call link already has a different opportunity")
            return CallLinkResponse(
                id=existing.id,
                call_id=existing.call_id,
                contact_id=existing.contact_id,
                opportunity_id=existing.opportunity_id,
                request_id=uuid4(),
            )
        link = CallLink(
            organization_id=member.organization_id,
            call_id=call.id,
            contact_id=body.contact_id,
            opportunity_id=body.opportunity_id,
            provenance=body.match_provenance,
            reason=body.reason,
            verified_by_user_id=member.user_id,
        )
        session.add(link)
        session.flush()
        analysis = session.get(AnalysisRun, call.active_analysis_id) if call.active_analysis_id else None
        if analysis and analysis.generation == call.requested_generation:
            # Same deterministic rules as publication; delivery keys make a repeat run a no-op.
            apply_analysis_automation(session, call, analysis)
        return CallLinkResponse(
            id=link.id,
            call_id=call.id,
            contact_id=body.contact_id,
            opportunity_id=body.opportunity_id,
            request_id=uuid4(),
        )


@router.post("/opportunities/{opportunity_id}/transitions", response_model=OpportunityResponse)
def transition_opportunity(
    opportunity_id: UUID,
    body: StageTransition,
    member: Member,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> OpportunityResponse:
    if not idempotency_key or len(idempotency_key) > 120:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required")
    with Session(domain_engine()) as session, session.begin():
        opportunity = session.scalar(select(Opportunity).where(Opportunity.id == opportunity_id).with_for_update())
        if not opportunity or opportunity.organization_id != member.organization_id:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        existing = session.scalar(
            select(PipelineEvent).where(
                PipelineEvent.organization_id == member.organization_id,
                PipelineEvent.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.opportunity_id != opportunity.id or existing.new_stage != body.target_stage.value:
                raise HTTPException(status_code=409, detail="Idempotency key was used for a different transition")
            return _opportunity_response(session, opportunity)
        if opportunity.version != body.expected_version:
            raise HTTPException(status_code=409, detail="Opportunity version changed; refresh first")
        prior = OpportunityStage(opportunity.stage)
        if not can_transition(prior, body.target_stage):
            raise HTTPException(status_code=409, detail="This stage transition requires a separate review")
        source_call = None
        source_analysis = None
        source_refs: list[dict] = []
        if body.target_stage in {OpportunityStage.WON, OpportunityStage.LOST}:
            if not body.source_call_id or not body.source_segment_ids:
                raise HTTPException(status_code=422, detail="A terminal outcome needs a linked call and current evidence")
            link = session.scalar(select(CallLink).where(
                CallLink.organization_id == member.organization_id,
                CallLink.opportunity_id == opportunity.id,
                CallLink.call_id == body.source_call_id,
            ))
            source_call = session.scalar(select(CallRecord).where(
                CallRecord.id == body.source_call_id,
                CallRecord.organization_id == member.organization_id,
                CallRecord.deletion_status == "active",
            ))
            if not link or not source_call or source_call.call_at is None:
                raise HTTPException(status_code=409, detail="Outcome needs a linked call with known chronology")
            source_analysis = session.get(AnalysisRun, source_call.active_analysis_id) if source_call.active_analysis_id else None
            transcript = session.get(TranscriptVersion, source_call.active_transcript_id) if source_call.active_transcript_id else None
            if not source_analysis or source_analysis.generation != source_call.requested_generation or not transcript:
                raise HTTPException(status_code=409, detail="Current source analysis is unavailable")
            segments = session.scalars(select(TranscriptSegment).where(
                TranscriptSegment.transcript_id == transcript.id,
                TranscriptSegment.id.in_(body.source_segment_ids),
            )).all()
            if len(segments) != len(set(body.source_segment_ids)):
                raise HTTPException(status_code=422, detail="Outcome evidence must name current transcript segments")
            source_refs = [{
                "source_kind": "transcript", "call_id": str(source_call.id),
                "transcript_version": transcript.version, "segment_ids": [str(segment.id)],
                "start_seconds": segment.start_seconds, "end_seconds": segment.end_seconds,
            } for segment in segments]
        opportunity.stage = body.target_stage.value
        opportunity.version += 1
        if body.automation_lock is not None:
            opportunity.manual_lock = body.automation_lock
        session.add(
            PipelineEvent(
                organization_id=member.organization_id,
                opportunity_id=opportunity.id,
                call_id=source_call.id if source_call else None,
                analysis_id=source_analysis.id if source_analysis else None,
                prior_stage=prior.value,
                new_stage=body.target_stage.value,
                actor_user_id=member.user_id,
                rule="manual_reviewed_transition",
                reason=body.reason,
                source_refs=source_refs,
                effective_call_at=source_call.call_at if source_call else None,
                idempotency_key=idempotency_key,
            )
        )
        return _opportunity_response(session, opportunity)


@router.get("/proposals", response_model=ProposalListResponse)
def list_proposals(
    member: Member,
    opportunity_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ProposalListResponse:
    with Session(domain_engine()) as session:
        stmt = (
            select(PipelineEvent)
            .outerjoin(CallRecord, PipelineEvent.call_id == CallRecord.id)
            .where(
                PipelineEvent.organization_id == member.organization_id,
                PipelineEvent.rule.startswith(PROPOSAL_PREFIX),
                PipelineEvent.superseded.is_(False),
                or_(PipelineEvent.call_id.is_(None), CallRecord.deletion_status == "active"),
            )
        )
        if opportunity_id:
            stmt = stmt.where(PipelineEvent.opportunity_id == opportunity_id)
        if cursor:
            stmt = stmt.where(tuple_(PipelineEvent.recorded_at, PipelineEvent.id) < decode_cursor(cursor))
        events = session.scalars(stmt.order_by(PipelineEvent.recorded_at.desc(), PipelineEvent.id.desc()).limit(limit + 1)).all()
        last = events[limit - 1] if len(events) > limit else None
        return ProposalListResponse(
            items=[_proposal_response(session, event) for event in events[:limit]],
            next_cursor=encode_cursor(last.recorded_at, last.id) if last else None,
            request_id=uuid4(),
        )


def _pending_proposal(session: Session, member: AuthenticatedMember, event_id: UUID) -> PipelineEvent:
    event = session.scalar(select(PipelineEvent).where(PipelineEvent.id == event_id).with_for_update())
    if not event or event.organization_id != member.organization_id or not event.rule.startswith(PROPOSAL_PREFIX):
        raise HTTPException(status_code=404, detail="Proposal not found")
    if event.superseded:
        raise HTTPException(status_code=409, detail="Proposal is no longer pending")
    return event


@router.post("/proposals/{event_id}/apply", response_model=OpportunityResponse)
def apply_proposal(event_id: UUID, body: ProposalApply, member: Member) -> OpportunityResponse:
    with Session(domain_engine()) as session, session.begin():
        proposal = _pending_proposal(session, member, event_id)
        opportunity = session.scalar(select(Opportunity).where(Opportunity.id == proposal.opportunity_id).with_for_update())
        if opportunity.version != body.expected_version:
            raise HTTPException(status_code=409, detail="Opportunity version changed; refresh first")
        if opportunity.stage != proposal.prior_stage:
            raise HTTPException(status_code=409, detail="Opportunity stage changed since this proposal; dismiss it instead")
        if proposal.call_id:
            call = session.get(CallRecord, proposal.call_id)
            if not call or call.deletion_status != "active":
                raise HTTPException(status_code=409, detail="The source call is no longer available")
        # A reviewer applying a proposal is the review that Won/Lost reopening requires.
        target = OpportunityStage(proposal.rule.removeprefix(PROPOSAL_PREFIX))
        opportunity.stage = target.value
        opportunity.version += 1
        proposal.superseded = True
        proposal.reason = f"{proposal.reason}\nApplied by reviewer: {body.reason}"
        session.add(PipelineEvent(
            organization_id=member.organization_id,
            opportunity_id=opportunity.id,
            call_id=proposal.call_id,
            analysis_id=proposal.analysis_id,
            prior_stage=proposal.prior_stage,
            new_stage=target.value,
            actor_user_id=member.user_id,
            rule="proposal_applied",
            reason=body.reason,
            source_refs=proposal.source_refs,
            effective_call_at=proposal.effective_call_at,
            idempotency_key=f"proposal:{proposal.id}:apply",
        ))
        session.flush()
        return _opportunity_response(session, opportunity)


@router.post("/proposals/{event_id}/dismiss", response_model=ProposalResponse)
def dismiss_proposal(event_id: UUID, body: ProposalDismiss, member: Member) -> ProposalResponse:
    with Session(domain_engine()) as session, session.begin():
        proposal = _pending_proposal(session, member, event_id)
        proposal.superseded = True
        proposal.reason = f"{proposal.reason}\nDismissed by reviewer {member.user_id}: {body.reason}"
        session.flush()
        return _proposal_response(session, proposal)


@router.get("/retention-cases", response_model=RetentionCaseListResponse)
def list_retention_cases(
    member: Member,
    outcome: Literal["retained", "cancellation_reported", "open", "unknown"] | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> RetentionCaseListResponse:
    with Session(domain_engine()) as session:
        stmt = (
            select(RetentionCase, CallRecord)
            .join(CallRecord, RetentionCase.call_id == CallRecord.id)
            .where(RetentionCase.organization_id == member.organization_id, CallRecord.deletion_status == "active")
        )
        if outcome:
            stmt = stmt.where(RetentionCase.outcome == outcome)
        if cursor:
            stmt = stmt.where(tuple_(CallRecord.uploaded_at, RetentionCase.id) < decode_cursor(cursor))
        rows = session.execute(stmt.order_by(CallRecord.uploaded_at.desc(), RetentionCase.id.desc()).limit(limit + 1)).all()
        last = rows[limit - 1] if len(rows) > limit else None
        return RetentionCaseListResponse(
            items=[_retention_response(case, call) for case, call in rows[:limit]],
            next_cursor=encode_cursor(last[1].uploaded_at, last[0].id) if last else None,
            request_id=uuid4(),
        )


@router.patch("/retention-cases/{case_id}", response_model=RetentionCaseResponse)
def update_retention_case(case_id: UUID, body: RetentionUpdate, member: Member) -> RetentionCaseResponse:
    with Session(domain_engine()) as session, session.begin():
        case = session.scalar(select(RetentionCase).where(RetentionCase.id == case_id).with_for_update())
        call = session.get(CallRecord, case.call_id) if case else None
        if not case or case.organization_id != member.organization_id or not call or call.deletion_status != "active":
            raise HTTPException(status_code=404, detail="Retention case not found")
        if case.version != body.expected_version:
            raise HTTPException(status_code=409, detail="Retention case version changed; refresh first")
        # ponytail: no retention audit table exists; the review entry lives in source_refs (kept across re-analysis).
        case.source_refs = [*(case.source_refs or []), {
            "source_kind": "review",
            "actor_user_id": member.user_id,
            "prior_version": case.version,
            "prior_outcome": case.outcome,
            "new_outcome": body.outcome,
            "reason": body.reason,
            "reviewed_at": datetime.now(UTC).isoformat(),
        }]
        case.outcome = body.outcome
        case.version += 1
        session.flush()
        return _retention_response(case, call)
