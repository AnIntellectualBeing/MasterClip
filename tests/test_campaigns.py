import importlib

import pytest
from fastapi.testclient import TestClient

from clipstudio import campaigns

FEED = {
    "updated": "2026-11-24",
    "platforms": [{"id": "p1", "name": "Plat", "url": "https://example.com"}, {"id": "bad", "name": "No link", "url": "x"}],
    "campaigns": [
        {"id": "c1", "title": "One", "payout_per_1k": 2, "url": "https://example.com/c1", "clip_len": [15, 45],
         "sources": [{"name": "Streamer", "link": "https://youtu.be/x"}], "active": True},
        {"id": "c2", "title": "Hidden", "payout_per_1k": 1, "active": False},
        {"id": "c3", "title": "No rate", "payout_per_1k": "lots"},
    ],
}


def boom():
    raise OSError("offline")


def test_live_feed_is_cleaned_and_cached(tmp_path):
    feed = campaigns.load_feed(tmp_path, fetch=lambda: FEED)
    assert feed["source"] == "live"
    assert [p["id"] for p in feed["platforms"]] == ["p1"]
    assert [c["id"] for c in feed["campaigns"]] == ["c1", "c3"]
    assert feed["campaigns"][1]["payout_per_1k"] is None
    assert (tmp_path / "campaigns-cache.json").exists()


def test_falls_back_to_cache_then_bundled(tmp_path):
    campaigns.load_feed(tmp_path, fetch=lambda: FEED)
    assert campaigns.load_feed(tmp_path, fetch=boom)["source"] == "cache"
    other = tmp_path / "empty"
    seed = campaigns.load_feed(other, fetch=boom)
    assert seed["source"] == "bundled"
    assert seed["platforms"]  # the seed ships real platforms
    assert seed["campaigns"] == []


def test_garbage_feed_is_ignored(tmp_path):
    assert campaigns.load_feed(tmp_path, fetch=lambda: ["nope"])["source"] == "bundled"


def test_estimate():
    assert campaigns.estimate(12500, 2) == 25.0
    assert campaigns.estimate(999, 1.5) == 1.5
    assert campaigns.estimate(0, 3) == 0.0
    assert campaigns.estimate(1000, None) is None


def test_submissions_store_and_summary(tmp_path):
    store = campaigns.Submissions(tmp_path)
    a = store.add(campaign_id="c1", clip_name="clip A", posted_url="https://t.co/1", views=5000)
    b = store.add(campaign_id="c3", clip_name="clip B", views=100)
    assert len(store.all()) == 2
    store.update(a["id"], views=10000)
    mine = store.summary(campaigns.clean_feed(FEED)["campaigns"])
    rows = {r["clip_name"]: r for r in mine["submissions"]}
    assert rows["clip A"]["estimate"] == 20.0
    assert rows["clip B"]["estimate"] is None
    assert mine["total_estimate"] == 20.0 and mine["label"] == "estimate"
    assert store.delete(b["id"]) and not store.delete(b["id"])
    assert store.update("nope", views=1) is None
    # survives a restart
    assert len(campaigns.Submissions(tmp_path).all()) == 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_DATA", str(tmp_path))
    monkeypatch.setattr(campaigns, "FEED_URL", "http://127.0.0.1:9/campaigns.json")
    import clipstudio.app as module

    importlib.reload(module)
    with TestClient(module.app) as c:
        yield c


def test_api_round_trip(client):
    feed = client.get("/api/campaigns").json()
    assert feed["source"] in ("bundled", "cache", "live") and feed["platforms"]
    made = client.post("/api/campaigns/submissions", json={"campaign_id": "x", "clip_name": "hi", "views": 3}).json()
    sid = made["item"]["id"]
    after = client.put(f"/api/campaigns/submissions/{sid}", json={"views": 4000}).json()
    assert after["submissions"][0]["views"] == 4000
    assert client.delete(f"/api/campaigns/submissions/{sid}").json()["submissions"] == []
    assert client.delete(f"/api/campaigns/submissions/{sid}").status_code == 404
    assert client.post("/api/campaigns/submissions", json={"views": -1}).status_code == 422


def test_recipe_remembers_campaign_id():
    from clipstudio.app import Recipe

    assert Recipe(campaign_id="c1").model_dump()["campaign_id"] == "c1"
    assert Recipe().model_dump()["campaign_id"] is None
