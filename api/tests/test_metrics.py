"""Synthetic metric boundary cases: reviewed rates, criterion denominators, rubric separation."""

from uuid import uuid4

from fastapi.testclient import TestClient

from call_intelligence_api.auth import AuthenticatedMember, get_current_member
from call_intelligence_api.main import app
from call_intelligence_api.metrics import (
    coaching_improvements,
    criterion_rates,
    outcome_class,
    rate,
    reviewed_rate,
    rubric_rollup,
)
from call_intelligence_api.models import MemberRole


def test_zero_denominator_is_no_rate_not_zero() -> None:
    assert rate(0, 0) is None
    assert rate(0, 4) == 0.0
    assert rate(1, 4) == 0.25


def test_outcome_classes_use_prefix_for_success_and_exact_failure() -> None:
    assert outcome_class("reviewed_conversion", "booked") == "success"
    assert outcome_class("reviewed_conversion", "agreed_to_quote") == "success"
    assert outcome_class("reviewed_conversion", "declined") == "failure"
    assert outcome_class("reviewed_conversion", "declined_maybe_later") is None
    assert outcome_class("reviewed_conversion", "retained") is None
    assert outcome_class("reviewed_save_rate", "cancellation_reported") == "failure"
    assert outcome_class("reviewed_save_rate", None) is None


def test_reviewed_conversion_ignores_unreviewed_and_shows_exclusions() -> None:
    # (outcome_override, calls): None = model outcome only, never counted as reviewed.
    metric = reviewed_rate("reviewed_conversion", [
        ("booked", 2), ("agreed", 1), ("declined", 1), (None, 5), ("unknown", 2), ("follow_up", 1),
    ])
    assert (metric["numerator"], metric["denominator"]) == (3, 4)
    assert metric["value"] == 0.75
    assert {item["label"]: item["count"] for item in metric["exclusions"]} == {
        "Not reviewed (pending)": 5, "Reviewed with other or unknown outcome": 3,
    }
    assert "reviewer" in metric["definition"] and "sales" in metric["definition"]


def test_reviewed_rate_without_resolved_reviews_has_no_value() -> None:
    metric = reviewed_rate("reviewed_save_rate", [(None, 3), ("open", 1)])
    assert (metric["numerator"], metric["denominator"], metric["value"]) == (0, 0, None)
    assert sum(item["count"] for item in metric["exclusions"]) == 4


def test_criterion_denominator_is_met_plus_missed_only() -> None:
    rows = criterion_rates([
        ("sales_v1", "greeting", "met", 3), ("sales_v1", "greeting", "missed", 1),
        ("sales_v1", "greeting", "not_observable", 4), ("sales_v1", "greeting", "not_applicable", 2),
        ("sales_v1", "greeting", "auto_credit", 1), ("sales_v1", "close", "met", 5),
    ])
    assert rows == [{"rubric_id": "sales_v1", "criterion_id": "greeting", "missed": 1, "applicable": 4}]


def test_same_criterion_id_in_two_rubrics_stays_separate() -> None:
    rows = criterion_rates([("sales", "greeting", "missed", 1), ("retention", "greeting", "missed", 2),
                            ("retention", "greeting", "met", 2)])
    assert [(row["rubric_id"], row["missed"], row["applicable"]) for row in rows] == [
        ("retention", 2, 4), ("sales", 1, 1)]


def test_rubric_rollup_never_combines_rubrics_and_skips_unscored_points() -> None:
    runs = [
        ("sales", "gold", 17, 17, 0),
        ("sales", None, 10, 17, 2),  # provisional: counted as a call, not scored
        ("retention", "green", 8, 10, 0),
        (None, None, None, None, None),  # ungraded call type
    ]
    decisions = [("sales", "close", "missed", 1), ("sales", "close", "met", 1),
                 ("retention", "save_offer", "missed", 1)]
    by_rubric = {row["rubric_id"]: row for row in rubric_rollup(runs, decisions)}
    assert set(by_rubric) == {"sales", "retention", None}
    sales = by_rubric["sales"]
    assert (sales["calls"], sales["scored_calls"], sales["points_earned"], sales["points_possible"]) == (2, 1, 17, 17)
    assert sales["grade_distribution"] == [{"label": "gold", "count": 1}, {"label": "not_graded", "count": 1}]
    assert sales["missed_criteria"] == [{"label": "close", "count": 1, "denominator": 2}]
    assert by_rubric["retention"]["points_possible"] == 10
    assert by_rubric["retention"]["missed_criteria"] == [{"label": "save_offer", "count": 1, "denominator": 1}]
    assert by_rubric[None]["scored_calls"] == 0 and by_rubric[None]["missed_criteria"] == []


def test_coaching_improvements_tolerates_missing_and_malformed_items() -> None:
    assert coaching_improvements(None) == []
    assert coaching_improvements({"improvements": "bad"}) == []
    items = coaching_improvements({"improvements": [
        {"criterion_ids": ["close"], "observation": "Synthetic: ask for the booking.", "evidence_segment_ids": ["S0003"]},
        {"criterion_ids": ["close"]},
        "not an item",
    ]})
    assert items == [{"criterion_ids": ["close"], "observation": "Synthetic: ask for the booking.",
                      "evidence_segment_ids": ["S0003"]}]


def test_overview_date_filter_without_offset_is_rejected_before_any_query() -> None:
    app.dependency_overrides[get_current_member] = lambda: AuthenticatedMember(
        user_id="synthetic-user", organization_id=uuid4(), role=MemberRole.OWNER)
    try:
        response = TestClient(app).get("/api/v1/analytics/overview", params={"call_at_from": "2026-01-01T00:00:00"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
