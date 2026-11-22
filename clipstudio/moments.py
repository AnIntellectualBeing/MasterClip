"""Rule-based moment picker for GTA streams. No AI and no network.

Every second of the stream gets a score from:
  - GTA events said out loud (chase, crash, stunt, fight, NPC chaos, roleplay scene, mission win),
  - funny back-and-forth (quick turns, questions, laughter),
  - a loudness jump (the streamer suddenly louder than a moment ago), loudness and peaks,
  - on-screen change (scene score),
  - YouTube's "most replayed" curve when the link had one.
Lines that thank donors/subs, retell "earlier today" stories, or talk about plans and what-ifs
earn no event credit and pull their seconds down.

Then the best peaks become clips with about 8 s of setup and 10 s of reaction, and both cuts are
moved to a gap between sentences, so a clip never starts or ends mid-sentence.

Several event patterns and the plan/what-if/story framing idea are adapted from the
"Grand Theft Auto 6 Clips Kitty Plugin" by ColinGPT9 (MIT licence, see NOTICE).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

LABELS = {
    "chase": "Chase",
    "crash": "Crash",
    "stunt": "Stunt",
    "fight": "Fight",
    "npc_chaos": "NPC chaos",
    "roleplay": "Roleplay scene",
    "mission": "Mission win",
    "funny": "Funny back-and-forth",
    "reaction": "Big reaction",
}

# (category, strength, pattern). Strength is how specific the phrase is, not a probability.
# Word boundaries everywhere: "crash" inside a username or "fire" in "campfire" never counts.
_RULES = (
    # chase
    ("chase", 0.88, r"(?:cops|police|they|feds|pd) (?:are |were |keep |kept )?(?:chasing|following|after|behind) (?:me|us)"),
    ("chase", 0.82, r"(?:police|cop|high speed|car) chase"),
    ("chase", 0.86, r"(?:escaped|lost|outran|evaded|shook) (?:the )?(?:cops|police|feds|them)"),
    ("chase", 0.8, r"(?:go go go|drive drive|floor it|step on it|they're on us|pit (?:me|him|them)|got pitted)"),
    ("chase", 0.78, r"(?:wanted level|five stars|5 stars|lose the cops)"),
    # crash
    ("crash", 0.9, r"(?:i|we|he|she|they|you) (?:(?:just|actually|really|totally|literally) )?crashed"),
    ("crash", 0.88, r"crashed (?:right |straight )?into\b"),
    ("crash", 0.84, r"(?:car|truck|bike|vehicle|plane|heli|helicopter) (?:(?:just|completely) )?(?:flipped|rolled|exploded|blew up)"),
    ("crash", 0.84, r"(?:spun out|spinning out|lost control|head on|pile ?up|wrecked (?:it|the car))"),
    # stunt
    ("stunt", 0.91, r"(?:landed|nailed|stuck) (?:that|the|a) (?:jump|stunt|flip|backflip|landing)|nailed it|clutched (?:it|that|up)"),
    ("stunt", 0.86, r"(?:pulled off|did) (?:(?:that|the|a) )?(?:stunt|jump|backflip|frontflip|barrel roll)"),
    ("stunt", 0.85, r"(?:missed|failed) (?:(?:that|the|a) )?(?:landing|jump|stunt|backflip)"),
    ("stunt", 0.8, r"(?:sent it|full send|what a jump|that jump)"),
    # fight
    ("fight", 0.87, r"(?:shootout|firefight|gunfight|shots fired|shots shots)"),
    ("fight", 0.86, r"(?:i|we|he|she|they|you) (?:(?:just|actually) )?(?:got shot|got ambushed|got jumped|got stabbed|got knocked)"),
    ("fight", 0.84, r"(?:he|she|they|someone) (?:is |are |just )?(?:shooting|stabbing|punching|swinging) (?:at )?(?:me|us)"),
    ("fight", 0.82, r"(?:he's down|she's down|got him|got her|dropped him|headshot|one tap)"),
    ("fight", 0.8, r"(?:blew up|shot down) (?:the|that|a) (?:car|truck|helicopter|heli|chopper|plane|tank)"),
    # NPC chaos
    ("npc_chaos", 0.89, r"(?:npc|local|pedestrian|ped) (?:(?:just|actually|suddenly) )?(?:stole|attacked|punched|rammed|carjacked|hit|shot|ran)"),
    ("npc_chaos", 0.84, r"(?:npc|local|pedestrian|ped) (?:just )?(?:went flying|ran into|walked into|started fighting|ran me over)"),
    ("npc_chaos", 0.8, r"(?:this npc|these npcs|stupid npc|the npcs are)"),
    # roleplay scenes
    ("roleplay", 0.84, r"(?:you're under arrest|you are under arrest|right to remain silent|hands up|put your hands|drop the (?:gun|weapon)|get on the ground|get out of the (?:car|vehicle))"),
    ("roleplay", 0.8, r"(?:traffic stop|pull over|license and registration|step out of the vehicle|search (?:you|the car)|cuff (?:him|her|them)|in cuffs)"),
    ("roleplay", 0.78, r"(?:hostage|robbery|rob (?:the|this|a) (?:store|bank)|hands where i can see|nobody move|this is a robbery)"),
    ("roleplay", 0.76, r"(?:i didn't do it|it wasn't me|who did this|who killed|found the body|you're lying|he's lying|she's lying|hear me out)"),
    # mission wins
    ("mission", 0.88, r"(?:mission|heist|job) (?:passed|complete|completed|done)"),
    ("mission", 0.86, r"(?:we did it|we got away|we made it|we won|gg)"),
    ("mission", 0.84, r"(?:passed|completed|finished) (?:the|this|that|a) (?:mission|heist|job)"),
    # reactions
    ("reaction", 0.8, r"(?:no way|oh my god|oh my gosh|what just happened|are you kidding me|are you serious|bro what|what the)"),
    ("reaction", 0.78, r"(?:i can't believe|that was (?:so |actually |fucking )?(?:crazy|insane)|holy (?:shit|crap)|yo+ what)"),
    ("funny", 0.86, r"(?:i can't stop laughing|i'm dying|i'm crying|that's hilarious|that was hilarious|so funny|lmao|ha ?ha ?ha)"),
    # Hindi / Hinglish streams
    ("chase", 0.82, r"(?:police aa gayi|police peeche|bhago bhago|bhaag|police se bach)"),
    ("crash", 0.82, r"(?:gaadi thok|thok di|accident ho gaya|gaadi palat)"),
    ("fight", 0.82, r"(?:maar diya|goli maar|mar gaya|maar do)"),
    ("mission", 0.82, r"(?:mission (?:pass|complete) ho gaya|jeet gaye|bach gaye)"),
    ("reaction", 0.76, r"(?:arey? (?:bhai|yaar)|kya baat|bhai kya|ye kya)"),
)
_COMPILED = tuple((cat, s, re.compile(r"\b(?:" + p + r")\b")) for cat, s, p in _RULES)

# Lines that should not drive a clip (multiplier on their seconds, why).
_THANKS = re.compile(
    r"\b(?:thank(?:s| you) (?:so much )?(?:for|to) (?:the |that )?(?:\d+ |a |an )?(?:sub|subs|gifted|gift|bits|donation|dono|follow|raid|prime|tier|superchat|resub|months?|cheer|tip)"
    r"|(?:appreciate|thanks for) (?:the )?(?:sub|subs|gifted|bits|donation|dono|follow|raid|prime|superchat|support)"
    r"|(?:gifted|gifting) (?:\d+ )?subs?|welcome (?:in|to the stream)|(?:\d+|one|two|three|five|ten) (?:gifted|bits)\b|for the raid|shukriya)\b"
)
_STORY = re.compile(
    r"\b(?:earlier today|earlier this|yesterday|last (?:night|week|stream|time|month|year)|the other day|"
    r"remember when|(?:days?|weeks?|months?|years?) ago|this one time|back in the day|in my dream)\b"
)
_PLAN = re.compile(
    r"\b(?:the plan is|my plan|our plan|we're gonna|we are going to|we should|"
    r"tomorrow|next stream|next time|later today|after this|first we|then we'll|we'll go)\b"
)
# Only real hypotheticals: a bare "if you" is everyday talk ("one if you did, two if you didn't").
_WHATIF = re.compile(r"\b(?:what if|imagine if|imagine we|imagine you|if only|if we could|would be (?:so )?(?:funny|cool|crazy) if|hypothetically|in theory)\b")
_NEGATED = re.compile(r"\b(?:no|not|never|didn't|don't|doesn't|wasn't|isn't|won't|can't)\b")
_LETS_GO = re.compile(r"(?:^|[.!?] )(?:yes+ |yeah+ |ayy+ )?let'?s go+[!. ]*$")
_LAUGH = re.compile(r"\b(?:ha(?:ha)+|lol|lmao|lmfao|hehe|laugh(?:ing)?)\b")
_NORMAL = str.maketrans({"’": "'", "‘": "'", "–": " ", "—": " ", "-": " "})

SKIP_WEIGHT = {"thank-you": 0.25, "story": 0.55, "plan": 0.8, "what-if": 0.8}


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    events: list = field(default_factory=list)  # [(category, strength, phrase)]
    skip: str = ""  # "", "thank-you", "story", "plan", "what-if"


def classify(text: str) -> tuple[list[tuple[str, float, str]], str]:
    """Events said in one line, and why the line should be skipped ("" when it shouldn't)."""
    low = " ".join(str(text or "").lower().translate(_NORMAL).split())
    if not low:
        return [], ""
    if _THANKS.search(low):
        return [], "thank-you"
    skip = "story" if _STORY.search(low) else "plan" if _PLAN.search(low) else "what-if" if _WHATIF.search(low) else ""
    if skip:
        return [], skip
    best: dict[str, tuple[str, float, str]] = {}
    if _LETS_GO.search(low):
        best["mission"] = ("mission", 0.82, "let's go")  # "LET'S GO!" as a cheer, not "let's go to..."
    for cat, strength, pattern in _COMPILED:
        for m in pattern.finditer(low):
            before = " ".join(low[: m.start()].split()[-4:])
            if cat != "reaction" and cat != "funny" and _NEGATED.search(before):
                continue  # "we didn't crash"
            if cat not in best or strength > best[cat][1]:
                best[cat] = (cat, strength, m.group(0))
    return sorted(best.values(), key=lambda e: -e[1]), ""


def sentences(transcript) -> list[Sentence]:
    """Split speech into sentences from word times: at . ! ? or a pause of 0.7 s or more.
    Overlong runs (no punctuation, no pause) are split at their biggest gap."""
    out: list[Sentence] = []
    for seg in transcript or []:
        if not isinstance(seg, dict) or not str(seg.get("text", "")).strip():
            continue
        words = [w for w in seg.get("words") or [] if str(w.get("text", "")).strip()]
        if not words:
            out.append(Sentence(float(seg["start"]), float(seg["end"]), str(seg["text"]).strip()))
            continue
        run: list[dict] = []
        for i, w in enumerate(words):
            run.append(w)
            nxt = words[i + 1] if i + 1 < len(words) else None
            ends = str(w["text"]).strip()[-1:] in ".!?"
            pause = nxt is not None and float(nxt["start"]) - float(w["end"]) >= 0.7
            if nxt is None or ends or pause:
                out.extend(_split_long(run))
                run = []
    out.sort(key=lambda s: s.start)
    for s in out:
        s.events, s.skip = classify(s.text)
    return out


def _split_long(words, limit=14.0) -> list[Sentence]:
    start, end = float(words[0]["start"]), float(words[-1]["end"])
    if end - start <= limit or len(words) < 4:
        return [Sentence(start, end, " ".join(str(w["text"]).strip() for w in words))]
    gaps = [(float(words[i + 1]["start"]) - float(words[i]["end"]), i) for i in range(1, len(words) - 2)]
    _, cut = max(gaps)
    return _split_long(words[: cut + 1], limit) + _split_long(words[cut + 1:], limit)


def _norm(a: np.ndarray, lo_pct=10, hi_pct=95) -> np.ndarray:
    if a.size == 0:
        return a
    lo, hi = float(np.percentile(a, lo_pct)), float(np.percentile(a, hi_pct))
    return np.zeros_like(a) if hi <= lo else np.clip((a - lo) / (hi - lo), 0, 1)


def _fit(values, n, repeat=1) -> np.ndarray:
    a = np.repeat(np.asarray(values or [], dtype=float), repeat)[:n]
    return np.pad(a, (0, n - a.size)) if a.size < n else a


def _smooth(a: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or a.size == 0:
        return a
    k = np.ones(width) / width
    return np.convolve(np.pad(a, (width // 2, width - 1 - width // 2), mode="edge"), k, mode="valid")


@dataclass
class Timeline:
    """Per-second scores, kept so reasons and tests can explain a pick."""
    score: np.ndarray
    parts: dict
    sents: list
    label: list  # best event category per second ("" when none)


RANK_SMOOTH = 3  # seconds averaged when ranking (a moment is longer than one second)
WEIGHTS = {"event": 0.32, "talk": 0.14, "jump": 0.16, "loud": 0.08, "peaks": 0.04, "scene": 0.12, "heat": 0.14, "chat": 0.3}

# Chat: laughs, hype and "clip it". Channel emotes end in Giggle/LUL/KEK/Laugh/LOL/Pog etc.
_CHAT_LAUGH = re.compile(r"(?:\b(?:lul|lule|kekw|kekl|omegalul|lmao+|lmfao|lol+|icant|xdd+|ha(?:ha)+|pepelaugh|dead|crying)\b|[A-Za-z]+(?:Giggle|LUL|KEK|KEKW|Laugh|LOL|Lol)\b|\U0001F602|\U0001F923|\U0001F480)", re.I)
_CHAT_HYPE = re.compile(r"(?:\b(?:pog+|poggers|pogu|pogchamp|w+|omg|wtf|holy|monkas|clap|no way|what+)\b|[A-Za-z]+(?:Pog|POG|Hype|HYPE|Wow|WOW)\b|\?{2,}|!{3,})", re.I)
_CHAT_CLIP = re.compile(r"\bclip(?:ped| it| that| this)?\b", re.I)
CHAT_DELAY = 1  # Twitch VOD chat times already line up with what viewers saw (measured on the tune cases)


def chat_signal(chat, n) -> np.ndarray:
    """0..1 per second from chat replay [(seconds, text)]: bursts against the stream's own normal
    chat rate, laughs/hype, and "clip it". Moved earlier by CHAT_DELAY."""
    rate, fun, clips = np.zeros(n), np.zeros(n), np.zeros(n)
    for t, text in chat or []:
        i = int(float(t)) - CHAT_DELAY
        if 0 <= i < n:
            text = str(text)
            rate[i] += 1
            if _CHAT_LAUGH.search(text) or _CHAT_HYPE.search(text):
                fun[i] += 1
            if _CHAT_CLIP.search(text):
                clips[i] += 1
    if not rate.any():
        return np.zeros(n)
    r = _smooth(rate, 8)
    normal = np.array([float(np.median(r[max(0, t - 150): t + 150])) for t in range(n)]) + 0.05
    burst = np.clip((r / normal - 1) / 2.5, 0, 1)
    laughs = np.clip(_smooth(fun, 8) / normal / 2.5, 0, 1)
    clipped = np.clip(_smooth(clips, 8) * 4, 0, 1)
    return np.clip(0.45 * burst + 0.4 * laughs + 0.15 * clipped, 0, 1)


def timeline(duration, transcript, rms=None, peak=None, motion2=None, heat=None, chat=None) -> Timeline:
    """Score every second. rms/peak: one value per second; motion2: one per 2 s; heat: 0..1 per second;
    chat: [(seconds, message)] chat replay, when the link had one."""
    n = max(1, int(math.ceil(float(duration))))
    sents = sentences(transcript)
    rms_a, peak_a = _fit(rms, n), _fit(peak, n)
    db = 20 * np.log10(np.maximum(rms_a, 1e-4))
    # Loudness jump: now vs the typical level over the 20 s before (dB above it, 12 dB = full).
    jump = np.zeros(n)
    for t in range(n):
        past = db[max(0, t - 20): max(0, t - 2)]
        if past.size >= 4:
            jump[t] = max(0.0, db[t] - float(np.median(past))) / 12.0
    jump = np.clip(_smooth(jump, 2), 0, 1)
    loud = _norm(rms_a)
    peaks = _norm(peak_a / (rms_a + 1e-4))
    scene = _norm(_fit(motion2, n, repeat=2)) if motion2 else np.zeros(n)
    has_heat = bool(heat) and len(heat) > 0
    heat_a = np.clip(_fit(heat, n), 0, 1) if has_heat else np.zeros(n)

    event = np.zeros(n)
    label = [""] * n
    penalty = np.ones(n)
    for s in sents:
        a, b = max(0, int(s.start)), min(n, int(math.ceil(s.end)) + 1)
        if s.skip:
            penalty[max(0, a - 1): min(n, b + 1)] = np.minimum(penalty[max(0, a - 1): min(n, b + 1)], SKIP_WEIGHT[s.skip])
            continue
        if not s.events:
            continue
        cat, strength, _ = s.events[0]
        # Several kinds of event in one line ("they're chasing us... we crashed") is stronger.
        strength = min(1.0, strength + 0.06 * (len(s.events) - 1))
        if cat == "reaction":
            strength *= 0.75  # a reaction alone is weaker evidence than a named event
        # The line itself, plus a short fade after it (the reaction).
        for t in range(a, min(n, b + 4)):
            v = strength if t < b else strength * (1 - (t - b + 1) / 5)
            if v > event[t]:
                event[t] = v
                label[t] = cat

    # Funny back-and-forth: many short lines, questions or exclamations, laughter, in 16 s.
    talk = np.zeros(n)
    if sents:
        starts = np.array([s.start for s in sents])
        for t in range(n):
            lo, hi = np.searchsorted(starts, t - 8), np.searchsorted(starts, t + 8)
            near = [s for s in sents[lo:hi] if not s.skip]
            if len(near) < 3:
                continue
            short = sum(1 for s in near if s.end - s.start < 3.5)
            asks = sum(1 for s in near if s.text.rstrip().endswith(("?", "!")))
            laughs = sum(1 for s in near if _LAUGH.search(s.text.lower()))
            talk[t] = min(1.0, 0.07 * len(near) + 0.05 * short + 0.08 * asks + 0.2 * laughs)
        talk = _smooth(talk, 3)

    chat_a = chat_signal(chat, n)
    parts = {"event": event, "talk": talk, "jump": jump, "loud": loud, "peaks": peaks, "scene": scene, "heat": heat_a, "chat": chat_a}
    weights = dict(WEIGHTS)
    if not chat_a.any():
        weights.pop("chat")
    if not has_heat:
        weights.pop("heat")
    if not motion2:
        weights.pop("scene")
    total = sum(weights.values())
    score = sum(weights[k] * parts[k] for k in weights) / total
    score = _smooth(score, RANK_SMOOTH) * penalty
    return Timeline(score, parts, sents, label)


def _reasons(tl: Timeline, a: int, b: int) -> list[str]:
    a, b = max(0, a), min(len(tl.score), max(a + 1, b))
    out = []
    cats = [c for c in tl.label[a:b] if c]
    if cats:
        out.append(LABELS[max(set(cats), key=cats.count)])
    p = tl.parts
    if float(p["talk"][a:b].max(initial=0)) > 0.55 and LABELS["funny"] not in out:
        out.append(LABELS["funny"])
    if float(p["jump"][a:b].max(initial=0)) > 0.5:
        out.append("loud reaction")
    if float(p["scene"][a:b].max(initial=0)) > 0.7:
        out.append("big on-screen change")
    if float(p["heat"][a:b].max(initial=0)) > 0.6:
        out.append("most replayed")
    if float(p["chat"][a:b].max(initial=0)) > 0.5:
        out.append("chat went wild")
    return out or ["activity"]


@dataclass
class Moment:
    core_start: float
    core_end: float
    peak: float
    score: float
    reasons: list


def find_moments(duration, transcript, rms=None, peak=None, motion2=None, heat=None, chat=None, limit=None, spacing=14) -> tuple[list[Moment], Timeline]:
    """The strongest peaks, best first. The core is the event itself; setup and reaction get
    added when the clip is shaped."""
    tl = timeline(duration, transcript, rms, peak, motion2, heat, chat)
    limit = limit or max(60, int(float(duration) / 20))  # long streams need more for the 16:9 compilation
    score = tl.score.copy()
    n = len(score)
    moments: list[Moment] = []
    taken = np.zeros(n, dtype=bool)
    order = np.argsort(-score, kind="stable")
    for t in order:
        if len(moments) >= limit:
            break
        t = int(t)
        if taken[t] or score[t] <= 0:
            continue
        level = 0.75 * score[t]
        a = t
        while a > 0 and t - a < 6 and score[a - 1] >= level:
            a -= 1
        b = t + 1
        while b < n and b - t < 6 and score[b] >= level:
            b += 1
        # Keep the whole line that names the event inside the core.
        for s in tl.sents:
            if s.events and not s.skip and s.start < b and s.end > a and s.end - s.start < 12:
                a, b = min(a, int(s.start)), max(b, int(math.ceil(s.end)))
        moments.append(Moment(float(a), float(min(b, duration)), float(t), round(float(score[t]), 4), _reasons(tl, a, b)))
        taken[max(0, t - spacing): min(n, t + spacing)] = True
    return moments, tl


# ---- shaping a clip around a moment -----------------------------------------------------

SETUP_SECONDS = 8.0
REACTION_SECONDS = 10.0


def _inside(t: float, sents: list[Sentence]):
    """The sentence that t cuts through, if any (0.15 s grace at each end)."""
    for s in sents:
        if s.start + 0.15 < t < s.end - 0.15:
            return s
        if s.start > t:
            break
    return None


def cuts_mid_sentence(t: float, sents: list[Sentence]) -> bool:
    return _inside(t, sents) is not None


def shape_clip(core_start, core_end, sents, duration, length) -> tuple[float, float]:
    """Setup before, reaction after, about `length` seconds, both cuts between sentences."""
    length = min(float(length), float(duration))
    setup = min(SETUP_SECONDS, 0.3 * length)
    reaction = min(REACTION_SECONDS, 0.4 * length)
    core_start, core_end = max(0.0, core_start), min(float(duration), max(core_end, core_start + 0.5))
    if core_end - core_start > length - setup - reaction:
        # A long event: keep its end (the payoff) and the reaction.
        end = core_end + reaction
        start = end - length
    else:
        start, end = core_start - setup, core_end + reaction
        slack = length - (end - start)
        start -= 0.4 * slack
        end += 0.6 * slack
    if start < 0:
        end, start = end - start, 0.0
    if end > duration:
        start, end = max(0.0, start - (end - duration)), float(duration)
    min_len, max_len = length * 0.85, length * 1.2 + 2

    s = _inside(start, sents)
    if s is not None:
        earlier, later = max(0.0, s.start - 0.2), s.end + 0.1
        if start - earlier <= 5 or later > core_start:
            start = earlier
        else:
            start = later
    e = _inside(end, sents)
    if e is not None:
        longer, shorter = min(float(duration), e.end + 0.3), e.start - 0.1
        if longer - start <= max_len:
            end = longer
        elif shorter >= core_end and shorter - start >= min_len and not cuts_mid_sentence(shorter, sents):
            end = shorter
        else:
            end = longer
    # Moving a cut may have landed inside a neighbouring sentence; walk past it.
    for _ in range(6):
        s = _inside(start, sents)
        if s is None:
            break
        start = max(0.0, s.start - 0.2)
    for _ in range(6):
        e = _inside(end, sents)
        if e is None:
            break
        end = min(float(duration), e.end + 0.3)
    return round(start, 2), round(end, 2)
