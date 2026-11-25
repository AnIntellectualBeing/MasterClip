"""Free chatbot helper for long videos: no AI key needed.

The transcript is cut into parts of about 25 minutes. Each part gets a ready prompt the buyer
copies into any free chatbot (ChatGPT, Gemini, Claude, Copilot...). The chatbot answers with
moments, the buyer pastes the answers back, and MasterClip makes clips of the ticked ones.
Answers may be JSON or simple lines like "12:30-13:10 Cops chase him into the river".
"""

from __future__ import annotations

import json
import re

PART_SECONDS = 25 * 60


def clock(seconds: float) -> str:
    s = max(0, int(round(float(seconds))))
    return f"{s // 3600}:{s % 3600 // 60:02}:{s % 60:02}" if s >= 3600 else f"{s // 60}:{s % 60:02}"


def split_parts(transcript, part_seconds: float = PART_SECONDS, duration: float | None = None) -> list[dict]:
    """[{start, end, lines: ["12:30 words"...]}] cut between caption lines, about part_seconds each."""
    cues = sorted((c for c in transcript if str(c.get("text", "")).strip()), key=lambda c: float(c["start"]))
    parts: list[dict] = []
    current = None
    for cue in cues:
        start = float(cue["start"])
        if current is None or start - current["start"] >= part_seconds:
            current = {"start": 0.0 if not parts else start, "end": start, "lines": []}
            parts.append(current)
        current["lines"].append(f"{clock(start)} {' '.join(str(cue['text']).split())}")
        current["end"] = max(current["end"], float(cue["end"]))
    for a, b in zip(parts, parts[1:]):
        a["end"] = b["start"]
    if parts and duration:
        parts[-1]["end"] = max(parts[-1]["end"], float(duration))
    return parts


def part_prompt(part: dict, number: int, total: int, length: int = 30, context: str = "") -> str:
    about = f" The stream is about: {context.strip()}." if context.strip() else ""
    return (
        f"Below is part {number} of {total} of a live stream transcript ({clock(part['start'])} to "
        f"{clock(part['end'])}). Each line starts with the time it was said.{about}\n"
        f"Find the best moments for short vertical clips (about {length} seconds each): funny, surprising, "
        "dramatic or a clear little story. Only pick what the words really show.\n"
        "Answer with one moment per line, nothing else, in this exact form:\n"
        "START-END short title\n"
        f"for example: {clock(part['start'] + 75)}-{clock(part['start'] + 75 + length)} He crashes the police car\n\n"
        "Transcript:\n" + "\n".join(part["lines"])
    )


def _seconds(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,]\d+)?", text)
    if not match:
        return None
    h, m, s = match.groups()
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


_LINE = re.compile(
    r"(?P<a>(?:\d+:)?\d{1,2}:\d{2})\s*(?:-|–|—|to)\s*(?P<b>(?:\d+:)?\d{1,2}:\d{2})\s*[:\-–—|)\]]*\s*(?P<title>.*)",
    re.I,
)


def parse_answer(text: str, duration: float | None = None, max_len: float = 180) -> list[dict]:
    """Moments from pasted chatbot answers -> [{start, end, title}] sorted, without repeats."""
    found = []
    raw = (text or "").strip()
    data = None
    if raw[:1] in "[{" or "```" in raw:
        body = raw.replace("```json", "").replace("```", "")
        lo = min([i for i in (body.find("["), body.find("{")) if i >= 0], default=-1)
        hi = max(body.rfind("]"), body.rfind("}"))
        if lo >= 0 and hi > lo:
            try:
                data = json.loads(body[lo: hi + 1])
            except ValueError:
                data = None
    if data is not None:
        rows = data.get("moments", data.get("clips", [])) if isinstance(data, dict) else data
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            a, b = _seconds(row.get("start", "")), _seconds(row.get("end", ""))
            if a is not None and b is not None:
                found.append((a, b, str(row.get("title") or row.get("name") or "")))
    else:
        for line in raw.splitlines():
            match = _LINE.search(line)
            if match:
                a, b = _seconds(match["a"]), _seconds(match["b"])
                title = match["title"].strip().strip("\"'*").strip()
                if a is not None and b is not None:
                    found.append((a, b, title))
    out, seen = [], set()
    for a, b, title in found:
        if duration:
            b = min(b, float(duration))
        if b - a < 3 or a < 0 or b - a > max_len:
            continue
        key = (round(a), round(b))
        if key in seen:
            continue
        seen.add(key)
        out.append({"start": float(a), "end": float(b), "title": " ".join(title.split())[:90]})
    out.sort(key=lambda m: m["start"])
    return out
