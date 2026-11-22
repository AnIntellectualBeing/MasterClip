from __future__ import annotations

import os
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .media import MediaError, find_tool, probe_media


class CaptionError(MediaError):
    """Caption render failed or its input was invalid."""


def phrase_segments(
    segments: list[dict[str, Any]], max_words: int = 4, pause_seconds: float = 0.5
) -> list[dict[str, Any]]:
    """Group timed words into readable cues; legacy text uses marked estimates."""
    if max_words < 1:
        raise ValueError("max_words must be at least 1")
    if pause_seconds < 0 or not math.isfinite(pause_seconds):
        raise ValueError("pause_seconds must be finite and nonnegative")
    phrases: list[dict[str, Any]] = []
    timed_words: list[dict[str, Any]] = []
    for segment in segments:
        try:
            start, end = float(segment["start"]), float(segment["end"])
            text = str(segment["text"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Each transcript segment needs numeric start/end and text."
            ) from exc
        if not text or end <= start:
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0:
            raise ValueError("Transcript segment times must be finite and nonnegative")
        raw_words = segment.get("words")
        if raw_words:
            for word in raw_words:
                try:
                    word_start = float(word["start"])
                    word_end = float(word["end"])
                    word_text = str(word["text"]).strip()
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("Word timing requires start, end, and text") from exc
                if (
                    not math.isfinite(word_start)
                    or not math.isfinite(word_end)
                    or word_start < 0
                    or word_end <= word_start
                    or not word_text
                ):
                    continue
                timed_words.append({**word, "start": word_start, "end": word_end, "text": word_text})
            continue
        words = text.split()
        chunks = [words[i : i + max_words] for i in range(0, len(words), max_words)]
        total = len(words)
        cursor = 0
        for index, chunk in enumerate(chunks):
            next_cursor = cursor + len(chunk)
            phrase_start = start + (end - start) * cursor / total
            phrase_end = (
                end
                if index == len(chunks) - 1
                else start + (end - start) * next_cursor / total
            )
            phrases.append(
                {
                    "start": phrase_start,
                    "end": phrase_end,
                    "text": " ".join(chunk),
                    "timing_estimated": True,
                }
            )
            cursor = next_cursor

    timed_words.sort(key=lambda word: (word["start"], word["end"]))
    group: list[dict[str, Any]] = []

    def flush_group():
        if not group:
            return
        phrases.append(
            {
                "start": group[0]["start"],
                "end": group[-1]["end"],
                "text": " ".join(word["text"] for word in group),
                "words": list(group),
            }
        )
        group.clear()

    for word in timed_words:
        if group:
            pause = word["start"] - group[-1]["end"]
            sentence_end = group[-1]["text"].rstrip().endswith((".", "?", "!"))
            if len(group) >= max_words or pause >= pause_seconds or sentence_end:
                flush_group()
        group.append(word)
    flush_group()
    phrases.sort(key=lambda phrase: (phrase["start"], phrase["end"]))
    return phrases


def _ass_timestamp(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{centiseconds:02}"


def _ass_escape(text: str) -> str:
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\\", "\\\\")
    normalized = normalized.replace("{", "\\{").replace("}", "\\}")
    return normalized.replace("\n", r"\N")


def _srt_to_ass(srt_text: str, width: int, height: int) -> str:
    from .transcript import parse_transcript

    if width <= 0 or height <= 0:
        raise CaptionError("Video dimensions must be positive.")
    cues = parse_transcript(srt_text, format="srt")
    raw_texts = []
    for block in re.split(r"\r?\n\s*\r?\n", srt_text.strip()):
        lines_in_block = block.strip().splitlines()
        timing_index = next(
            (i for i, line in enumerate(lines_in_block) if "-->" in line), None
        )
        if timing_index is not None:
            raw_texts.append(
                re.sub(
                    r"<[^>]*>",
                    "",
                    "\n".join(lines_in_block[timing_index + 1 :]).strip(),
                )
            )
    if len(raw_texts) != len(cues):
        raw_texts = [cue["text"] for cue in cues]
    portrait = height > width
    font_size = 60 if portrait else 42
    margin = 300 if portrait else 120
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Nirmala UI,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,60,60,{margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue, caption_text in zip(cues, raw_texts):
        end = max(cue["end"], cue["start"] + 0.01)
        lines.append(
            "Dialogue: 0,{},{},Default,,0,0,0,,{}".format(
                _ass_timestamp(cue["start"]),
                _ass_timestamp(end),
                _ass_escape(caption_text),
            )
        )
    return "\n".join(lines) + "\n"


def _wait_for_process(
    cmd: list[str], cwd: Path, cancel_event=None, timeout: float = 3600
) -> None:
    if cancel_event and cancel_event.is_set():
        raise CaptionError("Caption rendering cancelled.")
    with tempfile.TemporaryFile(mode="w+b") as stderr:
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=stderr
            )
        except OSError as exc:
            raise CaptionError(f"Could not start FFmpeg: {exc}") from exc
        started = time.monotonic()
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                proc.wait()
                raise CaptionError("Caption rendering cancelled.")
            if time.monotonic() - started > timeout:
                proc.kill()
                proc.wait()
                raise CaptionError("Caption rendering timed out.")
            time.sleep(0.1)
        if proc.returncode:
            stderr.seek(0, os.SEEK_END)
            stderr.seek(max(0, stderr.tell() - 1600))
            detail = stderr.read().decode("utf-8", "replace")
            raise CaptionError(f"FFmpeg caption rendering failed: {detail[-1600:]}")


def burn_captions(video: str | Path, srt: str | Path, cancel_event=None) -> Path:
    """Burn SRT subtitles, atomically replace the source, and return its path."""
    source, subtitle = Path(video).resolve(), Path(srt).resolve()
    if not source.is_file():
        raise CaptionError(f"Video file not found: {source}")
    if not subtitle.is_file():
        raise CaptionError(f"SRT subtitle file not found: {subtitle}")
    if subtitle.suffix.lower() != ".srt":
        raise CaptionError("Captions must be supplied as an SRT file.")
    try:
        info = probe_media(source)
    except MediaError as exc:
        raise CaptionError(str(exc)) from exc
    if cancel_event and cancel_event.is_set():
        raise CaptionError("Caption rendering cancelled.")

    partial = source.with_name(f".{source.stem}.captioning.part.mp4")
    source.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="clipstudio-captions-") as temp_name:
            temp_dir = Path(temp_name)
            local_ass = temp_dir / "captions.ass"
            srt_text = subtitle.read_text(encoding="utf-8-sig")
            local_ass.write_text(
                _srt_to_ass(srt_text, info.width, info.height), encoding="utf-8"
            )
            subtitle_filter = "subtitles=filename=captions.ass"
            cmd = [
                find_tool("ffmpeg"),
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-vf",
                subtitle_filter,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(partial),
            ]
            _wait_for_process(cmd, temp_dir, cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            raise CaptionError("Caption rendering cancelled.")
        os.replace(partial, source)
        return source
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
