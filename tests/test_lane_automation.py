"""Lane D (1.6): Creators auto-watch, YouTube live chat, phone remote, update check, Report a
problem, covers, 4K availability."""

import importlib
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipstudio import creators as cr
from clipstudio import remote as rm
from clipstudio import updates, ytchat

DATA = Path(__file__).parent / "data"


# ---------------- YouTube live chat ----------------
def test_youtube_chat_parser_reads_saved_sample():
    data = json.loads((DATA / "yt_live_chat_sample.json").read_text(encoding="utf-8"))
    messages, nxt, wait = ytchat.parse_chat_response(data)
    assert nxt == "SAMPLE_NEXT"
    assert wait == 8.0  # YouTube's 10 s is capped so spikes aren't late
    assert len(messages) == 3  # the ticker action is not a message
    assert all(t > 1.7e9 for t, _ in messages)  # unix seconds from timestampUsec
    assert messages[-1][1] == ":face-with-tears-of-joy: LMAO"
    assert any("Tammy" in text for _, text in messages)


def test_youtube_chat_page_and_live_page_parsing():
    html = '..."INNERTUBE_API_KEY":"AIzaX","INNERTUBE_CLIENT_VERSION":"2.2026","continuation":"abc%3D"...'
    assert ytchat.parse_chat_page(html) == {"key": "AIzaX", "version": "2.2026", "continuation": "abc%3D"}
    assert ytchat.parse_chat_page("<html>chat is disabled</html>") is None
    live = '<link rel="canonical" href="https://www.youtube.com/watch?v=4t4AQ15yj1M"> ... "isLiveNow":true'
    assert ytchat.live_video_from_page(live) == "4t4AQ15yj1M"
    upcoming = '<link rel="canonical" href="https://www.youtube.com/watch?v=4t4AQ15yj1M"> "isLiveNow":false'
    assert ytchat.live_video_from_page(upcoming) is None
    channel = '<link rel="canonical" href="https://www.youtube.com/channel/UCabc">'
    assert ytchat.live_video_from_page(channel) is None
    assert ytchat.channel_live("@LofiGirl", fetch=lambda url: live) == "https://www.youtube.com/watch?v=4t4AQ15yj1M"
    assert ytchat.channel_live_url("https://www.youtube.com/@x/") == "https://www.youtube.com/@x/live"


def test_youtube_video_id_from_links():
    assert ytchat.video_id("https://www.youtube.com/watch?v=4t4AQ15yj1M&t=3") == "4t4AQ15yj1M"
    assert ytchat.video_id("https://youtu.be/4t4AQ15yj1M") == "4t4AQ15yj1M"
    assert ytchat.video_id("https://www.youtube.com/live/4t4AQ15yj1M?si=x") == "4t4AQ15yj1M"
    assert ytchat.video_id("https://www.youtube.com/@LofiGirl/live") is None
    assert ytchat.video_id("https://www.twitch.tv/x") is None


def test_youtube_chat_feeds_spike_counter_with_fake_site():
    now_us = int(time.time() * 1_000_000)
    page = '"INNERTUBE_API_KEY":"k","INNERTUBE_CLIENT_VERSION":"2","continuation":"c1"'
    batch = {"continuationContents": {"liveChatContinuation": {
        "continuations": [{"timedContinuationData": {"timeoutMs": 1000, "continuation": "c2"}}],
        "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "message": {"runs": [{"text": f"W {i}"}]}, "timestampUsec": str(now_us)}}}} for i in range(5)]
        + [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {  # from before we joined
            "message": {"runs": [{"text": "old"}]}, "timestampUsec": str(now_us - 600_000_000)}}}}]}}}
    calls = []

    def fetch(url, data=None):
        calls.append(url)
        return page if data is None else json.dumps(batch)

    chat = ytchat.YouTubeChat("4t4AQ15yj1M", fetch=fetch).start()
    for _ in range(50):
        if len(chat.messages) >= 5:
            break
        time.sleep(0.05)
    chat.stop()
    texts = [m for _, m in chat.messages]
    assert texts[:5] == [f"W {i}" for i in range(5)]
    assert "old" not in texts
    assert calls[0].endswith("v=4t4AQ15yj1M") and "get_live_chat?key=k" in calls[1]
    assert chat.meter()["per_min"] >= 5


# ---------------- phone remote ----------------
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_pin_lockout_after_five_wrong_pins():
    clock = Clock()
    gate = rm.PinGate(now=clock, pin="123456")
    for left in (4, 3, 2, 1):
        assert gate.try_pin("000000") == ("wrong", None)
        assert gate.tries_left() == left
    assert gate.try_pin("000000") == ("locked", None)
    assert gate.try_pin("123456") == ("locked", None)  # even the right PIN waits out the lock
    clock.t += rm.LOCK_SECONDS - 1
    assert gate.locked()
    clock.t += 2
    result, token = gate.try_pin("123456")
    assert result == "ok" and gate.valid(token)
    clock.t += rm.SESSION_SECONDS + 1
    assert not gate.valid(token)


def test_new_pin_unlocks_and_signs_everyone_out():
    gate = rm.PinGate(pin="111111")
    _, token = gate.try_pin("111111")
    for _ in range(5):
        gate.try_pin("999999")
    assert gate.locked()
    pin = gate.new_pin()
    assert len(pin) == 6 and pin.isdigit()
    assert not gate.locked() and not gate.valid(token)


class FakeActions:
    def __init__(self, tmp_path):
        self.started = []
        self.clip = tmp_path / "c.mp4"
        self.clip.write_bytes(b"mp4")

    def state(self):
        return {"live": None, "clips": []}

    def start(self, url, length):
        self.started.append((url, length))
        return self.state()

    def clip_now(self):
        raise ValueError("Nothing is recording right now.")

    def stop(self):
        return self.state()

    def clip_file(self, job, index):
        return self.clip


@pytest.fixture
def phone(tmp_path):
    gate = rm.PinGate(pin="246810")
    page = tmp_path / "m.html"
    page.write_text("<h1>remote</h1>", encoding="utf-8")
    actions = FakeActions(tmp_path)
    app = rm.build_app(gate, actions, ["testserver"], page)
    return TestClient(app), gate, actions


def test_phone_remote_needs_pin_and_header(phone):
    client, gate, actions = phone
    assert client.get("/m").status_code == 200
    assert client.get("/m/api/state").status_code == 401
    assert client.post("/m/api/login", json={"pin": "246810"}).status_code == 403  # no page header
    h = {"X-MasterClip": "1"}
    r = client.post("/m/api/login", json={"pin": "000000"}, headers=h)
    assert r.status_code == 401 and "4 tries left" in r.json()["detail"]
    assert client.post("/m/api/login", json={"pin": "246810"}, headers=h).status_code == 200
    assert client.get("/m/api/state").status_code == 200
    assert client.post("/m/api/start", json={"url": "https://twitch.tv/x", "length": 30}).status_code == 403
    assert client.post("/m/api/start", json={"url": "https://twitch.tv/x", "length": 30}, headers=h).status_code == 200
    assert actions.started == [("https://twitch.tv/x", 30)]
    r = client.post("/m/api/clip", headers=h)
    assert r.status_code == 400 and "Nothing is recording" in r.json()["detail"]
    assert client.get("/m/api/clip/abc/0").content == b"mp4"


def test_phone_remote_has_only_its_own_routes(phone):
    client, _, _ = phone
    for path in ("/api/jobs", "/api/post/status", "/api/remote", "/static/app.js", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    assert client.get("/m", headers={"Host": "evil.example"}).status_code == 400


def test_phone_remote_lockout_over_http(phone):
    client, gate, _ = phone
    h = {"X-MasterClip": "1"}
    for _ in range(4):
        client.post("/m/api/login", json={"pin": "1"}, headers=h)
    assert client.post("/m/api/login", json={"pin": "1"}, headers=h).status_code == 429
    assert client.post("/m/api/login", json={"pin": "246810"}, headers=h).status_code == 429


def test_lan_address_is_private_or_none():
    import ipaddress

    ip = rm.lan_address()
    assert ip is None or ipaddress.ip_address(ip).is_private


# ---------------- update check ----------------
def test_update_version_compare():
    assert updates.is_newer("v1.6.0", "1.5.0")
    assert updates.is_newer("1.10.0", "1.9.9")
    assert not updates.is_newer("v1.5.0", "1.5.0")
    assert not updates.is_newer("1.4.9", "1.5.0")
    assert not updates.is_newer("nightly", "1.5.0")
    assert updates.parse_version("MasterClip 1.6") == (1, 6, 0)


def test_update_check_asks_github_once_a_day(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPSTUDIO_NO_UPDATE_CHECK", raising=False)
    clock = Clock()
    calls = []

    def fetch():
        calls.append(1)
        return {"tag_name": "v1.6.0", "html_url": "https://github.com/AnIntellectualBeing/MasterClip/releases/tag/v1.6.0", "body": "new"}

    first = updates.check(tmp_path, "1.5.0", fetch=fetch, now=clock)
    assert first["available"] and first["latest"] == "1.6.0" and first["url"].endswith("v1.6.0")
    clock.t += 3600
    updates.check(tmp_path, "1.5.0", fetch=fetch, now=clock)
    assert len(calls) == 1  # cached
    clock.t += updates.EVERY
    updates.check(tmp_path, "1.5.0", fetch=fetch, now=clock)
    assert len(calls) == 2
    assert not updates.check(tmp_path, "1.6.0", fetch=fetch, now=clock)["available"]


def test_update_check_offline_and_foreign_links(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPSTUDIO_NO_UPDATE_CHECK", raising=False)

    def offline():
        raise OSError("no internet")

    out = updates.check(tmp_path, "1.5.0", fetch=offline, now=Clock())
    assert out["available"] is False
    evil = updates.check(tmp_path / "b", "1.5.0", fetch=lambda: {"tag_name": "9.0", "html_url": "https://evil.example/x"}, now=Clock())
    assert evil["url"] == updates.PAGE


# ---------------- creators ----------------
def test_parse_creator_names_and_links():
    assert cr.parse_creator("shroud")["url"] == "https://www.twitch.tv/shroud"
    assert cr.parse_creator("https://www.twitch.tv/Kai_Cenat")["key"] == "kai_cenat"
    yt = cr.parse_creator("youtube.com/@LofiGirl")
    assert yt["kind"] == "youtube" and yt["url"] == "https://www.youtube.com/@LofiGirl"
    assert cr.parse_creator("@LofiGirl")["kind"] == "youtube"
    assert cr.parse_creator("https://www.youtube.com/channel/UCSJ4gkVC6NrvII8umztf0Ow")["kind"] == "youtube"
    with pytest.raises(ValueError, match="channel link"):
        cr.parse_creator("https://www.youtube.com/watch?v=4t4AQ15yj1M")
    with pytest.raises(ValueError):
        cr.parse_creator("not a channel!")


class World:
    """Fake network + jobs for the watcher."""

    def __init__(self, tmp_path):
        self.store = cr.Creators(tmp_path)
        self.clock = Clock()
        self.live = set()
        self.running = set()
        self.started, self.stopped, self.deleted = [], [], []
        self.free = 50 * 1024**3
        self.fail_start = False
        self.checks = 0

    def check_live(self, due):
        self.checks += 1
        return {c["id"]: c["url"] for c in due if c["key"] in self.live}

    def start(self, creator, url):
        if self.fail_start:
            raise RuntimeError("Your free videos are used up.")
        job = f"job{len(self.started)}"
        self.started.append((creator["key"], job))
        self.running.add(job)
        return job

    def stop(self, job):
        self.stopped.append(job)
        self.running.discard(job)

    def watcher(self):
        return cr.Watcher(self.store, check_live=self.check_live, start_capture=self.start,
                          capture_running=lambda j: j in self.running, stop_capture=self.stop,
                          delete_recording=self.deleted.append, free_bytes=lambda: self.free,
                          min_free=2 * 1024**3, now=self.clock)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    w.a = w.store.add("alpha", {"length": 30})
    w.b = w.store.add("bravo", {"length": 30})
    return w


def test_watcher_records_one_stream_at_a_time(world):
    watch = world.watcher()
    watch.tick()
    assert world.started == []  # nobody live
    world.live = {"alpha", "bravo"}
    watch.tick()
    assert world.started == [("alpha", "job0")]
    world.clock.t += 60
    watch.tick()
    assert len(world.started) == 1  # bravo waits its turn
    assert world.store.get(world.a["id"])["jobs"] == ["job0"]


def test_watcher_three_hour_cap_and_recording_deleted_after_clips(world):
    watch = world.watcher()
    world.live = {"alpha", "bravo"}
    watch.tick()
    world.clock.t += cr.CAP_SECONDS
    watch.tick()
    assert world.stopped == ["job0"]
    world.clock.t += 60
    watch.tick()  # the capture finished: recording deleted, next turn goes to bravo
    assert world.deleted == ["job0"]
    assert world.started[-1] == ("bravo", "job1")


def test_watcher_disk_guard(world):
    watch = world.watcher()
    world.live = {"alpha"}
    world.free = 1 * 1024**3
    watch.tick()
    assert world.started == [] and world.checks == 0  # not even checked
    assert "almost full" in watch.note
    world.free = 50 * 1024**3
    watch.tick()
    assert world.started == [("alpha", "job0")]
    world.free = 1 * 1024**3
    watch.tick()
    assert world.stopped == ["job0"]


def test_stop_pauses_creator_for_30_minutes(world):
    watch = world.watcher()
    world.live = {"alpha"}
    watch.tick()
    watch.stop(world.a["id"])
    assert world.stopped == ["job0"]
    world.clock.t += 60
    watch.tick()
    assert len(world.started) == 1 and world.deleted == ["job0"]
    world.clock.t += cr.PAUSE_SECONDS
    watch.tick()
    assert world.started[-1] == ("alpha", "job1")


def test_failed_start_pauses_with_a_note_and_off_creators_are_skipped(world):
    watch = world.watcher()
    world.store.update(world.b["id"], on=False)
    world.live = {"alpha", "bravo"}
    world.fail_start = True
    watch.tick()
    a = world.store.get(world.a["id"])
    assert "free videos" in a["note"] and a["paused_until"] > world.clock.t
    assert world.started == []


def test_creators_list_rejects_duplicates(tmp_path):
    store = cr.Creators(tmp_path)
    store.add("shroud", {})
    with pytest.raises(ValueError, match="already"):
        store.add("https://twitch.tv/SHROUD", {})


def test_twitch_live_logins_batched():
    seen = {}

    def gql(query, variables):
        seen.update(variables)
        return {"users": [{"login": "aa", "stream": {"id": "1"}}, {"login": "bb", "stream": None}]}

    from clipstudio.twitch import live_logins

    assert live_logins(["AA", "bb", "bad name!"], gql=gql) == {"aa"}
    assert seen == {"logins": ["aa", "bb"]}


# ---------------- 4K availability ----------------
def test_max_height_reports_4k_only_when_offered():
    from clipstudio.sources import max_height

    assert max_height({"formats": [{"width": 1920, "height": 1080}, {"width": 3840, "height": 2160}]}) == 2160
    assert max_height({"formats": [{"width": 1080, "height": 1920}]}) == 1080  # vertical 1080p
    assert max_height({"formats": [{"vcodec": "none", "height": 4320}, {"height": 720}]}) == 720
    assert max_height({}) is None


# ---------------- cover ----------------
def test_wrap_title_keeps_lines_inside_width():
    from clipstudio.cover import wrap_title

    measure = lambda s: len(s) * 0.5  # noqa: E731
    lines = wrap_title("he did not see that coming in the final chase at all today", measure, 8)
    assert len(lines) == 3 and lines[-1].endswith("…")
    assert all(measure(line) <= 8 or " " not in line for line in lines[:-1])
    assert wrap_title("", measure, 8) == []


def test_cover_image_is_made_next_to_the_clip(tmp_path):
    from clipstudio.cover import cover_path, make_cover
    from clipstudio.media import find_tool, probe_media

    clip = tmp_path / "clip.mp4"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=10:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip)], check=True)
    out = make_cover(clip, "Cops lose him in 10 seconds")
    assert out == cover_path(clip) and out.stat().st_size > 5000
    info = probe_media(out)
    assert (info.width, info.height) == (540, 960)


def test_posting_sends_cover_as_thumbnail(tmp_path, monkeypatch):
    from clipstudio import posting
    from tests.test_posting import FakeVault, FakeZernio

    class CoverZernio(FakeZernio):
        def upload(self, path, content_type="video/mp4"):
            self.log.append(("upload", path.name, content_type))
            return "https://cdn/cover.jpg" if content_type == "image/jpeg" else "https://cdn/video.mp4"

    fake = FakeVault()
    monkeypatch.setattr(posting.vault, "get", fake.get)
    monkeypatch.setattr(posting.vault, "put", fake.put)
    monkeypatch.setattr(posting.vault, "delete", fake.delete)
    log = []
    p = posting.Poster(tmp_path, client_factory=lambda key: CoverZernio(key, log))
    p.kick = lambda: None
    p.set_key("zk_good_key_123456")
    p.save_settings({"short_platforms": ["youtube"]})
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    cover = tmp_path / "v.jpg"
    cover.write_bytes(b"jpg")
    rows = p.add([{"file": video, "cover": cover, "kind": "short"}])
    p.send(rows[0]["id"])
    post = next(payload for kind, payload, *_ in log if kind == "post")
    assert post["thumbnail"] == "https://cdn/cover.jpg"
    assert ("upload", f"{rows[0]['id']}-cover.jpg", "image/jpeg") in log


# ---------------- app routes ----------------
@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_DATA", str(tmp_path))
    monkeypatch.setenv("CLIPSTUDIO_LOGS", str(tmp_path))
    monkeypatch.setenv("CLIPSTUDIO_NO_WATCH", "1")
    monkeypatch.setenv("CLIPSTUDIO_NO_UPDATE_CHECK", "1")
    import clipstudio.app as module
    import clipstudio.automation_api as auto

    importlib.reload(module)
    auto._state.clear()
    with TestClient(module.app) as client:
        yield module, client
    auto._state.clear()


def test_creators_routes_snapshot_style(app_client):
    module, client = app_client
    r = client.post("/api/creators", json={"text": "shroud", "template": "big_captions", "length": 45})
    assert r.status_code == 201, r.text
    c = r.json()["creators"][0]
    assert c["name"] == "shroud" and c["length"] == 45 and c["style"]
    assert "Watching" in c["status"]
    assert client.post("/api/creators", json={"text": "shroud"}).status_code == 400
    assert client.post("/api/creators", json={"text": "x y z"}).status_code == 400
    assert client.post("/api/creators", json={"text": "bravo", "template": "nope"}).status_code == 400
    stopped = client.post(f"/api/creators/{c['id']}/stop").json()["creators"][0]
    assert stopped["paused"] and stopped["status"].startswith("Paused until")
    assert client.delete(f"/api/creators/{c['id']}").json()["creators"] == []


def test_report_update_and_remote_routes(app_client, tmp_path):
    module, client = app_client
    (tmp_path / "app.log").write_text(
        "ok line\nfetch https://cdn.example/v.m3u8?sig=SECRET&exp=1 failed\napi_key=sk-123 sent to me@mail.com\n", encoding="utf-8")
    report = client.get("/api/report").json()
    assert "SECRET" not in report["logs"] and "sk-123" not in report["logs"] and "me@mail.com" not in report["logs"]
    assert "ok line" in report["logs"] and report["email"].endswith("@gmail.com")
    up = client.get("/api/update-check").json()
    assert up["available"] is False and up["current"]
    assert client.get("/api/remote").json() == {"on": False, "url": None, "pin": None, "locked": False, "locked_until": None}


def test_cover_route_404_for_missing_clip(app_client):
    module, client = app_client
    job = module.store.create("x", status="complete", clips=[])
    assert client.get(f"/api/jobs/{job['id']}/clips/0/cover").status_code == 404


def test_live_job_starter_is_shared(app_client, monkeypatch):
    module, client = app_client
    started = []
    monkeypatch.setattr(module, "run_live", lambda *a: started.append(a))
    monkeypatch.setattr(module, "validate_url", lambda url: url)
    job = module.start_live_job(module.SourceRequest(source_type="url", source="https://www.twitch.tv/x"),
                                None, True, "vertical", extra={"creator": "c1"})
    for _ in range(20):
        if started:
            break
        time.sleep(0.05)
    assert job["creator"] == "c1" and job["live"]["state"] == "starting"
    assert started and job["id"] in module.live_stops
