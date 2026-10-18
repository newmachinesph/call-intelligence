"""Daily AI spend guard and per-user question rate limit (NFR01)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from functools import lru_cache

from fastapi import HTTPException
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from call_intelligence_api.call_models import (
    AnalysisRun,
    CallRecord,
    ChatMessage,
    IndexManifest,
    TranscriptVersion,
)


class LimitSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    ai_daily_token_budget: int = 5_000_000
    ai_daily_audio_minutes: int = 300
    chat_questions_per_10_minutes: int = 20


@lru_cache
def limit_settings() -> LimitSettings:
    return LimitSettings()


def total_tokens(usage: object) -> int:
    """Sum every provider-reported total_tokens inside a stored usage blob."""
    if isinstance(usage, dict):
        own = usage.get("total_tokens")
        return (own if isinstance(own, int) else 0) + sum(
            total_tokens(value) for key, value in usage.items() if key != "total_tokens")
    if isinstance(usage, list):
        return sum(total_tokens(item) for item in usage)
    return 0


def usage_today(session: Session) -> tuple[int, float]:
    """(text+embedding tokens, transcribed audio minutes) recorded since 00:00 UTC, across organizations."""
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    blobs = [
        *session.scalars(select(AnalysisRun.usage).where(AnalysisRun.created_at >= since)),
        *session.scalars(select(ChatMessage.usage).where(ChatMessage.created_at >= since, ChatMessage.role == "assistant")),
        *session.scalars(select(IndexManifest.usage).where(IndexManifest.created_at >= since)),
    ]
    seconds = session.scalar(
        select(func.coalesce(func.sum(CallRecord.duration_seconds), 0.0))
        .join(TranscriptVersion, TranscriptVersion.call_id == CallRecord.id)
        .where(TranscriptVersion.created_at >= since, TranscriptVersion.origin == "automatic")
    ) or 0.0
    return sum(total_tokens(blob) for blob in blobs), float(seconds) / 60


def budget_exhausted(session: Session) -> str | None:
    tokens, minutes = usage_today(session)
    config = limit_settings()
    if tokens >= config.ai_daily_token_budget:
        return "Daily AI token budget reached"
    if minutes >= config.ai_daily_audio_minutes:
        return "Daily transcription budget reached"
    return None


def enforce_ai_budget(session: Session) -> None:
    reason = budget_exhausted(session)
    if reason:
        raise HTTPException(status_code=429, detail=f"{reason}; try again after 00:00 UTC or raise the configured budget")


# ponytail: per-process sliding window; one API instance today. Move to Upstash Redis when scaling out.
_questions: dict[str, deque[float]] = defaultdict(deque)
_questions_lock = threading.Lock()


def enforce_question_rate(user_id: str, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    window = _questions[user_id]
    with _questions_lock:
        while window and now - window[0] > 600:
            window.popleft()
        if len(window) >= limit_settings().chat_questions_per_10_minutes:
            raise HTTPException(status_code=429, detail="Too many questions; wait a few minutes and try again")
        window.append(now)
