"""Clip design: templates, layouts and the FFmpeg filter that draws them.

Every position is normalised (0..1) so the same numbers drive the browser
preview (static/editor.js) and this renderer. Keep the two in step.
"""

from __future__ import annotations

import re
import struct
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CANVAS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}

# Family name (what ASS asks for) -> bundled file in fonts/.
FONTS = {
    "Anton": "Anton-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Poppins ExtraBold": "Poppins-ExtraBold.ttf",
    "Bangers": "Bangers-Regular.ttf",
    "Archivo Black": "ArchivoBlack-Regular.ttf",
    "Lilita One": "LilitaOne-Regular.ttf",
    "Luckiest Guy": "LuckiestGuy-Regular.ttf",
    "Permanent Marker": "PermanentMarker-Regular.ttf",
}

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"


def usable_font(family: str) -> bool:
    """A bundled font, or a font installed on this PC (captions only)."""
    if family in FONTS:
        return True
    from .fonts import installed_fonts

    return isinstance(family, str) and family in installed_fonts()


def _font_path(family: str) -> Path:
    if family in FONTS:
        return FONT_DIR / FONTS[family]
    from .fonts import installed_fonts

    return installed_fonts()[family]


@lru_cache(maxsize=None)
def font_height_ratio(family: str) -> float:
    """libass sizes text by the font's Windows ascent+descent, browsers by the em.
    Multiply a CSS-style size by this so the video matches the preview."""
    try:
        data = _font_path(family).read_bytes()
        if data[:4] == b"ttcf":
            return 1.0  # font collections: keep 1.0 rather than mis-read the offsets
        tables = {}
        for i in range(struct.unpack(">H", data[4:6])[0]):
            tag, _, offset, _ = struct.unpack(">4sIII", data[12 + 16 * i : 28 + 16 * i])
            tables[tag] = offset
        units = struct.unpack(">H", data[tables[b"head"] + 18 : tables[b"head"] + 20])[0]
        os2 = tables[b"OS/2"]
        ascent, descent = struct.unpack(">HH", data[os2 + 74 : os2 + 78])
        ratio = (ascent + descent) / units
        return ratio if 0.8 <= ratio <= 2.5 else 1.0
    except (KeyError, OSError, struct.error, ZeroDivisionError):
        return 1.0


def _hex(value: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("Colours must look like #FFAA00.")
    return value.upper()


def _rect(value):
    if value is None:
        return None
    if len(value) != 4:
        raise ValueError("A box needs x, y, width and height.")
    x, y, w, h = (float(v) for v in value)
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError("Box values must be between 0 and 1.")
    w, h = min(w, 1 - x), min(h, 1 - y)
    if w < 0.02 or h < 0.02:
        raise ValueError("That box is too small.")
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


class CaptionStyle(BaseModel):
    # pop = one word at a time pops in, karaoke = the spoken word lights up, twoline = up to 2 lines,
    # boxed = dark box behind, classic = plain line (meme template),
    # punch = one bold line, the strongest word in the highlight colour, soft shadow,
    # twotone = two lines: first line with a coloured key word, second line in `second` with a glow,
    # none = off
    style: Literal["pop", "karaoke", "twoline", "classic", "boxed", "punch", "twotone", "none"] = "pop"
    font: str = "Anton"
    size: int = Field(default=86, ge=24, le=220)  # px on a 1080-wide frame
    color: str = "#FFFFFF"
    highlight: str = "#FFD233"
    second: str = "#3BFF5C"  # second line of "twotone"
    outline: int = Field(default=7, ge=0, le=24)
    outline_color: str = "#000000"
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.68, ge=0, le=1)
    uppercase: bool = True
    words: int = Field(default=2, ge=1, le=10)
    emoji: bool = False  # a fitting emoji above the caption (see emoji.py)
    # How the words move (pop/karaoke/twoline/classic/boxed; punch + twotone always build up):
    # auto = what the style always did, highlight = spoken word coloured, pop_word = spoken word
    # coloured and pops, pop_line = the whole line pops in, one_by_one = only the spoken word,
    # build_up = words appear as they are said, full_line = the line just sits there.
    animation: Literal["auto", "highlight", "pop_word", "pop_line", "one_by_one", "build_up", "full_line"] = "auto"

    _colors = field_validator("color", "highlight", "outline_color", "second")(_hex)

    @field_validator("font")
    @classmethod
    def known_font(cls, value):
        return value if usable_font(value) else "Anton"


class TitleStyle(BaseModel):
    text: str = Field(default="", max_length=140)
    style: Literal["white_box", "yellow", "red_box", "meme", "outline"] = "white_box"
    font: str = "Poppins ExtraBold"
    size: int = Field(default=70, ge=24, le=200)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.13, ge=0, le=1)
    seconds: float = Field(default=0.0, ge=0, le=600)  # 0 = whole clip (default: the title stays on)
    show: bool = True

    @field_validator("font")
    @classmethod
    def known_font(cls, value):
        return value if value in FONTS else "Poppins ExtraBold"


class Design(BaseModel):
    template: str = Field(default="big_captions", max_length=40)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    resolution: Literal[720, 1080, 2160] = 1080
    layout: Literal["full", "stack", "corner", "split", "meme"] = "full"
    fill: Literal["blur", "bars", "zoom"] = "zoom"
    cam_roi: list[float] | None = None  # streamer cam inside the source
    cam_rect: list[float] | None = None  # where the cam goes in the output (any cam layout)
    cam_side: Literal["top", "bottom"] = "top"
    # Remove the streamer's cam that is burned into the stream picture (it gets blurred out).
    # When the cam is shown in its own box (stack/corner) the original spot is always covered.
    hide_cam: bool = False
    split: float = Field(default=0.4, ge=0.2, le=0.7)
    focus_x: float = Field(default=0.5, ge=0, le=1)
    game_roi: list[float] | None = None
    game_rect: list[float] | None = None  # where the gameplay goes in the output (dragged by the buyer)
    meme_bar: float = Field(default=0.24, ge=0.1, le=0.45)
    caption: CaptionStyle = Field(default_factory=CaptionStyle)
    title: TitleStyle = Field(default_factory=TitleStyle)
    credit: str = Field(default="", max_length=60)
    credit_on: bool = True
    credit_x: float = Field(default=0.5, ge=0, le=1)
    credit_y: float = Field(default=0.965, ge=0, le=1)
    credit_size: int = Field(default=30, ge=14, le=120)
    # When someone shouts: the shouted word big in yellow, a quick shake, and (sfx) a boom sound.
    shout: bool = False
    sfx: bool = True

    _rects = field_validator("cam_roi", "cam_rect", "game_roi", "game_rect")(_rect)


TEMPLATES = [
    {
        "id": "streamer_top",
        "kind": "short",
        "name": "Streamer top + gameplay",
        "blurb": "Face cam on top, gameplay below, captions on the line between.",
        "length": 30,
        "needs_cam": True,
        "design": {
            "aspect": "9:16", "layout": "stack", "cam_side": "top", "split": 0.4, "fill": "zoom", "shout": True,
            "caption": {"style": "pop", "y": 0.4, "size": 92, "words": 2},
            "title": {"style": "white_box", "y": 0.07},
        },
    },
    {
        "id": "hype_punch",
        "kind": "short",
        "name": "Hype: bold line + red word",
        "blurb": "Streamer on top. One bold line on the split with the strongest word in red. Shouts hit hard.",
        "length": 30,
        "needs_cam": True,
        "design": {
            "aspect": "9:16", "layout": "stack", "cam_side": "top", "split": 0.42, "fill": "zoom", "shout": True,
            "caption": {"style": "punch", "y": 0.42, "size": 74, "words": 4, "font": "Poppins ExtraBold",
                        "color": "#FFFFFF", "highlight": "#FF2D2D", "outline": 4},
            "title": {"style": "white_box", "y": 0.07, "seconds": 0},
        },
    },
    {
        "id": "hype_twotone",
        "kind": "short",
        "name": "Hype: two colours + glow",
        "blurb": "Streamer on top. Two lines: white with a red word, then glowing green. Shouts hit hard.",
        "length": 30,
        "needs_cam": True,
        "design": {
            "aspect": "9:16", "layout": "stack", "cam_side": "top", "split": 0.42, "fill": "zoom", "shout": True,
            "caption": {"style": "twotone", "y": 0.42, "size": 72, "words": 6, "font": "Poppins ExtraBold",
                        "color": "#FFFFFF", "highlight": "#FF2D2D", "second": "#3BFF5C", "outline": 4},
            "title": {"style": "white_box", "y": 0.07, "seconds": 0},
        },
    },
    {
        "id": "big_captions",
        "kind": "short",
        "name": "Full gameplay + big captions",
        "blurb": "Gameplay fills the screen. Big bold words pop as they're said.",
        "length": 30,
        "needs_cam": False,
        "design": {
            "aspect": "9:16", "layout": "full", "fill": "zoom", "shout": True,
            "caption": {"style": "pop", "y": 0.62, "size": 104, "words": 1, "font": "Luckiest Guy"},
            "title": {"style": "yellow", "y": 0.14, "font": "Luckiest Guy"},
        },
    },
    {
        "id": "reaction_corner",
        "kind": "short",
        "name": "Reaction corner cam",
        "blurb": "Gameplay full screen with the streamer's face in a corner bubble.",
        "length": 30,
        "needs_cam": True,
        "design": {
            "aspect": "9:16", "layout": "corner", "fill": "zoom", "cam_rect": [0.05, 0.05, 0.44, 0.2], "shout": True,
            "caption": {"style": "karaoke", "y": 0.72, "size": 78, "words": 4, "font": "Poppins ExtraBold"},
            "title": {"style": "red_box", "y": 0.3},
        },
    },
    {
        "id": "podcast_split",
        "kind": "short",
        "name": "Podcast split",
        "blurb": "Two people side by side become top and bottom.",
        "length": 45,
        "needs_cam": False,
        "design": {
            "aspect": "9:16", "layout": "split", "fill": "zoom",
            "caption": {"style": "boxed", "y": 0.5, "size": 64, "words": 5, "uppercase": False, "font": "Poppins ExtraBold"},
            "title": {"style": "white_box", "y": 0.08},
        },
    },
    {
        "id": "meme_hook",
        "kind": "short",
        "name": "Meme: big top bar",
        "blurb": "A white bar fills the top with a big hook line, video below.",
        "length": 15,
        "needs_cam": False,
        "design": {
            "aspect": "9:16", "layout": "meme", "fill": "bars", "meme_bar": 0.26,
            "caption": {"style": "classic", "y": 0.86, "size": 62, "words": 6, "uppercase": False, "font": "Poppins ExtraBold"},
            "title": {"style": "meme", "y": 0.13, "size": 104, "seconds": 0, "font": "Anton"},
        },
    },
    {
        "id": "meme_caption",
        "kind": "short",
        "name": "Meme: caption on top",
        "blurb": "Video fills the screen. The hook sits on top in a white box just around the words; normal captions below.",
        "length": 30,
        "needs_cam": False,
        "design": {
            "aspect": "9:16", "layout": "full", "fill": "zoom",
            "caption": {"style": "pop", "y": 0.7, "size": 86, "words": 2, "font": "Anton"},
            "title": {"style": "white_box", "y": 0.12, "size": 64, "seconds": 0, "font": "Poppins ExtraBold"},
        },
    },
]


LONG_TEMPLATES = [
    {
        "id": "yt_full",
        "kind": "long",
        "name": "Full gameplay",
        "blurb": "The whole picture, clean captions at the bottom.",
        "length": 60,
        "needs_cam": False,
        "design": {
            "aspect": "16:9", "layout": "full", "fill": "zoom", "credit_on": False,
            "caption": {"style": "twoline", "y": 0.84, "size": 58, "words": 8, "uppercase": False, "font": "Poppins ExtraBold", "outline": 5},
            "title": {"style": "outline", "y": 0.1, "size": 64},
        },
    },
    {
        "id": "yt_big_captions",
        "kind": "long",
        "name": "Gameplay + big captions",
        "blurb": "Bold words pop in as they're said. Great for funny moments.",
        "length": 60,
        "needs_cam": False,
        "design": {
            "aspect": "16:9", "layout": "full", "fill": "zoom", "credit_on": False, "shout": True,
            "caption": {"style": "pop", "y": 0.78, "size": 92, "words": 2, "font": "Luckiest Guy"},
            "title": {"style": "yellow", "y": 0.12, "size": 72, "font": "Luckiest Guy"},
        },
    },
    {
        "id": "yt_cam_corner",
        "kind": "long",
        "name": "Gameplay + big face cam",
        "blurb": "The streamer's cam made bigger in the corner.",
        "length": 60,
        "needs_cam": True,
        "design": {
            "aspect": "16:9", "layout": "corner", "fill": "zoom", "cam_rect": [0.69, 0.05, 0.28, 0.3], "credit_on": False,
            "caption": {"style": "karaoke", "y": 0.86, "size": 66, "words": 4, "font": "Poppins ExtraBold"},
            "title": {"style": "red_box", "x": 0.3, "y": 0.1, "size": 56},
        },
    },
    {
        "id": "yt_punch",
        "kind": "long",
        "name": "Hype: bold line + red word",
        "blurb": "Full gameplay, one bold line with the strongest word in red. Shouts hit hard.",
        "length": 60,
        "needs_cam": False,
        "design": {
            "aspect": "16:9", "layout": "full", "fill": "zoom", "credit_on": False, "shout": True,
            "caption": {"style": "punch", "y": 0.84, "size": 64, "words": 5, "font": "Poppins ExtraBold",
                        "color": "#FFFFFF", "highlight": "#FF2D2D", "outline": 4},
            "title": {"style": "white_box", "y": 0.1, "size": 56},
        },
    },
    {
        "id": "yt_boxed",
        "kind": "long",
        "name": "Talk show captions",
        "blurb": "Easy-to-read captions on a soft dark box.",
        "length": 60,
        "needs_cam": False,
        "design": {
            "aspect": "16:9", "layout": "full", "fill": "zoom", "credit_on": False,
            "caption": {"style": "boxed", "y": 0.85, "size": 54, "words": 6, "uppercase": False, "font": "Poppins ExtraBold"},
            "title": {"style": "white_box", "y": 0.1, "size": 56},
        },
    },
    {
        "id": "yt_meme_top",
        "kind": "long",
        "name": "Meme: big top bar",
        "blurb": "A white bar on top with the hook line, the video below it.",
        "length": 60,
        "needs_cam": False,
        "design": {
            "aspect": "16:9", "layout": "meme", "fill": "blur", "meme_bar": 0.2, "credit_on": False,
            "caption": {"style": "twoline", "y": 0.88, "size": 54, "words": 8, "uppercase": False, "font": "Poppins ExtraBold", "outline": 5},
            "title": {"style": "meme", "y": 0.1, "size": 84, "seconds": 0, "font": "Anton"},
        },
    },
    {
        "id": "yt_meme_caption",
        "kind": "long",
        "name": "Meme: caption on top",
        "blurb": "Full video. The hook sits on top in a white box just around the words; normal captions below.",
        "length": 60,
        "needs_cam": False,
        "design": {
            "aspect": "16:9", "layout": "full", "fill": "zoom", "credit_on": False,
            "caption": {"style": "twoline", "y": 0.86, "size": 58, "words": 8, "uppercase": False, "font": "Poppins ExtraBold", "outline": 5},
            "title": {"style": "white_box", "y": 0.1, "size": 56, "seconds": 0, "font": "Poppins ExtraBold"},
        },
    },
    {
        "id": "yt_clean",
        "kind": "long",
        "name": "Clean (no captions)",
        "blurb": "Just the gameplay, nothing on top.",
        "length": 60,
        "needs_cam": False,
        "design": {
            "aspect": "16:9", "layout": "full", "fill": "zoom", "credit_on": False,
            "caption": {"style": "none"},
            "title": {"style": "outline", "y": 0.1, "show": False},
        },
    },
]
TEMPLATES += LONG_TEMPLATES


def template_design(template_id: str, extra_templates=(), **overrides) -> Design:
    """A Design filled from a template (built-in or the buyer's own), then from explicit overrides."""
    pool = [*TEMPLATES, *extra_templates]
    base = next((t for t in pool if t["id"] == template_id), TEMPLATES[1])
    data = {"template": base["id"], **base["design"]}
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        elif value is not None:
            data[key] = value
    return Design(**data)


# --- geometry --------------------------------------------------------------


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def _px(rect, width, height):
    """Normalised rect -> even pixel rect clamped inside width x height."""
    x, y, w, h = rect
    px, py = _even(x * width) if x > 0 else 0, _even(y * height) if y > 0 else 0
    pw, ph = _even(w * width), _even(h * height)
    pw, ph = min(pw, width - px), min(ph, height - py)
    return px, py, max(2, pw // 2 * 2), max(2, ph // 2 * 2)


def game_region(design: Design):
    """Part of the source that holds gameplay (the cam strip removed when stacking)."""
    if design.game_roi:
        return design.game_roi
    # The whole frame, so the gameplay under the cam stays centred on the action (cutting the cam's
    # strip off pushed the picture left or right). Where the stream's cam overlaps it, it is blurred.
    return [0.0, 0.0, 1.0, 1.0]


def effective_layout(design: Design) -> str:
    layout = design.layout
    if layout == "meme" and not design.title.show:
        return "full"  # title removed: no empty white bar on top
    if layout in ("stack", "corner") and not design.cam_roi:
        return "full"  # no cam found or drawn yet
    if design.aspect != "9:16" and layout in ("stack", "split"):
        return "corner" if layout == "stack" else "full"
    return layout


def cam_output_rect(design: Design):
    layout = effective_layout(design)
    if layout == "stack":
        if design.cam_rect:
            return design.cam_rect
        band = design.split
        return [0.0, 0.0, 1.0, band] if design.cam_side == "top" else [0.0, 1 - band, 1.0, band]
    if layout == "corner":
        return design.cam_rect or [0.05, 0.05, 0.4, 0.2]
    return None


def game_area(design: Design):
    layout = effective_layout(design)
    if layout == "full" and design.layout in ("stack", "corner"):
        return [0.0, 0.0, 1.0, 1.0]  # no streamer cam found: the gameplay fills the whole frame
    if design.game_rect and layout != "split":
        return design.game_rect
    if layout == "stack":
        band = design.split
        return [0.0, band, 1.0, 1 - band] if design.cam_side == "top" else [0.0, 0.0, 1.0, 1 - band]
    if layout == "meme":
        return [0.0, design.meme_bar, 1.0, 1 - design.meme_bar]
    return [0.0, 0.0, 1.0, 1.0]


def cam_cover(design: Design, game_px, src_w: int, src_h: int):
    """Pixel box (inside the cropped gameplay) to blur so the stream's own cam disappears, or None.
    Done when the buyer removed the cam, or when the cam is shown in its own box anyway."""
    if not design.cam_roi or effective_layout(design) == "split":
        return None
    if not (design.hide_cam or cam_output_rect(design)):
        return None
    if not design.hide_cam and design.cam_roi[2] * design.cam_roi[3] > 0.12:
        return None  # a cam box this big is a bad detection: blurring it would smear the gameplay
    gx, gy, gw, gh = game_px
    cx, cy, cw, ch = _px(design.cam_roi, src_w, src_h)
    x0, y0 = max(cx, gx), max(cy, gy)
    x1, y1 = min(cx + cw, gx + gw), min(cy + ch, gy + gh)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None  # the cam isn't inside the part of the picture we use
    rx, ry = (x0 - gx) // 2 * 2, (y0 - gy) // 2 * 2
    return rx, ry, min((x1 - x0) // 2 * 2, gw - rx), min((y1 - y0) // 2 * 2, gh - ry)


def _fit(label_in, label_out, width, height, fill, focus_x):
    if fill == "bars":
        return (
            f"[{label_in}]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[{label_out}]"
        )
    if fill == "blur":
        bw, bh = _even(width / 8), _even(height / 8)
        return (
            f"[{label_in}]split[{label_out}a][{label_out}b];"
            f"[{label_out}a]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=4:2,scale={width}:{height},setsar=1[{label_out}bg];"
            f"[{label_out}b]scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"setsar=1[{label_out}fg];"
            f"[{label_out}bg][{label_out}fg]overlay=(W-w)/2:(H-h)/2[{label_out}]"
        )
    fx = min(1.0, max(0.0, float(focus_x)))
    return (
        f"[{label_in}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-{width})*{fx:.4f}:(ih-{height})/2,setsar=1[{label_out}]"
    )


def is_portrait(src_w: int, src_h: int) -> bool:
    return src_h > src_w * 1.15


def adapt_to_source(design: Design, src_w: int, src_h: int) -> Design:
    """A vertical stream (phone / IRL / vertical canvas) is already the streamer and the action in
    one picture: no cam box, no split, nothing blurred out. In a 16:9 or square video it is shown
    whole on a blurred copy of itself instead of a narrow zoomed strip."""
    if not is_portrait(src_w, src_h):
        return design
    d = design.model_copy(deep=True)
    if d.layout in ("stack", "corner", "split"):
        d.layout = "full"
    d.hide_cam = False
    d.cam_rect = None
    d.game_rect = None
    d.game_roi = None
    if d.aspect != "9:16" and d.fill == "zoom":
        d.fill = "blur"
    return d


def build_filter(design: Design, src_w: int, src_h: int, fps: int = 30):
    """FFmpeg filter_complex drawing the layout; output label is [base]."""
    design = adapt_to_source(design, src_w, src_h)
    base_w, base_h = CANVAS[design.aspect]
    scale = design.resolution / 1080
    W, H = _even(base_w * scale), _even(base_h * scale)
    layout = effective_layout(design)
    parts = []
    gx, gy, gw, gh = _px(game_region(design), src_w, src_h)
    ax, ay, aw, ah = _px(game_area(design), W, H)
    background = "white" if layout == "meme" else "black"
    cam_rect = cam_output_rect(design)
    streams = 2 if cam_rect else 1
    if layout == "split":
        streams = 2
    parts.append(f"[0:v]fps={fps},setsar=1,split={streams}" + "".join(f"[s{i}]" for i in range(streams)))
    if layout == "split":
        half = _even(H / 2)
        left_w = gw // 2 // 2 * 2
        parts.append(f"[s0]crop={left_w}:{gh}:{gx}:{gy}[l0]")
        parts.append(f"[s1]crop={left_w}:{gh}:{gx + gw - left_w}:{gy}[r0]")
        parts.append(_fit("l0", "l1", W, half, "zoom", 0.5))
        parts.append(_fit("r0", "r1", W, H - half, "zoom", 0.5))
        parts.append("[l1][r1]vstack=inputs=2,setsar=1[base]")
        return ";".join(parts), W, H
    parts.append(f"[s0]crop={gw}:{gh}:{gx}:{gy}[g0]")
    game = "g0"
    cover = cam_cover(design, (gx, gy, gw, gh), src_w, src_h)
    if cover:
        rx, ry, rw, rh = cover
        sw, sh = max(2, rw // 24 // 2 * 2), max(2, rh // 24 // 2 * 2)
        parts.append(
            f"[g0]split[g0k][g0m];[g0m]crop={rw}:{rh}:{rx}:{ry},scale={sw}:{sh},"
            f"scale={rw}:{rh}:flags=bicubic,gblur=sigma=6[g0b];[g0k][g0b]overlay={rx}:{ry}[g0h]"
        )
        game = "g0h"
    parts.append(_fit(game, "g1", aw, ah, design.fill, design.focus_x))
    parts.append(f"[g1]pad={W}:{H}:{ax}:{ay}:color={background}[c0]")
    if not cam_rect:
        parts[-1] = parts[-1].replace("[c0]", "[base]")
        return ";".join(parts), W, H
    cx, cy, cw, ch = _px(design.cam_roi, src_w, src_h)
    ox, oy, ow, oh = _px(cam_rect, W, H)
    if layout == "corner":
        border = 6
        iw, ih = ow - 2 * border, oh - 2 * border
        parts.append(
            f"[s1]crop={cw}:{ch}:{cx}:{cy},scale={iw}:{ih}:force_original_aspect_ratio=increase,"
            f"crop={iw}:{ih},pad={ow}:{oh}:{border}:{border}:color=white,setsar=1[cam]"
        )
    else:
        parts.append(
            f"[s1]crop={cw}:{ch}:{cx}:{cy},scale={ow}:{oh}:force_original_aspect_ratio=increase,"
            f"crop={ow}:{oh},setsar=1[cam]"
        )
    parts.append(f"[c0][cam]overlay={ox}:{oy}[base]")
    return ";".join(parts), W, H
