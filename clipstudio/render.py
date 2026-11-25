"""Render one finished clip in a single FFmpeg pass: layout + captions + title."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .design import FONTS, CANVAS, Design, build_filter
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
    from . import overlays as ov

    # Outro + music belong to a finished clip, not to a part that is joined into a long video later.
    outro_len = 0.0 if joinable else ov.outro_seconds(design)
    music = None if joinable else ov.music_file(design)
    total = duration + outro_len
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
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(Path(source).resolve()),
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
            from .effects import boom_wav

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

        def still(name, image, seconds=duration):
            """Add a PNG as a looping input; returns its input number."""
            nonlocal command, inputs
            path = temp / name
            image.save(path)
            command += ["-loop", "1", "-framerate", "30", "-t", f"{seconds:.3f}", "-i", str(path)]
            inputs += 1
            return inputs - 1

        base = "base"
        frame = ov.frame_parts(design)
        if frame:  # background card: the video shrinks into a rounded card on blur / colour / picture
            fx, fy, fw, fh, mask, bg = frame
            m = still("frame-mask.png", mask)
            video_in = "base"
            if bg is None:
                graph += (f";[base]split[fra][frb];[fra]scale={max(2, W // 16 * 2)}:{max(2, H // 16 * 2)},"
                          f"boxblur=6:2,scale={W}:{H},eq=brightness=-0.12,setsar=1[frbg]")
                video_in = "frb"
            else:
                b = still("frame-bg.png", bg.convert("RGB"))
                graph += f";[{b}:v]scale={W}:{H},setsar=1[frbg]"
            graph += (f";[{video_in}]scale={fw}:{fh},format=rgba[frv];[{m}:v]format=gray[frm];"
                      f"[frv][frm]alphamerge[frr];[frbg][frr]overlay={fx}:{fy}:shortest=1,setsar=1[framed]")
            base = "framed"
        card = design.card
        if card.kind != "none":
            until = duration if card.seconds <= 0 else min(duration, card.seconds)
            enable = f":enable='between(t,0,{until:.2f})'" if until < duration else ""
            if card.kind == "news":
                news, strip, ty, loop_w, x0 = ov.news_images(design, title_text)
                s = still("ticker.png", strip)
                n = still("news.png", news)
                tw = ov.ticker_width(design, x0)
                speed = max(40, round(W * 0.11))
                graph += (f";[{s}:v]format=rgba,crop={tw}:{strip.height}:x='mod(t*{speed},{loop_w})':y=0[tk]"
                          f";[{base}][tk]overlay={x0}:{ty}{enable}[nw1];[{n}:v]format=rgba[nwf]"
                          f";[nw1][nwf]overlay=0:0{enable}[carded]")
            else:
                made = ov.card_image(design, title_text)
                img, (cx, cy) = made
                c = still("card.png", img)
                fade = f",fade=out:st={max(0.0, until - 0.25):.2f}:d=0.25:alpha=1" if enable else ""
                graph += (f";[{c}:v]format=rgba,fade=in:st=0:d=0.25:alpha=1{fade}[cd]"
                          f";[{base}][cd]overlay={cx}:{cy}{enable}[carded]")
            base = "carded"
        if shouts:
            from .effects import shake_filter

            graph += ";" + shake_filter(shouts, W, H, base, "shk")
        emoji = emoji_overlays(design, segments, duration)
        last = "subs"
        graph += f";[{'shk' if shouts else base}]ass=filename=captions.ass:fontsdir=fonts[{last}]"
        for n, (png, at, until, x, y, size) in enumerate(emoji):
            output_scale = W / CANVAS[design.aspect][0]
            x, y, size = round(x * output_scale), round(y * output_scale), max(2, round(size * output_scale))
            command += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(png)]
            graph += (
                f";[{inputs}:v]scale={size}:{size},format=rgba[e{n}]"
                f";[{last}][e{n}]overlay={x - size // 2}:{y - size // 2}:enable='between(t,{at},{until})'"
                f":eof_action=pass[o{n}]"
            )
            last = f"o{n}"
            inputs += 1
        for name, made in (("logo", ov.logo_image(design)), ("handles", ov.handles_image(design))):
            if made:
                img, (bx, by) = made
                i = still(f"{name}.png", img)
                graph += f";[{last}][{i}:v]overlay={bx}:{by}[br{name}]"
                last = f"br{name}"
        graph += f";[{last}]format=yuv420p[{'vm' if outro_len else 'v'}]"
        audio = audio_map[1:-1] if audio_map.startswith("[") else None
        if (outro_len or music) and audio is None:
            graph += f";[{audio_input}:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[am0]"
            audio = "am0"
        if outro_len:
            graph += (f";[{audio}]aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},"
                      f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[am]")
            silent = ["-f", "lavfi", "-t", f"{outro_len:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            if design.outro.mode == "card":
                o = still("outro.png", ov.outro_image(design).convert("RGB"), outro_len)
                graph += f";[{o}:v]scale={W}:{H},fps=30,format=yuv420p,setsar=1,fade=in:st=0:d=0.35[vo]"
                command += silent
                graph += f";[{inputs}:a]asetpts=PTS-STARTPTS[ao]"
                inputs += 1
            else:
                clip = ov.asset_path(design.outro.video)
                command += ["-t", f"{outro_len:.3f}", "-i", str(clip)]
                o = inputs
                inputs += 1
                graph += (f";[{o}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,"
                          f"format=yuv420p,setsar=1,trim=0:{outro_len:.3f},setpts=PTS-STARTPTS[vo]")
                if probe_media(clip).has_audio:
                    graph += (f";[{o}:a]aresample=48000,aformat=channel_layouts=stereo,apad=whole_dur={outro_len:.3f},"
                              f"atrim=0:{outro_len:.3f},asetpts=PTS-STARTPTS[ao]")
                else:
                    command += silent
                    graph += f";[{inputs}:a]asetpts=PTS-STARTPTS[ao]"
                    inputs += 1
            graph += ";[vm][am][vo][ao]concat=n=2:v=1:a=1[v][ac]"
            audio = "ac"
        if music:
            command += ["-stream_loop", "-1", "-i", str(music)]
            graph += (f";[{inputs}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                      f"asetpts=PTS-STARTPTS,volume='{ov.music_volume_expr(design, total)}':eval=frame[mu]")
            inputs += 1
            if design.music.duck:  # quieter while someone talks
                graph += (f";[{audio}]asplit[vx][sc];[mu][sc]sidechaincompress=threshold=0.03:ratio=8:"
                          f"attack=20:release=350[mud];[vx][mud]amix=inputs=2:duration=first:"
                          f"dropout_transition=0:normalize=0[amx]")
            else:
                graph += f";[{audio}][mu]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amx]"
            audio = "amx"
        if audio:
            audio_map, audio_filter = f"[{audio}]", []
        video = video_args(20)
        tail = [
            "-ar", "48000", "-ac", "2", *audio_filter,
            *(["-c:a", "pcm_s16le"] if joinable else ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]),
            "-t", f"{total:.3f}", str(partial),
        ]
        head = [*command, "-filter_complex", graph, "-map", "[v]", "-map", audio_map]
        # libass reads captions.ass + fonts/ relative to the working folder.
        try:
            _run([*head, *video, *tail], cancel_event, timeout=max(600, int(total * 20)), cwd=temp)
        except MediaError:
            hardware = is_hardware(video)
            if not hardware or (cancel_event is not None and cancel_event.is_set()):
                raise
            mark_broken(hardware)  # e.g. a driver hiccup or a size the chip can't do: CPU from now on
            _run([*head, *video_args(20), *tail], cancel_event, timeout=max(600, int(total * 20)), cwd=temp)
    os.replace(partial, out)
    return out
