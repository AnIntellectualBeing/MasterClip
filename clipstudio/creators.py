"""Creators: clip a streamer automatically whenever they go live.

The buyer adds a Twitch name or a YouTube channel. While MasterClip is open it checks once a
minute; when one of them is live it starts a normal live capture (chat spikes become clips) with
the clip style saved when the creator was added.

Rules (the PC has to cope unattended):
  * one stream at a time; the others wait their turn;
  * a recording stops after 3 hours (a new one starts on the next check if they are still live);
  * nothing starts, and a running recording stops, when the disk is nearly full;
  * the big recording file is deleted after its last clip is made (clips are kept);
  * Stop on a creator pauses that creator for 30 minutes.

`Watcher` holds no network or job code itself: the app passes small functions in, so the
scheduling rules are tested with fakes.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

CHECK_EVERY = 60
CAP_SECONDS = 3 * 3600
PAUSE_SECONDS = 30 * 60
MAX_CREATORS = 30
_LOGIN = re.compile(r"^[A-Za-z0-9_]{2,25}$")
_HANDLE = re.compile(r"^@[A-Za-z0-9._-]{3,30}$")
_NOT_TWITCH = {"videos", "directory", "search", "settings", "downloads", "p", "jobs", "turbo"}


def parse_creator(text: str) -> dict:
    """A Twitch name / link or a YouTube channel link / @handle -> {kind, key, url, name}.
    Raises ValueError with plain words."""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Type a Twitch name or paste a YouTube channel link.")
    if _HANDLE.match(raw):
        return {"kind": "youtube", "key": raw.lower(), "url": f"https://www.youtube.com/{raw}", "name": raw}
    if _LOGIN.match(raw):
        return {"kind": "twitch", "key": raw.lower(), "url": f"https://www.twitch.tv/{raw.lower()}", "name": raw}
    url = raw if re.match(r"^https?://", raw, re.I) else "https://" + raw
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("twitch.tv") and parts and _LOGIN.match(parts[0]) and parts[0].lower() not in _NOT_TWITCH:
        login = parts[0].lower()
        return {"kind": "twitch", "key": login, "url": f"https://www.twitch.tv/{login}", "name": parts[0]}
    if host.endswith("youtube.com") and parts:
        if _HANDLE.match(parts[0]):
            return {"kind": "youtube", "key": parts[0].lower(), "url": f"https://www.youtube.com/{parts[0]}", "name": parts[0]}
        if parts[0] in ("channel", "c", "user") and len(parts) > 1 and re.match(r"^[A-Za-z0-9_-]{2,64}$", parts[1]):
            base = f"https://www.youtube.com/{parts[0]}/{parts[1]}"
            return {"kind": "youtube", "key": base.lower(), "url": base, "name": parts[1]}
    if host.endswith("youtube.com") or host == "youtu.be":
        raise ValueError("Paste the channel link (like youtube.com/@name), not a video link.")
    raise ValueError("Use a Twitch name (like shroud) or a YouTube channel link (like youtube.com/@name).")


class Creators:
    """The list, kept in creators.json in the app's data folder."""

    def __init__(self, root: Path):
        self.path = Path(root) / "creators.json"
        self.lock = threading.RLock()

    def all(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [c for c in data if isinstance(c, dict) and c.get("id")] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, ident: str) -> dict | None:
        return next((c for c in self.all() if c["id"] == ident), None)

    def add(self, text: str, recipe: dict, captions: bool = True, fmt: str = "vertical", style_name: str = "") -> dict:
        found = parse_creator(text)
        with self.lock:
            items = self.all()
            if any(c["kind"] == found["kind"] and c["key"] == found["key"] for c in items):
                raise ValueError(f"{found['name']} is already in your list.")
            if len(items) >= MAX_CREATORS:
                raise ValueError(f"You can watch up to {MAX_CREATORS} creators.")
            creator = {**found, "id": uuid.uuid4().hex[:10], "recipe": recipe, "captions": bool(captions),
                       "format": fmt, "style_name": style_name[:60], "on": True, "paused_until": 0,
                       "added": time.time(), "jobs": [], "note": "", "live": False}
            items.append(creator)
            self._save(items)
            return creator

    def update(self, ident: str, **changes) -> dict | None:
        with self.lock:
            items = self.all()
            for c in items:
                if c["id"] == ident:
                    c.update(changes)
                    self._save(items)
                    return c
        return None

    def remove(self, ident: str):
        with self.lock:
            self._save([c for c in self.all() if c["id"] != ident])


class Watcher:
    """Once a minute: look after the running recording, else start one for a creator who is live.

    check_live(creators) -> {creator id: live url}      (network, batched where it can be)
    start_capture(creator, url) -> job id                (raises with a plain message if it can't)
    capture_running(job id) -> bool
    stop_capture(job id)
    delete_recording(job id)                             (after the last clip)
    free_bytes() -> int | None                           (work folder's drive)
    """

    def __init__(self, creators: Creators, *, check_live, start_capture, capture_running, stop_capture,
                 delete_recording, free_bytes, min_free: int, now=time.time, cap_seconds=CAP_SECONDS):
        self.creators = creators
        self.check_live = check_live
        self.start_capture = start_capture
        self.capture_running = capture_running
        self.stop_capture = stop_capture
        self.delete_recording = delete_recording
        self.free_bytes = free_bytes
        self.min_free = min_free
        self.now = now
        self.cap_seconds = cap_seconds
        self.current: dict | None = None  # {creator, job, started}
        self.note = ""
        self._last_start = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    # ---- the rules ----
    def _disk_low(self) -> bool:
        free = self.free_bytes()
        return free is not None and free < self.min_free

    def _finish(self):
        cur, self.current = self.current, None
        try:
            self.delete_recording(cur["job"])
        except Exception:  # noqa: BLE001 - a locked file must not stop the watcher
            pass
        self.creators.update(cur["creator"], live=False)

    def tick(self) -> None:
        with self._lock:
            now = self.now()
            if self.current:
                cur = self.current
                if self.capture_running(cur["job"]):
                    if now - cur["started"] >= self.cap_seconds:
                        self.creators.update(cur["creator"], note="Stopped after 3 hours. Starts again if still live.")
                        self.stop_capture(cur["job"])
                    elif self._disk_low():
                        self.creators.update(cur["creator"], note="Stopped: the disk is almost full.")
                        self.note = "Recording stopped because the disk is almost full."
                        self.stop_capture(cur["job"])
                    return
                self._finish()
            if self._disk_low():
                self.note = "Not checking: the disk is almost full. Free some space or pick another Save folder."
                return
            self.note = ""
            due = [c for c in self.creators.all() if c.get("on", True) and float(c.get("paused_until") or 0) <= now]
            if not due:
                return
            try:
                live = self.check_live(due) or {}
            except Exception:  # noqa: BLE001 - offline: try again next minute
                self.note = "Couldn't check who is live (no internet?). Trying again in a minute."
                return
            for c in due:
                self.creators.update(c["id"], live=c["id"] in live, checked=now)
            # take turns: start after the creator that started last
            order = due
            if self._last_start:
                ids = [c["id"] for c in due]
                if self._last_start in ids:
                    i = ids.index(self._last_start) + 1
                    order = due[i:] + due[:i]
            for c in order:
                if c["id"] not in live:
                    continue
                try:
                    job = self.start_capture(c, live[c["id"]])
                except Exception as exc:  # noqa: BLE001
                    self.creators.update(c["id"], note=f"Couldn't start: {str(exc)[:160]}", paused_until=now + PAUSE_SECONDS)
                    continue
                self.current = {"creator": c["id"], "job": job, "started": now}
                self._last_start = c["id"]
                self.creators.update(c["id"], jobs=(list(c.get("jobs") or []) + [job])[-50:], note="Recording now.",
                                     last_live=now)
                return

    def stop(self, creator_id: str):
        """Stop pressed: end that creator's recording and pause them for 30 minutes."""
        with self._lock:
            self.creators.update(creator_id, paused_until=self.now() + PAUSE_SECONDS, note="Paused for 30 minutes.")
            if self.current and self.current["creator"] == creator_id:
                self.stop_capture(self.current["job"])

    def resume(self, creator_id: str):
        with self._lock:
            self.creators.update(creator_id, paused_until=0, note="")
        self.wake()

    def status(self) -> dict:
        return {"current": dict(self.current) if self.current else None, "note": self.note,
                "running": bool(self._thread and self._thread.is_alive())}

    # ---- the background loop ----
    def wake(self):
        self._wake.set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="creators-watch", daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop.set()
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            if not self.creators.all() and not self.current:
                self._thread = None
                return  # nothing to watch: no network at all
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                pass
            self._wake.wait(CHECK_EVERY)
            self._wake.clear()
