"""Resolve model citation candidates to canonical, versioned transcript segments."""

from __future__ import annotations

import re

from call_intelligence_api.call_models import TranscriptSegment


def resolve_segment(candidate: str, segments: list[TranscriptSegment]) -> TranscriptSegment | None:
    by_id = {str(segment.id): segment for segment in segments}
    if candidate in by_id:
        return by_id[candidate]
    alias = re.fullmatch(r"S(\d{1,5})", candidate, flags=re.IGNORECASE)
    if alias:
        ordinal = int(alias.group(1))
        return next((segment for segment in segments if segment.ordinal == ordinal), None)
    try:
        point = float(candidate)
    except (TypeError, ValueError):
        return None
    starts = [segment for segment in segments if abs(segment.start_seconds - point) <= 0.05]
    if len(starts) == 1:
        return starts[0]
    ends = [segment for segment in segments if abs(segment.end_seconds - point) <= 0.05]
    if len(ends) == 1:
        return ends[0]
    containing = [
        segment for segment in segments if segment.start_seconds <= point <= segment.end_seconds
    ]
    return containing[0] if len(containing) == 1 else None
