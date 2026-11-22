import subprocess
from clipstudio.engine import analyze_media


def test_silent_but_moving_video_still_has_activity_candidates(tmp_path):
    path = tmp_path / "silent-motion.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=5:duration=8",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    result = analyze_media(path)
    assert result.candidates
    assert result.warnings


def test_video_with_no_audio_stream_is_analyzable(tmp_path):
    path = tmp_path / "video-only.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=5:duration=8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    result = analyze_media(path)
    assert result.candidates
    assert any("audio" in w.lower() for w in result.warnings)
