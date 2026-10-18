"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { AddressCard } from "./address-card";
import { CallChat } from "./call-chat";
import { SegmentDrawer, type SegmentAction } from "./call-review";
import { Coaching, Scorecard } from "./call-scorecard";
import { CallStatus, stageText } from "./call-status";
import { clock, speakerName, words, type Detail, type Transcript } from "./call-utils";
import { DeleteCall } from "./delete-call";
import { ReviewDrawer } from "./review-drawer";

type Tab = "transcript" | "scorecard" | "coaching" | "chat";
const NARROW = "(max-width: 899px)";
const subscribe = (notify: () => void) => {
  const query = window.matchMedia(NARROW);
  query.addEventListener("change", notify);
  return () => query.removeEventListener("change", notify);
};

const segmentAt = (transcript: Transcript | null, seconds: number) =>
  transcript?.segments.find((segment) => seconds >= segment.start_seconds && seconds < segment.end_seconds)
  ?? transcript?.segments.findLast((segment) => segment.start_seconds <= seconds);

// Hidden (narrow, other tab) turns have no box, so this is a no-op until the transcript tab is shown.
const reveal = (id: string | null, block: ScrollLogicalPosition = "nearest") => {
  if (id) document.getElementById(`seg-${id}`)?.scrollIntoView({ block, inline: "nearest" });
};

export function CallInspector({ detail, transcript, isOwner, initialSeconds }: { detail: Detail; transcript: Transcript | null; isOwner: boolean; initialSeconds: number | null }) {
  const narrow = useSyncExternalStore(subscribe, () => window.matchMedia(NARROW).matches, () => false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const deepLinked = useRef(false);
  const [audioAttempt, setAudioAttempt] = useState(0);
  const [audioError, setAudioError] = useState("");
  const [active, setActive] = useState<string | null>(() => initialSeconds === null ? null : segmentAt(transcript, initialSeconds)?.id ?? null);
  const [tab, setTab] = useState<Tab>("transcript"); // desktop maps this to Scorecard (transcript is always visible)
  const [reviewOpen, setReviewOpen] = useState(false);
  const [segmentAction, setSegmentAction] = useState<SegmentAction | null>(null);
  const call = detail.call;

  // ?t=<seconds>: position the player and highlight the turn once; never autoplay.
  useEffect(() => {
    if (deepLinked.current || initialSeconds === null) return;
    deepLinked.current = true;
    const player = audioRef.current;
    if (player) {
      if (player.readyState >= 1) player.currentTime = initialSeconds;
      else player.addEventListener("loadedmetadata", () => { player.currentTime = initialSeconds; }, { once: true });
    }
    reveal(segmentAt(transcript, initialSeconds)?.id ?? null, "center");
  }, [initialSeconds, transcript]);

  const seek = useCallback((seconds: number, segmentId?: string) => {
    const id = segmentId ?? segmentAt(transcript, seconds)?.id ?? null;
    setActive(id);
    reveal(id);
    const player = audioRef.current;
    if (!player) return;
    setAudioError("");
    const play = () => { player.currentTime = seconds; player.play().catch(() => setAudioError("Press play to hear this point in the recording.")); };
    if (player.readyState >= 1) play();
    else { player.addEventListener("loadedmetadata", play, { once: true }); player.load(); }
  }, [transcript]);

  function choose(next: Tab) {
    setTab(next);
    if (next === "transcript") requestAnimationFrame(() => reveal(active));
  }

  const current: Tab = !narrow && tab === "transcript" ? "scorecard" : tab;
  const tabs: [Tab, string][] = [...(narrow ? [["transcript", "Transcript"] as [Tab, string]] : []), ["scorecard", "Scorecard"], ["coaching", "Coaching"], ["chat", narrow ? "Chat" : "Ask this call"]];
  const chatBlocked = call.index_status === "ready" ? null
    : call.index_status === "failed" ? "Chat is unavailable because chat setup failed. Use “Retry chat setup” above."
    : `Chat opens once this transcript is indexed. Current step: ${stageText(detail, "indexing").text}.`;

  return (
    <>
      <CallStatus detail={detail} />
      <div className="cd-toolbar">
        <button type="button" className="primary-button" disabled={!detail.analysis} onClick={() => setReviewOpen(true)}>Review</button>
        <span className="cd-hint">{detail.analysis ? "Correct type, outcome, rep or a criterion with a reason." : "Review opens once an analysis exists."}</span>
      </div>
      <div className="surface cd-player">
        <audio ref={audioRef} controls preload="metadata" aria-label="Original call recording"
          src={call.processing_status === "uploading" ? undefined : `/api/call-audio/${call.id}?attempt=${audioAttempt}`}
          onPlay={() => setAudioError("")} onError={() => setAudioError("The recording could not load.")} />
        {audioError ? <p role="alert" className="cd-hint">{audioError} <button type="button" className="text-button" onClick={() => { setAudioError(""); setAudioAttempt((attempt) => attempt + 1); }}>Retry audio</button></p>
          : <p className="cd-hint">Evidence and citation buttons jump to the original audio.</p>}
      </div>
      <div className="cd-grid">
        <section id="cd-panel-transcript" role={narrow ? "tabpanel" : undefined} aria-labelledby="cd-transcript-heading" className="surface cd-transcript" hidden={narrow && current !== "transcript"}>
          <div className="cd-panel-head">
            <div><p className="status-label">Source version {transcript?.version ?? "—"}{transcript && !transcript.complete_coverage ? " · partial coverage" : ""}</p><h2 id="cd-transcript-heading">Transcript</h2></div>
            <span className="cd-hint">Select a turn to play it, correct it or propose a follow-up.</span>
          </div>
          {transcript ? <ol className="cd-turns">
            {transcript.segments.map((segment) => (
              <li key={segment.id} id={`seg-${segment.id}`} className={`cd-turn${active === segment.id ? " cd-turn-active" : ""}`}>
                <button type="button" className="cd-turn-play" aria-current={active === segment.id ? "true" : undefined} onClick={() => seek(segment.start_seconds, segment.id)}>
                  <span className="cd-turn-meta"><time>{clock(segment.start_seconds)}</time><strong>{segment.speaker_role === "unknown" ? speakerName(segment.speaker_label) : words(segment.speaker_role)}{segment.role_uncertain ? " (uncertain)" : ""}</strong></span>
                  <span className="cd-turn-text">{segment.text}</span>
                </button>
                {active === segment.id && <div className="cd-turn-actions">
                  <button type="button" className="text-button" onClick={() => setSegmentAction({ kind: "edit", segment })}>Edit</button>
                  <button type="button" className="text-button" onClick={() => setSegmentAction({ kind: "follow-up", segment })}>Propose follow-up</button>
                </div>}
              </li>
            ))}
          </ol> : <p className="cd-empty">{stageText(detail, "transcription").text}. The transcript appears here when it is ready; playback works as soon as the upload finishes.</p>}
        </section>
        <section className="surface cd-analysis" aria-label="Call analysis">
          <div className="cd-tabs" role="tablist" aria-label="Call sections" onKeyDown={(event) => {
            // WAI-ARIA tabs: arrows/Home/End move between tabs; only the active tab is in the Tab order.
            const index = tabs.findIndex(([key]) => key === current);
            const next = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: tabs.length - 1 }[event.key];
            if (next === undefined) return;
            event.preventDefault();
            const [key] = tabs[(next + tabs.length) % tabs.length];
            choose(key);
            document.getElementById(`cd-tab-${key}`)?.focus();
          }}>
            {tabs.map(([key, label]) => <button key={key} type="button" role="tab" id={`cd-tab-${key}`} aria-controls={`cd-panel-${key}`} aria-selected={current === key} tabIndex={current === key ? 0 : -1} className={current === key ? "active" : ""} onClick={() => choose(key)}>{label}</button>)}
          </div>
          {/* Panels stay mounted so the chat draft and scroll position survive tab switches. */}
          <div id="cd-panel-scorecard" role="tabpanel" aria-labelledby="cd-tab-scorecard" className="cd-tabpanel" hidden={current !== "scorecard"}><Scorecard detail={detail} onSeek={seek} /></div>
          <div id="cd-panel-coaching" role="tabpanel" aria-labelledby="cd-tab-coaching" className="cd-tabpanel" hidden={current !== "coaching"}><Coaching detail={detail} transcript={transcript} onSeek={seek} /></div>
          <div id="cd-panel-chat" role="tabpanel" aria-labelledby="cd-tab-chat" className="cd-tabpanel" hidden={current !== "chat"}><CallChat callId={call.id} transcriptVersion={detail.transcript_version} disabledReason={chatBlocked} onSeek={seek} /></div>
        </section>
      </div>
      <AddressCard detail={detail} />
      {isOwner && <DeleteCall callId={call.id} />}
      {reviewOpen && detail.analysis && <ReviewDrawer detail={detail} transcript={transcript} onClose={() => setReviewOpen(false)} />}
      {segmentAction && transcript && <SegmentDrawer key={segmentAction.segment.id + segmentAction.kind} action={segmentAction} detail={detail} transcript={transcript} onClose={() => setSegmentAction(null)} />}
    </>
  );
}
