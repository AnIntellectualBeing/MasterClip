"""Rule-based moment picker: GTA events, skipped lines, setup/reaction, sentence-safe cuts,
"most replayed" and chat signals, and the test-set score staying sane."""

import json
from pathlib import Path

import numpy as np
import pytest

from clipstudio import twitch
from clipstudio.app import heat_curve
from clipstudio.engine import Candidate, rank_moments
from clipstudio.moments import classify, cuts_mid_sentence, sentences, shape_clip, timeline
from clipstudio.shorts import select_shorts

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("text,category", [
    ("The cops are chasing us down the highway!", "chase"),
    ("We crashed into a barrier!", "crash"),
    ("Nailed it! Clean landing.", "stunt"),
    ("Shots fired, shots fired!", "fight"),
    ("This NPC just stole my car", "npc_chaos"),
    ("You are under arrest, put your hands up", "roleplay"),
    ("Mission passed, we did it!", "mission"),
    ("LET'S GOOO!", "mission"),
    ("haha I can't stop laughing", "funny"),
    ("police aa gayi bhago bhago", "chase"),
])
def test_gta_events_are_found(text, category):
    events, skip = classify(text)
    assert skip == "" and events[0][0] == category


@pytest.mark.parametrize("text,why", [
    ("Thank you so much for the 5 gifted subs!", "thank-you"),
    ("thanks for the raid guys, welcome in", "thank-you"),
    ("Earlier today we crashed into a wall, it was crazy", "story"),
    ("Remember when the cops chased us last stream?", "story"),
    ("The plan is we rob the bank and lose the cops", "plan"),
    ("What if we crashed into the police station", "what-if"),
])
def test_thank_yous_stories_plans_and_what_ifs_are_skipped(text, why):
    events, skip = classify(text)
    assert skip == why and events == []


@pytest.mark.parametrize("text", ["we didn't crash", "let's go to the bank", "I landed in Los Santos", "one if you did, two if you didn't"])
def test_everyday_lines_are_not_events_or_skips(text):
    assert classify(text) == ([], "")


def words(text, start, gap=0.3, each=0.3):
    out, t = [], start
    for w in text.split():
        out.append({"start": round(t, 2), "end": round(t + each, 2), "text": w})
        t += each + gap
    return out


def seg(text, start, **kw):
    ws = words(text, start, **kw)
    return {"start": ws[0]["start"], "end": ws[-1]["end"], "text": text, "words": ws}


def test_sentences_split_at_punctuation_and_pauses():
    s = sentences([seg("They are chasing us. We crashed! oh no", 10)])
    assert [x.text for x in s] == ["They are chasing us.", "We crashed!", "oh no"]
    assert s[1].events[0][0] == "crash"
    # One endless run with no punctuation or pause still gets cut into pieces of 14 s or less.
    long = sentences([seg(" ".join(["word"] * 60), 0, gap=0.1)])
    assert len(long) > 1 and all(x.end - x.start <= 14 for x in long)


def flat(n, level=0.02):
    return [level] * n, [level * 3] * n


def test_event_line_lifts_and_thank_you_line_sinks():
    rms, peak = flat(120)
    rms[40:44] = [0.2] * 4
    rms[90:94] = [0.2] * 4  # equally loud, but it's a donation shout-out
    peak = [r * 3 for r in rms]
    transcript = [seg("They are chasing us! We crashed into a truck!", 39), seg("Thank you so much for the ten gifted subs!", 89)]
    tl = timeline(120, transcript, rms, peak)
    assert tl.score[38:48].max() > 2 * tl.score[86:98].max()
    assert "crash" in tl.label[38:48] or "chase" in tl.label[38:48]


def test_most_replayed_and_chat_lift_a_quiet_moment():
    rms, peak = flat(200)
    plain = timeline(200, [], rms, peak).score
    heat = [0.0] * 200
    heat[120:135] = [1.0] * 15
    with_heat = timeline(200, [], rms, peak, heat=heat).score
    assert int(np.argmax(with_heat)) in range(115, 140) and with_heat[125] > plain[125]
    chat = [(t / 4, "hello") for t in range(0, 800)]  # 1 message a second
    chat += [(60 + i / 10, "KEKW KEKW") for i in range(80)]  # a laugh burst at 60 s
    tl = timeline(200, [], rms, peak, chat=chat)
    assert int(np.argmax(tl.score)) in range(50, 75)
    assert tl.parts["chat"][60] > 0.5


def test_clip_keeps_setup_and_reaction_when_nobody_talks():
    start, end = shape_clip(100, 104, [], 600, 30)
    assert end - start == pytest.approx(30, abs=0.01)
    assert 100 - start >= 8 and end - 104 >= 10


def test_clip_never_starts_or_ends_mid_sentence():
    # Wall-to-wall talk: sentences of ~4 s back to back.
    transcript = [seg(f"line number {i} goes on and on here.", 4.4 * i, gap=0.2) for i in range(120)]
    sents = sentences(transcript)
    for core in (37.0, 101.3, 222.2, 400.0):
        start, end = shape_clip(core, core + 3, sents, 528, 30)
        assert not cuts_mid_sentence(start, sents) and not cuts_mid_sentence(end, sents)
        assert 30 * 0.8 <= end - start <= 30 * 1.5
        assert start <= core - 4 and end >= core + 3 + 6


def test_short_clips_scale_setup_and_reaction_down():
    start, end = shape_clip(50, 52, [], 300, 15)
    assert end - start == pytest.approx(15, abs=0.01) and start < 50 and end > 52


def test_select_shorts_shapes_around_the_event_core():
    cands = [Candidate(80, 120, 0.9, ["Crash"], 98, 101), Candidate(300, 330, 0.5, ["activity"])]
    out = select_shorts(cands, [], 600, length=30, count=2)
    assert out[0].core_start == 98 and out[0].start <= 90 and out[0].end >= 111
    assert out[1].core_start is None  # old candidates (no core) still work


def test_rank_moments_gives_cores_and_reasons():
    rms, peak = flat(300)
    rms[150:154] = [0.3] * 4
    peak = [r * 3 for r in rms]
    found = rank_moments(300, [seg("They are chasing us! We crashed into a truck!", 149)], rms, peak)
    best = found[0]
    assert best.core_start <= 151 <= best.core_end and best.start < best.core_start and best.end > best.core_end
    assert best.reasons[0] in {"Crash", "Chase"}


def test_heat_curve_maps_vod_time_to_the_downloaded_part():
    points = [{"start": 1000, "end": 1010, "value": 0.5}, {"start": 1010, "end": 1020, "value": 1.0}]
    curve = heat_curve(points, 995, 30)
    assert curve[4] == 0 and curve[5] == 0.5 and curve[16] == 1.0 and len(curve) == 30
    assert heat_curve([], 0, 30) is None


def test_twitch_vod_id_and_chat_paging(monkeypatch):
    assert twitch.vod_id("https://www.twitch.tv/videos/2930713449?t=1h") == "2930713449"
    assert twitch.vod_id("https://www.youtube.com/watch?v=x") is None
    pages = {
        100: [(1, 100, "a"), (2, 101, "KEKW"), (3, 103, "b")],
        103: [(3, 103, "b"), (4, 104, "clip it"), (5, 130, "late")],
    }
    asked = []

    def fake(query, variables, cancel_event=None):
        asked.append(variables)
        rows = pages[variables["off"]]
        return {"video": {"comments": {"edges": [
            {"node": {"id": str(i), "contentOffsetSeconds": t, "message": {"fragments": [{"text": m}]}}} for i, t, m in rows
        ], "pageInfo": {"hasNextPage": True}}}}

    monkeypatch.setattr(twitch, "_gql", fake)
    chat = twitch.vod_chat("123", 100, 120)
    assert chat == [(100.0, "a"), (101.0, "KEKW"), (103.0, "b"), (104.0, "clip it")]
    assert [v["off"] for v in asked] == [100, 103] and all(set(v) == {"id", "off"} for v in asked)


@pytest.mark.skipif(not (ROOT / "moment_eval" / "cases").is_dir(), reason="test set not on this PC")
def test_test_set_score_stays_sane():
    import sys

    sys.path.insert(0, str(ROOT))
    from tools import score_moments

    argv, sys.argv = sys.argv, ["score_moments.py"]
    try:
        out = score_moments.main()
    finally:
        sys.argv = argv
    new, old, rand = out["new/all"], out["old/all"], out["random/all"]
    assert new["clean cuts"] == 1.0  # never mid-sentence
    assert new["score"] > rand["score"] + 5
    assert new["auc"] >= old["auc"]
    case = json.loads(next((ROOT / "moment_eval" / "cases").glob("*.json")).read_text(encoding="utf-8"))
    assert case["moments"] and case["transcript"] and case["rms"]
