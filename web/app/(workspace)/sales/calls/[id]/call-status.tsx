"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ProcessButton } from "@/app/(workspace)/components/process-button";

import { api, failure, type Detail } from "./call-utils";

type Stage = "transcription" | "analysis" | "indexing";
const STAGES: { stage: Stage; running: string; done: string; retry: string }[] = [
  { stage: "transcription", running: "Transcribing", done: "Transcript ready", retry: "Retry transcription" },
  { stage: "analysis", running: "Analyzing", done: "Analysis ready", retry: "Retry analysis" },
  { stage: "indexing", running: "Indexing for chat", done: "Chat ready", retry: "Retry chat setup" },
];

// A retry resets the call status before the worker picks the step up; the step row still says "failed" until then.
function retryQueued(detail: Detail, stage: Stage) {
  const call = detail.call;
  return stage === "transcription" ? call.processing_status === "queued"
    : stage === "analysis" ? call.analysis_status === "stale" : call.index_status === "stale";
}

export function stageText(detail: Detail, stage: Stage): { state: string; text: string } {
  const meta = STAGES.find((item) => item.stage === stage)!;
  const step = detail.processing_steps.find((item) => item.stage === stage);
  if (!step) return { state: "waiting", text: `${meta.running}: waiting to start` };
  if (step.state === "failed") return retryQueued(detail, stage)
    ? { state: "queued", text: `${meta.running}: retry queued` }
    : { state: "failed", text: `${meta.running} failed${step.error_code ? ` (code ${step.error_code})` : ""}` };
  if (step.state === "complete") return { state: "complete", text: meta.done };
  if (step.state === "running") return { state: "running", text: `${meta.running}… (attempt ${step.attempt})` };
  return { state: "queued", text: `${meta.running}: queued` };
}

export function isProcessing(detail: Detail) {
  const call = detail.call;
  return detail.analysis_stale
    || detail.processing_steps.some((step) => step.state === "pending" || step.state === "running")
    || ["queued", "running"].includes(call.processing_status)
    || ["running", "stale"].includes(call.analysis_status)
    || ["indexing", "stale"].includes(call.index_status);
}

export function CallStatus({ detail }: { detail: Detail }) {
  const router = useRouter();
  const [busy, setBusy] = useState<Stage | null>(null);
  const [message, setMessage] = useState("");
  const active = isProcessing(detail);

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => router.refresh(), 8000);
    return () => window.clearInterval(timer);
  }, [active, router]);

  async function retry(stage: Stage) {
    setBusy(stage); setMessage("");
    try {
      await api(`/calls/${detail.call.id}/retry`, "POST", { expected_generation: detail.call.requested_generation, stage, reason: "Reviewer requested a retry from call detail" });
      setMessage("Retry queued. This page refreshes while it runs.");
      router.refresh();
    } catch (error) { setMessage(failure(error)); }
    finally { setBusy(null); }
  }

  const states = STAGES.map((meta) => ({ ...meta, ...stageText(detail, meta.stage) }));
  if (!active && !message && states.every((item) => item.state === "complete")) return null;
  return (
    <section className="surface cd-status" aria-label="Processing status">
      <ol className="cd-steps" aria-live="polite">
        {states.map((item) => (
          <li key={item.stage} className={`cd-step cd-step-${item.state}`}>
            <span>{item.text}</span>
            {item.state === "failed" && <button type="button" className="primary-button" disabled={busy !== null} onClick={() => void retry(item.stage)}>{busy === item.stage ? "Queuing…" : item.retry}</button>}
          </li>
        ))}
      </ol>
      {(detail.call.processing_status === "uploaded" || detail.call.error_code === "dispatch_failed") && <div className="process-bar">
        <p>{detail.call.error_code === "dispatch_failed" ? "Processing could not start last time. Nothing was lost; start it again." : "This recording is stored but not processed yet. Transcription, scoring and coaching start when you choose Process."}</p>
        <ProcessButton callIds={[detail.call.id]} label="Process this call" />
      </div>}
      {detail.call.processing_status === "uploading" && <p className="form-message">The upload has not finished. Upload the file again from the Call Log if it does not complete.</p>}
      {active && <p className="form-message">Checking progress every few seconds. Earlier results stay visible until new ones are ready.</p>}
      {message && <p className="form-message" role="status">{message}</p>}
    </section>
  );
}
