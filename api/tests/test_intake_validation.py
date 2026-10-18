"""Synthetic intake outputs: new analysis fields keep only canonical, valid evidence."""

from uuid import uuid4

from call_intelligence_api.ai import IntakeOutput
from call_intelligence_api.call_models import TranscriptSegment
from call_intelligence_api.processing import _validate_intake


def segments() -> list[TranscriptSegment]:
    return [TranscriptSegment(id=uuid4(), ordinal=index, start_seconds=index * 5.0, end_seconds=index * 5.0 + 4,
                              speaker_label="A", speaker_role="unknown", role_uncertain=True,
                              text=f"Synthetic line {index}") for index in range(3)]


def base_intake() -> dict:
    return {"primary_type": "sales", "speaker_roles": [], "facts": [], "needs_review": [],
            "outcome": {"kind": "follow_up", "evidence_segment_ids": ["S0001"], "reason": "synthetic"}}


def address(country: str | None, ids: list[str], line1: str | None = None) -> dict:
    return {"purpose": "service", "spoken_text": "synthetic", "line1": line1, "unit": None, "city": None,
            "region": None, "postal_code": None, "country_code": country, "evidence_segment_ids": ids}


def test_new_fields_canonicalise_ids_and_drop_or_downgrade_invalid_items() -> None:
    segs = segments()
    raw = base_intake() | {
        "commitments": [
            {"action": "Call back", "kind": "callback", "owner_role": "employee", "due_text": "tomorrow afternoon",
             "evidence_segment_ids": ["S0002"]},
            {"action": "Send quote", "kind": "send_info", "owner_role": "employee", "due_text": None,
             "evidence_segment_ids": ["S0099"]},
            {"action": "Uncited", "kind": "other", "owner_role": "unknown", "due_text": None, "evidence_segment_ids": []},
        ],
        "sales_signal": {"decision": "accepted", "evidence_segment_ids": ["S0001", "bogus"]},
        "retention": {"intent": "cancellation", "reason": None, "outcome": "retained", "evidence_segment_ids": []},
        "address_candidates": [
            address("us", ["S0000"], "1 Test Way"),
            address("USA", [str(segs[1].id)]),
            address(None, ["S0042"]),
        ],
    }
    out = _validate_intake(raw, segs)

    assert [item["action"] for item in out["commitments"]] == ["Call back"]
    assert out["commitments"][0]["evidence_segment_ids"] == [str(segs[2].id)]
    assert out["commitments"][0]["due_text"] == "tomorrow afternoon"
    assert out["sales_signal"] == {"decision": "unknown", "evidence_segment_ids": []}
    assert out["retention"]["intent"] == "unknown" and out["retention"]["outcome"] == "unknown"
    assert [item["country_code"] for item in out["address_candidates"]] == ["US", None]
    assert out["address_candidates"][0]["evidence_segment_ids"] == [str(segs[0].id)]
    reasons = out["needs_review"]
    assert sum("commitment" in reason for reason in reasons) == 2
    assert sum("address candidate" in reason for reason in reasons) == 1
    assert any("Sales signal" in reason for reason in reasons)
    assert any("Retention" in reason for reason in reasons)


def test_valid_signals_survive_and_older_intake_is_not_extended() -> None:
    segs = segments()
    raw = base_intake() | {
        "sales_signal": {"decision": "rejected", "evidence_segment_ids": ["S0002"]},
        "retention": {"intent": "none", "reason": None, "outcome": "unknown", "evidence_segment_ids": []},
    }
    out = _validate_intake(raw, segs)
    assert out["sales_signal"] == {"decision": "rejected", "evidence_segment_ids": [str(segs[2].id)]}
    assert out["retention"]["intent"] == "none" and out["needs_review"] == []
    older = _validate_intake(base_intake(), segs)
    assert not {"commitments", "address_candidates", "sales_signal", "retention"} & set(older)


def test_intake_schema_requires_every_field_for_strict_structured_outputs() -> None:
    schema = IntakeOutput.model_json_schema()
    for item in [schema, *schema.get("$defs", {}).values()]:
        if item.get("type") == "object":
            assert set(item.get("required", [])) == set(item["properties"]), item.get("title")
    assert {"commitments", "sales_signal", "retention", "address_candidates"} <= set(schema["required"])


def test_bare_segment_ids_become_one_readable_review_reason() -> None:
    from call_intelligence_api.processing import _validate_intake

    result = _validate_intake({"needs_review": ["S0113", "Price unclear", "s0115", " "]}, [])
    assert result["needs_review"] == [
        "Price unclear",
        "Model flagged transcript segments S0113, s0115 without a stated reason",
    ]


def test_speaker_role_keeps_valid_evidence_and_drops_bad_references() -> None:
    from uuid import uuid4

    from call_intelligence_api.call_models import TranscriptSegment
    from call_intelligence_api.processing import _validate_intake

    segment = TranscriptSegment(id=uuid4(), ordinal=3, start_seconds=1.0, end_seconds=2.0, text="Hi")
    raw = {"speaker_roles": [
        {"speaker_label": "chunk1:A", "role": "customer", "uncertain": False, "evidence_segment_ids": ["S0003", "S9999"]},
        {"speaker_label": "chunk1:B", "role": "employee", "uncertain": False, "evidence_segment_ids": ["S9999"]},
    ]}
    kept, lost = _validate_intake(raw, [segment])["speaker_roles"]
    assert (kept["role"], kept["evidence_segment_ids"]) == ("customer", [str(segment.id)])
    assert (lost["role"], lost["uncertain"]) == ("unknown", True)


def test_temperature_only_for_models_that_accept_it(monkeypatch) -> None:
    from types import SimpleNamespace

    from call_intelligence_api import ai

    monkeypatch.setattr(ai, "settings", lambda: SimpleNamespace(openai_text_model="gpt-4.1-mini"))
    assert ai.deterministic() == {"temperature": 0}
    monkeypatch.setattr(ai, "settings", lambda: SimpleNamespace(openai_text_model="o4-mini"))
    assert ai.deterministic() == {}


def test_odd_transcription_segments_do_not_reject_the_whole_call() -> None:
    from call_intelligence_api.processing import clean_segments

    raw = [
        {"start": 0.0, "end": 2.0, "text": " Hello "},
        {"start": 2.0, "end": 3.0, "text": "   "},          # silence: skipped, not lost speech
        {"start": -0.2, "end": 4.0, "text": "early"},       # clamped to 0
        {"start": 98.0, "end": 103.0, "text": "tail"},      # small overrun: clamped to the audio end
    ]
    kept, lost = clean_segments(raw, 100.0)
    assert [(start, end, text) for start, end, text, _ in kept] == [(0.0, 2.0, "Hello"), (0.0, 4.0, "early"), (98.0, 100.0, "tail")]
    assert lost is False

    kept, lost = clean_segments([{"start": 150.0, "end": 151.0, "text": "outside"}, {"start": 1, "end": 2, "text": "ok"}], 100.0)
    assert [text for *_, text, _ in kept] == ["ok"] and lost is True  # dropped speech => not complete coverage
