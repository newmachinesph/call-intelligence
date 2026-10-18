"""User-owned, read-only call chat with canonical transcript citations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from call_intelligence_api import ai
from call_intelligence_api.auth import (
    AuthenticatedMember,
    domain_engine,
    get_current_member,
)
from call_intelligence_api.call_models import (
    AnalysisRun,
    CallIndexChunk,
    CallRecord,
    ChatMessage,
    ChatThread,
    IndexManifest,
    TranscriptSegment,
    TranscriptVersion,
)
from call_intelligence_api.evidence import resolve_segment
from call_intelligence_api.indexing import vector_client
from call_intelligence_api.limits import enforce_ai_budget, enforce_question_rate

router = APIRouter(prefix="/api/v1", tags=["chat"])
Member = Annotated[AuthenticatedMember, Depends(get_current_member)]


class CreateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ThreadResponse(BaseModel):
    id: UUID
    call_id: UUID
    title: str | None
    request_id: UUID


class ThreadListResponse(BaseModel):
    items: list[ThreadResponse]
    next_cursor: str | None
    request_id: UUID


class PostMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    client_message_id: str = Field(min_length=1, max_length=100)


class MessageResponse(BaseModel):
    id: UUID
    role: str
    text: str
    evidence_status: str | None
    citations: list[dict]
    source_versions: list[dict]
    stale: bool


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    next_cursor: str | None
    request_id: UUID


class ChatAnswerResponse(BaseModel):
    message_id: UUID
    text: str
    evidence_status: str
    citations: list[dict]
    resolved_scope: dict
    coverage: dict
    source_versions: list[dict]
    stale: bool
    request_id: UUID


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


def _thread(session: Session, member: AuthenticatedMember, call_id: UUID, thread_id: UUID) -> ChatThread:
    thread = session.scalar(
        select(ChatThread).where(
            ChatThread.id == thread_id,
            ChatThread.organization_id == member.organization_id,
            ChatThread.author_user_id == member.user_id,
            ChatThread.call_id == call_id,
            ChatThread.scope == "call",
        )
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


def _message(message: ChatMessage, active_version: int | None) -> MessageResponse:
    source = message.source_versions or []
    stale = message.role == "assistant" and any(
        item.get("transcript_version") != active_version for item in source
    )
    return MessageResponse(
        id=message.id,
        role=message.role,
        text=message.text,
        evidence_status=message.evidence_status,
        citations=message.citations or [],
        source_versions=source,
        stale=stale,
    )


@router.post("/calls/{call_id}/chat/threads", response_model=ThreadResponse, status_code=201)
def create_thread(call_id: UUID, body: CreateThreadRequest, member: Member) -> ThreadResponse:
    with Session(domain_engine()) as session, session.begin():
        call = _call(session, member, call_id)
        thread = ChatThread(
            organization_id=call.organization_id,
            author_user_id=member.user_id,
            scope="call",
            call_id=call.id,
            title=body.title,
        )
        session.add(thread)
        session.flush()
        return ThreadResponse(id=thread.id, call_id=call.id, title=thread.title, request_id=uuid4())


@router.get("/calls/{call_id}/chat/threads", response_model=ThreadListResponse)
def list_threads(call_id: UUID, member: Member, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> ThreadListResponse:
    with Session(domain_engine()) as session:
        _call(session, member, call_id)
        threads = session.scalars(
            select(ChatThread)
            .where(
                ChatThread.call_id == call_id,
                ChatThread.organization_id == member.organization_id,
                ChatThread.author_user_id == member.user_id,
                ChatThread.scope == "call",
            )
            .order_by(ChatThread.created_at.desc(), ChatThread.id.desc())
            .limit(limit)
        ).all()
        return ThreadListResponse(
            items=[ThreadResponse(id=t.id, call_id=call_id, title=t.title, request_id=uuid4()) for t in threads],
            next_cursor=None,
            request_id=uuid4(),
        )


@router.get("/calls/{call_id}/chat/threads/{thread_id}/messages", response_model=MessageListResponse)
def list_messages(call_id: UUID, thread_id: UUID, member: Member) -> MessageListResponse:
    with Session(domain_engine()) as session:
        call = _call(session, member, call_id)
        _thread(session, member, call_id, thread_id)
        transcript = session.get(TranscriptVersion, call.active_transcript_id) if call.active_transcript_id else None
        messages = session.scalars(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread_id, ChatMessage.organization_id == member.organization_id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        ).all()
        return MessageListResponse(
            items=[_message(message, transcript.version if transcript else None) for message in messages],
            next_cursor=None,
            request_id=uuid4(),
        )


@router.post("/calls/{call_id}/chat/threads/{thread_id}/messages", response_model=ChatAnswerResponse)
def ask_call(
    call_id: UUID,
    thread_id: UUID,
    body: PostMessageRequest,
    member: Member,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> ChatAnswerResponse:
    if not idempotency_key or len(idempotency_key) > 120:
        raise HTTPException(status_code=422, detail="Idempotency-Key is required")
    with Session(domain_engine()) as session:
        call = _call(session, member, call_id)
        _thread(session, member, call_id, thread_id)
        prior_user = session.scalar(
            select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.client_message_id == body.client_message_id)
        )
        prior_answer = session.scalar(
            select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.client_message_id == f"answer:{body.client_message_id}")
        )
        if prior_user:
            if prior_user.text != body.text:
                raise HTTPException(status_code=409, detail="Message ID was used for different text")
            if prior_answer:
                active = session.get(TranscriptVersion, call.active_transcript_id) if call.active_transcript_id else None
                old = _message(prior_answer, active.version if active else None)
                return ChatAnswerResponse(
                    message_id=old.id,
                    text=old.text,
                    evidence_status=old.evidence_status or "insufficient",
                    citations=old.citations,
                    resolved_scope={"kind": "call", "call_id": str(call_id)},
                    coverage={"eligible_calls": 1, "indexed_calls": 1, "inspected_calls": 1},
                    source_versions=old.source_versions,
                    stale=old.stale,
                    request_id=uuid4(),
                )
            raise HTTPException(status_code=409, detail="The prior answer is still pending")
        enforce_question_rate(member.user_id)
        enforce_ai_budget(session)
        transcript = session.get(TranscriptVersion, call.active_transcript_id) if call.active_transcript_id else None
        manifest = session.get(IndexManifest, call.active_manifest_id) if call.active_manifest_id else None
        if not transcript or not manifest or manifest.transcript_id != transcript.id or manifest.state != "ready":
            raise HTTPException(status_code=409, detail="Current transcript index is not ready")
        segments = session.scalars(
            select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id).order_by(TranscriptSegment.ordinal)
        ).all()
        analysis = session.get(AnalysisRun, call.active_analysis_id) if call.active_analysis_id else None
        source_version = transcript.version
        manifest_id = manifest.id
        generation = call.requested_generation
        organization_id = call.organization_id
        scorecard = (
            {
                "rubric_id": analysis.rubric_id,
                "confirmed_points": analysis.confirmed_points,
                "nominal_maximum": analysis.nominal_maximum,
                "unresolved_count": analysis.unresolved_count,
                "grade": analysis.grade,
            }
            if analysis and analysis.transcript_id == transcript.id and analysis.generation == generation
            else None
        )

    question_embedding = ai.client().embeddings.create(
        input=body.text,
        model=ai.settings().openai_embedding_model,
        dimensions=ai.settings().openai_embedding_dimensions,
        timeout=30,
    )
    hits = vector_client().query(
        vector=question_embedding.data[0].embedding,
        top_k=8,
        namespace=str(organization_id),
        filter=f"call_id = '{call_id}' AND manifest_id = '{manifest_id}'",
    )
    hit_ids = [hit.id for hit in hits]
    with Session(domain_engine()) as session:
        chunks = session.scalars(
            select(CallIndexChunk).where(
                CallIndexChunk.vector_id.in_(hit_ids),
                CallIndexChunk.manifest_id == manifest_id,
                CallIndexChunk.call_id == call_id,
                CallIndexChunk.organization_id == organization_id,
            )
        ).all()
        by_id = {chunk.vector_id: chunk for chunk in chunks}
        retrieved = [by_id[identifier].text for identifier in hit_ids if identifier in by_id]
    draft, usage = ai.answer_call(body.text, segments, retrieved, scorecard)
    citations = []
    invalid = False
    for candidate in draft.citation_segment_ids:
        segment = resolve_segment(candidate, segments)
        if segment is None:
            invalid = True
            continue
        citations.append(
            {
                "source_kind": "transcript",
                "call_id": str(call_id),
                "transcript_version": source_version,
                "segment_ids": [str(segment.id)],
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "quote": segment.text,
            }
        )
    evidence_status = draft.evidence_status
    answer_text = draft.answer
    if invalid or (evidence_status in {"supported", "contradicted", "conflicting"} and not citations):
        evidence_status = "insufficient"
        answer_text = "I could not verify enough current call evidence to answer that reliably."
        citations = []
    sources = [{"call_id": str(call_id), "transcript_version": source_version, "manifest_id": str(manifest_id)}]
    with Session(domain_engine()) as session, session.begin():
        current = _call(session, member, call_id)
        _thread(session, member, call_id, thread_id)
        if (
            current.requested_generation != generation
            or current.active_transcript_id != transcript.id
            or current.active_manifest_id != manifest_id
        ):
            raise HTTPException(status_code=409, detail="Call source changed; ask again after refreshing")
        # Explicit, strictly ordered times: both rows share one transaction, so now() would tie.
        asked_at = datetime.now(UTC)
        user_message = ChatMessage(
            organization_id=organization_id,
            thread_id=thread_id,
            role="user",
            client_message_id=body.client_message_id,
            text=body.text,
            created_at=asked_at,
        )
        answer = ChatMessage(
            organization_id=organization_id,
            thread_id=thread_id,
            role="assistant",
            created_at=asked_at + timedelta(microseconds=1),
            client_message_id=f"answer:{body.client_message_id}",
            text=answer_text,
            evidence_status=evidence_status,
            citations=citations,
            source_versions=sources,
            usage={"answer": usage, "embedding": question_embedding.usage.model_dump(mode="json")},
        )
        session.add_all([user_message, answer])
        session.flush()
        return ChatAnswerResponse(
            message_id=answer.id,
            text=answer_text,
            evidence_status=evidence_status,
            citations=citations,
            resolved_scope={"kind": "call", "call_id": str(call_id)},
            coverage={"eligible_calls": 1, "indexed_calls": 1, "inspected_calls": 1},
            source_versions=sources,
            stale=False,
            request_id=uuid4(),
        )
