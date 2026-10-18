"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useRef, useState } from "react";

import { ProcessButton } from "@/app/(workspace)/components/process-button";
import { readError } from "@/lib/api-error";

const maxBytes = 25 * 1024 * 1024;

type Item = {
  file: File; callId: string; callAt: string;
  state: "ready" | "invalid" | "uploading" | "finalizing" | "done" | "error";
  progress: number; message?: string; resultId?: string;
};

function check(file: File) {
  if (!file.name.toLowerCase().endsWith(".mp3")) return "Not an MP3 file";
  if (file.size < 1 || file.size > maxBytes) return "Must be between 1 byte and 25 MB";
  return undefined;
}

type Ticket = { call_id: string; upload_id: string; upload_url: string; headers: Record<string, string> };
type Outcome = { ok: boolean; callId?: string; message?: string };

// The browser PUTs straight to private storage with a 5-minute signed URL (no size limit from the web host),
// then the API verifies size, MP3 decoding and hash before queuing processing.
function putToStorage(ticket: Ticket, file: File, onProgress: (percent: number) => void) {
  return new Promise<Outcome>((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", ticket.upload_url);
    for (const [name, value] of Object.entries(ticket.headers)) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100)); };
    xhr.onload = () => resolve(xhr.status >= 200 && xhr.status < 300 ? { ok: true } : { ok: false, message: `Private storage refused the file (${xhr.status})` });
    xhr.onerror = () => resolve({ ok: false, message: "Could not reach private storage. Check the connection (or the storage CORS setting for this site) and retry." });
    xhr.send(file);
  });
}

async function send(item: Item, timezone: string, onProgress: (percent: number) => void): Promise<Outcome> {
  try {
    const intent = await fetch("/api/domain/uploads", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files: [{
        filename: item.file.name, byte_size: item.file.size, content_type: "audio/mpeg",
        external_call_id: item.callId.trim() || null, call_at: item.callAt.trim() || null, call_timezone: timezone.trim() || null,
      }] }),
    });
    if (!intent.ok) return { ok: false, message: await readError(intent, `Upload request failed (${intent.status})`) };
    const ticket = ((await intent.json()) as { items?: Ticket[] }).items?.[0];
    if (!ticket?.upload_url) return { ok: false, message: "The upload service returned an unexpected response." };
    const stored = await putToStorage(ticket, item.file, onProgress);
    if (!stored.ok) return stored;
    const complete = await fetch(`/api/domain/calls/${encodeURIComponent(ticket.call_id)}/complete-upload`, {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ upload_id: ticket.upload_id }),
    });
    if (!complete.ok) return { ok: false, message: await readError(complete, `Upload check failed (${complete.status})`) };
    return { ok: true, callId: ticket.call_id };
  } catch {
    return { ok: false, message: "Network error. Check the connection and try again." };
  }
}

const stateText: Record<Item["state"], string> = {
  ready: "Ready to upload", invalid: "Cannot upload", uploading: "Uploading", finalizing: "Storing privately and queuing",
  done: "Uploaded, not processed yet", error: "Failed",
};

export function UploadCall() {
  const id = useId();
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [timezone, setTimezone] = useState("");
  const [busy, setBusy] = useState(false);

  const update = (index: number, patch: Partial<Item>) => setItems((current) => current.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  function choose(files: FileList | null) {
    setItems([...(files ?? [])].map((file) => {
      const message = check(file);
      return { file, callId: "", callAt: "", state: message ? "invalid" : "ready", progress: 0, message };
    }));
  }

  async function uploadAll() {
    setBusy(true);
    for (const [index, item] of items.entries()) {
      if (item.state !== "ready" && item.state !== "error") continue;
      if (item.callAt && !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}.*(Z|[+-]\d{2}:\d{2})$/.test(item.callAt.trim())) {
        update(index, { state: "error", message: "Call time needs a UTC offset, such as 2025-01-23T14:30:00-05:00" });
        continue;
      }
      update(index, { state: "uploading", progress: 0, message: undefined });
      const result = await send(item, timezone, (progress) => update(index, { progress, state: progress >= 100 ? "finalizing" : "uploading" }));
      update(index, result.ok ? { state: "done", progress: 100, resultId: result.callId } : { state: "error", message: result.message });
    }
    setBusy(false);
    router.refresh();
  }

  function close() {
    if (busy) return;
    dialog.current?.close();
  }

  const pending = items.some((item) => item.state === "ready" || item.state === "error");
  return <>
    <button ref={opener} className="primary-button" type="button" onClick={() => dialog.current?.showModal()}>Upload calls</button>
    <dialog ref={dialog} className="upload-dialog" aria-labelledby={`${id}-title`}
      onCancel={(event) => { if (busy) event.preventDefault(); }}
      onClose={() => { if (items.every((item) => item.state === "done" || item.state === "invalid")) setItems([]); opener.current?.focus(); }}>
      <div className="dialog-heading"><h2 id={`${id}-title`}>Upload calls</h2><button type="button" className="text-button" onClick={close} disabled={busy}>Close</button></div>
      <p className="form-message">MP3 only, up to 25 MB each. Files upload one at a time. Call ID and call time are optional; leave them blank when unknown. Uploading only stores the recording: nothing is transcribed or scored until you choose Process.</p>
      <label className="upload-field" htmlFor={`${id}-files`}>Choose MP3 files
        <input id={`${id}-files`} type="file" accept=".mp3,audio/mpeg" multiple disabled={busy} onChange={(event) => choose(event.target.files)} />
      </label>
      <label className="upload-field" htmlFor={`${id}-tz`}>Business time zone for these calls, if known
        <input id={`${id}-tz`} value={timezone} onChange={(event) => setTimezone(event.target.value)} maxLength={100} placeholder="America/New_York" disabled={busy} />
      </label>
      {items.length > 0 && <ol className="upload-list">
        {items.map((item, index) => (
          <li key={`${item.file.name}-${index}`} className="upload-item">
            <div className="upload-item-head"><strong>{item.file.name}</strong><span>{(item.file.size / 1024 / 1024).toFixed(1)} MB</span></div>
            {(item.state === "ready" || item.state === "error") && <div className="upload-item-fields">
              <label>Call ID<input value={item.callId} maxLength={120} placeholder="Unknown" disabled={busy} onChange={(event) => update(index, { callId: event.target.value })} /></label>
              <label>Call time with UTC offset<input value={item.callAt} placeholder="2025-01-23T14:30:00-05:00" disabled={busy} onChange={(event) => update(index, { callAt: event.target.value })} /></label>
            </div>}
            {(item.state === "uploading" || item.state === "finalizing" || item.state === "done") && <progress max={100} value={item.progress} aria-label={`${item.file.name} upload progress`} />}
            <p className={`upload-state upload-${item.state}`} role={item.state === "error" || item.state === "invalid" ? "alert" : undefined}>
              {stateText[item.state]}{item.state === "uploading" ? ` ${item.progress}%` : ""}{item.message ? `: ${item.message}` : ""}
              {item.resultId && <> · <Link href={`/sales/calls/${item.resultId}`}>Open call</Link></>}
            </p>
          </li>
        ))}
      </ol>}
      {!busy && items.some((item) => item.state === "done" && item.resultId) && <div className="process-bar">
        <p>Uploads are stored privately and wait in the Call Log. Process them now, or later from the Call Log.</p>
        <ProcessButton callIds={items.flatMap((item) => (item.state === "done" && item.resultId ? [item.resultId] : []))} label={`Process ${items.filter((item) => item.state === "done").length === 1 ? "this call" : "these calls"} now`} />
      </div>}
      <div className="dialog-actions">
        <button className="primary-button" type="button" onClick={uploadAll} disabled={busy || !pending}>{busy ? "Uploading…" : items.some((item) => item.state === "error") ? "Retry failed uploads" : "Upload"}</button>
        <span className="form-message" role="status">{busy ? "Keep this dialog open until every file finishes." : ""}</span>
      </div>
    </dialog>
  </>;
}
