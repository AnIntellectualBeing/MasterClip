"""The moment picker as it was before the rule-based picker (commit b8e7fac), frozen for the
before/after score in tools/score_moments.py. Do not "fix" this file: it is the baseline."""
# ruff: noqa
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class Candidate:
    start: float
    end: float
    score: float = 0.0
    reasons: list | None = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


def score_windows(
    rms_values,
    crest_values,
    window_seconds=2.0,
    transcript_segments=None,
    motion_values=None,
):
    rms = np.asarray(rms_values, dtype=float)
    crest = np.asarray(crest_values, dtype=float)

    def norm(a):
        lo, hi = float(np.percentile(a, 10)), float(np.percentile(a, 90))
        return np.zeros_like(a) if hi <= lo else np.clip((a - lo) / (hi - lo), 0, 1)

    energy, dynamics = norm(rms), norm(crest)
    motion = (
        norm(np.asarray(motion_values, dtype=float))
        if motion_values is not None
        else np.zeros_like(energy)
    )
    segments = transcript_segments or []
    keywords = (
        "mission",
        "wanted",
        "police",
        "cop",
        "chase",
        "kill",
        "died",
        "crash",
        "explosion",
        "heist",
        "fuck",
        "shit",
        "bro",
        "arey",
        "arre",
        "kya",
        "bachao",
    )
    out = []
    for i, (e, d) in enumerate(zip(energy, dynamics)):
        start = i * window_seconds
        end = start + window_seconds
        relevance = 0
        for seg in segments:
            text = str(seg.get("text", "")).lower()
            if (
                float(seg.get("start", end + 1)) < end
                and float(seg.get("end", start - 1)) > start
            ):
                relevance += sum(1 for k in keywords if k in text)
        reasons = []
        if e > 0.35:
            reasons.append("high audio energy")
        if d > 0.35:
            reasons.append("dynamic audio")
        if motion[i] > 0.35:
            reasons.append("visual motion")
        if relevance:
            reasons.append("GTA-relevant transcript keywords")
        out.append(
            Candidate(
                start,
                end,
                round(
                    float(
                        0.45 * e
                        + 0.3 * d
                        + 0.25 * motion[i]
                        + min(relevance * 0.1, 0.5)
                    ),
                    4,
                ),
                reasons,
            )
        )
    return sorted(out, key=lambda c: (c.score, c.start), reverse=True)


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
    chosen = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if len(chosen) >= count:
            break
        start, end, aligned = _window(candidate, transcript, duration, clip_length)
        if end <= start or any(
            start < selected.end and end > selected.start for selected in chosen
        ):
            continue
        reasons = list(candidate.reasons or [])
        if aligned:
            reasons.append("speech lead-in")
        chosen.append(Candidate(start, end, candidate.score, reasons))
    return chosen


def candidates_from_features(rms1, peak1, motion2, duration, transcript, context_seconds=28):
    """analyze_media's ranking step, fed from saved per-second numbers instead of a file."""
    n = max(1, math.ceil(len(rms1) / 2))
    rms, crest = [], []
    for i in range(n):
        a = rms1[2 * i:2 * i + 2]
        p = peak1[2 * i:2 * i + 2]
        r = float(np.sqrt(np.mean(np.square(a)))) if a else 0.0
        rms.append(r)
        crest.append(float(max(p) / (r + 1e-6)) if p else 0.0)
    motion = motion2[:n] + [0.0] * (n - len(motion2)) if motion2 else None
    scored = score_windows(rms, crest, 2, transcript, motion)
    ranked = []
    for c in scored:
        if c.score <= 0:
            continue
        half = context_seconds / 2
        ranked.append(Candidate(max(0, c.start - half), min(duration, c.end + half), c.score, c.reasons))
    out = []
    for c in ranked:
        if not any(c.start < o.end and c.end > o.start for o in out):
            out.append(c)
    return out


def pick(case, length=30, count=5):
    cands = candidates_from_features(case["rms"], case["peak"], case["motion2"], case["duration"], case["transcript"])
    return select_shorts(cands, case["transcript"], case["duration"], length=length, count=count)


def second_scores(case):
    """The old per-window score spread to one value per second (for the ranking metric only)."""
    rms1, peak1 = case["rms"], case["peak"]
    n = max(1, math.ceil(len(rms1) / 2))
    rms, crest = [], []
    for i in range(n):
        a, p = rms1[2 * i:2 * i + 2], peak1[2 * i:2 * i + 2]
        r = float(np.sqrt(np.mean(np.square(a)))) if a else 0.0
        rms.append(r)
        crest.append(float(max(p) / (r + 1e-6)) if p else 0.0)
    motion = case["motion2"][:n] + [0.0] * (n - len(case["motion2"])) if case["motion2"] else None
    out = np.zeros(n * 2)
    for c in score_windows(rms, crest, 2, case["transcript"], motion):
        out[int(c.start):int(c.end)] = c.score
    return out[: int(math.ceil(case["duration"]))]
