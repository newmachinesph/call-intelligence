import type { components } from "@/contracts/api";

type CallSummary = components["schemas"]["CallSummary"];

// "sales.objection_handling" -> "Objection handling"; "cancellation_reported" -> "Cancellation reported".
export function humanize(value: string | null | undefined, unknown = "Unknown") {
  if (!value) return unknown;
  const text = value.split(".").pop()!.replaceAll("_", " ").replaceAll("-", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function formatDuration(seconds: number | null) {
  if (seconds === null) return "Unknown";
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

export function formatDate(iso: string | null, unknown = "Unknown") {
  return iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : unknown;
}

export function percent(value: number | null) {
  return value === null ? null : `${Math.round(value * 100)}%`;
}

const startOfDay = (date: Date) => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());

// Due wording from a stored due time; spoken wording is kept when no date could be resolved.
// ponytail: calendar days in the server's time zone; use the org time zone once it is stored.
export function dueWording(dueAt: string | null, dueText: string | null, now = new Date()) {
  if (!dueAt) return { label: dueText ? `Due date unknown (said "${dueText}")` : "Due date unknown", tone: "muted" };
  const days = Math.round((startOfDay(new Date(dueAt)) - startOfDay(now)) / 86_400_000);
  if (days < 0) return { label: `${-days} day${days === -1 ? "" : "s"} overdue`, tone: "danger" };
  if (days === 0) return { label: "Due today", tone: "warning" };
  return { label: `Due in ${days} day${days === 1 ? "" : "s"}`, tone: "muted" };
}

// Honest pipeline state for one call; failed rows name the stage so the reviewer knows what to retry.
export function callStatus(call: CallSummary): { label: string; tone: "info" | "warning" | "danger" | "success"; detail?: string } {
  if (call.processing_status === "uploading") return { label: "Uploading", tone: "info" };
  if (call.processing_status === "uploaded") return { label: "Not processed", tone: "warning", detail: "Uploaded; waiting for Process" };
  if (call.error_code === "dispatch_failed") return { label: "Not started", tone: "danger", detail: "Processing could not start; press Process" };
  if (call.processing_status === "failed") {
    const stage = call.analysis_status === "failed" ? "analysis" : "transcription";
    return { label: "Failed", tone: "danger", detail: `${stage} stage${call.error_code ? ` · ${humanize(call.error_code)}` : ""}` };
  }
  if (call.processing_status === "queued") return { label: "Queued", tone: "info" };
  if (call.processing_status === "running" || call.analysis_status === "stale") return { label: "Processing", tone: "info" };
  if (call.analysis_status === "needs_review" || call.review_status === "needs_review") return { label: "Needs review", tone: "warning" };
  if (call.analysis_status === "ready") {
    return call.index_status === "failed" ? { label: "Ready", tone: "success", detail: "Chat index failed" } : { label: "Ready", tone: "success" };
  }
  return { label: humanize(call.analysis_status), tone: "info" };
}
