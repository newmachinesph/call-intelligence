import type { components } from "@/contracts/api";
import { AssistantPanel } from "@/app/(workspace)/assistant/assistant-panel";
import type { CallListResponse } from "@/lib/call-types";
import { domainGet } from "@/lib/domain";
import "../workflows.css";

type Overview = components["schemas"]["OverviewResponse"];
type Reps = components["schemas"]["RepListResponse"];
type Threads = components["schemas"]["InsightThreadList"];

export default async function Assistant() {
  const [calls, reps, overview, threads] = await Promise.all([
    domainGet<CallListResponse>("/calls?limit=100"), domainGet<Reps>("/reps"),
    domainGet<Overview>("/analytics/overview"), domainGet<Threads>("/insights/threads?limit=20"),
  ]);
  return <div className="content-column">
    <header className="page-heading"><h1>Assistant</h1></header>
    <AssistantPanel calls={calls.items} reps={reps.items} outcomes={overview.by_outcome.map((item) => item.label)} initialThreads={threads.items} />
  </div>;
}
