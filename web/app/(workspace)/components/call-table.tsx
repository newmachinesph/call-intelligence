import Link from "next/link";

import type { components } from "@/contracts/api";
import { callStatus, formatDate, formatDuration, humanize } from "@/app/(workspace)/components/format";
import { ProcessButton } from "@/app/(workspace)/components/process-button";

type CallSummary = components["schemas"]["CallSummary"];

function score(call: CallSummary) {
  if (call.confirmed_points === null) return "Not scored";
  const points = `${call.confirmed_points}/${call.nominal_maximum ?? "?"}`;
  if (call.grade) return `${points} · ${humanize(call.grade)}`;
  return `${points} · provisional${call.unresolved_count ? ` (${call.unresolved_count} unresolved)` : ""}`;
}

export function CallTable({ calls, caption }: { calls: CallSummary[]; caption: string }) {
  return (
    <div className="table-scroll">
      <table className="call-table">
        <caption className="sr-only">{caption}</caption>
        <thead><tr><th scope="col">Call ID</th><th scope="col">Call date</th><th scope="col">Type</th><th scope="col">Outcome</th><th scope="col">Score</th><th scope="col">Rep</th><th scope="col">Duration</th><th scope="col">Status</th></tr></thead>
        <tbody>
          {calls.map((call) => {
            const status = callStatus(call);
            return <tr key={call.id}>
              <td><Link href={`/sales/calls/${call.id}`}>{call.external_call_id ?? "No Call ID"}</Link><small>{call.filename}</small></td>
              <td>{formatDate(call.call_at)}<small>Uploaded {formatDate(call.uploaded_at)}</small></td>
              <td>{humanize(call.primary_type)}{call.type_reviewed && <small>Reviewed</small>}</td>
              <td>{humanize(call.outcome)}{call.outcome_reviewed && <small>Reviewed</small>}</td>
              <td>{score(call)}</td>
              <td>{call.rep_name ?? "Unknown rep"}</td>
              <td>{formatDuration(call.duration_seconds)}</td>
              <td>
                <span className={`status-pill tone-pill-${status.tone}`}>{status.label}</span>
                {status.detail && <small>{status.detail}</small>}
                {(call.processing_status === "uploaded" || call.error_code === "dispatch_failed") && <ProcessButton callIds={[call.id]} label="Process" compact />}
                {status.label === "Failed" && <small><Link href={`/sales/calls/${call.id}`}>Open to retry<span className="sr-only"> {call.external_call_id ?? call.filename}</span></Link></small>}
              </td>
            </tr>;
          })}
        </tbody>
      </table>
    </div>
  );
}
