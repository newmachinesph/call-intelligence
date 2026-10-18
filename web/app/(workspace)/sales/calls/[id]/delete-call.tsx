"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

import { api, failure, type Schemas } from "./call-utils";

export function DeleteCall({ callId }: { callId: string }) {
  const router = useRouter();
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function remove(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      const { data } = await api<Schemas["DeleteCallResponse"]>(`/calls/${callId}`, "DELETE");
      router.push(data.deletion_job_id ? `/deletions/${data.deletion_job_id}` : "/sales?view=calls");
    } catch (error) { setMessage(failure(error)); setBusy(false); }
  }

  return (
    <form className="surface cd-card cd-danger" onSubmit={remove} aria-labelledby="cd-delete-heading">
      <p className="status-label">Owner only</p>
      <h2 id="cd-delete-heading">Delete this call</h2>
      <p className="cd-hint">Hides the call immediately and purges the audio, transcripts, analyses, chat, search index, address checks, follow-ups and CRM call links. This cannot be undone.</p>
      <label>Type DELETE to confirm<input value={word} onChange={(event) => setWord(event.target.value)} autoComplete="off" /></label>
      <button type="submit" className="cd-danger-button" disabled={busy || word !== "DELETE"}>{busy ? "Deleting…" : "Delete call and purge copies"}</button>
      <p className="form-message" role="status">{message}</p>
    </form>
  );
}
