"""Hook titles from the clip's words and the stream's title, without any AI key. Always editable.

Honesty rule: a title may only say what the evidence supports. Without AI we don't know what
happened, only which words were said, which game it is and which signals fired, so:
  * a real quote from the clip comes first when it's a strong one (it is literally true);
  * a topic word gives a label ("Kai Cenat vs the cops"), never an outcome
    ("did NOT escape the cops", "goes wrong", "worst ever");
  * a vibe label names the game ("Crazy GTA moment", "Pure GTA RP chaos"): hype words are
    the buyer's style choice, they don't claim a specific event. These rotate per clip so
    clips from one stream don't all get the same line, and they work in any language
    (the words don't have to be understood);
  * "chat went wild" / "most replayed" only when that signal really fired for this clip.
No emoji: they render as empty boxes on the video.
"""

from __future__ import annotations

import re
import zlib

from .styles import mask

_TOPICS = [
    (("police", "cop", "cops", "wanted", "chase", "chasing", "arrest", "sirens"), ["{s} vs the cops", "Cops vs {s} in {g}"]),
    (("rizz", "date", "kiss", "girlfriend", "boyfriend"), ["{p} rizz attempt", "{s} shoots their shot"]),
    (("heist", "bank", "rob", "robbery", "steal"), ["{p} {g} heist", "{s} on a robbery"]),
    (("crash", "crashed", "driving", "plane", "fly", "flying", "bike"), ["{s} behind the wheel in {g}", "{p} driving"]),
    (("shot", "shoot", "shooting", "gun", "fight"), ["{s} in a {g} shootout", "{s} in a fight"]),
    (("haha", "lol", "lmao", "laugh", "laughing"), ["{s} can't stop laughing", "A laugh with {s}"]),
    (("rap", "song", "sing", "singing", "beat"), ["{p} music moment", "{s} on the mic"]),
]
_SIGNALS = [
    ("chat went wild", "Chat reacted to this"),
    ("most replayed", "The most replayed part of the stream"),
    ("You pressed Clip now", "{s} live"),
    ("high audio energy", "{s} gets loud"),
]
_VIBES = [
    "Crazy {g} moment",
    "This {g} scene is wild",
    "Pure {g} chaos",
    "Peak {g} moment",
    "Wildest {g} clip today",
    "Only in {g}",
    "{g} moment of the day",
    "{s} in {g} is something else",
    "Unreal {g} moment",
    "{g} at its best",
]
_DEFAULTS = [
    "{s} on stream",
    "From {p} stream",
    "{s}: watch this",
    "A moment with {s}",
]
_GAMES = [
    (r"\bgta\s*(?:vi|6)\b", "GTA 6"),
    (r"\bgta\s*(?:rp|roleplay)\b|\bnopixel\b|\bsoulcity\b|\bsoul city\b|\bgrand rp\b|\bfivem\b", "GTA RP"),
    (r"\bgta\b|\bgrand theft auto\b", "GTA"),
    (r"\bvalorant\b", "Valorant"),
    (r"\bminecraft\b", "Minecraft"),
    (r"\bfortnite\b", "Fortnite"),
    (r"\bfree ?fire\b", "Free Fire"),
    (r"\bbgmi\b|\bpubg\b", "BGMI"),
    (r"\bcs2\b|\bcounter[- ]strike\b", "CS2"),
    (r"\bwarzone\b|\bcall of duty\b|\bcod\b", "Warzone"),
    (r"\broblox\b", "Roblox"),
    (r"\bapex\b", "Apex"),
    (r"\brocket league\b", "Rocket League"),
    (r"\bred dead\b|\brdr2?\b", "Red Dead"),
    (r"\belden ring\b", "Elden Ring"),
    (r"\bleague of legends\b|\blol esports\b", "League"),
]
_PUNCH = ("no", "wait", "bro", "what", "why", "stop", "run", "help", "oh my god", "omg", "yo")


def detect_game(context: str) -> str | None:
    """The game named in the stream's title / topic, or None."""
    low = (context or "").lower()
    for pattern, name in _GAMES:
        if re.search(pattern, low):
            return name
    return None


def _possessive(name: str) -> str:
    return name + ("'" if name.endswith("s") else "'s")


def _quote(text: str) -> tuple[str | None, int]:
    best, best_score = None, 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = sentence.strip().split()
        if not 3 <= len(words) <= 9:
            continue
        lower = sentence.lower()
        score = sentence.count("!") * 3 + sentence.count("?") * 2
        score += sum(2 for p in _PUNCH if re.search(rf"\b{re.escape(p)}\b", lower))
        if score > best_score:
            best, best_score = " ".join(mask(w) for w in words).rstrip(".,"), score
    return (f'"{best}"' if best else None), best_score


def suggest_titles(text: str, streamer: str = "", count: int = 3, avoid=(), reasons=(), context: str = "") -> list[str]:
    """Best titles first. Titles in `avoid` (already used for another clip of this video) go last,
    so every clip gets its own title. `reasons` are the picker's reasons for this clip; only those
    may be claimed. `context` = the stream's title / topic (finds the game)."""
    name = (streamer or "").strip().lstrip("@") or "The streamer"
    game = detect_game(context)
    fill = {"s": name, "p": _possessive(name), "g": game or "stream"}
    lower = (text or "").lower()
    quote, strength = _quote(text or "")
    found = []
    if quote and strength >= 3:
        found.append(quote)
    for keys, lines in _TOPICS:
        if any(re.search(rf"\b{re.escape(k)}\b", lower) for k in keys):
            found.extend(line.format(**fill) for line in lines if game or "{g}" not in line)
    given = {str(r).lower() for r in reasons or ()}
    for reason, line in _SIGNALS:
        if reason.lower() in given:
            found.append(line.format(**fill))
    # Vibe labels rotate with the clip's words, so clips of one stream start on different lines.
    vibes = [v for v in _VIBES if game or v not in ("Only in {g}", "{s} in {g} is something else", "{g} at its best")]
    turn = zlib.crc32((text or name).encode("utf-8")) % len(vibes)
    found.extend(v.format(**fill).replace("stream moment of the day", "Moment of the day") for v in vibes[turn:] + vibes[:turn])
    if quote and strength < 3:
        found.append(quote)
    found.extend(line.format(**fill) for line in _DEFAULTS)
    used = set(avoid or ())
    n = 2
    while len([t for t in found if t not in used]) < count:  # always enough fresh titles
        found.append(f"{name}, part {n}")
        n += 1
    unique = []
    for line in found:
        if line[:90] not in unique:
            unique.append(line[:90])
    return ([t for t in unique if t not in used] + [t for t in unique if t in used])[:count]
