"""Render one finished clip in a single FFmpeg pass: layout + captions + title."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .design import FONTS, Design, build_filter
from .encoders import is_hardware, mark_broken, video_args
from .engine import _run
from .media import MediaError, find_tool, probe_media
from .styles import build_ass, emoji_overlays

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"


def render_clip(
    source,
    start: float,
    end: float,
    output,
    design: Design,
    segments=(),
    title_text: str | None = None,
    cancel_event=None,
):
    """`segments` are caption cues already shifted so the clip starts at 0.

    A .mkv output keeps lossless PCM audio (for parts that get joined later without
    AAC gaps); .mp4 gets AAC. A source without sound gets a silent track so every
    part has audio."""
    start, end = float(start), float(end)
    if end <= start:
        raise MediaError("The clip must end after it starts.")
    info = probe_media(source)
    duration = end - start
    out = Path(output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    graph, W, H = build_filter(design, info.width, info.height)
    partial = out.with_name(f".{out.stem}.part{out.suffix}")
    joinable = out.suffix.lower() == ".mkv"
    segments = list(segments)
    shouts = []
    if design.shout and info.has_audio:
        from .effects import clip_loudness, find_shouts
        from .styles import caption_words

        shouts = find_shouts(clip_loudness(source, start, duration), caption_words(segments), duration)
    with tempfile.TemporaryDirectory(prefix="clipstudio-render-") as temp_name:
        temp = Path(temp_name)
        fonts = temp / "fonts"
        fonts.mkdir()
        from .fonts import font_file
        from .styles import text_families

        wanted = {design.caption.font, design.title.font, "Poppins ExtraBold"}
        wanted |= text_families(design, segments, title_text, shouts)
        for family in wanted:  # bundled fonts + Windows fonts for Hindi, Kannada, Arabic...
            path = font_file(family)
            if path and path.is_file() and not (fonts / path.name).exists():
                shutil.copy2(path, fonts / path.name)
        (temp / "captions.ass").write_text(
            build_ass(design, segments, duration, title_text, shouts), encoding="utf-8"
        )
        command = [
            find_tool("ffmpeg"), "-nostdin", "-y",
            "-ss", f"{start:.3f}", "-i", str(Path(source).resolve()), "-t", f"{duration:.3f}",
        ]
        inputs = 1
        audio_input = 0
        if not info.has_audio:
            command += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            audio_input = inputs
            inputs += 1
        audio_map = f"{audio_input}:a:0"
        audio_filter = ["-af", "asetpts=PTS-STARTPTS"]
        if shouts:
            from .effects import boom_wav, shake_filter

            graph += ";" + shake_filter(shouts, W, H, "base", "shk")
            if design.sfx:
                command += ["-i", str(boom_wav(temp / "boom.wav"))]
                n = len(shouts)
                graph += (f";[{audio_input}:a]asetpts=PTS-STARTPTS,aresample=48000[a0]"
                          f";[{inputs}:a]asplit={n}" + "".join(f"[b{i}]" for i in range(n)))
                for i, (s, _, _) in enumerate(shouts):
                    ms = max(0, int(s * 1000) - 40)
                    graph += f";[b{i}]adelay={ms}|{ms},volume=0.7[x{i}]"
                graph += (";[a0]" + "".join(f"[x{i}]" for i in range(n))
                          + f"amix=inputs={n + 1}:duration=first:dropout_transition=0:normalize=0[aout]")
                audio_map, audio_filter = "[aout]", []
                inputs += 1
        emoji = emoji_overlays(design, segments, duration)
        last = "subs"
        graph += f";[{'shk' if shouts else 'base'}]ass=filename=captions.ass:fontsdir=fonts[{last}]"
        for n, (png, at, until, x, y, size) in enumerate(emoji):
            command += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(png)]
            graph += (
                f";[{inputs}:v]scale={size}:{size},format=rgba[e{n}]"
                f";[{last}][e{n}]overlay={x - size // 2}:{y - size // 2}:enable='between(t,{at},{until})'"
                f":eof_action=pass[o{n}]"
            )
            last = f"o{n}"
            inputs += 1
        graph += f";[{last}]format=yuv420p[v]"
        video = video_args(20)
        tail = [
            "-ar", "48000", "-ac", "2", *audio_filter,
            *(["-c:a", "pcm_s16le"] if joinable else ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]),
            "-t", f"{duration:.3f}", str(partial),
        ]
        head = [*command, "-filter_complex", graph, "-map", "[v]", "-map", audio_map]
        # libass reads captions.ass + fonts/ relative to the working folder.
        try:
            _run([*head, *video, *tail], cancel_event, timeout=max(600, int(duration * 20)), cwd=temp)
        except MediaError:
            hardware = is_hardware(video)
            if not hardware or (cancel_event is not None and cancel_event.is_set()):
                raise
            mark_broken(hardware)  # e.g. a driver hiccup or a size the chip can't do: CPU from now on
            _run([*head, *video_args(20), *tail], cancel_event, timeout=max(600, int(duration * 20)), cwd=temp)
    os.replace(partial, out)
    return out
