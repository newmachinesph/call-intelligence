"use client";

import { useState } from "react";

import type { components } from "@/contracts/api";
import type { CallSummary } from "@/lib/call-types";
import { failure, load, send, when } from "@/app/(workspace)/sales/pipeline-format";
import { InsightMessage, type Coverage } from "@/app/(workspace)/assistant/insight-message";
import { ScopePicker, scopeText, type Filters, type Scope } from "@/app/(workspace)/assistant/scope-picker";

type Thread = components["schemas"]["InsightThreadResponse"];
type Message = components["schemas"]["InsightMessageResponse"];
type Answer = components["schemas"]["InsightAnswerResponse"];
type Rep = { id: string; display_name: string };

// Only questions the Insights tools can ground in call records/transcripts.
const SUGGESTIONS = ["Find missed sales opportunities", "Review rep coaching", "Show cancellation reasons", "Which calls need follow-up?"];
const pad = (value: number) => String(value).padStart(2, "0");
const localDate = (iso: unknown) => {
  if (typeof iso !== "string") return undefined;
  const date = new Date(iso);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};
const text = (value: unknown) => typeof value === "string" ? value : undefined;

function threadScope(thread: Thread): Scope {
  const f = thread.filters ?? {};
  return { kind: thread.scope, callIds: thread.call_ids ?? [], filters: {
    primary_type: text(f.primary_type), outcome: text(f.outcome), rep_id: text(f.rep_id),
    call_at_from: localDate(f.call_at_from), call_at_to: localDate(f.call_at_to),
  } };
}

function apiFilters({ call_at_from, call_at_to, ...rest }: Filters) {
  const all = { ...rest,
    call_at_from: call_at_from && new Date(`${call_at_from}T00:00`).toISOString(),
    call_at_to: call_at_to && new Date(`${call_at_to}T23:59:59.999`).toISOString() };
  const entries = Object.entries(all).filter(([, value]) => value);
  return entries.length ? Object.fromEntries(entries) : null;
}

export function AssistantPanel({ calls, reps, outcomes, initialThreads }: {
  calls: CallSummary[]; reps: Rep[]; outcomes: string[]; initialThreads: Thread[];
}) {
  const [scope, setScope] = useState<Scope>({ kind: "organization", callIds: [], filters: {} });
  const [thread, setThread] = useState<Thread | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [coverage, setCoverage] = useState<Record<string, Coverage>>({});
  const [threads, setThreads] = useState(initialThreads);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const indexed = calls.filter((call) => call.index_status === "ready").length;

  function reset() { setThread(null); setMessages([]); setError(""); }

  async function openThread(item: Thread) {
    setBusy(true); setError("");
    try {
      const list = await load<{ items: Message[] }>(`/insights/threads/${item.id}/messages?limit=100`);
      setThread(item); setScope(threadScope(item)); setMessages(list.items);
    } catch (cause) { setError(failure(cause)); }
    finally { setBusy(false); }
  }

  async function ask(value: string) {
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    if (!thread && scope.kind === "selected" && !scope.callIds.length) { setError("Choose at least one call for a selected-calls scope."); return; }
    setBusy(true); setError("");
    try {
      let current = thread;
      if (!current) {
        const created = await send<Thread>("/insights/threads", {
          title: trimmed.slice(0, 90), scope: scope.kind,
          call_ids: scope.kind === "selected" ? scope.callIds : null,
          filters: scope.kind === "organization" ? apiFilters(scope.filters) : null,
        });
        current = created; setThread(created); setThreads((items) => [created, ...items]);
      }
      const key = crypto.randomUUID();
      const answer = await send<Answer>(`/insights/threads/${current.id}/messages`, { text: trimmed, client_message_id: key }, "POST", key);
      setCoverage((map) => ({ ...map, [answer.message_id]: answer.coverage as Coverage }));
      // ponytail: first 100 messages of a thread; add message paging if conversations get that long.
      setMessages((await load<{ items: Message[] }>(`/insights/threads/${current.id}/messages?limit=100`)).items);
      setQuestion("");
    } catch (cause) {
      const conflict = (cause as { status?: number }).status === 409;
      setError(conflict ? `${failure(cause)} Your question is kept. Select Go to ask again against the current sources.` : failure(cause));
    } finally { setBusy(false); }
  }

  return <section className="surface wf-assistant" aria-labelledby="assistant-heading">
    <h2 id="assistant-heading">What would you like to know?</h2>
    {thread && <div className="wf-thread-head">
      <p><strong>{thread.title ?? "Conversation"}</strong><span>{scopeText(scope, reps)}</span></p>
      <button type="button" className="text-button" onClick={reset}>New conversation</button>
    </div>}
    {messages.length > 0 && <div className="wf-conversation" aria-live="polite">
      {messages.map((message) => <InsightMessage key={message.id} message={message} coverage={coverage[message.id]} />)}
    </div>}
    <form className="wf-ask" onSubmit={(event) => { event.preventDefault(); void ask(question); }}>
      <label className="sr-only" htmlFor="assistant-input">Question for the assistant</label>
      <input id="assistant-input" value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={4000} autoComplete="off"
        placeholder={thread ? "Ask a follow-up in this conversation" : "Ask about your calls"} />
      <button className="wf-go" disabled={busy || !question.trim()}>{busy ? "Checking" : "Go"}</button>
    </form>
    {busy && <p className="wf-meta" role="status">Searching authorized call excerpts and checking citations…</p>}
    {error && <p className="wf-error" role="alert">{error}</p>}
    <ScopePicker scope={scope} calls={calls} reps={reps} outcomes={outcomes} onChange={(next) => { setScope(next); if (thread) reset(); }} />
    <p className="wf-meta">{indexed} of {calls.length} listed calls are indexed for excerpt search. Excerpts are examples, not every call; counts come from call records.</p>
    <div className="wf-suggestions">{SUGGESTIONS.map((item) =>
      <button type="button" key={item} disabled={busy} onClick={() => { setQuestion(item); void ask(item); }}>{item}</button>)}</div>
    <div className="wf-recent">
      <h3>Recent</h3>
      {threads.length ? <ul>{threads.map((item) => <li key={item.id}>
        <button type="button" disabled={busy} aria-current={thread?.id === item.id || undefined} onClick={() => openThread(item)}>
          {item.title ?? "Untitled conversation"}<span>{scopeText(threadScope(item), reps)} · {when(item.created_at)}</span>
        </button></li>)}</ul>
        : <p className="wf-meta">No conversations yet. Only you can see your conversations.</p>}
    </div>
  </section>;
}
