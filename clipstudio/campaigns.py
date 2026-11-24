"""Campaigns: our own feed of clipping campaigns, plus the buyer's list of posted clips (My submissions)."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

FEED_URL = os.environ.get("CLIPSTUDIO_CAMPAIGNS_URL", "https://anintellectualcompany.com/campaigns.json")
FEED_TIMEOUT = 8
SEED = Path(__file__).resolve().parent.parent / "static" / "campaigns-seed.json"
_lock = threading.Lock()


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None


def clean_feed(raw) -> dict | None:
    """Keep only what we understand. Returns None when it is not a feed at all."""
    if not isinstance(raw, dict) or not isinstance(raw.get("platforms", []), list) or not isinstance(raw.get("campaigns", []), list):
        return None
    platforms = [p for p in raw.get("platforms", []) if isinstance(p, dict) and p.get("id") and p.get("name") and str(p.get("url", "")).startswith("http")]
    campaigns = []
    for c in raw.get("campaigns", []):
        if not isinstance(c, dict) or not c.get("id") or not c.get("title") or c.get("active") is False:
            continue
        item = dict(c)
        item["payout_per_1k"] = _num(c.get("payout_per_1k"))
        if not str(c.get("url", "")).startswith("http"):
            item["url"] = ""
        item["sources"] = [s for s in c.get("sources") or [] if isinstance(s, dict) and s.get("name")]
        item["rules"] = [str(r) for r in c.get("rules") or []]
        item["platforms"] = [str(p) for p in c.get("platforms") or []]
        lens = c.get("clip_len")
        item["clip_len"] = [int(lens[0]), int(lens[1])] if isinstance(lens, list) and len(lens) == 2 and all(isinstance(n, (int, float)) for n in lens) else None
        campaigns.append(item)
    return dict(updated=raw.get("updated"), platforms=platforms, campaigns=campaigns)


def load_feed(data_dir: Path, fetch=None) -> dict:
    """Live feed -> cached copy -> bundled copy. `source` says which one was used."""
    cache = Path(data_dir) / "campaigns-cache.json"
    if fetch is None:
        def fetch():
            import requests

            reply = requests.get(FEED_URL, timeout=FEED_TIMEOUT, headers={"Accept": "application/json"})
            reply.raise_for_status()
            return reply.json()
    try:
        feed = clean_feed(fetch())
    except Exception:
        feed = None
    if feed is not None:
        try:
            _write(cache, feed)
        except OSError:
            pass
        return dict(feed, source="live")
    feed = clean_feed(_read(cache))
    if feed is not None:
        return dict(feed, source="cache")
    return dict(clean_feed(_read(SEED)) or dict(updated=None, platforms=[], campaigns=[]), source="bundled")


def estimate(views, payout_per_1k) -> float | None:
    """views / 1000 x payout per 1K views, to cents. None when the payout is unknown."""
    rate = _num(payout_per_1k)
    count = _num(views)
    if rate is None or count is None:
        return None
    return round(count / 1000 * rate, 2)


class Submissions:
    FIELDS = ("campaign_id", "clip_name", "posted_url", "posted_at")

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "campaign-submissions.json"

    def all(self) -> list[dict]:
        data = _read(self.path)
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    def add(self, **fields) -> dict:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        item = dict(id=uuid.uuid4().hex[:10], campaign_id="", clip_name="", posted_url="", posted_at=now[:10], views=0, updated_at=now)
        self._apply(item, fields)
        with _lock:
            items = self.all()
            items.append(item)
            _write(self.path, items)
        return item

    def update(self, sid: str, **fields) -> dict | None:
        with _lock:
            items = self.all()
            for item in items:
                if item.get("id") == sid:
                    self._apply(item, fields)
                    item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    _write(self.path, items)
                    return item
        return None

    def delete(self, sid: str) -> bool:
        with _lock:
            items = self.all()
            kept = [i for i in items if i.get("id") != sid]
            if len(kept) == len(items):
                return False
            _write(self.path, kept)
            return True

    def _apply(self, item: dict, fields: dict) -> None:
        for key in self.FIELDS:
            if fields.get(key) is not None:
                item[key] = str(fields[key]).strip()[:500]
        if fields.get("views") is not None:
            item["views"] = max(0, int(fields["views"]))

    def summary(self, campaigns: list[dict]) -> dict:
        rates = {c["id"]: c.get("payout_per_1k") for c in campaigns}
        titles = {c["id"]: c.get("title") for c in campaigns}
        rows, total = [], 0.0
        for item in self.all():
            est = estimate(item.get("views", 0), rates.get(item.get("campaign_id")))
            total += est or 0
            rows.append(dict(item, estimate=est, campaign_title=titles.get(item.get("campaign_id")) or ""))
        return dict(submissions=rows, total_estimate=round(total, 2), label="estimate")
