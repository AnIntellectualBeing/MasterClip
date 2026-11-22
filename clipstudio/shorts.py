from __future__ import annotations

import math
from typing import Any

from .engine import Candidate
from .moments import sentences, shape_clip

_DEFAULT_LEAD_SECONDS = 1.5
_MAX_OUTSIDE_LEAD_SECONDS = 4.0


def _speech_segments(
    transcript: Any, start: float, end: float
) -> list[tuple[float, float]]:
    if not isinstance(transcript, (list, tuple)):
        return []
    result = []
    for segment in transcript:
        if not isinstance(segment, dict):
            continue
        if not str(segment.get("text", "")).strip():
            continue
        try:
            speech_start = float(segment["start"])
            speech_end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(speech_start) or not math.isfinite(speech_end):
            continue
        if speech_end > speech_start and speech_start < end and speech_end > start:
            result.append((speech_start, speech_end))
    return sorted(result)


def _window(
    candidate: Candidate, transcript: Any, duration: float, clip_length: float
) -> tuple[float, float, bool]:
    candidate_start = max(0.0, min(float(candidate.start), duration))
    candidate_end = max(candidate_start, min(float(candidate.end), duration))
    speech = _speech_segments(transcript, candidate_start, candidate_end)
    aligned = bool(speech)

    if aligned:
        first_speech = speech[0][0]
        last_payoff = max(end for _, end in speech)
        if last_payoff - first_speech <= clip_length:
            # Start shortly before the first line, but don't drag a clip far back
            # when the transcript reaches earlier than its selected moment.
            start = max(
                first_speech - _DEFAULT_LEAD_SECONDS,
                candidate_start - _MAX_OUTSIDE_LEAD_SECONDS,
            )
            if start + clip_length < last_payoff:
                start = last_payoff - clip_length
        else:
            # Speech spans longer than the short: center on the chosen moment,
            # then move forward only when needed to retain its final payoff.
            start = (candidate_start + candidate_end - clip_length) / 2
            start = max(start, last_payoff - clip_length)
            aligned = False
    else:
        start = (candidate_start + candidate_end - clip_length) / 2

    start = max(0.0, min(start, duration - clip_length))
    end = min(duration, start + clip_length)
    return start, end, aligned


def select_shorts(
    candidates: list[Candidate],
    transcript: Any,
    duration: float,
    length: float = 30,
    count: int = 3,
) -> list[Candidate]:
    """Select ranked, nonoverlapping short windows, leading into nearby dialogue when possible."""
    if length <= 0:
        raise ValueError("Short length must be positive.")
    if count <= 0 or duration <= 0 or not candidates:
        return []
    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        return []
    clip_length = min(float(length), duration)
    sents = sentences(transcript) if isinstance(transcript, (list, tuple)) else []
    chosen = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if len(chosen) >= count:
            break
        if candidate.core_start is not None and candidate.core_end is not None:
            # About 8 s of setup and 10 s of reaction around the event, cut between sentences.
            start, end = shape_clip(candidate.core_start, candidate.core_end, sents, duration, clip_length)
            aligned = False
        else:
            start, end, aligned = _window(candidate, transcript, duration, clip_length)
        if end <= start or any(
            start < selected.end and end > selected.start for selected in chosen
        ):
            continue
        reasons = list(candidate.reasons or [])
        if aligned:
            reasons.append("speech lead-in")
        chosen.append(Candidate(start, end, candidate.score, reasons, candidate.core_start, candidate.core_end))
    return chosen
