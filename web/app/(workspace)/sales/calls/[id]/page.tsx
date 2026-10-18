import Link from "next/link";
import { notFound } from "next/navigation";

import { CallInspector } from "./call-inspector";
import { words, type Detail, type Schemas, type Transcript } from "./call-utils";
import { domainFetch } from "@/lib/domain";
import "./call-detail.css";

async function load<T>(path: string): Promise<T | null> {
  const response = await domainFetch(path);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Call service returned ${response.status}`);
  return response.json() as Promise<T>;
}

function Reviewed({ on }: { on: boolean }) {
  return on ? <span className="cd-badge">Reviewed</span> : null;
}

export default async function CallDetail({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { id } = await params;
  const { t } = await searchParams;
  const detail = await load<Detail>(`/calls/${encodeURIComponent(id)}`);
  if (!detail) notFound();
  const [transcript, me] = await Promise.all([
    detail.transcript_version ? load<Transcript>(`/calls/${encodeURIComponent(id)}/transcript`) : null,
    load<Schemas["MeResponse"]>("/me").catch(() => null),
  ]);
  const initialSeconds = typeof t === "string" && /^\d+(\.\d+)?$/.test(t) ? Number(t) : null;
  const { call, analysis } = detail;
  const duration = call.duration_seconds === null ? "Duration unknown" : `${Math.floor(call.duration_seconds / 60)} min ${Math.round(call.duration_seconds % 60)} s`;
  const score = call.confirmed_points === null ? "Not scored"
    : `${call.confirmed_points} / ${call.nominal_maximum ?? "—"} · ${call.grade ? words(call.grade) : `provisional, ${call.unresolved_count ?? 0} unresolved`}`;

  return (
    <div className="content-column cd-page">
      <Link className="back-link" href="/sales?view=calls">← Call Log</Link>
      <header className="page-heading">
        <p className="eyebrow">Call detail</p>
        <h1>{call.external_call_id ?? call.filename}</h1>
        <p className="detail-subtitle">{call.filename} · {duration} · {call.call_at ? `Call time ${new Date(call.call_at).toLocaleString()}` : "Call time unknown"} · Uploaded {new Date(call.uploaded_at).toLocaleDateString()}</p>
      </header>
      <dl className="cd-facts-bar">
        <div><dt>Type</dt><dd>{words(call.primary_type, "Unclassified")} <Reviewed on={call.type_reviewed} /></dd></div>
        <div><dt>Outcome</dt><dd>{words(call.outcome)} <Reviewed on={call.outcome_reviewed} /></dd></div>
        <div><dt>Rep</dt><dd>{call.rep_name ?? "Unknown rep"}</dd></div>
        <div><dt>Score</dt><dd>{score}</dd></div>
        <div><dt>CRM</dt><dd>{detail.crm_links.length ? detail.crm_links.map((link) => (
          <span key={`${link.contact_id}-${link.opportunity_id}`} className="cd-crm">
            <Link href={`/sales/contacts/${link.contact_id}`}>{link.contact_name ?? "Unnamed contact"}</Link>
            {link.opportunity_id ? ` · opportunity ${words(link.opportunity_stage, "stage unknown")}` : " · no opportunity"}
          </span>)) : "Not linked to a contact"}</dd></div>
      </dl>
      <section className="surface cd-card" aria-labelledby="cd-summary-heading">
        <p className="status-label">Summary</p>
        <h2 id="cd-summary-heading" className="cd-summary">{analysis?.summary ?? "No analysis yet. Processing status is shown below."}</h2>
        {detail.analysis_stale && <p className="cd-updating">Updating — showing previous analysis</p>}
        {analysis?.needs_review.length ? <p className="cd-warn">Needs review: {analysis.needs_review.join("; ")}</p> : null}
        {analysis?.facts.length ? <>
          <h3 className="cd-subhead">Facts extracted from this call</h3>
          <dl className="cd-fact-grid">{analysis.facts.map((fact, index) => (
            <div key={index}><dt>{words(String(fact.field ?? "fact"))}</dt><dd>{fact.value == null ? "Unknown" : String(fact.value)}</dd><dd className="cd-hint">{fact.status === "observed" ? "Observed" : "Uncertain"}</dd></div>
          ))}</dl>
          <p className="cd-hint">AI-extracted. Check against the recording before using in customer records.</p>
        </> : null}
      </section>
      <CallInspector detail={detail} transcript={transcript} isOwner={me?.role === "owner"} initialSeconds={initialSeconds} />
    </div>
  );
}
