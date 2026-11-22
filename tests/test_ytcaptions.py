"""YouTube's own captions as the transcript (no speech model)."""

import json

from clipstudio import ytcaptions
from clipstudio.ytcaptions import parse_json3, pick_track

URL = "https://www.youtube.com/api/timedtext?v=abc&fmt=json3"

# Shape copied from a real auto-caption track (GTA RP video), times shortened.
EVENTS = {"events": [
    {"tStartMs": 0, "dDurationMs": 999999, "id": 1, "wpWinPosId": 1},
    {"tStartMs": 60600, "dDurationMs": 4000, "wWinId": 1, "segs": [
        {"utf8": "they"}, {"utf8": " reported", "tOffsetMs": 200}, {"utf8": " on", "tOffsetMs": 800},
        {"utf8": " you,", "tOffsetMs": 1000}, {"utf8": " fake", "tOffsetMs": 1200}, {"utf8": " news.", "tOffsetMs": 1500}]},
    {"tStartMs": 62490, "dDurationMs": 2110, "wWinId": 1, "aAppend": 1, "segs": [{"utf8": "\n"}]},
    {"tStartMs": 64600, "dDurationMs": 2700, "wWinId": 1, "segs": [
        {"utf8": ">> All", "isSpeakerChange": 1}, {"utf8": " right, we're", "tOffsetMs": 99},
        {"utf8": " looking", "tOffsetMs": 200}, {"utf8": " good.", "tOffsetMs": 400}]},
    {"tStartMs": 66000, "dDurationMs": 1000, "segs": [{"utf8": "[Music]"}]},
    {"tStartMs": 70000, "dDurationMs": 2000, "segs": [{"utf8": "go go go"}]},  # untimed line: spread
]}


def test_words_keep_their_order_and_times():
    segs = parse_json3(EVENTS, offset=60)
    words = [w for s in segs for w in s["words"]]
    assert [w["text"] for w in words] == ["they", "reported", "on", "you,", "fake", "news.", "All", "right,",
                                          "we're", "looking", "good.", "go", "go", "go"]
    assert words[0]["start"] == 0.6 and words[6]["start"] == 4.6
    assert all(a["start"] <= b["start"] for a, b in zip(words, words[1:]))
    assert all(w["end"] > w["start"] for w in words)
    # a pause of 0.8 s or more starts a new segment
    assert [s["text"] for s in segs] == ["they reported on you, fake news.", "All right, we're looking good.", "go go go"]
    assert segs[2]["words"][1]["start"] == round(10 + 2 / 3, 3)


def test_only_the_downloaded_part_is_kept():
    segs = parse_json3(EVENTS, offset=64, duration=5)
    assert [s["text"] for s in segs] == ["All right, we're looking good."]
    assert segs[0]["start"] == 0.6


def track(lang, ext="json3", url=URL):
    return {lang: [{"ext": "vtt", "url": url}, {"ext": ext, "url": url}]}


def test_track_choice():
    meta = {"language": "en", "automatic_captions": {**track("en-orig"), **track("fr")}}
    assert pick_track(meta) == ("en", URL)
    # Hindi speech: not used unless the buyer picked Hindi (it comes as Devanagari)
    hindi = {"language": "hi", "automatic_captions": {**track("hi-orig"), **track("en")}}
    assert pick_track(hindi) is None
    assert pick_track(hindi, "hi") == ("hi", URL)
    # a translated English track of Hindi speech is never used
    assert pick_track(hindi, "en") is None
    # uploader's own track when there are no automatic ones
    assert pick_track({"language": "en", "subtitles": track("en")}) == ("en", URL)
    # never fetch from anywhere but YouTube
    assert pick_track({"language": "en", "automatic_captions": track("en-orig", url="https://evil.example/x")}) is None


def test_fetch_falls_back_quietly(monkeypatch):
    meta = {"language": "en", "automatic_captions": track("en-orig")}

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self, _):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ytcaptions, "urlopen", lambda *a, **k: Response(json.dumps(EVENTS).encode()))
    got = ytcaptions.fetch(meta, 60, 30)
    assert got["language"] == "en" and got["segments"][0]["text"].startswith("they reported")

    def broken(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(ytcaptions, "urlopen", broken)
    assert ytcaptions.fetch(meta, 60, 30) is None
    assert ytcaptions.fetch({"language": "en"}, 0, 30) is None
