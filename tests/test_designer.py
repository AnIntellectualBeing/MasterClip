"""Video type (Short / Long / Both), the template editor's boxes, captions panel styles, emoji,
the buyer's own templates and the crash/support endpoints."""

import json

import pytest
from fastapi.testclient import TestClient

from clipstudio import app as app_module
from clipstudio.design import Design, cam_output_rect, game_area, template_design
from clipstudio.emoji import emoji_for, emoji_table
from clipstudio.mytemplates import MyTemplates
from clipstudio.styles import build_ass, emoji_overlays

CAM = [0.78, 0.43, 0.21, 0.2]
WORDS = [{"start": 0.0, "end": 3.0, "text": "the cops are chasing us right now lol", "words": [
    {"start": i * 0.35, "end": i * 0.35 + 0.3, "text": w} for i, w in enumerate("the cops are chasing us right now lol".split())
]}]


def test_dragged_boxes_win_over_the_layout():
    d = template_design("streamer_top", cam_roi=CAM, cam_rect=[0.1, 0.05, 0.8, 0.3], game_rect=[0, 0.4, 1, 0.5])
    assert cam_output_rect(d) == [0.1, 0.05, 0.8, 0.3]
    assert game_area(d) == [0, 0.4, 1, 0.5]
    plain = template_design("streamer_top", cam_roi=CAM)
    assert cam_output_rect(plain) == [0.0, 0.0, 1.0, plain.split]


def test_credit_can_move_and_resize():
    d = template_design("big_captions", credit="speed", credit_x=0.85, credit_y=0.1, credit_size=60)
    ass = build_ass(d, [], 5, "")
    assert "\\pos(918,192)" in ass
    assert "Style: Credit,Poppins ExtraBold," in ass
    with pytest.raises(ValueError):
        Design(credit_size=500)


def test_two_line_captions_break_once_and_stay_two_lines():
    d = template_design("yt_full")
    assert d.caption.style == "twoline" and d.aspect == "16:9"
    ass = build_ass(d, WORDS, 5, "")
    lines = [l for l in ass.splitlines() if l.startswith("Dialogue: 1")]
    assert lines and all(l.count("\\N") <= 1 for l in lines)
    assert any("\\N" in l and "\\q2" in l for l in lines)


def test_title_can_be_switched_off():
    d = template_design("big_captions", title={"show": False})
    assert "Dialogue: 2" not in build_ass(d, [], 5, "A title")


def test_emoji_overlays_follow_the_words():
    assert emoji_for("lol that was funny") == "1f602"
    assert emoji_for("nothing here") is None
    table = emoji_table()
    assert table["words"]["cops"] == "1f693" and "1f602" in table["files"]
    off = template_design("big_captions")
    assert emoji_overlays(off, WORDS, 5) == []
    on = template_design("big_captions", caption={"emoji": True})
    found = emoji_overlays(on, WORDS, 5)
    assert found and all(p.is_file() for p, *_ in found)
    starts = [start for _, start, *_ in found]
    assert starts == sorted(starts)


def test_my_templates_save_edit_delete(tmp_path):
    mine = MyTemplates(tmp_path)
    design = template_design("yt_cam_corner", cam_roi=CAM, credit="speed", cam_rect=[0.6, 0.6, 0.35, 0.35]).model_dump()
    item = mine.add("  My  GTA style ", design, base="yt_cam_corner", length=45)
    assert item["name"] == "My GTA style" and item["kind"] == "long" and item["mine"]
    assert "cam_roi" not in item["design"] and "credit" not in item["design"]
    assert item["design"]["cam_rect"] == [0.6, 0.6, 0.35, 0.35]
    item = mine.update(item["id"], name="Renamed")
    assert mine.all()[0]["name"] == "Renamed"
    # A saved template drives a render like a built-in one.
    d = template_design(item["id"], mine.all(), cam_roi=CAM)
    assert d.aspect == "16:9" and d.cam_rect == [0.6, 0.6, 0.35, 0.35]
    with pytest.raises(ValueError):
        mine.add("", design)
    mine.delete(item["id"])
    assert mine.all() == []
    with pytest.raises(KeyError):
        mine.delete(item["id"])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "my_templates", MyTemplates(tmp_path))
    monkeypatch.setenv("CLIPSTUDIO_LOGS", str(tmp_path))
    return TestClient(app_module.app)


def test_templates_api_lists_kinds_defaults_and_my_templates(client):
    data = client.get("/api/templates").json()
    kinds = {t["kind"] for t in data["templates"]}
    assert kinds == {"short", "long"}
    assert data["defaults"]["caption"]["style"] == "pop"
    assert data["emoji"]["words"]
    made = client.post("/api/my-templates", json={"name": "Mine", "design": template_design("big_captions").model_dump()})
    assert made.status_code == 201
    ident = made.json()["id"]
    assert any(t["id"] == ident for t in client.get("/api/templates").json()["templates"])
    assert client.put(f"/api/my-templates/{ident}", json={"name": "New name"}).json()["name"] == "New name"
    bad = client.post("/api/my-templates", json={"name": "x", "design": {"caption": {"color": "red"}}})
    assert bad.status_code == 400 and "validation" not in bad.json()["detail"]
    assert client.delete(f"/api/my-templates/{ident}").status_code == 200
    assert client.delete(f"/api/my-templates/{ident}").status_code == 404


def test_recipe_formats_for_both():
    recipe = app_module.Recipe(kind="both", formats=[
        {"kind": "short", "template": "big_captions"}, {"kind": "long", "template": "yt_full"},
    ]).model_dump()
    formats = app_module.recipe_formats(recipe)
    assert [f["kind"] for f in formats] == ["short", "long"]
    old = app_module.recipe_formats({"template": "big_captions", "aspect": "16:9"})
    assert old == [{"kind": "long", "template": "big_captions", "design": {"aspect": "16:9"}}]
    options = app_module.automatic_options("vertical", True, recipe)
    assert len(options["formats"]) == 2


def test_job_designs_render_both_shapes():
    job = {"cam_roi": CAM, "channel": "speed", "recipe": {"kind": "both", "formats": [
        {"kind": "short", "template": "streamer_top", "design": {"cam_roi": None, "caption": {"emoji": True}}},
        {"kind": "long", "template": "yt_full", "design": None},
    ]}}
    designs = app_module.job_designs(job)
    assert [(k, d.aspect) for k, _, d in designs] == [("short", "9:16"), ("long", "16:9")]
    short = designs[0][2]
    assert short.cam_roi == CAM and short.credit == "speed" and short.caption.emoji
    assert all(d.caption.style == "none" for _, _, d in app_module.job_designs(job, captions=False))


def test_licence_key_errors_are_plain_words(client):
    for key in ("", "QA-TEST-1"):
        r = client.post("/api/licence", json={"key": key})
        assert r.status_code == 400
        text = r.json()["detail"]
        assert "String should" not in text and "characters" not in text
        assert "key" in text.lower()


def test_page_errors_land_in_crash_log(client, tmp_path):
    assert client.post("/api/client-error", json={"message": "boom", "where": "app.js:1:2", "stack": "x"}).json()["ok"]
    assert "boom" in (tmp_path / "crash.log").read_text(encoding="utf-8")
    info = client.get("/api/support").json()
    assert info["crash_log"].endswith("crash.log")


def test_health_names_the_install_folder(client):
    from clipstudio.paths import APP_DIR

    health = client.get("/api/health").json()
    assert health["app_dir"] == str(APP_DIR.resolve())
