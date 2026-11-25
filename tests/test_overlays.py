"""Cards, My brand (handles bar, logo, credit line, background card), outro and music bed."""

import io
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from clipstudio import app as app_module
from clipstudio import overlays as ov
from clipstudio.design import CARD_KINDS, Design, template_design
from clipstudio.media import find_tool, probe_media
from clipstudio.mytemplates import MyTemplates
from clipstudio.render import render_clip
from clipstudio.styles import build_ass

CAM = [0.78, 0.43, 0.21, 0.2]
WORDS = [{"start": 0.2, "end": 1.5, "text": "hello there"}]


@pytest.fixture
def assets(tmp_path):
    ov.set_asset_dir(tmp_path / "brand")
    pic = io.BytesIO()
    Image.new("RGBA", (300, 120), (255, 90, 60, 255)).save(pic, "PNG")
    pic.seek(0)
    logo = ov.save_asset(pic, "logo.png")["id"]
    song = tmp_path / "song.wav"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=6", str(song)], check=True)
    music = ov.save_asset(song.open("rb"), "song.wav")["id"]
    clip = tmp_path / "end.mp4"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=1.5:r=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
    outro = ov.save_asset(clip.open("rb"), "end.mp4")["id"]
    yield {"logo": logo, "music": music, "outro": outro}
    ov.set_asset_dir(app_module.store.root / "brand")


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "src.mp4"
    subprocess.run(
        [find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=4:r=30",
         "-f", "lavfi", "-i", "sine=d=4", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(path)],
        check=True,
    )
    return path


def frame_at(path, at, w, h):
    raw = subprocess.run([find_tool("ffmpeg"), "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


def test_defaults_change_nothing():
    d = Design()
    assert d.card.kind == "none" and d.outro.mode == "off" and not d.music.file
    assert d.brand.frame == "none" and not d.brand.logo and not d.brand.handles
    assert not ov.has_extras(d) and ov.outro_seconds(d) == 0 and ov.music_file(d) is None
    assert "@me" in build_ass(template_design("streamer_top", credit="me"), WORDS, 3.0)


def test_bad_values_are_refused():
    with pytest.raises(ValueError):
        Design(card={"kind": "x_post", "avatar": "../../secret.png"})
    with pytest.raises(ValueError):
        Design(card={"kind": "ig_poll", "options": [{"text": "only one", "pct": 100}]})
    with pytest.raises(ValueError):
        Design(brand={"frame_color": "red"})


def test_credit_line_can_say_clip_channel():
    ass = build_ass(template_design("streamer_top", credit="@GamerBhai", credit_style="clip"), WORDS, 3.0)
    assert "Clip: GamerBhai" in ass and "@GamerBhai" not in ass


@pytest.mark.parametrize("kind", [k for k in CARD_KINDS if k not in ("none", "news")])
def test_every_card_draws_inside_the_frame(kind):
    for aspect in ("9:16", "16:9"):
        d = Design(aspect=aspect, credit="me", card={"kind": kind, "x": 0.98, "y": 0.02,
                                                      "text": "a very long hook " * 12})
        img, (x, y) = ov.card_image(d, "title")
        W, H = ov.frame_size(d)
        assert 0 <= x <= W - img.width and 0 <= y <= H - img.height
        assert img.mode == "RGBA" and img.getextrema()[3][1] == 255


def test_card_text_falls_back_to_the_clip_title():
    d = Design(card={"kind": "tiktok_hook"})
    assert ov.card_text(d, "Clip title") == "Clip title"
    assert ov.card_text(d.model_copy(update={"card": d.card.model_copy(update={"text": "Mine"})}), "x") == "Mine"


def test_card_size_follows_the_slider():
    small = ov.card_image(Design(card={"kind": "x_post", "size": 0.5}), "t")[0]
    big = ov.card_image(Design(card={"kind": "x_post", "size": 1.0}), "t")[0]
    assert small.width == 540 and big.width == 1080


def test_news_ticker_scrolls_inside_the_bar():
    d = Design(card={"kind": "news", "ticker": "Big news"})
    frame, strip, ty, loop_w, x0 = ov.news_images(d, "Headline")
    assert frame.size == (1080, 1920) and strip.width >= 1080 + loop_w
    assert 0 < ty < 1920 and x0 + ov.ticker_width(d, x0) <= 1080


def test_handles_bar_and_logo_positions(assets):
    d = Design(brand={"handles": [{"platform": p, "name": "me"} for p in ("youtube", "twitch", "kick", "instagram", "tiktok")],
                      "handles_y": 0.95, "logo": assets["logo"], "logo_corner": "br"})
    img, (x, y) = ov.handles_image(d)
    assert img.width <= 1080 and y + img.height <= 1920
    logo, (lx, ly) = ov.logo_image(d)
    assert lx + logo.width < 1080 and ly + logo.height < 1920 and lx > 540 and ly > 960
    assert ov.handles_image(Design(brand={"handles": [{"platform": "x", "name": " "}]})) is None


def test_music_volume_rises_at_the_end():
    d = Design(music={"file": "", "volume": 0.2, "peak": 0.8, "rise": 3})
    assert ov.music_volume_expr(d, 20) == "0.2+(0.8-0.2)*clip((t-17.0)/3.0,0,1)"
    assert ov.music_volume_expr(d.model_copy(update={"music": d.music.model_copy(update={"rise": 0})}), 20) == "0.2"


def test_render_with_card_brand_frame(source, tmp_path, assets):
    out = tmp_path / "card.mp4"
    design = template_design("streamer_top", cam_roi=CAM, credit="me", card={"kind": "x_post", "y": 0.5, "seconds": 2},
                             brand={"logo": assets["logo"], "frame": "color", "frame_color": "#00FF00",
                                    "handles": [{"platform": "youtube", "name": "me"}]})
    render_clip(source, 0.5, 3.5, out, design, WORDS, "A title")
    info = probe_media(out)
    assert (info.width, info.height) == (1080, 1920) and abs(info.duration - 3.0) < 0.15 and info.has_audio
    early = frame_at(out, 1.0, 1080, 1920)
    late = frame_at(out, 2.8, 1080, 1920)
    assert early[960, 540].min() > 230  # the white X card in the middle
    assert late[960, 540].min() < 230  # gone after 2 s
    assert early[5, 540][1] > 200 and early[5, 540][0] < 60  # green background card around the video


@pytest.mark.parametrize("mode", ["card", "video"])
def test_outro_and_music_make_the_clip_longer(source, tmp_path, assets, mode):
    out = tmp_path / f"outro-{mode}.mp4"
    design = template_design("big_captions", credit="me",
                             outro={"mode": mode, "seconds": 2, "video": assets["outro"], "bg": "#2040FF"},
                             music={"file": assets["music"], "volume": 0.3})
    render_clip(source, 0.5, 3.5, out, design, WORDS, "A title")
    info = probe_media(out)
    extra = 2.0 if mode == "card" else 1.5
    assert abs(info.duration - (3.0 + extra)) < 0.2 and info.has_audio


def test_joined_parts_skip_outro_and_music(source, tmp_path, assets):
    out = tmp_path / "part.mkv"
    design = template_design("yt_full", outro={"mode": "card"}, music={"file": assets["music"]})
    render_clip(source, 0.5, 2.5, out, design, WORDS, None)
    assert abs(probe_media(out).duration - 2.0) < 0.15


def test_my_templates_keep_cards_and_brand(tmp_path):
    store = MyTemplates(tmp_path)
    d = Design(card={"kind": "ig_poll", "text": "Q"}, credit_style="clip",
               brand={"handles": [{"platform": "kick", "name": "me"}]}, outro={"mode": "card"}).model_dump()
    item = store.add("Mine", d)
    again = template_design(item["id"], store.all())
    assert again.card.kind == "ig_poll" and again.brand.handles[0].name == "me"
    assert again.outro.mode == "card" and again.credit_style == "clip"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "my_templates", MyTemplates(tmp_path))
    ov.set_asset_dir(tmp_path / "brand")
    yield TestClient(app_module.app)
    ov.set_asset_dir(app_module.store.root / "brand")


def test_upload_and_preview_api(client):
    pic = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 30)).save(pic, "PNG")
    res = client.post("/api/brand-assets", files={"file": ("me.png", pic.getvalue(), "image/png")})
    assert res.status_code == 201
    ident = res.json()["id"]
    assert client.get(f"/api/brand-assets/{ident}").status_code == 200
    assert client.post("/api/brand-assets", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert client.get("/api/brand-assets/0123456789abcdef.png").status_code == 404
    design = Design(card={"kind": "x_post", "avatar": ident}).model_dump()
    res = client.post("/api/overlay-preview", json={"design": design, "part": "card", "title": "Hello"})
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(res.content)).width == round(0.86 * 1080)
    assert client.post("/api/overlay-preview", json={"design": Design().model_dump(), "part": "handles"}).status_code == 204
    assert client.post("/api/overlay-preview", json={"design": Design().model_dump(), "part": "outro"}).status_code == 200
