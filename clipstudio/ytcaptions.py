"""YouTube's own word-timed captions as the transcript: no speech model, about a second for any length.

yt-dlp's metadata lists the caption tracks of a video. YouTube's automatic captions in the spoken
language come with a time for every word (json3 format), which is all the picker and the burned-in
captions need. Running Whisper on a CPU takes minutes for the same 10 minutes of stream.
Idea from NeonClipping and PeakCut (both MIT, credited in NOTICE); this file is our own code.

Only English or the language the buyer picked is used: other languages (Hindi comes as Devanagari)
still go through the local speech model so captions look the way the buyer expects.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TIMEOUT = 30
MAX_BYTES = 40 * 1024 * 1024
_NOISE = re.compile(r"^[\[(].*[\])]$")  # [Music], [Applause], (laughs)


def pick_track(metadata: dict, wanted: str | None = None) -> tuple[str, str] | None:
    """(language, json3 url) of the best caption track in the spoken language, or None.
    `wanted` = the language the buyer chose ('en', 'hi', ...); None/'auto' = English only."""
    auto = metadata.get("automatic_captions") or {}
    manual = metadata.get("subtitles") or {}
    spoken = str(metadata.get("language") or "").split("-")[0].lower()
    allowed = {wanted.lower()} if wanted and wanted != "auto" else {"en"}

    def url_of(group, key):
        for fmt in group.get(key) or []:
            if isinstance(fmt, dict) and fmt.get("ext") == "json3" and _youtube(fmt.get("url")):
                return fmt["url"]
        return None

    # Automatic captions in the spoken language are the word-timed ones; translated tracks are not.
    keys = [k for k in auto if k.endswith("-orig")]
    if spoken:
        keys = [f"{spoken}-orig", spoken] + keys
    for key in keys:
        lang = key.replace("-orig", "").split("-")[0].lower()
        if lang in allowed and (not spoken or lang == spoken) and (url := url_of(auto, key)):
            return lang, url
    # A track written by the uploader in the spoken language (no word times: words are spread).
    for key in manual:
        lang = key.split("-")[0].lower()
        if lang in allowed and (not spoken or lang == spoken) and (url := url_of(manual, key)):
            return lang, url
    return None


def _youtube(url) -> bool:
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    host = (urlparse(url).hostname or "").lower()
    return host == "youtube.com" or host.endswith(".youtube.com")


def parse_json3(data: dict, offset: float = 0.0, duration: float | None = None) -> list[dict]:
    """json3 caption events -> transcript segments [{start, end, text, words}] with times relative
    to `offset` (the start of the downloaded part), keeping only [0, duration)."""
    words = []
    for ev in data.get("events") or []:
        segs = [s for s in ev.get("segs") or [] if str(s.get("utf8") or "").strip()]
        if not segs:
            continue
        t0 = float(ev.get("tStartMs") or 0) / 1000
        length = float(ev.get("dDurationMs") or 0) / 1000
        timed = len(segs) > 1 or "tOffsetMs" in segs[0]
        line = [(s, w) for s in segs for w in str(s["utf8"]).split()
                if not _NOISE.match(w) and w not in {"[", "]", ">>", "-", "__"}]
        for n, (s, w) in enumerate(line):
            if timed:
                # words sharing one timed piece ("right, we're") keep their order, 0.1 s apart
                k = sum(1 for prev, _ in line[:n] if prev is s)
                at = t0 + float(s.get("tOffsetMs") or 0) / 1000 + k * 0.1
            else:
                at = t0 + length * n / max(1, len(line))
            words.append([at - offset, w, len(words)])
    words.sort(key=lambda x: (x[0], x[2]))
    words = [[at, w] for at, w, _ in words]
    out_words = []
    for i, (at, w) in enumerate(words):
        nxt = words[i + 1][0] if i + 1 < len(words) else at + 0.5
        end = max(at + 0.1, min(nxt, at + 0.7))
        if at < 0 or (duration is not None and at >= duration):
            continue
        out_words.append({"start": round(at, 3), "end": round(end if duration is None else min(end, duration), 3), "text": w})
    # one segment per run of speech: split at a pause of 0.8 s or after 14 words
    segments, run = [], []
    for w in out_words:
        if run and (w["start"] - run[-1]["end"] >= 0.8 or len(run) >= 14):
            segments.append(run)
            run = []
        run.append(w)
    if run:
        segments.append(run)
    return [{"start": r[0]["start"], "end": r[-1]["end"], "text": " ".join(x["text"] for x in r), "words": r} for r in segments]


def fetch(metadata: dict, offset: float = 0.0, duration: float | None = None, wanted: str | None = None) -> dict | None:
    """{'language', 'segments'} from YouTube's captions for the downloaded part, or None."""
    track = pick_track(metadata, wanted)
    if not track:
        return None
    lang, url = track
    try:
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=TIMEOUT) as response:
            data = json.loads(response.read(MAX_BYTES).decode("utf-8"))
    except (OSError, ValueError):
        return None
    segments = parse_json3(data, offset, duration)
    if sum(len(s["words"]) for s in segments) < 5:
        return None
    return {"language": lang, "segments": segments}
