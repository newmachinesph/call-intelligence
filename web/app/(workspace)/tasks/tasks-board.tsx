"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import type { components } from "@/contracts/api";
import { clock, failure, send, words } from "@/app/(workspace)/sales/pipeline-format";
import { Dialog } from "@/app/(workspace)/sales/pipeline-shared";

type Task = components["schemas"]["TaskResponse"];
type Member = components["schemas"]["MemberItem"];
type Mode = "approve" | "assign" | "complete" | "cancel" | "due";

const TITLES: Record<Mode, string> = { approve: "Approve & assign", assign: "Assign", complete: "Mark complete", cancel: "Cancel task", due: "Edit due date" };
const DEFAULT_REASON: Record<Mode, string> = { approve: "Approved after reviewing the source evidence", assign: "Reassigned", complete: "Follow-up completed", cancel: "", due: "Due date updated" };
const DAY = 86_400_000;

// The API returns user IDs and roles only, so owners are labelled by relationship/role.
function ownerLabels(members: Member[]) {
  const labels: Record<string, string> = {};
  for (const member of members) {
    const role = member.role.charAt(0).toUpperCase() + member.role.slice(1);
    const same = members.filter((item) => !item.is_current_user && item.role === member.role);
    labels[member.user_id] = member.is_current_user ? "You" : same.length > 1 ? `${role} ${same.indexOf(member) + 1}` : role;
  }
  return labels;
}

function dueWording(task: Task): { text: string; tone?: "late" | "today" } {
  const spoken = task.due_text ? ` · said “${task.due_text}”` : "";
  if (!task.due_at) return { text: `Due date unknown${spoken}` };
  const due = new Date(task.due_at);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const open = task.status === "pending_approval" || task.status === "assigned";
  if (open && due < now) {
    const days = Math.floor((today - new Date(due.getFullYear(), due.getMonth(), due.getDate()).getTime()) / DAY);
    return { text: `${days > 0 ? `${days} day${days === 1 ? "" : "s"} overdue` : "Overdue today"}${spoken}`, tone: "late" };
  }
  if (due.getTime() < today + DAY) return { text: `Due today ${due.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}${spoken}`, tone: "today" };
  return { text: `Due ${due.toLocaleDateString()}${spoken}` };
}

function TaskRow({ task, labels, onAct }: { task: Task; labels: Record<string, string>; onAct: (mode: Mode) => void }) {
  const [preview, setPreview] = useState(false);
  const due = dueWording(task);
  const start = task.source_refs.map((ref) => ref.start_seconds).find((value) => typeof value === "number");
  const open = task.status === "pending_approval" || task.status === "assigned";
  return <li className="wf-row">
    <div className="wf-row-main">
      <strong>{words(task.proposed_action)}</strong>
      <p className="wf-row-meta">
        <span className={due.tone ? `wf-due-${due.tone}` : undefined} suppressHydrationWarning>{due.text}</span>
        <span>{words(task.kind)}</span>
        <span>Owner: {task.owner_user_id ? labels[task.owner_user_id] ?? "Former member" : "Unassigned"}</span>
        <span className="status-pill">{words(task.status)}</span>
        <Link href={`/sales/calls/${task.call_id}${start !== undefined ? `?t=${Math.floor(Number(start))}` : ""}`}>{task.call_label ?? "Source call"}{start !== undefined ? ` · ${clock(start)}` : ""}</Link>
        {task.source_quote && <button type="button" className="text-button" aria-expanded={preview} onClick={() => setPreview(!preview)}>Preview</button>}
      </p>
      {preview && <blockquote className="wf-quote">{task.source_quote}</blockquote>}
      {!task.source_quote && <p className="wf-meta">Source quote unavailable (transcript changed or evidence missing).</p>}
    </div>
    {open && <div className="row-actions">
      {task.status === "pending_approval" ? <button type="button" className="primary-button" onClick={() => onAct("approve")}>Approve &amp; assign</button>
        : <><button type="button" className="primary-button" onClick={() => onAct("complete")}>Mark complete</button><button type="button" className="wf-secondary" onClick={() => onAct("assign")}>Assign</button></>}
      <button type="button" className="text-button" onClick={() => onAct("due")}>Edit due</button>
      <button type="button" className="text-button" onClick={() => onAct("cancel")}>Cancel</button>
    </div>}
  </li>;
}

export function TasksBoard({ items, members, filtered }: { items: Task[]; members: Member[]; filtered: boolean }) {
  const router = useRouter();
  const labels = ownerLabels(members);
  const me = members.find((member) => member.is_current_user)?.user_id ?? "";
  const [active, setActive] = useState<{ task: Task; mode: Mode } | null>(null);
  const [owner, setOwner] = useState(me);
  const [reason, setReason] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [dueText, setDueText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function act(task: Task, mode: Mode) {
    setActive({ task, mode }); setOwner(task.owner_user_id ?? me); setReason(DEFAULT_REASON[mode]);
    setDueAt(""); setDueText(task.due_text ?? ""); setError("");
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!active) return;
    const { task, mode } = active;
    const change = { approve: { status: "assigned", owner_user_id: owner }, assign: { owner_user_id: owner }, complete: { status: "completed" },
      cancel: { status: "cancelled" }, due: { due_at: dueAt ? new Date(dueAt).toISOString() : undefined, due_text: dueText.trim() || undefined } }[mode];
    setBusy(true); setError("");
    try {
      await send(`/tasks/${task.id}`, { expected_version: task.version, reason: reason.trim(), ...change }, "PATCH");
      setActive(null); router.refresh();
    } catch (cause) {
      const conflict = (cause as { status?: number }).status === 409;
      setError(conflict ? `${failure(cause)} The list has been refreshed; reopen the task to try again.` : failure(cause));
      if (conflict) router.refresh();
    } finally { setBusy(false); }
  }

  const mode = active?.mode;
  return <>
    {items.length ? <ul className="wf-rows">{items.map((task) => <TaskRow key={task.id} task={task} labels={labels} onAct={(next) => act(task, next)} />)}</ul>
      : <p className="wf-empty">{filtered ? "No tasks match these filters." : "No follow-ups yet. They are proposed from commitments found in analyzed calls, or created from a call's transcript."}</p>}
    <Dialog open={Boolean(active)} title={mode ? TITLES[mode] : ""} onClose={() => setActive(null)}>
      {active && <form className="wf-form" onSubmit={submit}>
        <p className="wf-meta">{active.task.proposed_action}</p>
        {(mode === "approve" || mode === "assign") && <label>Owner<select value={owner} onChange={(event) => setOwner(event.target.value)} required>
          {members.map((member) => <option key={member.user_id} value={member.user_id}>{labels[member.user_id]} ({member.role})</option>)}</select></label>}
        {mode === "due" && <>
          <label>Due date and time<input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} /></label>
          <label>Due wording<input value={dueText} maxLength={200} onChange={(event) => setDueText(event.target.value)} placeholder="e.g. next Tuesday morning" /></label>
        </>}
        <label>Reason<input value={reason} onChange={(event) => setReason(event.target.value)} required minLength={3} maxLength={1000} autoFocus /></label>
        <p className="wf-meta">Updates this workspace only — no customer message is sent.</p>
        {error && <p className="wf-error" role="alert">{error}</p>}
        <button className="primary-button" disabled={busy || (mode === "due" && !dueAt && !dueText.trim())}>{busy ? "Saving" : mode ? TITLES[mode] : "Save"}</button>
      </form>}
    </Dialog>
  </>;
}
