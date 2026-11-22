import datetime as dt
import importlib
import json
import threading

import pytest
import requests
from fastapi.testclient import TestClient

from clipstudio.zernio import Zernio, ZernioError
from clipstudio.posting import clean_settings, next_free_slots
from tests.test_posting import poster, FakeZernio  # noqa: F401


def video(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake")
    return path


def test_targets_are_separate_and_share_one_clip_time(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    rows = poster.add([{"file": video(tmp_path), "job_id": "j", "index": 0}])
    assert [r["only"] for r in rows] == [["instagram"], ["youtube"]]
    assert rows[0]["when"] == rows[1]["when"]
    assert rows[0]["key"] != rows[1]["key"]


def test_same_clip_upload_is_reused_across_destinations(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    rows = poster.add([{"file": video(tmp_path)}])
    for row in rows:
        poster.send(row["id"])
    assert len([entry for entry in poster.log if entry[0] == "upload"]) == 1


def test_long_format_never_goes_to_instagram(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    rows = poster.add([{"file": video(tmp_path), "kind": "long"}])
    assert [r["only"] for r in rows] == [["youtube"]]
    poster.send(rows[0]["id"])
    post = next(payload for kind, payload in poster.log if kind == "post")
    assert post["accounts"] == {"youtube": "yt1"}
    assert post["short"] is False


def test_vertical_destinations_can_be_selected(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    poster.save_settings({"short_platforms": ["instagram"]})
    rows = poster.add([{"file": video(tmp_path), "kind": "short"}])
    assert [r["only"] for r in rows] == [["instagram"]]


def test_youtube_quota_does_not_block_instagram_or_next_clip(poster, tmp_path):
    poster.set_key("zk_good_key_123456")

    class Limited(FakeZernio):
        def create_post(self, **kw):
            if "youtube" in kw["accounts"]:
                raise ZernioError("YouTube daily upload limit reached", status_code=429, retry_after=3600)
            return super().create_post(**kw)

    poster.client_factory = lambda key: Limited(key, poster.log)
    rows = poster.add([{"file": video(tmp_path), "kind": "short"}], "now")
    for row in reversed(rows):  # hit YouTube first, then prove Instagram still sends
        poster.send(row["id"])
    state = {r["only"][0]: r for r in poster.queue()}
    assert state["youtube"]["status"] == "retrying"
    assert state["youtube"]["retry_at"] > dt.datetime.now(dt.timezone.utc).isoformat()
    assert state["instagram"]["status"] == "scheduled"
    before = len(poster.log)
    poster.send(state["youtube"]["id"])
    assert len(poster.log) == before  # do not hammer a quota before its reset
    more = poster.add([{"file": video(tmp_path), "kind": "short"}], "now")
    for row in more:
        poster.send(row["id"])
    assert len([p for k, p in poster.log if k == "post"]) == 2
    yt = next(r for r in poster.queue() if r["id"] == more[1]["id"])
    assert yt["status"] == "retrying"


def test_retry_keeps_post_identity_and_successful_platform_is_not_resent(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    rows = poster.add([{"file": video(tmp_path)}], "now")
    ig, yt = rows
    poster.send(ig["id"])
    poster._update(yt["id"], status="retrying", retry_at="2000-01-01T00:00:00+00:00")
    poster.send(yt["id"])
    calls = [p for k, p in poster.log if k == "post"]
    assert [p["accounts"] for p in calls] == [{"instagram": "ig1"}, {"youtube": "yt1"}]
    assert calls[1]["idempotency_key"] == yt["key"]


def test_interrupted_upload_resumes_with_the_same_post_identity(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    row = poster.add([{"file": video(tmp_path), "only": ["youtube"]}], "now")[0]
    poster._update(row["id"], status="uploading", media_url="https://cdn/already-uploaded.mp4")
    poster.recover()
    poster.send(row["id"])
    post = next(p for k, p in poster.log if k == "post")
    assert post["idempotency_key"] == row["key"]
    assert post["video_url"] == "https://cdn/already-uploaded.mp4"
    assert not any(k == "upload" for k, _ in poster.log)


def test_cancel_scheduled_post_cancels_remote_before_removing_local(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    row = poster.add([{"file": video(tmp_path), "only": ["youtube"]}], "now")[0]
    poster.send(row["id"])
    calls = []
    client = FakeZernio("zk_good_key_123456", poster.log)
    client.delete_post = calls.append
    poster.client_factory = lambda key: client
    poster.remove(row["id"])
    assert calls == ["post1"]
    assert poster.queue() == []


def test_rate_limit_error_preserves_details_and_retry_after():
    response = requests.Response()
    response.status_code = 429
    response._content = json.dumps({"error": "YouTube upload limit reached", "platform": "youtube"}).encode()
    response.headers["Retry-After"] = "120"
    class Http:
        headers = {}
        def request(self, *a, **kw):
            return response
    with pytest.raises(ZernioError, match="YouTube upload limit") as caught:
        Zernio("test", session=Http())._call("GET", "/test")
    assert caught.value.retry_after == 120
    assert caught.value.status_code == 429
    assert caught.value.platform == "youtube"


def test_youtube_upload_quota_is_not_reported_as_a_bad_zernio_key():
    from clipstudio.zernio import _friendly
    response = requests.Response()
    response.status_code = 403
    response._content = json.dumps({"error": "uploadLimitExceeded: YouTube daily upload limit reached", "platform": "youtube"}).encode()
    assert "uploadLimitExceeded" in _friendly(response)


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_DATA", str(tmp_path))
    import clipstudio.app as module
    importlib.reload(module)
    with TestClient(module.app) as client:
        yield module, client


def test_manual_mixed_format_queue_routes_long_to_youtube(app_client, monkeypatch):
    module, client = app_client
    job = module.store.create("mixed", status="complete", clips=[
        {"index": 0, "kind": "short", "file": "short.mp4"},
        {"index": 1, "kind": "long", "file": "long.mp4"},
    ])
    for name in ("short.mp4", "long.mp4"):
        (module.store.folder(job["id"]) / name).write_bytes(b"fake")
    queued = []
    monkeypatch.setattr(module.poster, "add", lambda items, *a: queued.extend(items) or [])
    assert client.post("/api/post/queue", json={"job_id": job["id"]}).status_code == 200
    assert queued[1]["kind"] == "long"
    assert queued[1]["only"] == ["youtube"]


def test_finished_compilation_auto_posts_only_to_youtube(app_client, monkeypatch):
    module, _ = app_client
    job = module.store.create("Long QA", source_path="qa.mp4", duration=10, candidates=[], recipe={"auto_post": True})
    (module.store.folder(job["id"]) / "transcript.json").write_text("[]")
    module.events[job["id"]] = threading.Event()
    def make_full(source, start, end, segments, output, *args, **kwargs):
        output.write_bytes(b"qa video")
        return {"description": "QA long description"}
    monkeypatch.setattr("clipstudio.longform.make_full", make_full)
    queued = []
    monkeypatch.setattr(module.poster, "add", lambda items, *a: queued.extend(items) or [])
    module.make_long(job["id"], module.LongRequest(kind="full", title="Long QA"))
    assert len(queued) == 1
    assert queued[0]["only"] == ["youtube"]
    assert queued[0]["index"] == "long-0"
    assert queued[0]["caption"] == "QA long description"


def test_auto_post_requires_setup_before_starting(app_client, monkeypatch, tmp_path):
    module, client = app_client
    monkeypatch.setattr(module.pool, "submit", lambda *a: None)
    response = client.post("/api/projects", json={
        "source_type": "path", "source": str(video(tmp_path)), "recipe": {"auto_post": True},
    })
    assert response.status_code == 400
    assert "posting" in response.json()["detail"].lower()


def test_continuous_live_defaults_to_minute_scans(app_client):
    module, _ = app_client
    assert module.live_window_seconds() == 60


def test_hourly_schedule_spreads_clips_and_skips_taken_times():
    settings = clean_settings({"cadence": "hourly", "clips_per_hour": 4, "timezone": "Asia/Kolkata"})
    now = dt.datetime(2026, 11, 22, 6, 30, tzinfo=dt.timezone.utc)  # noon
    times = next_free_slots(settings, ["2026-11-22T12:15"], 3, now)
    assert times == ["2026-11-22T12:30", "2026-11-22T12:45", "2026-11-22T13:00"]
