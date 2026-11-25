"""App-level helpers: Creators auto-watch, phone remote, update check, Report a problem, clip covers.

Kept out of app.py so the routes are easy to find. Uses the app's own job store and live
capture through `_core()` (app.py imports this file at its end).
"""

from __future__ import annotations

import os
import platform
import re
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import storage
from .creators import Creators, Watcher

router = APIRouter()
SUPPORT_EMAIL = "anintellectualcompany.contact@gmail.com"
ROOT = Path(__file__).resolve().parent.parent


def _core():
    return sys.modules["clipstudio.app"]


def _plain(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    return str(detail if isinstance(detail, str) else exc)[:300]


# ---------------- creators ----------------
_state: dict = {}


def creators() -> Creators:
    if "creators" not in _state:
        _state["creators"] = Creators(_core().store.root)
    return _state["creators"]


def _check_live(due: list[dict]) -> dict:
    from .twitch import live_logins
    from .ytchat import channel_live

    live = {}
    twitch = [c for c in due if c["kind"] == "twitch"]
    if twitch:
        on = live_logins([c["key"] for c in twitch])
        live.update({c["id"]: c["url"] for c in twitch if c["key"] in on})
    for c in due:
        if c["kind"] == "youtube":
            url = channel_live(c["url"])
            if url:
                live[c["id"]] = url
    return live


def _start_capture(creator: dict, url: str) -> str:
    core = _core()
    try:
        core.licence_gate()
        storage.ensure_room(core.store.work_root, core.output_folder())
        source = core.SourceRequest(source_type="url", source=url)
        job = core.start_live_job(source, creator.get("recipe") or core.Recipe().model_dump(), creator.get("captions", True),
                                  creator.get("format") or "vertical",
                                  extra={"creator": creator["id"], "creator_name": creator.get("name", "")})
    except (HTTPException, ValueError) as exc:
        raise RuntimeError(_plain(exc)) from exc
    core.licence_count()
    return job["id"]


def _delete_recording(ident: str):
    core = _core()
    try:
        job = core.store.get(ident)
    except KeyError:
        return
    path = job.get("source_path")
    if path and Path(path).is_file():
        Path(path).unlink(missing_ok=True)
        message = (job.get("message") or "").rstrip(".")
        core.store.update(ident, recording_deleted=True,
                          message=(message + ". " if message else "") + "The full recording was deleted to save space; your clips are kept.")


def watcher() -> Watcher:
    if "watcher" not in _state:
        core = _core()
        _state["watcher"] = Watcher(
            creators(),
            check_live=_check_live,
            start_capture=_start_capture,
            capture_running=lambda job: job in core.live_stops,
            stop_capture=lambda job: core.stop_live(job),
            delete_recording=_delete_recording,
            free_bytes=lambda: storage.free_bytes(core.store.work_root),
            min_free=storage.MIN_FREE_BYTES,
        )
    return _state["watcher"]


def _kick():
    if not os.environ.get("CLIPSTUDIO_NO_WATCH"):  # tests: never check real channels
        w = watcher()
        w.start()
        w.wake()


class CreatorBody(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    template: str = Field(default="big_captions", max_length=40)
    length: int = Field(default=30, ge=10, le=90)
    captions: bool = True


def _recipe_for(template: str, length: int) -> tuple[dict, str]:
    """Snapshot of the chosen clip style, so later template edits don't change this creator."""
    from .design import TEMPLATES

    core = _core()
    mine = next((t for t in core.my_templates.all() if t["id"] == template), None)
    if mine:
        design = dict(mine["design"])
        base = mine.get("base") if any(t["id"] == mine.get("base") for t in TEMPLATES) else "big_captions"
        kind = "long" if design.get("aspect") == "16:9" else "short"
        return core.Recipe(template=base, length=length, kind=kind, design=design).model_dump(), mine["name"]
    built = next((t for t in TEMPLATES if t["id"] == template), None)
    if not built:
        raise HTTPException(400, "Pick a clip style from the list.")
    kind = "long" if built.get("kind") == "long" else "short"
    return core.Recipe(template=template, length=length, kind=kind).model_dump(), built["name"]


def _creator_view(c: dict) -> dict:
    core = _core()
    now = time.time()
    current = watcher().current
    recording = bool(current and current["creator"] == c["id"])
    paused = float(c.get("paused_until") or 0) > now
    if recording:
        status = "Live: recording and clipping"
    elif not c.get("on", True):
        status = "Off"
    elif paused:
        status = "Paused until " + time.strftime("%H:%M", time.localtime(c["paused_until"]))
    elif c.get("live"):
        status = "Live: waits for the other recording to finish"
    else:
        status = "Watching (checks every minute)"
    clips = []
    for ident in reversed(c.get("jobs") or []):
        try:
            job = core.store.get(ident)
        except KeyError:
            continue
        for clip in reversed(job.get("clips") or []):
            clips.append({"job": ident, "index": clip["index"], "title": clip.get("title") or "Clip",
                          "url": clip.get("url"), "when": job.get("created_at")})
            if len(clips) >= 12:
                break
        if len(clips) >= 12:
            break
    return {"id": c["id"], "name": c.get("name"), "kind": c["kind"], "url": c["url"], "style": c.get("style_name") or "",
            "length": (c.get("recipe") or {}).get("length"), "on": c.get("on", True), "paused": paused,
            "recording": recording, "status": status, "note": c.get("note") or "", "clips": clips,
            "job": current["job"] if recording else None}


@router.get("/api/creators")
def list_creators():
    w = watcher()
    return {"creators": [_creator_view(c) for c in creators().all()], "note": w.status()["note"]}


@router.post("/api/creators", status_code=201)
def add_creator(body: CreatorBody):
    recipe, style = _recipe_for(body.template, body.length)
    try:
        creators().add(body.text, recipe, body.captions, "horizontal" if recipe["kind"] == "long" else "vertical", style)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _kick()
    return list_creators()


@router.delete("/api/creators/{ident}")
def remove_creator(ident: str):
    w = watcher()
    if w.current and w.current["creator"] == ident:
        w.stop(ident)
    creators().remove(ident)
    return list_creators()


@router.post("/api/creators/{ident}/stop")
def stop_creator(ident: str):
    if not creators().get(ident):
        raise HTTPException(404, "That creator isn't in your list any more.")
    watcher().stop(ident)
    return list_creators()


class CreatorSwitch(BaseModel):
    on: bool


@router.post("/api/creators/{ident}/switch")
def switch_creator(ident: str, body: CreatorSwitch):
    if not creators().update(ident, on=body.on, paused_until=0, note=""):
        raise HTTPException(404, "That creator isn't in your list any more.")
    w = watcher()
    if not body.on and w.current and w.current["creator"] == ident:
        w.stop_capture(w.current["job"])
    if body.on:
        _kick()
    return list_creators()


# ---------------- phone remote ----------------
class RemoteActions:
    """What the phone may do. Nothing else of the app is reachable from it."""

    def _live_job(self):
        core = _core()
        running = [j for j in core.store.all() if j["id"] in core.live_stops]
        return running[0] if running else None

    def state(self):
        core = _core()
        job = self._live_job()
        shown = job or next((j for j in core.store.all() if j.get("live")), None)
        live = None
        if job:
            info = job.get("live") or {}
            live = {"title": job.get("title") or "Live stream", "channel": job.get("channel") or "",
                    "recorded": info.get("recorded") or 0, "hype": info.get("hype"), "hype_label": info.get("hype_label"),
                    "state": info.get("state"), "message": job.get("message") or "", "clip_now": bool(info.get("clip_now"))}
        clips = []
        if shown:
            for clip in reversed(shown.get("clips") or []):
                clips.append({"title": clip.get("title") or "Clip", "url": f"/m/api/clip/{shown['id']}/{clip['index']}"})
        return {"live": live, "clips": clips[:30], "from": (shown or {}).get("title")}

    def start(self, url: str, length: int):
        core = _core()
        if self._live_job():
            raise ValueError("Already recording a stream. Press Stop first.")
        core.licence_gate()
        recipe = core.Recipe(length=length, count=3).model_dump()
        core.start_live_job(core.SourceRequest(source_type="url", source=url), recipe, True, "vertical")
        core.licence_count()
        return self.state()

    def clip_now(self):
        job = self._live_job()
        if not job:
            raise ValueError("Nothing is recording right now.")
        _core().clip_live_now(job["id"])
        return self.state()

    def stop(self):
        job = self._live_job()
        if not job:
            raise ValueError("Nothing is recording right now.")
        _core().stop_live(job["id"])
        return self.state()

    def clip_file(self, job: str, index: int) -> Path:
        core = _core()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job):
            raise HTTPException(404, "Clip not found")
        try:
            found = core.store.get(job)
        except KeyError:
            raise HTTPException(404, "Clip not found")
        clip = next((c for c in found.get("clips") or [] if c["index"] == index), None)
        path = core.store.folder(job) / clip["file"] if clip else None
        if not path or not path.is_file():
            raise HTTPException(404, "Clip not found")
        return path


def remote():
    if "remote" not in _state:
        from .remote import RemoteServer

        _state["remote"] = RemoteServer(RemoteActions(), ROOT / "static" / "m.html")
    return _state["remote"]


class RemoteSwitch(BaseModel):
    on: bool


@router.get("/api/remote")
def remote_info():
    return remote().info()


@router.post("/api/remote")
def remote_switch(body: RemoteSwitch):
    r = remote()
    if body.on:
        try:
            return r.start()
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc))
    r.stop()
    return r.info()


@router.post("/api/remote/new-pin")
def remote_new_pin():
    r = remote()
    r.gate.new_pin()
    return r.info()


# ---------------- update check ----------------
@router.get("/api/update-check")
def update_check():
    from . import __version__
    from .updates import check

    return check(_core().store.root, __version__)


# ---------------- report a problem ----------------
_HOME = str(Path.home())


def redact(text: str) -> str:
    """Log lines safe to share: no link parameters (they can be signed), no keys, no home folder."""
    text = re.sub(r"(https?://[^\s?#\"']+)\?[^\s\"']*", r"\1?…", text)
    text = re.sub(r"(?i)(key|token|secret|password|authorization)([\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+", r"\1\2[hidden]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", text)
    if _HOME and len(_HOME) > 3:
        text = text.replace(_HOME, "%USERPROFILE%")
    return text


def _tail(path: Path, lines: int) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return ""
    return redact("\n".join(data))


@router.get("/api/report")
def report_info():
    from . import __version__

    folder = _core().logs_dir()
    logs = []
    for name, count in (("app.log", 40), ("crash.log", 30)):
        text = _tail(folder / name, count)
        if text.strip():
            logs.append(f"--- last lines of {name} ---\n{text}")
    return {"version": __version__, "os": platform.platform(), "python": platform.python_version(),
            "email": SUPPORT_EMAIL, "subject": f"MasterClip {__version__}: problem report",
            "logs": "\n\n".join(logs) or "(no log lines yet)"}


# ---------------- cover image per clip ----------------
@router.get("/api/jobs/{ident}/clips/{index}/cover")
def clip_cover_file(ident: str, index: int, download: bool = False):
    core = _core()
    job = core.get_job(ident)
    clip = next((c for c in job.get("clips") or [] if c["index"] == index), None)
    if not clip:
        raise HTTPException(404, "That clip is gone.")
    path = core.clip_cover(ident, clip, make=True)
    if not path:
        raise HTTPException(500, "Couldn't make a cover for this clip.")
    name = core._safe_name(clip.get("title") or "clip") + " cover.jpg"
    return FileResponse(path, media_type="image/jpeg", filename=name if download else None,
                        headers={"Cache-Control": "no-store"})


# ---------------- start / stop with the app ----------------
def on_start():
    if creators().all() and not os.environ.get("CLIPSTUDIO_NO_WATCH"):
        watcher().start()


def on_stop():
    if "watcher" in _state:
        _state["watcher"].shutdown()
    if "remote" in _state:
        _state["remote"].stop()
