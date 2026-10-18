"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { api, clock, failure, words, type Detail, type Segment, type Transcript } from "./call-utils";
import { Drawer } from "./drawer";

export type SegmentAction = { kind: "edit" | "follow-up"; segment: Segment };
const ROLES = ["employee", "customer", "other", "unknown"] as const;

// Transcript correction and local follow-up proposal for one selected turn. Nothing here sends a message.
export function SegmentDrawer({ action, detail, transcript, onClose }: { action: SegmentAction; detail: Detail; transcript: Transcript; onClose: () => void }) {
  const router = useRouter();
  const { segment } = action;
  const [text, setText] = useState(segment.text);
  const [role, setRole] = useState(segment.speaker_role);
  const [reason, setReason] = useState("");
  const [task, setTask] = useState("");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [done, setDone] = useState(false);
  const edit = action.kind === "edit";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      if (edit) {
        const change = { segment_id: segment.id, text: text.trim() !== segment.text ? text.trim() : null, speaker_role: role !== segment.speaker_role ? role : null };
        if (!change.text && !change.speaker_role) { setMessage("Change the wording or the speaker first."); return; }
        await api(`/calls/${detail.call.id}/transcript-revisions`, "POST", { base_version: transcript.version, reason: reason.trim(), changes: [change] });
        setMessage(`Saved as transcript version ${transcript.version + 1}. The original is preserved; analysis, scorecard and chat refresh from the new version.`);
      } else {
        await api(`/calls/${detail.call.id}/follow-ups`, "POST", {
          expected_generation: detail.call.requested_generation, proposed_action: task.trim(), source_segment_ids: [segment.id], due_text: due.trim() || null,
        });
        setMessage("Proposed as a local follow-up. Approve & assign it on Tasks. No message is sent to the customer.");
      }
      setDone(true);
      router.refresh();
    } catch (error) { setMessage(failure(error)); }
    finally { setBusy(false); }
  }

  return (
    <Drawer title={edit ? "Correct transcript turn" : "Propose follow-up"} onClose={onClose}>
      <form className="cd-form" onSubmit={submit}>
        <blockquote className="cd-quote"><strong>{clock(segment.start_seconds)} · {words(segment.speaker_role)}</strong> {segment.text}</blockquote>
        {edit ? <>
          <p className="cd-hint">Listen to the original audio first. Saving creates transcript version {transcript.version + 1}; version {transcript.version} stays preserved and dependent outputs (analysis, scorecard, chat index) refresh.</p>
          <label>Corrected wording<textarea value={text} onChange={(event) => setText(event.target.value)} rows={4} maxLength={4000} required /></label>
          <label>Speaker<select value={role} onChange={(event) => setRole(event.target.value)}>{ROLES.map((item) => <option key={item} value={item}>{words(item)}</option>)}</select></label>
          <label>Reason for correction (required)<input value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={1000} /></label>
        </> : <>
          <p className="cd-hint">Creates a local task pending approval, linked to this turn. It does not contact the customer.</p>
          <label>Action<textarea value={task} onChange={(event) => setTask(event.target.value)} rows={3} required minLength={3} maxLength={2000} /></label>
          <label>Due wording, if spoken<input value={due} onChange={(event) => setDue(event.target.value)} maxLength={255} placeholder="Leave empty if not stated" /></label>
        </>}
        <div className="cd-row">
          {!done && <button type="submit" className="primary-button" disabled={busy}>{busy ? "Saving…" : edit ? "Create new version" : "Propose follow-up"}</button>}
          <button type="button" className="text-button" onClick={onClose}>{done ? "Close" : "Cancel"}</button>
          {done && !edit && <Link className="text-button" href="/tasks">Open Tasks</Link>}
        </div>
        <p className="form-message" role="status">{message}</p>
      </form>
    </Drawer>
  );
}
