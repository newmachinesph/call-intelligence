"use client";

import { useState } from "react";

import type { CallSummary } from "@/lib/call-types";
import { words } from "@/app/(workspace)/sales/pipeline-format";

// Call dates are local YYYY-MM-DD here; the panel converts them to offset timestamps for the API.
export type Filters = { primary_type?: string; outcome?: string; rep_id?: string; call_at_from?: string; call_at_to?: string };
export type Scope = { kind: "organization" | "selected"; callIds: string[]; filters: Filters };
type Rep = { id: string; display_name: string };

export const MAX_SELECTED = 50;
const TYPES = ["sales", "retention", "customer_service", "other"];

export function scopeText(scope: Scope, reps: Rep[]) {
  if (scope.kind === "selected") return `${scope.callIds.length} selected call${scope.callIds.length === 1 ? "" : "s"}`;
  const f = scope.filters;
  const parts = [f.primary_type && words(f.primary_type), f.outcome && words(f.outcome),
    f.rep_id && (f.rep_id === "unknown" ? "unknown rep" : reps.find((rep) => rep.id === f.rep_id)?.display_name ?? "one rep"),
    f.call_at_from && `from ${f.call_at_from}`, f.call_at_to && `to ${f.call_at_to}`].filter(Boolean);
  return `All eligible calls in organization${parts.length ? ` · ${parts.join(" · ")}` : ""}`;
}

export function ScopePicker({ scope, onChange, calls, reps, outcomes }: {
  scope: Scope; onChange: (scope: Scope) => void; calls: CallSummary[]; reps: Rep[]; outcomes: string[];
}) {
  const [search, setSearch] = useState("");
  const setFilter = (key: keyof Filters, value: string) => onChange({ ...scope, filters: { ...scope.filters, [key]: value || undefined } });
  const toggle = (id: string, on: boolean) => onChange({ ...scope, callIds: on ? [...scope.callIds, id] : scope.callIds.filter((item) => item !== id) });
  const term = search.trim().toLowerCase();
  const visible = calls.filter((call) => !term || `${call.external_call_id ?? ""} ${call.filename} ${call.rep_name ?? ""}`.toLowerCase().includes(term));

  return <fieldset className="wf-scope">
    <legend>Scope <span>(changing it starts a new conversation)</span></legend>
    <div className="wf-radios">
      <label><input type="radio" name="scope" checked={scope.kind === "organization"} onChange={() => onChange({ ...scope, kind: "organization" })} /> All eligible calls in organization</label>
      <label><input type="radio" name="scope" checked={scope.kind === "selected"} onChange={() => onChange({ ...scope, kind: "selected" })} /> Selected calls</label>
    </div>
    {scope.kind === "organization" ? <details className="wf-filters">
      <summary>Filters (optional)</summary>
      <div className="wf-filter-grid">
        <label>Type<select value={scope.filters.primary_type ?? ""} onChange={(event) => setFilter("primary_type", event.target.value)}><option value="">Any type</option>{TYPES.map((type) => <option key={type} value={type}>{words(type)}</option>)}</select></label>
        <label>Outcome<select value={scope.filters.outcome ?? ""} onChange={(event) => setFilter("outcome", event.target.value)}><option value="">Any outcome</option>{outcomes.map((outcome) => <option key={outcome} value={outcome}>{words(outcome)}</option>)}</select></label>
        <label>Rep<select value={scope.filters.rep_id ?? ""} onChange={(event) => setFilter("rep_id", event.target.value)}><option value="">Any rep</option><option value="unknown">Unknown rep</option>{reps.map((rep) => <option key={rep.id} value={rep.id}>{rep.display_name}</option>)}</select></label>
        <label>Call date from<input type="date" value={scope.filters.call_at_from ?? ""} onChange={(event) => setFilter("call_at_from", event.target.value)} /></label>
        <label>Call date to<input type="date" value={scope.filters.call_at_to ?? ""} onChange={(event) => setFilter("call_at_to", event.target.value)} /></label>
      </div>
      <p className="wf-meta">A call-date filter excludes calls whose date is unknown.</p>
    </details> : <div className="wf-checklist">
      <label className="sr-only" htmlFor="scope-search">Search calls</label>
      <input id="scope-search" type="search" placeholder="Search calls by ID, file or rep" value={search} onChange={(event) => setSearch(event.target.value)} />
      <p className="wf-meta" aria-live="polite">{scope.callIds.length} of up to {MAX_SELECTED} selected</p>
      <div className="wf-checklist-items">{visible.length ? visible.map((call) => {
        const checked = scope.callIds.includes(call.id);
        return <label key={call.id}><input type="checkbox" checked={checked} disabled={!checked && scope.callIds.length >= MAX_SELECTED} onChange={(event) => toggle(call.id, event.target.checked)} />
          <span>{call.external_call_id ?? call.filename}<small>{words(call.primary_type, "type pending")} · {call.index_status === "ready" ? "indexed" : "not indexed yet"}</small></span></label>;
      }) : <p className="wf-meta">No calls match.</p>}</div>
    </div>}
  </fieldset>;
}
