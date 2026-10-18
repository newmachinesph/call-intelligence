"use client";

import "driver.js/dist/driver.css";

import { driver, type DriveStep } from "driver.js";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect } from "react";

type Step = { element: string; title: string; description: string };

// Per-screen walkthroughs anchored on existing class names. Steps whose element is not on screen are skipped.
const tours: Record<string, Step[]> = {
  home: [
    { element: ".primary-nav", title: "Your workspace", description: "Home is the owner overview. Sales holds uploads and the Call Log. Tasks, Team and Assistant are built from processed calls." },
    { element: ".sidebar-search", title: "Find a call", description: "Search the Call Log from anywhere." },
    { element: ".metric-grid", title: "Metrics from analyzed calls", description: "Every number counts analyzed calls only and says what it is based on. Calls that are not processed yet are not counted." },
    { element: "section[aria-labelledby='call-log-links']", title: "Call Log shortcuts", description: "Jump straight to calls that need review or are missing a call date or rep." },
    { element: "section[aria-labelledby='follow-ups-heading']", title: "Follow-ups due", description: "Promises made on calls, with the due date the customer heard. Nothing is ever sent automatically." },
    { element: ".bell", title: "Notifications", description: "Shows calls being processed and tells you when each one finishes or fails. Open pages update on their own." },
    { element: ".primary-nav a[href='/sales']", title: "Start here", description: "Open Sales, then Call Log, to upload recordings and process them." },
  ],
  sales: [
    { element: ".page-tabs", title: "Sales sections", description: "Overview shows outcomes by call type, Pipeline shows local opportunities, and Call Log lists every call." },
    { element: ".metric-grid", title: "Outcomes", description: "Counts come from analyzed calls. Open a card to see the calls behind it." },
  ],
  calls: [
    { element: ".call-table-surface .primary-button", title: "Upload calls", description: "Add MP3 recordings (up to 25 MB each). Uploading only stores the file; nothing is transcribed or scored yet." },
    { element: ".process-bar", title: "Process when ready", description: "Processing transcribes, classifies and scores each call. It takes a few minutes per call, and the status updates on its own." },
    { element: ".call-filters", title: "Filter", description: "Narrow the list by text, type, outcome, grade, rep or date." },
    { element: ".call-table-surface table", title: "Open a call", description: "Select a call to see its transcript, score, grade and coaching." },
  ],
  pipeline: [
    { element: ".page-tabs", title: "Local pipeline", description: "Opportunities created from calls. Changes are audited, and Won here does not prove payment or a booked appointment." },
  ],
  call: [
    { element: ".cd-facts-bar", title: "At a glance", description: "Call type, outcome, rep and rubric score with grade. \"Reviewed\" marks a human correction." },
    { element: ".cd-card", title: "Summary and facts", description: "What happened on the call and the details extracted from it. Check facts against the recording before using them." },
    { element: ".cd-toolbar", title: "Review", description: "Correct the type, outcome, rep or a single criterion, with a reason. Every change is versioned and audited, and correcting the type re-scores with the right rubric." },
    { element: ".cd-player", title: "Original recording", description: "Every evidence button jumps here to the exact moment." },
    { element: ".cd-transcript", title: "Transcript", description: "Speaker-labeled transcript. Play from any line." },
    { element: ".cd-analysis", title: "Scorecard, coaching and questions", description: "Each scorecard item links to the audio that proves it. Coaching gives one strength and up to three improvements. Ask questions about this call and get cited answers." },
  ],
  tasks: [
    { element: ".wf-card", title: "Follow-ups", description: "Created from promises made on calls, each linked to its evidence. Approving one never sends a message." },
  ],
  team: [
    { element: "section[aria-labelledby='reps-heading']", title: "Reps", description: "Each rep's calls, scores and coaching patterns. Open a rep for their profile." },
  ],
  assistant: [
    { element: ".wf-assistant", title: "Ask across calls", description: "Ask about many calls at once. Counts come from exact database queries, and answers cite the calls they rely on." },
  ],
};

function tourKey(pathname: string, view: string | null): string | null {
  if (pathname === "/") return "home";
  if (pathname.startsWith("/sales/calls/")) return "call";
  if (pathname === "/sales") return view === "calls" || view === "pipeline" ? view : "sales";
  const key = pathname.slice(1);
  return key in tours ? key : null;
}

function visibleSteps(key: string): DriveStep[] {
  return tours[key].flatMap(({ element, title, description }) => {
    const node = document.querySelector(element);
    return node?.checkVisibility({ visibilityProperty: true }) ? [{ element: node, popover: { title, description } }] : [];
  });
}

function start(key: string) {
  const steps = visibleSteps(key);
  if (steps.length) driver({ steps, showProgress: steps.length > 1, popoverClass: "tour-popover", doneBtnText: "Done" }).drive();
}

const seenKey = (key: string) => `tour-seen:${key}`;

/** Runs a screen's walkthrough once per browser; the help button replays it. */
export function PageTour() {
  const pathname = usePathname();
  const view = useSearchParams().get("view");
  const key = tourKey(pathname, view);
  useEffect(() => {
    if (!key) return;
    try {
      if (localStorage.getItem(seenKey(key))) return;
      localStorage.setItem(seenKey(key), "1");
    } catch {
      return; // storage blocked: never auto-start, the button still works
    }
    const timer = window.setTimeout(() => start(key), 500); // let the page paint first
    return () => window.clearTimeout(timer);
  }, [key]);

  if (!key) return null;
  return <button type="button" className="icon-button" aria-label="Tour this page" title="Tour this page" onClick={() => start(key)}>
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.3 2.4c-.5.2-.8.6-.8 1.1v.5M12 17h.01" /></svg>
  </button>;
}
