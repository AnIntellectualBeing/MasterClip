import datetime as dt

import pytest

from clipstudio import posting, vault
from clipstudio.posting import Poster, clean_settings, next_free_slots, render_caption
from clipstudio.zernio import ZernioError


class FakeVault:
    def __init__(self):
        self.data = {}

    def get(self, name):
        return self.data.get(name)

    def put(self, name, value):
        self.data[name] = value

    def delete(self, name):
        self.data.pop(name, None)


class FakeZernio:
    def __init__(self, key, log):
        if key != "zk_good_key_123456":
            raise ZernioError("Zernio didn't accept that key.")
        self.log = log

    def accounts(self):
        return [
            {"id": "ig1", "platform": "instagram", "username": "me_ig", "profile_id": "p1", "active": True},
            {"id": "yt1", "platform": "youtube", "username": "me_yt", "profile_id": "p1", "active": True},
            {"id": "x1", "platform": "twitter", "username": "me_x", "profile_id": "p1", "active": True},
        ]

    def default_profile(self):
        return "p1"

    def connect_url(self, platform, profile, redirect):
        return f"https://auth/{platform}?p={profile}&r={redirect}"

    def upload(self, path):
        self.log.append(("upload", path.name))
        if self.log and getattr(self, "fail_upload", False):
            raise ZernioError("upload broke")
        return "https://cdn/video.mp4"

    def create_post(self, **kw):
        self.log.append(("post", kw))
        return {"_id": "post1", "status": "scheduled", "platforms": []}

    def get_post(self, post_id):
        return {"_id": post_id, "status": "published", "platforms": [
            {"platform": "instagram", "status": "published", "platformPostUrl": "https://ig/p/1", "platformPostId": "1789"},
            {"platform": "youtube", "status": "published", "platformPostUrl": "https://yt/1"},
        ]}

    def retry(self, post_id):
        self.log.append(("retry", post_id))
        return {"_id": post_id, "status": "published", "platforms": []}

    def comment_dm(self, **kw):
        self.log.append(("dm", kw))
        return {"ok": True}


@pytest.fixture
def poster(tmp_path, monkeypatch):
    fake = FakeVault()
    monkeypatch.setattr(posting.vault, "get", fake.get)
    monkeypatch.setattr(posting.vault, "put", fake.put)
    monkeypatch.setattr(posting.vault, "delete", fake.delete)
    log = []
    p = Poster(tmp_path, client_factory=lambda key: FakeZernio(key, log))
    p.kick = lambda: None  # tests drive send() themselves
    p.log = log
    p.fake_vault = fake
    return p


def test_slots_skip_past_and_taken():
    s = clean_settings({"cadence": "daily", "per_day": 2, "slots": ["18:00", "12:00"], "timezone": "Asia/Kolkata"})
    now = dt.datetime(2026, 11, 22, 7, 0, tzinfo=dt.timezone.utc)  # 12:30 IST
    assert next_free_slots(s, [], 3, now) == ["2026-11-22T18:00", "2026-11-23T12:00", "2026-11-23T18:00"]
    assert next_free_slots(s, ["2026-11-22T18:00"], 1, now) == ["2026-11-23T12:00"]


def test_slot_less_than_ten_minutes_away_is_skipped():
    s = clean_settings({"cadence": "daily", "per_day": 1, "slots": ["12:00"], "timezone": "Asia/Kolkata"})
    now = dt.datetime(2026, 11, 22, 6, 25, tzinfo=dt.timezone.utc)  # 11:55 IST
    assert next_free_slots(s, [], 1, now) == ["2026-11-23T12:00"]


def test_settings_are_cleaned():
    s = clean_settings({"per_day": 99, "slots": ["7:5", "bad"], "timezone": "Mars/Base", "dm": {"keyword": "get link"}})
    assert s["per_day"] == 6 and s["slots"] == ["07:05"] and s["timezone"] == "Asia/Kolkata"
    assert s["dm"]["keyword"] == "getlink"


def test_caption_template_and_dm_line():
    text = render_caption("{title}\n\n{hashtags}\n{streamer}", title="Bro's rizz goes wrong", hashtags="#gta", streamer="kai", dm_keyword="LINK", dm_on=True)
    assert text.startswith("Bro's rizz goes wrong\n\n#gta\n@kai")
    assert "Comment LINK" in text


def test_bad_key_is_not_saved(poster):
    with pytest.raises(ZernioError):
        poster.set_key("zk_wrong_key_99999")
    assert poster.fake_vault.data == {}


def test_good_key_saved_in_vault_not_in_files(poster, tmp_path):
    found = poster.set_key("zk_good_key_123456")
    assert poster.fake_vault.data == {"zernio_api_key": "zk_good_key_123456"}
    assert found["chosen"] == {"instagram": "ig1", "youtube": "yt1"}
    assert [a["platform"] for a in found["accounts"]] == ["instagram", "youtube"]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "zk_good_key" not in path.read_text(encoding="utf-8", errors="ignore")
    status = poster.status()
    assert status["has_key"] and status["ready"] and status["key_hint"] == "…3456"
    assert "zk_good" not in str(status)


def test_queue_upload_schedule_dm_and_retry(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    poster.save_settings({"dm": {"on": True, "keyword": "LINK", "message": "here", "reply": "check DMs"}})
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")
    added = poster.add([{"file": clip, "title": "Funny one", "streamer": "kai", "job_id": "j", "index": 0}])
    item = added[0]
    assert item["status"] == "waiting" and item["when"] and "Comment LINK" in item["caption"]
    poster.send(item["id"])
    early = next(entry for entry in poster.log if entry[0] == "dm")[1]
    assert early["post_id"] == "post1" and early["keyword"] == "LINK"
    assert poster.queue()[0]["dm"] == "set"
    post = next(entry for entry in poster.log if entry[0] == "post")[1]
    assert post["accounts"] == {"instagram": "ig1"} and post["when"].endswith(":00")
    youtube = added[1]
    poster.send(youtube["id"])
    assert [p["accounts"] for k, p in poster.log if k == "post"] == [{"instagram": "ig1"}, {"youtube": "yt1"}]
    assert poster.queue()[0]["status"] == "scheduled"
    poster.refresh()
    done = poster.queue()[0]
    assert done["status"] == "published" and done["links"]["instagram"] == "https://ig/p/1"
    assert done["dm"] == "set"
    assert len([entry for entry in poster.log if entry[0] == "dm"]) == 1  # not created twice
    poster._update(item["id"], status="failed")
    poster.retry(item["id"])
    assert ("retry", "post1") in poster.log


def test_failed_upload_can_be_retried(poster, tmp_path):
    poster.set_key("zk_good_key_123456")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")
    item = poster.add([{"file": clip, "title": "x"}])[0]
    poster.client_factory = lambda key: type("Broken", (FakeZernio,), {"upload": lambda self, p: (_ for _ in ()).throw(ZernioError("upload broke"))})(key, poster.log)
    poster.send(item["id"])
    failed = poster.queue()[0]
    assert failed["status"] == "failed" and "upload broke" in failed["error"]
    poster.retry(item["id"])
    assert poster.queue()[0]["status"] == "waiting"


def test_add_needs_connected_account(poster, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")
    with pytest.raises(ZernioError, match="Connect"):
        poster.add([{"file": clip, "title": "x"}])


def test_vault_hint_never_shows_whole_key():
    assert vault.hint("zk_abcdefgh1234") == "…1234"
    assert vault.hint("short") == ""
