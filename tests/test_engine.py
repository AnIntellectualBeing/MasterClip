from clipstudio.engine import Candidate, select_highlights, score_windows


def test_audio_scoring_ranks_loud_dynamic_windows_above_silence():
    scored = score_windows(
        [0.0, 0.0, 0.8, 0.75, 0.1], [0.0, 0.0, 5.0, 4.0, 1.0], window_seconds=2
    )
    assert scored[0].start == 4
    assert scored[0].score > scored[-1].score
    assert scored[0].reasons


def test_motion_signal_contributes_a_reason_and_score():
    scored = score_windows(
        [0, 0, 0], [0, 0, 0], window_seconds=2, motion_values=[0, 1, 0]
    )
    assert scored[0].start == 2
    assert "visual motion" in scored[0].reasons


def test_selection_limits_duration_and_never_overlaps():
    candidates = [
        Candidate(0, 20, 5, ["loudness"]),
        Candidate(18, 38, 4, ["motion"]),
        Candidate(40, 60, 3, ["transcript"]),
    ]
    chosen = select_highlights(candidates, duration=60, target_seconds=30)
    assert sum(c.end - c.start for c in chosen) <= 30
    ordered = sorted(chosen, key=lambda c: c.start)
    assert all(a.end <= b.start for a, b in zip(ordered, ordered[1:]))


import subprocess
import threading
import sys
import time
import pytest
from clipstudio.media import MediaError, probe_media
from clipstudio.engine import render_compilation, render_short, analyze_media, _run


def make_video(path, silent=False):
    audio = (
        ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        if silent
        else ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    )
    visual = (
        "color=c=black:size=320x180:rate=24"
        if silent
        else "testsrc2=size=320x180:rate=24"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            visual,
            *audio,
            "-t",
            "6",
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


def test_render_compilation_handles_multiple_seeks_and_vertical_center_layout(tmp_path):
    source = tmp_path / "source.mp4"
    make_video(source)
    output = tmp_path / "vertical.mp4"
    selected = [Candidate(0.5, 2.0, 1, ["first"]), Candidate(4.0, 5.5, 0.5, ["second"])]
    actual = render_compilation(
        source, selected, output, vertical=True, layout="center"
    )
    info = probe_media(output)
    assert [c.start for c in actual] == [0.5, 4.0]
    assert info.duration == pytest.approx(3.0, abs=0.25)
    assert (info.width, info.height) == (1080, 1920)


def test_render_cancellation_is_checked_before_launch(tmp_path):
    source = tmp_path / "source.mp4"
    make_video(source)
    event = threading.Event()
    event.set()
    with pytest.raises(MediaError, match="cancelled"):
        render_short(
            source, Candidate(0, 2), tmp_path / "never.mp4", cancel_event=event
        )
    assert not (tmp_path / "never.mp4").exists()


def test_running_subprocess_is_terminated_when_cancelled():
    event = threading.Event()
    threading.Timer(0.2, event.set).start()
    started = time.monotonic()
    with pytest.raises(MediaError, match="cancelled"):
        _run([sys.executable, "-c", "import time; time.sleep(30)"], cancel_event=event)
    assert time.monotonic() - started < 3


def test_blurred_short_can_render_to_relative_output_path(tmp_path, monkeypatch):
    from pathlib import Path

    source = tmp_path / "source.mp4"
    make_video(source)
    monkeypatch.chdir(tmp_path)
    render_short(source, Candidate(0, 1), Path("exports") / "short.mp4")
    info = probe_media(Path("exports") / "short.mp4")
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(1, abs=0.2)


def test_silent_video_analysis_returns_empty_candidates_and_warning(tmp_path):
    source = tmp_path / "silent.mp4"
    make_video(source, silent=True)
    result = analyze_media(source, window_seconds=1)
    assert result.candidates == []
    assert any("silent" in warning.lower() for warning in result.warnings)
