"""'Set it manually' framing: layout chips, cam shapes, a third area, blurred background,
'Use for all clips of this video' and templates that keep their boxes."""

import threading

import pytest

from clipstudio import app as app_module
from clipstudio.design import (
    LAYOUTS, Design, apply_layout, build_filter, cam_output_rect, corner_rect, effective_layout,
    extra_area, framing_of, game_area, layout_id, layout_patch, template_design,
)
from clipstudio.mytemplates import MyTemplates

CAM = [0.78, 0.43, 0.21, 0.2]


def test_there_are_eleven_layouts_with_plain_names_and_hints():
    assert len(LAYOUTS) == 11
    assert len({x["id"] for x in LAYOUTS}) == 11
    for item in LAYOUTS:
        assert item["name"] and item["hint"].endswith((".", ")")) and len(item["hint"]) < 90


@pytest.mark.parametrize("aspect", ["9:16", "16:9"])
def test_every_layout_builds_a_filter_and_is_recognised(aspect):
    for item in LAYOUTS:
        d = apply_layout(Design(aspect=aspect, cam_roi=CAM), item["id"])
        graph, W, H = build_filter(d, 1920, 1080)
        assert graph.endswith("[base]") and graph.count("[base]") == 1
        if aspect == "9:16" or item["id"] not in ("cam_top", "cam_bottom", "halves"):
            assert layout_id(d) == item["id"], (aspect, item["id"])


def test_defaults_do_not_change_old_designs():
    old = template_design("streamer_top", cam_roi=CAM)
    assert old.cam_shape == "frame" and not old.bg_blur and old.extra_rect is None
    graph = build_filter(old, 1920, 1080)[0]
    assert "alphamerge" not in graph and "boxblur" not in graph
    corner = template_design("reaction_corner", cam_roi=CAM)
    assert "pad=" in build_filter(corner, 1920, 1080)[0] and "color=white" in build_filter(corner, 1920, 1080)[0]


def test_circle_and_round_cams_use_a_one_frame_mask():
    circle = apply_layout(Design(cam_roi=CAM), "circle")
    g = build_filter(circle, 1920, 1080)[0]
    assert "alphamerge" in g and "hypot" in g and "r=1:d=1" in g
    x, y, w, h = cam_output_rect(circle)
    assert abs(w * 1080 - h * 1920) < 4  # square on screen
    rounded = apply_layout(Design(cam_roi=CAM), "pip")
    assert rounded.cam_shape == "round" and "alphamerge" in build_filter(rounded, 1920, 1080)[0]


def test_corner_rect_keeps_clear_of_the_title_and_name():
    tl, br = corner_rect("9:16", "tl", 0.4), corner_rect("9:16", "br", 0.4)
    assert tl[1] >= 0.15 and br[1] + br[3] <= 0.96 and br[0] + br[2] <= 1
    wide = corner_rect("16:9", "tr", 0.3)
    assert wide[0] + wide[2] <= 1 and wide[3] == 0.3


def test_side_by_side_puts_a_blurred_copy_behind():
    d = apply_layout(Design(cam_roi=CAM), "side")
    assert effective_layout(d) == "side" and d.bg_blur
    g = build_filter(d, 1920, 1080)[0]
    assert "boxblur" in g and "[bg0][g1]overlay" in g
    assert game_area(d)[0] + game_area(d)[2] <= cam_output_rect(d)[0] + 1e-6


def test_trio_shows_a_third_part_of_the_stream():
    d = apply_layout(Design(cam_roi=CAM, extra_roi=[0.0, 0.0, 0.25, 0.3]), "trio")
    assert effective_layout(d) == "trio" and extra_area(d) == [0.0, 0.72, 1.0, 0.28]
    g = build_filter(d, 1920, 1080)[0]
    assert "crop=480:324:0:0" in g and "[xtra]" in g and "split=3" in g
    wide = d.model_copy(update={"aspect": "16:9"})
    assert effective_layout(wide) == "side"  # three rows don't fit a 16:9 video


def test_cam_layouts_without_a_cam_fall_back_to_full_gameplay():
    for lid in ("side", "trio", "circle", "pip"):
        d = apply_layout(Design(), lid)
        assert effective_layout(d) == "full"
        assert build_filter(d, 1920, 1080)[0].endswith("[base]")


def test_vertical_streams_ignore_the_manual_boxes():
    from clipstudio.design import adapt_to_source

    d = adapt_to_source(apply_layout(Design(cam_roi=CAM, extra_roi=CAM), "trio"), 1080, 1920)
    assert d.layout == "full" and d.extra_rect is None and not d.bg_blur


def test_layout_patch_resets_dragged_boxes():
    patch = layout_patch("full")
    assert patch["cam_rect"] is None and patch["game_rect"] is None and patch["extra_rect"] is None


def test_templates_drop_source_boxes_unless_asked_to_keep_them(tmp_path):
    store = MyTemplates(tmp_path)
    loose = store.add("Loose", apply_layout(Design(cam_roi=CAM, game_roi=[0, 0, 0.7, 1]), "trio").model_dump())
    assert "cam_roi" not in loose["design"] and loose["needs_cam"]
    kept = store.add("Mine", {**apply_layout(Design(cam_roi=CAM, extra_roi=[0, 0, 0.2, 0.2]), "trio").model_dump(),
                              "keep_boxes": True})
    assert kept["design"]["cam_roi"] == CAM and kept["design"]["extra_roi"] == [0, 0, 0.2, 0.2]


def test_job_design_uses_kept_boxes_and_video_framing(tmp_path, monkeypatch):
    store = MyTemplates(tmp_path)
    monkeypatch.setattr(app_module, "my_templates", store)
    kept = store.add("Mine", {**apply_layout(Design(cam_roi=CAM), "circle").model_dump(), "keep_boxes": True})
    job = {"cam_roi": [0.0, 0.0, 0.2, 0.2], "recipe": {}}
    assert app_module.job_design(job, kept["id"]).cam_roi == CAM  # the template's own box wins
    assert app_module.job_design(job, "streamer_top").cam_roi == [0.0, 0.0, 0.2, 0.2]
    framing = framing_of(apply_layout(Design(cam_roi=CAM), "side"))
    job["framing"] = {"9:16": framing}
    d = app_module.job_design(job, "streamer_top")
    assert d.layout == "side" and d.cam_roi == CAM and d.caption.style == "pop"  # captions stay the template's


def test_use_for_all_clips_puts_the_framing_on_the_other_clips(tmp_path, monkeypatch):
    import clipstudio.render as render

    rendered = []
    monkeypatch.setattr(render, "render_clip", lambda src, s, e, out, design, *a, **k: rendered.append((out.name, design)))
    job = app_module.store.create("framing-test")
    ident = job["id"]
    short = template_design("streamer_top", cam_roi=CAM).model_dump()
    long = template_design("yt_full", cam_roi=CAM).model_dump()
    clips = [dict(start=0, end=5, file="a.mp4", design=short, title="A"),
             dict(start=5, end=10, file="b.mp4", design=short, title="B"),
             dict(start=10, end=15, file="c.mp4", design=long, title="C")]
    app_module.store.update(ident, clips=clips, source_path="x.mp4", status="rendering")
    new = apply_layout(Design(**short), "circle")
    app_module._framing_to_all(ident, 0, new, [], threading.Event())
    after = app_module.store.get(ident)
    assert [name for name, _ in rendered] == [after["clips"][1]["file"]]  # only the other Short
    assert after["clips"][1]["design"]["cam_shape"] == "circle"
    assert after["clips"][1]["design"]["caption"] == short["caption"]
    assert after["clips"][2]["design"]["layout"] == "full"
    assert after["framing"]["9:16"]["layout"] == "corner" and after["status"] == "complete"
