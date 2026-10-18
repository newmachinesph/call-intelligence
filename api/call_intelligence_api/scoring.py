"""Validate model evidence and calculate source-defined points in Python."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from call_intelligence_api.call_models import TranscriptSegment
from call_intelligence_api.evidence import resolve_segment

RUBRIC_FILE = Path(__file__).resolve().parents[1] / "resources" / "rubrics" / "example-v1.json"
VALID_STATES = {"met", "missed", "auto_credit", "not_applicable", "not_observable"}


def load_rubric(primary_type: str) -> dict[str, Any] | None:
    key = "office" if primary_type == "customer_service" else primary_type
    with RUBRIC_FILE.open(encoding="utf-8") as handle:
        source = json.load(handle)
    return next((r for r in source["rubrics"] if r["id"] == key), None)


def validate_rubric(rubric: dict[str, Any]) -> None:
    criteria = rubric["criteria"]
    ids = [c["id"] for c in criteria]
    if len(ids) != len(set(ids)) or len(ids) != rubric["maximum_points"]:
        raise ValueError("Rubric criterion count or IDs are inconsistent")
    if any(c["maximum_points"] != 1 for c in criteria):
        raise ValueError("Unsupported criterion weight")
    for rule in rubric.get("automatic_credit_rules", []):
        if not set(rule["criterion_ids"]).issubset(ids):
            raise ValueError("Automatic credit references an unknown criterion")


def score_evaluation(
    *,
    rubric: dict[str, Any],
    evaluation: dict[str, Any],
    intake: dict[str, Any],
    segments: list[TranscriptSegment],
    call_id: UUID,
    transcript_version: int,
    complete_coverage: bool,
) -> dict[str, Any]:
    validate_rubric(rubric)
    expected = {criterion["id"] for criterion in rubric["criteria"]}
    proposed = evaluation.get("criteria", [])
    given = [item.get("criterion_id") for item in proposed]
    if len(given) != len(expected) or set(given) != expected or len(set(given)) != len(given):
        raise ValueError("Evaluation must contain each rubric criterion exactly once")
    candidates = {item["criterion_id"]: item for item in proposed}
    no_objections = (
        rubric["id"] == "sales"
        and complete_coverage
        and intake.get("objections_status") == "absent"
        and not any(flag in {"truncated", "inaudible_critical", "non_conversation"} for flag in intake.get("quality_flags", []))
    )
    auto_credit_ids = {
        criterion_id
        for rule in rubric.get("automatic_credit_rules", [])
        if rule["id"] == "sales.no_objections" and no_objections
        for criterion_id in rule["criterion_ids"]
    }
    decisions: list[dict[str, Any]] = []
    for criterion in rubric["criteria"]:
        item = candidates[criterion["id"]]
        state = item.get("state")
        if state not in VALID_STATES:
            raise ValueError("Evaluation contains an invalid state")
        refs = []
        bad_ref = False
        for raw_id in item.get("evidence_segment_ids", []):
            segment = resolve_segment(str(raw_id), segments)
            if not segment:
                bad_ref = True
                continue
            refs.append(
                {
                    "source_kind": "transcript",
                    "call_id": str(call_id),
                    "transcript_version": transcript_version,
                    "segment_ids": [str(segment.id)],
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds,
                    "quote": segment.text,
                }
            )
        credit_reason = None
        if criterion["id"] in auto_credit_ids:
            state = "auto_credit"
            refs = []
            credit_reason = "Complete usable conversation established no customer objections; source manual awards four objection points."
        elif state == "auto_credit":
            state = "not_observable"
        if bad_ref or (state == "met" and not refs) or (state == "missed" and not complete_coverage):
            state = "not_observable"
            refs = []
        points = 1 if state in {"met", "auto_credit"} else 0 if state == "missed" else None
        decisions.append(
            {
                "criterion_id": criterion["id"],
                "state": state,
                "points": points,
                "rationale": str(item.get("rationale") or "Evidence is insufficient for this criterion")[:2000],
                "evidence_refs": refs,
                "credit_reason": credit_reason,
            }
        )
    unresolved = sum(item["points"] is None for item in decisions)
    confirmed = sum(item["points"] or 0 for item in decisions)
    grade = None
    if unresolved == 0:
        grade = next(
            (
                threshold["grade"]
                for threshold in rubric["thresholds"]
                if threshold["minimum"] <= confirmed <= threshold["maximum"]
            ),
            None,
        )
    return {
        "criteria": decisions,
        "confirmed_points": confirmed,
        "nominal_maximum": rubric["maximum_points"],
        "unresolved_count": unresolved,
        "grade": grade,
        "status": "final" if grade else "provisional",
    }
