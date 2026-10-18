import Link from "next/link";

import type { components } from "@/contracts/api";
import { domainGet } from "@/lib/domain";
import { TasksBoard } from "@/app/(workspace)/tasks/tasks-board";
import "../workflows.css";

type TaskList = components["schemas"]["TaskListResponse"];
type Members = components["schemas"]["MemberListResponse"];
type Params = { status?: string; owner?: string; due?: string; call_id?: string; cursor?: string };

const FILTERS = {
  status: [["", "Any status"], ["pending_approval", "Pending approval"], ["assigned", "Assigned"], ["completed", "Completed"], ["cancelled", "Cancelled"]],
  owner: [["", "Anyone"], ["me", "Assigned to me"], ["unassigned", "Unassigned"]],
  due: [["", "Any due date"], ["overdue", "Overdue"], ["today", "Due today"], ["upcoming", "Upcoming"], ["unknown", "Due date unknown"]],
} as const;

export default async function Tasks({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const key of ["status", "owner", "due", "call_id", "cursor"] as const) if (params[key]) query.set(key, params[key]);
  const [data, members] = await Promise.all([domainGet<TaskList>(`/tasks?limit=30&${query}`), domainGet<Members>("/members")]);
  const filtered = Boolean(params.status || params.owner || params.due || params.call_id);
  const next = new URLSearchParams(query);
  if (data.next_cursor) next.set("cursor", data.next_cursor);
  const first = new URLSearchParams(query);
  first.delete("cursor");

  return <div className="content-column">
    <header className="page-heading"><p className="eyebrow">Local follow-ups</p><h1>Tasks</h1></header>
    <section className="surface wf-card" aria-labelledby="tasks-heading">
      <div className="wf-card-head"><h2 id="tasks-heading">Follow-ups</h2><p className="wf-meta">Updates this workspace only — no customer message is sent.</p></div>
      <form className="wf-filter-bar" method="get" aria-label="Filter tasks">
        {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((key) => <label key={key}>{key === "due" ? "Due" : key === "owner" ? "Owner" : "Status"}
          <select name={key} defaultValue={params[key] ?? ""}>{FILTERS[key].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}
        {params.call_id && <input type="hidden" name="call_id" value={params.call_id} />}
        <button type="submit" className="wf-secondary">Apply</button>
        {filtered && <Link href="/tasks">Clear filters</Link>}
      </form>
      <TasksBoard items={data.items} members={members.items} filtered={filtered} />
      <nav className="wf-pager" aria-label="Task pages">
        {params.cursor && <Link href={`/tasks?${first}`}>First page</Link>}
        {data.next_cursor && <Link href={`/tasks?${next}`}>Next page</Link>}
      </nav>
    </section>
  </div>;
}
