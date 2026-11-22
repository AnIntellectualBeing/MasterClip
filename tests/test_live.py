import subprocess
import time

import pytest

from clipstudio.live import LiveRecorder, cut_window, finish_recording, shift
from clipstudio.media import find_tool, probe_media


def test_shift_moves_segments_and_words():
    segs = [{"start": 1.0, "end": 2.5, "text": "hi", "words": [{"start": 1.0, "end": 1.4, "word": "hi"}]}]
    out = shift(segs, 300, end_cap=302)
    assert out[0]["start"] == 301 and out[0]["end"] == 302 and out[0]["words"][0]["start"] == 301
    assert segs[0]["start"] == 1.0  # original untouched


def test_written_reads_progress(tmp_path):
    rec = LiveRecorder(["-i", "x"], "0:a:0?", tmp_path)
    assert rec.written() == 0
    rec.progress_file.write_text("out_time_us=4000000\nprogress=continue\nout_time_us=9500000\n")
    assert rec.written() == pytest.approx(9.5)  # not running, so no safety margin


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    path = tmp_path_factory.mktemp("live") / "sample.mp4"
    subprocess.run([
        find_tool("ffmpeg"), "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
    ], check=True)
    return path


def test_record_stop_cut_and_finish(tmp_path, sample):
    rec = LiveRecorder.from_file(sample, tmp_path / "job")
    rec.start()
    deadline = time.monotonic() + 30
    while rec.written() < 3 and time.monotonic() < deadline:
        time.sleep(0.3)
    assert rec.running and rec.written() >= 3
    window = cut_window(rec.target, 0, 3, tmp_path / "w.mp4")  # read while still recording
    assert 2.5 < probe_media(window).duration < 3.6
    rec.stop()
    assert not rec.running
    total = rec.written()
    assert 3 <= total < 12
    mp4 = finish_recording(rec.target)
    info = probe_media(mp4)
    assert info.has_audio and abs(info.duration - total) < 1.5
