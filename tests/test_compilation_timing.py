import subprocess
import wave
import numpy as np
from clipstudio.engine import Candidate, render_compilation


def test_compilation_cuts_keep_audio_events_on_the_caption_timeline(tmp_path):
    rate = 48000
    t = np.arange(rate * 8) / rate
    samples = np.zeros(rate * 8, dtype=np.float32)
    for start in (0.2, 2.2, 4.2, 6.2):
        active = (t >= start) & (t < start + 0.1)
        samples[active] = 0.3 * np.sin(2 * np.pi * 440 * t[active])
    wav = tmp_path / "pulses.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((samples * 32767).astype("<i2").tobytes())
    source = tmp_path / "source.mp4"
    output = tmp_path / "out.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=160x90:rate=30:duration=8",
            "-i",
            str(wav),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        capture_output=True,
        check=True,
    )
    render_compilation(source, [Candidate(i, i + 1) for i in (0, 2, 4, 6)], output)
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    audio = np.frombuffer(raw, dtype="<f4")
    count = (len(audio) // 480) * 480
    rms = np.sqrt((audio[:count].reshape(-1, 480) ** 2).mean(axis=1))
    mask = rms > 0.04
    starts = np.flatnonzero(mask & ~np.r_[False, mask[:-1]]) * 0.01
    assert len(starts) == 4
    assert np.allclose(starts, [0.2, 1.2, 2.2, 3.2], atol=0.05), starts
