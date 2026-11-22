"""Live Twitch chat during a live capture: reading, spikes, hype meter, and the capture loop
clipping a chat spike or a Clip now press right away instead of waiting for the 5-minute stretch."""

import importlib
import socket
import threading
import types

import pytest

from clipstudio import livechat
from clipstudio.livechat import LiveChat, channel_login, hype_percent, is_spike


def test_channel_login_only_for_live_channel_links():
    assert channel_login("https://www.twitch.tv/SomeStreamer") == "somestreamer"
    assert channel_login("twitch.tv/some_streamer?ref=x") == "some_streamer"
    assert channel_login("https://www.twitch.tv/videos/2930713449") is None
    assert channel_login("https://www.twitch.tv/someone/clip/FunnyClip") is None
    assert channel_login("https://www.youtube.com/watch?v=abc") is None


def test_spike_needs_history_volume_and_a_real_jump():
    calm = [4, 5, 3, 6, 4, 5, 4, 5]
    assert is_spike(30, calm)
    assert not is_spike(9, calm)            # a little busier is not a spike
    assert not is_spike(30, [0, 0, 0, 5])   # not enough chat history yet
    assert not is_spike(6, [1, 1, 1, 1, 1, 1, 1])  # too few messages to mean anything


def test_hype_meter_is_relative_to_this_chat():
    assert hype_percent(15, 5, 20) == 0      # 15 in 30 s is this chat's normal speed
    assert hype_percent(60, 5, 20) == 100    # four times normal
    assert 0 < hype_percent(30, 5, 20) < 100
    assert hype_percent(30, 0, 2) == 50      # no history yet: absolute scale


class FakeSocket:
    def __init__(self, lines):
        self.chunks = [("\r\n".join(lines) + "\r\n").encode()]
        self.sent = b""

    def settimeout(self, _):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, _):
        if self.chunks:
            return self.chunks.pop(0)
        raise socket.timeout()

    def close(self):
        pass


def test_reads_messages_answers_ping_and_never_sends_chat():
    fake = FakeSocket([
        ":tmi.twitch.tv 001 justinfan123 :Welcome",
        "PING :tmi.twitch.tv",
        ":a!a@a.tmi.twitch.tv PRIVMSG #streamer :KEKW KEKW",
        "@badge-info=;color=#FF0000 :b!b@b.tmi.twitch.tv PRIVMSG #streamer :clip it",
    ])
    chat = LiveChat("Streamer", connect=lambda: fake).start()
    for _ in range(200):
        if len(chat.messages) == 2:
            break
        threading.Event().wait(0.01)
    chat.stop()
    assert [m for _, m in chat.messages] == ["KEKW KEKW", "clip it"]
    assert b"JOIN #streamer" in fake.sent and b"PONG :tmi.twitch.tv" in fake.sent
    assert b"PRIVMSG" not in fake.sent  # read only
    assert chat.status == "reading"


def test_spike_found_even_if_buckets_passed_while_clipping(monkeypatch):
    clock = {"t": 0.0}
    chat = LiveChat("x", clock_start=0.0)
    monkeypatch.setattr(chat, "now", lambda: clock["t"])
    for second in range(0, 300):
        chat.add("hi", at=second + 0.5) if second % 2 == 0 else None
    for i in range(40):
        chat.add("OMEGALUL", at=300 + i * 0.2)  # burst in the bucket 300-310
    clock["t"] = 400  # the loop was busy rendering for 90 s
    assert chat.new_spike() == 305
    assert chat.new_spike() is None
    assert chat.meter()["status"] == "connecting"
    assert [m for _, m in chat.between(300, 302)][:1] == ["OMEGALUL"]
    assert chat.between(300, 302, delay=0)[0][0] == 0.0


# ---------------- the capture loop ----------------
class FakeRecorder:
    def __init__(self, clock, folder, stop_at):
        self.clock, self.stop_at = clock, stop_at
        self.started = 0.0
        self.target = folder / "source.ts"
        self.progress_file = folder / "record-progress.txt"
        self.stopped = False

    def start(self):
        pass

    @property
    def running(self):
        return not self.stopped and self.clock["t"] < self.stop_at

    def written(self):
        # like ffmpeg: the first frame lands 2 s after start, and 2 s are held back while running
        seen = min(self.clock["t"], self.stop_at) - 2
        return max(0.0, seen - (2 if self.running else 0))

    def stop(self, wait=20):
        self.stopped = True

    def close(self):
        pass


class FakeChat:
    def __init__(self, clock, spikes):
        self.clock, self.spikes = clock, list(spikes)

    def now(self):
        return self.clock["t"]  # seconds since the recorder process started

    def new_spike(self):
        if self.spikes and self.clock["t"] >= self.spikes[0] + 10:
            return self.spikes.pop(0)
        return None

    def meter(self):
        return {"hype": 40, "label": "building", "per_min": 30, "status": "reading"}

    def between(self, start, end, delay=0.0):
        return [(1.0, f"chat {start:.0f}-{end:.0f}")]

    def stop(self):
        pass


@pytest.fixture
def live_app(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_DATA", str(tmp_path))
    import clipstudio.app as module

    importlib.reload(module)
    return module


def run_capture(module, monkeypatch, tmp_path, *, spikes=(), clip_now_at=None, stop_at=700, window=300):
    clock = {"t": 0.0}
    job = module.store.create("Live stream", source_path=None, options={}, recipe={"count": 3, "length": 30},
                              live=dict(state="starting", recorded=0, windows=0, window=window))
    ident = job["id"]
    module.events[ident] = threading.Event()
    module.live_stops[ident] = threading.Event()
    folder = module.store.folder(ident)
    recorder = FakeRecorder(clock, folder, stop_at)
    windows = []

    def fake_window(ident_, source, start, end, number, transcript, found, clips, recipe, captions, options,
                    chat=None, count=None, clip_now=False):
        windows.append(dict(start=start, end=end, count=count, clip_now=clip_now, chat=chat))
        return 0

    def tick(_):
        clock["t"] += 1
        if clip_now_at is not None and clock["t"] == clip_now_at:
            module.live_clip_now.add(ident)

    monkeypatch.setattr(module, "_live_recorder", lambda *a: (recorder, "Title", "Streamer"))
    monkeypatch.setattr(module, "_live_chat", lambda url, start: FakeChat(clock, spikes))
    monkeypatch.setattr(module, "_live_window", fake_window)
    monkeypatch.setattr(module.time, "sleep", tick)
    monkeypatch.setattr("clipstudio.live.finish_recording", lambda p: p)
    monkeypatch.setattr(module, "probe_media", lambda p: types.SimpleNamespace(duration=stop_at))
    monkeypatch.setattr(module, "asdict", lambda x: {})
    module.run_live(ident, types.SimpleNamespace(source="https://www.twitch.tv/streamer"), True)
    return windows, module.store.get(ident)


def test_capture_without_spikes_clips_every_window(live_app, monkeypatch, tmp_path):
    windows, job = run_capture(live_app, monkeypatch, tmp_path)
    assert [(w["start"], w["end"]) for w in windows] == [(0, 300), (300, 600), (600, 698)]
    assert all(w["count"] is None and not w["clip_now"] for w in windows)
    assert windows[0]["chat"] == [(1.0, "chat 0-300")]
    assert job["live"]["hype"] == 40 and job["live"]["chat"] == "reading"


def test_minute_scans_keep_running_beyond_the_requested_clip_count(live_app, monkeypatch, tmp_path):
    windows, job = run_capture(live_app, monkeypatch, tmp_path, stop_at=330, window=60)
    assert len(windows) >= 5  # count=3 never ends the live session
    assert windows[0]["end"] == 60
    assert windows[4]["end"] == 300


def test_chat_spike_closes_the_stretch_early(live_app, monkeypatch, tmp_path):
    # spike at chat time 122 = recording time 120: clip [0, 132) right away
    windows, _ = run_capture(live_app, monkeypatch, tmp_path, spikes=[122])
    assert windows[0]["start"] == 0 and windows[0]["end"] == pytest.approx(132)
    assert windows[0]["count"] == 1
    assert windows[1]["start"] == pytest.approx(132) and windows[1]["end"] == pytest.approx(432)


def test_clip_now_cuts_the_last_seconds(live_app, monkeypatch, tmp_path):
    windows, job = run_capture(live_app, monkeypatch, tmp_path, clip_now_at=90)
    assert windows[0]["clip_now"] and windows[0]["end"] == pytest.approx(86)
    assert not job["live"].get("clip_now")


def test_clip_now_endpoint_only_while_recording(live_app):
    from fastapi.testclient import TestClient

    module = live_app
    job = module.store.create("Live stream", source_path=None, live=dict(state="recording"))
    with TestClient(module.app) as client:
        assert client.post(f"/api/jobs/{job['id']}/live/clip").status_code == 409
        module.live_stops[job["id"]] = threading.Event()
        assert client.post(f"/api/jobs/{job['id']}/live/clip").status_code == 200
        assert job["id"] in module.live_clip_now
        module.live_stops.pop(job["id"])
        module.live_clip_now.discard(job["id"])


def test_constants_are_sane():
    assert livechat.BUCKET * livechat.HISTORY == 300 and livechat.COOLDOWN >= livechat.BUCKET


def test_live_clips_are_saved_to_the_pc_as_they_are_made(live_app, monkeypatch, tmp_path):
    out = tmp_path / "Videos"
    monkeypatch.setattr(live_app, "output_folder", lambda: out)
    job = live_app.store.create("Live stream", source_path=None, options={}, recipe={})
    clip_file = tmp_path / "live-001-01.mp4"
    clip_file.write_bytes(b"video")
    clip = dict(index=0, moment=0, kind="short", title="Cops: No no no!")
    saved = live_app._autosave_live(job["id"], {"title": "GTA RP live"}, clip_file, clip, False)
    assert saved == str(out / "GTA RP live")
    assert (out / "GTA RP live" / "01 Cops No no no.mp4").read_bytes() == b"video"
    # a copy that fails (disk full, folder gone) never stops the recording
    assert live_app._autosave_live(job["id"], {}, tmp_path / "missing.mp4", clip, False) is None
