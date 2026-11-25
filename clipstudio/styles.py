"""ASS subtitles for captions, the hook title, shout words and the @credit line."""

from __future__ import annotations

from .design import CANVAS, CaptionStyle, Design, TitleStyle, font_height_ratio

_BANNED = {
    # Mask on screen only; the audio stays as it is.
    "fuck", "fucking", "fucked", "shit", "bitch", "nigga", "nigger", "pussy",
    "dick", "cunt", "asshole", "motherfucker", "bastard", "slut", "whore",
}
# Words that never get the key-word colour in "punch" / "twotone".
_PLAIN = {
    "the", "a", "an", "to", "of", "and", "is", "it", "i", "you", "we", "he", "she", "they", "in", "on",
    "at", "for", "this", "that", "be", "was", "are", "me", "my", "so", "just", "like", "do", "oh", "im",
    "i'm", "its", "it's", "your", "our", "with", "but", "or", "if", "then", "there", "what", "yeah",
    "hai", "ka", "ki", "ke", "ko", "se", "ne", "toh", "to", "na", "bhi", "ho", "main", "hum",
}
SHOUT_COLOR = "#FFE600"


def ass_color(hex_color: str, alpha: int = 0) -> str:
    value = hex_color.lstrip("#")
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def escape(text: str) -> str:
    text = str(text).replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    return text.replace("\r", " ").replace("\n", r"\N")


def rich(text: str, family: str) -> str:
    """Escaped ASS text; letters the font can't draw (Hindi in Anton, Kannada, ...) switch to a
    font that has them and back, so nothing shows as empty boxes."""
    from .fonts import runs

    out = []
    for fam, piece in runs(str(text), family):
        out.append(escape(piece) if fam == family else f"{{\\fn{fam}}}{escape(piece)}{{\\fn{family}}}")
    return "".join(out)


def mask(word: str) -> str:
    core = "".join(ch for ch in word.lower() if ch.isalpha())
    if core in _BANNED and len(word) > 2:
        return word[0] + "*" * (len(word) - 2) + word[-1]
    return word


def caption_words(segments) -> list[dict]:
    """Timed words; cues without word times are spread evenly (marked estimated)."""
    words = []
    for segment in segments:
        if segment.get("words"):
            for word in segment["words"]:
                text = str(word.get("text", "")).strip()
                if text and float(word["end"]) > float(word["start"]):
                    words.append({"start": float(word["start"]), "end": float(word["end"]), "text": text})
            continue
        tokens = str(segment.get("text", "")).split()
        start, end = float(segment["start"]), float(segment["end"])
        if not tokens or end <= start:
            continue
        step = (end - start) / len(tokens)
        for i, token in enumerate(tokens):
            words.append({"start": start + i * step, "end": start + (i + 1) * step, "text": token})
    words.sort(key=lambda w: w["start"])
    return words


def _chunks(words, size, pause=0.6):
    chunk = []
    for word in words:
        if chunk and (
            len(chunk) >= size
            or word["start"] - chunk[-1]["end"] > pause
            or chunk[-1]["text"].endswith((".", "?", "!"))
        ):
            yield chunk
            chunk = []
        chunk.append(word)
    if chunk:
        yield chunk


def caption_groups(cap: CaptionStyle, segments, duration: float):
    """(words, end time) for each caption that shows on screen."""
    words = caption_words(segments)
    size = 7 if cap.style == "classic" else cap.words
    groups = list(_chunks(words, size))
    for index, group in enumerate(groups):
        group_end = group[-1]["end"] + 0.4
        if index + 1 < len(groups):
            group_end = min(group_end, groups[index + 1][0]["start"])
        yield group, min(max(group_end, group[-1]["end"]), duration)


def _balanced_cut(texts) -> int:
    """Where to break a caption into two lines of about the same width."""
    total = sum(len(t) for t in texts) + len(texts) - 1
    best, best_gap, run = 1, total, 0
    for i in range(1, len(texts)):
        run += len(texts[i - 1]) + (1 if i > 1 else 0)
        gap = abs(total - 2 * run)
        if gap < best_gap:
            best, best_gap = i, gap
    return best


def key_word(texts) -> int:
    """Index of the word that carries the line (gets the highlight colour), or -1."""
    best, best_score = -1, 0.0
    for i, text in enumerate(texts):
        core = "".join(ch for ch in text.lower() if ch.isalpha() or ch == "'")
        if not core or core in _PLAIN:
            continue
        score = len(core) + (6 if "*" in text or core in _BANNED else 0) + (3 if text.endswith(("!", "?")) else 0)
        if score > best_score:
            best, best_score = i, score
    return best


def emoji_overlays(design: Design, segments, duration: float):
    """[(png path, start, end, centre x px, centre y px, size px)] for the Emoji switch."""
    from .emoji import EMOJI_DIR, emoji_for

    cap = design.caption
    if not cap.emoji or cap.style == "none":
        return []
    W, H = CANVAS[design.aspect]
    scale = W / 1080 if design.aspect != "16:9" else H / 1080
    size = max(48, round(cap.size * scale * 1.25))
    lines = 2 if cap.style in ("twoline", "twotone") else 1
    x = round(cap.x * W)
    y = round(cap.y * H - (cap.size * scale * (0.55 * lines + 0.25)) - size / 2)
    y = max(size // 2, y)
    out, last_end = [], -10.0
    for group, end in caption_groups(cap, segments, duration):
        code = emoji_for(" ".join(w["text"] for w in group))
        start = group[0]["start"]
        if code and start >= last_end and end - start >= 0.3:
            out.append((EMOJI_DIR / f"{code}.png", round(start, 3), round(end, 3), x, y, size))
            last_end = end
        if len(out) >= 16:
            break
    return out


def caption_animation(cap: CaptionStyle) -> str:
    """The animation a caption really uses ("auto" = what each style always did)."""
    if cap.animation != "auto":
        return cap.animation
    return {"pop": "pop_auto", "karaoke": "highlight"}.get(cap.style, "full_line")


def _style_line(name, font, size, primary, secondary, outline_color, back, bold, border_style, outline, shadow):
    return (
        f"Style: {name},{font},{size},{primary},{secondary},{outline_color},{back},"
        f"0,0,0,0,100,100,0,0,{border_style},{outline},{shadow},5,40,40,40,1"
    )


# (text colour, rounded box colour or None, outline colour, outline px, shadow px)
_TITLE_LOOKS = {
    "white_box": ("#111111", "#FFFFFF", "#000000", 0, 0),
    "red_box": ("#FFFFFF", "#E5322D", "#000000", 0, 0),
    "yellow": ("#FFD233", None, "#000000", 7, 3),
    "outline": ("#FFFFFF", None, "#000000", 8, 2),
    "meme": ("#111111", None, "#000000", 0, 0),
}
TITLE_PAD = 18      # px (1080 frame) between the title text and the edge of its box - as the preview
TITLE_RADIUS = 22   # px corner radius of the title box - as the preview
TITLE_LINE = 1.1    # line height in em - as the preview (CSS line-height 1.1)


def rounded_rect(x0: float, y0: float, x1: float, y1: float, r: float) -> str:
    """ASS drawing commands for a rounded rectangle."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    k = r * 0.448  # 1 - 0.552 (circle bezier constant)
    p = lambda *v: " ".join(str(round(n)) for n in v)  # noqa: E731
    return (
        f"m {p(x0 + r, y0)} l {p(x1 - r, y0)} b {p(x1 - k, y0, x1, y0 + k, x1, y0 + r)} "
        f"l {p(x1, y1 - r)} b {p(x1, y1 - k, x1 - k, y1, x1 - r, y1)} "
        f"l {p(x0 + r, y1)} b {p(x0 + k, y1, x0, y1 - k, x0, y1 - r)} "
        f"l {p(x0, y0 + r)} b {p(x0, y0 + k, x0 + k, y0, x0 + r, y0)}"
    )


def _title_over_face(design: Design) -> bool:
    from .design import CAM_LAYOUTS, cam_output_rect, effective_layout

    if design.aspect == "16:9" or effective_layout(design) not in CAM_LAYOUTS:
        return False
    try:
        rect = cam_output_rect(design)
    except Exception:  # noqa: BLE001
        return False
    if not rect:
        return False
    _, y, _, h = rect
    return y <= design.title.y <= y + h


def title_lines(design: Design, text: str):
    """(lines, em px, centre x, centre y, box or None) for the hook title, laid out like the preview."""
    from .fonts import text_width, wrap

    W, H = CANVAS[design.aspect]
    scale = W / 1080 if design.aspect != "16:9" else H / 1080
    title = design.title
    em = title.size * scale
    _, box, *_ = _TITLE_LOOKS[title.style]
    shown = text.upper() if title.style in ("meme", "yellow") else text
    pad = TITLE_PAD * scale if box else 0
    lines = wrap(shown, title.font, em, W * 0.92 - 2 * pad)
    if len(lines) > 1 and _title_over_face(design):
        # Keep the streamer's face clear: a long title over the cam shrinks to one line (or a smaller box).
        for factor in (0.9, 0.8, 0.72, 0.64):
            fitted = wrap(shown, title.font, em * factor, W * 0.92 - 2 * pad)
            if len(fitted) == 1:
                em, lines = em * factor, fitted
                break
        else:
            em = em * 0.8
            lines = wrap(shown, title.font, em, W * 0.92 - 2 * pad)
    # A single word wider than the frame (long names, Kannada...) shrinks until it fits too.
    room = W * 0.92 - 2 * pad
    widest = max((text_width(line, title.font, em) for line in lines), default=0)
    if widest > room > 0:
        em = em * room / widest
    tx, ty = title.x * W, title.y * H
    if design.layout == "meme":
        tx, ty = W / 2, design.meme_bar * H / 2
    rect = None
    if box:
        width = max(text_width(line, title.font, em) for line in lines) + 2 * pad
        height = len(lines) * TITLE_LINE * em + 2 * pad
        rect = (tx - width / 2, ty - height / 2, tx + width / 2, ty + height / 2)
    return lines, em, tx, ty, rect


def credit_label(design: Design) -> str:
    """The credit line: "@name" or "Clip: name"."""
    name = design.credit.strip()
    if design.credit_style == "clip":
        return "Clip: " + name.lstrip("@")
    return name if name.startswith("@") else "@" + name


def text_families(design: Design, segments, title_text, shouts=()) -> set[str]:
    """Every font family the subtitles will ask for (so render can put the files next to them)."""
    from .fonts import families_used

    texts = [w["text"] for w in caption_words(segments)]
    used = families_used(texts, design.caption.font)
    used |= families_used([title_text or design.title.text or ""], design.title.font)
    used |= families_used([s[2] or "" for s in shouts], design.caption.font)
    used |= families_used([design.credit], "Poppins ExtraBold")
    return used


def build_ass(design: Design, segments, duration: float, title_text: str | None = None, shouts=()) -> str:
    W, H = CANVAS[design.aspect]
    scale = W / 1080 if design.aspect != "16:9" else H / 1080
    cap: CaptionStyle = design.caption
    title: TitleStyle = design.title
    cap_size = max(12, round(cap.size * scale * font_height_ratio(cap.font)))
    box = cap.style == "boxed"
    soft = cap.style in ("punch", "twotone")
    styles = [
        _style_line(
            "Caption", cap.font, cap_size, ass_color(cap.color), ass_color(cap.highlight),
            ass_color("#000000", 0x60) if box else ass_color(cap.outline_color),
            ass_color("#000000", 0x40 if soft else 0x80), True, 3 if box else 1,
            max(8, cap.outline * 2) if box else cap.outline, 0 if box else (4 if soft else 2),
        )
    ]
    text_color, title_box, edge, outline, shadow = _TITLE_LOOKS[title.style]
    styles.append(
        _style_line(
            "Title", title.font, max(12, round(title.size * scale * font_height_ratio(title.font))), ass_color(text_color),
            ass_color(text_color), ass_color(edge), ass_color("#000000", 0x90), True,
            1, outline, shadow,
        )
    )
    styles.append(
        _style_line(
            "Credit", "Poppins ExtraBold", max(12, round(design.credit_size * scale * font_height_ratio("Poppins ExtraBold"))), ass_color("#FFFFFF", 0x20),
            ass_color("#FFFFFF"), ass_color("#000000", 0x40), ass_color("#000000", 0x80),
            True, 1, 3, 0,
        )
    )
    shout_size = max(12, round(min(200, max(cap.size * 1.45, 110)) * scale * font_height_ratio(cap.font)))  # = shout_css
    styles.append(
        _style_line(
            "Shout", cap.font, shout_size, ass_color(SHOUT_COLOR), ass_color(SHOUT_COLOR),
            ass_color("#000000"), ass_color("#000000", 0x50), True, 1, max(6, round(9 * scale)), 4,
        )
    )
    events = []
    cx, cy = round(cap.x * W), round(cap.y * H)
    spoken = [(s, e) for s, e, text in shouts if text]
    from .fonts import text_width

    def fit(*lines, size=cap.size) -> float:
        """Share of full size (<= 1) so the widest line stays inside the frame (long Kannada words...)."""
        room = W * 0.92
        widest = max(text_width(line, cap.font, size * scale) + 2 * cap.outline * scale for line in lines)
        return min(1.0, room / widest) if widest > 0 else 1.0

    def scaled(share: float) -> str:
        return "" if share >= 0.999 else f"\\fscx{share * 100:.0f}\\fscy{share * 100:.0f}"

    def under_shout(start, end):
        return any(start < e and end > s for s, e in spoken)

    if cap.style != "none":
        for group, group_end in caption_groups(cap, segments, duration):
            if under_shout(group[0]["start"], group_end):
                continue  # the shout word takes the caption's place for that moment
            texts = [mask(w["text"]) for w in group]
            if cap.uppercase:
                texts = [t.upper() for t in texts]
            start_t, end_t = ass_time(group[0]["start"]), ass_time(group_end)
            if cap.style in ("punch", "twotone"):
                hi, base, second = ass_color(cap.highlight), ass_color(cap.color), ass_color(cap.second)
                lines = [texts]
                if cap.style == "twotone" and len(texts) > 2:
                    cut = _balanced_cut(texts)
                    lines = [texts[:cut], texts[cut:]]
                key = key_word(lines[0])
                size = scaled(fit(*(" ".join(line) for line in lines)))
                # No border; a hard dark drop shadow plus a soft far one = a 3D look.
                d = max(3, round(6 * scale))
                hard = f"\\bord0\\xshad{d}\\yshad{round(d * 1.4)}\\4c&H000000&\\4a&H10&"
                soft_sh = f"\\bord0\\1a&HFF&\\xshad{round(d * 1.8)}\\yshad{round(d * 2.6)}\\4c&H000000&\\4a&H70&\\blur8"
                flat = [(0, j, t) for j, t in enumerate(lines[0])] + [(1, j, t) for j, t in enumerate(lines[1] if len(lines) == 2 else [])]

                def colour(line_no, j):
                    if line_no == 1:
                        return second
                    return hi if j == key else base

                def text_upto(shown: int, shadow: bool) -> str:
                    """Words 0..shown visible, the rest laid out but invisible (no jumping)."""
                    out, prev_line = [], 0
                    for n, (line_no, j, t) in enumerate(flat):
                        if line_no != prev_line:
                            out.append("\\N")
                            prev_line = line_no
                        elif n:
                            out.append(" ")
                        alpha = "&HFF&" if n > shown else ("&HFF&" if shadow else "&H00&")
                        tag = f"\\1a{alpha}" if shadow else f"\\c{colour(line_no, j)}\\alpha{alpha}"
                        if shadow:
                            tag += f"\\4a{'&HFF&' if n > shown else '&H70&'}"
                        out.append(f"{{{tag}}}{rich(t, cap.font)}")
                    return "".join(out)

                # One word at a time: first-colour line fills in, then the second colour.
                times = [w["start"] for w in group]
                for n in range(len(flat)):
                    s_t = times[n] if n < len(times) else times[-1]
                    e_t = times[n + 1] if n + 1 < len(times) else group_end
                    if e_t <= s_t:
                        continue
                    a_s, a_e = ass_time(s_t), ass_time(e_t)
                    events.append(f"Dialogue: 0,{a_s},{a_e},Caption,,0,0,0,,{{\\an5\\pos({cx},{cy})\\q2{soft_sh}{size}}}{text_upto(n, True)}")
                    events.append(f"Dialogue: 1,{a_s},{a_e},Caption,,0,0,0,,{{\\an5\\pos({cx},{cy})\\q2{hard}{size}}}{text_upto(n, False)}")
                continue
            anim = caption_animation(cap)
            cut = _balanced_cut(texts) if cap.style == "twoline" and len(texts) > 2 else None
            lines_text = [" ".join(texts)] if cut is None else [" ".join(texts[:cut]), " ".join(texts[cut:])]
            share = fit(*lines_text)
            lead = "" if cut is None else "\\q2"  # we broke the lines ourselves

            def join(pieces):
                if cut is None:
                    return " ".join(pieces)
                return " ".join(pieces[:cut]) + r"\N" + " ".join(pieces[cut:])

            def popped(s):
                a, b, c = (round(v * s) for v in (70, 108, 100))
                return f"\\fscx{a}\\fscy{a}\\t(0,90,\\fscx{b}\\fscy{b})\\t(90,160,\\fscx{c}\\fscy{c})"

            if anim in ("full_line", "pop_line"):
                head = popped(share) if anim == "pop_line" else scaled(share)
                events.append(
                    f"Dialogue: 1,{start_t},{end_t},Caption,,0,0,0,,"
                    f"{{\\an5\\pos({cx},{cy}){lead}{head}}}{join([rich(t, cap.font) for t in texts])}"
                )
                continue
            hi, base = ass_color(cap.highlight), ass_color(cap.color)
            size_now = round(share * 100)
            for i, word in enumerate(group):
                start = word["start"]
                end = group[i + 1]["start"] if i + 1 < len(group) else group_end
                if end <= start:
                    continue
                if anim == "one_by_one":
                    alone = fit(texts[i])
                    events.append(
                        f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,"
                        f"{{\\an5\\pos({cx},{cy}){popped(alone)}\\c{hi}}}{rich(texts[i], cap.font)}"
                    )
                    continue
                pieces = []
                for j, text in enumerate(texts):
                    piece = rich(text, cap.font)
                    if j == i:
                        if anim == "pop_word":
                            a, b = round(size_now * 0.75), round(size_now * 1.18)
                            piece = (f"{{\\c{hi}\\fscx{a}\\fscy{a}\\t(0,80,\\fscx{b}\\fscy{b})"
                                     f"\\t(80,150,\\fscx{size_now}\\fscy{size_now})}}{piece}"
                                     f"{{\\c{base}\\fscx{size_now}\\fscy{size_now}}}")
                        else:
                            piece = f"{{\\c{hi}}}{piece}{{\\c{base}}}"
                    elif j > i and anim == "build_up":
                        piece = f"{{\\alpha&HFF&}}{piece}{{\\alpha&H00&}}"  # laid out, not shown yet
                    pieces.append(piece)
                head = popped(share) if anim == "pop_auto" and i == 0 else scaled(share)
                events.append(
                    f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,"
                    f"{{\\an5\\pos({cx},{cy}){lead}{head}}}{join(pieces)}"
                )
    # Shout words: big, yellow, punched in with a little tilt.
    sy = cy if cap.style != "none" else round(0.45 * H)
    shout_css = min(200, max(cap.size * 1.45, 110))
    for s, e, text in shouts:
        if not text:
            continue
        share = fit(text, size=shout_css)
        a, b, c = (round(v * share) for v in (40, 122, 100))
        events.append(
            f"Dialogue: 4,{ass_time(s)},{ass_time(e)},Shout,,0,0,0,,"
            f"{{\\an5\\pos({cx},{sy})\\q2\\fscx{a}\\fscy{a}\\frz-5\\t(0,110,\\fscx{b}\\fscy{b}\\frz3)"
            f"\\t(110,230,\\fscx{c}\\fscy{c}\\frz0)}}{rich(text, cap.font)}"
        )
    text = (title_text if title_text is not None else title.text or "").strip()
    if text and title.show:
        end = duration if title.seconds <= 0 else min(duration, title.seconds)
        lines, em, tx, ty, rect = title_lines(design, text)
        # title_lines may shrink the title (long hook over the cam, very long word): draw it at that size.
        share = em / (title.size * scale) if title.size else 1.0
        fs = scaled(share)
        if rect:
            events.append(
                f"Dialogue: 2,{ass_time(0)},{ass_time(end)},Title,,0,0,0,,"
                f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{ass_color(title_box)}\\p1}}{rounded_rect(*rect, TITLE_RADIUS * scale)}{{\\p0}}"
            )
        step = TITLE_LINE * em
        for i, line in enumerate(lines):
            y = ty + (i - (len(lines) - 1) / 2) * step
            events.append(
                f"Dialogue: 3,{ass_time(0)},{ass_time(end)},Title,,0,0,0,,"
                f"{{\\an5\\pos({round(tx)},{round(y)})\\q2{fs}}}{rich(line, title.font)}"
            )
    if design.credit_on and design.credit.strip():
        name = credit_label(design)
        events.append(
            f"Dialogue: 3,{ass_time(0)},{ass_time(duration)},Credit,,0,0,0,,"
            f"{{\\an5\\pos({round(design.credit_x * W)},{round(design.credit_y * H)})}}{rich(name, 'Poppins ExtraBold')}"
        )
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            f"PlayResX: {W}",
            f"PlayResY: {H}",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            *styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *events,
        ]
    ) + "\n"
