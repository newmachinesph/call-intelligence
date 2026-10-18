import Link from "next/link";

import type { components } from "@/contracts/api";
import { humanize } from "@/app/(workspace)/components/format";
import { domainGet } from "@/lib/domain";

type Reps = components["schemas"]["RepListResponse"];
type Overview = components["schemas"]["OverviewResponse"];

export default async function Team() {
  const [reps, overview] = await Promise.all([domainGet<Reps>("/reps?limit=100"), domainGet<Overview>("/analytics/overview")]);
  const unknown = overview.reps.find((rep) => rep.id === null);
  return <div className="content-column">
    <header className="page-heading"><p className="eyebrow">People and coverage</p><h1>Team</h1></header>
    <section className="surface panel-card" aria-labelledby="reps-heading">
      <div className="panel-card-heading"><h2 id="reps-heading">Rep profiles</h2><span>{reps.items.length} rep{reps.items.length === 1 ? "" : "s"}</span></div>
      {reps.items.length ? <ul className="inset-list">
        {reps.items.map((rep) => <li key={rep.id} className="inset-row">
          <div><Link className="row-title" href={`/team/${rep.id}`}>{rep.display_name}</Link>
            <p>{rep.verified ? "Verified identity" : "Identity not verified"} · Source: {humanize(rep.identity_source)}</p></div>
          <p className="row-stats"><span>{rep.analyzed_calls} analyzed</span><span>{rep.scored_calls} fully scored</span></p>
        </li>)}
      </ul> : <p className="panel-empty-text">No rep has been assigned or verified yet. Assign a rep from a call&apos;s review panel.</p>}
      <ul className="inset-list">
        <li className="inset-row">
          <div><Link className="row-title" href="/sales?view=calls&rep_id=unknown">Unknown rep</Link><p>Analyzed calls without an assigned or verified rep. Not a profile.</p></div>
          <p className="row-stats"><span>{unknown?.analyzed_calls ?? 0} analyzed</span><span>{unknown?.scored_calls ?? 0} fully scored</span></p>
        </li>
      </ul>
      <p className="panel-empty-text">Sample counts are shown so small samples are not over-read. Scores are compared per rubric on each profile, never averaged across rubrics.</p>
    </section>
  </div>;
}
