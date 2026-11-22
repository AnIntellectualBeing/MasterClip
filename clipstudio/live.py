"""Rolling live capture.

The stream is recorded into ONE growing MPEG-TS file (readable while it is still being written).
Every few minutes the newest window is cut out at low resolution for analysis, and clips are
rendered straight from the growing file with whole-stream times. When the stream ends or the
buyer presses Stop, the .ts is remuxed to .mp4 so the editor and long videos work on it.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .media import find_tool
from .sources import (
    MediaError,
    _format_for_url,
    _input_args,
    _request_headers,
    child_env,
)

WINDOW_SECONDS = 60
MAX_SECONDS = None  # Continuous means until Stop or the stream ends.
KEYFRAME_SECONDS = 2


class LiveRecorder:
    """ffmpeg recording to `folder/source.ts`. `inputs` is the ffmpeg input part of the command."""

    def __init__(self, inputs: list[str], audio_map: str, folder, max_seconds=MAX_SECONDS):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.target = self.folder / "source.ts"
        self.progress_file = self.folder / "record-progress.txt"
        self.inputs = inputs
        self.audio_map = audio_map
        self.max_seconds = int(max_seconds) if max_seconds else None
        self.proc = None
        self.log = None
        self.started = 0.0

    @classmethod
    def from_stream(cls, video_url, audio_url, metadata, folder, max_seconds=MAX_SECONDS):
        inputs = _input_args(video_url, _request_headers(metadata, _format_for_url(metadata, video_url)))
        if audio_url:
            inputs += _input_args(audio_url, _request_headers(metadata, _format_for_url(metadata, audio_url)))
        return cls(inputs, "1:a:0" if audio_url else "0:a:0?", folder, max_seconds)

    @classmethod
    def from_file(cls, path, folder, max_seconds=MAX_SECONDS):
        """Plays a local file at real speed, like a live stream (used by tests and QA)."""
        return cls(["-re", "-i", str(path)], "0:a:0?", folder, max_seconds)

    def command(self) -> list[str]:
        return [
            find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            *self.inputs,
            *(["-t", str(self.max_seconds)] if self.max_seconds else []),
            "-map", "0:v:0", "-map", self.audio_map,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-force_key_frames", f"expr:gte(t,n_forced*{KEYFRAME_SECONDS})",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-f", "mpegts", "-flush_packets", "1",
            "-progress", str(self.progress_file), "-stats_period", "1",
            str(self.target),
        ]

    def start(self):
        self.progress_file.unlink(missing_ok=True)
        self.log = open(self.folder / "record.log", "w", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(
                self.command(), stdin=subprocess.PIPE, stdout=self.log, stderr=self.log,
                env=child_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self.log.close()
            raise MediaError("The recorder couldn't start. Reinstall Clip Studio.") from error
        self.started = time.monotonic()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def written(self) -> float:
        """Seconds of video safely written so far."""
        try:
            text = self.progress_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return 0.0
        value = 0.0
        for line in text.splitlines():
            if line.startswith("out_time_us="):
                try:
                    value = max(value, int(line.split("=", 1)[1]) / 1_000_000)
                except ValueError:
                    pass
        # the muxer may still hold the last second or two in memory while running
        return max(0.0, value - (2.0 if self.running else 0.0))

    def stop(self, wait=20):
        """Ask ffmpeg to finish cleanly (like pressing q), then make sure it's gone."""
        if not self.proc:
            return
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
        self.close()

    def close(self):
        if self.log and not self.log.closed:
            self.log.close()


def _run(command, timeout):
    run = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=child_env(),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if run.returncode:
        raise MediaError("Couldn't read the newest part of the stream. " + run.stderr.strip()[-200:])


def cut_window(source, start, seconds, output, height=480):
    """Exact-time, low-resolution copy of [start, start+seconds) for analysis."""
    _run([
        find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{seconds:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale=-2:'min({height},ih)'", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k", str(output),
    ], timeout=max(120, int(seconds * 2)))
    return Path(output)


def finish_recording(source: Path) -> Path:
    """Remux the finished .ts to a seekable .mp4 that browsers can play."""
    target = source.with_suffix(".mp4")
    _run([
        find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(target),
    ], timeout=1800)
    return target


def shift(items, offset, end_cap=None):
    """Move transcript segments (dicts with start/end) by `offset` seconds."""
    out = []
    for seg in items:
        seg = dict(seg)
        seg["start"] = round(float(seg["start"]) + offset, 3)
        seg["end"] = round(float(seg["end"]) + offset, 3)
        if end_cap is not None:
            seg["end"] = min(seg["end"], end_cap)
        if "words" in seg and isinstance(seg["words"], list):
            seg["words"] = [
                {**w, "start": round(float(w["start"]) + offset, 3), "end": round(float(w["end"]) + offset, 3)}
                for w in seg["words"] if "start" in w and "end" in w
            ]
        out.append(seg)
    return out
