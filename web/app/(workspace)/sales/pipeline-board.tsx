"use client";

import { useState } from "react";

import type { components } from "@/contracts/api";

type Opportunity = components["schemas"]["OpportunityResponse"];

const STAGES = ["New", "Contacted", "Follow-up", "Won", "Lost"] as const;

function facts(item: Opportunity, linkCounts?: Record<string, number>) {
  const count = linkCounts?.[item.id];
  return [item.manual_lock ? "Automation locked" : "Automation on", count === undefined ? null : `${count} linked call${count === 1 ? "" : "s"}`].filter(Boolean).join(" · ");
}

export function PipelineBoard({ opportunities, linkCounts, selected, onSelect }: {
  opportunities: Opportunity[]; linkCounts?: Record<string, number>; selected: string | null; onSelect: (id: string) => void;
}) {
  const [view, setView] = useState<"board" | "list">("board");
  const name = (item: Opportunity) => item.contact_name ?? "Unnamed contact";
  return <>
    <div className="wf-toggle" role="group" aria-label="Opportunity layout">
      {(["board", "list"] as const).map((item) => <button key={item} type="button" aria-pressed={view === item} onClick={() => setView(item)}>{item === "board" ? "Board" : "List"}</button>)}
    </div>
    {!opportunities.length ? <p className="wf-empty">No opportunities yet. Create a contact and an opportunity, then link a reviewed call to it.</p>
      : view === "board" ? <div className="wf-board">{STAGES.map((stage) => {
        const items = opportunities.filter((item) => item.stage === stage);
        return <section key={stage} className="wf-column" aria-label={`${stage}: ${items.length}`}>
          <h3>{stage} <span>{items.length}</span></h3>
          {items.map((item) => <button key={item.id} type="button" className="wf-opp" aria-pressed={selected === item.id} onClick={() => onSelect(item.id)}>
            <strong>{name(item)}</strong><span>{item.service_interest ?? "Service not specified"}</span><small>{facts(item, linkCounts)}</small>
          </button>)}
          {!items.length && <p className="wf-meta">None</p>}
        </section>;
      })}</div>
      : <div className="table-scroll"><table className="wf-table">
        <thead><tr><th scope="col">Contact</th><th scope="col">Service interest</th><th scope="col">Stage</th><th scope="col">Automation · links</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
        <tbody>{opportunities.map((item) => <tr key={item.id}>
          <td>{name(item)}</td><td>{item.service_interest ?? "Not specified"}</td><td>{item.stage}</td><td>{facts(item, linkCounts)}</td>
          <td><button type="button" className="text-button" aria-pressed={selected === item.id} onClick={() => onSelect(item.id)}>Open<span className="sr-only"> {name(item)}</span></button></td>
        </tr>)}</tbody>
      </table></div>}
  </>;
}
