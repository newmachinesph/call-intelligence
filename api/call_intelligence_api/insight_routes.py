"""Exact organization-scoped overview metrics, rep profiles and reviewed rep assignments."""

from __future__ import annotations

import json
import logging
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import Counter
from datetime import date as CalendarDate
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.orm import Session

from call_intelligence_api import metrics
from call_intelligence_api.auth import (
    AuthenticatedMember,
    domain_engine,
    get_current_member,
    require_owner,
)
from call_intelligence_api.call_models import (
    AnalysisRun,
    CallRecord,
    CriterionDecision,
    ReviewEvent,
    TranscriptSegment,
)
from call_intelligence_api.call_routes import CallSummary, summarize_calls
from call_intelligence_api.crm_models import FollowUp, Opportunity, Rep
from call_intelligence_api.crm_rules import TaskStatus, readable_action
from call_intelligence_api.evidence import resolve_segment
from call_intelligence_api.metrics import CallFilters
from call_intelligence_api.models import Membership

router = APIRouter(prefix="/api/v1", tags=["insights"])
Member = Annotated[AuthenticatedMember, Depends(get_current_member)]
Owner = Annotated[AuthenticatedMember, Depends(require_owner)]
logger = logging.getLogger(__name__)
OPEN_TASK_STATUSES = (TaskStatus.PENDING_APPROVAL.value, TaskStatus.ASSIGNED.value)
MAX_COACHING_EXAMPLES = 20


class MetricCount(BaseModel):
    label: str
    count: int
    denominator: int | None = None


class CriterionCount(MetricCount):
    rubric_id: str | None


class RateMetric(BaseModel):
    key: str
    label: str
    numerator: int
    denominator: int
    value: float | None
    exclusions: list[MetricCount]
    definition: str


class FollowUpDue(BaseModel):
    id: UUID
    proposed_action: str
    due_at: datetime | None
    due_text: str | None
    status: str
    owner_user_id: str | None
    call_id: UUID
    call_label: str


class ActivityPoint(BaseModel):
    date: CalendarDate
    count: int


class RepMetric(BaseModel):
    id: UUID | None
    display_name: str
    verified: bool
    analyzed_calls: int
    scored_calls: int
    unknown_date_calls: int
    points_earned: int
    points_possible: int


class OverviewResponse(BaseModel):
    uploaded_calls: int
    analyzed_calls: int
    processing_calls: int
    awaiting_processing_calls: int = 0
    needs_review_calls: int
    unknown_date_calls: int
    by_type: list[MetricCount]
    by_outcome: list[MetricCount]
    missed_criteria: list[CriterionCount]
    opportunities_by_stage: list[MetricCount]
    tasks_by_status: list[MetricCount]
    reps: list[RepMetric]
    metrics: list[RateMetric]
    follow_ups_due: list[FollowUpDue]
    unassigned_follow_ups: int
    pending_approval_follow_ups: int
    upload_activity: list[ActivityPoint]
    call_activity: list[ActivityPoint]
    request_id: UUID


class RepCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class RepResponse(BaseModel):
    id: UUID
    display_name: str
    verified: bool
    request_id: UUID


class RepAssignment(BaseModel):
    rep_id: UUID
    expected_generation: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class RepSummary(BaseModel):
    id: UUID
    display_name: str
    verified: bool
    identity_source: str
    analyzed_calls: int
    scored_calls: int


class RepSummaryResponse(RepSummary):
    request_id: UUID


class RepListResponse(BaseModel):
    items: list[RepSummary]
    next_cursor: str | None
    request_id: UUID


class RepUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    verified: bool | None = None
    reason: str = Field(min_length=3, max_length=1000)


class RubricRollup(BaseModel):
    rubric_id: str | None
    calls: int
    scored_calls: int
    points_earned: int
    points_possible: int
    grade_distribution: list[MetricCount]
    missed_criteria: list[MetricCount]


class CoachingExample(BaseModel):
    call_id: UUID
    call_label: str
    criterion_ids: list[str]
    observation: str
    start_seconds: float | None


class RepDetailResponse(BaseModel):
    rep: RepSummary
    by_rubric: list[RubricRollup]
    coaching_examples: list[CoachingExample]
    calls: list[CallSummary]
    request_id: UUID


class MemberItem(BaseModel):
    user_id: str
    role: str
    is_current_user: bool


class MemberListResponse(BaseModel):
    items: list[MemberItem]
    request_id: UUID


def _counts(rows) -> list[MetricCount]:
    return [MetricCount(label=str(label), count=count) for label, count in rows]


def _ranked(counter: Counter[str]) -> list[tuple[str, int]]:
    """Same ordering as metrics.aggregate: most frequent first, then label."""
    return sorted(counter.items(), key=lambda row: (-row[1], row[0]))


def _call_label(external_call_id: str | None, filename: str) -> str:
    return external_call_id or filename


def _active_calls(organization_id: UUID, filters: CallFilters | None) -> Select:
    """All non-deleted calls matching the filters (the upload-side population)."""
    return metrics.apply_call_filters(select(CallRecord).where(
        CallRecord.organization_id == organization_id,
        CallRecord.deletion_status == "active",
    ), filters)


def _count(session: Session, subquery, *where) -> int:
    return session.scalar(select(func.count()).select_from(subquery).where(*where)) or 0


def _criterion_state_rows(session: Session, organization_id: UUID, eligible) -> list[tuple]:
    return session.execute(
        select(AnalysisRun.rubric_id, CriterionDecision.criterion_id, CriterionDecision.state, func.count())
        .select_from(CriterionDecision)
        .join(eligible, CriterionDecision.analysis_id == eligible.c.active_analysis_id)
        .join(AnalysisRun, AnalysisRun.id == CriterionDecision.analysis_id)
        .where(CriterionDecision.organization_id == organization_id)
        .group_by(AnalysisRun.rubric_id, CriterionDecision.criterion_id, CriterionDecision.state)
    ).all()


def _activity(session: Session, column) -> list[ActivityPoint]:
    day = func.date(func.timezone("UTC", column))
    rows = session.execute(select(day, func.count()).where(column.is_not(None)).group_by(day).order_by(day)).all()
    return [ActivityPoint(date=value, count=count) for value, count in rows]


@router.get("/analytics/overview", response_model=OverviewResponse)
@router.get("/insights/overview", response_model=OverviewResponse)
def overview(member: Member, filters: Annotated[CallFilters, Query()]) -> OverviewResponse:
    org = member.organization_id
    with Session(domain_engine()) as session:
        uploaded = _active_calls(org, filters).subquery()
        eligible = metrics.eligible_calls(org, filters).subquery()
        # Grouped reads instead of one query per number: each round trip to Neon is costly.
        # "Awaiting processing" = verified uploads nobody has started, plus hand-offs that failed.
        awaiting = uploaded.c.processing_status.in_(["uploaded"]) | (func.coalesce(uploaded.c.error_code, "") == "dispatch_failed")
        uploaded_count, processing_calls, unknown_date_calls, awaiting_processing_calls = session.execute(select(
            func.count(), func.count().filter(uploaded.c.processing_status.not_in(["complete", "uploaded"]) & ~awaiting),
            func.count().filter(uploaded.c.call_at.is_(None)), func.count().filter(awaiting),
        ).select_from(uploaded)).one()
        groups = session.execute(
            select(eligible.c.primary_type, eligible.c.outcome, eligible.c.outcome_override,
                   eligible.c.analysis_status, func.count())
            .group_by(eligible.c.primary_type, eligible.c.outcome, eligible.c.outcome_override,
                      eligible.c.analysis_status)
        ).all()
        analyzed = sum(row[4] for row in groups)
        needs_review = sum(row[4] for row in groups if row[3] == "needs_review")
        by_type: Counter[str] = Counter()
        by_outcome: Counter[str] = Counter()
        for primary_type, outcome, _override, _status, count in groups:
            by_type[primary_type or "unknown"] += count
            by_outcome[outcome or "unknown"] += count

        rate_metrics = [
            RateMetric(key="analyzed_share", label="Analyzed calls", numerator=analyzed,
                       denominator=uploaded_count, value=metrics.rate(analyzed, uploaded_count),
                       exclusions=[MetricCount(label="No current analysis (processing, failed or re-analysis pending)",
                                               count=uploaded_count - analyzed)],
                       definition="Share of uploaded calls (matching your filters) that have a finished, "
                                  "up-to-date analysis. Calls still processing, failed or being re-analyzed are "
                                  "not counted yet."),
            RateMetric(key="needs_review_share", label="Analyzed calls needing review", numerator=needs_review,
                       denominator=analyzed, value=metrics.rate(needs_review, analyzed),
                       exclusions=[MetricCount(label="Not yet analyzed", count=uploaded_count - analyzed)],
                       definition="Share of analyzed calls the AI flagged for a person to check, for example "
                                  "an unclear fact or a scorecard item it could not decide."),
        ]
        for key, spec in metrics.REVIEWED_RATE_OUTCOME_CLASSES.items():
            override_counts: Counter[str | None] = Counter()
            for primary_type, _outcome, override, _status, count in groups:
                if primary_type == spec["call_type"]:
                    override_counts[override] += count
            rate_metrics.append(RateMetric.model_validate(metrics.reviewed_rate(key, override_counts.items())))

        missed = [CriterionCount(label=item["criterion_id"], count=item["missed"],
                                 denominator=item["applicable"], rubric_id=item["rubric_id"])
                  for item in metrics.criterion_rates(_criterion_state_rows(session, org, eligible))]

        scored = metrics.scored_clause()
        rep_rows = session.execute(
            select(eligible.c.rep_id, func.count(), func.count().filter(scored),
                   func.count().filter(eligible.c.call_at.is_(None)),
                   func.coalesce(func.sum(AnalysisRun.confirmed_points).filter(scored), 0),
                   func.coalesce(func.sum(AnalysisRun.nominal_maximum).filter(scored), 0))
            .select_from(eligible)
            .join(AnalysisRun, AnalysisRun.id == eligible.c.active_analysis_id)
            .group_by(eligible.c.rep_id)
        ).all()
        rep_by_id = {rep.id: rep for rep in session.scalars(select(Rep).where(
            Rep.organization_id == org, Rep.id.in_([row[0] for row in rep_rows if row[0]]))).all()}
        reps = []
        for rep_id, calls, scored_calls, unknown_date, earned, possible in sorted(rep_rows, key=lambda row: str(row[0])):
            rep = rep_by_id.get(rep_id)
            reps.append(RepMetric(
                id=rep.id if rep else None, display_name=rep.display_name if rep else "Unknown rep",
                verified=rep.verified if rep else False, analyzed_calls=calls, scored_calls=scored_calls,
                unknown_date_calls=unknown_date, points_earned=earned, points_possible=possible,
            ))

        # Tasks follow the filtered, non-deleted call set; opportunities are an org-level measure.
        tasks = (select(FollowUp).join(uploaded, FollowUp.call_id == uploaded.c.id)
                 .where(FollowUp.organization_id == org).subquery())
        task_groups = session.execute(
            select(tasks.c.status, tasks.c.owner_user_id.is_(None), func.count())
            .group_by(tasks.c.status, tasks.c.owner_user_id.is_(None))
        ).all()
        tasks_by_status: Counter[str] = Counter()
        for task_status, _ownerless, count in task_groups:
            tasks_by_status[task_status] += count
        due_rows = session.execute(
            select(FollowUp, uploaded.c.external_call_id, uploaded.c.original_filename)
            .join(uploaded, FollowUp.call_id == uploaded.c.id)
            .where(FollowUp.organization_id == org, FollowUp.status.in_(OPEN_TASK_STATUSES))
            .order_by(FollowUp.due_at.asc().nulls_last(), FollowUp.created_at, FollowUp.id)
            .limit(5)
        ).all()

        return OverviewResponse(
            uploaded_calls=uploaded_count, analyzed_calls=analyzed,
            processing_calls=processing_calls, awaiting_processing_calls=awaiting_processing_calls,
            needs_review_calls=needs_review,
            unknown_date_calls=unknown_date_calls,
            by_type=_counts(_ranked(by_type)),
            by_outcome=_counts(_ranked(by_outcome)),
            missed_criteria=missed,
            opportunities_by_stage=_counts(session.execute(
                select(Opportunity.stage, func.count()).where(Opportunity.organization_id == org)
                .group_by(Opportunity.stage).order_by(Opportunity.stage)).all()),
            tasks_by_status=_counts(sorted(tasks_by_status.items())),
            reps=reps, metrics=rate_metrics,
            follow_ups_due=[FollowUpDue(
                id=task.id, proposed_action=readable_action(task.proposed_action), due_at=task.due_at, due_text=task.due_text,
                status=task.status, owner_user_id=task.owner_user_id, call_id=task.call_id,
                call_label=_call_label(external_id, filename),
            ) for task, external_id, filename in due_rows],
            unassigned_follow_ups=sum(count for task_status, ownerless, count in task_groups
                                      if ownerless and task_status in OPEN_TASK_STATUSES),
            pending_approval_follow_ups=tasks_by_status[TaskStatus.PENDING_APPROVAL.value],
            upload_activity=_activity(session, uploaded.c.uploaded_at),
            call_activity=_activity(session, uploaded.c.call_at),
            request_id=uuid4(),
        )


def _rep_summaries(session: Session, organization_id: UUID, reps: list[Rep]) -> list[RepSummary]:
    eligible = metrics.eligible_calls(organization_id).subquery()
    rows = {rep_id: (calls, scored) for rep_id, calls, scored in session.execute(
        select(eligible.c.rep_id, func.count(), func.count().filter(metrics.scored_clause()))
        .select_from(eligible)
        .join(AnalysisRun, AnalysisRun.id == eligible.c.active_analysis_id)
        .where(eligible.c.rep_id.in_([rep.id for rep in reps]))
        .group_by(eligible.c.rep_id)
    ).all()} if reps else {}
    return [RepSummary(id=rep.id, display_name=rep.display_name, verified=rep.verified,
                       identity_source=rep.identity_source, analyzed_calls=rows.get(rep.id, (0, 0))[0],
                       scored_calls=rows.get(rep.id, (0, 0))[1]) for rep in reps]


def _org_rep(session: Session, organization_id: UUID, rep_id: UUID, *, lock: bool = False) -> Rep:
    stmt = select(Rep).where(Rep.id == rep_id, Rep.organization_id == organization_id)
    rep = session.scalar(stmt.with_for_update() if lock else stmt)
    if rep is None:
        raise HTTPException(status_code=404, detail="Rep not found")
    return rep


@router.post("/reps", response_model=RepResponse, status_code=201)
def create_rep(body: RepCreate, member: Member) -> RepResponse:
    with Session(domain_engine()) as session, session.begin():
        rep = Rep(organization_id=member.organization_id, display_name=body.display_name.strip())
        session.add(rep)
        session.flush()
        return RepResponse(id=rep.id, display_name=rep.display_name, verified=False, request_id=uuid4())


@router.get("/reps", response_model=RepListResponse)
def list_reps(member: Member, cursor: str | None = None,
              limit: Annotated[int, Query(ge=1, le=100)] = 50) -> RepListResponse:
    with Session(domain_engine()) as session:
        stmt = select(Rep).where(Rep.organization_id == member.organization_id)
        if cursor:
            try:
                name, raw_id = json.loads(urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4)))
                stmt = stmt.where(tuple_(Rep.display_name, Rep.id) > (str(name), UUID(raw_id)))
            except (ValueError, TypeError, AttributeError, UnicodeError) as exc:
                raise HTTPException(status_code=422, detail="Invalid rep cursor") from exc
        reps = list(session.scalars(stmt.order_by(Rep.display_name, Rep.id).limit(limit + 1)).all())
        next_cursor = None
        if len(reps) > limit:
            reps = reps[:limit]
            last = reps[-1]
            next_cursor = urlsafe_b64encode(json.dumps([last.display_name, str(last.id)]).encode()).decode().rstrip("=")
        return RepListResponse(items=_rep_summaries(session, member.organization_id, reps),
                               next_cursor=next_cursor, request_id=uuid4())


@router.patch("/reps/{rep_id}", response_model=RepSummaryResponse)
def update_rep(rep_id: UUID, body: RepUpdate, member: Owner) -> RepSummaryResponse:
    if body.display_name is None and body.verified is None:
        raise HTTPException(status_code=422, detail="Nothing to change")
    with Session(domain_engine()) as session, session.begin():
        rep = _org_rep(session, member.organization_id, rep_id, lock=True)
        changed = []
        if body.display_name is not None and body.display_name.strip() != rep.display_name:
            rep.display_name = body.display_name.strip()
            changed.append("display_name")
        if body.verified is not None and body.verified != rep.verified:
            rep.verified = body.verified
            changed.append("verified")
        session.flush()
        # ponytail: no rep audit table yet; content-free log line only (reason is validated, not stored).
        logger.info("rep_updated rep_id=%s actor=%s fields=%s", rep.id, member.user_id, ",".join(changed) or "none")
        summary = _rep_summaries(session, member.organization_id, [rep])[0]
        return RepSummaryResponse(**summary.model_dump(), request_id=uuid4())


@router.get("/reps/{rep_id}", response_model=RepDetailResponse)
def rep_detail(rep_id: UUID, member: Member) -> RepDetailResponse:
    org = member.organization_id
    with Session(domain_engine()) as session:
        rep = _org_rep(session, org, rep_id)
        eligible = metrics.eligible_calls(org, CallFilters(rep_id=rep.id)).subquery()
        runs = session.execute(
            select(AnalysisRun.rubric_id, AnalysisRun.grade, AnalysisRun.confirmed_points,
                   AnalysisRun.nominal_maximum, AnalysisRun.unresolved_count)
            .select_from(eligible).join(AnalysisRun, AnalysisRun.id == eligible.c.active_analysis_id)
        ).all()
        by_rubric = metrics.rubric_rollup(runs, _criterion_state_rows(session, org, eligible))

        coaching_rows = session.execute(
            select(eligible.c.id, eligible.c.external_call_id, eligible.c.original_filename,
                   eligible.c.active_transcript_id, AnalysisRun.transcript_id, AnalysisRun.coaching)
            .select_from(eligible).join(AnalysisRun, AnalysisRun.id == eligible.c.active_analysis_id)
            .order_by(eligible.c.call_at.desc().nulls_last(), eligible.c.uploaded_at.desc(), eligible.c.id)
        ).all()
        examples: list[tuple[dict, UUID | None, UUID, str]] = []
        for call_id, external_id, filename, active_transcript, run_transcript, coaching in coaching_rows:
            # Segment IDs/aliases only resolve against the transcript the analysis was produced from.
            transcript = run_transcript if run_transcript == active_transcript else None
            for item in metrics.coaching_improvements(coaching):
                examples.append((item, transcript, call_id, _call_label(external_id, filename)))
        examples = examples[:MAX_COACHING_EXAMPLES]
        transcript_ids = {transcript for item, transcript, _, _ in examples if transcript and item["evidence_segment_ids"]}
        segments: dict[UUID, list[TranscriptSegment]] = {}
        if transcript_ids:
            for segment in session.scalars(select(TranscriptSegment).where(
                    TranscriptSegment.organization_id == org,
                    TranscriptSegment.transcript_id.in_(transcript_ids))):
                segments.setdefault(segment.transcript_id, []).append(segment)
        coaching_examples = []
        for item, transcript, call_id, label in examples:
            segment = (resolve_segment(item["evidence_segment_ids"][0], segments.get(transcript, []))
                       if transcript and item["evidence_segment_ids"] else None)
            coaching_examples.append(CoachingExample(
                call_id=call_id, call_label=label, criterion_ids=item["criterion_ids"],
                observation=item["observation"], start_seconds=segment.start_seconds if segment else None,
            ))

        calls = list(session.scalars(select(CallRecord).where(
            CallRecord.organization_id == org, CallRecord.deletion_status == "active", CallRecord.rep_id == rep.id,
        ).order_by(CallRecord.uploaded_at.desc(), CallRecord.id.desc()).limit(50)).all())
        return RepDetailResponse(
            rep=_rep_summaries(session, org, [rep])[0],
            by_rubric=[RubricRollup.model_validate(row) for row in by_rubric],
            coaching_examples=coaching_examples,
            calls=summarize_calls(session, calls),
            request_id=uuid4(),
        )


@router.get("/members", response_model=MemberListResponse)
def list_members(member: Member) -> MemberListResponse:
    with Session(domain_engine()) as session:
        rows = session.scalars(select(Membership).where(
            Membership.organization_id == member.organization_id, Membership.active.is_(True),
        ).order_by(Membership.created_at, Membership.id)).all()
        return MemberListResponse(items=[MemberItem(
            user_id=row.user_id, role=row.role, is_current_user=row.user_id == member.user_id,
        ) for row in rows], request_id=uuid4())


@router.patch("/calls/{call_id}/rep", response_model=RepResponse)
def assign_rep(call_id: UUID, body: RepAssignment, member: Member) -> RepResponse:
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if call is None or call.organization_id != member.organization_id or call.deletion_status != "active":
            raise HTTPException(status_code=404, detail="Call not found")
        if call.requested_generation != body.expected_generation:
            raise HTTPException(status_code=409, detail="Call generation changed; refresh first")
        rep = session.scalar(select(Rep).where(Rep.id == body.rep_id, Rep.organization_id == member.organization_id))
        if rep is None:
            raise HTTPException(status_code=404, detail="Rep not found")
        prior = call.rep_id
        call.rep_id = rep.id
        session.add(ReviewEvent(
            organization_id=member.organization_id, call_id=call.id, actor_user_id=member.user_id,
            kind="rep_assignment", prior_version=call.requested_generation,
            new_version=call.requested_generation,
            change={"prior_rep_id": str(prior) if prior else None, "new_rep_id": str(rep.id)},
            reason=body.reason,
        ))
        return RepResponse(id=rep.id, display_name=rep.display_name, verified=rep.verified, request_id=uuid4())
