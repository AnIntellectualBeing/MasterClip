"""Shout effect: find where someone really shouts in a clip, and the boom sound that goes with it.

A shout = a short burst clearly louder than the rest of this clip (not loud in absolute terms: a
quiet stream gets its own shouts too). The words said during the burst become the big yellow
word on screen. No words (language not understood, or a game explosion) = shake + boom only.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

from .media import find_tool
from .styles import mask

RATE = 8000
FRAME = 0.05  # seconds per loudness frame


def clip_loudness(source, start: float, duration: float, timeout: int = 120) -> np.ndarray:
    """dBFS per 50 ms frame of [start, start+duration) of the source (empty on any problem)."""
    cmd = [find_tool("ffmpeg"), "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
           "-i", str(source), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    try:
        run = subprocess.run(cmd, capture_output=True, timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return np.zeros(0)
    pcm = np.frombuffer(run.stdout[: len(run.stdout) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    step = int(RATE * FRAME)
    if len(pcm) < step * 4:
        return np.zeros(0)
    frames = pcm[: len(pcm) // step * step].reshape(-1, step)
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9
    return 20 * np.log10(rms)


def find_shouts(db: np.ndarray, words=(), duration: float | None = None, max_count: int | None = None):
    """[(start, end, text or None)] in clip time. `words` = [{start, end, text}] in clip time."""
    if len(db) < 20:
        return []
    duration = duration or len(db) * FRAME
    voiced = db[db > -55]
    if len(voiced) < 10:
        return []
    base = float(np.median(voiced))
    # Clearly above this clip's normal level AND among its loudest moments.
    threshold = max(base + 10.0, float(np.percentile(voiced, 96)), -26.0)
    hot = db >= threshold
    bursts, i = [], 0
    while i < len(hot):
        if not hot[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(hot) and (hot[j + 1] or (j + 5 < len(hot) and hot[j + 2 : j + 6].any())):
            j += 1
        if (j - i + 1) * FRAME >= 0.15:
            bursts.append((i * FRAME, (j + 1) * FRAME, float(db[i : j + 1].max())))
        i = j + 1
    bursts.sort(key=lambda b: -b[2])
    limit = max_count if max_count is not None else max(1, min(3, int(duration // 12)))
    chosen = []
    for b0, b1, _ in bursts:
        if b0 < 0.6 or b1 > duration - 0.5:
            continue
        if any(abs(b0 - c[0]) < 4.0 for c in chosen):
            continue
        chosen.append((b0, min(b1, b0 + 1.6)))
        if len(chosen) >= limit:
            break
    out = []
    for b0, b1 in sorted(chosen):
        said = [w for w in words if w["end"] > b0 - 0.2 and w["start"] < b1 + 0.2]
        text = None
        if said:
            # the word(s) in the burst; two short words read as one shout ("NO WAY")
            pick = said[:2] if len(said) >= 2 and sum(len(w["text"]) for w in said[:2]) <= 12 else said[:1]
            text = " ".join(mask(w["text"]) for w in pick).strip(" ,.").upper()
            text = text + "!" if text and text[-1] not in "!?" else text
            b0 = min(b0, pick[0]["start"])
        out.append((round(b0, 2), round(max(b1, b0 + 0.7), 2), text))
    return out


def boom_wav(path: Path) -> Path:
    """A short deep hit (made here, no sample library): falling bass tone + a soft click."""
    rate, length = 48000, 0.75
    t = np.arange(int(rate * length)) / rate
    freq = 38 + 95 * np.exp(-t * 7)
    phase = 2 * np.pi * np.cumsum(freq) / rate
    tone = np.sin(phase) * np.exp(-t * 4.2)
    tone += 0.35 * np.sin(2 * phase) * np.exp(-t * 9)
    click = np.random.default_rng(7).normal(0, 1, len(t)) * np.exp(-t * 160) * 0.5
    sound = tone + click
    sound = np.tanh(sound * 1.6)
    sound = sound / max(1e-6, np.abs(sound).max()) * 0.8
    pcm = (np.repeat(sound[:, None], 2, axis=1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    return path


def shake_filter(shouts, width: int, height: int, label_in: str, label_out: str) -> str:
    """Shake the picture for the first 0.35 s of every shout (text drawn after stays steady)."""
    margin = 18
    windows = "+".join(f"between(t,{s:.2f},{s + 0.35:.2f})" for s, _, _ in shouts)
    dx = f"{margin}+if({windows},{margin - 4}*sin(t*95),0)"
    dy = f"{margin}+if({windows},{margin - 6}*cos(t*71),0)"
    return (f"[{label_in}]crop=w={width - 2 * margin}:h={height - 2 * margin}:x='{dx}':y='{dy}',"
            f"scale={width}:{height},setsar=1[{label_out}]")
