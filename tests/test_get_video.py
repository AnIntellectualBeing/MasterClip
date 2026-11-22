"""Get video: fast search, friendly errors, and VOD time ranges (tester findings 22 Nov)."""

from fastapi.testclient import TestClient

from clipstudio import app as app_module
from clipstudio import sources
from clipstudio.discovery import normalize_search_entry
from clipstudio.sources import friendly_error, heat_points, suggest_range


def test_friendly_errors_never_tell_buyer_to_download_it_themselves():
    assert "private" in friendly_error("ERROR: [youtube] abc: Private video", "x")
    assert "members" in friendly_error("ERROR: Join this channel to get access", "x")
    assert "internet" in friendly_error("ERROR: Unable to download webpage: timed out", "x")
    assert friendly_error("something odd", "fallback") == "fallback"
    for needle, _ in sources._FRIENDLY:
        assert all("download it yourself" not in message for _, message in sources._FRIENDLY)


def test_heat_points_and_hottest_window_skip_intro_spike():
    meta = {
        "heatmap": [
            {"start_time": i * 60.0, "end_time": (i + 1) * 60.0, "value": v}
            for i, v in enumerate([1.0, 0.1, 0.1, 0.9, 0.95, 0.9, 0.1, 0.1, 0.1, 0.1] * 2)
        ]
    }
    heat = heat_points(meta)
    assert len(heat) == 20
    start, end, why = suggest_range(1200, heat, window=180)
    assert (start, end) == (180.0, 360.0)
    assert why == "most replayed part"


def test_suggest_range_without_heat_and_short_video():
    assert suggest_range(300, [], window=600) == (0.0, 300, "whole video")
    start, end, why = suggest_range(7200, [], window=600)
    assert end - start == 600 and start > 0 and why == "start of the stream"


def test_search_entry_keeps_live_and_vods_and_drops_uploads():
    live = normalize_search_entry(
        {"id": "abcdefghijk", "title": "🔴 LIVE", "live_status": "is_live", "concurrent_view_count": 42}
    )
    assert live["live"] and live["views"] == 42 and live["title"].startswith("🔴")
    vod = normalize_search_entry(
        {"id": "abcdefghijl", "title": "VOD", "live_status": "was_live", "duration": 7200, "view_count": 9}
    )
    assert not vod["live"] and vod["duration"] == 7200 and vod["views"] == 9
    assert normalize_search_entry({"id": "abcdefghijm", "live_status": "not_live", "duration": 60}) is None


def test_section_download_asks_ytdlp_for_only_that_range(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, log, cancel, timeout=7200):
        calls.append(command)
        (tmp_path / "source.mp4").write_bytes(b"x")

    monkeypatch.setattr(sources, "run_process", fake_run)
    monkeypatch.setattr(sources, "validate_url", lambda url: url)
    monkeypatch.setattr(
        sources, "fetch_metadata", lambda *a, **k: {"title": "Stream", "is_live": False}
    )
    target, title = sources.import_url(
        "https://www.youtube.com/watch?v=abcdefghijk",
        tmp_path,
        600,
        None,
        lambda p, m: None,
        section=(430, 460),
    )
    assert title == "Stream" and target.name == "source.mp4"
    command = calls[0]
    assert command[command.index("--download-sections") + 1] == "*430.00-460.00"
    assert "m3u8" in command[command.index("-f") + 1]


def test_project_time_range_is_validated_and_stored(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "store", app_module.JobStore(tmp_path))
    monkeypatch.setattr(app_module.pool, "submit", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "validate_url", lambda url: url)
    client = TestClient(app_module.app)
    base = {"source_type": "url", "source": "https://www.youtube.com/watch?v=abcdefghijk"}
    bad = client.post("/api/projects", json={**base, "section_start": 50, "section_end": 10})
    assert bad.status_code == 400 and "after" in bad.json()["detail"]
    ok = client.post(
        "/api/projects",
        json={**base, "from_discovery": True, "section_start": 10, "section_end": 70},
    )
    assert ok.status_code == 202
    assert ok.json()["section"] == {"start": 10, "end": 70}
