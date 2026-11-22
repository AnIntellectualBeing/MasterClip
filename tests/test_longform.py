import json
import shutil
import subprocess

import pytest

from clipstudio.design import template_design
from clipstudio.longform import Moment, _ffmetadata, description, make_compilation, make_full, youtube_chapters

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_youtube_chapters_rules():
    assert youtube_chapters([(5, "a"), (20, "b")]) == []  # fewer than 3
    got = youtube_chapters([(3, "first"), (8, "too close"), (20, "second"), (40, "third")])
    assert got == [(0.0, "first"), (20, "second"), (40, "third")]


def test_description_lists_chapters_or_moments():
    text = description("Best bits", [(0, "Start"), (75, "Chase"), (3700, "End")], "#gta")
    assert "0:00 Start" in text and "1:15 Chase" in text and "1:01:40 End" in text and text.strip().endswith("#gta")
    short = description("Two", [], names=["One", "Two"])
    assert "• One" in short and "Chapters" not in short


def test_ffmetadata_escapes_names():
    meta = _ffmetadata([(0, "Intro"), (12.5, "a=b; #1")], 30)
    assert "START=12500" in meta and "END=30000" in meta and "title=a-b, No.1" in meta


def _video(path, seconds=14, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@needs_ffmpeg
@pytest.mark.parametrize("audio", [True, False])
def test_compilation_has_intro_chapters_and_sound(tmp_path, audio):
    src = _video(tmp_path / "src.mp4", audio=audio)
    design = template_design("big_captions", aspect="16:9", fill="blur", caption={"style": "classic"})
    segs = [{"start": 0.2, "end": 2.0, "text": "hello there", "words": []}]
    moments = [Moment(0, 4, "One", segs), Moment(5, 9, "Two", segs), Moment(10, 14, "Three", segs)]
    result = make_compilation(src, moments, tmp_path / "comp.mp4", design, "Test video")
    info = _probe(tmp_path / "comp.mp4")
    kinds = [s["codec_type"] for s in info["streams"]]
    assert "audio" in kinds and "video" in kinds
    assert abs(float(info["format"]["duration"]) - 15) < 0.5  # 3 s intro + 3 x 4 s
    assert [c["tags"]["title"] for c in info["chapters"]] == ["One", "Two", "Three"]
    assert result["chapters"][0][0] == 0 and "In this video" in result["description"]
    assert not list(tmp_path.glob(".comp-parts"))


@needs_ffmpeg
def test_full_length_keeps_whole_range(tmp_path):
    src = _video(tmp_path / "src.mp4", seconds=40)
    design = template_design("big_captions")
    result = make_full(src, 0, 40, [], tmp_path / "full.mp4", design, "Full",
                       [(0, "Start"), (12, "Middle"), (30, "End")])
    info = _probe(tmp_path / "full.mp4")
    assert abs(float(info["format"]["duration"]) - 40) < 0.5
    assert int(info["streams"][0]["width"]) == 1920
    assert len(info["chapters"]) == 3 and "0:30 End" in result["description"]
