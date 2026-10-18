import pytest
from fastapi import HTTPException

from call_intelligence_api import limits


def test_total_tokens_sums_nested_provider_usage_only() -> None:
    usage = {"intake": {"total_tokens": 7208, "input_tokens": 5995}, "evaluation": {"total_tokens": 18299},
             "coverage": {"indexed_calls": 4}, "embedding": {"total_tokens": 6}}
    assert limits.total_tokens(usage) == 7208 + 18299 + 6
    assert limits.total_tokens(None) == 0


def test_question_rate_limit_uses_a_sliding_ten_minute_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "limit_settings", lambda: limits.LimitSettings(chat_questions_per_10_minutes=2))
    limits._questions.clear()
    limits.enforce_question_rate("user-a", now=0)
    limits.enforce_question_rate("user-a", now=10)
    with pytest.raises(HTTPException) as blocked:
        limits.enforce_question_rate("user-a", now=20)
    assert blocked.value.status_code == 429
    limits.enforce_question_rate("user-b", now=20)  # separate users have separate windows
    limits.enforce_question_rate("user-a", now=611)  # oldest question left the window


def test_budget_blocks_on_tokens_or_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "limit_settings", lambda: limits.LimitSettings(ai_daily_token_budget=100, ai_daily_audio_minutes=10))
    monkeypatch.setattr(limits, "usage_today", lambda session: (99, 9.9))
    assert limits.budget_exhausted(None) is None
    monkeypatch.setattr(limits, "usage_today", lambda session: (100, 0))
    assert "token" in limits.budget_exhausted(None)
    monkeypatch.setattr(limits, "usage_today", lambda session: (0, 10))
    assert "transcription" in limits.budget_exhausted(None)
