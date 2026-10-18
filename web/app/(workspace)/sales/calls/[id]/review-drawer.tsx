"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { api, ApiError, clock, failure, words, type Detail, type Schemas, type Transcript } from "./call-utils";
import { Drawer } from "./drawer";

type Rep = { id: string; display_name: string; verified: boolean };
type CriterionState = Schemas["CriterionChange"]["state"];
const TYPES = ["sales", "retention", "customer_service", "other"] as const;
// Outcome labels the shared metrics recognise; the current and model values are always added.
const OUTCOMES = ["booked", "agreed", "declined", "follow_up", "retained", "cancellation_reported", "open", "unknown"];

export function ReviewDrawer({ detail, transcript, onClose }: { detail: Detail; transcript: Transcript | null; onClose: () => void }) {
  const router = useRouter();
  const call = detail.call;
  const analysis = detail.analysis!;
  const [type, setType] = useState(call.primary_type ?? "");
  const [outcome, setOutcome] = useState(call.outcome ?? "");
  const [reps, setReps] = useState<Rep[]>([]);
  const [repId, setRepId] = useState(call.rep_id ?? "");
  const [newRep, setNewRep] = useState("");
  const [criterionId, setCriterionId] = useState("");
  const [state, setState] = useState<CriterionState>("met");
  const [segmentId, setSegmentId] = useState("");
  const [rationale, setRationale] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);

  useEffect(() => {
    api<Schemas["RepListResponse"]>("/reps?limit=100").then(({ data }) => setReps(data.items))
      .catch((error) => setMessage(`Reps could not load: ${failure(error)}`));
  }, []);

  const typeChanged = !!type && type !== call.primary_type;
  const outcomes = [...new Set([call.outcome, detail.model_outcome, ...OUTCOMES].filter((value): value is string => !!value))];
  const criterion = analysis.criteria.find((item) => item.criterion_id === criterionId);

  async function createRep() {
    setBusy(true); setMessage("");
    try {
      const { data } = await api<Schemas["RepResponse"]>("/reps", "POST", { display_name: newRep.trim() });
      setReps((items) => [...items, data]); setRepId(data.id); setNewRep("");
      setMessage(`Rep “${data.display_name}” created and selected. Save the review to assign it.`);
    } catch (error) { setMessage(failure(error)); }
    finally { setBusy(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const changes: Schemas["ReviewChanges"] = {};
    if (typeChanged) changes.primary_type = type as (typeof TYPES)[number];
    if (outcome && outcome !== call.outcome) changes.outcome = outcome;
    if (repId !== (call.rep_id ?? "")) changes.rep_id = repId || null;
    if (criterionId && !typeChanged) changes.criteria = [{
      criterion_id: criterionId, state, rationale: rationale.trim(),
      evidence_segment_ids: segmentId && (state === "met" || state === "missed") ? [segmentId] : [],
    }];
    if (!Object.keys(changes).length) { setMessage("Change at least one field before saving."); return; }
    setBusy(true); setMessage(""); setConflict(false);
    try {
      const { status } = await api(`/calls/${call.id}/review`, "POST", {
        expected_review_version: analysis.review_version, expected_generation: call.requested_generation, reason: reason.trim(), changes,
      });
      router.refresh();
      if (status === 202) setMessage("Call type changed. Re-analysis is queued; the page shows the previous analysis as updating until it finishes.");
      else onClose();
    } catch (error) {
      setConflict(error instanceof ApiError && error.status === 409);
      setMessage(failure(error));
    } finally { setBusy(false); }
  }

  return (
    <Drawer title="Review this call" onClose={onClose}>
      <form className="cd-form" onSubmit={submit}>
        <p className="form-message">Changes are saved as reviewed values with your reason. The original model output stays inspectable.</p>
        <fieldset>
          <legend>Call type</legend>
          <label>Type<select value={type} onChange={(event) => setType(event.target.value)}>
            {!call.primary_type && <option value="">Unclassified</option>}
            {TYPES.map((value) => <option key={value} value={value}>{words(value)}</option>)}
          </select></label>
          <p className="cd-hint">Model original: {words(detail.model_primary_type)}{call.type_reviewed ? " · currently reviewed" : ""}</p>
          {typeChanged && <p className="cd-hint cd-warn">Changing the type re-runs analysis with the matching rubric. Criterion changes are disabled for this save.</p>}
        </fieldset>
        <fieldset>
          <legend>Outcome</legend>
          <label>Outcome<select value={outcome} onChange={(event) => setOutcome(event.target.value)}>
            {!call.outcome && <option value="">Unknown</option>}
            {outcomes.map((value) => <option key={value} value={value}>{words(value)}</option>)}
          </select></label>
          <p className="cd-hint">Model original: {words(detail.model_outcome)}{call.outcome_reviewed ? " · currently reviewed" : ""}</p>
        </fieldset>
        <fieldset>
          <legend>Rep</legend>
          <label>Assigned rep<select value={repId} onChange={(event) => setRepId(event.target.value)}>
            <option value="">Unknown rep</option>
            {call.rep_id && !reps.some((rep) => rep.id === call.rep_id) && <option value={call.rep_id}>{call.rep_name ?? "Current rep"}</option>}
            {reps.map((rep) => <option key={rep.id} value={rep.id}>{rep.display_name}{rep.verified ? " · verified" : ""}</option>)}
          </select></label>
          <div className="cd-inline">
            <label>New rep name<input value={newRep} onChange={(event) => setNewRep(event.target.value)} maxLength={200} /></label>
            <button type="button" className="text-button" disabled={busy || newRep.trim().length < 2} onClick={() => void createRep()}>Create rep</button>
          </div>
        </fieldset>
        <fieldset disabled={typeChanged}>
          <legend>Criterion</legend>
          <label>Criterion<select value={criterionId} onChange={(event) => { setCriterionId(event.target.value); setRationale(""); }}>
            <option value="">No criterion change</option>
            {analysis.criteria.map((item) => <option key={item.criterion_id} value={item.criterion_id}>{words(item.criterion_id.split(".").at(-1))} · now {words(item.state)}</option>)}
          </select></label>
          {criterion && <>
            <p className="cd-hint">Current rationale: {criterion.rationale}</p>
            <label>Assessment<select value={state} onChange={(event) => setState(event.target.value as CriterionState)}>
              <option value="met">Met</option><option value="missed">Missed</option>
              <option value="not_applicable">Not applicable</option><option value="not_observable">Not observable</option>
            </select></label>
            <label>Evidence segment<select value={segmentId} onChange={(event) => setSegmentId(event.target.value)} disabled={state !== "met" && state !== "missed"}>
              <option value="">No segment</option>
              {transcript?.segments.map((item) => <option key={item.id} value={item.id}>{clock(item.start_seconds)} · {item.text.slice(0, 60)}</option>)}
            </select></label>
            <label>Rationale<textarea value={rationale} onChange={(event) => setRationale(event.target.value)} required minLength={3} maxLength={2000} rows={3} /></label>
          </>}
        </fieldset>
        <label>Reason for this review (required)<input value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={1000} /></label>
        <div className="cd-row">
          <button type="submit" className="primary-button" disabled={busy}>{busy ? "Saving…" : "Save review"}</button>
          <button type="button" className="text-button" onClick={onClose}>Cancel</button>
          {conflict && <button type="button" className="text-button" onClick={() => { router.refresh(); onClose(); }}>Refresh call</button>}
        </div>
        <p className="form-message" role="status">{message}</p>
      </form>
    </Drawer>
  );
}
