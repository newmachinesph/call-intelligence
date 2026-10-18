"""Synthetic boundary cases: source points, citations, status and telemetry."""

import json
from uuid import uuid4

import pytest

from call_intelligence_api.call_models import TranscriptSegment
from call_intelligence_api.crm_rules import (
    OpportunityStage,
    can_transition,
    logical_followup_key,
    normalize_identifier,
)
from call_intelligence_api.scoring import load_rubric, score_evaluation
from call_intelligence_api.telemetry import scrub_event


def score_sales(*, missed: str | None = None, unresolved: str | None = None, duplicate: bool = False):
    rubric = load_rubric("sales")
    segment = TranscriptSegment(id=uuid4(), ordinal=0, start_seconds=1, end_seconds=3,
                                speaker_label="A", speaker_role="employee", role_uncertain=False,
                                text="Synthetic customer call evidence")
    criteria = []
    for criterion in rubric["criteria"]:
        criterion_id = criterion["id"]
        state = "missed" if criterion_id == missed else "not_observable" if criterion_id == unresolved else "met"
        criteria.append({"criterion_id": criterion_id, "state": state, "rationale": "Synthetic source assessment",
                         "evidence_segment_ids": [str(segment.id)] if state == "met" else []})
    if duplicate:
        criteria[-1]["criterion_id"] = criteria[0]["criterion_id"]
    return score_evaluation(rubric=rubric, evaluation={"criteria": criteria},
                            intake={"objections_status": "absent", "quality_flags": []},
                            segments=[segment], call_id=uuid4(), transcript_version=1,
                            complete_coverage=True)


def test_sales_no_objection_credit_and_grade_boundary() -> None:
    full = score_sales()
    assert full["confirmed_points"] == 12 and full["grade"] == "gold"
    credits = [item for item in full["criteria"] if item["state"] == "auto_credit"]
    assert len(credits) == 3 and all(item["points"] == 1 for item in credits)
    non_credit = next(item["criterion_id"] for item in full["criteria"] if item["state"] != "auto_credit")
    lower = score_sales(missed=non_credit)
    assert lower["confirmed_points"] == 11 and lower["grade"] == "green"


def test_unresolved_and_duplicate_criterion_never_receive_a_final_grade() -> None:
    full = score_sales()
    non_credit = next(item["criterion_id"] for item in full["criteria"] if item["state"] != "auto_credit")
    provisional = score_sales(unresolved=non_credit)
    assert provisional["unresolved_count"] == 1 and provisional["grade"] is None
    with pytest.raises(ValueError, match="exactly once"):
        score_sales(duplicate=True)


def test_terminal_crm_reopen_and_logical_commitment_rules() -> None:
    assert not can_transition(OpportunityStage.WON, OpportunityStage.CONTACTED)
    assert can_transition(OpportunityStage.CONTACTED, OpportunityStage.FOLLOW_UP)
    assert normalize_identifier("email", "  Person@Example.Com ") == "person@example.com"
    assert logical_followup_key(["segment-b", "segment-a"], " Call  tomorrow ") == logical_followup_key(
        ["segment-a", "segment-b"], "call tomorrow"
    )


def test_sentry_envelope_drops_customer_content_and_urls() -> None:
    synthetic = {"event_id": "a" * 32, "timestamp": 123.0, "request": {"url": "https://private.invalid/?secret=synthetic"},
                 "exception": {"values": [{"value": "synthetic customer transcript"}]},
                 "breadcrumbs": [{"message": "synthetic customer transcript"}],
                 "tags": {"stage": "analysis", "customer_name": "synthetic customer transcript"}}
    outgoing = scrub_event(synthetic)
    serialized = json.dumps(outgoing)
    assert outgoing["tags"] == {"stage": "analysis"}
    assert "synthetic customer transcript" not in serialized and "secret=" not in serialized
