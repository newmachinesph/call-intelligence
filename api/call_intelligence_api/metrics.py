"""Shared eligible-call predicate and whitelisted aggregates; every metric reader uses these."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from call_intelligence_api.call_models import AnalysisRun, CallRecord, CriterionDecision

PrimaryType = Literal["sales", "retention", "customer_service", "other"]
GroupBy = Literal["primary_type", "outcome", "rep", "grade", "missed_criterion"]


class CallFilters(BaseModel):
    primary_type: PrimaryType | None = None
    outcome: str | None = Field(default=None, pattern=r"^[a-z_]{2,40}$")
    rep_id: UUID | Literal["unknown"] | None = None
    call_at_from: datetime | None = None
    call_at_to: datetime | None = None

    @model_validator(mode="after")
    def require_offsets(self) -> CallFilters:
        for value in (self.call_at_from, self.call_at_to):
            if value is not None and value.tzinfo is None:
                raise ValueError("Date filters need a UTC offset")
        return self


def apply_call_filters(stmt: Select, filters: CallFilters | None) -> Select:
    """Filter on effective call columns; unknown dates never match a date range."""
    if filters is None:
        return stmt
    if filters.primary_type:
        stmt = stmt.where(CallRecord.primary_type == filters.primary_type)
    if filters.outcome:
        stmt = stmt.where(CallRecord.outcome == filters.outcome)
    if filters.rep_id == "unknown":
        stmt = stmt.where(CallRecord.rep_id.is_(None))
    elif filters.rep_id is not None:
        stmt = stmt.where(CallRecord.rep_id == filters.rep_id)
    if filters.call_at_from:
        stmt = stmt.where(CallRecord.call_at >= filters.call_at_from)
    if filters.call_at_to:
        stmt = stmt.where(CallRecord.call_at <= filters.call_at_to)
    return stmt


def eligible_calls(organization_id: UUID, filters: CallFilters | None = None) -> Select:
    """Active calls whose active analysis belongs to the current requested generation."""
    stmt = (
        select(CallRecord)
        .join(AnalysisRun, AnalysisRun.id == CallRecord.active_analysis_id)
        .where(
            CallRecord.organization_id == organization_id,
            CallRecord.deletion_status == "active",
            AnalysisRun.generation == CallRecord.requested_generation,
        )
    )
    return apply_call_filters(stmt, filters)


def aggregate(session: Session, organization_id: UUID, group_by: GroupBy,
              filters: CallFilters | None = None) -> list[tuple[str, int]]:
    """Exact counts over eligible calls; missed_criterion counts missed decisions, not calls."""
    eligible = eligible_calls(organization_id, filters).subquery()
    if group_by == "missed_criterion":
        stmt = (
            select(CriterionDecision.criterion_id, func.count())
            .join(eligible, CriterionDecision.analysis_id == eligible.c.active_analysis_id)
            .where(CriterionDecision.state == "missed")
            .group_by(CriterionDecision.criterion_id)
        )
    elif group_by == "grade":
        stmt = (
            select(AnalysisRun.grade, func.count())
            .join(eligible, AnalysisRun.id == eligible.c.active_analysis_id)
            .group_by(AnalysisRun.grade)
        )
    else:
        column = {"primary_type": eligible.c.primary_type, "outcome": eligible.c.outcome,
                  "rep": eligible.c.rep_id}[group_by]
        stmt = select(column, func.count()).select_from(eligible).group_by(column)
    rows = session.execute(stmt).all()
    missing = "not_graded" if group_by == "grade" else "unknown"
    return sorted(((str(label) if label is not None else missing, count) for label, count in rows),
                  key=lambda row: (-row[1], row[0]))


# --- Rate definitions (specs/01-product.md "Metrics definitions") -------------------------------
# One place for the outcome labels behind the reviewed rates. Only the reviewer's
# call_record.outcome_override counts ("reviewed"); model outcomes never enter a reviewed rate.
# A label is in the success class when it equals or starts with a success label
# (e.g. "booked_initial_service"); failure labels match exactly. Each rate covers one call type.
REVIEWED_RATE_OUTCOME_CLASSES: dict[str, dict[str, str | tuple[str, ...]]] = {
    "reviewed_conversion": {"label": "Reviewed conversion", "call_type": "sales",
                            "success": ("booked", "agreed"), "failure": ("declined",)},
    "reviewed_save_rate": {"label": "Reviewed save rate", "call_type": "retention",
                           "success": ("retained",), "failure": ("cancellation_reported",)},
}


def scored_clause():
    """A run is scored when it has a nominal maximum and no unresolved criteria."""
    return and_(AnalysisRun.nominal_maximum.is_not(None), AnalysisRun.unresolved_count == 0)


def is_scored(nominal_maximum: int | None, unresolved_count: int | None) -> bool:
    return nominal_maximum is not None and unresolved_count == 0


def rate(numerator: int, denominator: int) -> float | None:
    """Zero denominator means no rate, never 0%."""
    return numerator / denominator if denominator else None


def outcome_class(metric_key: str, outcome: str | None) -> Literal["success", "failure"] | None:
    spec = REVIEWED_RATE_OUTCOME_CLASSES[metric_key]
    if not outcome:
        return None
    if any(outcome.startswith(label) for label in spec["success"]):
        return "success"
    return "failure" if outcome in spec["failure"] else None


def reviewed_rate_definition(metric_key: str) -> str:
    spec = REVIEWED_RATE_OUTCOME_CLASSES[metric_key]
    def words(labels: tuple[str, ...]) -> str:
        return " or ".join(label.replace("_", " ") for label in labels)

    return (f"Of the {spec['call_type']} calls where a reviewer confirmed the outcome, the share that ended "
            f"{words(spec['success'])} rather than {words(spec['failure'])}. Calls nobody has reviewed yet, or "
            "with any other outcome, are left out and listed below. An agreement on the call does not prove "
            "payment or a booked appointment.")


def reviewed_rate(metric_key: str, override_counts: Iterable[tuple[str | None, int]]) -> dict:
    """override_counts: (outcome_override, calls) over eligible calls of the metric's call type."""
    success = failure = unreviewed = other = 0
    for override, count in override_counts:
        if override is None:
            unreviewed += count
            continue
        kind = outcome_class(metric_key, override)
        if kind == "success":
            success += count
        elif kind == "failure":
            failure += count
        else:
            other += count
    denominator = success + failure
    return {
        "key": metric_key, "label": REVIEWED_RATE_OUTCOME_CLASSES[metric_key]["label"],
        "numerator": success, "denominator": denominator, "value": rate(success, denominator),
        "exclusions": [{"label": "Not reviewed (pending)", "count": unreviewed},
                       {"label": "Reviewed with other or unknown outcome", "count": other}],
        "definition": reviewed_rate_definition(metric_key),
    }


def criterion_rates(rows: Iterable[tuple[str | None, str, str, int]]) -> list[dict]:
    """rows: (rubric_id, criterion_id, state, count). Denominator = met + missed decisions
    (applicable and observable) for that rubric criterion; only criteria with a miss are returned."""
    totals: dict[tuple[str | None, str], dict[str, int]] = {}
    for rubric_id, criterion_id, state, count in rows:
        row = totals.setdefault((rubric_id, criterion_id), {"missed": 0, "applicable": 0})
        if state in ("met", "missed"):
            row["applicable"] += count
        if state == "missed":
            row["missed"] += count
    items = [{"rubric_id": rubric_id, "criterion_id": criterion_id, **row}
             for (rubric_id, criterion_id), row in totals.items() if row["missed"]]
    return sorted(items, key=lambda item: (-item["missed"], str(item["rubric_id"]), item["criterion_id"]))


def rubric_rollup(runs: Iterable[tuple[str | None, str | None, int | None, int | None, int | None]],
                  decisions: Iterable[tuple[str | None, str, str, int]]) -> list[dict]:
    """runs: (rubric_id, grade, confirmed_points, nominal_maximum, unresolved_count). One row per
    rubric; points and grades are never combined across rubrics."""
    groups: dict[str | None, dict] = {}
    for rubric_id, grade, points, maximum, unresolved in runs:
        row = groups.setdefault(rubric_id, {"rubric_id": rubric_id, "calls": 0, "scored_calls": 0,
                                            "points_earned": 0, "points_possible": 0,
                                            "grades": Counter(), "missed_criteria": []})
        row["calls"] += 1
        row["grades"][grade or "not_graded"] += 1
        if is_scored(maximum, unresolved):
            row["scored_calls"] += 1
            row["points_earned"] += points or 0
            row["points_possible"] += maximum or 0
    for item in criterion_rates(decisions):
        if item["rubric_id"] in groups:
            groups[item["rubric_id"]]["missed_criteria"].append(
                {"label": item["criterion_id"], "count": item["missed"], "denominator": item["applicable"]})
    result = []
    for row in sorted(groups.values(), key=lambda row: str(row["rubric_id"])):
        grades = row.pop("grades")
        row["grade_distribution"] = [{"label": label, "count": count} for label, count in sorted(grades.items())]
        result.append(row)
    return result


def coaching_improvements(coaching: object) -> list[dict]:
    """Tolerant read of run.coaching["improvements"]; malformed items are skipped."""
    items = coaching.get("improvements") if isinstance(coaching, dict) else None
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("observation"), str):
            continue
        result.append({
            "criterion_ids": [str(value) for value in item.get("criterion_ids") or [] if isinstance(value, str)],
            "observation": item["observation"],
            "evidence_segment_ids": [str(value) for value in item.get("evidence_segment_ids") or []
                                     if isinstance(value, str)],
        })
    return result
