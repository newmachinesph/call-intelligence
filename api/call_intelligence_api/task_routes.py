"""Local follow-up proposals and reviewed assignment; no messages are sent."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from call_intelligence_api.auth import (
    AuthenticatedMember,
    domain_engine,
    get_current_member,
)
from call_intelligence_api.call_models import (
    CallRecord,
    TranscriptSegment,
    TranscriptVersion,
)
from call_intelligence_api.crm_automation import call_label
from call_intelligence_api.crm_models import FollowUp, TaskEvent
from call_intelligence_api.crm_rules import (
    ALLOWED_TASK_TRANSITIONS,
    TaskStatus,
    logical_followup_key,
    readable_action,
)
from call_intelligence_api.models import Membership

router = APIRouter(prefix="/api/v1", tags=["tasks"])
Member = Annotated[AuthenticatedMember, Depends(get_current_member)]


class FollowUpCreate(BaseModel):
    expected_generation: int = Field(ge=1)
    proposed_action: str = Field(min_length=3, max_length=2000)
    source_segment_ids: list[UUID] = Field(min_length=1, max_length=20)
    due_at: datetime | None = None
    due_text: str | None = Field(default=None, max_length=255)


class TaskUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    status: TaskStatus | None = None
    owner_user_id: str | None = None
    due_at: datetime | None = None
    due_text: str | None = Field(default=None, max_length=255)
    reason: str = Field(min_length=3, max_length=1000)


class TaskResponse(BaseModel):
    id: UUID
    call_id: UUID
    opportunity_id: UUID | None
    proposed_action: str
    owner_user_id: str | None
    due_at: datetime | None
    due_text: str | None
    status: str
    version: int
    source_refs: list[dict]
    call_label: str | None = None
    source_quote: str | None = None
    kind: str = "follow_up"
    request_id: UUID


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    next_cursor: str | None
    request_id: UUID


OPEN_TASK_STATUSES = (TaskStatus.PENDING_APPROVAL.value, TaskStatus.ASSIGNED.value)
DueBucket = Literal["overdue", "today", "upcoming", "unknown"]


def encode_cursor(at: datetime, item_id: UUID) -> str:
    return urlsafe_b64encode(json.dumps([at.isoformat(), str(item_id)]).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        if len(cursor) > 512:
            raise ValueError("Cursor too long")
        raw = json.loads(urlsafe_b64decode(cursor.encode("ascii") + b"=" * (-len(cursor) % 4)))
        at, item_id = datetime.fromisoformat(raw[0]), UUID(raw[1])
        if at.tzinfo is None:
            raise ValueError("Cursor must have a timezone")
        return at, item_id
    except (Base64Error, ValueError, TypeError, IndexError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """UTC calendar-day boundaries used by the due filter."""
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def due_bucket(due_at: datetime | None, now: datetime) -> DueBucket:
    """Mirror of the SQL due filter: past due, later today (UTC), after today, or no known due time."""
    if due_at is None:
        return "unknown"
    _, tomorrow = day_bounds(now)
    if due_at < now:
        return "overdue"
    return "today" if due_at < tomorrow else "upcoming"


def task_kind(source_refs: list[dict] | None) -> str:
    return next((ref["commitment_kind"] for ref in source_refs or [] if ref.get("commitment_kind")), "follow_up")


def task_responses(session: Session, tasks: list[FollowUp]) -> list[TaskResponse]:
    """Batch call labels and current-version source quotes; a superseded transcript yields no quote."""
    calls = {call.id: call for call in session.scalars(select(CallRecord).where(CallRecord.id.in_({t.call_id for t in tasks})))}
    first_ids = {}
    for task in tasks:
        segment_ids = next((ref.get("segment_ids") for ref in task.source_refs or [] if ref.get("segment_ids")), None)
        try:
            first_ids[task.id] = UUID(str(segment_ids[0])) if segment_ids else None
        except ValueError:
            first_ids[task.id] = None
    segments = {
        segment.id: segment
        for segment in session.scalars(
            select(TranscriptSegment).where(TranscriptSegment.id.in_({i for i in first_ids.values() if i}))
        )
    }
    results = []
    for task in tasks:
        call = calls.get(task.call_id)
        segment = segments.get(first_ids[task.id])
        current = segment is not None and call is not None and segment.transcript_id == call.active_transcript_id
        response = _response(task)
        response.call_label = call_label(call) if call else None
        response.source_quote = segment.text if current else None
        response.kind = task_kind(task.source_refs)
        results.append(response)
    return results


def _response(task: FollowUp) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        call_id=task.call_id,
        opportunity_id=task.opportunity_id,
        proposed_action=readable_action(task.proposed_action),
        owner_user_id=task.owner_user_id,
        due_at=task.due_at,
        due_text=task.due_text,
        status=task.status,
        version=task.version,
        source_refs=task.source_refs,
        request_id=uuid4(),
    )


@router.post("/calls/{call_id}/follow-ups", response_model=TaskResponse, status_code=201)
def create_follow_up(call_id: UUID, body: FollowUpCreate, member: Member) -> TaskResponse:
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(
            select(CallRecord).where(
                CallRecord.id == call_id,
                CallRecord.organization_id == member.organization_id,
                CallRecord.deletion_status == "active",
            )
        )
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        if call.requested_generation != body.expected_generation:
            raise HTTPException(status_code=409, detail="Call generation changed; refresh first")
        transcript = session.get(TranscriptVersion, call.active_transcript_id) if call.active_transcript_id else None
        if transcript is None:
            raise HTTPException(status_code=409, detail="Transcript is not ready")
        segments = session.scalars(
            select(TranscriptSegment).where(
                TranscriptSegment.transcript_id == transcript.id,
                TranscriptSegment.id.in_(body.source_segment_ids),
            )
        ).all()
        if len(segments) != len(set(body.source_segment_ids)):
            raise HTTPException(status_code=422, detail="Each source segment must exist in the current transcript")
        logical_key = logical_followup_key([str(segment.id) for segment in segments], body.proposed_action)
        existing = session.scalar(
            select(FollowUp).where(
                FollowUp.organization_id == member.organization_id,
                FollowUp.call_id == call.id,
                FollowUp.logical_key == logical_key,
            )
        )
        if existing:
            return task_responses(session, [existing])[0]
        refs = [
            {
                "source_kind": "transcript",
                "call_id": str(call.id),
                "transcript_version": transcript.version,
                "segment_ids": [str(segment.id)],
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
            }
            for segment in segments
        ]
        task = FollowUp(
            organization_id=member.organization_id,
            call_id=call.id,
            analysis_id=call.active_analysis_id,
            logical_key=logical_key,
            proposed_action=body.proposed_action,
            due_at=body.due_at,
            due_text=body.due_text,
            status=TaskStatus.PENDING_APPROVAL.value,
            source_refs=refs,
        )
        session.add(task)
        session.flush()
        return task_responses(session, [task])[0]


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    member: Member,
    status: TaskStatus | None = None,
    owner: Annotated[str | None, Query(max_length=255)] = None,
    due: DueBucket | None = None,
    call_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> TaskListResponse:
    with Session(domain_engine()) as session:
        stmt = select(FollowUp).join(CallRecord, FollowUp.call_id == CallRecord.id).where(
            FollowUp.organization_id == member.organization_id,
            CallRecord.deletion_status == "active",
        )
        if status:
            stmt = stmt.where(FollowUp.status == status.value)
        if owner == "unassigned":
            stmt = stmt.where(FollowUp.owner_user_id.is_(None))
        elif owner:
            stmt = stmt.where(FollowUp.owner_user_id == (member.user_id if owner == "me" else owner))
        if due:
            now = datetime.now(UTC)
            _, tomorrow = day_bounds(now)
            stmt = stmt.where({
                # Overdue means an open task past due; completed/cancelled work is never overdue.
                "overdue": (FollowUp.due_at < now) & FollowUp.status.in_(OPEN_TASK_STATUSES),
                "today": (FollowUp.due_at >= now) & (FollowUp.due_at < tomorrow),
                "upcoming": FollowUp.due_at >= tomorrow,
                "unknown": FollowUp.due_at.is_(None),
            }[due])
        if call_id:
            stmt = stmt.where(FollowUp.call_id == call_id)
        if cursor:
            stmt = stmt.where(tuple_(FollowUp.created_at, FollowUp.id) < decode_cursor(cursor))
        tasks = session.scalars(stmt.order_by(FollowUp.created_at.desc(), FollowUp.id.desc()).limit(limit + 1)).all()
        next_cursor = encode_cursor(tasks[limit - 1].created_at, tasks[limit - 1].id) if len(tasks) > limit else None
        return TaskListResponse(items=task_responses(session, tasks[:limit]), next_cursor=next_cursor, request_id=uuid4())


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(task_id: UUID, body: TaskUpdate, member: Member) -> TaskResponse:
    with Session(domain_engine()) as session, session.begin():
        task = session.scalar(select(FollowUp).where(FollowUp.id == task_id).with_for_update())
        if not task or task.organization_id != member.organization_id:
            raise HTTPException(status_code=404, detail="Task not found")
        call = session.get(CallRecord, task.call_id)
        if not call or call.deletion_status != "active":
            raise HTTPException(status_code=404, detail="Task not found")
        if task.version != body.expected_version:
            raise HTTPException(status_code=409, detail="Task version changed; refresh first")
        if body.status and body.status != TaskStatus(task.status):
            if body.status not in ALLOWED_TASK_TRANSITIONS[TaskStatus(task.status)]:
                raise HTTPException(status_code=409, detail="Task status transition is not permitted")
            task.status = body.status.value
        if body.owner_user_id is not None:
            assignee = session.scalar(
                select(Membership).where(
                    Membership.organization_id == member.organization_id,
                    Membership.user_id == body.owner_user_id,
                    Membership.active.is_(True),
                )
            )
            if assignee is None:
                raise HTTPException(status_code=422, detail="Task owner is not an active member")
            task.owner_user_id = body.owner_user_id
        if task.status == TaskStatus.ASSIGNED.value and not task.owner_user_id:
            raise HTTPException(status_code=422, detail="An assigned task needs an active owner")
        if body.due_at is not None:
            task.due_at = body.due_at
        if body.due_text is not None:
            task.due_text = body.due_text
        task.version += 1
        session.add(TaskEvent(
            organization_id=member.organization_id,
            task_id=task.id,
            actor_user_id=member.user_id,
            prior_version=body.expected_version,
            new_version=task.version,
            change={
                "status": task.status,
                "owner_user_id": task.owner_user_id,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "due_text": task.due_text,
            },
            reason=body.reason,
        ))
        return task_responses(session, [task])[0]
