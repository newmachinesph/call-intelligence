"use client";

import { useState } from "react";

import type { components } from "@/contracts/api";
import type { CallSummary } from "@/lib/call-types";
import { PipelineBoard } from "@/app/(workspace)/sales/pipeline-board";
import { ContactsTable, CreateRecords } from "@/app/(workspace)/sales/pipeline-contacts";
import { OpportunityDetail } from "@/app/(workspace)/sales/pipeline-detail";
import { ProposalQueue, RetentionTable } from "@/app/(workspace)/sales/pipeline-queues";
import "../workflows.css";

type Contact = components["schemas"]["ContactResponse"];
type Opportunity = components["schemas"]["OpportunityResponse"];

export function PipelineWorkspace({ contacts, opportunities, calls, linkCounts }: {
  contacts: Contact[]; opportunities: Opportunity[]; calls: CallSummary[]; linkCounts?: Record<string, number>;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const opportunity = opportunities.find((item) => item.id === selected);
  return <div className="pipeline-stack">
    <section className="surface wf-card" aria-labelledby="pipeline-heading">
      <div className="wf-card-head">
        <div><h2 id="pipeline-heading">Opportunities</h2>
          <p className="wf-meta">Stages are local reported outcomes. Won does not prove payment or a booked appointment. External CRM: not connected.</p></div>
        <CreateRecords contacts={contacts} onOpportunity={setSelected} />
      </div>
      <PipelineBoard opportunities={opportunities} linkCounts={linkCounts} selected={selected} onSelect={setSelected} />
    </section>
    {opportunity && <OpportunityDetail key={opportunity.id} opportunity={opportunity} calls={calls} onClose={() => setSelected(null)} />}
    <ProposalQueue />
    <RetentionTable />
    <ContactsTable key={contacts.length} />
  </div>;
}
