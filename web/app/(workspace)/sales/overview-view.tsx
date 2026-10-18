import Link from "next/link";

import type { components } from "@/contracts/api";
import { ActivityColumns, BarList } from "@/app/(workspace)/components/charts";
import { humanize, percent } from "@/app/(workspace)/components/format";
import { AttentionRows, FollowUpsDue, MetricDetails } from "@/app/(workspace)/components/overview-parts";
import { domainGet } from "@/lib/domain";

type Overview = components["schemas"]["OverviewResponse"];
type CallList = components["schemas"]["CallListResponse"];
type RateMetric = components["schemas"]["RateMetric"];

const calls = (params: string) => `/sales?view=calls&${params}`;
// Rubric -> call type used for Call Log links (office rubric scores customer-service calls).
const rubricType: Record<string, string> = { sales: "sales", retention: "retention", office: "customer_service" };

function OutcomeCard({ title, type, data, metric }: { title: string; type: string; data: Overview; metric?: RateMetric }) {
  return (
    <section className="surface metric-card" aria-labelledby={`${type}-outcomes`}>
      <span id={`${type}-outcomes`}>{title}</span>
      <strong><Link href={calls(`primary_type=${type}`)}>{data.analyzed_calls}<span className="sr-only"> analyzed {type} calls</span></Link></strong>
      <small>analyzed {humanize(type).toLowerCase()} calls</small>
      {data.by_outcome.length > 0 && <ul className="outcome-list">
        {data.by_outcome.map((item) => <li key={item.label}><Link href={calls(`primary_type=${type}&outcome=${encodeURIComponent(item.label)}`)}>{humanize(item.label)}</Link><span>{item.count}</span></li>)}
      </ul>}
      {metric && <>
        <p className="metric-rate">{metric.label}: <b>{percent(metric.value) ?? "No reviewed rate yet"}</b></p>
        <MetricDetails metric={metric} />
      </>}
    </section>
  );
}

export async function OverviewView() {
  const [all, sales, retention, failed] = await Promise.all([
    domainGet<Overview>("/analytics/overview"),
    domainGet<Overview>("/analytics/overview?primary_type=sales"),
    domainGet<Overview>("/analytics/overview?primary_type=retention"),
    domainGet<CallList>("/calls?processing_status=failed&limit=100"),
  ]);
  const metric = (key: string) => all.metrics.find((item) => item.key === key);
  const analyzedShare = metric("analyzed_share");
  const reviewShare = metric("needs_review_share");

  return <>
    <div className="metric-grid overview-cards">
      <section className="surface metric-card" aria-labelledby="analyzed-card">
        <span id="analyzed-card">Analyzed calls</span>
        <strong><Link href="/sales?view=calls">{all.analyzed_calls}<span className="sr-only"> analyzed calls</span></Link></strong>
        {analyzedShare && <MetricDetails metric={analyzedShare} />}
        <small>{all.processing_calls} not finished processing</small>
      </section>
      <section className="surface metric-card" aria-labelledby="review-card">
        <span id="review-card">Needs review</span>
        <strong><Link href={calls("analysis_status=needs_review")}>{all.needs_review_calls}<span className="sr-only"> calls need review</span></Link></strong>
        {reviewShare && <MetricDetails metric={reviewShare} />}
      </section>
      <OutcomeCard title="Sales outcomes" type="sales" data={sales} metric={metric("reviewed_conversion")} />
      <OutcomeCard title="Retention outcomes" type="retention" data={retention} metric={metric("reviewed_save_rate")} />
    </div>

    <div className="overview-panels">
      <AttentionRows rows={[
        { count: all.needs_review_calls, title: "calls need review", detail: "The current analysis flagged a fact or criterion for a reviewer.", href: calls("analysis_status=needs_review"), action: "Review calls" },
        { count: all.pending_approval_follow_ups, title: "follow-ups waiting approval", detail: "Proposed from call commitments. Nothing is sent automatically.", href: "/tasks?status=pending_approval", action: "Approve" },
        { count: all.unassigned_follow_ups, title: "follow-ups without an owner", detail: "Open local tasks that nobody is assigned to yet.", href: "/tasks?owner=unassigned", action: "Assign" },
        { count: failed.items.length, title: "calls failed processing", detail: "Open a call to see the failed stage and retry it.", href: calls("processing_status=failed"), action: "Retry" },
      ]} />
      <FollowUpsDue items={all.follow_ups_due} />
    </div>

    <section className="surface panel-card" aria-labelledby="patterns-heading">
      <div className="panel-card-heading"><h2 id="patterns-heading">Call Patterns</h2><span>Current analyses only</span></div>
      <div className="chart-grid">
        <BarList title="Call type" caption={`Share of ${all.analyzed_calls} analyzed calls`} denominator={all.analyzed_calls} empty="No analyzed calls yet."
          rows={all.by_type.map((item) => ({ label: humanize(item.label), count: item.count, href: calls(`primary_type=${item.label}`) }))} />
        <BarList title="Outcome" caption={`Share of ${all.analyzed_calls} analyzed calls, all types`} denominator={all.analyzed_calls} empty="No analyzed calls yet."
          rows={all.by_outcome.map((item) => ({ label: humanize(item.label), count: item.count, href: calls(`outcome=${encodeURIComponent(item.label)}`) }))} />
        <BarList title="Missed criteria" caption="Missed ÷ applicable, observable decisions for that criterion (met + missed); not-applicable and not-observable excluded" unit="decisions" empty="No missed criteria in current analyses."
          rows={all.missed_criteria.map((item) => ({ label: `${humanize(item.rubric_id)} · ${humanize(item.label)}`, count: item.count, denominator: item.denominator,
            href: item.rubric_id && rubricType[item.rubric_id] ? calls(`primary_type=${rubricType[item.rubric_id]}`) : undefined }))} />
        <ActivityColumns title="Upload activity" caption="Calls per upload date (when files were uploaded, not when calls happened)" points={all.upload_activity} empty="No uploads yet." />
        <ActivityColumns title="Call activity" caption={`Calls per known call date · ${all.unknown_date_calls} uploaded calls have no known date`} points={all.call_activity} empty="No call has a known call date yet." />
      </div>
    </section>
  </>;
}
