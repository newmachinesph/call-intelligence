"""Deterministic local CRM automation applied after analysis publication (specs/07).

Models only assess evidence. The pure `decide_*` functions choose permitted local changes; the applier persists
them in the caller's transaction with audit events. Nothing here sends messages or touches an external system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from call_intelligence_api.call_models import (
    AnalysisRun,
    CallRecord,
    TranscriptSegment,
    TranscriptVersion,
)
from call_intelligence_api.crm_models import (
    CallLink,
    FollowUp,
    Opportunity,
    PipelineEvent,
    RetentionCase,
    TaskEvent,
)
from call_intelligence_api.crm_rules import (
    OpportunityStage,
    TaskStatus,
    commitment_key,
    readable_action,
)

Stage = OpportunityStage
TERMINAL = {Stage.WON, Stage.LOST}
DECISION_TARGETS = {"accepted": Stage.WON, "rejected": Stage.LOST, "follow_up": Stage.FOLLOW_UP}
DECISION_RULES = {Stage.WON: "sales_accepted", Stage.LOST: "sales_rejected", Stage.FOLLOW_UP: "sales_follow_up"}
RETENTION_INTENTS = {"cancellation", "downgrade", "pause"}
RETENTION_OUTCOMES = {"retained", "cancellation_reported", "open", "unknown"}
COMMITMENT_KINDS = {"follow_up", "callback", "send_info", "schedule", "other"}
PROPOSAL_PREFIX = "proposed:"


@dataclass(frozen=True)
class StageContext:
    stage: str
    locked: bool
    is_sales: bool
    call_at_known: bool
    # An applied, unsuperseded event from a later-dated call already exists on this opportunity.
    newer_conflict: bool


@dataclass(frozen=True)
class StageAction:
    kind: Literal["apply", "propose"]
    prior: str
    target: str
    rule: str
    reason: str


@dataclass(frozen=True)
class TaskIntent:
    logical_key: str
    action: str
    kind: str
    owner_role: str
    due_text: str | None
    evidence_segment_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetentionIntent:
    intent: str
    reason: str | None
    outcome: str
    evidence_segment_ids: tuple[str, ...]


def call_label(call: CallRecord) -> str:
    return call.external_call_id or call.original_filename


def _propose(prior: Stage, target: Stage, reason: str) -> StageAction:
    return StageAction("propose", prior.value, target.value, f"{PROPOSAL_PREFIX}{target.value}", reason)


def decide_stage_actions(ctx: StageContext, decision: str, has_evidence: bool) -> list[StageAction]:
    """Stage actions for one linked opportunity. Only a linked sales conversation can move a sales stage."""
    if not ctx.is_sales:
        return []
    stage = Stage(ctx.stage)
    target = DECISION_TARGETS.get(decision)
    if stage in TERMINAL:
        # Won/Lost reopening is never automatic; evidence older than the established outcome does not reopen it.
        if target in TERMINAL and target != stage and not ctx.newer_conflict:
            return [_propose(stage, target, f"Linked call reports {decision} after a {stage.value} outcome; reopening needs review")]
        return []
    chain: list[Stage] = [Stage.CONTACTED] if stage == Stage.NEW else []
    if target and target != stage:
        chain.append(target)
    if not chain:
        return []
    blocker = (
        "opportunity is locked for manual review" if ctx.locked
        else "call time is unknown" if not ctx.call_at_known
        else "a later-dated call already changed this opportunity" if ctx.newer_conflict
        else None
    )
    if blocker:
        return [_propose(stage, chain[-1], f"Automatic change withheld: {blocker}")]
    actions = []
    for step in chain:
        if step == Stage.CONTACTED and stage == Stage.NEW:
            actions.append(StageAction("apply", stage.value, step.value, "sales_contacted", "Linked sales conversation"))
        elif not has_evidence:
            actions.append(_propose(stage, step, f"Sales decision '{decision}' lacks validated customer evidence"))
            break
        else:
            actions.append(StageAction("apply", stage.value, step.value, DECISION_RULES[step], f"Explicit sales decision: {decision}"))
        stage = step
    return actions


def decide_followups(call_id: str, commitments: list | None, sales_signal: dict | None) -> list[TaskIntent]:
    """Proposed local tasks; logical keys ignore segment UUIDs so re-analysis maps to the same task."""
    intents: dict[str, TaskIntent] = {}
    for item in commitments or []:
        if not isinstance(item, dict):
            continue
        action = readable_action(str(item.get("action") or ""))
        evidence = tuple(item.get("evidence_segment_ids") or ())
        if not action or not evidence:
            continue
        kind = item.get("kind") if item.get("kind") in COMMITMENT_KINDS else "other"
        key = commitment_key(call_id, kind, action)
        intents.setdefault(key, TaskIntent(key, action, kind, str(item.get("owner_role") or "unknown"), item.get("due_text"), evidence))
    signal = sales_signal or {}
    evidence = tuple(signal.get("evidence_segment_ids") or ())
    if not intents and signal.get("decision") == "follow_up" and evidence:
        action = "Follow up on the customer's pending sales decision"
        key = commitment_key(call_id, "follow_up", action)
        intents[key] = TaskIntent(key, action, "follow_up", "employee", None, evidence)
    return list(intents.values())


def decide_retention(retention: dict | None) -> RetentionIntent | None:
    """Retention cases record cancellation intent separately; they never touch the sales stage."""
    retention = retention or {}
    evidence = tuple(retention.get("evidence_segment_ids") or ())
    if retention.get("intent") not in RETENTION_INTENTS or not evidence:
        return None
    outcome = retention.get("outcome") if retention.get("outcome") in RETENTION_OUTCOMES else "unknown"
    return RetentionIntent(retention["intent"], retention.get("reason"), outcome, evidence)


def _refs(transcript: TranscriptVersion, segments: dict[str, TranscriptSegment], ids, call_id, **extra) -> list[dict]:
    return [
        {
            "source_kind": "transcript",
            "call_id": str(call_id),
            "transcript_version": transcript.version,
            "segment_ids": [segment_id],
            "start_seconds": segments[segment_id].start_seconds,
            "end_seconds": segments[segment_id].end_seconds,
            **extra,
        }
        for segment_id in ids
        if segment_id in segments
    ]


def _newer_conflict(session: Session, opportunity: Opportunity, call: CallRecord) -> bool:
    if call.call_at is None:
        return False
    return session.scalar(
        select(PipelineEvent.id).where(
            PipelineEvent.opportunity_id == opportunity.id,
            PipelineEvent.superseded.is_(False),
            PipelineEvent.prior_stage != PipelineEvent.new_stage,
            or_(PipelineEvent.call_id.is_(None), PipelineEvent.call_id != call.id),
            PipelineEvent.effective_call_at > call.call_at,
        ).limit(1)
    ) is not None


def is_untouched_automatic_task(task: FollowUp, current_run_id, keep_keys: set[str]) -> bool:
    """An automation-created task nobody has acted on, from an older analysis that no longer proposes it."""
    automatic = any(isinstance(ref, dict) and "commitment_kind" in ref for ref in task.source_refs or [])
    return (automatic and task.analysis_id is not None and task.analysis_id != current_run_id
            and task.status == TaskStatus.PENDING_APPROVAL.value and task.version == 1
            and task.logical_key not in keep_keys)


def _retire_stale_automatic_tasks(session: Session, call: CallRecord, run: AnalysisRun, keep_keys: set[str]) -> None:
    candidates = session.scalars(select(FollowUp).where(
        FollowUp.organization_id == call.organization_id, FollowUp.call_id == call.id,
        FollowUp.status == TaskStatus.PENDING_APPROVAL.value,
    )).all()
    for task in candidates:
        if not is_untouched_automatic_task(task, run.id, keep_keys):
            continue
        task.status = TaskStatus.CANCELLED.value
        task.version += 1
        session.add(TaskEvent(
            organization_id=call.organization_id, task_id=task.id, actor_user_id="system:reanalysis",
            prior_version=task.version - 1, new_version=task.version,
            change={"status": task.status, "superseded_by_analysis_id": str(run.id)},
            reason="A newer analysis of this call no longer proposes this follow-up",
        ))


def apply_analysis_automation(session: Session, call: CallRecord, run: AnalysisRun) -> None:
    """Apply specs/07 rules for the call's current analysis inside the caller's transaction."""
    intake = run.intake or {}
    transcript = session.get(TranscriptVersion, run.transcript_id)
    segments = {
        str(segment.id): segment
        for segment in session.scalars(select(TranscriptSegment).where(TranscriptSegment.transcript_id == run.transcript_id))
    }
    # Earlier automatic events for this call are superseded, never undone; manual events stay untouched.
    session.execute(
        update(PipelineEvent)
        .where(
            PipelineEvent.organization_id == call.organization_id,
            PipelineEvent.call_id == call.id,
            PipelineEvent.actor_user_id.is_(None),
            or_(PipelineEvent.analysis_id.is_(None), PipelineEvent.analysis_id != run.id),
            PipelineEvent.superseded.is_(False),
        )
        .values(superseded=True)
    )
    links = session.scalars(
        select(CallLink).where(CallLink.organization_id == call.organization_id, CallLink.call_id == call.id)
    ).all()
    opportunity_ids = sorted({link.opportunity_id for link in links if link.opportunity_id}, key=str)
    contact_ids = {link.contact_id for link in links}

    signal = intake.get("sales_signal") or {}
    signal_refs = _refs(transcript, segments, signal.get("evidence_segment_ids") or [], call.id)
    decision = str(signal.get("decision") or "unknown")
    # Won/Lost need the customer's own words with a confident role, so transcript text steering the model
    # cannot move a deal by citing an employee turn; otherwise the change becomes a proposal.
    spoken = [segments[s] for s in signal.get("evidence_segment_ids") or [] if s in segments]
    has_decision_evidence = bool(signal_refs) if decision == "follow_up" else any(
        s.speaker_role == "customer" and not s.role_uncertain for s in spoken)
    is_sales =(call.primary_type or intake.get("primary_type")) == "sales"
    for opportunity_id in opportunity_ids:
        opportunity = session.scalar(
            select(Opportunity)
            .where(Opportunity.id == opportunity_id, Opportunity.organization_id == call.organization_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if opportunity is None:
            continue
        ctx = StageContext(
            stage=opportunity.stage,
            locked=opportunity.manual_lock,
            is_sales=is_sales,
            call_at_known=call.call_at is not None,
            newer_conflict=_newer_conflict(session, opportunity, call),
        )
        for action in decide_stage_actions(ctx, decision, has_decision_evidence):
            key = f"analysis:{run.id}:{opportunity.id}:{action.rule}"
            if session.scalar(select(PipelineEvent.id).where(
                PipelineEvent.organization_id == call.organization_id, PipelineEvent.idempotency_key == key
            )):
                continue
            if action.kind == "apply":
                opportunity.stage = action.target
                opportunity.version += 1
            session.add(PipelineEvent(
                organization_id=call.organization_id,
                opportunity_id=opportunity.id,
                call_id=call.id,
                analysis_id=run.id,
                prior_stage=action.prior,
                new_stage=action.target if action.kind == "apply" else action.prior,
                actor_user_id=None,
                rule=action.rule,
                reason=action.reason,
                source_refs=signal_refs,
                effective_call_at=call.call_at,
                idempotency_key=key,
            ))
            session.flush()

    task_opportunity = opportunity_ids[0] if len(opportunity_ids) == 1 else None
    proposed = decide_followups(str(call.id), intake.get("commitments"), signal)
    _retire_stale_automatic_tasks(session, call, run, {task.logical_key for task in proposed})
    for task in proposed:
        refs = _refs(transcript, segments, task.evidence_segment_ids, call.id,
                     commitment_kind=task.kind, owner_role=task.owner_role)
        if not refs:
            continue
        session.execute(
            pg_insert(FollowUp)
            .values(
                organization_id=call.organization_id,
                call_id=call.id,
                analysis_id=run.id,
                opportunity_id=task_opportunity,
                logical_key=task.logical_key,
                proposed_action=task.action[:2000],
                due_text=str(task.due_text)[:255] if task.due_text else None,
                status=TaskStatus.PENDING_APPROVAL.value,
                source_refs=refs,
            )
            .on_conflict_do_nothing(constraint="uq_followup_logical")
        )
        if task_opportunity:
            session.execute(
                update(FollowUp)
                .where(
                    FollowUp.organization_id == call.organization_id,
                    FollowUp.call_id == call.id,
                    FollowUp.logical_key == task.logical_key,
                    FollowUp.opportunity_id.is_(None),
                )
                .values(opportunity_id=task_opportunity)
            )

    retention = decide_retention(intake.get("retention"))
    refs = _refs(transcript, segments, retention.evidence_segment_ids, call.id, retention_reason=retention.reason) if retention else []
    if not refs:
        return
    contact_id = next(iter(contact_ids)) if len(contact_ids) == 1 else None
    session.execute(
        pg_insert(RetentionCase)
        .values(
            organization_id=call.organization_id,
            call_id=call.id,
            contact_id=contact_id,
            source_analysis_id=run.id,
            intent=retention.intent,
            outcome=retention.outcome,
            source_refs=refs,
        )
        .on_conflict_do_nothing(constraint="uq_retention_call")
    )
    case = session.scalar(
        select(RetentionCase).where(RetentionCase.call_id == call.id).with_for_update().execution_options(populate_existing=True)
    )
    if case.contact_id is None and contact_id:
        case.contact_id = contact_id
    if case.source_analysis_id == run.id:
        return
    reviews = [ref for ref in case.source_refs or [] if ref.get("source_kind") == "review"]
    if not reviews:  # a reviewed case (intent and outcome) survives re-analysis
        case.intent = retention.intent
        case.outcome = retention.outcome
    case.source_analysis_id = run.id
    case.source_refs = reviews + refs
    case.version += 1
