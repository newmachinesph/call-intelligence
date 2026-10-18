"""Synthetic Insights scope, retrieval-filter, intent and citation checks (no OpenAI, Upstash or DB)."""

from itertools import batched
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from call_intelligence_api.call_models import ChatThread, TranscriptSegment
from call_intelligence_api.insight_chat import (
    CreateInsightThread,
    InsightCitationDraft,
    best_hits,
    classify_aggregate,
    scope_statement,
    thread_scope,
    validate_citations,
    vector_filter,
)
from call_intelligence_api.metrics import CallFilters


def test_legacy_and_new_thread_rows_resolve_scope() -> None:
    ids = [uuid4(), uuid4()]
    legacy = ChatThread(selected_call_ids=[str(item) for item in ids], scope_filters=None)
    assert thread_scope(legacy) == ("selected", ids, None)
    assert thread_scope(ChatThread(selected_call_ids=None, scope_filters=None)) == ("organization", None, None)
    kind, call_ids, filters = thread_scope(ChatThread(selected_call_ids=None, scope_filters={"primary_type": "sales"}))
    assert (kind, call_ids, filters.primary_type) == ("organization", None, "sales")


def test_create_body_enforces_scope_shape() -> None:
    CreateInsightThread(scope="organization", filters={"outcome": "booked"})
    CreateInsightThread(scope="selected", call_ids=[uuid4()])
    one = uuid4()
    for bad in ({"scope": "selected"}, {"scope": "selected", "call_ids": [one, one]},
                {"scope": "selected", "call_ids": [uuid4() for _ in range(51)]},
                {"scope": "organization", "call_ids": [uuid4()]},
                {"scope": "selected", "call_ids": [uuid4()], "filters": {"primary_type": "sales"}}):
        with pytest.raises(ValidationError):
            CreateInsightThread(**bad)


def test_scope_statement_is_org_bound_and_uncapped() -> None:
    org = uuid4()
    sql = scope_statement(org, None, CallFilters(primary_type="sales")).compile(dialect=postgresql.dialect())
    text = str(sql)
    assert "call_record.organization_id" in text and "deletion_status" in text and "primary_type" in text
    assert "LIMIT" not in text and org in sql.params.values()
    selected = scope_statement(org, [uuid4()], None).compile(dialect=postgresql.dialect())
    assert "call_record.id IN" in str(selected)


def test_vector_filter_batches_uuid_pairs_and_rejects_injection() -> None:
    pairs = [(uuid4(), uuid4()) for _ in range(60)]
    filters = [vector_filter(list(batch)) for batch in batched(pairs, 25)]
    assert len(filters) == 3
    assert all(f"'{call}'" in "".join(filters) and f"'{manifest}'" in "".join(filters) for call, manifest in pairs)
    assert filters[0].startswith("call_id IN (") and " AND manifest_id IN (" in filters[0]
    with pytest.raises(TypeError):
        vector_filter([("x') OR call_id != ('", uuid4())])
    with pytest.raises(ValueError):
        vector_filter([])


def test_best_hits_dedupes_and_ranks_across_batches() -> None:
    hits = [("a", 0.2), ("b", 0.9), ("a", 0.95), ("c", 0.5)]
    assert best_hits(hits, limit=2) == ["a", "b"]


@pytest.mark.parametrize(("question", "expected"), [
    ("How many calls were booked last week?", "outcome"),
    ("Which criteria are missed most often?", "missed_criterion"),
    ("What is the grade distribution?", "grade"),
    ("How many calls did each rep take?", "rep"),
    ("How many calls are there?", "primary_type"),
    ("What did customers say about pricing?", None),
])
def test_aggregate_intent(question: str, expected: str | None) -> None:
    assert classify_aggregate(question) == expected


def test_citations_outside_authorized_set_are_rejected() -> None:
    call, other_call = str(uuid4()), str(uuid4())
    segment = TranscriptSegment(id=uuid4(), start_seconds=12.5, end_seconds=14, text="Synthetic quote")
    authorized = {(call, str(segment.id)): (segment, 2, uuid4())}
    labels = {call: "SYN-001"}
    good, invalid = validate_citations([InsightCitationDraft(call_id=call, segment_id=str(segment.id))], authorized, labels)
    assert not invalid and good[0]["call_label"] == "SYN-001" and good[0]["start_seconds"] == 12.5
    kept, invalid = validate_citations([InsightCitationDraft(call_id=other_call, segment_id=str(segment.id)),
                                        InsightCitationDraft(call_id=call, segment_id=str(uuid4()))], authorized, labels)
    assert invalid and kept == []


def test_list_questions_use_retrieval_not_counts() -> None:
    assert classify_aggregate("Which calls need follow-up?") is None


def test_answer_text_drops_source_id_asides() -> None:
    from call_intelligence_api.insight_chat import strip_source_ids

    text = "CALL 006 needs a visit (call_id: 6345, segment_id: d28a and more); CALL 012 too (see pricing)."
    assert strip_source_ids(text) == "CALL 006 needs a visit; CALL 012 too (see pricing)."
