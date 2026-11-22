import pytest
from clipstudio.media import MediaError, parse_probe_output


def test_parse_probe_output_uses_video_and_audio_metadata():
    info = parse_probe_output(
        {
            "format": {"duration": "12.5", "format_name": "mov,mp4"},
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "60/1",
                },
                {"codec_type": "audio", "sample_rate": "48000", "channels": 2},
            ],
        }
    )
    assert info.duration == 12.5
    assert (info.width, info.height, info.fps) == (1920, 1080, 60)
    assert info.has_audio is True


def test_parse_probe_output_rejects_media_without_video():
    with pytest.raises(MediaError, match="video stream"):
        parse_probe_output({"format": {"duration": "12"}, "streams": []})


def test_parse_probe_output_rejects_nonpositive_duration():
    with pytest.raises(MediaError, match="duration"):
        parse_probe_output(
            {"format": {"duration": "0"}, "streams": [{"codec_type": "video"}]}
        )


def test_extract_cancelled_before_launch_does_not_write_file(tmp_path):
    import threading
    from clipstudio.media import extract_audio

    event = threading.Event()
    event.set()
    import wave

    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    target = tmp_path / "never.wav"
    with pytest.raises(MediaError, match="cancelled"):
        extract_audio(source, target, cancel_event=event)
    assert not target.exists()


def test_parse_probe_rejects_infinite_duration():
    with pytest.raises(MediaError, match="duration"):
        parse_probe_output(
            {"format": {"duration": "inf"}, "streams": [{"codec_type": "video"}]}
        )
