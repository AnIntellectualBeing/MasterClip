"""Whole-clip overlays: social cards, the social handles bar, logo, background card and outro.

Everything is drawn here with Pillow into PNGs at the output size; render.py lays them on the video
with FFmpeg overlays, and /api/overlay-preview sends the same PNGs to the live preview, so the
preview and the video always match. Uploaded pictures / videos / songs live in <data>/brand.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .design import CANVAS, FONT_DIR, FONTS, Design

SS = 2  # draw at twice the size, then shrink: smooth edges on circles and rounded corners
WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
MAX_ASSET = {"image": 25 << 20, "video": 400 << 20, "audio": 60 << 20}

_asset_dir: Path | None = None


# --- uploaded files -----------------------------------------------------------


def set_asset_dir(path) -> None:
    global _asset_dir
    _asset_dir = Path(path)


def asset_dir() -> Path:
    if _asset_dir is None:
        return Path(os.environ.get("CLIPSTUDIO_DATA", str(Path(__file__).resolve().parent.parent / "data"))) / "brand"
    return _asset_dir


def asset_kind(name: str) -> str | None:
    ext = Path(str(name)).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return None


def asset_path(ident: str) -> Path | None:
    """The file behind an uploaded-file id, or None when it's blank or gone."""
    if not ident or not re.fullmatch(r"[0-9a-f]{16}\.[a-z0-9]{2,4}", ident):
        return None
    path = asset_dir() / ident
    return path if path.is_file() else None


def save_asset(stream, filename: str) -> dict:
    """Keep an uploaded picture / video / song. Same file twice = same id."""
    kind = asset_kind(filename)
    if not kind:
        raise ValueError("Use a picture (PNG, JPG, WEBP), a video (MP4, MOV, WEBM) or a song (MP3, WAV, M4A).")
    folder = asset_dir()
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f".upload-{os.getpid()}-{id(stream)}.part"
    digest, size = hashlib.sha256(), 0
    try:
        with part.open("wb") as out:
            while chunk := stream.read(1 << 20):
                size += len(chunk)
                if size > MAX_ASSET[kind]:
                    raise ValueError(f"That {kind} is too big (up to {MAX_ASSET[kind] >> 20} MB).")
                digest.update(chunk)
                out.write(chunk)
        if not size:
            raise ValueError("That file is empty.")
        ext = Path(filename).suffix.lower()
        if kind == "image":
            try:
                with Image.open(part) as img:
                    img.verify()
            except Exception as error:  # noqa: BLE001
                raise ValueError("That picture can't be opened. Try a PNG or JPG.") from error
        ident = digest.hexdigest()[:16] + ext
        final = folder / ident
        if final.exists():
            part.unlink()
        else:
            part.replace(final)
    finally:
        part.unlink(missing_ok=True)
    return {"id": ident, "kind": kind, "name": Path(filename).name[:80], "url": f"/api/brand-assets/{ident}"}


# --- fonts + small helpers ----------------------------------------------------


@lru_cache(maxsize=64)
def _font(weight: str, size: int, family: str = "") -> ImageFont.FreeTypeFont:
    """weight: regular / semibold / bold; family: one of design.FONTS for display text."""
    size = max(6, int(size))
    if family in FONTS:
        try:
            return ImageFont.truetype(str(FONT_DIR / FONTS[family]), size)
        except OSError:
            pass
    files = {"regular": "segoeui.ttf", "semibold": "seguisb.ttf", "bold": "segoeuib.ttf", "black": "seguibl.ttf"}
    for path in (WINDOWS_FONTS / files.get(weight, "segoeui.ttf"), WINDOWS_FONTS / "segoeuib.ttf",
                 FONT_DIR / FONTS["Poppins ExtraBold"]):
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _indic_font(text: str, size: int):
    """Hindi / Kannada / Tamil letters: Nirmala UI (every Indian script)."""
    if any(0x0900 <= ord(ch) <= 0x0DFF for ch in text):
        try:
            return ImageFont.truetype(str(WINDOWS_FONTS / "Nirmala.ttc"), max(6, int(size)))
        except OSError:
            return None
    return None


def _pick(weight: str, size: float, text: str, family: str = ""):
    return _indic_font(text, size) or _font(weight, int(size), family)


def wrap(text: str, font, width: float, max_lines: int = 8) -> list[str]:
    lines: list[str] = []
    for para in str(text).replace("\r", "").split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if font.getlength(trial) <= width or not line:
                while font.getlength(trial) > width and len(trial) > 1 and not line:
                    # one word wider than the box: break it
                    cut = len(trial)
                    while cut > 1 and font.getlength(trial[:cut]) > width:
                        cut -= 1
                    lines.append(trial[:cut])
                    trial = trial[cut:]
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    lines = [ln for ln in lines if ln.strip()] or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .") + "…"
    return lines


def _rgb(hex_color: str, alpha: int = 255):
    v = hex_color.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), alpha)


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def _gradient(size, c1, c2, horizontal=True) -> Image.Image:
    w, h = size
    strip = Image.new("RGBA", (256, 1))
    for i in range(256):
        strip.putpixel((i, 0), _mix(c1, c2, i / 255))
    img = strip.resize((w, h) if horizontal else (h, w), Image.BILINEAR)
    return img if horizontal else img.rotate(-90, expand=True)


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    scale = max(w / img.width, h / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _load(ident: str) -> Image.Image | None:
    path = asset_path(ident)
    if not path or asset_kind(path.name) != "image":
        return None
    try:
        with Image.open(path) as img:
            return img.convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def frame_size(design: Design) -> tuple[int, int]:
    base_w, base_h = CANVAS[design.aspect]
    scale = design.resolution / 1080
    return max(2, round(base_w * scale / 2) * 2), max(2, round(base_h * scale / 2) * 2)


def short_side(design: Design) -> int:
    return min(frame_size(design))


def channel(design: Design) -> str:
    return design.credit.strip().lstrip("@") or "yourchannel"


def _handle(text: str, design: Design) -> str:
    name = (text or channel(design)).strip()
    name = re.sub(r"\s+", "", name) if not text else name
    return name if name.startswith("@") else "@" + name


AVATAR_COLOURS = ["#FF5A3C", "#5EC8FF", "#7C5CFF", "#22C55E", "#F59E0B", "#EC4899", "#14B8A6"]


class Pen:
    """Draws in "units" (the card is 1000 units wide) on a supersampled canvas."""

    def __init__(self, width_px: int, height_units: float, unit_px: float):
        self.k = unit_px * SS
        self.img = Image.new("RGBA", (max(2, round(width_px * SS)), max(2, round(height_units * self.k))), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def p(self, *v):
        return [round(n * self.k) for n in v]

    def rect(self, x0, y0, x1, y1, r, fill, outline=None, width=0):
        self.d.rounded_rectangle(self.p(x0, y0, x1, y1), radius=round(r * self.k), fill=fill,
                                 outline=outline, width=round(width * self.k) if width else 0)

    def circle(self, cx, cy, r, fill=None, outline=None, width=0):
        self.d.ellipse(self.p(cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline,
                       width=round(width * self.k) if width else 0)

    def line(self, pts, fill, width, joint="curve"):
        self.d.line([tuple(self.p(x, y)) for x, y in pts], fill=fill, width=max(1, round(width * self.k)), joint=joint)

    def poly(self, pts, fill):
        self.d.polygon([tuple(self.p(x, y)) for x, y in pts], fill=fill)

    def font(self, weight, size, text="", family=""):
        return _pick(weight, size * self.k, text, family)

    def text(self, x, y, text, font, fill, anchor="lm"):
        self.d.text(tuple(self.p(x, y)), text, font=font, fill=fill, anchor=anchor)

    def length(self, text, font) -> float:
        return font.getlength(text) / self.k

    def paste(self, img: Image.Image, x, y):
        self.img.alpha_composite(img, tuple(self.p(x, y)))

    def avatar(self, cx, cy, r, ident, name):
        pic = _load(ident)
        size = max(2, round(2 * r * self.k))
        if pic is not None:
            pic = _cover(pic, size, size)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
            pic.putalpha(Image.composite(pic.getchannel("A"), mask, mask))
            self.img.alpha_composite(pic, tuple(self.p(cx - r, cy - r)))
            return
        colour = AVATAR_COLOURS[sum(map(ord, name or "x")) % len(AVATAR_COLOURS)]
        self.circle(cx, cy, r, fill=_rgb(colour))
        letter = next((ch for ch in (name or "") if ch.isalnum()), "?").upper()
        self.text(cx, cy, letter, self.font("bold", r * 1.05, letter), (255, 255, 255, 255), anchor="mm")

    def tick(self, cx, cy, r):
        """A blue round badge with a white check (generic "verified" mark)."""
        self.circle(cx, cy, r, fill=(29, 155, 240, 255))
        self.line([(cx - r * 0.45, cy + r * 0.02), (cx - r * 0.12, cy + r * 0.35), (cx + r * 0.48, cy - r * 0.3)],
                  (255, 255, 255, 255), r * 0.28)

    def done(self, width_px: int) -> Image.Image:
        h = max(2, round(self.img.height / SS))
        return self.img.resize((max(2, round(width_px)), h), Image.LANCZOS)


# --- small generic icons (drawn, not brand logos) ------------------------------


def _icon_reply(pen, x, y, s, c):
    pen.rect(x, y, x + s, y + s * 0.78, s * 0.35, None, c, s * 0.09)
    pen.poly([(x + s * 0.22, y + s * 0.7), (x + s * 0.2, y + s), (x + s * 0.48, y + s * 0.74)], c)


def _icon_repost(pen, x, y, s, c):
    w = s * 0.09
    pen.line([(x + s * 0.15, y + s * 0.7), (x + s * 0.15, y + s * 0.25), (x + s * 0.75, y + s * 0.25)], c, w, None)
    pen.poly([(x, y + s * 0.42), (x + s * 0.15, y + s * 0.2), (x + s * 0.3, y + s * 0.42)], c)
    pen.line([(x + s * 0.85, y + s * 0.3), (x + s * 0.85, y + s * 0.75), (x + s * 0.25, y + s * 0.75)], c, w, None)
    pen.poly([(x + s * 0.7, y + s * 0.58), (x + s * 0.85, y + s * 0.8), (x + s, y + s * 0.58)], c)


def _heart_points(x, y, s):
    pts = []
    for i in range(48):
        t = 2 * math.pi * i / 48
        hx = 16 * math.sin(t) ** 3
        hy = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((x + s / 2 + hx * s / 34, y + s * 0.45 - hy * s / 34))
    return pts


def _icon_heart(pen, x, y, s, c, filled=False):
    pts = _heart_points(x, y, s)
    if filled:
        pen.poly(pts, c)
    else:
        pen.line(pts + [pts[0]], c, s * 0.09)


def _icon_bars(pen, x, y, s, c):
    for i, hgt in enumerate((0.45, 0.8, 0.6)):
        bx = x + s * (0.12 + i * 0.3)
        pen.rect(bx, y + s * (1 - hgt), bx + s * 0.16, y + s, s * 0.05, c)


def _icon_thumb(pen, x, y, s, c, down=False):
    pts = [(0.1, 0.45), (0.3, 0.45), (0.3, 0.95), (0.1, 0.95), (0.1, 0.45), (0.3, 0.45), (0.5, 0.05),
           (0.62, 0.12), (0.56, 0.42), (0.92, 0.42), (0.82, 0.95), (0.3, 0.95)]
    if down:
        pts = [(px, 1 - py) for px, py in pts]
    pen.line([(x + px * s, y + py * s) for px, py in pts], c, s * 0.08)


def platform_icon(pen: Pen, platform: str, x, y, s):
    """Simple generic icons for the handles bar: a coloured tile with a plain symbol."""
    white = (255, 255, 255, 255)
    if platform == "youtube":
        pen.rect(x, y + s * 0.14, x + s, y + s * 0.86, s * 0.22, (255, 0, 51, 255))
        pen.poly([(x + s * 0.4, y + s * 0.33), (x + s * 0.4, y + s * 0.67), (x + s * 0.7, y + s * 0.5)], white)
    elif platform == "twitch":
        pen.rect(x, y, x + s, y + s, s * 0.22, (145, 70, 255, 255))
        pen.rect(x + s * 0.22, y + s * 0.22, x + s * 0.78, y + s * 0.66, s * 0.06, None, white, s * 0.08)
        pen.poly([(x + s * 0.32, y + s * 0.64), (x + s * 0.32, y + s * 0.84), (x + s * 0.5, y + s * 0.64)], white)
        pen.rect(x + s * 0.42, y + s * 0.33, x + s * 0.48, y + s * 0.5, 0, white)
        pen.rect(x + s * 0.56, y + s * 0.33, x + s * 0.62, y + s * 0.5, 0, white)
    elif platform == "kick":
        pen.rect(x, y, x + s, y + s, s * 0.22, (83, 252, 24, 255))
        pen.text(x + s / 2, y + s * 0.52, "K", pen.font("black", s * 0.72, "K"), (0, 0, 0, 255), anchor="mm")
    elif platform == "instagram":
        tile = _gradient((round(s * pen.k), round(s * pen.k)), _rgb("#F58529"), _rgb("#8134AF"))
        mask = Image.new("L", tile.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile.width - 1, tile.height - 1), radius=round(s * 0.26 * pen.k), fill=255)
        tile.putalpha(mask)
        pen.paste(tile, x, y)
        pen.rect(x + s * 0.2, y + s * 0.2, x + s * 0.8, y + s * 0.8, s * 0.18, None, white, s * 0.08)
        pen.circle(x + s * 0.5, y + s * 0.5, s * 0.14, None, white, s * 0.08)
        pen.circle(x + s * 0.67, y + s * 0.33, s * 0.04, white)
    elif platform == "tiktok":
        pen.rect(x, y, x + s, y + s, s * 0.22, (17, 17, 17, 255))
        for dx, colour in ((-0.04, (37, 244, 238, 255)), (0.04, (254, 44, 85, 255)), (0, white)):
            ox = x + s * dx
            pen.circle(ox + s * 0.4, y + s * 0.66, s * 0.13, colour)
            pen.rect(ox + s * 0.47, y + s * 0.2, ox + s * 0.55, y + s * 0.68, 0, colour)
            pen.line([(ox + s * 0.51, y + s * 0.24), (ox + s * 0.72, y + s * 0.36)], colour, s * 0.08)
    else:  # x
        pen.rect(x, y, x + s, y + s, s * 0.22, (17, 17, 17, 255))
        pen.line([(x + s * 0.28, y + s * 0.26), (x + s * 0.72, y + s * 0.74)], white, s * 0.09, None)
        pen.line([(x + s * 0.72, y + s * 0.26), (x + s * 0.28, y + s * 0.74)], white, s * 0.09, None)


# --- the cards ---------------------------------------------------------------


THEMES = {
    "light": dict(bg=(255, 255, 255, 255), text=(15, 20, 25, 255), muted=(96, 108, 120, 255), line=(228, 232, 236, 255),
                  track=(236, 238, 241, 255)),
    "dark": dict(bg=(22, 24, 28, 255), text=(236, 238, 240, 255), muted=(139, 152, 165, 255), line=(52, 56, 62, 255),
                 track=(48, 51, 56, 255)),
}
CARD_NAMES = {
    "x_post": "X post", "ig_comment": "Instagram comment", "ig_question": "Instagram question",
    "ig_poll": "Instagram poll", "yt_comment": "YouTube comment", "tiktok_hook": "TikTok hook", "news": "Breaking news",
}


def card_text(design: Design, title_text: str | None) -> str:
    return (design.card.text or title_text or design.title.text or "").strip() or "Your text here"


def _measure(weight, size, text, width, max_lines=8, k=1.0):
    font = _pick(weight, size * k * SS, text)
    return font, wrap(text, font, width * k * SS, max_lines)


def _x_post(card, design, text, cw):
    t = THEMES[card.theme]
    k = cw / 1000
    name = card.name or channel(design)
    handle = _handle(card.handle, design)
    font, lines = _measure("regular", 46, text, 1000 - 96, 8, k)
    body_h = len(lines) * 46 * 1.32
    h = 48 + 92 + 26 + body_h + 34 + 2 + 70
    pen = Pen(cw, h, k)
    pen.rect(0, 0, 1000, h, 40, t["bg"])
    pen.avatar(48 + 46, 48 + 46, 46, card.avatar, name)
    nf = pen.font("bold", 40, name)
    nx = 48 + 92 + 24
    shown = name
    while pen.length(shown, nf) > 1000 - nx - 120 and len(shown) > 2:
        shown = shown[:-2] + "…"
    pen.text(nx, 48 + 28, shown, nf, t["text"])
    if card.verified:
        pen.tick(nx + pen.length(shown, nf) + 26, 48 + 30, 18)
    pen.text(nx, 48 + 72, handle, pen.font("regular", 34, handle), t["muted"])
    y = 48 + 92 + 26
    for i, line in enumerate(lines):
        pen.text(48, y + (i + 0.5) * 46 * 1.32, line, font, t["text"])
    y += body_h + 34
    pen.rect(48, y, 1000 - 48, y + 2, 0, t["line"])
    y += 18
    s = 38
    for i, draw in enumerate((_icon_reply, _icon_repost, _icon_heart, _icon_bars)):
        draw(pen, 60 + i * 240, y, s, t["muted"])
    return pen.done(cw)


def _ig_comment(card, design, text, cw):
    t = THEMES[card.theme]
    k = cw / 1000
    handle = _handle(card.handle, design).lstrip("@")
    font, lines = _measure("regular", 40, text, 1000 - 40 - 80 - 28 - 110, 6, k)
    body_h = len(lines) * 40 * 1.3
    h = 40 + 44 + 8 + body_h + 16 + 40 + 32
    pen = Pen(cw, h, k)
    pen.rect(0, 0, 1000, h, 36, t["bg"])
    pen.avatar(40 + 40, 40 + 40, 40, card.avatar, handle)
    x = 40 + 80 + 28
    hf = pen.font("semibold", 34, handle)
    pen.text(x, 40 + 22, handle, hf, t["text"])
    if card.verified:
        pen.tick(x + pen.length(handle, hf) + 22, 40 + 24, 15)
    y = 40 + 44 + 8
    for i, line in enumerate(lines):
        pen.text(x, y + (i + 0.5) * 40 * 1.3, line, font, t["text"])
    y += body_h + 16
    pen.text(x, y + 20, "Reply", pen.font("semibold", 30, "Reply"), t["muted"])
    _icon_heart(pen, 1000 - 40 - 54, 52, 50, t["muted"])
    return pen.done(cw)


def _ig_question(card, design, text, cw):
    t = THEMES[card.theme]
    k = cw / 1000
    font, lines = _measure("bold", 50, text, 1000 - 120, 6, k)
    body_h = len(lines) * 50 * 1.28
    top = 64  # the avatar sits half above the card
    head = 150
    h = top + head + 50 + body_h + 60
    pen = Pen(cw, h, k)
    pen.rect(0, top, 1000, h, 44, t["bg"])
    band = _gradient((round(1000 * pen.k), round(head * pen.k)), _rgb("#7A3AED"), _rgb("#F97316"))
    mask = Image.new("L", band.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, band.width - 1, band.height + round(44 * pen.k)), radius=round(44 * pen.k), fill=255)
    band.putalpha(mask)
    pen.paste(band, 0, top)
    label = "Ask me a question"
    pen.text(500, top + head * 0.58, label, pen.font("bold", 46, label), (255, 255, 255, 255), anchor="mm")
    pen.circle(500, top, 62, t["bg"])
    pen.avatar(500, top, 54, card.avatar, card.name or channel(design))
    y = top + head + 50
    for i, line in enumerate(lines):
        pen.text(500, y + (i + 0.5) * 50 * 1.28, line, font, t["text"], anchor="mm")
    return pen.done(cw)


def _ig_poll(card, design, text, cw):
    t = THEMES[card.theme]
    k = cw / 1000
    font, lines = _measure("bold", 50, text, 1000 - 100, 4, k)
    body_h = len(lines) * 50 * 1.26
    opts = [o for o in card.options if o.text.strip()] or card.options
    row, gap = 96, 22
    h = 50 + body_h + 36 + len(opts) * (row + gap) - gap + 50
    pen = Pen(cw, h, k)
    pen.rect(0, 0, 1000, h, 44, t["bg"])
    for i, line in enumerate(lines):
        pen.text(500, 50 + (i + 0.5) * 50 * 1.26, line, font, t["text"], anchor="mm")
    y = 50 + body_h + 36
    best = max(o.pct for o in opts)
    for o in opts:
        x0, x1 = 50, 950
        pen.rect(x0, y, x1, y + row, row / 2, t["track"])
        fill_w = (x1 - x0) * o.pct / 100
        if fill_w > 4:
            colours = (_rgb("#7A3AED"), _rgb("#EC4899")) if o.pct == best else (_rgb("#9CA3AF"), _rgb("#9CA3AF"))
            bar = _gradient((max(2, round(fill_w * pen.k)), round(row * pen.k)), *colours)
            m = Image.new("L", bar.size, 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, bar.width - 1, bar.height - 1), radius=round(row / 2 * pen.k), fill=255)
            bar.putalpha(m)
            pen.paste(bar, x0, y)
        of = pen.font("bold", 38, o.text)
        on_fill = o.pct >= 45
        pen.text(x0 + 36, y + row / 2, o.text.strip() or "…", of, (255, 255, 255, 255) if on_fill else t["text"])
        pct = f"{o.pct}%"
        pen.text(x1 - 36, y + row / 2, pct, pen.font("bold", 36, pct), t["text"] if o.pct < 92 else (255, 255, 255, 255), anchor="rm")
        y += row + gap
    return pen.done(cw)


def _yt_comment(card, design, text, cw):
    t = dict(THEMES[card.theme])
    if card.theme == "dark":
        t["bg"] = (15, 15, 15, 255)
    k = cw / 1000
    handle = _handle(card.handle, design)
    font, lines = _measure("regular", 40, text, 1000 - 40 - 84 - 28 - 40, 6, k)
    body_h = len(lines) * 40 * 1.3
    h = 40 + 44 + 6 + body_h + 22 + 46 + 34
    pen = Pen(cw, h, k)
    pen.rect(0, 0, 1000, h, 28, t["bg"])
    pen.avatar(40 + 42, 40 + 42, 42, card.avatar, handle.lstrip("@"))
    x = 40 + 84 + 28
    hf = pen.font("semibold", 32, handle)
    pen.text(x, 40 + 22, handle, hf, t["text"])
    if card.verified:
        pen.tick(x + pen.length(handle, hf) + 20, 40 + 23, 14)
    y = 40 + 44 + 6
    for i, line in enumerate(lines):
        pen.text(x, y + (i + 0.5) * 40 * 1.3, line, font, t["text"])
    y += body_h + 22
    _icon_thumb(pen, x, y, 40, t["text"])
    _icon_thumb(pen, x + 110, y + 4, 40, t["text"], down=True)
    pen.text(x + 220, y + 22, "Reply", pen.font("semibold", 30, "Reply"), t["text"])
    return pen.done(cw)


def _tiktok_hook(card, design, text, cw):
    k = cw / 1000
    font, lines = _measure("bold", 58, text, 1000 - 40 - 100, 5, k)
    lh = 58 * 1.25
    widest = max(font.getlength(ln) for ln in lines) / (k * SS)
    bw = min(1000 - 40, widest + 100)
    h = 20 + 44 + len(lines) * lh + 44 + 20
    pen = Pen(cw, h, k)
    x0, x1 = 500 - bw / 2, 500 + bw / 2
    light = card.theme == "light"
    pen.rect(x0 - 8, 12, x1 - 8, h - 28, 28, (37, 244, 238, 255))
    pen.rect(x0 + 8, 28, x1 + 8, h - 12, 28, (254, 44, 85, 255))
    pen.rect(x0, 20, x1, h - 20, 28, (255, 255, 255, 255) if light else (18, 18, 18, 255))
    for i, line in enumerate(lines):
        pen.text(500, 20 + 44 + (i + 0.5) * lh, line, font, (10, 10, 10, 255) if light else (255, 255, 255, 255), anchor="mm")
    return pen.done(cw)


DRAW = {"x_post": _x_post, "ig_comment": _ig_comment, "ig_question": _ig_question, "ig_poll": _ig_poll,
        "yt_comment": _yt_comment, "tiktok_hook": _tiktok_hook}


def card_box(design: Design, img_size) -> tuple[int, int]:
    """Top-left pixel of a card centred on (card.x, card.y), kept inside the frame."""
    W, H = frame_size(design)
    w, h = img_size
    x = round(design.card.x * W - w / 2)
    y = round(design.card.y * H - h / 2)
    return max(0, min(W - w, x)), max(0, min(H - h, y))


def card_image(design: Design, title_text: str | None = None):
    """(PNG image, (x, y)) for the card, or None. News is a full-frame picture (see news_images)."""
    card = design.card
    if card.kind in ("none", "news"):
        return None
    cw = round(card.size * short_side(design))
    img = DRAW[card.kind](card, design, card_text(design, title_text), cw)
    W, H = frame_size(design)
    if img.height > H:
        img = img.resize((max(2, round(img.width * H / img.height)), H), Image.LANCZOS)
    return img, card_box(design, img.size)


NEWS_RED = (214, 32, 39, 255)


def news_images(design: Design, title_text: str | None = None):
    """(frame PNG, ticker strip PNG, ticker y, one ticker loop width px). The strip scrolls right to left."""
    W, H = frame_size(design)
    u = short_side(design) / 1000
    pen = Pen(W, H / u, u)
    Wu, Hu = W / u, H / u
    # top-left tag
    tag = "BREAKING NEWS"
    tf = pen.font("black", 40, tag)
    tw = pen.length(tag, tf)
    pen.rect(40, 50, 40 + tw + 56, 50 + 70, 10, NEWS_RED)
    pen.text(40 + 28, 50 + 35, tag, tf, (255, 255, 255, 255))
    lx = 40 + tw + 56 + 16
    pen.rect(lx, 58, lx + 132, 58 + 54, 27, (0, 0, 0, 170))
    pen.circle(lx + 30, 58 + 27, 10, (255, 59, 48, 255))
    pen.text(lx + 50, 58 + 27, "LIVE", pen.font("bold", 30, "LIVE"), (255, 255, 255, 255))
    # lower third
    text = card_text(design, title_text)
    label_w = 230
    font, lines = _measure("bold", 50, text.upper(), Wu - 60 - label_w - 50, 2, u)
    lh = 50 * 1.22
    bar_h = max(110, len(lines) * lh + 40)
    ticker_h = 64
    y0 = Hu * (0.74 if design.aspect == "9:16" else 0.7) - bar_h / 2
    y0 = min(y0, Hu - bar_h - ticker_h - 30)
    pen.rect(30, y0, Wu - 30, y0 + bar_h, 0, (255, 255, 255, 255))
    pen.rect(30, y0, 30 + label_w, y0 + bar_h, 0, NEWS_RED)
    pen.text(30 + label_w / 2, y0 + bar_h / 2, "BREAKING", pen.font("black", 38, "BREAKING"), (255, 255, 255, 255), anchor="mm")
    for i, line in enumerate(lines):
        pen.text(30 + label_w + 30, y0 + 20 + (i + 0.5) * lh, line, font, (17, 17, 17, 255))
    ty = y0 + bar_h
    nw = 150
    pen.rect(30, ty, 30 + nw, ty + ticker_h, 0, (255, 204, 0, 255))
    pen.text(30 + nw / 2, ty + ticker_h / 2, "NEWS", pen.font("black", 32, "NEWS"), (17, 17, 17, 255), anchor="mm")
    frame = pen.done(W)
    # ticker strip: the text repeated, on the dark bar colour
    ticker = (design.card.ticker or f"{text}  •  Follow {_handle(design.card.handle, design)} for more").strip()
    unit_text = ticker + "   •   "
    sf = _pick("bold", 34 * u, unit_text)
    loop_w = max(40, round(sf.getlength(unit_text)))
    strip_h = max(2, round(ticker_h * u))
    reps = max(2, math.ceil(W / loop_w) + 2)
    strip = Image.new("RGBA", (loop_w * reps, strip_h), (17, 17, 17, 235))
    sd = ImageDraw.Draw(strip)
    for r in range(reps):
        sd.text((r * loop_w, strip_h / 2), unit_text, font=sf, fill=(255, 255, 255, 255), anchor="lm")
    return frame, strip, round(ty * u), loop_w, round((30 + nw) * u)


# --- brand ----------------------------------------------------------------------


def ticker_width(design: Design, x0: int) -> int:
    """Width of the ticker window: from after the NEWS label to the bar's right edge."""
    W, _ = frame_size(design)
    return max(2, (W - round(30 * short_side(design) / 1000) - x0) // 2 * 2)


def handles_image(design: Design):
    """(PNG, (x, y)) for the social handles bar, or None."""
    brand = design.brand
    items = [h for h in brand.handles if h.name.strip()]
    if not items:
        return None
    W, H = frame_size(design)
    scale = short_side(design) / 1080
    size = brand.handles_size * scale  # icon + text height in px
    pad, gap, icon_gap = size * 0.45, size * 0.9, size * 0.32
    font = _pick("bold", size * 0.78 * SS, " ".join(h.name for h in items))
    labels = [h.name.strip() if not h.name.strip().startswith("@") else h.name.strip() for h in items]
    widths = [size + icon_gap + font.getlength(lb) / SS for lb in labels]
    total = sum(widths) + gap * (len(items) - 1) + 2 * pad
    shrink = min(1.0, W * 0.96 / total)
    unit = scale * shrink
    width_px = total * shrink
    height_units = (size + 2 * pad * 0.7) / scale
    pen = Pen(width_px, height_units, unit)
    s = size / scale
    pen.rect(0, 0, width_px / unit, height_units, height_units / 2, (0, 0, 0, 150))
    x = pad / scale
    f = pen.font("bold", s * 0.78, " ".join(labels))
    for item, label, w in zip(items, labels, widths):
        platform_icon(pen, item.platform, x, (height_units - s) / 2, s)
        pen.text(x + s + icon_gap / scale, height_units / 2, label, f, (255, 255, 255, 255))
        x += w / scale + gap / scale
    img = pen.done(width_px)
    px = round(brand.handles_x * W - img.width / 2)
    py = round(brand.handles_y * H - img.height / 2)
    return img, (max(0, min(W - img.width, px)), max(0, min(H - img.height, py)))


def logo_image(design: Design):
    """(PNG, (x, y)) for the logo in its corner, or None."""
    brand = design.brand
    pic = _load(brand.logo)
    if pic is None:
        return None
    W, H = frame_size(design)
    side = short_side(design)
    w = max(8, round(brand.logo_size * side))
    h = max(8, round(pic.height * w / pic.width))
    if h > H * 0.4:
        h = round(H * 0.4)
        w = max(8, round(pic.width * h / pic.height))
    img = pic.resize((w, h), Image.LANCZOS)
    alpha = img.getchannel("A").point(lambda a: round(a * brand.logo_opacity))
    img.putalpha(alpha)
    m = round(0.035 * side)
    x = m if brand.logo_corner in ("tl", "bl") else W - w - m
    y = m if brand.logo_corner in ("tl", "tr") else H - h - m
    return img, (x, y)


def frame_parts(design: Design):
    """Background card: (inner x, y, w, h, rounded mask PNG, background PNG or None for "blurred video")."""
    brand = design.brand
    if brand.frame == "none":
        return None
    W, H = frame_size(design)
    m = round(brand.frame_pad * short_side(design) / 2) * 2
    w, h = W - 2 * m, H - 2 * m
    r = brand.frame_radius * short_side(design) / 1080
    mask = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=round(r * SS), fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    bg = None
    if brand.frame == "color":
        bg = Image.new("RGBA", (W, H), _rgb(brand.frame_color))
    elif brand.frame == "gradient":
        bg = _gradient((W, H), _rgb(brand.frame_color), _rgb(brand.frame_color2), horizontal=False)
    elif brand.frame == "image":
        pic = _load(brand.frame_image)
        if pic is None:
            bg = Image.new("RGBA", (W, H), _rgb(brand.frame_color))
        else:
            bg = _cover(pic, W, H).filter(ImageFilter.GaussianBlur(max(2, W / 120)))
            bg = Image.alpha_composite(bg, Image.new("RGBA", (W, H), (0, 0, 0, 90)))
    return m, m, w, h, mask, bg


# --- outro ------------------------------------------------------------------------


def outro_image(design: Design) -> Image.Image:
    o = design.outro
    W, H = frame_size(design)
    u = short_side(design) / 1000
    top = _rgb(o.bg)
    bottom = _mix(top, (0, 0, 0, 255), 0.45)
    img = _gradient((W, H), top, bottom, horizontal=False)
    pen = Pen(W, H / u, u)
    Wu, Hu = W / u, H / u
    cx = Wu / 2
    accent = _rgb(o.accent)
    pic_r = 150 if design.aspect != "16:9" else 120
    cy = Hu * 0.36 if design.aspect != "16:9" else Hu * 0.3
    pen.circle(cx, cy, pic_r + 12, accent)
    pen.avatar(cx, cy, pic_r, o.picture or design.card.avatar, channel(design))
    font =_pick("regular", 120 * u * SS, o.text, o.font)
    lines = wrap(o.text or "Follow for more", font, Wu * 0.86 * u * SS, 3)
    lh = 120 * 1.1
    y = cy + pic_r + 80
    for i, line in enumerate(lines):
        pen.text(cx, y + (i + 0.5) * lh, line, font, _rgb(o.color), anchor="mm")
    y += len(lines) * lh + 40
    sub = o.sub.strip() or _handle("", design)
    sf = pen.font("bold", 54, sub)
    pen.text(cx, y + 30, sub, sf, accent, anchor="mm")
    y += 110
    label = "+  FOLLOW"
    lf = pen.font("black", 44, label)
    bw = pen.length(label, lf) + 110
    pen.rect(cx - bw / 2, y, cx + bw / 2, y + 96, 48, accent)
    dark = sum(accent[:3]) > 380
    pen.text(cx, y + 48, label, lf, (17, 17, 17, 255) if dark else (255, 255, 255, 255), anchor="mm")
    img.alpha_composite(pen.done(W))
    return img


def outro_seconds(design: Design) -> float:
    """How long the outro adds (0 when off). A video outro is capped at 10 s."""
    o = design.outro
    if o.mode == "card":
        return float(o.seconds)
    if o.mode == "video" and asset_path(o.video):
        from .media import probe_media

        try:
            return round(min(10.0, max(0.5, probe_media(asset_path(o.video)).duration)), 3)
        except Exception:  # noqa: BLE001
            return 0.0
    return 0.0


def music_file(design: Design) -> Path | None:
    path = asset_path(design.music.file)
    return path if path and asset_kind(path.name) in ("audio", "video") else None


def music_volume_expr(design: Design, total: float) -> str:
    """Volume over time: steady, then a straight rise to the peak over the last `rise` seconds."""
    m = design.music
    v, p, r = round(m.volume, 3), round(m.peak, 3), round(min(m.rise, max(0.0, total - 0.5)), 3)
    if r <= 0 or p <= v:
        return f"{v}"
    start = round(total - r, 3)
    return f"{v}+({p}-{v})*clip((t-{start})/{r},0,1)"  # render.py puts it in quotes


def has_extras(design: Design) -> bool:
    b = design.brand
    return (design.card.kind != "none" or b.frame != "none" or bool(b.logo)
            or any(h.name.strip() for h in b.handles))


def preview_png(design: Design, part: str, title_text: str | None = None) -> Image.Image | None:
    """What the live preview shows for one part, drawn exactly as in the video (1080 frame)."""
    design = design.model_copy(update={"resolution": 1080})
    if part == "card":
        if design.card.kind == "news":
            frame, strip, ty, _, x0 = news_images(design, title_text)
            out = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            out.alpha_composite(strip.crop((0, 0, ticker_width(design, x0), strip.height)), (x0, ty))
            out.alpha_composite(frame)
            return out
        made = card_image(design, title_text)
        return made[0] if made else None
    if part == "handles":
        made = handles_image(design)
        return made[0] if made else None
    if part == "logo":
        made = logo_image(design)
        return made[0] if made else None
    if part == "outro":
        return outro_image(design)
    return None
