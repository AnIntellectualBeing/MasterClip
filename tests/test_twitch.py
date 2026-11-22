from clipstudio import discovery, twitch
from clipstudio.discovery import merge_sites, pack_site


def _user(login, live=None, vods=()):
    return {
        "login": login,
        "displayName": login.title(),
        "stream": live,
        "videos": {"edges": [{"node": v} for v in vods]},
    }


def test_live_channel_becomes_card():
    card = twitch.normalize_live(_user("paypay", {"id": "1", "title": "GTA RP 🌴", "viewersCount": 403,
                                                  "createdAt": "2026-11-22T00:00:00Z",
                                                  "previewImageURL": "https://x/live.jpg", "game": {"name": "GTA V"}}))
    assert card["url"] == "https://www.twitch.tv/paypay"
    assert card["live"] and card["views"] == 403 and card["site"] == "Twitch" and card["title"] == "GTA RP 🌴"
    assert twitch.normalize_live(_user("offline")) is None
    assert twitch.normalize_live(_user("bad login!", {"id": "1"})) is None


def test_vods_skip_the_one_still_recording_and_short_ones():
    user = _user(
        "paypay",
        {"id": "1", "createdAt": "2026-11-22T00:01:25Z"},
        [
            {"id": "300", "title": "now", "lengthSeconds": 13000, "publishedAt": "2026-11-22T00:01:30Z",
             "previewThumbnailURL": "https://vod-secure.twitch.tv/_404/404_processing_440x248.png"},
            {"id": "200", "title": "yesterday", "lengthSeconds": 29520, "viewCount": 1029,
             "publishedAt": "2026-11-20T23:48:20Z", "previewThumbnailURL": "https://x/v.jpg"},
            {"id": "100", "title": "tiny", "lengthSeconds": 30, "publishedAt": "2026-11-19T00:00:00Z"},
            {"id": "abc", "title": "bad id", "lengthSeconds": 9999},
        ],
    )
    vods = twitch.normalize_vods(user)
    assert [v["url"] for v in vods] == ["https://www.twitch.tv/videos/200"]
    assert vods[0]["duration"] == 29520.0 and vods[0]["views"] == 1029 and not vods[0]["live"]


def test_search_uses_two_requests_and_survives_a_failed_vod_lookup(monkeypatch):
    calls = []

    def fake_gql(query, variables, cancel_event=None):
        calls.append(variables)
        if "q" in variables:
            return {"searchFor": {"channels": {"edges": [
                {"item": _user("small", {"id": "1", "viewersCount": 5})},
                {"item": _user("big", {"id": "2", "viewersCount": 900})},
                {"item": _user("offline")},
            ]}}}
        raise twitch.TwitchError("Twitch search didn't answer.")

    monkeypatch.setattr(twitch, "_gql", fake_gql)
    out = twitch.search("gta rp")
    assert [c["channel"] for c in out["live"]] == ["Big", "Small"]  # most watched first
    assert out["vod"] == []
    assert calls[1] == {"logins": ["small", "big", "offline"]}


def _items(site, live, vod, views=10):
    return {
        "live": [{"id": f"{site}l{i}", "site": site, "live": True, "views": views - i} for i in range(live)],
        "vod": [{"id": f"{site}v{i}", "site": site, "live": False, "views": 1} for i in range(vod)],
    }


def test_slots_are_shared_fairly_between_sites():
    out = merge_sites({"youtube": _items("YouTube", 15, 15), "twitch": _items("Twitch", 1, 2, views=100)}, 8)
    assert len(out) == 8
    assert sum(1 for r in out if r["site"] == "Twitch") == 3  # Twitch has only 3, YouTube fills the rest
    assert [r["live"] for r in out] == [True] * 3 + [False] * 5
    assert [r["id"] for r in out[:3]] == ["YouTubel0", "Twitchl0", "YouTubel1"]  # live streams take turns per site


def test_plenty_on_both_sites_still_shows_past_streams():
    out = merge_sites({"youtube": _items("YouTube", 20, 20), "twitch": _items("Twitch", 15, 16)}, 24)
    kinds = {(r["site"], r["live"]): 0 for r in out}
    for r in out:
        kinds[(r["site"], r["live"])] += 1
    assert kinds == {("YouTube", True): 6, ("YouTube", False): 6, ("Twitch", True): 6, ("Twitch", False): 6}
    assert pack_site([{"id": "l"}] * 1, [{"id": "v"}] * 1, 5) == [{"id": "l"}, {"id": "v"}]


def test_inspect_twitch_live_uses_the_real_stream_title(monkeypatch, tmp_path):
    from clipstudio import sources

    meta = {"extractor_key": "TwitchStream", "is_live": True, "title": "summit1g (live) 2026-11-22 09:23",
            "description": "WoW FOREVER Rogue 60", "uploader": "summit1g", "webpage_url": "https://www.twitch.tv/summit1g"}
    monkeypatch.setattr(sources, "fetch_metadata", lambda *a, **k: meta)
    info = sources.inspect_url("https://www.twitch.tv/summit1g", tmp_path)
    assert info["title"] == "WoW FOREVER Rogue 60" and info["site"] == "Twitch" and info["is_live"]
    assert [sources.site_name(x) for x in ("TwitchVod", "Youtube", "Kick", "Vimeo")] == ["Twitch", "YouTube", "Kick", "Vimeo"]


def test_one_site_down_still_shows_the_other(monkeypatch):
    def broken(topic, cancel_event=None):
        raise twitch.TwitchError("Twitch search didn't answer.")

    entry = {"id": "abcdefghijk", "live_status": "was_live", "duration": 3600, "title": "GTA", "view_count": 5}
    monkeypatch.setattr(twitch, "search", broken)
    monkeypatch.setattr(discovery, "_run_json_command", lambda *a, **k: {"entries": [entry]})
    out = discovery.find_streams("gta rp", 12)
    assert [r["site"] for r in out["results"]] == ["YouTube"]
    assert "Twitch didn't answer" in out["warnings"][0]

    monkeypatch.setattr(discovery, "_run_json_command", lambda *a, **k: (_ for _ in ()).throw(discovery.DiscoveryError("x")))
    out = discovery.find_streams("gta rp", 12)
    assert out["results"] == [] and "couldn't search" in out["warnings"][0]
