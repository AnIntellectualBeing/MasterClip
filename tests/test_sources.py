import threading

import pytest

from clipstudio.media import MediaError
from clipstudio.sources import choose_live_inputs, import_url, validate_url


def fmt(
    url,
    *,
    protocol="https",
    height=None,
    vcodec="none",
    acodec="none",
    tbr=0,
    headers=None,
):
    return {
        "url": url,
        "protocol": protocol,
        "height": height,
        "vcodec": vcodec,
        "acodec": acodec,
        "tbr": tbr,
        "http_headers": headers or {},
    }


def test_choose_live_inputs_prefers_best_supported_combined_below_1080():
    metadata = {
        "formats": [
            fmt(
                "https://cdn.test/1440",
                height=1440,
                vcodec="h264",
                acodec="aac",
                tbr=9000,
            ),
            fmt(
                "https://cdn.test/720",
                height=720,
                vcodec="h264",
                acodec="aac",
                tbr=3000,
            ),
            fmt(
                "https://cdn.test/1080",
                height=1080,
                vcodec="h264",
                acodec="aac",
                tbr=6000,
            ),
        ]
    }
    assert choose_live_inputs(metadata) == ("https://cdn.test/1080", None)


def test_choose_live_inputs_uses_separate_video_and_audio_if_no_combined_at_limit():
    metadata = {
        "formats": [
            fmt(
                "https://cdn.test/1440",
                height=1440,
                vcodec="h264",
                acodec="aac",
                tbr=9000,
            ),
            fmt(
                "https://cdn.test/video",
                protocol="m3u8_native",
                height=1080,
                vcodec="h264",
                tbr=6000,
            ),
            fmt("https://cdn.test/audio", protocol="https", acodec="aac", tbr=128),
        ]
    }
    assert choose_live_inputs(metadata) == (
        "https://cdn.test/video",
        "https://cdn.test/audio",
    )


def test_choose_live_inputs_rejects_missing_audio_or_video_and_unsupported_protocol():
    metadata = {
        "formats": [
            fmt(
                "https://cdn.test/video",
                height=720,
                vcodec="h264",
                protocol="http_dash_segments",
            )
        ]
    }
    with pytest.raises(MediaError, match="video and audio"):
        choose_live_inputs(metadata)


def test_choose_live_inputs_rejects_non_http_format_urls():
    metadata = {
        "formats": [fmt("file:///tmp/secret", height=720, vcodec="h264", acodec="aac")]
    }
    with pytest.raises(MediaError, match="supported"):
        choose_live_inputs(metadata)


def test_import_url_cancellation_happens_before_url_validation(monkeypatch, tmp_path):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(
        "clipstudio.sources.validate_url",
        lambda _url: pytest.fail("network validation should not run"),
    )
    with pytest.raises(MediaError, match="Cancelled"):
        import_url("https://example.test/live", tmp_path, 30, event, lambda *_: None)


def test_validate_url_rejects_localhost_without_external_network(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(ValueError, match="private network"):
        validate_url("https://localhost/live")


def test_import_url_records_separate_streams_with_timeout_headers_and_maps(
    monkeypatch, tmp_path
):
    from clipstudio import sources

    metadata = {
        "title": "Live test",
        "is_live": True,
        "http_headers": {
            "User-Agent": "TestAgent/1",
            "Referer": "https://site.test/live",
            "Cookie": "secret",
        },
        "formats": [
            fmt(
                "https://cdn.test/video",
                protocol="m3u8_native",
                height=1080,
                vcodec="h264",
                headers={"User-Agent": "VideoAgent/2"},
            ),
            fmt("https://cdn.test/audio", acodec="aac"),
        ],
    }
    monkeypatch.setattr(sources, "validate_url", lambda url: url)
    monkeypatch.setattr(sources, "fetch_metadata", lambda *args, **kwargs: metadata)
    commands = []

    def fake_run(command, log_path, cancel_event, timeout=7200):
        commands.append((command, timeout))
        (tmp_path / "source.mp4").write_bytes(b"synthetic")

    monkeypatch.setattr(sources, "run_process", fake_run)
    target, title = sources.import_url(
        "https://source.test/live", tmp_path, 60, threading.Event(), lambda *_: None
    )
    command, timeout = commands[0]
    assert target.name == "source.mp4" and title == "Live test"
    assert timeout == 240
    assert command.count("-rw_timeout") == 2
    assert "0:v:0" in command and "1:a:0" in command
    headers_index = command.index("-headers")
    assert "User-Agent: VideoAgent/2" in command[headers_index + 1]
    assert "Referer: https://site.test/live" in command[headers_index + 1]
    assert "secret" not in " ".join(command)


def test_capture_local_separate_hls_inputs_is_bounded_and_muxes_audio(
    monkeypatch, tmp_path
):
    import functools
    import subprocess
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from clipstudio import sources
    from clipstudio.media import probe_media

    video_playlist = tmp_path / "video.m3u8"
    audio_playlist = tmp_path / "audio.m3u8"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=10",
            "-t",
            "6",
            "-an",
            "-c:v",
            "libx264",
            "-g",
            "10",
            "-sc_threshold",
            "0",
            "-hls_time",
            "1",
            "-hls_list_size",
            "0",
            "-f",
            "hls",
            str(video_playlist),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "6",
            "-vn",
            "-c:a",
            "aac",
            "-hls_time",
            "1",
            "-hls_list_size",
            "0",
            "-f",
            "hls",
            str(audio_playlist),
        ],
        check=True,
        capture_output=True,
    )

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        video_url, audio_url = (
            f"http://127.0.0.1:{port}/video.m3u8",
            f"http://127.0.0.1:{port}/audio.m3u8",
        )
        metadata = {
            "formats": [
                fmt(video_url, protocol="m3u8_native", height=180, vcodec="h264"),
                fmt(audio_url, protocol="m3u8_native", acodec="aac"),
            ]
        }
        output = sources._capture_live(
            video_url, audio_url, metadata, tmp_path / "capture", 3, threading.Event()
        )
        info = probe_media(output)
        assert info.duration == pytest.approx(3, abs=0.5)
        assert info.has_audio
        assert (info.width, info.height) == (320, 180)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
