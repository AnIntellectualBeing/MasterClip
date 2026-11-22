"""Check visible source corners survive a horizontal render."""

import subprocess
import numpy as np
from clipstudio.engine import Candidate, render_compilation
from clipstudio.media import probe_media


def test_horizontal_render_preserves_all_four_source_corners(tmp_path):
    source = tmp_path / "source.mp4"
    output = tmp_path / "horizontal.mp4"
    pattern = "color=black:size=320x240:rate=5,drawbox=x=0:y=0:w=40:h=40:color=red:t=fill,drawbox=x=280:y=0:w=40:h=40:color=lime:t=fill,drawbox=x=0:y=200:w=40:h=40:color=blue:t=fill,drawbox=x=280:y=200:w=40:h=40:color=yellow:t=fill"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            pattern,
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    render_compilation(source, [Candidate(0, 1)], output, vertical=False)
    info = probe_media(output)
    assert (info.width, info.height) == (1920, 1080)
    frame = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "0.2",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    rgb = np.frombuffer(frame, dtype=np.uint8).reshape(1080, 1920, 3)
    assert rgb[90, 330, 0] > 180 and rgb[90, 330, 1] < 80
    assert rgb[90, 1590, 1] > 180 and rgb[90, 1590, 0] < 80
    assert rgb[990, 330, 2] > 180 and rgb[990, 330, 0] < 80
    assert rgb[990, 1590, 0] > 180 and rgb[990, 1590, 1] > 180
    assert rgb[:, 0].max() < 30
