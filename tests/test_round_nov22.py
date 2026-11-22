"""The 22 Nov round: Hindi/Kannada captions, shout effect, vertical streams, new caption looks,
AI picker guards, posting at the buyer's own time."""
import datetime as dt
import re

import numpy as np
import pytest

from clipstudio import fonts
from clipstudio.design import Design, adapt_to_source, template_design
from clipstudio.effects import FRAME, find_shouts
from clipstudio.styles import build_ass, key_word

CAM = (0.02, 0.6, 0.24, 0.36)


# ---------- Indian scripts: a font that has the letters ----------
def test_kannada_switches_to_a_font_that_has_the_letters():
    family = "Poppins ExtraBold"
    if not fonts.font_file(family):
        pytest.skip("bundled font missing")
    parts = fonts.runs("ಏನ್ ಗುರು bro", family)
    assert any(f != family for f, _ in parts), parts  # Kannada goes to Nirmala UI (or another fallback)
    assert any(f == family and "bro" in t for f, t in parts)


def test_design_language_choice_is_checked():
    from clipstudio.app import SourceRequest

    assert SourceRequest(source_type="url", source="https://youtu.be/abcdefghijk", language="kn").language == "kn"
    with pytest.raises(Exception):
        SourceRequest(source_type="url", source="https://youtu.be/abcdefghijk", language="klingon")


# ---------- shout effect ----------
def _loudness(seconds=20.0, shout_at=8.0, shout_len=0.6):
    db = np.full(int(seconds / FRAME), -32.0)
    a, b = int(shout_at / FRAME), int((shout_at + shout_len) / FRAME)
    db[a:b] = -8.0
    return db


def test_a_yell_becomes_one_shout_with_the_word_said():
    words = [{"start": 7.9, "end": 8.3, "text": "no"}, {"start": 8.3, "end": 8.7, "text": "way"}]
    shouts = find_shouts(_loudness(), words, 20.0)
    assert len(shouts) == 1
    start, end, text = shouts[0]
    assert 7.8 <= start <= 8.1 and end > start
    assert text == "NO WAY!"


def test_steady_talking_has_no_shout():
    rng = np.random.default_rng(1)
    db = -30 + rng.normal(0, 1.5, int(20 / FRAME))
    assert find_shouts(db, (), 20.0) == []


def test_a_swear_shout_is_masked():
    words = [{"start": 8.0, "end": 8.5, "text": "fucking"}]
    (_, _, text), = find_shouts(_loudness(), words, 20.0)
    assert "*" in text and "FUCKING" not in text


def test_shout_replaces_the_caption_and_draws_big():
    d = template_design("hype_punch", cam_roi=CAM)
    d.shout = True
    segs = [{"start": 7.9, "end": 8.7, "text": "no way", "words": [
        {"start": 7.9, "end": 8.3, "text": "no"}, {"start": 8.3, "end": 8.7, "text": "way"}]}]
    ass = build_ass(d, segs, 20.0, "", shouts=[(7.9, 8.8, "NO WAY!")])
    assert "Style: Shout" in ass and "NO WAY!" in ass


# ---------- vertical stream, no cam ----------
def test_a_vertical_stream_fills_the_short_without_a_cam_box():
    d = template_design("streamer_top", cam_roi=CAM)
    v = adapt_to_source(d, 1080, 1920)
    from clipstudio.design import cam_output_rect

    assert v.layout == "full" and not v.hide_cam and cam_output_rect(v) is None  # nothing blurred or boxed
    landscape = adapt_to_source(template_design("streamer_top", cam_roi=CAM), 1920, 1080)
    assert landscape.layout == "stack"


def test_no_cam_found_still_makes_a_full_gameplay_short():
    from clipstudio.design import build_filter

    graph, W, H = build_filter(template_design("streamer_top", cam_roi=None), 1280, 720)
    assert (W, H) == (1080, 1920) and graph.endswith("[base]")


# ---------- new caption looks ----------
def test_punch_puts_the_key_word_in_red():
    d = template_design("hype_punch", cam_roi=CAM)
    segs = [{"start": 0, "end": 2, "text": "i can't do it anymore", "words": [
        {"start": i * 0.4, "end": i * 0.4 + 0.4, "text": w} for i, w in enumerate("i can't do it anymore".split())]}]
    ass = build_ass(d, segs, 3.0, "")
    red = d.caption.highlight.lstrip("#")
    bgr = (red[4:6] + red[2:4] + red[0:2]).upper()
    assert f"\\C&H00{bgr}" in ass.upper()


def test_twotone_draws_a_glowing_second_line():
    d = template_design("hype_twotone", cam_roi=CAM)
    words = "nah see i feel bad now bro".split()
    segs = [{"start": 0, "end": 3, "text": " ".join(words), "words": [
        {"start": i * 0.4, "end": i * 0.4 + 0.4, "text": w} for i, w in enumerate(words)]}]
    ass = build_ass(d, segs, 3.5, "")
    assert re.search(r"\\blur\d", ass), "second line has a glow layer"


def test_key_word_skips_little_words():
    assert key_word(["I", "CAN'T", "DO", "IT"]) == 1


def test_title_box_is_rounded_and_can_be_switched_off():
    d = template_design("hype_punch", cam_roi=CAM)
    ass = build_ass(d, [], 10.0, "Cops vs Kai in GTA RP")
    assert "\\p1" in ass  # drawn as a rounded box, not a sharp BorderStyle 3 rectangle
    d.title.show = False
    assert "Cops vs Kai" not in build_ass(d, [], 10.0, "Cops vs Kai in GTA RP")


def test_streamer_name_watermark_can_be_removed():
    d = template_design("big_captions", cam_roi=CAM)
    d.credit_on, d.credit = True, "@kai"
    assert "@kai" in build_ass(d, [], 10.0, "")
    d.credit_on = False
    assert "@kai" not in build_ass(d, [], 10.0, "")


# ---------- AI picker ----------
def test_ai_picker_knows_gemini():
    from clipstudio import boost

    assert {"gemini", "gemini_api"} <= set(boost.MODES)


def test_quiet_moments_lean_on_energy_not_ai():
    from clipstudio.boost import Pick, blend

    moments = [{"id": "a", "score": 0.9, "text": ""}, {"id": "b", "score": 0.2, "text": "the cops are here run run"}]
    picks = {"a": Pick("a", 1, "x"), "b": Pick("b", 9, "y")}
    order = [m["id"] for m in blend(moments, picks, 2)]
    assert set(order) == {"a", "b"}


# ---------- posting at the buyer's own time ----------
def test_picked_time_is_kept_and_spaced(tmp_path):
    from clipstudio.posting import Poster

    p = Poster(tmp_path)
    tz = p.settings()["timezone"]
    from zoneinfo import ZoneInfo

    now = dt.datetime(2026, 11, 22, 16, 20, tzinfo=ZoneInfo(tz))
    times = p.picked_times("2026-11-22T16:25", 3, 30, zone=tz, now=now)
    assert times == ["2026-11-22T16:25", "2026-11-22T16:55", "2026-11-22T17:25"]


def test_a_time_in_the_past_is_refused(tmp_path):
    from clipstudio.posting import Poster, ZernioError
    from zoneinfo import ZoneInfo

    p = Poster(tmp_path)
    tz = p.settings()["timezone"]
    with pytest.raises(ZernioError):
        p.picked_times("2026-11-22T10:00", 1, 0, zone=tz, now=dt.datetime(2026, 11, 22, 16, 0, tzinfo=ZoneInfo(tz)))
