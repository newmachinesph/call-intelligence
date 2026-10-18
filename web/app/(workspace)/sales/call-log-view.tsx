import Link from "next/link";

import type { components } from "@/contracts/api";
import { CallTable } from "@/app/(workspace)/components/call-table";
import { humanize } from "@/app/(workspace)/components/format";
import { ProcessButton } from "@/app/(workspace)/components/process-button";
import { UploadCall } from "@/app/(workspace)/sales/upload-call";
import { domainGet } from "@/lib/domain";

type CallList = components["schemas"]["CallListResponse"];
type Overview = components["schemas"]["OverviewResponse"];
type Reps = components["schemas"]["RepListResponse"];

export type CallLogParams = Partial<Record<(typeof filterKeys)[number] | "cursor", string>>;

// URL params kept across search, filters and pagination. `from`/`to` are calendar dates (UTC).
const filterKeys = ["q", "primary_type", "outcome", "rep_id", "analysis_status", "review_status", "processing_status", "date_known", "from", "to"] as const;
// Drop malformed params instead of letting the API reject the whole page.
const valid: Partial<Record<(typeof filterKeys)[number], RegExp>> = {
  primary_type: /^(sales|retention|customer_service|other)$/, outcome: /^[a-z_]{2,40}$/, date_known: /^(true|false)$/,
  rep_id: /^(unknown|[0-9a-f-]{36})$/i, analysis_status: /^[a-z_]{2,24}$/, review_status: /^[a-z_]{2,24}$/, processing_status: /^[a-z_]{2,24}$/,
  from: /^\d{4}-\d{2}-\d{2}$/, to: /^\d{4}-\d{2}-\d{2}$/,
};

function Select({ name, label, value, options }: { name: string; label: string; value?: string; options: [string, string][] }) {
  return <label>{label}<select name={name} defaultValue={value ?? ""}><option value="">Any</option>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>;
}

export async function CallLogView({ params }: { params: CallLogParams }) {
  const kept = new URLSearchParams({ view: "calls" });
  const api = new URLSearchParams({ limit: "50" });
  for (const key of filterKeys) {
    const value = params[key]?.trim();
    if (!value || (valid[key] && !valid[key].test(value))) continue;
    kept.set(key, value);
    if (key === "from") api.set("call_at_from", `${value}T00:00:00Z`);
    else if (key === "to") api.set("call_at_to", `${value}T23:59:59.999Z`);
    else api.set(key, value);
  }
  if (params.cursor) api.set("cursor", params.cursor);
  const filtered = [...kept.keys()].length > 1;

  const [data, overview, reps, waiting] = await Promise.all([
    domainGet<CallList>(`/calls?${api}`),
    domainGet<Overview>("/analytics/overview"),
    domainGet<Reps>("/reps?limit=100"),
    domainGet<CallList>("/calls?processing_status=uploaded&limit=50"),
  ]);
  const empty = !data.items.length && !filtered && !params.cursor;
  const outcomes = [...new Set([...overview.by_outcome.map((item) => item.label), ...(params.outcome ? [params.outcome] : [])])];

  return (
    <section className="surface call-table-surface" aria-labelledby="call-log-heading">
      <div className="table-heading">
        <div><p className="status-label">Private recordings</p><h2 id="call-log-heading">Call Log</h2></div>
        {!empty && <UploadCall />}
      </div>
      <form className="call-filters" action="/sales" method="get" role="search" aria-label="Filter calls">
        <input type="hidden" name="view" value="calls" />
        <label className="filter-search">Search<input name="q" type="search" defaultValue={params.q ?? ""} placeholder="Filename or Call ID" maxLength={100} /></label>
        <Select name="primary_type" label="Type" value={params.primary_type} options={["sales", "retention", "customer_service", "other"].map((key) => [key, humanize(key)])} />
        <Select name="outcome" label="Outcome" value={params.outcome} options={outcomes.map((key) => [key, humanize(key)])} />
        <Select name="rep_id" label="Rep" value={params.rep_id} options={[["unknown", "Unknown rep"], ...reps.items.map((rep): [string, string] => [rep.id, rep.display_name])]} />
        <Select name="analysis_status" label="Analysis" value={params.analysis_status} options={["not_started", "running", "ready", "needs_review", "failed", "stale"].map((key) => [key, humanize(key)])} />
        <Select name="review_status" label="Review" value={params.review_status} options={["unreviewed", "needs_review", "reviewed"].map((key) => [key, humanize(key)])} />
        <Select name="processing_status" label="Processing" value={params.processing_status} options={[["uploaded", "Not processed"], ...["uploading", "queued", "running", "complete", "failed"].map((key): [string, string] => [key, humanize(key)])]} />
        <Select name="date_known" label="Call date" value={params.date_known} options={[["true", "Known"], ["false", "Unknown"]]} />
        <label>From (UTC)<input name="from" type="date" defaultValue={kept.get("from") ?? ""} /></label>
        <label>To (UTC)<input name="to" type="date" defaultValue={kept.get("to") ?? ""} /></label>
        <div className="filter-actions"><button className="primary-button" type="submit">Apply</button>{filtered && <Link href="/sales?view=calls">Clear filters</Link>}</div>
      </form>
      {waiting.items.length > 0 && <div className="process-bar" role="region" aria-label="Calls waiting to be processed">
        <p><strong>{waiting.items.length}{waiting.next_cursor ? "+" : ""} uploaded call{waiting.items.length === 1 ? "" : "s"} not processed yet.</strong> Nothing is transcribed or scored until you choose to process it.</p>
        <ProcessButton callIds={waiting.items.map((call) => call.id)} label={`Process ${waiting.items.length === 1 ? "it" : `all ${waiting.items.length}`}`} />
      </div>}
      {data.items.length ? <CallTable calls={data.items} caption="Calls matching the current filters" />
        : filtered ? <div className="table-empty"><p>No calls match these filters.</p><Link href="/sales?view=calls">Clear filters</Link></div>
          : <div className="table-empty"><p>No calls have been uploaded yet. Upload MP3 recordings to start reviewing.</p><UploadCall /></div>}
      {(params.cursor || data.next_cursor) && <nav className="table-pagination" aria-label="Call Log pages">
        {params.cursor && <Link href={`/sales?${kept}`}>First page</Link>}
        {data.next_cursor && <Link href={`/sales?${kept}&cursor=${encodeURIComponent(data.next_cursor)}`}>Older calls</Link>}
      </nav>}
    </section>
  );
}
