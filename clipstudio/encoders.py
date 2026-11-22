"""Pick the fastest H.264 encoder that really works on this PC.

NVIDIA (NVENC), Intel (Quick Sync) and AMD (AMF) chips encode a clip several times faster than the
CPU and leave the CPU free. Having the encoder listed in ffmpeg is not enough (no driver, too old a
card), so each one is tried once on a tiny test picture; the first that works is used for the rest
of the session. Anything failing falls back to the CPU encoder (x264), which always works.
CLIPSTUDIO_ENCODER=nvenc|qsv|amf|x264 forces one.
"""

from __future__ import annotations

import os
import subprocess
import threading

from .media import find_tool

ORDER = ("nvenc", "qsv", "amf")
_lock = threading.Lock()
_chosen: str | None = None
_broken: set[str] = set()


def _args(name: str, quality: int) -> list[str]:
    if name == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(quality + 1), "-b:v", "0"]
    if name == "qsv":
        return ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", str(quality + 2)]
    if name == "amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp",
                "-qp_i", str(quality), "-qp_p", str(quality + 2), "-qp_b", str(quality + 4)]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(quality)]


def _works(name: str) -> bool:
    command = [find_tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=0.5",
               "-pix_fmt", "yuv420p", *_args(name, 20), "-f", "null", "-"]
    try:
        run = subprocess.run(command, capture_output=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return run.returncode == 0


def encoder() -> str:
    """'nvenc', 'qsv', 'amf' or 'x264'. Tried once per session."""
    global _chosen
    with _lock:
        forced = os.environ.get("CLIPSTUDIO_ENCODER", "").strip().lower()
        if forced in (*ORDER, "x264"):
            return "x264" if forced in _broken else forced
        if _chosen is None or _chosen in _broken:
            _chosen = next((n for n in ORDER if n not in _broken and _works(n)), "x264")
        return _chosen


def video_args(quality: int = 20) -> list[str]:
    """ffmpeg arguments for the chosen encoder at about x264 CRF `quality`."""
    return _args(encoder(), quality)


def mark_broken(name: str):
    """A real render failed on this encoder: use the next one from now on."""
    with _lock:
        if name != "x264":
            _broken.add(name)


def is_hardware(args: list[str]) -> str | None:
    """Which hardware encoder these arguments use, or None for x264."""
    for name in ORDER:
        if f"h264_{name}" in args:
            return name
    return None
