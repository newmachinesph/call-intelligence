import type { components } from "@/contracts/api";
import { PipelineWorkspace } from "@/app/(workspace)/sales/pipeline-workspace";
import type { CallListResponse } from "@/lib/call-types";
import { domainGet } from "@/lib/domain";

type Contacts = components["schemas"]["ContactListResponse"];
type Opportunities = components["schemas"]["OpportunityListResponse"];
type OpportunityDetail = components["schemas"]["OpportunityDetailResponse"];

// Self-contained Pipeline tab; the Sales page only renders <PipelineView />.
export async function PipelineView() {
  const [contacts, opportunities, calls] = await Promise.all([
    domainGet<Contacts>("/crm/contacts?limit=100"),
    domainGet<Opportunities>("/crm/opportunities?limit=100"),
    domainGet<CallListResponse>("/calls?limit=100"),
  ]);
  // ponytail: one detail read per listed opportunity for linked-call counts; add a count to the list API if the board grows.
  const details = await Promise.all(opportunities.items.map((item) => domainGet<OpportunityDetail>(`/crm/opportunities/${item.id}`)));
  const linkCounts = Object.fromEntries(details.map((detail) => [detail.opportunity.id, detail.linked_calls.length]));
  return <PipelineWorkspace contacts={contacts.items} opportunities={opportunities.items} calls={calls.items} linkCounts={linkCounts} />;
}
