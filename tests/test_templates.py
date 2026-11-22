"""Templates, layouts, caption styles, titles and facecam (step 2)."""

import subprocess

import numpy as np
import pytest

from clipstudio.design import (
    CANVAS,
    TEMPLATES,
    Design,
    build_filter,
    effective_layout,
    game_region,
    template_design,
)
from clipstudio.facecam import choose_facecam_roi, refine_to_panel
from clipstudio.media import find_tool, probe_media
from clipstudio.render import render_clip
from clipstudio.styles import build_ass, mask
from clipstudio.titles import suggest_titles

CAM = [0.78, 0.43, 0.21, 0.2]


def test_short_and_long_templates_exist_and_build_filters():
    short = [t["id"] for t in TEMPLATES if t["kind"] == "short"]
    long = [t["id"] for t in TEMPLATES if t["kind"] == "long"]
    assert short == ["streamer_top", "hype_punch", "hype_twotone", "big_captions", "reaction_corner",
                     "podcast_split", "meme_hook", "meme_caption"]
    assert len(long) >= 4
    for template in TEMPLATES:
        graph, W, H = build_filter(template_design(template["id"], cam_roi=CAM), 1280, 720)
        expected = CANVAS["9:16"] if template["kind"] == "short" else CANVAS["16:9"]
        assert (W, H) == expected and graph.endswith("[base]")


def test_cam_layouts_fall_back_to_full_without_a_cam():
    assert effective_layout(template_design("streamer_top")) == "full"
    assert effective_layout(template_design("streamer_top", cam_roi=CAM)) == "stack"
    assert effective_layout(template_design("streamer_top", cam_roi=CAM, aspect="16:9")) == "corner"


def test_stacked_gameplay_stays_centred_and_blurs_the_cam():
    from clipstudio.design import build_filter

    d = template_design("streamer_top", cam_roi=CAM)
    assert game_region(d) == [0.0, 0.0, 1.0, 1.0]  # centred on the action, not pushed left/right
    graph, _, _ = build_filter(d, 1920, 1080)
    assert "crop=1920:1080:0:0" in graph


def test_bad_design_values_are_rejected():
    with pytest.raises(ValueError):
        Design(caption={"color": "red"})
    with pytest.raises(ValueError):
        Design(cam_roi=[0.5, 0.5, 0.0, 0.1])
    assert Design(caption={"font": "Comic Sans"}).caption.font == "Anton"


def test_pop_captions_highlight_each_word_and_mask_swears():
    design = template_design("big_captions", credit="speed")
    segments = [{"start": 0.5, "end": 2.0, "text": "oh shit run", "words": [
        {"start": 0.5, "end": 0.9, "text": "oh"},
        {"start": 0.9, "end": 1.4, "text": "shit"},
        {"start": 1.4, "end": 2.0, "text": "run"},
    ]}]
    ass = build_ass(design, segments, 10, "Speed vs the cops")
    assert ass.count("Style: Caption,Luckiest Guy") == 1
    assert "S**T" in ass and "SHIT" not in ass
    assert "SPEED VS THE COPS" in ass  # yellow title style is upper case
    assert "@speed" in ass
    assert mask("fuck") == "f**k" and mask("hello") == "hello"


def test_titles_use_topic_and_streamer_without_emoji():
    titles = suggest_titles("No no the cops are here! Run!", "Kai Cenat")
    assert titles[0] == '"No no the cops are here!"'  # a real quote first: it is literally true
    assert "Kai Cenat vs the cops" in titles
    assert all(ord(ch) < 0x2600 for t in titles for ch in t)
    assert len(suggest_titles("", "")) == 3


def test_facecam_needs_a_persistent_edge_face():
    still = [[(1060, 350, 70, 90)] for _ in range(10)]
    assert choose_facecam_roi(1280, 720, still) is not None
    moving = [[(100 + i * 80, 300, 70, 90)] for i in range(10)]
    assert choose_facecam_roi(1280, 720, moving) is None


def test_panel_refine_snaps_to_border_lines():
    edges = np.zeros((720, 1280))
    edges[295, 1000:1272] = 1.0
    edges[442, 1000:1272] = 1.0
    edges[295:443, 1002] = 1.0
    edges[295:443, 1272] = 1.0
    box = refine_to_panel(edges, (1060, 350, 70, 90), 1280, 720)
    assert box == (1002, 295, 270, 147)


@pytest.mark.parametrize("template", ["streamer_top", "meme_hook"])
def test_render_one_clip_end_to_end(tmp_path, template):
    source = tmp_path / "src.mp4"
    subprocess.run(
        [find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=4:r=30",
         "-f", "lavfi", "-i", "sine=d=4", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(source)],
        check=True,
    )
    out = tmp_path / "clip.mp4"
    design = template_design(template, cam_roi=CAM, credit="me")
    render_clip(source, 0.5, 3.5, out, design,
                [{"start": 0.2, "end": 1.5, "text": "hello there"}], "A title")
    info = probe_media(out)
    assert (info.width, info.height) == (1080, 1920)
    assert abs(info.duration - 3.0) < 0.15 and info.has_audio


def test_clips_without_keywords_still_get_different_titles():
    from clipstudio.titles import suggest_titles

    used, titles = set(), []
    for _ in range(6):  # e.g. a Portuguese stream: no English keywords, no quotable sentence
        title = suggest_titles("eu vou la agora mano", "duascaras_rp", avoid=used)[0]
        used.add(title)
        titles.append(title)
    assert len(set(titles)) == 6
    # no words we understand: a lively topic line, never a bare "<name> on stream"
    assert "on stream" not in titles[0] and titles[0]
    # and the game is named when the stream title says it
    gta = suggest_titles("eu vou la agora mano", "duascaras_rp", context="GTA RP | dia 40")[0]
    assert "GTA" in gta, gta


def test_titles_never_claim_what_nobody_knows():
    from clipstudio.titles import suggest_titles

    lines = [
        "the cops are chasing us right now", "we crashed the car into the bank", "he got shot",
        "haha that was so funny", "", "eu vou la agora mano",
    ]
    banned = ("did not", "didn't", "escape", "goes wrong", "went wrong", "worst", "destroyed", "failed",
              "nobody saw", "wait for the end", "chat went", "can't believe", "should not", "most replayed")
    for text in lines:
        for title in suggest_titles(text, "Kai", count=12):
            assert not any(b in title.lower() for b in banned), title
    # signals are claimed only when they really fired for this clip
    assert "Chat reacted to this" in suggest_titles("", "Kai", count=5, reasons=["chat went wild"])
    assert "The most replayed part of the stream" in suggest_titles("", "Kai", count=5, reasons=["most replayed"])
    assert "Chat reacted to this" not in suggest_titles("", "Kai", count=12)


def test_remove_streamer_cam_blurs_it_out_of_the_picture():
    from clipstudio.design import cam_cover

    keep = template_design("yt_full", cam_roi=CAM)
    assert cam_cover(keep, (0, 0, 1280, 720), 1280, 720) is None
    assert "gblur" not in build_filter(keep, 1280, 720)[0]
    gone = template_design("yt_full", cam_roi=CAM, hide_cam=True)
    box = cam_cover(gone, (0, 0, 1280, 720), 1280, 720)
    assert box and all(v % 2 == 0 for v in box)
    assert abs(box[0] - 0.78 * 1280) <= 2 and abs(box[2] - 0.21 * 1280) <= 4
    assert "gblur" in build_filter(gone, 1280, 720)[0]
    # cam moved into its own box: the original spot is covered so the face isn't there twice
    moved = template_design("yt_cam_corner", cam_roi=[0.4, 0.0, 0.2, 0.25])
    assert "gblur" in build_filter(moved, 1280, 720)[0]
    # no cam found: nothing to remove, nothing breaks
    assert "gblur" not in build_filter(template_design("yt_full", hide_cam=True), 1280, 720)[0]


def test_meme_templates_top_bar_and_caption_on_top():
    bar = template_design("meme_hook")
    top = template_design("meme_caption")
    assert bar.layout == "meme" and top.layout == "full"
    assert top.title.style == "white_box" and top.title.seconds == 0 and top.caption.style != "none"
    assert template_design("yt_meme_top").layout == "meme" and template_design("yt_meme_caption").aspect == "16:9"
