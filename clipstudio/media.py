from __future__ import annotations
import json, math, shutil, subprocess, tempfile, time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class MediaError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    format_name: str = ""


def find_tool(name: str) -> str:
    from .paths import bundled

    found = bundled(name) or shutil.which(name)
    if found:
        return found
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            pass
    raise MediaError(
        f"{name} was not found. Install FFmpeg and ensure {name} is on PATH."
    )


def parse_probe_output(data: dict, path: str = "") -> MediaInfo:
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise MediaError("Input does not contain a video stream.")
    try:
        duration = float(
            data.get("format", {}).get("duration") or video.get("duration")
        )
    except (TypeError, ValueError):
        raise MediaError("Could not determine video duration.")
    if not (duration > 0) or not math.isfinite(duration):
        raise MediaError("Media duration must be positive and finite.")
    rate = video.get("avg_frame_rate", "0/1")
    try:
        fps = float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        fps = 0
    return MediaInfo(
        path,
        duration,
        int(video.get("width", 0)),
        int(video.get("height", 0)),
        fps,
        any(s.get("codec_type") == "audio" for s in streams),
        data.get("format", {}).get("format_name", ""),
    )


def probe_media(path, timeout=30) -> MediaInfo:
    p = Path(path)
    if not p.is_file():
        raise MediaError(f"Media file not found: {p}")
    cmd = [
        find_tool("ffprobe"),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(p),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True
        )
    except subprocess.TimeoutExpired as e:
        raise MediaError("Media probe timed out.") from e
    except subprocess.CalledProcessError as e:
        raise MediaError(f"Unable to read media: {e.stderr[-1000:]}") from e
    try:
        return parse_probe_output(json.loads(result.stdout), str(p))
    except json.JSONDecodeError as e:
        raise MediaError("FFprobe returned invalid metadata.") from e


def extract_audio(
    path, output_wav, sample_rate=16000, timeout=600, cancel_event=None
) -> Path:
    if cancel_event and cancel_event.is_set():
        raise MediaError("Audio extraction cancelled.")
    out = Path(output_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        find_tool("ffmpeg"),
        "-nostdin",
        "-y",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "wav",
        str(out),
    ]
    with tempfile.TemporaryFile(mode="w+b") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)
        started = time.monotonic()
        try:
            while proc.poll() is None:
                if cancel_event and cancel_event.is_set():
                    raise MediaError("Audio extraction cancelled.")
                if time.monotonic() - started > timeout:
                    raise MediaError("Audio extraction timed out.")
                time.sleep(0.1)
            if proc.returncode:
                err.seek(0, 2)
                err.seek(max(0, err.tell() - 1200))
                raise MediaError(
                    "Audio extraction failed: " + err.read().decode("utf-8", "replace")
                )
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            out.unlink(missing_ok=True)
            raise
    return out
