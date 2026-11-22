"""Owner's 1.1 complaints: AI apps not found, Local AI error, auto-post vs minutes, blurry corner cam."""

from clipstudio import boost
from clipstudio.design import cam_cover, template_design


def test_app_found_in_npm_folder_even_when_not_on_path(tmp_path, monkeypatch):
    npm = tmp_path / "npm"
    npm.mkdir()
    (npm / "codex.cmd").write_text("@echo off")
    monkeypatch.setattr(boost.shutil, "which", lambda name: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert boost.find_app("codex").endswith("codex.cmd")


def test_small_model_answer_shape_is_understood():
    picks = boost.parse_picks({"m1": {"score": 8, "title": "Police chase"}, "m2": {"score": 3, "title": "Drive"}}, {"m1", "m2"})
    assert [(p.id, p.score) for p in picks] == [("m1", 8), ("m2", 3)]


def test_local_mode_is_a_valid_setting():
    from clipstudio.app import BoostRequest

    assert BoostRequest(mode="local").mode == "local"


def test_huge_cam_detection_is_not_blurred_over_the_gameplay():
    d = template_design("reaction_corner", cam_roi=[0.2, 0.2, 0.5, 0.5])
    assert cam_cover(d, (0, 0, 1920, 1080), 1920, 1080) is None
    small = template_design("reaction_corner", cam_roi=[0.78, 0.41, 0.21, 0.3])
    assert cam_cover(small, (0, 0, 1920, 1080), 1920, 1080) is not None
