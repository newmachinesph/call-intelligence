"""Uploads wait for an explicit Process request; failed hand-offs become visible and restartable."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from call_intelligence_api.call_routes import process_block_reason
from call_intelligence_api.dispatcher import dispatch_verdict


def call(**overrides):
    values = {"deletion_status": "active", "processing_status": "uploaded", "error_code": None, "requested_generation": 1}
    return SimpleNamespace(**{**values, **overrides})


@pytest.mark.parametrize("state, error, expected", [
    ("uploaded", None, None),                                   # verified upload, never processed: may start
    ("failed", "dispatch_failed", None),                        # hand-off failed: Process restarts it
    ("uploading", None, "Upload has not finished"),
    ("queued", None, "Already processing"),
    ("running", None, "Already processing"),
    ("complete", None, "Already processed; use Re-analyze to run it again"),
    ("failed", "RuntimeError", "Already processed; use Re-analyze to run it again"),  # stage failure uses Retry
])
def test_process_is_allowed_only_for_unprocessed_or_undispatched_calls(state, error, expected) -> None:
    assert process_block_reason(call(processing_status=state, error_code=error)) == expected


def test_deleted_or_unknown_calls_cannot_be_processed() -> None:
    assert process_block_reason(None) == "Call not found"
    assert process_block_reason(call(deletion_status="pending")) == "Call not found"


def test_exhausted_hand_off_is_surfaced_only_when_work_never_started() -> None:
    event = SimpleNamespace(call_id=uuid4(), generation=1)
    assert dispatch_verdict(call(processing_status="queued"), event) == "call_failed"
    assert dispatch_verdict(call(processing_status="running"), event) == "leave"
    assert dispatch_verdict(call(processing_status="complete"), event) == "close"
    assert dispatch_verdict(call(requested_generation=2), event) == "superseded"
    assert dispatch_verdict(None, event) == "superseded"


def test_failure_codes_name_known_causes_without_content() -> None:
    from call_intelligence_api.processing import failure_code

    assert failure_code(ValueError("Invalid transcription segment")) == "invalid_transcript_segment"
    assert failure_code(ValueError("customer said 38 Birch Hill Road")) == "ValueError"  # unknown text never recorded
    assert failure_code(TimeoutError()) == "TimeoutError"


def test_qstash_dedup_id_uses_only_allowed_characters() -> None:
    from call_intelligence_api.dispatcher import dedup_id

    value = dedup_id(uuid4(), 3)
    assert ":" not in value and value.endswith("-generation-3-retry-0")


def test_workflow_steps_target_the_public_endpoint_not_the_qstash_host():
    from upstash_workflow.serve.options import _determine_urls

    from call_intelligence_api.workflow import WorkflowSettings, workflow_urls

    config = WorkflowSettings(
        upstash_workflow_url="https://qstash-us-east-1.upstash.io",
        qstash_token="t", qstash_current_signing_key="k", qstash_next_signing_key="k",
        processing_service_secret="s", processing_public_url="https://api.example.test/",
    )
    base_url, url = workflow_urls(config)

    class Request:
        url = "http://internal:8000/api/v1/internal/workflow/call"

    workflow_url, _ = _determine_urls(Request(), url, base_url, False, None)
    assert workflow_url == "https://api.example.test/api/v1/internal/workflow/call"


def test_new_hand_offs_wait_for_one_of_two_slots_but_redeliveries_do_not():
    from call_intelligence_api.dispatcher import awaits_slot

    new, delivered = SimpleNamespace(dispatch_id=None), SimpleNamespace(dispatch_id="msg_1")
    assert not awaits_slot(new, 0) and not awaits_slot(new, 1)
    assert awaits_slot(new, 2) and awaits_slot(new, 3)
    assert not awaits_slot(delivered, 2)


def test_activity_feed_is_routed_before_the_call_id_route():
    from call_intelligence_api.call_routes import router

    paths = [route.path.removeprefix("/api/v1") for route in router.routes]
    assert paths.index("/calls/activity") < paths.index("/calls/{call_id}")


def test_each_manual_retry_gets_its_own_qstash_dedup_id():
    from call_intelligence_api.dispatcher import dedup_id

    call_id = uuid4()
    assert dedup_id(call_id, 1) == dedup_id(call_id, 1, 0)  # automatic re-publish stays deduplicated
    assert len({dedup_id(call_id, 1, 0), dedup_id(call_id, 1, 1), dedup_id(call_id, 2, 0)}) == 3
