"""Add the extra signals (voice 2-4 kHz share, police-light strobe, flash) to every test case.

Downloads the same 160p slice again, measures it, saves only the numbers, deletes the slice.
python tools/add_case_signals.py [case_id ...]
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipstudio.media import extract_audio, probe_media  # noqa: E402
from clipstudio.signals import action_cues, voice_share  # noqa: E402
from clipstudio.sources import run_process, ytdlp_command  # noqa: E402

CASES = ROOT / "moment_eval" / "cases"


def main():
    wanted = set(sys.argv[1:])
    for path in sorted(CASES.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if wanted and case["id"] not in wanted:
            continue
        if "voice" in case and not wanted:
            print(case["id"], "already has signals")
            continue
        src = case["source"]
        work = Path(tempfile.mkdtemp(prefix="moment-signals-"))
        t = time.time()
        try:
            run_process(
                ytdlp_command("-f", "worst[height>=144]/worst", "--download-sections",
                              f"*{src['vod_start']:.0f}-{src['vod_end']:.0f}", "--remux-video", "mp4",
                              "-o", str(work / "source.%(ext)s"), f"https://www.twitch.tv/videos/{src['vod']}"),
                work / "import.log", None, timeout=1800,
            )
            video = next(work.glob("source.*"))
            info = probe_media(video)
            wav = work / "audio.wav"
            extract_audio(video, wav, timeout=1800)
            n = len(case["rms"])
            voice = voice_share(wav)[:n]
            cues = action_cues(video, info.duration)
            case["voice"] = voice + [0.0] * (n - len(voice))
            case["strobe"] = (cues["strobe"] + [0.0] * n)[:n]
            case["flash"] = (cues["flash"] + [0.0] * n)[:n]
            case["signals_note"] = f"slice {info.duration:.1f}s vs case {case['duration']}s"
            path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            print(case["id"], f"{time.time() - t:.0f}s", f"slice {info.duration:.0f}s / case {case['duration']:.0f}s",
                  "strobe>0:", sum(1 for x in case["strobe"] if x > 0), "flash>0:", sum(1 for x in case["flash"] if x > 0), flush=True)
        except Exception as exc:  # keep going with the other cases
            print(case["id"], "FAILED", str(exc)[:200], flush=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
