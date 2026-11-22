"""Extra no-AI signals, one value per second:

  voice   share of the sound's energy in the 2-4 kHz band: raised voices, screams, impacts. A loud
          car radio is loud but sits lower; a streamer yelling "RUN!" lands here.
  strobe  red/blue light flicker on screen: police lights in a chase.
  flash   short bright bursts and fire-orange jumps: explosions, muzzle flashes, crashes.

Ideas from NeonClipping (MIT, credited in NOTICE). Our own code. Video cues use a tiny 48x27 copy at
6 frames a second, so a 10-minute stretch takes a few seconds and almost no memory.
"""

from __future__ import annotations

import subprocess
import wave

import numpy as np

from .media import MediaError, find_tool

W, H, FPS = 48, 27, 6


def voice_share(wav_path) -> list[float]:
    """Per second: fraction of energy between 2 and 4 kHz (0..1)."""
    out = []
    with wave.open(str(wav_path), "rb") as wf:
        rate, channels = wf.getframerate(), wf.getnchannels()
        lo, hi = 2000, min(4000, rate // 2)
        while True:
            raw = wf.readframes(rate)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            if channels > 1:
                x = x[: len(x) // channels * channels].reshape(-1, channels).mean(1)
            if x.size < rate // 4 or hi <= lo:
                out.append(0.0)
                continue
            spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
            freqs = np.fft.rfftfreq(x.size, 1 / rate)
            total = float(spec[freqs >= 80].sum())
            band = float(spec[(freqs >= lo) & (freqs < hi)].sum())
            out.append(round(band / total, 4) if total > 1e-3 else 0.0)
    return out


def action_cues(path, duration: float, cancel_event=None) -> dict[str, list[float]]:
    """{'strobe': [...], 'flash': [...]} per second (0..1)."""
    n = max(1, int(np.ceil(duration)))
    frame = W * H * 3
    cmd = [find_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", str(path), "-an",
           "-vf", f"fps={FPS},scale={W}:{H}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    luma, red, blue, orange = [], [], [], []
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise MediaError("Analysis cancelled.")
            raw = proc.stdout.read(frame)
            if len(raw) != frame:
                break
            px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int16)
            r, g, b = px[:, 0], px[:, 1], px[:, 2]
            luma.append(float((0.299 * r + 0.587 * g + 0.114 * b).mean()))
            red.append(float(((r > 150) & (r > 1.7 * g) & (r > 1.7 * b)).mean()))
            blue.append(float(((b > 150) & (b > 1.5 * r) & (b > 1.15 * g)).mean()))
            orange.append(float(((r > 200) & (g > 80) & (g < 190) & (b < 90)).mean()))
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
    strobe, flash = np.zeros(n), np.zeros(n)
    if len(luma) < 3:
        return {"strobe": strobe.tolist(), "flash": flash.tolist()}
    luma, red, blue, orange = map(np.asarray, (luma, red, blue, orange))
    # strobe: red and blue both show up and take turns being the stronger one
    lead = np.sign(red - blue) * ((red + blue) > 0.004)
    turns = np.abs(np.diff(lead)) >= 2  # red -> blue or blue -> red between two frames
    # flash: brightness jumps up and drops again within two frames (not a scene cut), or fire-orange appears
    d = np.diff(luma)
    burst = np.zeros(len(luma), bool)
    burst[1:-1] = (d[:-1] > 22) & (d[1:] < -8)
    if len(d) > 2:
        burst[2:] |= (d[:-1] > 22) & (d[1:] > -2) & (np.r_[d[2:], 0][: len(d) - 1] < -8)
    fire = np.r_[False, np.diff(orange) > 0.06]
    for i in range(n):
        a, b = i * FPS, (i + 1) * FPS
        if a >= len(luma):
            break
        both = (red[a:b] > 0.002).any() and (blue[a:b] > 0.002).any()
        strobe[i] = min(1.0, turns[a:b].sum() / 3.0) if both else 0.0
        flash[i] = min(1.0, (burst[a:b].sum() + fire[a:b].sum()) / 2.0)
    return {"strobe": [round(float(x), 3) for x in strobe], "flash": [round(float(x), 3) for x in flash]}
