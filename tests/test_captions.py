import hashlib
import subprocess
import threading
import sys
import time

import pytest

from clipstudio.captions import (
    CaptionError,
    _wait_for_process,
    _ass_escape,
    _srt_to_ass,
    burn_captions,
    phrase_segments,
)
from clipstudio.media import probe_media


def make_video(path):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def frame_hash(path):
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            "1",
            "-i",
            str(path),
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
    )
    return hashlib.sha256(result.stdout).hexdigest()


def frame_rgb(path, timestamp):
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            str(timestamp),
            "-i",
            str(path),
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
    )
    import numpy as np

    return np.frombuffer(result.stdout, dtype=np.uint8)


def make_black_video(path, width, height):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r=24:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def make_video_with_native_subtitle_band(path, width, height, band_height):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r=24:d=2,drawbox=x=0:y=ih-{band_height}:w=iw:h={band_height}:color=gray:t=fill",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def light_pixel_bounds(rgb, width, height):
    import numpy as np

    image = rgb.reshape(height, width, 3)
    ys, xs = np.where(np.all(image > 220, axis=2))
    assert len(xs), "caption text should produce measurable light pixels"
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def test_phrase_segments_split_long_text_and_preserve_approximate_timing():
    phrases = phrase_segments(
        [
            {
                "start": 4,
                "end": 12,
                "text": "one two three four five six seven eight nine ten eleven",
            }
        ],
        max_words=5,
    )
    assert [p["text"] for p in phrases] == [
        "one two three four five",
        "six seven eight nine ten",
        "eleven",
    ]
    assert phrases[0]["start"] == pytest.approx(4)
    assert phrases[-1]["end"] == pytest.approx(12)
    assert all(
        a["end"] == pytest.approx(b["start"]) for a, b in zip(phrases, phrases[1:])
    )
    assert all(phrase["timing_estimated"] is True for phrase in phrases)


def test_phrase_segments_use_word_timestamps_and_split_on_pauses():
    segments = [
        {
            "start": 0.0,
            "end": 4.0,
            "text": "We need to move now, then go.",
            "words": [
                {"start": 0.1, "end": 0.35, "text": "We", "probability": 0.9},
                {"start": 0.36, "end": 0.55, "text": "need", "probability": 0.9},
                {"start": 0.56, "end": 0.72, "text": "to", "probability": 0.9},
                {"start": 0.75, "end": 1.0, "text": "move", "probability": 0.9},
                {"start": 1.01, "end": 1.3, "text": "now,", "probability": 0.9},
                {"start": 2.1, "end": 2.3, "text": "then", "probability": 0.9},
                {"start": 2.31, "end": 2.6, "text": "go.", "probability": 0.9},
            ],
        }
    ]
    phrases = phrase_segments(segments, max_words=4, pause_seconds=0.5)
    assert [phrase["text"] for phrase in phrases] == ["We need to move", "now,", "then go."]
    assert (phrases[0]["start"], phrases[0]["end"]) == pytest.approx((0.1, 1.0))
    assert (phrases[1]["start"], phrases[1]["end"]) == pytest.approx((1.01, 1.3))
    assert phrases[0]["words"][0]["probability"] == pytest.approx(0.9)


def test_ass_uses_explicit_playres_and_escapes_user_text():
    ass = _srt_to_ass(
        "1\n00:00:01,230 --> 00:00:02,500\nA {literal} path \\\\\nsecond line\n",
        1920,
        1080,
    )
    assert "PlayResX: 1920" in ass
    assert "PlayResY: 1080" in ass
    assert "WrapStyle: 0" in ass
    assert "Style: Default,Nirmala UI,42," in ass
    assert "Dialogue: 0,0:00:01.23,0:00:02.50" in ass
    assert r"A \{literal\} path " + "\\" * 4 + r"\Nsecond line" in ass
    assert _ass_escape("first\nsecond") == r"first\Nsecond"


@pytest.mark.parametrize(
    ("width", "height", "max_glyph_height", "min_bottom_margin", "max_bottom_margin"),
    [
        (1920, 1080, 80, 95, 150),
        (1080, 1920, 100, 250, 350),
    ],
)
def test_caption_glyph_size_and_bottom_margin_match_output_pixels(
    tmp_path, width, height, max_glyph_height, min_bottom_margin, max_bottom_margin
):
    source = tmp_path / f"black-{width}x{height}.mp4"
    srt = tmp_path / "captions.srt"
    make_black_video(source, width, height)
    srt.write_text(
        "1\n00:00:00,200 --> 00:00:01,800\nA readable caption\n",
        encoding="utf-8",
    )
    burn_captions(source, srt)
    x0, y0, x1, y1 = light_pixel_bounds(frame_rgb(source, 0.8), width, height)
    glyph_height = y1 - y0 + 1
    bottom_margin = height - y1 - 1
    assert 20 <= glyph_height <= max_glyph_height
    assert min_bottom_margin <= bottom_margin <= max_bottom_margin
    assert x1 - x0 + 1 < width * 0.9


@pytest.mark.parametrize(
    ("width", "height", "band_height"),
    [(1920, 1080, 100), (1080, 1920, 220)],
)
def test_caption_lane_stays_above_native_subtitle_band_without_cropping(
    tmp_path, width, height, band_height
):
    source = tmp_path / f"native-{width}x{height}.mp4"
    srt = tmp_path / "captions.srt"
    make_video_with_native_subtitle_band(source, width, height, band_height)
    srt.write_text(
        "1\n00:00:00,200 --> 00:00:01,800\nA readable caption\n",
        encoding="utf-8",
    )
    before = probe_media(source)
    burn_captions(source, srt)
    after = probe_media(source)
    assert (after.width, after.height) == (before.width, before.height) == (width, height)
    import numpy as np

    frame = frame_rgb(source, 0.8).reshape(height, width, 3)
    caption_region = frame[: height - band_height]
    ys, xs = np.where(np.all(caption_region > 220, axis=2))
    assert len(xs), "caption text should be rendered in the upper caption lane"
    assert int(ys.max()) < height - band_height
    bottom_band = frame[height - band_height :]
    assert not np.any(np.all(bottom_band > 220, axis=2)), "caption text must not overlap the native subtitle lane"
    assert 110 <= float(bottom_band.mean()) <= 145, "native subtitle band must remain visible"


def test_burn_captions_handles_special_paths_and_preserves_video_and_audio(tmp_path):
    source = tmp_path / "O'Brien gameplay.mp4"
    srt = tmp_path / "Player's captions.srt"
    make_video(source)
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:03,500\nA caption visible in this frame\n",
        encoding="utf-8",
    )
    original_hash = frame_hash(source)
    before = probe_media(source)
    output = burn_captions(source, srt)
    assert output == source
    after = probe_media(source)
    assert after.duration == pytest.approx(before.duration, abs=0.15)
    assert after.has_audio is True
    assert frame_hash(source) != original_hash
    assert not list(tmp_path.glob("*.part.mp4"))


def test_burn_captions_only_changes_frames_during_cue(tmp_path):
    import numpy as np

    source = tmp_path / "gameplay.mp4"
    srt = tmp_path / "captions.srt"
    make_video(source)
    original_before = frame_rgb(source, 0.5)
    original_during = frame_rgb(source, 2.0)
    srt.write_text(
        "1\n00:00:01,500 --> 00:00:02,500\nA timed caption\n", encoding="utf-8"
    )
    burn_captions(source, srt)
    rendered_before = frame_rgb(source, 0.5)
    rendered_during = frame_rgb(source, 2.0)
    outside_delta = np.abs(original_before.astype(int) - rendered_before.astype(int)).mean()
    inside_delta = np.abs(original_during.astype(int) - rendered_during.astype(int)).mean()
    assert inside_delta > outside_delta + 2.0


def test_burn_captions_rejects_missing_srt(tmp_path):
    source = tmp_path / "source.mp4"
    make_video(source)
    with pytest.raises(CaptionError, match="SRT"):
        burn_captions(source, tmp_path / "missing.srt")


def test_cancelled_burn_leaves_source_intact_and_no_partial(tmp_path):
    source = tmp_path / "source.mp4"
    srt = tmp_path / "captions.srt"
    make_video(source)
    srt.write_text("1\n00:00:00,000 --> 00:00:03,000\nCaption\n", encoding="utf-8")
    original_hash = frame_hash(source)
    event = threading.Event()
    event.set()
    with pytest.raises(CaptionError, match="cancelled"):
        burn_captions(source, srt, cancel_event=event)
    assert frame_hash(source) == original_hash
    assert not list(tmp_path.glob("*.part.mp4"))


def test_running_render_process_is_killed_when_cancelled(tmp_path):
    event = threading.Event()
    threading.Timer(0.2, event.set).start()
    started = time.monotonic()
    with pytest.raises(CaptionError, match="cancelled"):
        _wait_for_process(
            [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, event
        )
    assert time.monotonic() - started < 3
