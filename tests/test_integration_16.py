"""1.6 integration fixes: one 'Use this on all clips of this video' switch, long titles over the cam."""

import json
import re
import threading

from clipstudio import app as app_module
from clipstudio.design import Design, apply_layout, template_design
from clipstudio.fonts import text_width
from clipstudio.styles import build_ass, title_lines

CAM = [0.78, 0.43, 0.21, 0.2]
LONG = "Can this streamer really escape five police cars on the highway tonight"


def _title_events(ass):
    return [x for x in ass.splitlines() if x.startswith("Dialogue: 3") and ",Title," in x]


def test_long_title_over_the_cam_is_drawn_at_the_smaller_size():
    d = template_design("streamer_top", cam_roi=CAM)
    cam_y = 0.0 + 0.2  # inside the cam band at the top (streamer_top puts the cam on top)
    d = d.model_copy(update={"title": d.title.model_copy(update={"y": cam_y, "size": 96})})
    lines, em, *_ = title_lines(d, LONG)
    assert em < 96  # shrunk to keep the face clear
    events = _title_events(build_ass(d, [], 5, LONG))
    assert len(events) == len(lines)
    share = round(em / 96 * 100)
    assert all(f"\\fscx{share}\\fscy{share}" in e for e in events)
    # every line, drawn at that size, stays inside the 1080 px frame
    for line in lines:
        assert text_width(line.upper() if d.title.style in ("meme", "yellow") else line, d.title.font, em) <= 1080 * 0.92


def test_short_title_keeps_its_size():
    d = template_design("streamer_top", cam_roi=CAM)
    events = _title_events(build_ass(d, [], 5, "Wow"))
    assert events and not any("\\fscx" in e for e in events)


def test_a_word_wider_than_the_frame_shrinks_to_fit():
    d = template_design("big_captions")
    d = d.model_copy(update={"title": d.title.model_copy(update={"size": 140})})
    word = "Supercalifragilisticexpialidocious"
    lines, em, *_ = title_lines(d, word)
    assert max(text_width(x, d.title.font, em) for x in lines) <= 1080 * 0.92 + 1
    assert re.search(r"\\fscx\d+", _title_events(build_ass(d, [], 5, word))[0])


def _job_with_two_shorts(tmp_path):
    job = app_module.store.create("apply-all-test")
    ident = job["id"]
    short = template_design("streamer_top", cam_roi=CAM).model_dump()
    clips = [dict(start=0, end=5, file="a.mp4", design=short, title="A", kind="short"),
             dict(start=5, end=10, file="b.mp4", design=short, title="B", kind="short")]
    app_module.store.update(ident, clips=clips, source_path="x.mp4", status="rendering")
    (app_module.store.folder(ident) / "transcript.json").write_text("[]", encoding="utf-8")
    app_module.events[ident] = threading.Event()
    return ident, short


def test_use_this_on_all_clips_carries_the_whole_look_and_remembers_the_framing(tmp_path, monkeypatch):
    import clipstudio.render as render

    rendered = []
    monkeypatch.setattr(render, "render_clip", lambda src, s, e, out, design, *a, **k: rendered.append(design))
    ident, short = _job_with_two_shorts(tmp_path)
    new = apply_layout(Design(**short), "trio").model_dump()
    new["caption"]["color"] = "#7CFFB2"
    app_module.rerender_clip(ident, 0, app_module.ClipEdit(start=0, end=5, title="A", design=new, apply_all=True))
    after = app_module.store.get(ident)
    assert len(rendered) == 2
    assert after["clips"][1]["design"]["layout"] == "trio"
    assert after["clips"][1]["design"]["caption"]["color"] == "#7CFFB2"
    assert after["clips"][1]["title"] == "B"
    assert after["framing"]["9:16"]["layout"] == "trio"  # new / live clips of this video get it
    assert after["status"] == "complete"


def test_old_framing_all_flag_still_works(tmp_path, monkeypatch):
    import clipstudio.render as render

    monkeypatch.setattr(render, "render_clip", lambda *a, **k: None)
    ident, short = _job_with_two_shorts(tmp_path)
    new = apply_layout(Design(**short), "circle").model_dump()
    new["caption"]["color"] = "#7CFFB2"
    app_module.rerender_clip(ident, 0, app_module.ClipEdit(start=0, end=5, title="A", design=new, framing_all=True))
    after = app_module.store.get(ident)
    assert after["clips"][1]["design"]["cam_shape"] == "circle"
    assert after["clips"][1]["design"]["caption"]["color"] != "#7CFFB2"  # framing only
    assert after["framing"]["9:16"]["layout"] == "corner"


def test_version_is_1_6():
    import clipstudio

    assert clipstudio.__version__ == "1.6.0"
    assert json.dumps(clipstudio.__version__)
