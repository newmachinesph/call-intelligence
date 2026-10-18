"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { readError } from "@/lib/api-error";

// Owner-only identity verification for a rep (PATCH /reps/{id} requires a reason).
export function VerifyRep({ repId }: { repId: string }) {
  const router = useRouter();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    const response = await fetch(`/api/domain/reps/${repId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ verified: true, reason: reason.trim() }),
    }).catch(() => null);
    setBusy(false);
    if (!response) return setMessage("Network error. Try again.");
    if (!response.ok) return setMessage(await readError(response));
    router.refresh();
  }

  return (
    <form className="surface panel-card verify-form" onSubmit={submit}>
      <h2>Verify identity</h2>
      <p className="panel-empty-text">Confirm this rep is a real, correctly named team member. A reason of at least 3 characters is required.</p>
      <label htmlFor="verify-reason">Reason</label>
      <div className="verify-row">
        <input id="verify-reason" value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={1000} placeholder="e.g. Confirmed with the office roster" disabled={busy} />
        <button className="primary-button" type="submit" disabled={busy || reason.trim().length < 3}>{busy ? "Saving…" : "Mark identity verified"}</button>
      </div>
      {message && <p className="form-message tone-danger" role="alert">{message}</p>}
    </form>
  );
}
