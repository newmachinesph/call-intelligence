"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import type { components } from "@/contracts/api";
import { clock, failure, send, when, words } from "@/app/(workspace)/sales/pipeline-format";
import { Dialog, useLoad } from "@/app/(workspace)/sales/pipeline-shared";

type Proposal = components["schemas"]["ProposalResponse"];
type Retention = components["schemas"]["RetentionCaseResponse"];
type Ref = { call_id?: string; start_seconds?: number; source_kind?: string; new_outcome?: string; reason?: string; reviewed_at?: string };

const evidence = (refs: Ref[]) => refs.filter((ref) => ref.source_kind !== "review" && ref.call_id && typeof ref.start_seconds === "number");

function ProposalItem({ proposal, onDone }: { proposal: Proposal; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [state, setState] = useState({ busy: false, message: "" });
  async function act(action: "apply" | "dismiss") {
    if (reason.trim().length < 3) { setState({ busy: false, message: "Enter a reason of at least 3 characters." }); return; }
    setState({ busy: true, message: "" });
    try {
      await send(`/crm/proposals/${proposal.id}/${action}`, action === "apply" ? { expected_version: proposal.opportunity_version, reason } : { reason });
      onDone();
    } catch (error) { setState({ busy: false, message: failure(error) }); }
  }
  const refs = evidence(proposal.source_refs as Ref[]);
  return <li className="wf-row">
    <div className="wf-row-main">
      <strong>{proposal.contact_name ?? "Unnamed contact"}: {proposal.current_stage} → {proposal.target_stage}</strong>
      <p>{proposal.reason}</p>
      <p className="wf-row-meta">
        {proposal.call_id && <Link href={`/sales/calls/${proposal.call_id}`}>{proposal.call_label ?? "Source call"}</Link>}
        {refs.map((ref, index) => <Link key={index} className="citation-link" href={`/sales/calls/${ref.call_id}?t=${Math.floor(ref.start_seconds ?? 0)}`}>Evidence {clock(ref.start_seconds)}</Link>)}
        <span suppressHydrationWarning>Call time: {when(proposal.effective_call_at, "unknown")}</span>
        <span suppressHydrationWarning>Recorded: {when(proposal.recorded_at)}</span>
      </p>
    </div>
    <div className="wf-form wf-inline">
      <label>Reason<input value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={1000} required /></label>
      <div className="row-actions">
        <button type="button" className="primary-button" disabled={state.busy} onClick={() => act("apply")}>Apply</button>
        <button type="button" className="wf-secondary" disabled={state.busy} onClick={() => act("dismiss")}>Dismiss</button>
      </div>
      {state.message && <p className="wf-error" role="alert">{state.message}</p>}
    </div>
  </li>;
}

export function ProposalQueue() {
  const router = useRouter();
  const { data, error, reload } = useLoad<components["schemas"]["ProposalListResponse"]>("/crm/proposals?limit=50");
  const items = data?.items.filter((item) => item.pending) ?? [];
  return <section className="surface wf-card" aria-labelledby="proposals-heading">
    <div className="wf-card-head"><h2 id="proposals-heading">Proposed changes</h2>
      <p className="wf-meta">Automation proposes instead of changing a stage when evidence, chronology or a lock needs review.</p></div>
    {error && <p className="wf-error" role="alert">{error}</p>}
    {!data ? !error && <p className="wf-meta">Loading proposals…</p>
      : items.length ? <ul className="wf-rows">{items.map((item) => <ProposalItem key={item.id} proposal={item} onDone={() => { reload(); router.refresh(); }} />)}</ul>
      : <p className="wf-empty">No proposals waiting for review.</p>}
  </section>;
}

export function RetentionTable() {
  const { data, error, reload } = useLoad<components["schemas"]["RetentionCaseListResponse"]>("/crm/retention-cases?limit=50");
  const [active, setActive] = useState<Retention | null>(null);
  const [outcome, setOutcome] = useState<components["schemas"]["RetentionUpdate"]["outcome"]>("open");
  const [reason, setReason] = useState("");
  const [state, setState] = useState({ busy: false, message: "" });
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!active) return;
    setState({ busy: true, message: "" });
    try {
      await send(`/crm/retention-cases/${active.id}`, { expected_version: active.version, outcome, reason }, "PATCH");
      setActive(null); setState({ busy: false, message: "" }); reload();
    } catch (error) { setState({ busy: false, message: failure(error) }); }
  }
  return <section className="surface wf-card" aria-labelledby="retention-heading">
    <div className="wf-card-head"><h2 id="retention-heading">Retention cases</h2>
      <p className="wf-meta">Cancellation intent is tracked separately from sales stages. No account is closed here.</p></div>
    {error && <p className="wf-error" role="alert">{error}</p>}
    {!data ? !error && <p className="wf-meta">Loading retention cases…</p> : data.items.length ? <div className="table-scroll"><table className="wf-table">
      <thead><tr><th scope="col">Source call</th><th scope="col">Call date</th><th scope="col">Intent</th><th scope="col">Outcome</th><th scope="col"><span className="sr-only">Update</span></th></tr></thead>
      <tbody>{data.items.map((item) => {
        const review = (item.source_refs as Ref[]).filter((ref) => ref.source_kind === "review").at(-1);
        return <tr key={item.id}>
          <td><Link href={`/sales/calls/${item.call_id}`}>{item.call_label ?? "Source call"}</Link></td>
          <td suppressHydrationWarning>{when(item.call_at, "Unknown")}</td>
          <td>{words(item.intent)}</td>
          <td>{words(item.outcome)}{review && <small>Reviewed: {review.reason}</small>}</td>
          <td><button type="button" className="text-button" onClick={() => { setActive(item); setOutcome(item.outcome as typeof outcome); setReason(""); setState({ busy: false, message: "" }); }}>Update outcome</button></td>
        </tr>;
      })}</tbody>
    </table></div> : <p className="wf-empty">No retention cases. They are created when an analyzed call shows cancellation intent.</p>}
    <Dialog open={Boolean(active)} title="Update retention outcome" onClose={() => setActive(null)}>
      <form className="wf-form" onSubmit={submit}>
        <label>Reviewed outcome<select value={outcome} onChange={(event) => setOutcome(event.target.value as typeof outcome)}>
          {(["open", "retained", "cancellation_reported", "unknown"] as const).map((value) => <option key={value} value={value}>{words(value)}</option>)}</select></label>
        <label>Reason<input value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={1000} autoFocus /></label>
        <p className="wf-meta">The call&apos;s original conversational label stays visible in the call record.</p>
        {state.message && <p className="wf-error" role="alert">{state.message}</p>}
        <button className="primary-button" disabled={state.busy}>Save outcome</button>
      </form>
    </Dialog>
  </section>;
}
