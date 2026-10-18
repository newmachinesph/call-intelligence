"use client";

import { useEffect, useState } from "react";

import type { components } from "@/contracts/api";
import { readError } from "@/lib/api-error";

type Job = components["schemas"]["DeletionJobResponse"];
const STORES: Record<string, string> = { r2: "Audio file storage", vector: "Search index", database: "Call records, transcripts, analyses and chat" };
const STATES: Record<string, string> = { pending: "Purge queued", rechecking: "Copies purged; rechecking for late copies", complete: "Purge complete" };

// Content-free progress: only store names and states, polled every 5s until the purge completes.
export function DeletionProgress({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let timer: number | undefined;
    let cancelled = false;
    async function poll() {
      try {
        const response = await fetch(`/api/domain/deletions/${encodeURIComponent(jobId)}`, { cache: "no-store" });
        if (!response.ok) throw new Error(await readError(response));
        const next = await response.json() as Job;
        if (cancelled) return;
        setJob(next); setError("");
        if (next.state === "complete") return;
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Progress could not load.");
      }
      if (!cancelled) timer = window.setTimeout(poll, 5000);
    }
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobId]);

  return (
    <section className="surface del-card" aria-labelledby="del-heading">
      <p className="status-label">Private copies</p>
      <h2 id="del-heading">{job ? STATES[job.state] ?? job.state.replaceAll("_", " ") : "Loading progress…"}</h2>
      <p className="del-note">The call is already hidden from the workspace. This page shows purge progress only, never call content.</p>
      <ul className="del-stores" aria-live="polite">
        {job?.stores.map((store) => (
          <li key={store.store} className={`del-store del-${store.state}`}>
            <span>{STORES[store.store] ?? store.store}</span>
            <strong>{store.state === "complete" ? "Purged" : "Pending"}</strong>
          </li>
        ))}
      </ul>
      {job && <p className="del-note">Started {new Date(job.created_at).toLocaleString()} · attempts {job.attempts}{job.completed_at ? ` · completed ${new Date(job.completed_at).toLocaleString()}` : " · checking every 5 seconds"}</p>}
      {error && <p className="del-error" role="alert">{error} Retrying in 5 seconds.</p>}
    </section>
  );
}
