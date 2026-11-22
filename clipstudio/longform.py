"""Long YouTube videos: a compilation of moments (intro card + chapters) and the
full-length video with captions. Both are 16:9 and come with a description that has
YouTube chapter times and real chapter markers inside the MP4."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .design import FONTS, Design
from .engine import _run
from .media import MediaError, find_tool, probe_media
from .render import FONT_DIR, render_clip
from .styles import ass_color, ass_time, escape

W, H = 1920, 1080


@dataclass
class Moment:
    start: float
    end: float
    title: str
    segments: list = field(default_factory=list)  # caption cues shifted to start at 0


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def youtube_chapters(chapters: list[tuple[float, str]], min_gap: float = 10) -> list[tuple[float, str]]:
    """YouTube needs the first chapter at 0:00, at least 3 chapters, each 10 s or longer."""
    out: list[tuple[float, str]] = []
    for at, title in sorted(chapters):
        title = " ".join(str(title).split())[:90] or "Moment"
        if not out:
            out.append((0.0, title))
        elif at - out[-1][0] >= min_gap:
            out.append((at, title))
    return out if len(out) >= 3 else []


def description(title: str, chapters: list[tuple[float, str]], footer: str = "", names: list[str] | None = None) -> str:
    lines = [title.strip(), ""]
    if chapters:
        lines += ["Chapters", *[f"{clock(at)} {name}" for at, name in chapters], ""]
    elif names:
        lines += ["In this video", *[f"• {name}" for name in names], ""]
    if footer.strip():
        lines.append(footer.strip())
    return "\n".join(lines).strip() + "\n"


def _ffmetadata(chapters: list[tuple[float, str]], total: float) -> str:
    text = [";FFMETADATA1"]
    for i, (at, name) in enumerate(chapters):
        end = chapters[i + 1][0] if i + 1 < len(chapters) else total
        clean = name.replace("\\", "").replace("=", "-").replace(";", ",").replace("#", "No.").replace("\n", " ")
        text += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(at * 1000)}", f"END={int(end * 1000)}", f"title={clean}"]
    return "\n".join(text) + "\n"


def _finish(parts: list[Path], output: Path, chapters, total, cancel_event=None):
    """Join same-format parts (h264 + PCM .mkv) and write AAC + chapters + faststart."""
    out = output.resolve()
    with tempfile.TemporaryDirectory(prefix="clipstudio-join-") as temp_name:
        temp = Path(temp_name)
        listing = temp / "parts.txt"
        listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
        meta = temp / "chapters.txt"
        meta.write_text(_ffmetadata(chapters, total), encoding="utf-8")
        partial = out.with_name(f".{out.stem}.part.mp4")
        command = [
            find_tool("ffmpeg"), "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-i", str(meta), "-map", "0:v:0", "-map", "0:a:0", "-map_metadata", "1", "-map_chapters", "1",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(partial),
        ]
        _run(command, cancel_event, timeout=max(900, int(total * 2)))
    os.replace(partial, out)


def _intro_ass(title: str, subtitle: str, seconds: float) -> str:
    big = "Luckiest Guy"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Big,{big},132,{ass_color('#FFFFFF')},{ass_color('#FFFFFF')},{ass_color('#000000')},{ass_color('#000000', 0x70)},0,0,0,0,100,100,0,0,1,9,4,5,120,120,0,1
Style: Small,Poppins ExtraBold,64,{ass_color('#2A211C')},{ass_color('#2A211C')},{ass_color('#FFD233')},{ass_color('#000000', 0x80)},0,0,0,0,100,100,0,0,3,22,0,5,120,120,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    end = ass_time(seconds)
    pop = "\\fad(150,250)\\fscx80\\fscy80\\t(0,220,\\fscx104\\fscy104)\\t(220,340,\\fscx100\\fscy100)"
    lines = [f"Dialogue: 1,{ass_time(0)},{end},Big,,0,0,0,,{{\\an5\\pos({W // 2},{int(H * 0.44)}){pop}}}{escape(title.upper())}"]
    if subtitle:
        lines.append(f"Dialogue: 1,{ass_time(0.25)},{end},Small,,0,0,0,,{{\\an5\\pos({W // 2},{int(H * 0.64)})\\fad(200,250)}}{escape(subtitle)}")
    return head + "\n".join(lines) + "\n"


def intro_card(source, at: float, output: Path, title: str, subtitle: str, seconds: float = 3.0, cancel_event=None):
    """Blurred, darkened gameplay from the first moment with the video title on top."""
    info = probe_media(source)
    at = max(0.0, min(at, max(0.0, info.duration - seconds)))
    with tempfile.TemporaryDirectory(prefix="clipstudio-intro-") as temp_name:
        temp = Path(temp_name)
        (temp / "fonts").mkdir()
        for name in ("Luckiest Guy", "Poppins ExtraBold"):
            shutil.copy2(FONT_DIR / FONTS[name], temp / "fonts" / FONTS[name])
        (temp / "intro.ass").write_text(_intro_ass(title, subtitle, seconds), encoding="utf-8")
        vf = (
            f"fps=30,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            "boxblur=22:2,eq=brightness=-0.18:saturation=1.15,setsar=1,"
            "ass=filename=intro.ass:fontsdir=fonts,format=yuv420p"
        )
        command = [
            find_tool("ffmpeg"), "-nostdin", "-y", "-ss", f"{at:.3f}", "-i", str(Path(source).resolve()),
            "-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", f"{seconds:.3f}", "-vf", vf, "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "pcm_s16le",
            "-ar", "48000", "-ac", "2", str(output.resolve()),
        ]
        _run(command, cancel_event, timeout=300, cwd=temp)
    return output


def make_compilation(source, moments: list[Moment], output, design: Design, title: str,
                     intro: bool = True, footer: str = "", cancel_event=None, progress=None) -> dict:
    if not moments:
        raise MediaError("Pick at least one moment.")
    if design.aspect != "16:9":
        design = design.model_copy(update={"aspect": "16:9"})
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.with_name(f".{out.stem}-parts")
    work.mkdir(exist_ok=True)
    parts, chapters, at = [], [], 0.0
    steps = len(moments) + (1 if intro else 0) + 1
    try:
        if intro:
            card = work / "00-intro.mkv"
            intro_card(source, moments[0].start, card, title, f"{len(moments)} moments" if len(moments) > 1 else "", cancel_event=cancel_event)
            parts.append(card)
            at += probe_media(card).duration  # the intro shares chapter 1, which starts at 0:00
        for i, moment in enumerate(moments):
            if cancel_event and cancel_event.is_set():
                raise MediaError("Cancelled")
            if progress:
                progress(len(parts) / steps, f"Moment {i + 1} of {len(moments)}")
            part = work / f"{i + 1:02}.mkv"
            render_clip(source, moment.start, moment.end, part, design, moment.segments, moment.title, cancel_event=cancel_event)
            parts.append(part)
            chapters.append((0.0 if i == 0 else at, moment.title))
            at += probe_media(part).duration
        if progress:
            progress((steps - 1) / steps, "Joining everything into one video")
        yt = youtube_chapters(chapters)
        _finish(parts, out, yt or chapters, at, cancel_event)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"duration": round(at, 2), "chapters": [[round(a, 2), n] for a, n in (yt or chapters)],
            "description": description(title, yt, footer, [m.title for m in moments])}


def make_full(source, start: float, end: float, segments, output, design: Design, title: str,
              chapters: list[tuple[float, str]], footer: str = "", cancel_event=None) -> dict:
    if design.aspect != "16:9":
        design = design.model_copy(update={"aspect": "16:9"})
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.stem}-full.mkv")
    try:
        render_clip(source, start, end, part, design, segments, None, cancel_event=cancel_event)
        total = probe_media(part).duration
        yt = youtube_chapters([(max(0.0, a - start), n) for a, n in chapters if start <= a < end])
        _finish([part], out, yt, total, cancel_event)
    finally:
        part.unlink(missing_ok=True)
    return {"duration": round(total, 2), "chapters": [[round(a, 2), n] for a, n in yt],
            "description": description(title, yt, footer)}
