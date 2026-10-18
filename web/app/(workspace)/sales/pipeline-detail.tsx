"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import type { components } from "@/contracts/api";
import type { CallSummary, TranscriptResponse } from "@/lib/call-types";
import { clock, failure, send, when, words } from "@/app/(workspace)/sales/pipeline-format";
import { useLoad } from "@/app/(workspace)/sales/pipeline-shared";

type Opportunity = components["schemas"]["OpportunityResponse"];
type Detail = components["schemas"]["OpportunityDetailResponse"];
type Stage = components["schemas"]["OpportunityStage"];

// Mirrors the API's allowed manual transitions; Won/Lost reopen only through a reviewed proposal.
const NEXT: Record<string, Stage[]> = { New: ["Contacted"], Contacted: ["Follow-up", "Won", "Lost"], "Follow-up": ["Contacted", "Won", "Lost"], Won: [], Lost: [] };
const ruleText = (rule: string) => rule.startsWith("proposed:") ? `Proposed ${rule.slice(9)} (not applied)` : words(rule);

function LinkCall({ opportunity, calls, linked, onDone }: { opportunity: Opportunity; calls: CallSummary[]; linked: string[]; onDone: () => void }) {
  const [callId, setCallId] = useState("");
  const [reason, setReason] = useState("");
  const [state, setState] = useState({ busy: false, message: "" });
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setState({ busy: true, message: "" });
    try {
      await send("/crm/call-links", { call_id: callId, contact_id: opportunity.contact_id, opportunity_id: opportunity.id, reason, match_provenance: "reviewer_verified" });
      setCallId(""); setReason(""); setState({ busy: false, message: "Call linked. The call's current analysis was checked against the pipeline rules." }); onDone();
    } catch (error) { setState({ busy: false, message: failure(error) }); }
  }
  const options = calls.filter((call) => !linked.includes(call.id));
  return <form className="wf-form wf-inset" onSubmit={submit}>
    <h3>Link a call</h3>
    <label>Call<select value={callId} onChange={(event) => setCallId(event.target.value)} required>
      <option value="">Select a call</option>{options.map((call) => <option key={call.id} value={call.id}>{call.external_call_id ?? call.filename} · {words(call.primary_type, "type pending")}</option>)}</select></label>
    <label>Why this call belongs to this customer<input value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={1000} /></label>
    <p className="wf-meta">Recorded as reviewer verified.</p>
    <button className="primary-button" disabled={state.busy || !callId}>Link call</button>
    {state.message && <p className="wf-meta" role="status">{state.message}</p>}
  </form>;
}

function Transition({ opportunity, detail, onDone }: { opportunity: Opportunity; detail: Detail; onDone: () => void }) {
  const options = NEXT[opportunity.stage] ?? [];
  const [target, setTarget] = useState<Stage | "">(options[0] ?? "");
  const [callId, setCallId] = useState("");
  const [segmentId, setSegmentId] = useState("");
  const [lock, setLock] = useState(opportunity.manual_lock);
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [state, setState] = useState({ busy: false, message: "" });
  const terminal = target === "Won" || target === "Lost";
  const transcript = useLoad<TranscriptResponse>(terminal && callId ? `/calls/${callId}/transcript` : null);
  if (!options.length) return <div className="wf-inset"><h3>Stage</h3><p className="wf-meta">{opportunity.stage} is final here. Reopening needs a reviewed proposal from a later call.</p></div>;
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setState({ busy: true, message: "" });
    try {
      await send(`/crm/opportunities/${opportunity.id}/transitions`, { expected_version: opportunity.version, target_stage: target, reason, automation_lock: lock,
        source_call_id: terminal ? callId : null, source_segment_ids: terminal ? [segmentId] : [] }, "POST", key);
      setKey(crypto.randomUUID()); setState({ busy: false, message: "Local stage saved with an audit event." }); onDone();
    } catch (error) { setState({ busy: false, message: failure(error) }); }
  }
  return <form className="wf-form wf-inset" onSubmit={submit}>
    <h3>Change local stage</h3>
    <label>New stage<select value={target} onChange={(event) => setTarget(event.target.value as Stage)}>{options.map((stage) => <option key={stage}>{stage}</option>)}</select></label>
    {terminal && <>
      <label>Linked source call<select value={callId} required onChange={(event) => { setCallId(event.target.value); setSegmentId(""); }}>
        <option value="">Select a linked call</option>
        {detail.linked_calls.map((call) => <option key={call.call_id} value={call.call_id} disabled={!call.call_at}>{call.call_label}{call.call_at ? "" : " (call date unknown, not eligible)"}</option>)}</select></label>
      {!detail.linked_calls.length && <p className="wf-meta">Link the call where the customer decided before recording {target}.</p>}
      <label>Evidence segment<select value={segmentId} required onChange={(event) => setSegmentId(event.target.value)} disabled={!transcript.data}>
        <option value="">{callId && !transcript.data && !transcript.error ? "Loading transcript…" : "Select the deciding statement"}</option>
        {transcript.data?.segments.map((segment) => <option key={segment.id} value={segment.id}>{clock(segment.start_seconds)} · {segment.text.slice(0, 70)}</option>)}</select></label>
      {transcript.error && <p className="wf-error">{transcript.error}</p>}
    </>}
    <label className="wf-check"><input type="checkbox" checked={lock} onChange={(event) => setLock(event.target.checked)} /> Lock automation after this change (analysis can then only propose)</label>
    <label>Reason<input value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={1000} /></label>
    <button className="primary-button" disabled={state.busy || !target || (terminal && !segmentId)}>Save local stage</button>
    {state.message && <p className="wf-meta" role="status">{state.message}</p>}
  </form>;
}

export function OpportunityDetail({ opportunity, calls, onClose }: { opportunity: Opportunity; calls: CallSummary[]; onClose: () => void }) {
  const router = useRouter();
  const { data, error, reload } = useLoad<Detail>(`/crm/opportunities/${opportunity.id}`);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const done = () => { reload(); router.refresh(); };
  return <section className="surface wf-card" aria-labelledby="opportunity-heading">
    <div className="wf-card-head">
      <div><p className="status-label">Opportunity</p>
        <h2 id="opportunity-heading" ref={heading} tabIndex={-1}>{opportunity.contact_name ?? "Unnamed contact"} · {opportunity.stage}</h2>
        <p className="wf-meta">{opportunity.service_interest ?? "Service not specified"} · {opportunity.manual_lock ? "Automation locked" : "Automation on"} · <Link href={`/sales/contacts/${opportunity.contact_id}`}>Contact record</Link></p></div>
      <button type="button" className="text-button" onClick={onClose}>Close</button>
    </div>
    {error && <p className="wf-error" role="alert">{error}</p>}
    {!data ? !error && <p className="wf-meta">Loading opportunity…</p> : <div className="wf-detail-grid">
      <div>
        <h3>Timeline</h3>
        {data.events.length ? <ol className="wf-timeline">{data.events.map((event) => <li key={event.id}>
          <strong>{event.prior_stage} → {event.new_stage}</strong> <span className="status-pill">{ruleText(event.rule)}</span>
          <p>{event.reason}</p>
          <small suppressHydrationWarning>Call time: {when(event.effective_call_at, "unknown")} · Recorded: {when(event.recorded_at)}</small>
        </li>)}</ol> : <p className="wf-meta">No stage changes yet.</p>}
        <h3>Linked calls</h3>
        {data.linked_calls.length ? <ul className="wf-list">{data.linked_calls.map((call) => <li key={call.call_id}>
          <Link href={`/sales/calls/${call.call_id}`}>{call.call_label}</Link> · {words(call.primary_type, "type pending")} · {words(call.outcome, "outcome unknown")} · <span suppressHydrationWarning>{when(call.call_at, "call date unknown")}</span>
        </li>)}</ul> : <p className="wf-meta">No calls linked.</p>}
        <h3>Tasks</h3>
        {data.tasks.length ? <ul className="wf-list">{data.tasks.map((task) => <li key={task.id}>{task.proposed_action} · {words(task.status)}</li>)}</ul>
          : <p className="wf-meta">No follow-ups for this opportunity.</p>}
        {data.tasks.length > 0 && <Link href="/tasks">Manage in Tasks</Link>}
      </div>
      <div className="wf-stack">
        <Transition key={opportunity.version} opportunity={opportunity} detail={data} onDone={done} />
        <LinkCall opportunity={opportunity} calls={calls} linked={data.linked_calls.map((call) => call.call_id)} onDone={done} />
      </div>
    </div>}
  </section>;
}
