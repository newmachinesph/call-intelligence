import Link from "next/link";

import type { components } from "@/contracts/api";
import { FollowUpsDue, MetricCards } from "@/app/(workspace)/components/overview-parts";
import { domainGet } from "@/lib/domain";

type Overview = components["schemas"]["OverviewResponse"];

export default async function Home() {
  const data = await domainGet<Overview>("/analytics/overview");
  const links = [
    { href: "/sales?view=calls", label: "All calls", count: data.uploaded_calls },
    { href: "/sales?view=calls&processing_status=uploaded", label: "Waiting to be processed", count: data.awaiting_processing_calls },
    { href: "/sales?view=calls&analysis_status=needs_review", label: "Need review", count: data.needs_review_calls },
    { href: "/sales?view=calls&date_known=false", label: "Unknown call date", count: data.unknown_date_calls },
    { href: "/sales?view=calls&rep_id=unknown", label: "Unknown rep", count: data.reps.find((rep) => rep.id === null)?.analyzed_calls ?? 0 },
  ];
  return <div className="content-column">
    <header className="page-heading"><p className="eyebrow">Owner overview</p><h1>Home</h1></header>
    <MetricCards metrics={data.metrics} />
    <div className="overview-panels">
      <section className="surface panel-card" aria-labelledby="call-log-links">
        <div className="panel-card-heading"><h2 id="call-log-links">Call Log</h2><Link href="/sales">Sales overview</Link></div>
        <ul className="inset-list">
          {links.map((link) => <li key={link.href} className="inset-row"><Link href={link.href}>{link.label}</Link><strong>{link.count}</strong></li>)}
        </ul>
        <p className="panel-empty-text">{data.processing_calls} call{data.processing_calls === 1 ? " is" : "s are"} processing now. Unknown-rep count covers analyzed calls only.</p>
      </section>
      <FollowUpsDue items={data.follow_ups_due} />
    </div>
  </div>;
}
