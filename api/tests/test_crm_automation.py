"""Synthetic checks of the deterministic CRM rules against the specs/07 acceptance list."""

from dataclasses import replace
from uuid import uuid4

from call_intelligence_api.crm_automation import (
    StageContext,
    decide_followups,
    decide_retention,
    decide_stage_actions,
)
from call_intelligence_api.crm_rules import normalize_identifier

READY = StageContext(stage="New", locked=False, is_sales=True, call_at_known=True, newer_conflict=False)


def plan(stage: str, decision: str = "unknown", evidence: bool = True, **ctx):
    actions = decide_stage_actions(replace(READY, stage=stage, **ctx), decision, evidence)
    return [(a.kind, a.prior, a.target, a.rule) for a in actions]


def run_to_rest(stage: str, decision: str) -> str:
    for action in decide_stage_actions(replace(READY, stage=stage), decision, True):
        if action.kind == "apply":
            stage = action.target
    return stage


def test_linked_sales_conversation_marks_new_contacted():
    assert plan("New") == [("apply", "New", "Contacted", "sales_contacted")]


def test_non_sales_or_unlinked_context_never_moves_stage():
    for decision in ("accepted", "rejected", "follow_up", "unknown"):
        assert plan("New", decision, is_sales=False) == []
        assert plan("Contacted", decision, is_sales=False) == []


def test_follow_up_and_explicit_outcomes():
    assert plan("Contacted", "follow_up") == [("apply", "Contacted", "Follow-up", "sales_follow_up")]
    assert plan("Follow-up", "follow_up") == []
    assert plan("Contacted", "accepted") == [("apply", "Contacted", "Won", "sales_accepted")]
    assert plan("Follow-up", "rejected") == [("apply", "Follow-up", "Lost", "sales_rejected")]
    assert plan("New", "accepted") == [
        ("apply", "New", "Contacted", "sales_contacted"),
        ("apply", "Contacted", "Won", "sales_accepted"),
    ]
    assert plan("Contacted", "undecided") == []


def test_missing_evidence_proposes_instead_of_applying_decision():
    assert plan("Contacted", "accepted", evidence=False) == [("propose", "Contacted", "Won", "proposed:Won")]
    assert plan("New", "rejected", evidence=False) == [
        ("apply", "New", "Contacted", "sales_contacted"),
        ("propose", "Contacted", "Lost", "proposed:Lost"),
    ]


def test_unknown_chronology_and_locked_state_only_propose():
    assert plan("Contacted", "accepted", call_at_known=False) == [("propose", "Contacted", "Won", "proposed:Won")]
    assert plan("New", call_at_known=False) == [("propose", "New", "Contacted", "proposed:Contacted")]
    assert plan("Follow-up", "rejected", locked=True) == [("propose", "Follow-up", "Lost", "proposed:Lost")]
    assert plan("New", "accepted", locked=True) == [("propose", "New", "Won", "proposed:Won")]


def test_newer_conflicting_event_blocks_automatic_change():
    assert plan("Contacted", "accepted", newer_conflict=True) == [("propose", "Contacted", "Won", "proposed:Won")]


def test_acceptance_reversed_later_needs_review_and_older_evidence_is_ignored():
    # A later rejection after Won is surfaced for review; Won/Lost reopening is never automatic.
    assert plan("Won", "rejected") == [("propose", "Won", "Lost", "proposed:Lost")]
    assert plan("Lost", "accepted") == [("propose", "Lost", "Won", "proposed:Won")]
    # Evidence from a call older than the established outcome does not reopen it.
    assert plan("Won", "rejected", newer_conflict=True) == []
    assert plan("Won", "accepted") == []
    assert plan("Lost", "follow_up") == []


def test_duplicate_delivery_is_a_no_op_after_the_first_application():
    for decision in ("accepted", "rejected", "follow_up", "unknown"):
        rested = run_to_rest("New", decision)
        assert plan(rested, decision) == []


def test_retention_is_separate_from_sales_stage():
    retention = {"intent": "cancellation", "reason": "moving", "outcome": "retained", "evidence_segment_ids": ["s1"]}
    intent = decide_retention(retention)
    assert (intent.intent, intent.outcome, intent.reason) == ("cancellation", "retained", "moving")
    # A retention call linked to an opportunity never changes the sales stage.
    assert plan("Contacted", "rejected", is_sales=False) == []
    assert decide_retention({**retention, "outcome": "closed_account"}).outcome == "unknown"
    assert decide_retention({**retention, "evidence_segment_ids": []}) is None
    assert decide_retention({**retention, "intent": "none"}) is None
    assert decide_retention(None) is None


def test_repeated_reanalysis_keeps_logical_task_ids_stable():
    call_id = str(uuid4())
    first = decide_followups(call_id, [
        {"action": "Call back Friday with a quote.", "kind": "callback", "owner_role": "employee",
         "due_text": "Friday", "evidence_segment_ids": [str(uuid4())]},
    ], None)
    # New transcript version: new segment UUIDs, different casing/punctuation from the model.
    second = decide_followups(call_id, [
        {"action": "call back friday with a quote", "kind": "callback", "owner_role": "employee",
         "due_text": "Friday", "evidence_segment_ids": [str(uuid4())]},
    ], None)
    assert len(first) == len(second) == 1
    assert first[0].logical_key == second[0].logical_key
    assert first[0].due_text == "Friday"
    other_call = decide_followups(str(uuid4()), [
        {"action": "Call back Friday with a quote.", "kind": "callback", "evidence_segment_ids": ["x"]},
    ], None)
    assert other_call[0].logical_key != first[0].logical_key


def test_commitments_are_deduped_and_need_evidence():
    call_id = str(uuid4())
    tasks = decide_followups(call_id, [
        {"action": "Send the treatment plan", "kind": "send_info", "evidence_segment_ids": ["a"]},
        {"action": "send the treatment plan!", "kind": "send_info", "evidence_segment_ids": ["b"]},
        {"action": "Schedule inspection", "kind": "schedule", "evidence_segment_ids": []},
        {"action": "", "kind": "other", "evidence_segment_ids": ["c"]},
        {"action": "Mystery", "kind": "not_a_kind", "evidence_segment_ids": ["d"]},
    ], None)
    assert [(t.action, t.kind) for t in tasks] == [("Send the treatment plan", "send_info"), ("Mystery", "other")]


def test_follow_up_decision_proposes_one_task_only_when_no_commitment_exists():
    call_id = str(uuid4())
    signal = {"decision": "follow_up", "evidence_segment_ids": ["s1"]}
    fallback = decide_followups(call_id, None, signal)
    assert len(fallback) == 1 and fallback[0].kind == "follow_up"
    assert decide_followups(call_id, [], {"decision": "follow_up", "evidence_segment_ids": []}) == []
    with_commitment = decide_followups(call_id, [
        {"action": "Call Tuesday", "kind": "callback", "evidence_segment_ids": ["s2"]},
    ], signal)
    assert [t.action for t in with_commitment] == ["Call Tuesday"]


def test_identity_collisions_surface_as_candidates_not_merges():
    # Contacts are matched on exact normalized identifiers; different spellings collide and must be reviewed.
    assert normalize_identifier("phone", "(555) 010-0199") == normalize_identifier("phone", "555.010.0199")
    assert normalize_identifier("email", " Pat@Example.COM ") == normalize_identifier("email", "pat@example.com")


def test_reanalysis_retires_only_untouched_automatic_tasks() -> None:
    from uuid import uuid4

    from call_intelligence_api.crm_automation import is_untouched_automatic_task
    from call_intelligence_api.crm_models import FollowUp

    old_run, new_run = uuid4(), uuid4()
    auto_ref = [{"source_kind": "transcript", "commitment_kind": "callback"}]

    def task(**overrides):
        values = {"source_refs": auto_ref, "analysis_id": old_run, "status": "pending_approval", "version": 1,
                  "logical_key": "old-key"}
        return FollowUp(**{**values, **overrides})

    assert is_untouched_automatic_task(task(), new_run, {"new-key"})
    assert not is_untouched_automatic_task(task(logical_key="new-key"), new_run, {"new-key"})  # re-proposed
    assert not is_untouched_automatic_task(task(version=2), new_run, set())  # a person edited it
    assert not is_untouched_automatic_task(task(status="assigned"), new_run, set())  # already approved
    assert not is_untouched_automatic_task(task(source_refs=[{"source_kind": "transcript"}]), new_run, set())  # manual
    assert not is_untouched_automatic_task(task(analysis_id=new_run), new_run, set())  # same analysis


def test_enum_like_actions_read_as_words() -> None:
    from call_intelligence_api.crm_rules import readable_action

    assert readable_action("send_info") == "Send info"
    assert readable_action("  schedule   technician visit ") == "schedule technician visit"
    assert readable_action("Call back re: $59_plan") == "Call back re: $59_plan"  # real sentences untouched
