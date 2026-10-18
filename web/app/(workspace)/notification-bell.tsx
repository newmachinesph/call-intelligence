"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import type { components } from "@/contracts/api";

type Activity = components["schemas"]["ActivityResponse"];

const SEEN_KEY = "bell-seen-at";

function readSeen(): string | null {
  try { return localStorage.getItem(SEEN_KEY); } catch { return null; }
}

function writeSeen(value: string) {
  try { localStorage.setItem(SEEN_KEY, value); } catch { /* per-browser convenience only */ }
}

function ago(iso: string) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : new Date(iso).toLocaleDateString();
}

/** Processing notifications: polls while calls are processing, refreshes the page when one finishes. */
export function NotificationBell() {
  const router = useRouter();
  const [activity, setActivity] = useState<Activity | null>(null);
  // Only affects the badge, which needs fetched activity, so server and first client render match.
  const [seenAt, setSeenAt] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const seen = readSeen() ?? new Date().toISOString(); // first visit: nothing is "new" yet
    writeSeen(seen);
    return seen;
  });
  const [open, setOpen] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const signature = useRef<string | null>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    const response = await fetch("/api/domain/calls/activity", { cache: "no-store" }).catch(() => null);
    if (!response?.ok) return;
    const data: Activity = await response.json();
    const next = `${data.processing}|${data.recent.map((item) => item.id + item.status).join(",")}`;
    if (signature.current !== null && signature.current !== next) {
      const previous = signature.current;
      const finished = data.recent.find((item) => !previous.includes(item.id + item.status));
      if (finished) setAnnouncement(`${finished.label} ${finished.status === "complete" ? "processed" : "failed"}`);
      router.refresh(); // statuses on the current page catch up without a reload
    }
    signature.current = next;
    setActivity(data);
  }, [router]);

  useEffect(() => {
    const first = window.setTimeout(load, 0);
    const timer = window.setInterval(load, activity?.processing ? 8000 : 60000);
    window.addEventListener("focus", load);
    return () => { window.clearTimeout(first); window.clearInterval(timer); window.removeEventListener("focus", load); };
  }, [load, activity?.processing]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); button.current?.focus(); } };
    const onClick = (event: MouseEvent) => { if (!wrapper.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onClick); };
  }, [open]);

  const unread = activity && seenAt ? activity.recent.filter((item) => item.finished_at > seenAt).length : 0;
  const processing = activity?.processing ?? 0;
  const label = `Notifications${unread ? `, ${unread} new` : ""}${processing ? `, ${processing} processing` : ""}`;

  function toggle() {
    if (!open && activity?.recent[0]) {
      const latest = activity.recent[0].finished_at;
      if (!seenAt || latest > seenAt) { writeSeen(latest); setSeenAt(latest); }
    }
    setOpen(!open);
  }

  return <div className="bell" ref={wrapper}>
    <button ref={button} type="button" className="icon-button" aria-label={label} title="Notifications" aria-expanded={open} aria-controls="bell-panel" onClick={toggle}>
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0" /></svg>
      {unread > 0 ? <span className="bell-badge" aria-hidden="true">{unread}</span> : processing > 0 && <span className="bell-dot" aria-hidden="true" />}
    </button>
    <p className="sr-only" role="status">{announcement}</p>
    {open && <div className="bell-panel surface" id="bell-panel" role="region" aria-label="Notifications">
      <p className="bell-title">Notifications</p>
      {processing > 0 && <Link className="bell-row bell-processing" href="/sales?view=calls" onClick={() => setOpen(false)}>
        <span className="bell-spinner" aria-hidden="true" />{processing} call{processing === 1 ? " is" : "s are"} processing. Two run at a time; the rest wait in the queue.
      </Link>}
      {activity?.recent.length ? <ul>
        {activity.recent.map((item) => <li key={item.id}>
          <Link className="bell-row" href={`/sales/calls/${item.id}`} onClick={() => setOpen(false)}>
            <span className={`bell-status bell-${item.status}`} aria-hidden="true" />
            <span><strong>{item.label}</strong> {item.status === "complete" ? "processed" : "failed. Open it to retry."}<small>{ago(item.finished_at)}</small></span>
          </Link>
        </li>)}
      </ul> : processing === 0 && <p className="bell-empty">No processed calls yet. Upload calls and press Process in the Call Log.</p>}
    </div>}
  </div>;
}
