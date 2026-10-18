"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { readError } from "@/lib/api-error";

type Result = { items: { started: boolean; reason: string | null }[] };

// Uploads are never analyzed automatically: this explicit, confirmed click is what spends AI credits.
export function ProcessButton({ callIds, label, compact = false }: { callIds: string[]; label: string; compact?: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const ids = callIds.slice(0, 50); // API accepts 50 per request

  async function run() {
    const count = ids.length;
    const plural = count === 1 ? "" : "s";
    if (!window.confirm(`Process ${count} call${plural} now?\n\nTranscription, scoring and indexing use AI credits (roughly $0.25 per 10-minute call).`)) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/domain/calls/process", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ call_ids: ids }),
      });
      if (!response.ok) throw new Error(await readError(response, "Could not start processing"));
      const { items } = (await response.json()) as Result;
      const started = items.filter((item) => item.started).length;
      const skipped = items.find((item) => !item.started);
      setMessage(`${started} queued for processing${skipped ? ` · ${items.length - started} skipped: ${skipped.reason}` : ""}`);
      router.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not start processing");
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="process-action">
      <button type="button" className={compact ? "process-button-compact" : "primary-button"} onClick={run} disabled={busy || ids.length === 0}>
        {busy ? "Starting…" : label}
      </button>
      {message && <small role="status">{message}</small>}
    </span>
  );
}
