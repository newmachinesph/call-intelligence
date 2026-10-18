import Link from "next/link";

import { CallLogView, type CallLogParams } from "@/app/(workspace)/sales/call-log-view";
import { OverviewView } from "@/app/(workspace)/sales/overview-view";
import { PipelineView } from "@/app/(workspace)/sales/pipeline-view";

const tabs = [
  { key: "overview", label: "Overview" },
  { key: "pipeline", label: "Pipeline" },
  { key: "calls", label: "Call Log" },
] as const;

export default async function Sales({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const raw = await searchParams;
  const params = Object.fromEntries(Object.entries(raw).map(([key, value]) => [key, Array.isArray(value) ? value[0] : value])) as CallLogParams & { view?: string };
  const selected = tabs.find((tab) => tab.key === params.view)?.key ?? "overview";

  return (
    <div className="content-column">
      <header className="page-heading"><p className="eyebrow">Sales</p><h1>Sales &amp; Call Intelligence</h1></header>
      <nav className="page-tabs" aria-label="Sales sections">
        {tabs.map((tab) => <Link key={tab.key} href={tab.key === "overview" ? "/sales" : `/sales?view=${tab.key}`} className={selected === tab.key ? "page-tab-active" : ""} aria-current={selected === tab.key ? "page" : undefined}>{tab.label}</Link>)}
      </nav>
      {selected === "overview" && <OverviewView />}
      {selected === "pipeline" && <PipelineView />}
      {selected === "calls" && <CallLogView params={params} />}
    </div>
  );
}
