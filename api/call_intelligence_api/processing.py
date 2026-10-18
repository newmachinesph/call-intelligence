"""Durable, fenced processing stages for one finalized call at a time."""

from __future__ import annotations

import hashlib
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from call_intelligence_api import ai
from call_intelligence_api.auth import domain_engine
from call_intelligence_api.call_models import (
    AnalysisRun,
    CallRecord,
    CriterionDecision,
    OutboxEvent,
    ProcessingStep,
    TranscriptSegment,
    TranscriptVersion,
)
from call_intelligence_api.evidence import resolve_segment
from call_intelligence_api.scoring import load_rubric, score_evaluation
from call_intelligence_api.storage import r2_client, r2_settings

STAGE_LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 3


# Our own ValueError messages are fixed, content-free strings; recording them turns "ValueError" into a diagnosis.
KNOWN_FAILURES = {
    "Call audio is not finalized": "audio_not_finalized",
    "Finalized audio hash mismatch": "audio_hash_mismatch",
    "Transcription contained no speech segments": "no_speech_segments",
    "Invalid transcription segment": "invalid_transcript_segment",
    "Transcript is not ready": "transcript_not_ready",
    "No canonical transcript chunks": "no_transcript_chunks",
    "Embedding count differs from chunk count": "embedding_count_mismatch",
    "Intake model returned no structured assessment": "intake_empty",
    "Evaluator returned no structured assessment": "evaluation_empty",
}


def failure_code(exc: BaseException) -> str:
    """Content-free error code: a known message slug, otherwise only the exception type."""
    return KNOWN_FAILURES.get(str(exc), type(exc).__name__)[:80]


class StaleGeneration(Exception):
    """The worker's source generation was replaced or deleted."""


def _claim(call_id: UUID, generation: int, stage: str) -> tuple[int, str] | None:
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if call is None or call.deletion_status != "active" or call.requested_generation != generation:
            raise StaleGeneration()
        step = session.scalar(
            select(ProcessingStep)
            .where(ProcessingStep.call_id == call_id, ProcessingStep.generation == generation, ProcessingStep.stage == stage)
            .with_for_update()
        )
        if step is None:
            step = ProcessingStep(
                organization_id=call.organization_id,
                call_id=call_id,
                generation=generation,
                stage=stage,
            )
            session.add(step)
            session.flush()
        if step.state == "complete":
            return None
        if step.state == "running" and step.lease_until and step.lease_until > datetime.now(UTC):
            raise RuntimeError("Stage is already leased")
        if step.attempt >= MAX_ATTEMPTS:
            raise RuntimeError("Stage retry limit reached")
        step.attempt += 1
        step.fence += 1
        step.state = "running"
        step.lease_owner = str(uuid4())
        step.lease_until = datetime.now(UTC) + STAGE_LEASE
        step.error_code = None
        if stage != "indexing":  # chat indexing runs after results exist and reports through index_status
            call.processing_status = "running"
        call.error_code = None
        if stage == "analysis":
            call.analysis_status = "running"
        if stage == "indexing":
            call.index_status = "indexing"
        return step.fence, step.lease_owner


def _locked_current(session: Session, call_id: UUID, generation: int, stage: str, fence: int, owner: str):
    call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
    step = session.scalar(
        select(ProcessingStep)
        .where(ProcessingStep.call_id == call_id, ProcessingStep.generation == generation, ProcessingStep.stage == stage)
        .with_for_update()
    )
    if (
        call is None
        or step is None
        or call.deletion_status != "active"
        or call.requested_generation != generation
        or step.fence != fence
        or step.lease_owner != owner
        or step.state != "running"
    ):
        raise StaleGeneration()
    return call, step


def _fail(call_id: UUID, generation: int, stage: str, fence: int, owner: str, code: str) -> None:
    with Session(domain_engine()) as session, session.begin():
        try:
            call, step = _locked_current(session, call_id, generation, stage, fence, owner)
        except StaleGeneration:
            return
        step.state = "failed"
        step.error_code = code
        step.lease_owner = None
        step.lease_until = None
        call.error_code = code
        if stage == "transcription":
            call.processing_status = "failed"
        elif stage == "analysis":
            call.analysis_status = "failed"
            call.processing_status = "failed"
        else:
            call.index_status = "failed"


def _audio_to_temp(call: CallRecord) -> Path:
    if not call.original_audio_key or not call.audio_sha256:
        raise ValueError("Call audio is not finalized")
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as output:
        path = Path(output.name)
        digest = hashlib.sha256()
        obj = r2_client().get_object(Bucket=r2_settings().r2_bucket_name, Key=call.original_audio_key)
        for chunk in obj["Body"].iter_chunks(chunk_size=1024 * 1024):
            if chunk:
                digest.update(chunk)
                output.write(chunk)
    if digest.hexdigest() != call.audio_sha256:
        path.unlink(missing_ok=True)
        raise ValueError("Finalized audio hash mismatch")
    return path


def clean_segments(raw_segments: list, duration: float) -> tuple[list[tuple[float, float, str, dict]], bool]:
    """Keep usable speech from a transcription response instead of rejecting the whole call for one odd segment.

    Empty segments (silence) are skipped; small timestamp overruns are clamped to the audio; a segment that
    cannot be placed in the recording is dropped. Returns (segments, lost_speech) where lost_speech means
    spoken text was dropped, so the transcript must not claim complete coverage.
    """
    kept, lost_speech = [], False
    for item in raw_segments:
        try:
            start, end, spoken = float(item["start"]), float(item["end"]), str(item.get("text") or "").strip()
        except (KeyError, TypeError, ValueError):
            lost_speech = True
            continue
        if not spoken:
            continue
        start, end = max(0.0, start), min(end, duration) if duration else end
        if start > (duration or start) + 5 or end < start - 5:
            lost_speech = True
            continue
        kept.append((start, max(start, end), spoken, item))
    return kept, lost_speech


def transcribe_stage(call_id: UUID, generation: int) -> UUID | None:
    claim = _claim(call_id, generation, "transcription")
    if claim is None:
        return None
    fence, owner = claim
    path = None
    try:
        with Session(domain_engine()) as session:
            existing_call = session.get(CallRecord, call_id)
            existing_id = existing_call.active_transcript_id
            existing = session.get(TranscriptVersion, existing_id) if existing_id else None
            if existing and existing.source_audio_sha256 == existing_call.audio_sha256:
                with Session(domain_engine()) as publish, publish.begin():
                    _, step = _locked_current(publish, call_id, generation, "transcription", fence, owner)
                    step.result_id = existing.id
                    step.state = "complete"
                    step.lease_owner = None
                    step.lease_until = None
                return existing.id
        with Session(domain_engine()) as session:
            call = session.get(CallRecord, call_id)
            path = _audio_to_temp(call)
            audio_hash = call.audio_sha256
            duration = call.duration_seconds or 0
        raw = ai.transcribe(path, duration)
        candidate_segments, lost_speech = clean_segments(raw.get("segments") or [], duration)
        if not candidate_segments:
            raise ValueError("Transcription contained no speech segments")
        with Session(domain_engine()) as session, session.begin():
            call, step = _locked_current(session, call_id, generation, "transcription", fence, owner)
            version = (session.scalar(select(func.max(TranscriptVersion.version)).where(TranscriptVersion.call_id == call_id)) or 0) + 1
            transcript = TranscriptVersion(
                organization_id=call.organization_id,
                call_id=call_id,
                version=version,
                source_audio_sha256=audio_hash,
                model_name=ai.settings().openai_transcription_model,
                origin="automatic",
                raw_response=raw,
                complete_coverage=not lost_speech,  # dropped spoken text means an incomplete record
            )
            session.add(transcript)
            session.flush()
            for ordinal, (start, end, spoken, item) in enumerate(candidate_segments):
                session.add(
                    TranscriptSegment(
                        organization_id=call.organization_id,
                        transcript_id=transcript.id,
                        ordinal=ordinal,
                        start_seconds=start,
                        end_seconds=end,
                        speaker_label=str(item.get("speaker")) if item.get("speaker") is not None else None,
                        speaker_role="unknown",
                        role_uncertain=True,
                        text=spoken,
                    )
                )
            call.active_transcript_id = transcript.id
            step.result_id = transcript.id
            step.state = "complete"
            step.lease_owner = None
            step.lease_until = None
            return transcript.id
    except Exception as exc:
        _fail(call_id, generation, "transcription", fence, owner, failure_code(exc))
        raise
    finally:
        if path:
            path.unlink(missing_ok=True)


def _validate_intake(raw: dict, segments: list[TranscriptSegment]) -> dict:
    def canonical(raw_ids: list[str]) -> tuple[list[str], bool]:
        resolved = [resolve_segment(str(identifier), segments) for identifier in raw_ids]
        return [str(segment.id) for segment in resolved if segment], any(segment is None for segment in resolved)

    for role in raw.get("speaker_roles", []):
        ids, invalid = canonical(role.get("evidence_segment_ids", []))
        role["evidence_segment_ids"] = ids
        if invalid and not ids:
            # Only an entry with no valid evidence at all loses its role; bad extra references are just dropped.
            role["role"] = "unknown"
            role["uncertain"] = True
            raw.setdefault("needs_review", []).append(
                f"Speaker {role.get('speaker_label') or '?'} role had no valid evidence reference")
    outcome = raw.get("outcome", {})
    outcome_ids, outcome_invalid = canonical(outcome.get("evidence_segment_ids", []))
    outcome["evidence_segment_ids"] = outcome_ids
    if outcome_invalid:
        outcome["kind"] = "unknown"
        outcome["evidence_segment_ids"] = []
        raw.setdefault("needs_review", []).append("Outcome had an invalid evidence reference")
    for fact in raw.get("facts", []):
        fact_ids, fact_invalid = canonical(fact.get("evidence_segment_ids", []))
        fact["evidence_segment_ids"] = fact_ids
        if fact_invalid:
            fact["value"] = None
            fact["status"] = "uncertain"
            fact["evidence_segment_ids"] = []
            raw.setdefault("needs_review", []).append("A fact had an invalid evidence reference")
    # Items without valid evidence are dropped/downgraded: downstream CRM automation acts on them.
    for key, label in (("commitments", "A commitment"), ("address_candidates", "An address candidate")):
        if key not in raw:
            continue
        kept = []
        for item in raw[key]:
            ids, invalid = canonical(item.get("evidence_segment_ids", []))
            if invalid or not ids:
                raw.setdefault("needs_review", []).append(f"{label} had no valid evidence reference")
                continue
            item["evidence_segment_ids"] = ids
            if "country_code" in item:
                code = str(item["country_code"] or "").strip().upper()
                item["country_code"] = code if re.fullmatch(r"[A-Z]{2}", code) else None
            kept.append(item)
        raw[key] = kept
    signal = raw.get("sales_signal")
    if signal is not None:
        ids, invalid = canonical(signal.get("evidence_segment_ids", []))
        signal["evidence_segment_ids"] = ids
        if invalid or (not ids and signal.get("decision") != "unknown"):
            signal["decision"] = "unknown"
            signal["evidence_segment_ids"] = []
            raw.setdefault("needs_review", []).append("Sales signal had no valid evidence reference")
    retention = raw.get("retention")
    if retention is not None:
        ids, invalid = canonical(retention.get("evidence_segment_ids", []))
        retention["evidence_segment_ids"] = ids
        claims = retention.get("intent") not in {"none", "unknown"} or retention.get("outcome") != "unknown"
        if invalid or (not ids and claims):
            retention["intent"] = "unknown"
            retention["outcome"] = "unknown"
            retention["evidence_segment_ids"] = []
            raw.setdefault("needs_review", []).append("Retention signal had no valid evidence reference")
    raw["needs_review"] = readable_review_reasons(raw.get("needs_review", []))
    return raw


def readable_review_reasons(items: list) -> list[str]:
    """Models sometimes list bare segment IDs as "reasons"; keep them reviewable but readable."""
    reasons = [str(item).strip() for item in items if str(item).strip()]
    bare = [item for item in reasons if re.fullmatch(r"S\d{1,5}", item, flags=re.IGNORECASE)]
    if bare:
        reasons = [item for item in reasons if item not in bare]
        reasons.append(f"Model flagged transcript segments {', '.join(bare)} without a stated reason")
    return reasons


def analysis_stage(call_id: UUID, generation: int) -> UUID | None:
    claim = _claim(call_id, generation, "analysis")
    if claim is None:
        return None
    fence, owner = claim
    try:
        with Session(domain_engine()) as session:
            call = session.get(CallRecord, call_id)
            if not call.active_transcript_id:
                raise ValueError("Transcript is not ready")
            transcript_id = call.active_transcript_id
            transcript = session.get(TranscriptVersion, transcript_id)
            segments = session.scalars(
                select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript_id).order_by(TranscriptSegment.ordinal)
            ).all()
            transcript_version = transcript.version
            complete_coverage = transcript.complete_coverage
            call_at = call.call_at.isoformat() if call.call_at else None
            call_timezone = call.call_timezone
            type_override = call.type_override
        intake_output, intake_usage = ai.intake(segments, call_at, call_timezone)
        intake_data = _validate_intake(intake_output.model_dump(mode="json"), segments)
        effective_type = type_override or intake_data["primary_type"]
        if type_override:
            # The model's own primary_type stays untouched in intake; the reviewer's type selects the rubric.
            intake_data["type_override_applied"] = type_override
        rubric = load_rubric(effective_type)
        evaluated = None
        score = None
        eval_usage = {}
        if rubric:
            evaluated, eval_usage = ai.evaluate(segments, intake_output, rubric)
            score = score_evaluation(
                rubric=rubric,
                evaluation=evaluated.model_dump(mode="json"),
                intake=intake_data,
                segments=segments,
                call_id=call_id,
                transcript_version=transcript_version,
                complete_coverage=complete_coverage,
            )
        with Session(domain_engine()) as session, session.begin():
            call, step = _locked_current(session, call_id, generation, "analysis", fence, owner)
            if call.active_transcript_id != transcript_id:
                raise StaleGeneration()
            run = AnalysisRun(
                organization_id=call.organization_id,
                call_id=call_id,
                transcript_id=transcript_id,
                generation=generation,
                model_name=ai.settings().openai_text_model,
                prompt_version=ai.PROMPT_VERSION,
                rubric_id=rubric["id"] if rubric else None,
                rubric_version=rubric["version"] if rubric else None,
                state="ready" if score and score["grade"] and not intake_data["needs_review"] else "needs_review",
                intake=intake_data,
                raw_evaluation=evaluated.model_dump(mode="json") if evaluated else None,
                coaching=evaluated.coaching.model_dump(mode="json") if evaluated else None,
                confirmed_points=score["confirmed_points"] if score else None,
                nominal_maximum=score["nominal_maximum"] if score else None,
                unresolved_count=score["unresolved_count"] if score else None,
                grade=score["grade"] if score else None,
                usage={"intake": intake_usage, "evaluation": eval_usage},
                completed_at=datetime.now(UTC),
            )
            session.add(run)
            session.flush()
            if score:
                for decision in score["criteria"]:
                    session.add(
                        CriterionDecision(
                            organization_id=call.organization_id,
                            analysis_id=run.id,
                            **decision,
                        )
                    )
            role_by_speaker = {r["speaker_label"]: r for r in intake_data.get("speaker_roles", [])}
            active_segments = session.scalars(
                select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript_id)
            ).all()
            for segment in active_segments:
                proposed = role_by_speaker.get(segment.speaker_label)
                if proposed:
                    segment.speaker_role = proposed["role"]
                    segment.role_uncertain = proposed["uncertain"]
            call.active_analysis_id = run.id
            call.primary_type = call.type_override or intake_data["primary_type"]
            call.outcome = call.outcome_override or str(intake_data["outcome"].get("kind") or "unknown")[:40]
            call.analysis_status = run.state
            call.processing_status = "complete"
            call.review_status = "needs_review" if run.state == "needs_review" else "unreviewed"
            call.processed_at = datetime.now(UTC)
            step.result_id = run.id
            step.state = "complete"
            step.lease_owner = None
            step.lease_until = None
            from call_intelligence_api.crm_automation import apply_analysis_automation

            apply_analysis_automation(session, call, run)
            return run.id
    except Exception as exc:
        _fail(call_id, generation, "analysis", fence, owner, failure_code(exc))
        raise


def process_call(call_id: UUID, generation: int) -> None:
    from call_intelligence_api.indexing import indexing_stage

    transcribe_stage(call_id, generation)
    errors = []
    for stage in (analysis_stage, indexing_stage):
        try:
            stage(call_id, generation)
        except Exception as exc:  # noqa: BLE001  # run the independent indexing branch after analysis failure
            errors.append(exc)
    if errors:
        raise errors[0]
    with Session(domain_engine()) as session, session.begin():
        call = session.scalar(select(CallRecord).where(CallRecord.id == call_id).with_for_update())
        if call and call.deletion_status == "active" and call.requested_generation == generation:
            call.processing_status = "complete"
        event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.call_id == call_id,
                OutboxEvent.generation == generation,
                OutboxEvent.kind == "process_call",
            )
        )
        if event:
            event.state = "complete"
            event.dispatched_at = datetime.now(UTC)
