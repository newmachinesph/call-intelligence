"""Synthetic call list/review/deletion behavior without a database: pure planners plus a stub session."""

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Self
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

import call_intelligence_api.call_routes as routes
from call_intelligence_api.auth import AuthenticatedMember, get_current_member
from call_intelligence_api.call_models import (
    AnalysisRun,
    CallRecord,
    OutboxEvent,
    ReviewEvent,
)
from call_intelligence_api.crm_models import AddressCheck
from call_intelligence_api.deletion import store_progress
from call_intelligence_api.main import app
from call_intelligence_api.models import MemberRole

ORG = uuid4()


def make_call(**overrides) -> CallRecord:
    values = {"id": uuid4(), "organization_id": ORG, "original_filename": "synthetic.mp3",
              "uploaded_at": datetime.now(UTC), "processing_status": "complete", "analysis_status": "ready",
              "index_status": "ready", "review_status": "unreviewed", "deletion_status": "active",
              "requested_generation": 1, "active_transcript_id": uuid4(), "rep_id": None, "primary_type": None,
              "outcome": None, "type_override": None, "outcome_override": None}
    values.update(overrides)
    return CallRecord(**values)


def make_run(call: CallRecord, **overrides) -> AnalysisRun:
    values = {"id": uuid4(), "call_id": call.id, "transcript_id": call.active_transcript_id, "generation": 1,
              "review_version": 3, "rubric_id": "sales", "intake": {"primary_type": "sales", "outcome": {"kind": "booked"}},
              "confirmed_points": 10, "unresolved_count": 0, "grade": "silver"}
    values.update(overrides)
    run = AnalysisRun(**values)
    call.active_analysis_id = run.id
    return run


def compiled(query: routes.CallListQuery) -> str:
    stmt = routes.call_list_statement(ORG, query)
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_list_filters_match_filename_or_external_id_and_escape_wildcards() -> None:
    sql = compiled(routes.CallListQuery(q="50%_off"))
    assert "original_filename" in sql and "external_call_id" in sql and " OR " in sql
    assert "50/%%/_off" in sql  # LIKE wildcards in the search term are literal


def test_list_filters_unknown_rep_and_unknown_dates() -> None:
    sql = compiled(routes.CallListQuery(rep_id="unknown", date_known=False, review_status="reviewed"))
    assert "call_record.rep_id IS NULL" in sql and "call_record.call_at IS NULL" in sql
    assert "call_record.review_status = 'reviewed'" in sql


def test_summary_prefers_effective_columns_and_falls_back_to_intake() -> None:
    old = make_call()
    run = make_run(old)
    assert (routes._summary(old, run).primary_type, routes._summary(old, run).outcome) == ("sales", "booked")
    reviewed = make_call(primary_type="retention", outcome="retained", outcome_override="retained")
    summary = routes._summary(reviewed, make_run(reviewed), "Synthetic Rep")
    assert (summary.primary_type, summary.outcome, summary.rep_name) == ("retention", "retained", "Synthetic Rep")
    assert summary.outcome_reviewed and not summary.type_reviewed


def test_review_changes_require_something_and_unique_criteria() -> None:
    with pytest.raises(ValidationError):
        routes.ReviewChanges()
    with pytest.raises(ValidationError):
        routes.ReviewChanges(outcome="Booked!")
    item = {"criterion_id": "sales.greeting", "state": "met", "rationale": "Synthetic"}
    with pytest.raises(ValidationError):
        routes.ReviewChanges(criteria=[item, item])
    assert "rep_id" in routes.ReviewChanges(rep_id=None).model_fields_set  # explicit null = clear rep


def test_plan_confirmation_records_review_without_reanalysis() -> None:
    call = make_call(primary_type="sales", outcome="booked")
    planned, reanalyze = routes.plan_call_review(call, "sales", routes.ReviewChanges(primary_type="sales", outcome="booked"))
    assert [item["field"] for item in planned] == ["primary_type", "outcome"] and not reanalyze


def test_plan_type_change_reanalyzes_and_rejects_criteria_in_same_review() -> None:
    call = make_call(primary_type="sales")
    planned, reanalyze = routes.plan_call_review(call, "sales", routes.ReviewChanges(primary_type="retention"))
    assert reanalyze and planned[0]["prior"] == "sales"
    changes = routes.ReviewChanges(primary_type="retention", criteria=[
        {"criterion_id": "sales.greeting", "state": "met", "rationale": "Synthetic"}])
    with pytest.raises(HTTPException) as error:
        routes.plan_call_review(call, "sales", changes)
    assert error.value.status_code == 422


def test_plan_ignores_absent_rep_and_unchanged_override() -> None:
    call = make_call(rep_id=uuid4(), outcome="declined", outcome_override="declined")
    planned, _ = routes.plan_call_review(call, "sales", routes.ReviewChanges(outcome="declined"))
    assert planned == []
    planned, _ = routes.plan_call_review(call, "sales", routes.ReviewChanges(rep_id=None))
    assert planned[0] == {"field": "rep_id", "prior": str(call.rep_id), "new": None}


class StubSession:
    """Serves one locked call and its analysis; records writes."""

    def __init__(self, call: CallRecord, run: AnalysisRun) -> None:
        self.call, self.run, self.added = call, run, []

    def __call__(self, _engine: object) -> Self:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def begin(self):
        return nullcontext()

    def scalar(self, _stmt: object):
        return self.call

    def get(self, _model: object, _identifier: object):
        return self.run

    def add(self, item: object) -> None:
        self.added.append(item)


@pytest.fixture
def client_with(monkeypatch: pytest.MonkeyPatch):
    member = AuthenticatedMember(user_id="synthetic-reviewer", organization_id=ORG, role=MemberRole.REVIEWER)

    def build(call: CallRecord, run: AnalysisRun) -> tuple[TestClient, StubSession]:
        stub = StubSession(call, run)
        monkeypatch.setattr(routes, "Session", stub)
        monkeypatch.setattr(routes, "domain_engine", lambda: None)
        app.dependency_overrides[get_current_member] = lambda: member
        return TestClient(app), stub

    yield build
    app.dependency_overrides.clear()


def review_body(**changes) -> dict:
    return {"expected_review_version": 3, "expected_generation": 1, "reason": "Synthetic review", "changes": changes}


def test_outcome_review_sets_override_and_bumps_review_version(client_with) -> None:
    call = make_call(primary_type="sales", outcome="booked")
    client, stub = client_with(call, make_run(call))
    response = client.post(f"/api/v1/calls/{call.id}/review", json=review_body(outcome="declined"))
    assert response.status_code == 200
    body = response.json()
    assert (body["outcome"], body["review_version"]) == ("declined", 4)
    assert call.outcome_override == "declined" and call.review_status == "reviewed"
    events = [item for item in stub.added if isinstance(item, ReviewEvent)]
    assert [(e.kind, e.change["prior"], e.change["new"], e.prior_version, e.new_version) for e in events] == [
        ("outcome_review", "booked", "declined", 3, 4)]


def test_type_change_queues_reanalysis_with_202(client_with) -> None:
    call = make_call(primary_type="sales", outcome="booked")
    run = make_run(call)
    client, stub = client_with(call, run)
    response = client.post(f"/api/v1/calls/{call.id}/review", json=review_body(primary_type="customer_service"))
    assert response.status_code == 202
    assert response.json()["status"] == "reanalysis_queued" and response.json()["requested_generation"] == 2
    assert (call.type_override, call.primary_type, call.analysis_status) == ("customer_service", "customer_service", "stale")
    outbox = [item for item in stub.added if isinstance(item, OutboxEvent)]
    assert [(e.kind, e.generation) for e in outbox] == [("process_call", 2)]
    assert run.review_version == 3


def test_stale_generation_conflicts_without_writes(client_with) -> None:
    call = make_call(requested_generation=2)
    client, stub = client_with(call, make_run(call, generation=2))
    response = client.post(f"/api/v1/calls/{call.id}/review", json=review_body(outcome="declined"))
    assert response.status_code == 409 and response.json()["error"]["code"] == "conflict"
    assert stub.added == [] and call.outcome_override is None


def test_address_check_response_only_exposes_dict_suggestion() -> None:
    row = AddressCheck(id=uuid4(), call_id=uuid4(), purpose="service", candidate={"spoken_text": "synthetic"},
                       status="needs_confirmation", version=1, result={"suggestion": "not-a-dict", "raw": {"x": 1}})
    assert routes.address_check_response(row).suggestion is None
    row.result = {"suggestion": {"line1": "1 Synthetic Way"}}
    assert routes.address_check_response(row).suggestion == {"line1": "1 Synthetic Way"}


def test_deletion_store_progress_follows_ledger_state() -> None:
    assert {s.state for s in store_progress("pending")} == {"pending"}
    assert {s.state for s in store_progress("rechecking")} == {"complete"}
    assert [s.store for s in store_progress("complete")] == ["r2", "vector", "database"]


def test_criterion_review_uses_published_rubric_not_intake_type() -> None:
    call = make_call(type_override="customer_service")
    run = make_run(call, rubric_id="office")  # intake still says sales; the override selected the office rubric
    assert routes._run_rubric(run)["id"] == "office"
    assert routes._run_rubric(make_run(call, rubric_id=None))["id"] == "sales"  # older runs fall back to intake
