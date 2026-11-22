"""Owner's 23 Nov list: Hinglish, whole-stream scan, key rotation, YouTube Shorts, word reveal."""

from clipstudio.hinglish import romanize, romanize_segments
from clipstudio.sources import pick_hot_windows
from clipstudio.zernio import Zernio
from tests.test_posting import poster  # noqa: F401  (fixture)


def test_hindi_becomes_hinglish():
    assert romanize("भाई ये क्या हो रहा है") == "bhai ye kya ho raha hai"
    assert romanize("करना समझना") == "karna samajhna"
    assert romanize("GTA में चलते हैं") == "GTA mein chalte hain"
    assert romanize("plain english stays") == "plain english stays"
    seg = romanize_segments([{"start": 0, "end": 1, "text": "चल यार", "words": [{"start": 0, "end": 0.5, "text": "चल"}]}])
    assert seg[0]["text"] == "chal yaar" and seg[0]["words"][0]["text"] == "chal"


def test_hot_windows_follow_chat_and_skip_downloading_everything():
    nine_hours = 9 * 3600
    chat = [5000 + i * 0.5 for i in range(400)] + [20000 + i for i in range(100)]
    windows, reason = pick_hot_windows(nine_hours, chat_times=chat, count=6)
    assert any(s <= 5000 <= e for s, e in windows)
    assert any(s <= 20000 <= e for s, e in windows)
    assert sum(e - s for s, e in windows) <= 6 * 150  # minutes, not hours
    assert windows == sorted(windows) and "chat" in reason


def test_hot_windows_without_any_signal_spread_out():
    windows, reason = pick_hot_windows(4 * 3600, count=5)
    assert len(windows) == 5 and reason == "spread-out parts"


def test_youtube_short_gets_shorts_tag(monkeypatch):
    sent = {}
    z = Zernio.__new__(Zernio)
    monkeypatch.setattr(z, "_call", lambda method, path, **kw: sent.update(kw["json"]) or {"post": {"_id": "p"}}, raising=False)
    z.create_post(video_url="u", caption="Wild one", title="Crazy GTA RP moment", accounts={"youtube": "yt1"},
                  when=None, timezone="Asia/Kolkata", short=True)
    yt = sent["platforms"][0]
    assert yt["platformSpecificData"]["title"].endswith("#Shorts")
    assert "#Shorts" in yt["customContent"]


def test_clips_rotate_across_zernio_keys(poster, tmp_path):  # noqa: F811
    poster.set_key("zk_good_key_123456")
    poster.add_extra_key("zk_good_key_123456", "Second")
    poster.add_extra_key("zk_good_key_123456", "Third")
    s = poster.settings()
    assert s["rotate"] and [e["slot"] for e in s["extra"]] == [2, 3]
    assert "zernio_api_key_2" in poster.fake_vault.data
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")
    added = poster.add([{"file": clip, "title": f"c{i}"} for i in range(4)], when="now")
    assert [a["slot"] for a in added] == [1, 2, 3, 1]
    poster.toggle_extra_key(2, False)
    assert poster.status()["senders"] == 2
    poster.remove_extra_key(3)
    assert "zernio_api_key_3" not in poster.fake_vault.data
