"""Posting: settings, time slots and the upload queue.

Each clip is copied into the queue folder, uploaded to the buyer's Zernio account and
scheduled there for its slot, so it still posts when Clip Studio is closed.
Settings and queue are plain JSON in the data folder; the Zernio key is NOT stored here
(see vault.py).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import threading
import uuid
import math
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import vault
from .zernio import PLATFORMS, Zernio, ZernioError

KEY_NAME = "zernio_api_key"
TIMEZONES = [
    "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "Europe/Berlin",
    "America/New_York", "America/Chicago", "America/Los_Angeles", "Australia/Sydney",
]
DEFAULTS = {
    "accounts": {"instagram": None, "youtube": None},
    "profile_id": None,
    "per_day": 3,
    "slots": ["12:00", "18:00", "21:00", "09:00", "15:00", "23:00"],
    "timezone": "Asia/Kolkata",
    "caption": "{title}\n\n{hashtags}",
    "hashtags": "#shorts #gaming #fyp",
    "dm": {"on": False, "keyword": "LINK", "message": "Here's the link 👉 ", "reply": "Sent you a DM!"},
    # More Zernio keys (up to 30 in all); with "rotate" on, clips take turns across every key.
    "extra": [],
    "rotate": False,
    "rr": 0,
    "short_platforms": ["instagram", "youtube"],
    "long_youtube": True,
    "cadence": "hourly",
    "clips_per_hour": 6,
}
MAX_KEYS = 30


def key_name(slot: int) -> str:
    return KEY_NAME if int(slot or 1) <= 1 else f"{KEY_NAME}_{int(slot)}"
OPEN = ("waiting", "uploading", "retrying", "cancelling")
NAMES = {"instagram": "Instagram", "youtube": "YouTube"}
LIVE = ("scheduled", "publishing")


def _merge(base: dict, extra: dict) -> dict:
    out = json.loads(json.dumps(base))
    for key, value in (extra or {}).items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = {**out[key], **value}
        elif key in out:
            out[key] = value
    return out


def clean_settings(data: dict) -> dict:
    s = _merge(DEFAULTS, data)
    s["per_day"] = max(1, min(6, int(s.get("per_day") or 1)))
    slots = []
    for slot in s.get("slots") or []:
        try:
            hh, mm = str(slot).split(":")
            slots.append(f"{max(0, min(23, int(hh))):02}:{max(0, min(59, int(mm))):02}")
        except ValueError:
            continue
    s["slots"] = slots or list(DEFAULTS["slots"])
    try:
        ZoneInfo(s["timezone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        s["timezone"] = DEFAULTS["timezone"]
    s["caption"] = str(s["caption"])[:2000]
    s["hashtags"] = str(s["hashtags"])[:500]
    s["accounts"] = {p: (s["accounts"].get(p) or None) for p in PLATFORMS}
    s["short_platforms"] = [p for p in PLATFORMS if p in (s.get("short_platforms") or [])]
    s["long_youtube"] = bool(s.get("long_youtube", True))
    s["cadence"] = "daily" if s.get("cadence") == "daily" else "hourly"
    s["clips_per_hour"] = max(1, min(60, int(s.get("clips_per_hour") or 6)))
    extra = []
    for e in s.get("extra") or []:
        try:
            slot = int(e.get("slot"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 2 <= slot <= MAX_KEYS and slot not in {x["slot"] for x in extra}:
            acc = e.get("accounts") or {}
            extra.append({"slot": slot, "label": str(e.get("label") or f"Account {slot}")[:40],
                          "hint": str(e.get("hint") or "")[:8], "on": bool(e.get("on", True)),
                          "accounts": {p: (acc.get(p) or None) for p in PLATFORMS}})
    s["extra"] = extra
    s["rotate"] = bool(s.get("rotate"))
    s["rr"] = max(0, int(s.get("rr") or 0))
    dm = s["dm"]
    s["dm"] = {
        "on": bool(dm.get("on")),
        "keyword": "".join(str(dm.get("keyword") or "LINK").split())[:30] or "LINK",
        "message": str(dm.get("message") or "")[:900],
        "reply": str(dm.get("reply") or "")[:200],
    }
    return s


def day_slots(settings: dict) -> list[dt.time]:
    if settings.get("cadence") == "hourly":
        minutes = sorted({math.floor(i * 60 / settings["clips_per_hour"]) for i in range(settings["clips_per_hour"])})
        return [dt.time(hour, minute) for hour in range(24) for minute in minutes]
    chosen = settings["slots"][: settings["per_day"]]
    return sorted(dt.time(*map(int, slot.split(":"))) for slot in chosen)


def next_free_slots(settings: dict, taken: list[str], count: int, now: dt.datetime | None = None) -> list[str]:
    """Local wall-clock times (ISO, no offset) in the buyer's timezone, skipping taken ones
    and anything less than 10 minutes away."""
    zone = ZoneInfo(settings["timezone"])
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(zone).replace(tzinfo=None)
    earliest = now + dt.timedelta(minutes=10)
    used = set(taken)
    out = []
    day = now.date()
    for _ in range(366):
        for slot in day_slots(settings):
            when = dt.datetime.combine(day, slot)
            stamp = when.isoformat(timespec="minutes")
            if when >= earliest and stamp not in used:
                out.append(stamp)
                used.add(stamp)
                if len(out) == count:
                    return out
        day += dt.timedelta(days=1)
    return out


def render_caption(template: str, *, title: str, hashtags: str, streamer: str = "", dm_keyword: str = "", dm_on: bool = False) -> str:
    text = template
    for key, value in {"{title}": title, "{hashtags}": hashtags, "{streamer}": streamer and f"@{streamer.lstrip('@')}"}.items():
        text = text.replace(key, value or "")
    if dm_on and dm_keyword and dm_keyword.lower() not in text.lower():
        text = text.rstrip() + f"\n\nComment {dm_keyword} and I'll DM you the link."
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines).strip()[:2200]


def is_short(video: Path) -> bool:
    """Vertical (or square) and 3 minutes or less: YouTube files it as a Short."""
    try:
        from .media import probe_media

        info = probe_media(video)
        return bool(info.height >= info.width and 0 < info.duration <= 180)
    except Exception:
        return True


def _still(video: Path, image: Path):
    """Small thumbnail for the queue list. Best effort: a missing still is fine."""
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", "0.8", "-i", str(video), "-frames:v", "1", "-vf", "scale=160:-2", str(image)],
            timeout=30, check=False, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass


class Poster:
    def __init__(self, root: Path, client_factory=None):
        self.root = Path(root)
        self.folder = self.root / "posting"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.client_factory = client_factory or (lambda key: Zernio(key))
        self.worker: threading.Thread | None = None
        self.send_lock = threading.Lock()

    # ---- storage ----
    def _read(self, name, default):
        try:
            return json.loads((self.root / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _write(self, name, data):
        path = self.root / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def settings(self) -> dict:
        return clean_settings(self._read("posting.json", {}))

    def save_settings(self, data: dict) -> dict:
        with self.lock:
            s = clean_settings({**self.settings(), **data})
            self._write("posting.json", s)
            return s

    def queue(self) -> list[dict]:
        return self._read("posting-queue.json", [])

    def _save_queue(self, items):
        self._write("posting-queue.json", items)

    def _update(self, ident, **changes):
        with self.lock:
            items = self.queue()
            for item in items:
                if item["id"] == ident:
                    item.update(changes)
            self._save_queue(items)

    # ---- key + accounts ----
    def client(self, slot: int = 1) -> Zernio:
        key = vault.get(key_name(slot))
        if not key:
            raise ZernioError("Add your Zernio key first (Posting → Step 1)." if int(slot or 1) <= 1
                              else f"The Zernio key for account {slot} is missing. Add it again in Posting.")
        return self.client_factory(key)

    def accounts_for(self, slot: int = 1, s: dict | None = None) -> dict:
        s = s or self.settings()
        if int(slot or 1) <= 1:
            return dict(s["accounts"])
        e = next((x for x in s["extra"] if x["slot"] == int(slot)), None)
        return dict(e["accounts"]) if e else {}

    def senders(self, s: dict | None = None) -> list[int]:
        """Key slots that post, in turn order (only those with a connected account)."""
        s = s or self.settings()
        out = [1] if any(s["accounts"].values()) else []
        if s["rotate"]:
            out += [e["slot"] for e in s["extra"] if e["on"] and any(e["accounts"].values())]
        return out

    # ---- more Zernio keys (rotation) ----
    def add_extra_key(self, key: str, label: str = "") -> dict:
        key = (key or "").strip()
        if len(key) < 10:
            raise ZernioError("That doesn't look like a Zernio key. Copy the whole key.")
        s = self.settings()
        used = {1} | {e["slot"] for e in s["extra"]}
        slot = next((n for n in range(2, MAX_KEYS + 1) if n not in used), None)
        if slot is None:
            raise ZernioError(f"You can add up to {MAX_KEYS} Zernio keys.")
        found = self.client_factory(key).accounts()  # test before saving
        chosen = {}
        for platform in PLATFORMS:
            first = next((a for a in found if a["platform"] == platform and a["active"]), None)
            chosen[platform] = first["id"] if first else None
        vault.put(key_name(slot), key)
        names = [a.get("name") or a.get("username") or "" for a in found if a["id"] in chosen.values()]
        extra = s["extra"] + [{"slot": slot, "label": (label or ", ".join(n for n in names if n) or f"Account {slot}")[:40],
                               "hint": vault.hint(key), "on": True, "accounts": chosen}]
        self.save_settings({"extra": extra, "rotate": True})
        return self.status()

    def remove_extra_key(self, slot: int):
        s = self.settings()
        vault.delete(key_name(slot))
        self.save_settings({"extra": [e for e in s["extra"] if e["slot"] != int(slot)]})
        return self.status()

    def toggle_extra_key(self, slot: int, on: bool):
        s = self.settings()
        for e in s["extra"]:
            if e["slot"] == int(slot):
                e["on"] = bool(on)
        self.save_settings({"extra": s["extra"]})
        return self.status()

    def status(self) -> dict:
        key = vault.get(KEY_NAME)
        s = self.settings()
        return {
            "has_key": bool(key),
            "key_hint": vault.hint(key),
            "settings": s,
            "ready": bool(key and any(s["accounts"].values())),
            "senders": len(self.senders(s)),
            "max_keys": MAX_KEYS,
            "timezones": TIMEZONES,
            "platform_status": self.platform_status(),
        }

    def platform_status(self) -> list[dict]:
        """Local counts are labelled as such; never invent a provider's daily maximum."""
        s = self.settings()
        limits = self._read("posting-limits.json", {})
        rows = self.queue()
        now = dt.datetime.now(dt.timezone.utc)
        today = now.astimezone(ZoneInfo(s["timezone"])).date().isoformat()
        result = []
        for slot in self.senders(s):
            label = "Main account" if slot == 1 else next(e["label"] for e in s["extra"] if e["slot"] == slot)
            for platform, account in self.accounts_for(slot, s).items():
                if not account:
                    continue
                limit = limits.get(f"{slot}:{platform}:{account}") or {}
                matching = [i for i in rows if i.get("slot", 1) == slot and
                            platform in (i.get("only") or PLATFORMS)]
                result.append(dict(slot=slot, label=label, platform=platform,
                    posted_today=sum(i["status"] == "published" and str(i.get("published_at") or "").startswith(today) for i in matching),
                    pending=sum(i["status"] in OPEN + LIVE for i in matching),
                    limited=bool(limit.get("retry_at", "") > now.isoformat()),
                    message=limit.get("message"), retry_at=limit.get("retry_at"), reset_at=limit.get("reset_at")))
        return result

    def set_key(self, key: str) -> dict:
        key = (key or "").strip()
        if len(key) < 10:
            raise ZernioError("That doesn't look like a Zernio key. Copy the whole key.")
        accounts = self.client_factory(key).accounts()  # test before saving
        vault.put(KEY_NAME, key)
        return self._pick_accounts(accounts)

    def forget_key(self):
        vault.delete(KEY_NAME)

    def _pick_accounts(self, accounts: list[dict]) -> dict:
        s = self.settings()
        chosen = dict(s["accounts"])
        ids = {a["id"] for a in accounts}
        for platform in PLATFORMS:
            if chosen.get(platform) not in ids:
                first = next((a for a in accounts if a["platform"] == platform and a["active"]), None)
                chosen[platform] = first["id"] if first else None
        profile = s["profile_id"] or next((a["profile_id"] for a in accounts if a["profile_id"]), None)
        self.save_settings({"accounts": chosen, "profile_id": profile})
        return {"accounts": [a for a in accounts if a["platform"] in PLATFORMS], "chosen": chosen}

    def accounts(self) -> dict:
        return self._pick_accounts(self.client().accounts())

    def connect_url(self, platform: str, redirect: str) -> str:
        client = self.client()
        s = self.settings()
        profile = s["profile_id"] or client.default_profile()
        if not profile:
            raise ZernioError("Your Zernio account has no profile yet. Open zernio.com once to create one.")
        self.save_settings({"profile_id": profile})
        return client.connect_url(platform, profile, redirect)

    # ---- queue ----
    def picked_times(self, at: str, count: int, gap_minutes: int = 0, zone: str | None = None,
                     now: dt.datetime | None = None) -> list[str]:
        """The buyer's own time ("2026-11-22T16:30", in `zone`, default the posting time zone) for the
        first clip, then one every `gap_minutes`. Returned as wall-clock times in the posting zone."""
        s = self.settings()
        home = ZoneInfo(s["timezone"])
        try:
            first = dt.datetime.fromisoformat(at)
            source = ZoneInfo(zone) if zone else home
        except (ValueError, TypeError, ZoneInfoNotFoundError) as error:
            raise ZernioError("Pick a date and time for the post.") from error
        first = first.replace(tzinfo=source).astimezone(home).replace(tzinfo=None, second=0, microsecond=0)
        local_now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(home).replace(tzinfo=None)
        if first < local_now + dt.timedelta(minutes=1):
            raise ZernioError("That time has already passed. Pick a later time, or choose Post now.")
        step = dt.timedelta(minutes=max(0, int(gap_minutes)))
        return [(first + step * i).isoformat(timespec="minutes") for i in range(count)]

    def add(self, clips: list[dict], when: str = "slots", at: str | None = None, gap_minutes: int = 0,
            zone: str | None = None) -> list[dict]:
        """clips: [{file, title, streamer, job_id, index}] - file is copied into the queue.
        when: "now", "slots" (the daily times in Posting settings) or "at" (the buyer's own time)."""
        s = self.settings()
        picked = self.picked_times(at, len(clips), gap_minutes, zone) if when == "at" else None
        if not self.senders(s):
            raise ZernioError("Connect Instagram or YouTube first (Posting → Step 2).")
        with self.lock:
            items = self.queue()
            taken = [i["when"] for i in items if i.get("when") and i["status"] not in ("removed", "cancelled")]
            turns = self.senders(s) or [1]
            rr = s["rr"]
            times = ([None] * len(clips) if when == "now" else picked if picked
                     else next_free_slots(s, taken, len(clips)))
            added = []
            for clip, at in zip(clips, times, strict=False):
                kind = clip.get("kind") or ("short" if is_short(Path(clip["file"])) else "long")
                allowed = (["youtube"] if s["long_youtube"] else []) if kind == "long" else s["short_platforms"]
                if clip.get("only"):
                    allowed = [p for p in allowed if p in clip["only"]]
                wanted_slot = int(clip.get("posting_slot") or 0)
                eligible = [n for n in turns if (not wanted_slot or n == wanted_slot) and
                            any(self.accounts_for(n, s).get(p) for p in allowed)]
                if not eligible:
                    raise ZernioError("No posting destination is selected for this format. Check Posting settings and accounts.")
                slot = eligible[rr % len(eligible)]
                accounts = self.accounts_for(slot, s)
                clip_group = uuid.uuid4().hex
                for platform in allowed:
                    if not accounts.get(platform):
                        continue
                    # Each platform owns its post ID, retry deadline and cancellation.
                    ident = uuid.uuid4().hex[:12]
                    target = self.folder / f"{ident}.mp4"
                    shutil.copy2(clip["file"], target)
                    _still(target, self.folder / f"{ident}.jpg")
                    item = {
                    "id": ident, "job_id": clip.get("job_id"), "index": clip.get("index"),
                    "file": target.name, "title": clip.get("title") or "Clip",
                    "caption": (clip.get("caption") or render_caption(
                        s["caption"], title=clip.get("title") or "", hashtags=s["hashtags"], streamer=clip.get("streamer") or "",
                        dm_keyword=s["dm"]["keyword"], dm_on=s["dm"]["on"] and platform == "instagram"))[:4900],
                    "only": [platform], "kind": kind, "accounts": {platform: accounts[platform]}, "clip_group": clip_group,
                    "when": at, "status": "waiting", "post_id": None, "error": None, "links": {},
                    # a time the buyer picked is never moved to a daily slot (late = post right away)
                    "pinned": when in ("at", "now"),
                    "slot": slot,
                    "dm": "waiting" if s["dm"]["on"] and platform == "instagram" and slot == 1 else "off",
                    "key": str(uuid.uuid4()),
                    "created": dt.datetime.now().isoformat(timespec="seconds"),
                    }
                    items.append(item)
                    added.append(item)
                rr += 1
            self._save_queue(items)
            if len(turns) > 1:
                self.save_settings({"rr": rr % len(turns)})
        self.kick()
        return added

    def edit(self, ident: str, caption: str | None = None, when: str | None = None):
        with self.lock:
            item = next((i for i in self.queue() if i["id"] == ident), None)
            if not item:
                raise ZernioError("That post isn't in the queue any more.")
            if item["status"] != "waiting":
                raise ZernioError("This one is already sent to Zernio. Change it on zernio.com.")
            changes = {}
            if caption is not None:
                changes["caption"] = caption[:2200]
            if when is not None:
                changes["when"] = when or None
            self._update(ident, **changes)

    def remove(self, ident: str):
        with self.lock:
            item = next((i for i in self.queue() if i["id"] == ident), None)
            if not item:
                return
            if item["status"] == "published":
                raise ZernioError("This post is already live. Remove it on the platform if needed.")
            if item["status"] == "uploading":
                self._update(ident, cancel_requested=True)
                return
            if item.get("post_id"):
                # If the server refuses cancellation, retain the local record and show its error.
                self.client(item.get("slot") or 1).delete_post(item["post_id"])
            items = [i for i in self.queue() if i["id"] != ident]
            (self.folder / f"{ident}.mp4").unlink(missing_ok=True)
            (self.folder / f"{ident}.jpg").unlink(missing_ok=True)
            self._save_queue(items)

    def retry(self, ident: str):
        item = next((i for i in self.queue() if i["id"] == ident), None)
        if not item:
            raise ZernioError("That post isn't in the queue any more.")
        if item.get("post_id") and item["status"] in ("failed", "partial"):
            post = self.client(item.get("slot") or 1).retry(item["post_id"])
            self._apply_post(item, post)
        else:
            s = self.settings()
            taken = [i["when"] for i in self.queue() if i.get("when") and i["id"] != ident]
            when = item.get("when")
            if when and dt.datetime.fromisoformat(when) < dt.datetime.now(ZoneInfo(s["timezone"])).replace(tzinfo=None) + dt.timedelta(minutes=10):
                when = None if item.get("pinned") else next_free_slots(s, taken, 1)[0]
            self._update(ident, status="waiting", error=None, when=when, retry_at=None)
            self.kick()

    # ---- worker ----
    def recover(self):
        """An interrupted request reuses its durable key to reconcile instead of duplicating a post."""
        with self.lock:
            items = self.queue()
            for item in items:
                if item["status"] == "uploading":
                    item["status"] = "waiting"
            self._save_queue(items)

    def kick(self):
        with self.lock:
            self.wake.set()
            if not self.worker or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, name="poster", daemon=True)
                self.worker.start()

    def _run(self):
        last_refresh = 0.0
        while True:
            self.wake.clear()
            if time.monotonic() - last_refresh >= 60:
                last_refresh = time.monotonic()
                self.refresh(only_due=True)
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            pending = [i for i in self.queue() if i["status"] in ("waiting", "retrying") and
                       (not i.get("retry_at") or i["retry_at"] <= now)]
            if not pending:
                if any(i["status"] in ("retrying", *LIVE) for i in self.queue()):
                    self.wake.wait(10)
                elif not self.wake.wait(30):
                    with self.lock:
                        if not self.wake.is_set():
                            self.worker = None
                            return
                continue
            self.send(pending[0]["id"])

    def send(self, ident: str):
        with self.send_lock:
            self._send(ident)

    def _defer(self, item, error, accounts):
        text = str(error)
        # A generic 429 needn't mean a daily cap. Preserve the provider's wording.
        quota = any(term in text.lower() for term in ("daily", "quota", "upload limit", "uploadlimit", "too many videos"))
        wait = getattr(error, "retry_after", None) or (12 * 3600 if quota else 120)
        retry_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=min(max(wait, 5), 7 * 86400))).isoformat()
        self._update(item["id"], status="retrying", error=text, retry_at=retry_at,
                     reset_at=retry_at if getattr(error, "retry_after", None) and quota else None)
        with self.lock:
            limits = self._read("posting-limits.json", {})
            for platform, account in accounts.items():
                limits[f"{item.get('slot') or 1}:{platform}:{account}"] = dict(
                    message=text, retry_at=retry_at, reset_at=retry_at if getattr(error, "retry_after", None) and quota else None)
            self._write("posting-limits.json", limits)

    def _send(self, ident: str):
        item = next((i for i in self.queue() if i["id"] == ident), None)
        if not item or item["status"] not in ("waiting", "retrying"):
            return
        if item.get("retry_at", "") and item["retry_at"] > dt.datetime.now(dt.timezone.utc).isoformat():
            return
        s = self.settings()
        slot = item.get("slot") or 1
        accounts = {p: a for p, a in (item.get("accounts") or self.accounts_for(slot, s)).items()
                    if a and (not item.get("only") or p in item["only"])}
        limits = self._read("posting-limits.json", {})
        for platform, account in accounts.items():
            limit = limits.get(f"{slot}:{platform}:{account}") or {}
            if limit.get("retry_at", "") > dt.datetime.now(dt.timezone.utc).isoformat():
                self._update(ident, status="retrying", error=limit.get("message"), retry_at=limit["retry_at"],
                             reset_at=limit.get("reset_at"))
                return
        when = item.get("when")
        if when:
            now_local = dt.datetime.now(ZoneInfo(s["timezone"])).replace(tzinfo=None)
            if dt.datetime.fromisoformat(when) < now_local + dt.timedelta(minutes=2):
                if item.get("pinned"):
                    when = None  # the buyer's own time is (almost) here: post now, never jump to 6 PM
                else:
                    taken = [i["when"] for i in self.queue() if i.get("when") and i["id"] != ident]
                    when = next_free_slots(s, taken, 1)[0]
        self._update(ident, status="uploading", error=None, when=when)
        try:
            if not accounts:
                raise ZernioError("No account is connected for this Zernio key. Check Posting.")
            client = self.client(slot)
            shared = next((i.get("media_url") for i in self.queue() if item.get("clip_group") and
                           i.get("clip_group") == item["clip_group"] and i.get("media_url")), None)
            url = item.get("media_url") or shared or client.upload(self.folder / item["file"])
            self._update(ident, media_url=url)
            if item.get("post_id"):
                post = client.retry(item["post_id"])
            else:
                post = client.create_post(video_url=url, caption=item["caption"], title=item["title"], accounts=accounts,
                                      when=f"{when}:00" if when else None, timezone=s["timezone"], idempotency_key=item["key"],
                                      short=item.get("kind") != "long" and is_short(self.folder / item["file"]))
        except (ZernioError, OSError) as error:
            if (getattr(error, "retry_after", None) or getattr(error, "status_code", None) == 429
                    or any(t in str(error).lower() for t in ("quota", "upload limit", "uploadlimit", "daily limit"))
                    or getattr(error, "status_code", 0) and error.status_code >= 500):
                self._defer(item, error, accounts)
            else:
                self._update(ident, status="failed", error=str(error))
            return
        item = next((i for i in self.queue() if i["id"] == ident), None)
        if item is None:
            return
        self._apply_post(item, post)
        if item.get("cancel_requested"):
            try:
                self.remove(ident)
            except ZernioError as error:
                self._update(ident, error=f"Cancellation failed: {error}")
            return
        if item.get("dm") == "waiting" and slot == 1 and accounts.get("instagram") and post.get("_id"):
            self._set_dm(item, post_id=post["_id"])

    def _apply_post(self, item: dict, post: dict):
        status = post.get("status") or "scheduled"
        links, errors, ig_post = {}, [], None
        for entry in post.get("platforms") or []:
            platform = entry.get("platform")
            if item.get("only") and platform not in item["only"]:
                continue
            if entry.get("platformPostUrl"):
                links[platform] = entry["platformPostUrl"]
            if entry.get("status") == "failed":
                errors.append(f"{NAMES.get(platform, platform)}: {entry.get('errorMessage') or 'failed'}")
            if platform == "instagram" and entry.get("platformPostId"):
                ig_post = entry["platformPostId"]
        status = {"pending": "scheduled", "draft": "scheduled", "cancelled": "failed"}.get(status, status)
        changes = {"post_id": post.get("_id") or item.get("post_id"), "status": status, "links": links or item.get("links") or {},
                   "error": "; ".join(errors) or None, "retry_at": None}
        if status == "published":
            changes["published_at"] = item.get("published_at") or dt.datetime.now(ZoneInfo(self.settings()["timezone"])).isoformat()
        if ig_post:
            changes["ig_post_id"] = ig_post
        self._update(item["id"], **changes)
        if status in ("failed", "partial") and errors and any(
                t in " ".join(errors).lower() for t in ("limit", "quota", "temporar", "try again")):
            current = next((i for i in self.queue() if i["id"] == item["id"]), item)
            accounts = item.get("accounts") or self.accounts_for(item.get("slot") or 1)
            # Split posts have one target. Legacy combined posts are left intact to avoid resending successes.
            if len(item.get("only") or []) == 1:
                self._defer(current, ZernioError("; ".join(errors)), accounts)
        current = next((i for i in self.queue() if i["id"] == item["id"]), item)
        if ig_post and current.get("dm") in ("waiting", "failed") and status in ("published", "partial"):
            self._set_dm(current, platform_post_id=ig_post)  # fallback if the early hookup failed

    def _set_dm(self, item: dict, post_id: str | None = None, platform_post_id: str | None = None):
        s = self.settings()
        dm = s["dm"]
        try:
            self.client().comment_dm(profile_id=s["profile_id"], account_id=s["accounts"]["instagram"], post_id=post_id,
                                     platform_post_id=platform_post_id, keyword=dm["keyword"], message=dm["message"],
                                     reply=dm["reply"], name=f"Clip Studio · {item['title']}")
            self._update(item["id"], dm="set")
        except ZernioError as error:
            self._update(item["id"], dm="failed", error=f"Auto-DM: {error}")

    def refresh(self, only_due=False) -> list[dict]:
        """Ask Zernio how the scheduled posts are doing. Quietly skips if offline."""
        items = self.queue()
        live = [i for i in items if i.get("post_id") and (i["status"] in LIVE or (i["status"] == "published" and i.get("dm") in ("waiting", "failed")))]
        if only_due:
            now = dt.datetime.now(ZoneInfo(self.settings()["timezone"])).replace(tzinfo=None).isoformat()
            live = [i for i in live if not i.get("when") or i["when"] <= now]
        for item in sorted(live, key=lambda i: i.get("checked_at") or "")[:5]:
            try:
                self._update(item["id"], checked_at=dt.datetime.now(dt.timezone.utc).isoformat())
                self._apply_post(item, self.client(item.get("slot") or 1).get_post(item["post_id"]))
            except ZernioError:
                pass
        if any(i["status"] in ("waiting", "retrying") for i in self.queue()):
            self.kick()
        return self.queue()
