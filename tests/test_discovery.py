import json
import sys
import threading
import time

import pytest

from clipstudio.discovery import (
    DiscoveryError,
    find_completed_streams,
    inspect_completed_stream,
    normalize_ytdlp_archive,
    parse_iso_duration,
    sanitize_topic,
)


def test_topic_is_trimmed_and_bounded():
    assert sanitize_topic("  GTA clips  ") == "GTA clips"
    assert len(sanitize_topic("x" * 200)) == 160
    with pytest.raises(ValueError, match="topic"):
        sanitize_topic(" \n ")


def test_parse_iso_duration_handles_hours_minutes_seconds():
    assert parse_iso_duration("PT1H2M3S") == 3723
    assert parse_iso_duration("PT5M") == 300
    assert parse_iso_duration("P1DT1S") == 86401
    with pytest.raises(ValueError):
        parse_iso_duration("not a duration")


def test_ytdlp_archive_requires_was_live_and_playable_positive_duration():
    base = {
        "id": "a1b2c3d4e5F",
        "title": "Past stream",
        "live_status": "was_live",
        "duration": 600,
        "availability": "public",
        "webpage_url": "https://www.youtube.com/watch?v=a1b2c3d4e5F",
    }
    result = normalize_ytdlp_archive(base)
    assert result["live_status"] == "was_live"
    assert result["duration"] == 600
    assert result["ended_at"] is None
    assert normalize_ytdlp_archive({**base, "live_status": "is_live"}) is None
    assert normalize_ytdlp_archive({**base, "live_status": "is_upcoming"}) is None
    assert normalize_ytdlp_archive({**base, "live_status": "post_live"}) is None
    assert normalize_ytdlp_archive({**base, "live_status": "not_live"}) is None
    assert normalize_ytdlp_archive({**base, "duration": 0}) is None
    assert normalize_ytdlp_archive({**base, "availability": "private"}) is None


def test_upload_timestamp_never_fills_ended_at():
    result = normalize_ytdlp_archive(
        {
            "id": "a1b2c3d4e5F",
            "title": "Past stream",
            "live_status": "was_live",
            "duration": 601,
            "availability": "public",
            "timestamp": 1760000000,
            "upload_date": "20251009",
            "webpage_url": "https://www.youtube.com/watch?v=a1b2c3d4e5F",
        }
    )
    assert result["ended_at"] is None
    assert result["published_at"]


def test_cancelled_discovery_stops_before_search(monkeypatch):
    from clipstudio import discovery

    event = threading.Event()
    event.set()
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setattr(
        discovery,
        "_yt_search",
        lambda *args, **kwargs: pytest.fail("search should not run"),
    )
    with pytest.raises(DiscoveryError, match="Cancelled"):
        find_completed_streams("GTA", cancel_event=event)


def test_ytdlp_search_uses_bounded_livestream_query_and_node(monkeypatch):
    from clipstudio import discovery

    captured = []
    monkeypatch.setattr(
        discovery,
        "_run_json_command",
        lambda command, timeout, cancel_event: (
            captured.append(command) or {"entries": []}
        ),
    )
    discovery._yt_search("GTA V", 12)
    assert "--js-runtimes" in captured[0]
    runtime = captured[0][captured[0].index("--js-runtimes") + 1]
    assert runtime in ("node", "deno") or runtime.startswith("deno:")
    assert captured[0][-1] == "ytsearch12:GTA V livestream"
    assert "--flat-playlist" in captured[0]


def test_ytdlp_subprocess_can_be_cancelled_while_running():
    from clipstudio.discovery import _run_json_command

    event = threading.Event()
    timer = threading.Timer(0.2, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(DiscoveryError, match="Cancelled"):
            _run_json_command(
                [sys.executable, "-c", "import time; time.sleep(30)"], 60, event
            )
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3


def test_no_key_ytdlp_fallback_requires_metadata_was_live_verification(monkeypatch):
    from clipstudio import discovery

    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    entries = [
        {
            "id": "a1b2c3d4e5F",
            "title": "Archive",
            "live_status": "was_live",
            "url": "https://www.youtube.com/watch?v=a1b2c3d4e5F",
        },
        {
            "id": "b1b2c3d4e5F",
            "title": "Live now",
            "live_status": "is_live",
            "url": "https://www.youtube.com/watch?v=b1b2c3d4e5F",
        },
        {
            "id": "c1b2c3d4e5F",
            "title": "Regular upload",
            "live_status": "not_live",
            "url": "https://www.youtube.com/watch?v=c1b2c3d4e5F",
        },
    ]
    monkeypatch.setattr(
        discovery, "_yt_search", lambda *args, **kwargs: {"entries": entries}
    )
    checked = []

    def inspect(url, *args, **kwargs):
        checked.append(url)
        return normalize_ytdlp_archive(
            {
                "id": "a1b2c3d4e5F",
                "title": "Archive verified",
                "live_status": "was_live",
                "duration": 700,
                "availability": "public",
                "webpage_url": url,
            }
        )

    monkeypatch.setattr(discovery, "_yt_metadata", inspect)
    result = find_completed_streams("GTA V", max_results=3)
    assert result["provider"] == "yt-dlp"
    assert [r["title"] for r in result["results"]] == ["Archive verified"]
    assert checked == [entries[0]["url"]]


def test_ytdlp_search_deduplicates_video_ids(monkeypatch):
    from clipstudio import discovery

    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    entry = {
        "id": "a1b2c3d4e5F",
        "title": "Archive",
        "live_status": "was_live",
        "webpage_url": "https://www.youtube.com/watch?v=a1b2c3d4e5F",
    }
    monkeypatch.setattr(
        discovery, "_yt_search", lambda *args, **kwargs: {"entries": [entry, entry]}
    )
    checked = []

    def inspect(url, *args, **kwargs):
        checked.append(url)
        return {
            "id": "a1b2c3d4e5F",
            "title": "Archive",
            "live_status": "was_live",
            "duration": 600,
            "availability": "public",
        }

    monkeypatch.setattr(discovery, "_yt_metadata", inspect)
    result = find_completed_streams("GTA", max_results=2)
    assert len(checked) == 1
    assert len(result["results"]) == 1


def test_api_key_mode_uses_completed_search_and_actual_end_time(monkeypatch):
    from clipstudio import discovery

    monkeypatch.setenv("YOUTUBE_API_KEY", "secret-key")
    calls = []

    def api(endpoint, params, cancel_event=None):
        calls.append((endpoint, dict(params)))
        if endpoint == "search":
            return {
                "items": [
                    {
                        "id": {"videoId": "a1b2c3d4e5F"},
                        "snippet": {
                            "title": "Search title",
                            "channelTitle": "Ch",
                            "publishedAt": "2025-01-01T00:00:00Z",
                        },
                    }
                ]
            }
        return {
            "items": [
                {
                    "id": "a1b2c3d4e5F",
                    "snippet": {
                        "title": "Archive",
                        "channelTitle": "Ch",
                        "publishedAt": "2025-01-01T00:00:00Z",
                        "thumbnails": {},
                    },
                    "contentDetails": {"duration": "PT10M"},
                    "liveStreamingDetails": {"actualEndTime": "2025-01-01T00:10:00Z"},
                    "statistics": {"viewCount": "42"},
                    "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                }
            ]
        }

    monkeypatch.setattr(discovery, "_api_get", api)
    result = find_completed_streams("GTA", max_results=1)
    assert result["provider"] == "youtube-api"
    assert result["results"][0]["ended_at"] == "2025-01-01T00:10:00Z"
    assert calls[0][1]["eventType"] == "completed"
    assert calls[0][1]["type"] == "video"
    assert "secret-key" not in json.dumps(result)
    assert calls[1][1]["id"] == "a1b2c3d4e5F"


def test_inspect_direct_url_restricts_to_youtube_and_requires_was_live(monkeypatch):
    from clipstudio import discovery

    monkeypatch.setattr(
        discovery,
        "_yt_metadata",
        lambda *args, **kwargs: {
            "id": "a1b2c3d4e5F",
            "title": "Regular upload",
            "live_status": "not_live",
            "duration": 300,
            "availability": "public",
        },
    )
    assert inspect_completed_stream("https://youtu.be/a1b2c3d4e5F") is None
    with pytest.raises(ValueError, match="YouTube"):
        inspect_completed_stream("https://example.com/watch?v=a1b2c3d4e5F")


def test_direct_inspection_requires_metadata_id_to_match_selected_video(monkeypatch):
    from clipstudio import discovery

    monkeypatch.setattr(
        discovery,
        "_yt_metadata",
        lambda *args, **kwargs: {
            "id": "b1b2c3d4e5F",
            "title": "Different video",
            "live_status": "was_live",
            "duration": 600,
            "availability": "public",
        },
    )
    assert inspect_completed_stream("https://youtu.be/a1b2c3d4e5F") is None
