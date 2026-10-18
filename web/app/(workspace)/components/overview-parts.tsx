import Link from "next/link";

import type { components } from "@/contracts/api";
import { dueWording, humanize, percent } from "@/app/(workspace)/components/format";

type RateMetric = components["schemas"]["RateMetric"];
type FollowUpDue = components["schemas"]["FollowUpDue"];

// Where each shared metric's source calls live in the Call Log.
const metricLinks: Record<string, string> = {
  analyzed_share: "/sales?view=calls",
  needs_review_share: "/sales?view=calls&analysis_status=needs_review",
  reviewed_conversion: "/sales?view=calls&primary_type=sales&review_status=reviewed",
  reviewed_save_rate: "/sales?view=calls&primary_type=retention&review_status=reviewed",
};

const denominatorNoun: Record<string, string> = {
  analyzed_share: "uploaded calls", needs_review_share: "analyzed calls",
  reviewed_conversion: "reviewed resolved sales calls", reviewed_save_rate: "reviewed resolved retention calls",
};

// Numerator/denominator, non-zero exclusions and the definition behind one shared metric.
export function MetricDetails({ metric }: { metric: RateMetric }) {
  const exclusions = metric.exclusions.filter((item) => item.count > 0);
  return <>
    <small>{metric.numerator} of {metric.denominator} {denominatorNoun[metric.key] ?? "calls"}{metric.value === null ? "" : ` (${percent(metric.value)})`}</small>
    {exclusions.length > 0 && <ul className="metric-exclusions">{exclusions.map((item) => <li key={item.label}>Excluded: {item.label} · {item.count}</li>)}</ul>}
    <details className="metric-definition"><summary>Definition</summary><p>{metric.definition}</p></details>
  </>;
}

export function MetricCards({ metrics }: { metrics: RateMetric[] }) {
  return (
    <div className="metric-grid">
      {metrics.map((metric) => (
        <section className="surface metric-card" key={metric.key} aria-labelledby={`metric-${metric.key}`}>
          <span id={`metric-${metric.key}`}>{metric.label}</span>
          <strong className={metric.value === null ? "metric-none" : ""}>{percent(metric.value) ?? (metric.key.startsWith("reviewed_") ? "No reviewed rate yet" : "No data yet")}</strong>
          <MetricDetails metric={metric} />
          {metricLinks[metric.key] && <Link className="metric-link" href={metricLinks[metric.key]}>Open calls<span className="sr-only"> for {metric.label}</span></Link>}
        </section>
      ))}
    </div>
  );
}

export function FollowUpsDue({ items }: { items: FollowUpDue[] }) {
  return (
    <section className="surface panel-card" aria-labelledby="follow-ups-heading">
      <div className="panel-card-heading"><h2 id="follow-ups-heading">Follow-Ups Due</h2><Link href="/tasks">All tasks</Link></div>
      {items.length ? <ul className="inset-list">
        {items.map((item) => {
          const due = dueWording(item.due_at, item.due_text);
          return <li className="inset-row" key={item.id}>
            <div>
              <strong>{/^[a-z_]+$/.test(item.proposed_action) ? humanize(item.proposed_action) : item.proposed_action}</strong>
              <p><span className={`tone-${due.tone}`}>{due.label}</span> · {humanize(item.status)} · {item.owner_user_id ? "Assigned" : "Unassigned"} · <Link href={`/sales/calls/${item.call_id}`}>Source call {item.call_label}</Link></p>
            </div>
            <Link className="primary-button" href={`/tasks?call_id=${item.call_id}`}>Open task</Link>
          </li>;
        })}
      </ul> : <p className="panel-empty-text">No open follow-ups. Follow-ups appear here when an analyzed call records a commitment.</p>}
    </section>
  );
}

export function AttentionRows({ rows }: { rows: { count: number; title: string; detail: string; href: string; action: string }[] }) {
  const open = rows.filter((row) => row.count > 0);
  return (
    <section className="surface panel-card panel-accent" aria-labelledby="attention-heading">
      <span className="surface-accent" aria-hidden="true" />
      <div className="panel-card-heading"><h2 id="attention-heading">Needs Attention</h2></div>
      {open.length ? <ul className="inset-list">
        {open.map((row) => <li className="inset-row" key={row.title}>
          <div><strong>{row.count} {row.title}</strong><p>{row.detail}</p></div>
          <Link className="primary-button" href={row.href}>{row.action} →</Link>
        </li>)}
      </ul> : <p className="panel-empty-text">Nothing is waiting: no calls need review, no follow-ups await approval or an owner, and no processing failures are recorded.</p>}
    </section>
  );
}
