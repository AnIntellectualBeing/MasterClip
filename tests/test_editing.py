"""Lane C (1.6 editing): caption files, caption editing, animations, moment mix + learning,
question titles, chatbot helper, my caption styles, add clips."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from clipstudio import captionedit, chathelp, taste
from clipstudio.design import CaptionStyle, Design
from clipstudio.styles import build_ass, caption_animation
from clipstudio.titles import question_titles

SRT = """1
00:00:01,000 --> 00:00:02,500
hello there

2
00:00:03,000 --> 00:00:04,000
<i>second</i> line
"""
VTT = """WEBVTT

NOTE made by hand

00:01.000 --> 00:02.500 align:center
hello there

00:00:03.000 --> 00:00:04.000
second <b>line</b>
"""
ASS = r"""[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\b1}hello{\b0} there
Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,second\Nline, with comma
Dialogue: 0,0:00:00.00,0:00:04.00,Default,,0,0,0,,{\p1}m 0 0 l 10 10{\p0}
"""


@pytest.mark.parametrize("name,text", [("a.srt", SRT), ("a.vtt", VTT), ("a.ass", ASS)])
def test_caption_files_parse_to_the_same_cues(name, text):
    cues = captionedit.parse_caption_file(text, name)
    assert [(c["start"], c["end"]) for c in cues] == [(1.0, 2.5), (3.0, 4.0)]
    assert cues[0]["text"] == "hello there"
    assert cues[1]["text"].startswith("second line")


def test_caption_file_kind_found_without_extension():
    assert captionedit.parse_caption_file(VTT, "")[0]["text"] == "hello there"
    assert captionedit.parse_caption_file(ASS, "")[1]["text"] == "second line, with comma"
    with pytest.raises(ValueError):
        captionedit.parse_caption_file("x", "captions.docx")


def test_export_writes_clip_time_srt_and_vtt():
    transcript = [{"start": 10, "end": 12, "text": "one two"}, {"start": 30, "end": 31, "text": "far away"}]
    srt = captionedit.export_captions(transcript, 9, 20, "srt")
    assert "00:00:01,000 --> 00:00:03,000" in srt and "far" not in srt
    vtt = captionedit.export_captions(transcript, 9, 20, "vtt")
    assert vtt.startswith("WEBVTT") and "00:00:01.000 --> 00:00:03.000" in vtt


def test_import_for_a_clip_moves_times_and_replaces_only_that_part():
    words = [{"start": 4.0, "end": 4.5, "text": "keep"}, {"start": 5.5, "end": 6.0, "text": "drop"}]
    transcript = [{"start": 4.0, "end": 6.0, "text": "keep drop", "words": words},
                  {"start": 7, "end": 8, "text": "old words"}, {"start": 20, "end": 21, "text": "outside"}]
    out = captionedit.import_captions(transcript, captionedit.parse_caption_file(SRT, "x.srt"), 5, 15, True)
    texts = [c["text"] for c in out]
    assert texts == ["keep", "hello there", "second line", "outside"]
    assert out[1]["start"] == 6.0 and out[2]["end"] == 9.0


def test_import_for_whole_video_replaces_everything():
    out = captionedit.import_captions([{"start": 0, "end": 1, "text": "x"}], [{"start": 2, "end": 3, "text": "new"}], 0, 50, False)
    assert out == [{"start": 2.0, "end": 3.0, "text": "new"}]


def test_edited_cue_keeps_word_timing_only_when_words_still_match():
    words = [{"start": 1.0, "end": 1.4, "text": "helo"}, {"start": 1.5, "end": 2.2, "text": "ther"}]
    fixed = captionedit.clean_edited({"start": 1.0, "end": 2.0, "text": "hello there", "words": words})
    assert [w["text"] for w in fixed["words"]] == ["hello", "there"]
    assert fixed["words"][-1]["end"] <= 2.0  # squeezed inside the cue
    changed = captionedit.clean_edited({"start": 1.0, "end": 2.0, "text": "hello over there", "words": words})
    assert "words" not in changed


SEGMENTS = [{"start": 0.0, "end": 2.0, "text": "one two three",
             "words": [{"start": 0.0, "end": 0.6, "text": "one"}, {"start": 0.7, "end": 1.2, "text": "two"},
                       {"start": 1.3, "end": 2.0, "text": "three"}]}]


def _captions(design):
    return [line for line in build_ass(design, SEGMENTS, 5).splitlines() if line.startswith("Dialogue: 1")]


def test_auto_animation_keeps_old_behaviour():
    assert caption_animation(CaptionStyle(style="karaoke")) == "highlight"
    assert caption_animation(CaptionStyle(style="pop")) == "pop_auto"
    assert caption_animation(CaptionStyle(style="boxed")) == "full_line"
    assert len(_captions(Design(caption={"style": "karaoke", "words": 3}))) == 3
    assert len(_captions(Design(caption={"style": "boxed", "words": 3}))) == 1


@pytest.mark.parametrize("animation,events", [
    ("highlight", 3), ("pop_word", 3), ("one_by_one", 3), ("build_up", 3), ("pop_line", 1), ("full_line", 1)])
def test_animations_make_the_right_events(animation, events):
    lines = _captions(Design(caption={"style": "karaoke", "words": 3, "animation": animation}))
    assert len(lines) == events
    if animation == "one_by_one":
        assert "TWO" in lines[1] and "ONE" not in lines[1]
    if animation == "build_up":
        assert lines[0].count("\\alpha&HFF&") == 2 and "\\alpha&HFF&" not in lines[2]
    if animation == "pop_word":
        assert "\\t(0,80" in lines[1]
    if animation == "pop_line":
        assert "\\t(0,90" in lines[0]


def test_twoline_breaks_with_any_animation():
    seg = [{"start": 0, "end": 3, "text": "a b c d e f"}]
    design = Design(caption={"style": "twoline", "words": 6, "animation": "highlight"})
    lines = [x for x in build_ass(design, seg, 4).splitlines() if x.startswith("Dialogue: 1")]
    assert len(lines) == 6 and all("\\N" in x for x in lines)


def test_unknown_font_falls_back_and_installed_font_is_kept(monkeypatch):
    from clipstudio import fonts

    assert CaptionStyle(font="No Such Font 123").font == "Anton"
    monkeypatch.setattr(fonts, "installed_fonts", lambda: {"Arial": fonts.WINDOWS_FONTS / "arial.ttf"})
    assert CaptionStyle(font="Arial").font == "Arial"


def test_mix_changes_weights_and_balanced_is_unchanged(tmp_path):
    from clipstudio.moments import WEIGHTS

    assert taste.moment_weights(tmp_path, "balanced") is None
    action = taste.moment_weights(tmp_path, "action")
    dialogue = taste.moment_weights(tmp_path, "dialogue")
    assert action["jump"] > WEIGHTS["jump"] and action["talk"] < WEIGHTS["talk"]
    assert dialogue["talk"] > WEIGHTS["talk"] and dialogue["scene"] < WEIGHTS["scene"]


def test_mix_moves_which_moment_ranks_first():
    from clipstudio.moments import find_moments

    talk = [{"start": float(t), "end": t + 1.0, "text": "haha what? lol no!"} for t in range(2, 16)]
    rms = [0.02] * 60
    for t in range(40, 46):
        rms[t] = 0.5  # a loud bang far from the talk
    first = lambda mix: find_moments(60, talk, rms, rms, weights=taste.mix_weights(mix))[0][0].peak  # noqa: E731
    assert first("action") >= 38
    assert first("dialogue") < 20


def test_ratings_learn_after_eight_moments(tmp_path):
    for n in range(7):
        taste.record(tmp_path, "j", {"start": n * 30, "parts": {"jump": 1.0 if n % 2 else 0.0}}, 5 if n % 2 else 1)
    assert taste.learned(tmp_path)["active"] is False
    taste.record(tmp_path, "j", {"start": 0, "parts": {"jump": 0.0}}, 2)  # re-rating: still 7 moments
    assert taste.learned(tmp_path)["rated"] == 7
    taste.record(tmp_path, "j", {"start": 999, "parts": {"jump": 1.0}}, 5)
    info = taste.learned(tmp_path)
    assert info["active"] and info["factors"]["jump"] > 1.2
    assert "loud reactions" in info["notes"][0]
    assert taste.moment_weights(tmp_path)["jump"] > 0.16
    with pytest.raises(ValueError):
        taste.record(tmp_path, "j", {"start": 1}, 6)


def test_parts_from_reasons_for_older_clips():
    assert taste.parts_from_reasons(["Chase", "loud reaction"]) == {"event": 1.0, "jump": 1.0}


def test_question_titles_are_grounded_questions():
    ideas = question_titles("Bro the cops are chasing us! No way!", "Kai", "GTA RP night", current="Kai vs the cops")
    assert all(t.endswith("?") for t in ideas)
    assert any("cops" in t for t in ideas)
    assert question_titles("", "")[0]  # always something to offer


def test_chat_helper_parts_prompts_and_answers():
    transcript = [{"start": float(t), "end": t + 5.0, "text": f"line {t}"} for t in range(0, 3600, 60)]
    parts = chathelp.split_parts(transcript, 25 * 60, 3600)
    assert len(parts) == 3 and parts[0]["start"] == 0 and parts[-1]["end"] == 3600
    assert parts[0]["end"] == parts[1]["start"]
    prompt = chathelp.part_prompt(parts[1], 2, 3, 30, "GTA")
    assert "part 2 of 3" in prompt and "25:00 line 1500" in prompt
    lines = "Here you go:\n1. 12:30-13:05 He crashes the car\n- 1:02:10 - 1:02:40: Cops arrive\nnothing here\n12:30-13:05 again"
    moments = chathelp.parse_answer(lines, 4000)
    assert [(m["start"], m["end"]) for m in moments] == [(750, 785), (3730, 3760)]
    assert moments[0]["title"] == "He crashes the car"
    js = '```json\n{"moments": [{"start": "0:10", "end": "0:40", "title": "Hi"}, {"start": 5, "end": 6}]}\n```'
    assert chathelp.parse_answer(js) == [{"start": 10.0, "end": 40.0, "title": "Hi"}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_DATA", str(tmp_path))
    import clipstudio.app as module

    importlib.reload(module)
    with TestClient(module.app) as client:
        yield client, module


def _job(module, **extra):
    job = module.store.create("test")
    clips = [dict(index=0, kind="short", start=5.0, end=15.0, title="t", file="c.mp4", reasons=["loud reaction"],
                  design=Design().model_dump())]
    module.store.update(job["id"], status="complete", duration=60, source_path="missing.mp4", clips=clips, **extra)
    (module.store.folder(job["id"]) / "transcript.json").write_text(
        json.dumps([{"start": 6, "end": 8, "text": "the cops are chasing us!"}]), encoding="utf-8")
    return job["id"]


def test_caption_file_routes(client):
    client, module = client
    ident = _job(module)
    r = client.post(f"/api/jobs/{ident}/captions/import", files={"file": ("x.vtt", VTT.encode())},
                    data={"for_clip": "true", "start": "5", "end": "15"})
    assert r.status_code == 200 and r.json()["imported"] == 2
    assert [c["text"] for c in r.json()["segments"]] == ["hello there", "second line"]
    r = client.get(f"/api/jobs/{ident}/captions/export", params={"format": "srt", "start": 5, "end": 15, "name": "clip 1"})
    assert r.status_code == 200 and "00:00:01,000 --> 00:00:02,500" in r.text
    assert 'filename="clip 1.srt"' in r.headers["content-disposition"]
    bad = client.post(f"/api/jobs/{ident}/captions/import", files={"file": ("x.srt", b"1\n00:05:00,000 --> 00:05:01,000\nlate\n")},
                      data={"for_clip": "true", "start": "5", "end": "15"})
    assert bad.status_code == 400


def test_rating_title_ideas_and_taste_routes(client):
    client, module = client
    ident = _job(module)
    r = client.post(f"/api/jobs/{ident}/clips/0/rating", json={"stars": 4})
    assert r.status_code == 200 and r.json()["taste"]["rated"] == 1
    assert module.store.get(ident)["clips"][0]["rating"] == 4
    assert client.post(f"/api/jobs/{ident}/clips/0/rating", json={"stars": 9}).status_code == 422
    assert (module.store.root / "feedback.jsonl").is_file()
    ideas = client.post(f"/api/jobs/{ident}/clips/0/title-ideas").json()
    assert ideas["titles"] and all(t.endswith("?") for t in ideas["titles"])
    assert client.get("/api/taste").json()["needed"] == 8


def test_chat_helper_and_add_clip_routes(client):
    client, module = client
    ident = _job(module)
    helper = client.get(f"/api/jobs/{ident}/chat-helper").json()
    assert len(helper["parts"]) == 1 and "0:06 the cops" in helper["parts"][0]["prompt"]
    parsed = client.post(f"/api/jobs/{ident}/chat-helper/parse", json={"text": "0:05-0:20 Chase"}).json()
    assert parsed["moments"][0]["start"] == 5
    assert client.post(f"/api/jobs/{ident}/chat-helper/parse", json={"text": "hi"}).status_code == 400
    # the source file is gone in this test: a clear message instead of a broken job
    r = client.post(f"/api/jobs/{ident}/add-clips", json={"clips": [{"start": 5, "end": 20}]})
    assert r.status_code == 400 and "source" in r.json()["detail"]


def test_add_clips_renders_with_the_clips_look(client, monkeypatch, tmp_path):
    client, module = client
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    ident = _job(module)
    module.store.update(ident, source_path=str(src))
    seen = []
    monkeypatch.setattr("clipstudio.render.render_clip",
                        lambda source, s, e, out, design, segments, title, cancel_event=None: seen.append((s, e, design, title)))
    r = client.post(f"/api/jobs/{ident}/add-clips", json={"clips": [{"start": 5, "end": 20, "title": "Mine"}]})
    assert r.status_code == 202
    module.pool.shutdown(wait=True)
    job = module.store.get(ident)
    assert job["status"] == "complete" and len(job["clips"]) == 2
    assert job["clips"][1]["title"] == "Mine" and seen[0][:2] == (5, 20)


def test_my_caption_styles(client):
    client, module = client
    r = client.post("/api/caption-styles", json={"name": "Big yellow", "caption": {"style": "pop", "color": "#FFD233", "animation": "pop_word"}})
    assert r.status_code == 201
    styles = client.get("/api/caption-styles").json()["styles"]
    assert styles[0]["caption"]["animation"] == "pop_word" and "y" not in styles[0]["caption"]
    assert client.get("/api/templates").json()["caption_styles"][0]["name"] == "Big yellow"
    assert client.delete(f"/api/caption-styles/{styles[0]['id']}").status_code == 200
    assert client.post("/api/caption-styles", json={"name": "", "caption": {}}).status_code == 400


def test_saved_caption_edits_keep_word_timing(client):
    client, module = client
    ident = _job(module)
    words = [{"start": 6.0, "end": 6.5, "text": "teh"}, {"start": 6.6, "end": 7.5, "text": "cops"}]
    r = client.put(f"/api/jobs/{ident}/captions", json={"segments": [{"start": 6, "end": 8, "text": "the cops", "words": words}]})
    assert r.status_code == 200
    saved = json.loads((module.store.folder(ident) / "transcript.json").read_text(encoding="utf-8"))
    assert saved[0]["words"][0]["text"] == "the"


def test_recipe_accepts_mix():
    from clipstudio.app import Recipe

    assert Recipe().mix == "balanced"
    assert Recipe(mix="action").mix == "action"
