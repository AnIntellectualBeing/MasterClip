"""Caption files in and out (SRT / VTT / ASS) and safe saving of hand-edited captions.

Times inside MasterClip are always seconds of the source video. A file made for one clip
starts at 0, so importing it "for this clip" moves it to where the clip starts; exporting a
clip writes clip time (starting at 0) so it lines up with the downloaded clip.
"""

from __future__ import annotations

import math
import re

from .transcript import TranscriptError, _parse_srt, map_transcript_to_clips, parse_transcript, segments_to_srt

_VTT_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})$")
_TAG = re.compile(r"<[^>]*>")
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")


def _vtt_seconds(value: str) -> float:
    match = _VTT_TIME.fullmatch(value.strip())
    if not match:
        raise TranscriptError(f"This time isn't readable: {value.strip()!r}")
    h, m, s, ms = match.groups()
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_vtt(text: str) -> list[dict]:
    out = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [line for line in block.strip().splitlines()]
        at = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if at is None:
            continue  # WEBVTT header, NOTE, STYLE blocks
        left, right = lines[at].split("-->", 1)
        start, end = _vtt_seconds(left), _vtt_seconds(right.strip().split()[0])
        words = " ".join(_TAG.sub("", line).strip() for line in lines[at + 1:]).strip()
        if words and end > start:
            out.append({"start": start, "end": end, "text": words})
    if not out and text.strip():
        raise TranscriptError("No captions found in this VTT file.")
    return out


def _ass_seconds(value: str) -> float:
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_ass(text: str) -> list[dict]:
    """Dialogue lines of an .ass / .ssa file (styling codes removed, drawings skipped)."""
    fields = None
    out = []
    in_events = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        if line.lower().startswith("format:"):
            fields = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
            continue
        if not line.lower().startswith("dialogue:"):
            continue
        names = fields or ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
        values = line.split(":", 1)[1].split(",", len(names) - 1)
        if len(values) < len(names):
            continue
        row = dict(zip(names, values))
        body = row.get("text", "")
        if re.search(r"\\p[1-9]", body):
            continue  # a drawn shape, not words
        words = _ASS_OVERRIDE.sub("", body).replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
        words = " ".join(words.split())
        try:
            start, end = _ass_seconds(row["start"]), _ass_seconds(row["end"])
        except (KeyError, ValueError):
            continue
        if words and end > start:
            out.append({"start": start, "end": end, "text": words})
    if not out and text.strip():
        raise TranscriptError("No captions found in this ASS file.")
    out.sort(key=lambda c: (c["start"], c["end"]))
    return out


def parse_caption_file(text: str, filename: str = "") -> list[dict]:
    """SRT, VTT, ASS/SSA or MasterClip JSON -> [{start, end, text}] (times as written in the file)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    head = text.lstrip()[:200]
    if ext == "vtt" or head.upper().startswith("WEBVTT"):
        return parse_vtt(text)
    if ext in ("ass", "ssa") or "[script info]" in head.lower():
        return parse_ass(text)
    if ext == "json" or head.startswith(("{", "[")):
        return parse_transcript(text, "json")
    if ext in ("", "srt", "txt"):
        return _parse_srt(text)
    raise TranscriptError("Use an SRT, VTT or ASS caption file.")


def _vtt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


def to_vtt(segments) -> str:
    rows = ["WEBVTT", ""]
    for cue in segments:
        rows += [f"{_vtt_time(cue['start'])} --> {_vtt_time(cue['end'])}", " ".join(str(cue["text"]).split()), ""]
    return "\n".join(rows)


def export_captions(transcript, start: float, end: float, fmt: str = "srt") -> str:
    """The captions of [start, end) of the source, in clip time, as SRT or VTT text."""
    cues = map_transcript_to_clips(transcript, [dict(start=start, end=end)])[0]
    cues = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in cues if c["text"].strip()]
    if fmt == "vtt":
        return to_vtt(cues)
    if fmt == "srt":
        return segments_to_srt(cues)
    raise ValueError("Pick SRT or VTT.")


def _cut_outside(cue: dict, start: float, end: float) -> list[dict]:
    """The parts of a cue that lie outside [start, end) (a cue across the edge keeps its outer words)."""
    if cue["end"] <= start or cue["start"] >= end:
        return [cue]
    words = cue.get("words") or []
    kept = []
    for side in ([w for w in words if w["end"] <= start + 0.01], [w for w in words if w["start"] >= end - 0.01]):
        if side:
            kept.append({"start": side[0]["start"], "end": side[-1]["end"],
                         "text": " ".join(w["text"] for w in side), "words": side})
    return kept


def replace_range(transcript, start: float, end: float, new_cues) -> list[dict]:
    """The transcript with everything inside [start, end) swapped for `new_cues` (source time)."""
    out = []
    for cue in transcript:
        out.extend(_cut_outside(cue, start, end))
    for cue in new_cues:
        s, e = max(start, float(cue["start"])), min(end, float(cue["end"]))
        if e > s and str(cue.get("text", "")).strip():
            item = {"start": round(s, 3), "end": round(e, 3), "text": " ".join(str(cue["text"]).split())}
            if cue.get("words"):
                item["words"] = cue["words"]
            out.append(clean_edited(item))
    out.sort(key=lambda c: (c["start"], c["end"]))
    return out


def import_captions(transcript, cues, start: float, end: float, for_clip: bool) -> list[dict]:
    """Put imported cues into the transcript. for_clip: the file's 0:00 is the clip's start and only
    that clip's part of the transcript is replaced; otherwise the file is for the whole video."""
    if for_clip:
        moved = [{**c, "start": c["start"] + start, "end": c["end"] + start} for c in cues]
        return replace_range(transcript, start, end, moved)
    return replace_range([], 0.0, math.inf, cues)


def clean_edited(cue: dict) -> dict:
    """A hand-edited cue: keep the per-word timing when the words still match one to one
    (only spelling changed), squeezed inside the cue's times; otherwise drop it so the
    words are spread evenly over the cue."""
    start, end = float(cue["start"]), float(cue["end"])
    text = " ".join(str(cue.get("text", "")).split())
    out = {"start": start, "end": end, "text": text}
    tokens = text.split()
    words = cue.get("words") or []
    if words and len(words) == len(tokens) and end > start:
        fixed = []
        floor = start
        for token, word in zip(tokens, words):
            try:
                ws, we = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                return out
            ws = min(max(ws, floor), end - 0.01)
            we = min(max(we, ws + 0.01), end)
            fixed.append({"start": round(ws, 3), "end": round(we, 3), "text": token})
            floor = ws
        out["words"] = fixed
    return out
