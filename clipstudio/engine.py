from __future__ import annotations

import math
import os
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .media import MediaError, extract_audio, find_tool, probe_media


@dataclass
class Candidate:
    start: float
    end: float
    score: float = 0.0
    reasons: list[str] | None = None
    # The event itself inside [start, end]; clips are shaped around it (setup before, reaction after).
    core_start: float | None = None
    core_end: float | None = None
    parts: dict | None = None  # signal strengths of the core (see rank_moments)

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []


@dataclass
class AnalysisResult:
    duration: float
    candidates: list[Candidate]
    warnings: list[str]


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


def _motion_scores(path, window_count, window_seconds, cancel_event=None):
    """Compare tiny grayscale frames at 0.5 fps; memory use stays constant with video length."""
    tool = find_tool("ffmpeg")
    frame_bytes = 64 * 36
    cmd = [
        tool,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        "fps=0.5,scale=64:36:flags=area,format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    scores = np.zeros(window_count, dtype=float)
    previous = None
    frame_no = 0
    try:
        while True:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                raise MediaError("Analysis cancelled.")
            raw = proc.stdout.read(frame_bytes)
            if len(raw) != frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
            if previous is not None:
                index = min(window_count - 1, int(frame_no * 2 / window_seconds))
                scores[index] = max(
                    scores[index], float(np.mean(np.abs(frame - previous)))
                )
            previous = frame
            frame_no += 1
        status = proc.wait(timeout=30)
        if status:
            return None
        return scores
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()
        proc.wait()
        return None
    finally:
        if proc.stdout:
            proc.stdout.close()


def rank_moments(duration, transcript, rms, peak, motion2=None, heat=None, context_seconds=28, chat=None, weights=None):
    """Rule-based moment picker (clipstudio.moments): GTA events said out loud, back-and-forth,
    loudness jumps, scene change and "most replayed". Each candidate spans setup + event + reaction,
    cut between sentences; core_start/core_end mark the event so a short can be re-shaped to any length."""
    from .moments import find_moments, shape_clip

    moments, tl = find_moments(duration, transcript, rms, peak, motion2, heat, chat, weights=weights)
    out = []
    for m in moments:
        start, end = shape_clip(m.core_start, m.core_end, tl.sents, duration, context_seconds)
        a, b = int(m.core_start), max(int(m.core_start) + 1, int(math.ceil(m.core_end)))
        # What made this moment stand out (0..1 per signal): the star ratings learn from it.
        parts = {k: round(float(v[a:b].max(initial=0)), 3) for k, v in tl.parts.items()}
        out.append(Candidate(start, end, m.score, list(m.reasons), m.core_start, m.core_end, parts))
    return out


def analyze_media(
    path,
    transcript_segments=None,
    audio_path=None,
    window_seconds=2,
    context_seconds=30,
    cancel_event=None,
    progress=None,
    heat=None,
    chat=None,
    weights=None,
):
    info = probe_media(path)
    warnings = []
    owned = audio_path is None and info.has_audio
    wavpath = (
        Path(audio_path) if audio_path else Path(path).with_suffix(".analysis.wav")
    )
    try:
        if owned:
            extract_audio(path, wavpath, cancel_event=cancel_event)
        rms = []
        peak = []
        if not info.has_audio:
            warnings.append("No audio stream; ranking uses visual motion only.")
            rms = [0.0] * max(1, math.ceil(info.duration))
            peak = list(rms)
        else:
            # One loudness and one peak value per second.
            with wave.open(str(wavpath), "rb") as wf:
                rate = wf.getframerate()
                frames = wf.getnframes()
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise MediaError("Analysis cancelled.")
                    raw = wf.readframes(rate)
                    if not raw:
                        break
                    samples = (
                        np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
                    )
                    rms.append(float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0)
                    peak.append(float(np.max(np.abs(samples))) if samples.size else 0.0)
                    if progress and len(rms) % 30 == 0:
                        progress(min(0.9, len(rms) * rate / max(frames, 1)), "Analyzing audio")
        if info.has_audio and (not rms or max(rms, default=0) < 1e-5):
            warnings.append("Audio is silent or too quiet to score.")
        if not rms:
            return AnalysisResult(info.duration, [], warnings)
        if progress:
            progress(0.92, "Analyzing visual motion")
        motion = _motion_scores(path, max(1, math.ceil(len(rms) / 2)), 2, cancel_event)
        candidates = rank_moments(
            info.duration,
            transcript_segments or [],
            rms,
            peak,
            [float(x) for x in motion] if motion is not None else None,
            heat,
            context_seconds,
            chat,
            weights,
        )
        return AnalysisResult(info.duration, candidates, warnings)
    finally:
        if owned:
            try:
                wavpath.unlink(missing_ok=True)
            except OSError:
                pass


def select_highlights(candidates, duration, target_seconds=210):
    target = max(0, min(float(target_seconds), float(duration)))
    chosen = []
    used = 0
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        if used >= target:
            break
        start = max(0, float(c.start))
        end = min(float(duration), float(c.end))
        if end <= start or any(start < x.end and end > x.start for x in chosen):
            continue
        if used + end - start > target:
            end = start + (target - used)
        if end > start:
            chosen.append(Candidate(start, end, c.score, list(c.reasons or [])))
            used += end - start
    return sorted(chosen, key=lambda x: x.start)


def _run(cmd, cancel_event=None, timeout=3600, cwd=None):
    if cancel_event and cancel_event.is_set():
        raise MediaError("Rendering cancelled.")
    with tempfile.TemporaryFile(mode="w+b") as err:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=err,
            cwd=str(cwd) if cwd else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        started = time.monotonic()
        while p.poll() is None:
            if cancel_event and cancel_event.is_set():
                p.kill()
                p.wait()
                raise MediaError("Rendering cancelled.")
            if time.monotonic() - started > timeout:
                p.kill()
                p.wait()
                raise MediaError("Rendering timed out.")
            time.sleep(0.1)
        if p.returncode:
            err.seek(0, 2)
            err.seek(max(0, err.tell() - 1600))
            detail = err.read().decode("utf-8", "replace")
            raise MediaError(f"FFmpeg render failed: {detail[-1600:]}")


def render_compilation(
    path,
    selected,
    output,
    vertical=False,
    layout="blur",
    cancel_event=None,
    progress=None,
):
    if not selected:
        raise MediaError("Select at least one highlight to render.")
    out = Path(output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tool = find_tool("ffmpeg")
    if layout not in ("blur", "center"):
        raise MediaError("Layout must be 'blur' or 'center'.")
    valid = [c for c in selected if c.end > c.start]
    if not valid:
        raise MediaError("Selected highlight windows must have positive duration.")
    temporary = []
    try:
        for i, c in enumerate(valid):
            if cancel_event and cancel_event.is_set():
                raise MediaError("Rendering cancelled.")
            # Keep segment audio lossless and unprimed; independently encoded AAC
            # segments each add encoder delay that accumulates at concat boundaries.
            temp = out.with_name(f".{out.stem}.{i}.part.mkv")
            temporary.append(temp)
            if vertical and layout == "blur":
                vf = "split=2[bg][fg];[bg]scale=180:320:force_original_aspect_ratio=increase,crop=180:320,boxblur=6:2,scale=1080:1920[bgx];[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgx];[bgx][fgx]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,setpts=PTS-STARTPTS[v]"
            elif vertical:
                vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30"
            else:
                vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS"
            if vertical and layout == "blur":
                cmd = [
                    tool,
                    "-nostdin",
                    "-y",
                    "-ss",
                    str(c.start),
                    "-i",
                    str(path),
                    "-t",
                    str(c.end - c.start),
                    "-filter_complex",
                    vf,
                    "-map",
                    "[v]",
                    "-map",
                    "0:a:0?",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-af",
                    "asetpts=PTS-STARTPTS",
                    str(temp),
                ]
            else:
                cmd = [
                    tool,
                    "-nostdin",
                    "-y",
                    "-ss",
                    str(c.start),
                    "-i",
                    str(path),
                    "-t",
                    str(c.end - c.start),
                    "-vf",
                    vf,
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-af",
                    "asetpts=PTS-STARTPTS",
                    str(temp),
                ]
            _run(cmd, cancel_event)
            if progress:
                progress(
                    (i + 1) / (len(valid) + 1),
                    f"Rendering segment {i + 1}/{len(valid)}",
                )
        listing = out.with_name(f".{out.stem}.concat.txt")
        temporary.append(listing)
        listing.write_text(
            "".join(
                f"file '{str(p).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                for p in temporary[:-1]
            ),
            encoding="utf-8",
        )
        finalpart = out.with_name(f".{out.stem}.part.mp4")
        temporary.append(finalpart)
        _run(
            [
                tool,
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(finalpart),
            ],
            cancel_event,
        )
        os.replace(finalpart, out)
        if progress:
            progress(1.0, "Render complete")
        return valid
    finally:
        for temp in temporary:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def render_short(
    path, candidate, output, layout="blur", cancel_event=None, progress=None
):
    selected = [candidate]
    return render_compilation(
        path,
        selected,
        output,
        vertical=True,
        layout=layout,
        cancel_event=cancel_event,
        progress=progress,
    )
