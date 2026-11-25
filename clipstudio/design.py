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


_ASSET = re.compile(r"^[0-9a-f]{16}\.(png|jpg|jpeg|webp|mp4|mov|webm|mkv|mp3|wav|m4a|aac|ogg)$")


def _asset(value):
    """An uploaded file in <data>/brand (see overlays.save_asset); blank = none."""
    value = str(value or "").strip().lower()
    if value and not _ASSET.fullmatch(value):
        raise ValueError("That file isn't one of your uploaded files.")
    return value


CARD_KINDS = ("none", "x_post", "ig_comment", "ig_question", "ig_poll", "yt_comment", "tiktok_hook", "news")


class PollOption(BaseModel):
    text: str = Field(default="", max_length=40)
    pct: int = Field(default=50, ge=0, le=100)


class CardStyle(BaseModel):
    """A social post card drawn over the video (Cards tab). Empty text = the clip's title."""
    kind: Literal["none", "x_post", "ig_comment", "ig_question", "ig_poll", "yt_comment", "tiktok_hook", "news"] = "none"
    theme: Literal["light", "dark"] = "light"
    name: str = Field(default="", max_length=40)      # blank = the channel name
    handle: str = Field(default="", max_length=40)    # blank = the channel name
    verified: bool = True
    avatar: str = ""                                   # uploaded picture; blank = first letter in a circle
    text: str = Field(default="", max_length=280)
    options: list[PollOption] = Field(
        default_factory=lambda: [PollOption(text="Yes", pct=64), PollOption(text="No", pct=36)],
        min_length=2, max_length=4)
    ticker: str = Field(default="", max_length=200)
    size: float = Field(default=0.86, ge=0.35, le=1.0)  # card width as a share of the frame's short side
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    seconds: float = Field(default=0.0, ge=0, le=600)   # 0 = whole clip

    _asset = field_validator("avatar")(_asset)


class Handle(BaseModel):
    platform: Literal["youtube", "twitch", "kick", "instagram", "tiktok", "x"] = "youtube"
    name: str = Field(default="", max_length=40)


class BrandStyle(BaseModel):
    """My brand tab: social handles bar, logo, background card. All off by default."""
    handles: list[Handle] = Field(default_factory=list, max_length=5)
    handles_x: float = Field(default=0.5, ge=0, le=1)
    handles_y: float = Field(default=0.9, ge=0, le=1)
    handles_size: int = Field(default=34, ge=16, le=90)
    logo: str = ""
    logo_corner: Literal["tl", "tr", "bl", "br"] = "tr"
    logo_size: float = Field(default=0.16, ge=0.05, le=0.5)
    logo_opacity: float = Field(default=0.85, ge=0.1, le=1)
    frame: Literal["none", "blur", "color", "gradient", "image"] = "none"
    frame_color: str = "#101418"
    frame_color2: str = "#6A3DF0"
    frame_image: str = ""
    frame_pad: float = Field(default=0.06, ge=0.02, le=0.2)
    frame_radius: int = Field(default=36, ge=0, le=90)

    _colors = field_validator("frame_color", "frame_color2")(_hex)
    _assets = field_validator("logo", "frame_image")(_asset)


class OutroStyle(BaseModel):
    """A few seconds added after the clip: a drawn end card or the buyer's own video."""
    mode: Literal["off", "card", "video"] = "off"
    text: str = Field(default="Follow for more", max_length=60)
    sub: str = Field(default="", max_length=60)  # blank = @channel
    bg: str = "#111111"
    color: str = "#FFFFFF"
    accent: str = "#FFD233"
    font: str = "Anton"
    picture: str = ""
    seconds: float = Field(default=3.0, ge=2, le=4)
    video: str = ""

    _colors = field_validator("bg", "color", "accent")(_hex)
    _assets = field_validator("picture", "video")(_asset)

    @field_validator("font")
    @classmethod
    def known_font(cls, value):
        return value if value in FONTS else "Anton"


class MusicStyle(BaseModel):
    """One song under the clip, quieter while people talk, rising for the last seconds."""
    file: str = ""
    volume: float = Field(default=0.18, ge=0, le=1)
    peak: float = Field(default=0.6, ge=0, le=1)
    rise: float = Field(default=3.0, ge=0, le=10)  # 0 = no rise
    duck: bool = True

    _asset = field_validator("file")(_asset)


class Design(BaseModel):
    template: str = Field(default="big_captions", max_length=40)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    resolution: Literal[720, 1080, 2160] = 1080
    # full = one picture, stack = cam above/below the game, corner = cam floats over the game,
    # split = left/right halves of the stream on top of each other, meme = white top bar,
    # side = game and cam next to each other on a blurred copy, trio = three areas stacked.
    layout: Literal["full", "stack", "corner", "split", "meme", "side", "trio"] = "full"
    fill: Literal["blur", "bars", "zoom"] = "zoom"
    cam_roi: list[float] | None = None  # streamer cam inside the source
    cam_rect: list[float] | None = None  # where the cam goes in the output (any cam layout)
    cam_side: Literal["top", "bottom"] = "top"
    # How a floating cam looks: white frame (as before), rounded corners, or a circle.
    cam_shape: Literal["frame", "round", "circle"] = "frame"
    # Third area (Trio): a part of the stream (minimap, chat, a 2nd streamer) and where it goes.
    extra_roi: list[float] | None = None
    extra_rect: list[float] | None = None
    # Space the boxes don't cover shows a blurred copy of the stream instead of black.
    bg_blur: bool = False
    # A template that keeps its own boxes in the stream ("same spots every stream").
    keep_boxes: bool = False
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
    credit_style: Literal["at", "clip"] = "at"  # "@name" or "Clip: name"
    card: CardStyle = Field(default_factory=CardStyle)
    brand: BrandStyle = Field(default_factory=BrandStyle)
    outro: OutroStyle = Field(default_factory=OutroStyle)
    music: MusicStyle = Field(default_factory=MusicStyle)
    # When someone shouts: the shouted word big in yellow, a quick shake, and (sfx) a boom sound.
    shout: bool = False
    sfx: bool = True

    _rects = field_validator("cam_roi", "cam_rect", "game_roi", "game_rect", "extra_roi", "extra_rect")(_rect)


# Everything "Set it manually" changes. "Use for all clips of this video" copies these keys.
FRAMING_KEYS = (
    "layout", "fill", "cam_side", "split", "focus_x", "hide_cam", "cam_shape", "bg_blur", "keep_boxes",
    "cam_roi", "cam_rect", "game_roi", "game_rect", "extra_roi", "extra_rect",
)
# Boxes in the source video: they belong to one video unless the template keeps them.
SOURCE_BOXES = ("cam_roi", "game_roi", "extra_roi")


def corner_rect(aspect: str, corner: str = "tl", size: float = 0.42, square: bool = False):
    """Cam box in one corner of the output. `size` = share of the frame width (9:16) or height (16:9).
    `square` makes it square on screen (for the circle cam)."""
    W, H = CANVAS.get(aspect, CANVAS["9:16"])
    margin = 0.04
    if aspect == "16:9":
        h = size
        w = h * H / W * (1 if square else 16 / 9)
    else:
        w = size
        h = w * W / H * (1 if square else 9 / 16)
    w, h = min(w, 1 - 2 * margin), min(h, 1 - 2 * margin)
    mx = margin * (H / W if aspect == "16:9" else 1)
    my = margin * (W / H if aspect != "16:9" else 1)
    x = mx if corner in ("tl", "bl") else 1 - mx - w
    y = my if corner in ("tl", "tr") else 1 - my - h
    if aspect == "9:16" and corner in ("tl", "tr"):
        y = 0.16  # under the hook title
    elif aspect == "9:16":
        y = 1 - 0.05 - h  # above the @name
    return [round(v, 4) for v in (x, y, w, h)]


# The layout chips of "Set it manually". Each one is a patch on the design; `long` is used for 16:9.
LAYOUTS = [
    {"id": "full", "name": "Full screen", "hint": "The gameplay fills the whole screen.",
     "short": {"layout": "full", "fill": "zoom", "hide_cam": False, "bg_blur": False}},
    {"id": "game_only", "name": "Gameplay only", "hint": "Full screen, with the streamer's cam blurred out.",
     "short": {"layout": "full", "fill": "zoom", "hide_cam": True, "bg_blur": False}},
    {"id": "centred", "name": "Centred", "hint": "The whole stream in the middle on a blurred copy of itself.",
     "short": {"layout": "full", "fill": "blur", "hide_cam": False, "bg_blur": False}},
    {"id": "cam_top", "name": "Cam on top", "hint": "The streamer on top, the game below. Slide to change the split.",
     "needs_cam": True,
     "short": {"layout": "stack", "cam_side": "top", "split": 0.4, "fill": "zoom", "bg_blur": False},
     "long": {"layout": "corner", "cam_shape": "frame", "cam_rect": corner_rect("16:9", "tr", 0.3)}},
    {"id": "cam_bottom", "name": "Cam below", "hint": "The game on top, the streamer below.",
     "needs_cam": True,
     "short": {"layout": "stack", "cam_side": "bottom", "split": 0.4, "fill": "zoom", "bg_blur": False},
     "long": {"layout": "corner", "cam_shape": "frame", "cam_rect": corner_rect("16:9", "br", 0.3)}},
    {"id": "pip", "name": "Cam in a corner", "hint": "The cam floats over the game. Pick a corner and a size.",
     "needs_cam": True,
     "short": {"layout": "corner", "cam_shape": "round", "fill": "zoom", "bg_blur": False,
               "cam_rect": corner_rect("9:16", "tl", 0.42)},
     "long": {"layout": "corner", "cam_shape": "round", "cam_rect": corner_rect("16:9", "tr", 0.3)}},
    {"id": "circle", "name": "Round cam", "hint": "The streamer's face in a circle over the game.",
     "needs_cam": True,
     "short": {"layout": "corner", "cam_shape": "circle", "fill": "zoom", "bg_blur": False,
               "cam_rect": corner_rect("9:16", "tl", 0.36, square=True)},
     "long": {"layout": "corner", "cam_shape": "circle", "cam_rect": corner_rect("16:9", "tr", 0.36, square=True)}},
    {"id": "side", "name": "Side by side", "hint": "Game and cam next to each other, blurred stream around them.",
     "needs_cam": True,
     "short": {"layout": "side", "bg_blur": True, "fill": "zoom", "game_rect": [0.0, 0.34, 0.62, 0.32],
               "cam_rect": [0.62, 0.34, 0.38, 0.32]},
     "long": {"layout": "side", "bg_blur": True, "fill": "zoom", "game_rect": [0.0, 0.0, 0.68, 1.0],
              "cam_rect": [0.68, 0.25, 0.32, 0.5]}},
    {"id": "trio", "name": "Trio", "hint": "Three parts stacked: cam, game, and one more part you pick (map, chat...).",
     "needs_cam": True,
     "short": {"layout": "trio", "fill": "zoom", "bg_blur": False, "cam_rect": [0.0, 0.0, 1.0, 0.3],
               "game_rect": [0.0, 0.3, 1.0, 0.42], "extra_rect": [0.0, 0.72, 1.0, 0.28]},
     "long": {"layout": "side", "bg_blur": True, "fill": "zoom", "game_rect": [0.0, 0.0, 0.68, 1.0],
              "cam_rect": [0.68, 0.0, 0.32, 0.5], "extra_rect": [0.68, 0.5, 0.32, 0.5]}},
    {"id": "halves", "name": "Two halves", "hint": "Left and right half of the stream, one on top of the other (2 people).",
     "short": {"layout": "split", "fill": "zoom", "bg_blur": False}},
    {"id": "topbar", "name": "Top bar", "hint": "A white bar on top for the hook line, the video below.",
     "short": {"layout": "meme", "fill": "bars", "meme_bar": 0.24, "bg_blur": False}},
]
_RESET = {"cam_rect": None, "game_rect": None, "extra_rect": None, "hide_cam": False, "cam_shape": "frame"}


def layout_patch(layout_id: str, aspect: str = "9:16") -> dict:
    """The design changes a layout chip makes (output boxes reset to that layout's own spots)."""
    item = next((x for x in LAYOUTS if x["id"] == layout_id), LAYOUTS[0])
    patch = dict(item["long"] if aspect != "9:16" and "long" in item else item["short"])
    if aspect != "9:16" and patch.get("layout") == "trio":
        patch["layout"] = "side"
    return {**_RESET, **patch}


def apply_layout(design: "Design", layout_id: str) -> "Design":
    return design.model_copy(update=layout_patch(layout_id, design.aspect), deep=True)


def layout_id(design: "Design") -> str:
    """Which chip a design matches (for the "on" chip in the editor)."""
    layout = design.layout
    if layout == "full":
        return "game_only" if design.hide_cam else "centred" if design.fill == "blur" else "full"
    if layout == "stack":
        return "cam_top" if design.cam_side == "top" else "cam_bottom"
    if layout == "corner":
        return "circle" if design.cam_shape == "circle" else "pip"
    return {"side": "trio" if design.extra_rect else "side", "trio": "trio", "split": "halves", "meme": "topbar"}[layout]


def framing_of(design: "Design") -> dict:
    data = design.model_dump()
    return {k: data[k] for k in FRAMING_KEYS} | {"meme_bar": data["meme_bar"]}


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
    if layout in CAM_LAYOUTS and not design.cam_roi:
        return "full"  # no cam found or drawn yet
    if design.aspect != "9:16" and layout in ("stack", "split", "trio"):
        return {"stack": "corner", "split": "full", "trio": "side"}[layout]
    return layout


CAM_LAYOUTS = ("stack", "corner", "side", "trio")
_SIDE = {"9:16": ([0.0, 0.34, 0.62, 0.32], [0.62, 0.34, 0.38, 0.32]),
         "16:9": ([0.0, 0.0, 0.68, 1.0], [0.68, 0.25, 0.32, 0.5]),
         "1:1": ([0.0, 0.2, 0.62, 0.6], [0.62, 0.2, 0.38, 0.6])}
_TRIO = ([0.0, 0.0, 1.0, 0.3], [0.0, 0.3, 1.0, 0.42], [0.0, 0.72, 1.0, 0.28])  # cam, game, extra


def cam_output_rect(design: Design):
    layout = effective_layout(design)
    if layout == "stack":
        if design.cam_rect:
            return design.cam_rect
        band = design.split
        return [0.0, 0.0, 1.0, band] if design.cam_side == "top" else [0.0, 1 - band, 1.0, band]
    if layout == "corner":
        return design.cam_rect or [0.05, 0.05, 0.4, 0.2]
    if layout == "side":
        return design.cam_rect or _SIDE[design.aspect][1]
    if layout == "trio":
        return design.cam_rect or _TRIO[0]
    return None


def game_area(design: Design):
    layout = effective_layout(design)
    if layout == "full" and design.layout in CAM_LAYOUTS:
        return [0.0, 0.0, 1.0, 1.0]  # no streamer cam found: the gameplay fills the whole frame
    if design.game_rect and layout != "split":
        return design.game_rect
    if layout == "stack":
        band = design.split
        return [0.0, band, 1.0, 1 - band] if design.cam_side == "top" else [0.0, 0.0, 1.0, 1 - band]
    if layout == "meme":
        return [0.0, design.meme_bar, 1.0, 1 - design.meme_bar]
    if layout == "side":
        return _SIDE[design.aspect][0]
    if layout == "trio":
        return _TRIO[1]
    return [0.0, 0.0, 1.0, 1.0]


def extra_area(design: Design):
    """Where the third area goes (Trio), or None."""
    layout = effective_layout(design)
    if layout == "trio":
        return design.extra_rect or _TRIO[2]
    if layout == "side" and design.extra_rect:
        return design.extra_rect
    return None


def extra_region(design: Design):
    """The part of the stream shown in the third area: picked by the buyer, else a closer look at the middle."""
    return design.extra_roi or [0.3, 0.3, 0.4, 0.4]


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
    if d.layout in (*CAM_LAYOUTS, "split"):
        d.layout = "full"
    d.hide_cam = False
    d.bg_blur = False
    d.cam_rect = None
    d.game_rect = None
    d.game_roi = None
    d.extra_rect = None
    d.extra_roi = None
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
    extra_rect = extra_area(design)
    covered = ax == 0 and ay == 0 and aw >= W and ah >= H
    bg_blur = design.bg_blur and layout != "meme" and not covered
    streams = 1 + bool(cam_rect) + bool(extra_rect) + bg_blur
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
    return _compose(design, parts, (gx, gy, gw, gh), (ax, ay, aw, ah), W, H, src_w, src_h,
                    background, cam_rect, extra_rect, bg_blur)


def _mask(label, w, h, shape, inset=0):
    """A one-frame grey mask (white = keep) for a rounded box or a circle. Overlays and alphamerge
    repeat the last frame, so it is drawn once, not on every frame."""
    if shape == "circle":
        r = min(w, h) / 2 - inset
        expr = f"255*lte(hypot(X-{w / 2 - 0.5:.1f},Y-{h / 2 - 0.5:.1f}),{r:.1f})"
    else:
        r = max(4, round(min(w, h) * 0.09))
        expr = (f"255*lte(hypot(max(0,max({r}-X,X-{w - 1 - r})),max(0,max({r}-Y,Y-{h - 1 - r}))),{r})")
    return f"color=c=black:s={w}x{h}:r=1:d=1,format=gray,geq=lum='{expr}'[{label}]"


def _shaped(parts, src, label, w, h, shape):
    """Cut a rounded box or a circle out of [src] (w x h) as [label] with see-through corners."""
    parts.append(_mask(label + "m", w, h, shape))
    parts.append(f"[{src}]format=yuva420p[{label}a];[{label}a][{label}m]alphamerge[{label}]")


def _compose(design, parts, game_px, area_px, W, H, src_w, src_h, background, cam_rect, extra_rect, bg_blur):
    """Game (+ blurred background) + cam (+ third area) for every layout except "split"."""
    gx, gy, gw, gh = game_px
    ax, ay, aw, ah = area_px
    stream = iter(f"s{i}" for i in range(1, 4))
    parts.append(f"[s0]crop={gw}:{gh}:{gx}:{gy}[g0]")
    game = "g0"
    cover = cam_cover(design, game_px, src_w, src_h)
    if cover:
        rx, ry, rw, rh = cover
        sw, sh = max(2, rw // 24 // 2 * 2), max(2, rh // 24 // 2 * 2)
        parts.append(
            f"[g0]split[g0k][g0m];[g0m]crop={rw}:{rh}:{rx}:{ry},scale={sw}:{sh},"
            f"scale={rw}:{rh}:flags=bicubic,gblur=sigma=6[g0b];[g0k][g0b]overlay={rx}:{ry}[g0h]"
        )
        game = "g0h"
    parts.append(_fit(game, "g1", aw, ah, design.fill, design.focus_x))
    if bg_blur:
        bw, bh = _even(W / 8), _even(H / 8)
        parts.append(
            f"[{next(stream)}]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=4:2,eq=brightness=-0.08,scale={W}:{H},setsar=1[bg0]"
        )
        parts.append(f"[bg0][g1]overlay={ax}:{ay}[c0]")
    else:
        parts.append(f"[g1]pad={W}:{H}:{ax}:{ay}:color={background}[c0]")
    last = "c0"
    if cam_rect:
        cam_src = next(stream)
        cx, cy, cw, ch = _px(design.cam_roi, src_w, src_h)
        ox, oy, ow, oh = _px(cam_rect, W, H)
        floating = effective_layout(design) == "corner"
        shape = design.cam_shape if floating else "box"
        if shape == "frame":
            border = 6
            iw, ih = ow - 2 * border, oh - 2 * border
            parts.append(
                f"[{cam_src}]crop={cw}:{ch}:{cx}:{cy},scale={iw}:{ih}:force_original_aspect_ratio=increase,"
                f"crop={iw}:{ih},pad={ow}:{oh}:{border}:{border}:color=white,setsar=1[cam]"
            )
        elif shape == "circle":
            d = min(ow, oh) // 2 * 2
            ring = max(4, _even(d * 0.025))
            inner = d - 2 * ring
            ox, oy = ox + (ow - d) // 2 // 2 * 2, oy + (oh - d) // 2 // 2 * 2
            parts.append(
                f"[{cam_src}]crop={cw}:{ch}:{cx}:{cy},scale={inner}:{inner}:force_original_aspect_ratio=increase,"
                f"crop={inner}:{inner},setsar=1[camr]"
            )
            _shaped(parts, "camr", "cam", inner, inner, "circle")
            parts.append(f"color=c=white:s={d}x{d}:r=1:d=1,format=yuva420p[ringw]")
            parts.append(_mask("ringm", d, d, "circle"))
            parts.append("[ringw][ringm]alphamerge[ring]")
            parts.append(f"[{last}][ring]overlay={ox}:{oy}[c0r]")
            last, ox, oy = "c0r", ox + ring, oy + ring
        else:
            parts.append(
                f"[{cam_src}]crop={cw}:{ch}:{cx}:{cy},scale={ow}:{oh}:force_original_aspect_ratio=increase,"
                f"crop={ow}:{oh},setsar=1[camr]"
            )
            if shape == "round":
                _shaped(parts, "camr", "cam", ow, oh, "round")
            else:
                parts[-1] = parts[-1].replace("[camr]", "[cam]")
        parts.append(f"[{last}][cam]overlay={ox}:{oy}[c1]")
        last = "c1"
    if extra_rect:
        ex, ey, ew, eh = _px(extra_region(design), src_w, src_h)
        px, py, pw, ph = _px(extra_rect, W, H)
        parts.append(
            f"[{next(stream)}]crop={ew}:{eh}:{ex}:{ey},scale={pw}:{ph}:force_original_aspect_ratio=increase,"
            f"crop={pw}:{ph},setsar=1[xtra]"
        )
        parts.append(f"[{last}][xtra]overlay={px}:{py}[c2]")
        last = "c2"
    parts[-1] = parts[-1][: -len(f"[{last}]")] + "[base]"
    return ";".join(parts), W, H
