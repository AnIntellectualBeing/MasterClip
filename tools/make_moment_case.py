"""Make one case for the moment-picker test set (moment_eval/cases/*.json).

A case is a real stretch of a GTA stream, saved as the numbers the picker reads (loudness and
peak per second, on-screen change per 2 s, the local transcript with word times) plus the
"good moments". Marks come from people: the clips Twitch viewers made from that VOD. The video
itself is NOT kept; only a 160p slice is downloaded to measure it, then deleted.

python tools/make_moment_case.py <twitch vod id> <start s> <end s> <case id> --marks marks.json
  marks.json: [{"offset": 1234, "duration": 30, "title": "...", "views": 900}, ...]   (VOD seconds)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipstudio.engine import _motion_scores  # noqa: E402
from clipstudio.media import extract_audio, probe_media  # noqa: E402
from clipstudio.sources import run_process, ytdlp_command  # noqa: E402
from clipstudio.transcript import transcribe  # noqa: E402

CASES = ROOT / "moment_eval" / "cases"


def loudness(wav_path: Path):
    rms, peak = [], []
    with wave.open(str(wav_path), "rb") as wf:
        rate = wf.getframerate()
        while True:
            raw = wf.readframes(rate)
            if not raw:
                break
            s = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
            rms.append(round(float(np.sqrt(np.mean(s * s))), 5) if s.size else 0.0)
            peak.append(round(float(np.max(np.abs(s))), 5) if s.size else 0.0)
    return rms, peak


def merge_marks(marks, start, end):
    """Viewer clips of the same moment overlap; one moment = their union. VOD time -> case time."""
    spans = sorted(
        (max(0.0, m["offset"] - start), min(end - start, m["offset"] + m["duration"] - start), m)
        for m in marks
        if m["offset"] < end and m["offset"] + m["duration"] > start
    )
    merged = []
    for a, b, m in spans:
        if merged and a < merged[-1]["end"] - 5:
            merged[-1]["end"] = max(merged[-1]["end"], b)
            merged[-1]["clips"].append(m)
        else:
            merged.append({"start": a, "end": b, "clips": [m]})
    return [
        {
            "start": round(x["start"], 1),
            "end": round(x["end"], 1),
            # A Twitch clip records ~25 s before the viewer presses Clip and ~5 s after:
            # the moment that made them press sits 13..4 s before the clip's end.
            "payoff": [round(max(x["start"], x["end"] - 13), 1), round(x["end"] - 4, 1)],
            "viewer_clips": len(x["clips"]),
            "titles": [c["title"][:80] for c in x["clips"]],
            "views": sum(int(c.get("views") or 0) for c in x["clips"]),
        }
        for x in merged
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vod")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("case_id")
    ap.add_argument("--marks", required=True)
    ap.add_argument("--streamer", default="")
    ap.add_argument("--game", default="")
    ap.add_argument("--model", default="base")
    args = ap.parse_args()
    marks = json.loads(Path(args.marks).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="moment-case-"))
    try:
        url = f"https://www.twitch.tv/videos/{args.vod}"
        run_process(
            ytdlp_command("-f", "worst[height>=144]/worst", "--download-sections", f"*{args.start:.0f}-{args.end:.0f}",
                          "--remux-video", "mp4", "-o", str(work / "source.%(ext)s"), url),
            work / "import.log", None, timeout=1800,
        )
        video = next(work.glob("source.*"))
        info = probe_media(video)
        wav = work / "audio.wav"
        extract_audio(video, wav, timeout=1800)
        rms, peak = loudness(wav)
        motion = _motion_scores(video, max(1, -(-len(rms) // 2)), 2)
        transcript = transcribe(wav, args.model, "en")
        case = {
            "id": args.case_id,
            "source": {"site": "twitch", "vod": args.vod, "streamer": args.streamer, "game": args.game,
                       "vod_start": args.start, "vod_end": args.end},
            "marks_from": "Clips Twitch viewers made from this VOD (payoff = 13..4 s before the clip end).",
            "duration": round(info.duration, 2),
            "rms": rms,
            "peak": peak,
            "motion2": [round(float(x), 3) for x in (motion if motion is not None else [])],
            "heat": None,
            "transcript": [
                {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"],
                 "words": [{"start": round(w["start"], 2), "end": round(w["end"], 2), "text": w["text"]}
                           for w in s.get("words") or []]}
                for s in transcript
            ],
            "moments": merge_marks(marks, args.start, args.end),
        }
        CASES.mkdir(parents=True, exist_ok=True)
        (CASES / f"{args.case_id}.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        print(args.case_id, f"{info.duration:.0f}s", len(transcript), "lines", len(case["moments"]), "moments")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
