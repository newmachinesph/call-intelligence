import Link from "next/link";
import { notFound } from "next/navigation";

import type { components } from "@/contracts/api";
import { CallTable } from "@/app/(workspace)/components/call-table";
import { BarList } from "@/app/(workspace)/components/charts";
import { humanize } from "@/app/(workspace)/components/format";
import { VerifyRep } from "@/app/(workspace)/components/verify-rep";
import { readError } from "@/lib/api-error";
import { domainFetch, domainGet } from "@/lib/domain";

type RepDetail = components["schemas"]["RepDetailResponse"];
type Me = components["schemas"]["MeResponse"];

const timeLabel = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

export default async function RepProfile({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) notFound();
  const [response, me] = await Promise.all([domainFetch(`/reps/${encodeURIComponent(id)}`), domainGet<Me>("/me")]);
  if (response.status === 404) notFound();
  if (!response.ok) throw new Error(await readError(response));
  const { rep, by_rubric, coaching_examples, calls } = (await response.json()) as RepDetail;

  return <div className="content-column">
    <Link className="back-link" href="/team">← Team</Link>
    <header className="page-heading"><p className="eyebrow">Rep profile</p><h1>{rep.display_name}</h1>
      <p className="detail-subtitle">{rep.verified ? "Verified identity" : "Identity not verified"} · Source: {humanize(rep.identity_source)} · {rep.analyzed_calls} analyzed calls · {rep.scored_calls} fully scored</p>
    </header>
    {me.role === "owner" && !rep.verified && <VerifyRep repId={rep.id} />}

    <section className="surface panel-card" aria-labelledby="rubric-heading">
      <div className="panel-card-heading"><h2 id="rubric-heading">Scores by rubric</h2><span>Rubrics are never averaged together</span></div>
      {by_rubric.length ? <div className="rubric-grid">
        {by_rubric.map((section) => <article key={section.rubric_id ?? "none"} className="rubric-card">
          <h3>{section.rubric_id ? `${humanize(section.rubric_id)} rubric` : "No rubric (ungraded)"}</h3>
          <p className="row-stats"><span>{section.scored_calls} of {section.calls} calls fully scored</span>
            <span>{section.points_possible ? `${section.points_earned}/${section.points_possible} points across scored calls` : "No scored points yet"}</span></p>
          <BarList title="Grade distribution" caption={`Of ${section.calls} calls on this rubric`} denominator={section.calls} empty="No grades yet."
            rows={section.grade_distribution.map((item) => ({ label: humanize(item.label), count: item.count }))} />
          <BarList title="Missed criteria" caption="Missed ÷ applicable, observable decisions" unit="decisions" empty="No missed criteria."
            rows={section.missed_criteria.map((item) => ({ label: humanize(item.label), count: item.count, denominator: item.denominator }))} />
        </article>)}
      </div> : <p className="panel-empty-text">No analyzed calls are assigned to this rep yet.</p>}
    </section>

    <section className="surface panel-card" aria-labelledby="coaching-heading">
      <div className="panel-card-heading"><h2 id="coaching-heading">Coaching examples</h2></div>
      {coaching_examples.length ? <ul className="inset-list">
        {coaching_examples.map((example, index) => <li key={`${example.call_id}-${index}`} className="inset-row">
          <div><strong>{example.observation}</strong><p>{example.criterion_ids.map((criterion) => humanize(criterion)).join(", ") || "General"}</p></div>
          <Link className="citation-link" href={`/sales/calls/${example.call_id}${example.start_seconds !== null ? `?t=${Math.floor(example.start_seconds)}` : ""}`}>
            {example.call_label}{example.start_seconds !== null ? ` at ${timeLabel(example.start_seconds)}` : ""}
          </Link>
        </li>)}
      </ul> : <p className="panel-empty-text">No coaching examples yet. They come from missed criteria with transcript evidence.</p>}
    </section>

    <section className="surface call-table-surface" aria-labelledby="rep-calls-heading">
      <div className="table-heading"><div><h2 id="rep-calls-heading">Calls</h2></div><Link href={`/sales?view=calls&rep_id=${rep.id}`}>Open in Call Log</Link></div>
      {calls.length ? <CallTable calls={calls} caption={`Calls assigned to ${rep.display_name}`} /> : <div className="table-empty"><p>No calls are assigned to this rep.</p></div>}
    </section>
  </div>;
}
