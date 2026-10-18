"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { EvidenceButtons } from "./call-scorecard";
import { api, failure, type Schemas, type Seek } from "./call-utils";

type Message = Schemas["MessageResponse"];
const PROMPTS = ["What price was quoted?", "Did the customer agree?", "Why this score?"];
const EVIDENCE_WORDS: Record<string, string> = {
  supported: "Supported by the transcript",
  contradicted: "The transcript contradicts this",
  insufficient: "Not enough evidence in the transcript",
  conflicting: "The transcript has conflicting evidence",
};

export function CallChat({ callId, transcriptVersion, disabledReason, onSeek }: { callId: string; transcriptVersion: number | null; disabledReason: string | null; onSeek: Seek }) {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [failed, setFailed] = useState(false);
  const pending = useRef<{ text: string; id: string } | null>(null); // reused on retry so the server dedupes

  useEffect(() => {
    let cancelled = false;
    api<Schemas["ThreadListResponse"]>(`/calls/${callId}/chat/threads`).then(async ({ data }) => {
      const id = data.items[0]?.id;
      if (!id || cancelled) return;
      const history = await api<Schemas["MessageListResponse"]>(`/calls/${callId}/chat/threads/${id}/messages`);
      if (!cancelled) { setThreadId(id); setMessages(history.data.items); }
    }).catch((error) => { if (!cancelled) setStatus(`Earlier questions could not load: ${failure(error)}`); });
    return () => { cancelled = true; };
  }, [callId]);

  async function send(text: string, retry = false) {
    if (!text || busy || disabledReason) return;
    if (!retry || pending.current?.text !== text) pending.current = { text, id: crypto.randomUUID() };
    const attempt = pending.current;
    setBusy(true); setFailed(false);
    setStatus("Checking the current transcript and scorecard…");
    try {
      let thread = threadId;
      if (!thread) {
        thread = (await api<Schemas["ThreadResponse"]>(`/calls/${callId}/chat/threads`, "POST", { title: "Call review" })).data.id;
        setThreadId(thread);
      }
      await api(`/calls/${callId}/chat/threads/${thread}/messages`, "POST", { text, client_message_id: attempt.id }, { "Idempotency-Key": attempt.id });
      setMessages((await api<Schemas["MessageListResponse"]>(`/calls/${callId}/chat/threads/${thread}/messages`)).data.items);
      pending.current = null;
      setDraft((current) => current.trim() === text ? "" : current);
      setStatus("Answer ready.");
    } catch (error) {
      setDraft((current) => current.trim() ? current : text); // keep the question so it can be retried
      setFailed(true);
      setStatus(`The answer could not be checked: ${failure(error, "The call changed or chat setup is not ready.")}`);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(draft.trim());
  }

  return (
    <div className="cd-chat">
      <div className="cd-chat-history" aria-live="polite">
        {messages.length ? messages.map((message) => (
          <article key={message.id} className={`cd-msg cd-msg-${message.role}`}>
            <p className="cd-msg-meta">{message.role === "user" ? "You" : `Answer · ${EVIDENCE_WORDS[message.evidence_status ?? ""] ?? "Evidence not checked"}`}</p>
            {message.stale && <p className="cd-updating">Stale: the transcript changed after this answer. Ask again for the current version.</p>}
            <p className="cd-msg-text">{message.text}</p>
            <EvidenceButtons refs={message.citations} version={transcriptVersion} onSeek={onSeek} />
          </article>
        )) : <p className="cd-empty">Ask about this recording. Answers use the current transcript and scorecard and link to playable sources.</p>}
      </div>
      <div className="cd-prompts" role="group" aria-label="Suggested questions">
        {PROMPTS.map((prompt) => <button key={prompt} type="button" className="cd-chip" disabled={busy || !!disabledReason} onClick={() => void send(prompt)}>{prompt}</button>)}
      </div>
      <form className="cd-chat-form" onSubmit={submit}>
        <label htmlFor="cd-chat-question">Ask this call</label>
        <textarea id="cd-chat-question" value={draft} onChange={(event) => setDraft(event.target.value)} rows={3} maxLength={2000}
          placeholder="What was agreed during this call?" disabled={!!disabledReason} aria-describedby="cd-chat-status" />
        <div className="cd-row">
          <button type="submit" className="primary-button" disabled={busy || !!disabledReason || !draft.trim()}>{busy ? "Checking…" : "Ask"}</button>
          {failed && !busy && <button type="button" className="text-button" onClick={() => void send(draft.trim(), true)}>Retry</button>}
        </div>
        <p id="cd-chat-status" className="form-message" role="status">{disabledReason ?? status}</p>
      </form>
    </div>
  );
}
