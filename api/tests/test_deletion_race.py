"""Deleting a call while work is in flight cannot leave or resurrect content (specs/08 Deletion, QA08/QA14).

Sessions, R2 and Vector are small in-memory fakes; no database or provider is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from call_intelligence_api import deletion, dispatcher, processing
from call_intelligence_api.auth import AuthenticatedMember
from call_intelligence_api.models import MemberRole
from call_intelligence_api.processing import StaleGeneration


class FakeSession:
    """Answers scalar()/scalars() from queues in call order; records adds."""

    def __init__(self, scalars: list | None = None, scalar: list | None = None, get: dict | None = None):
        self._scalars, self._scalar, self._get = list(scalars or []), list(scalar or []), get or {}
        self.added: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def begin(self):
        return self

    def scalar(self, _statement):
        return self._scalar.pop(0) if self._scalar else None

    def scalars(self, _statement):
        return SimpleNamespace(all=lambda: self._scalars.pop(0) if self._scalars else [])

    def get(self, _model, key):
        return self._get.get(key)

    def add(self, item):
        self.added.append(item)

    def flush(self):
        pass


def sessions(monkeypatch: pytest.MonkeyPatch, module, *queue: FakeSession) -> None:
    pending = list(queue)
    monkeypatch.setattr(module, "domain_engine", lambda: None)
    monkeypatch.setattr(module, "Session", lambda _engine: pending.pop(0))


def live_call(**overrides):
    values = {"id": uuid4(), "organization_id": uuid4(), "deletion_status": "active", "requested_generation": 3,
              "processing_status": "running", "analysis_status": "running", "index_status": "indexing",
              "error_code": None, "original_audio_key": None, "staging_key": None}
    return SimpleNamespace(**{**values, **overrides})


def running_step(**overrides):
    values = {"fence": 7, "lease_owner": "worker-1", "state": "running", "error_code": None,
              "lease_until": datetime.now(UTC) + timedelta(minutes=5)}
    return SimpleNamespace(**{**values, **overrides})


# 1. A worker that started before deletion can never publish afterwards.
@pytest.mark.parametrize("call_change, step_change", [
    ({"deletion_status": "pending"}, {}),   # tombstoned while the worker ran
    ({"deletion_status": "purged"}, {}),    # already purged
    ({"requested_generation": 4}, {}),      # delete (or re-analysis) bumped the generation
    ({}, {"fence": 8}),                     # lease expired and another worker took over
    ({}, {"lease_owner": "worker-2"}),
    ({}, {"state": "failed"}),
])
def test_publication_rejects_deleted_or_replaced_work(call_change: dict, step_change: dict) -> None:
    session = FakeSession(scalar=[live_call(**call_change), running_step(**step_change)])
    with pytest.raises(StaleGeneration):
        processing._locked_current(session, uuid4(), 3, "analysis", 7, "worker-1")


def test_current_worker_may_publish() -> None:
    call, step = live_call(), running_step()
    assert processing._locked_current(FakeSession(scalar=[call, step]), call.id, 3, "indexing", 7, "worker-1") == (call, step)


def test_claim_refuses_a_deleted_call(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions(monkeypatch, processing, FakeSession(scalar=[live_call(deletion_status="pending")]))
    with pytest.raises(StaleGeneration):
        processing._claim(uuid4(), 3, "transcription")


def test_stale_worker_failure_cannot_overwrite_a_deleted_call(monkeypatch: pytest.MonkeyPatch) -> None:
    call, step = live_call(deletion_status="pending", processing_status="deleted"), running_step()
    sessions(monkeypatch, processing, FakeSession(scalar=[call, step]))
    processing._fail(call.id, 3, "analysis", 7, "worker-1", "Timeout")
    assert (call.processing_status, call.error_code, step.state) == ("deleted", None, "running")


# 2. delete_call tombstones once; repeating it returns the same job.
def owner_for(call) -> AuthenticatedMember:
    return AuthenticatedMember(user_id="owner", organization_id=call.organization_id, role=MemberRole.OWNER)


def test_delete_tombstones_bumps_generation_and_supersedes_queued_work(monkeypatch: pytest.MonkeyPatch) -> None:
    call = live_call(original_audio_key="audio/o/c/x.mp3", staging_key="staging/o/c/u.mp3")
    queued, done = SimpleNamespace(state="pending"), SimpleNamespace(state="complete")
    session = FakeSession(scalar=[call], scalars=[["audio/o/c/x.mp3"], [queued, done]])
    sessions(monkeypatch, deletion, session)
    response = deletion.delete_call(call.id, owner_for(call))
    ledger = session.added[0]
    assert (call.deletion_status, call.requested_generation, call.processing_status) == ("pending", 4, "deleted")
    assert (queued.state, done.state) == ("superseded", "complete")
    assert ledger.object_keys == ["audio/o/c/x.mp3", "staging/o/c/u.mp3"] and response.deletion_job_id == ledger.id


def test_repeated_delete_returns_the_existing_job(monkeypatch: pytest.MonkeyPatch) -> None:
    call, job_id = live_call(deletion_status="pending"), uuid4()
    session = FakeSession(scalar=[call, job_id])
    sessions(monkeypatch, deletion, session)
    response = deletion.delete_call(call.id, owner_for(call))
    assert response.deletion_job_id == job_id and session.added == []


# 3. Purge waits for in-flight work, then sweeps storage and vectors on every pass.
class FakeBucket:
    def __init__(self, keys: list[str]):
        self.keys = set(keys)
        self.deleted: list[str] = []

    def get_paginator(self, _name):
        return SimpleNamespace(paginate=lambda Bucket, Prefix: [
            {"Contents": [{"Key": key} for key in sorted(self.keys) if key.startswith(Prefix)]}])

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.keys.discard(Key)


class FakeVectors:
    def __init__(self):
        self.filters: list[str] = []

    def delete(self, namespace, filter):
        self.filters.append(filter)


def purge_setup(monkeypatch, *, lease_active: bool, ledger_state: str = "pending", created_ago: int = 1,
                attempts: int = 0, bucket_keys: list[str] | None = None):
    call = live_call(deletion_status="pending")
    ledger = SimpleNamespace(id=uuid4(), call_id=call.id, organization_id=call.organization_id, object_keys=[],
                             state=ledger_state, attempts=attempts, last_attempt_at=None, completed_at=None,
                             created_at=datetime.now(UTC) - timedelta(minutes=created_ago))
    bucket, vectors, purged = FakeBucket(bucket_keys or []), FakeVectors(), []
    read = FakeSession(scalars=[[ledger]], scalar=[uuid4() if lease_active else None])
    sessions(monkeypatch, deletion, read, FakeSession(scalar=[ledger, call]))
    monkeypatch.setattr(deletion, "r2_client", lambda: bucket)
    monkeypatch.setattr(deletion, "r2_settings", lambda: SimpleNamespace(r2_bucket_name="private"))
    monkeypatch.setattr(deletion, "vector_client", lambda: vectors)
    monkeypatch.setattr(deletion, "_purge_database", lambda _session, target: purged.append(target.id))
    return call, ledger, bucket, vectors, purged


def test_purge_waits_while_a_worker_still_holds_a_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    _call, ledger, bucket, vectors, purged = purge_setup(monkeypatch, lease_active=True, bucket_keys=["audio/x"])
    assert deletion.purge_due_once() is False
    assert (bucket.deleted, vectors.filters, purged, ledger.state) == ([], [], [], "pending")


def test_first_pass_sweeps_late_objects_vectors_and_database(monkeypatch: pytest.MonkeyPatch) -> None:
    org, call_id = uuid4(), uuid4()
    late_audio = f"audio/{org}/{call_id}/written-after-delete.mp3"
    other_call = f"audio/{org}/{uuid4()}/keep.mp3"
    call, ledger, bucket, vectors, purged = purge_setup(monkeypatch, lease_active=False)
    call.id, call.organization_id, ledger.call_id, ledger.organization_id = call_id, org, call_id, org
    bucket.keys = {late_audio, other_call}
    assert deletion.purge_due_once() is True
    assert bucket.deleted == [late_audio] and other_call in bucket.keys  # only this call's prefix
    assert vectors.filters == [f"call_id = '{call_id}'"] and purged == [call_id]
    assert ledger.state == "rechecking" and call.deletion_status == "pending"


def test_recheck_pass_removes_late_vectors_without_repurging_then_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    call, ledger, _bucket, vectors, purged = purge_setup(
        monkeypatch, lease_active=False, ledger_state="rechecking", created_ago=25, attempts=1)
    assert deletion.purge_due_once() is True
    assert vectors.filters == [f"call_id = '{call.id}'"]  # stale worker's late upsert is swept again
    assert purged == []  # database content was already removed on the first pass
    assert (ledger.state, call.deletion_status, ledger.object_keys) == ("complete", "purged", [])


def test_purge_keeps_rechecking_inside_the_late_write_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _call, ledger, *_ = purge_setup(monkeypatch, lease_active=False, ledger_state="rechecking", created_ago=10, attempts=1)
    deletion.purge_due_once()
    assert ledger.state == "rechecking"


# 4. The dispatcher never starts work for a deleted call.
def test_dispatcher_supersedes_events_for_deleted_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    call = live_call(deletion_status="pending", requested_generation=4)
    event = SimpleNamespace(id=uuid4(), call_id=call.id, generation=3, state="pending", attempts=0, dispatched_at=None)
    ran = []
    sessions(monkeypatch, dispatcher, FakeSession(scalars=[[event]], get={call.id: call}))
    monkeypatch.setattr(dispatcher, "budget_exhausted", lambda _session: None)
    monkeypatch.setattr(dispatcher, "process_call", lambda *args: ran.append(args))
    assert dispatcher.dispatch_once() is False
    assert event.state == "superseded" and ran == []
