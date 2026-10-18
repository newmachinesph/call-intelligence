"use client";

import { clock, evidenceOf, words, type Detail, type Seek, type Transcript } from "./call-utils";

const STATE_WORDS: Record<string, string> = {
  met: "Met", missed: "Missed", auto_credit: "Automatic credit", not_applicable: "Not applicable", not_observable: "Not observable",
};

export function EvidenceButtons({ refs, version, onSeek }: { refs: Record<string, unknown>[]; version: number | null; onSeek: Seek }) {
  const items = refs.map((ref) => evidenceOf(ref, version)).filter((item) => item !== null);
  if (!items.length) return null;
  return (
    <div className="cd-evidence">
      {items.map((item, index) => item === "stale"
        ? <span key={index} className="cd-chip-muted">Source from an earlier transcript version</span>
        : <button key={index} type="button" className="cd-chip" title={item.quote}
          aria-label={`Play evidence at ${clock(item.seconds)}${item.quote ? `: ${item.quote}` : ""}`}
          onClick={() => onSeek(item.seconds, item.segmentId)}>▶ {clock(item.seconds)}</button>)}
    </div>
  );
}

function Updating({ stale }: { stale: boolean }) {
  return stale ? <p className="cd-updating" role="status">Updating — showing previous analysis</p> : null;
}

export function Scorecard({ detail, onSeek }: { detail: Detail; onSeek: Seek }) {
  const analysis = detail.analysis;
  if (!analysis?.rubric_id) return <p className="cd-empty">The scorecard appears after analysis. Calls that are not conversations or have no matching rubric stay ungraded.</p>;
  const provisional = !analysis.grade;
  return (
    <div>
      <Updating stale={detail.analysis_stale} />
      <div className="cd-score">
        <p className="status-label">{analysis.rubric_id} rubric · version {analysis.rubric_version}</p>
        <strong>{analysis.confirmed_points ?? 0}<span> / {analysis.nominal_maximum ?? "—"}</span></strong>
        <p>{provisional
          ? `Provisional: ${analysis.confirmed_points ?? 0} confirmed points, ${analysis.unresolved_count ?? 0} unresolved ${analysis.unresolved_count === 1 ? "criterion" : "criteria"}. No official band until they are resolved.`
          : `AI assessment band: ${words(analysis.grade)}`}</p>
      </div>
      <ul className="cd-criteria">
        {analysis.criteria.map((criterion) => (
          <li key={criterion.criterion_id} className="cd-criterion">
            <div className="cd-criterion-head">
              <strong>{words(criterion.criterion_id.split(".").at(-1))}</strong>
              <span className={`cd-state cd-state-${criterion.state}`}>{STATE_WORDS[criterion.state] ?? words(criterion.state)} · {criterion.points === null ? "no points yet" : `${criterion.points} pt`}</span>
            </div>
            <p>{criterion.rationale}</p>
            {criterion.credit_reason && <p className="cd-credit">Credit reason: {criterion.credit_reason}</p>}
            <EvidenceButtons refs={criterion.evidence_refs} version={detail.transcript_version} onSeek={onSeek} />
          </li>
        ))}
      </ul>
    </div>
  );
}

type CoachingItem = { observation?: string; evidence_segment_ids?: string[]; suggested_wording?: string | null };

export function Coaching({ detail, transcript, onSeek }: { detail: Detail; transcript: Transcript | null; onSeek: Seek }) {
  const coaching = detail.analysis?.coaching;
  if (!coaching) return <p className="cd-empty">Coaching appears when an analysis is ready.</p>;
  const segments = transcript?.segments ?? [];
  // Coaching may cite segment UUIDs or ordinal aliases such as "S0007" (same resolution as the API's evidence.py).
  const resolve = (id: string) => segments.find((segment) => segment.id === id)
    ?? segments.find((segment) => /^S\d{1,5}$/i.test(id) && segment.ordinal === Number(id.slice(1)));
  const strength = coaching.strength as CoachingItem | null | undefined;
  const improvements = (Array.isArray(coaching.improvements) ? coaching.improvements as CoachingItem[] : []).slice(0, 3);
  const evidence = (ids: string[] | undefined) => {
    const found = (ids ?? []).map(resolve).filter((segment) => segment !== undefined);
    return found.length ? <div className="cd-evidence">{found.map((segment) =>
      <button key={segment.id} type="button" className="cd-chip" aria-label={`Play evidence at ${clock(segment.start_seconds)}: ${segment.text}`}
        onClick={() => onSeek(segment.start_seconds, segment.id)}>▶ {clock(segment.start_seconds)}</button>)}</div> : null;
  };
  return (
    <div>
      <Updating stale={detail.analysis_stale} />
      {strength?.observation && <article className="cd-coach"><p className="status-label">Observed strength</p><p>{strength.observation}</p>{evidence(strength.evidence_segment_ids)}</article>}
      {improvements.map((item, index) => (
        <article key={index} className="cd-coach">
          <p className="status-label">Improvement {index + 1} of {improvements.length}</p>
          <p>{item.observation}</p>
          {item.suggested_wording && <p className="cd-suggestion"><strong>Suggestion (not said on the call):</strong> “{item.suggested_wording.replace(/^["“]|["”]$/g, "")}”</p>}
          {evidence(item.evidence_segment_ids)}
        </article>
      ))}
      {!strength?.observation && !improvements.length && <p className="cd-empty">No coaching items were produced for this call.</p>}
    </div>
  );
}
