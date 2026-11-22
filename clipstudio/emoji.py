"""Emoji over captions (the "Emoji" switch).

libass can't draw colour emoji, so each emoji is a small PNG (Twemoji, CC-BY 4.0, in emoji/)
laid over the video by FFmpeg while its caption is on screen. The browser preview uses the
same PNGs, so what you see is what you get. Make/refresh the PNGs with tools/make_emoji.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from .paths import RESOURCES

EMOJI_DIR = Path(RESOURCES) / "emoji"

# codepoint file name -> (emoji, words that call it). First match in a caption wins.
EMOJI = {
    "1f602": ("😂", "haha hahaha lol lmao funny laugh laughing hilarious joke joking"),
    "1f525": ("🔥", "fire insane crazy lit hot goat clutch sick wild"),
    "1f480": ("💀", "dead died die dying rip killed kill"),
    "1f631": ("😱", "scared scary omg terrifying help run"),
    "1f633": ("😳", "what wtf huh whoa"),
    "1f4b0": ("💰", "money cash rich bank paid dollars bucks million millions"),
    "1f693": ("🚓", "police cop cops sirens arrest arrested wanted chase"),
    "1f697": ("🚗", "car cars drive driving crash crashed speed race"),
    "2708": ("✈️", "plane fly flying airport pilot"),
    "1f4a5": ("💥", "boom explode exploded explosion bomb blew"),
    "2764": ("❤️", "love heart cute babe kiss crush"),
    "1f62d": ("😭", "cry crying sad nooo noo"),
    "1f3c6": ("🏆", "win won winner victory champion"),
    "1f451": ("👑", "king queen boss"),
    "1f92f": ("🤯", "unbelievable impossible mindblowing"),
    "1f914": ("🤔", "think thinking hmm maybe idea"),
    "1f440": ("👀", "look looking watch sus"),
    "1f389": ("🎉", "party celebrate birthday yay"),
    "1f624": ("😤", "angry mad rage tilted"),
    "1f64f": ("🙏", "please pray thank thanks"),
    "1f4af": ("💯", "facts real true exactly hundred"),
    "26a1": ("⚡", "fast quick quickly"),
    "1f3ae": ("🎮", "game gaming gamer controller"),
    "1f355": ("🍕", "food pizza eat eating hungry"),
    "1f921": ("🤡", "clown stupid dumb idiot"),
    "1f60e": ("😎", "cool smooth chill easy"),
    "1f976": ("🥶", "cold freeze frozen ice"),
    "1f9e0": ("🧠", "smart brain genius"),
}
_WORDS = {word: code for code, (_, words) in EMOJI.items() for word in words.split()}


def emoji_for(text: str) -> str | None:
    """Codepoint name of the first word in `text` that has an emoji, or None."""
    for raw in str(text).lower().split():
        word = re.sub(r"[^a-z]", "", raw)
        code = _WORDS.get(word)
        if code and (EMOJI_DIR / f"{code}.png").is_file():
            return code
    return None


def emoji_table() -> dict:
    """For the browser preview: word -> codepoint, and which PNGs exist."""
    have = {p.stem for p in EMOJI_DIR.glob("*.png")} if EMOJI_DIR.is_dir() else set()
    return {"words": {w: c for w, c in _WORDS.items() if c in have}, "files": sorted(have)}
