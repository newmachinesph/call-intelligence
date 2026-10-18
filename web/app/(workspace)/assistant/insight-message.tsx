import Link from "next/link";

import type { components } from "@/contracts/api";
import { clock, when, words } from "@/app/(workspace)/sales/pipeline-format";

type Message = components["schemas"]["InsightMessageResponse"];
export type Coverage = { eligible_calls?: number; indexed_calls?: number; inspected_calls?: number; retrieved_chunks?: number; exhaustive?: boolean };
type Aggregate = { definition?: string; counts?: { label: string; count: number }[]; read_at?: string };
type Citation = { call_id?: string; call_label?: string | null; start_seconds?: number; quote?: string };

const STATUS: Record<string, string> = {
  supported: "Supported by cited call evidence",
  contradicted: "Call evidence contradicts this",
  conflicting: "Call evidence conflicts",
  insufficient: "Not enough verified evidence to answer",
};

export function InsightMessage({ message, coverage }: { message: Message; coverage?: Coverage }) {
  if (message.role === "user") return <article className="chat-message chat-user"><span>You</span><p>{message.text}</p></article>;
  const aggregate = message.aggregate as Aggregate | null;
  const citations = message.citations as Citation[];
  return <article className="chat-message">
    <span>Assistant{message.stale ? " · source changed" : ""}</span>
    {message.stale && <p className="wf-warn">A cited call&apos;s transcript changed after this answer. Ask again for a current answer.</p>}
    <p>{message.text}</p>
    {message.evidence_status && <p className="wf-meta">Evidence: {STATUS[message.evidence_status] ?? words(message.evidence_status)}</p>}
    {coverage && <p className="wf-meta">Searched {coverage.inspected_calls ?? 0} of {coverage.indexed_calls ?? 0} indexed calls ({coverage.eligible_calls ?? 0} eligible), {coverage.retrieved_chunks ?? 0} excerpts — {coverage.exhaustive ? "exhaustive" : "not exhaustive"}.</p>}
    {aggregate?.counts && <div className="table-scroll"><table className="wf-table">
      <caption>{aggregate.definition ?? "Exact count from call records"}</caption>
      <thead><tr><th scope="col">Group</th><th scope="col">Calls</th></tr></thead>
      <tbody>{aggregate.counts.length ? aggregate.counts.map((row) => <tr key={row.label}><td>{words(row.label)}</td><td>{row.count}</td></tr>) : <tr><td colSpan={2}>No matching calls</td></tr>}</tbody>
    </table><p className="wf-meta">Counted from records at {when(aggregate.read_at, "an unrecorded time")}.</p></div>}
    {citations.length > 0 && <div className="wf-chips" aria-label="Cited call moments">{citations.map((citation, index) => citation.call_id &&
      <Link key={index} className="citation-link" title={citation.quote} href={`/sales/calls/${citation.call_id}?t=${Math.floor(Number(citation.start_seconds) || 0)}`}>
        {(citation.call_label ?? "Call").toUpperCase()} · {clock(citation.start_seconds)}
      </Link>)}</div>}
  </article>;
}
