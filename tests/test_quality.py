from clipstudio.design import Design, build_filter
from clipstudio import sources
import subprocess
from clipstudio.media import find_tool, probe_media
from clipstudio.render import render_clip
from clipstudio.longform import intro_card


def test_4k_layout_preserves_source_detail_before_captions():
    graph, width, height = build_filter(Design(aspect="16:9", resolution=2160), 3840, 2160)
    assert (width, height) == (3840, 2160)
    assert "scale=3840:2160" in graph


def test_portrait_4k_and_hd_have_correct_dimensions():
    assert build_filter(Design(resolution=2160), 3840, 2160)[1:] == (2160, 3840)
    assert build_filter(Design(resolution=720), 1920, 1080)[1:] == (720, 1280)


def test_4k_download_does_not_prefer_lower_quality_hls():
    selection = sources.download_format(2160, section=True)
    assert "height<=2160" in selection
    assert "1080" not in selection
    assert not selection.startswith("bv*[protocol")


def test_real_4k_file_export(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "export.mp4"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=3840x2160:rate=5:duration=0.6", "-c:v", "libx264", "-preset", "ultrafast",
                    str(source)], check=True)
    design = Design(aspect="16:9", resolution=2160, caption={"style": "none"}, title={"show": False},
                    credit_on=False, shout=False)
    render_clip(source, 0, 0.4, output, design)
    info = probe_media(output)
    assert (info.width, info.height) == (3840, 2160)
    assert info.duration > 0


def test_compilation_intro_matches_selected_output_resolution(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=size=640x360:duration=1", "-c:v", "libx264", "-preset", "ultrafast", str(source)], check=True)
    output = tmp_path / "intro.mkv"
    intro_card(source, 0, output, "QA", "", seconds=0.4, resolution=720)
    info = probe_media(output)
    assert (info.width, info.height) == (1280, 720)
