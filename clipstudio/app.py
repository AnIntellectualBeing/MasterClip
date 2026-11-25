from __future__ import annotations
import importlib.util
import json
import math
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Literal
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, field_validator
from . import storage
from . import campaigns as camp
from .jobs import JobStore, ACTIVE
from .mytemplates import MyTemplates
from .searches import Searches
from .sources import import_url, validate_url
from .media import extract_audio, probe_media
from .engine import (
    analyze_media,
    Candidate,
    select_highlights,
    render_compilation,
    render_short,
)

ROOT = Path(__file__).resolve().parent.parent
_DATA = Path(os.environ.get("CLIPSTUDIO_DATA", str(ROOT / "data")))
store = JobStore(_DATA, storage.work_root(_DATA), storage.old_work_roots())


def _use_temp_in(folder: Path):
    """Python, FFmpeg and yt-dlp scratch files follow the work folder, so a full C: drive doesn't break jobs."""
    if storage.chosen_folder() is None:
        return
    temp = Path(folder) / "temp"
    try:
        temp.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    tempfile.tempdir = str(temp)
    os.environ["TEMP"] = os.environ["TMP"] = str(temp)


_use_temp_in(store.work_root)
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-worker")
events: dict[str, threading.Event] = {}
busy = threading.Lock()
searches = Searches()
my_templates = MyTemplates(store.root)


@asynccontextmanager
async def lifespan(app):
    poster.recover()
    poster.kick()
    yield
    for event in events.values():
        event.set()
    for event in searches.events.values():
        event.set()


app = FastAPI(title="GTA Clip Studio", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)
app.mount(
    "/static", StaticFiles(directory=ROOT / "static", check_dir=False), name="static"
)
app.mount(
    "/fonts", StaticFiles(directory=ROOT / "fonts", check_dir=False), name="fonts"
)
app.mount(
    "/emoji", StaticFiles(directory=ROOT / "emoji", check_dir=False), name="emoji"
)


@app.get("/help", include_in_schema=False)
def installation_help():
    return FileResponse(ROOT / "static" / "help.html", media_type="text/html")


class SourceRequest(BaseModel):
    source_type: Literal["path", "url"] = "path"
    source: str = Field(min_length=1, max_length=4096)
    transcribe: bool = True
    language: str = Field(default="auto", max_length=8)  # "auto" or a Whisper code (hi, kn, ta, ...)
    # "spoken" = captions in the language people speak; "en" = English captions for any language
    captions_in: Literal["roman", "spoken", "en"] = "roman"
    model: Literal["tiny", "base", "small"] = "base"
    source_height: Literal[720, 1080, 2160] = 1080
    capture_seconds: int = Field(default=600, ge=30, le=10800)

    @field_validator("language")
    @classmethod
    def known_language(cls, value):
        from .transcript import LANGUAGE_NAMES

        if value != "auto" and value not in LANGUAGE_NAMES:
            raise ValueError("Pick a language from the list.")
        return value


class ClipRequest(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    score: float = Field(default=0, allow_inf_nan=False)
    reasons: list[str] = Field(default_factory=list)


class ExportRequest(BaseModel):
    mode: Literal["highlights", "shorts", "both", "clips"] = "both"
    target_seconds: int = Field(default=210, ge=180, le=240)
    short_seconds: int = Field(default=30, ge=5, le=180)
    short_count: int = Field(default=3, ge=1, le=20)
    layout: Literal["blur", "center"] = "blur"
    burn_captions: bool = True
    clips: list[ClipRequest] | None = Field(default=None, max_length=1000)
    # "clips" mode: template id + design overrides (see design.py)
    template: str = Field(default="big_captions", max_length=40)
    design: dict | None = None
    # or several output shapes at once ([{kind, template, design}]; wins over template/design)
    formats: list[dict] | None = Field(default=None, max_length=2)


class ClipEdit(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    title: str = Field(default="", max_length=140)
    design: dict
    # "Use for all clips of this video": this clip's framing goes on every clip of the same shape.
    framing_all: bool = False


class ProjectRequest(SourceRequest):
    format: Literal["horizontal", "vertical"] = "horizontal"
    captions: bool = True
    from_discovery: bool = False
    topic: str = Field(default="", max_length=160)
    # Only this part of a VOD is downloaded (seconds in the original video).
    section_start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    section_end: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # Product settings chosen on the home screen (template, clip length, …).
    recipe: dict | None = None
    # Long VOD: read the whole stream's signals, download only the hottest parts.
    scan_whole: bool = False
    # Live only: keep recording and clipping until Stop or the stream ends.
    live_rolling: bool = False


class DiscoveryRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=160)
    max_results: int = Field(default=12, ge=1, le=24)

    @field_validator("topic", mode="before")
    @classmethod
    def clean_topic(cls, value):
        return value.strip() if isinstance(value, str) else value


class InspectRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)


class RecipeFormat(BaseModel):
    """One output shape: a template plus the buyer's changes to it."""

    kind: Literal["short", "long"] = "short"
    template: str = Field(default="big_captions", max_length=40)
    design: dict | None = None


class Recipe(BaseModel):
    """What the buyer picked on the home screen."""

    template: str = Field(default="big_captions", max_length=40)
    length: int = Field(default=30, ge=5, le=180)
    count: int = Field(default=3, ge=1, le=20)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    design: dict | None = None
    # Video type from the first screen. "both" makes every moment as a Short AND a 16:9 video.
    kind: Literal["short", "long", "both"] = "short"
    formats: list[RecipeFormat] | None = Field(default=None, max_length=2)
    # First screen "Auto-post every clip": each finished clip joins the posting queue (daily times).
    auto_post: bool = False
    posting_slot: int = Field(default=0, ge=0, le=30)
    resolution: Literal[720, 1080, 2160] = 1080
    # Set when the clips are made for a campaign (Campaigns > Clip for this).
    campaign_id: str | None = Field(default=None, max_length=80)


def validate_auto_post(recipe):
    if not (recipe or {}).get("auto_post"):
        return
    if not poster.status()["ready"]:
        raise HTTPException(400, "Set up Posting first: add your Zernio key, select an account and save your posting times.")
    slot = recipe.get("posting_slot") or 0
    if slot and slot not in poster.senders():
        raise HTTPException(400, "The posting account for this window is unavailable. Select it again in Posting.")


def auto_post_clip(ident, job, clip):
    """Queue a finished clip for posting when the buyer chose auto-post. Never breaks the job."""
    if not (job.get("recipe") or {}).get("auto_post"):
        return
    settings = poster.settings()
    if (clip.get("kind") == "long" and not settings["long_youtube"] or
            clip.get("kind") != "long" and not settings["short_platforms"]):
        clip["posting_note"] = "Saved only: posting is disabled for this format."
        return
    try:
        path = store.folder(ident) / clip["file"]
        item = {"file": path, "title": clip.get("title") or "", "streamer": job.get("channel") or "",
                "job_id": ident, "index": clip["index"], "kind": clip.get("kind") or "short",
                "posting_slot": (job.get("recipe") or {}).get("posting_slot", 0)}
        if clip.get("caption"):
            item["caption"] = clip["caption"]
        if clip.get("kind") == "long":
            item["only"] = ["youtube"]
        added = poster.add([item], "slots")
        clip["posting_ids"] = [i["id"] for i in added]
    except Exception as exc:  # no key / no account: tell the buyer once, keep clipping
        warnings = list(store.get(ident).get("warnings") or [])
        note = f"Auto-post is on but this clip wasn't queued: {str(exc)[:160]}"
        if note not in warnings:
            store.update(ident, warnings=warnings + [note])


def recipe_formats(recipe: dict | None) -> list[dict]:
    """[{kind, template, design}] to render for each moment (old recipes: one format)."""
    recipe = recipe or {}
    formats = recipe.get("formats")
    if formats:
        return [dict(f) for f in formats][:2]
    design = dict(recipe.get("design") or {})
    if recipe.get("aspect") and "aspect" not in design:
        design["aspect"] = recipe["aspect"]
    kind = "long" if design.get("aspect") == "16:9" else "short"
    return [dict(kind=kind, template=recipe.get("template") or "big_captions", design=design or None)]


class CaptionsRequest(BaseModel):
    segments: list[dict] = Field(max_length=20000)


def get_job(ident):
    try:
        job = store.get(ident)
        for output in job.get("outputs", []):
            output["path"] = str(store.folder(ident) / output["name"])
        return job
    except KeyError:
        raise HTTPException(404, "Job not found")


def progress(ident, status, amount, message):
    store.update(
        ident, status=status, progress=round(min(1, max(0, amount)), 3), message=message
    )


def serialize_candidates(candidates, transcript):
    items = []
    for candidate in candidates:
        item = asdict(candidate)
        item["excerpt"] = " ".join(
            s["text"]
            for s in transcript
            if s["start"] < candidate.end and s["end"] > candidate.start
        )[:350]
        items.append(item)
    return items


def heat_curve(points, offset, duration):
    """YouTube "most replayed" buckets (VOD time) -> one 0..1 value per second of the downloaded part."""
    if not points:
        return None
    n = max(1, int(math.ceil(duration)))
    curve = [0.0] * n
    offset = float(offset or 0)
    top = max((float(p["value"]) for p in points), default=0) or 1.0
    for p in points:
        a, b = int(float(p["start"]) - offset), int(math.ceil(float(p["end"]) - offset))
        for t in range(max(0, a), min(n, b)):
            curve[t] = max(curve[t], float(p["value"]) / top)
    return curve if any(curve) else None


def read_vod_chat(url, section, duration, cancel_event=None, on_start=None):
    """Twitch VOD chat replay for the downloaded part, in seconds of that part (None if not Twitch)."""
    from .twitch import vod_chat, vod_id

    vid = vod_id(url)
    if not vid:
        return None
    if on_start:
        on_start()
    offset = float(section["start"]) if section else 0.0
    try:
        chat = vod_chat(vid, offset, offset + float(duration), cancel_event)
    except Exception:
        return None
    return [(t - offset, text) for t, text in chat] or None


def _youtube_captions(folder, duration):
    """Transcript segments from the captions.json import_url saved, or None."""
    try:
        data = json.loads((Path(folder) / "captions.json").read_text(encoding="utf-8"))
        segments = [s for s in data["segments"] if float(s["start"]) < duration]
        return segments or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def job_warn(ident, note: str):
    warnings = list(store.get(ident).get("warnings") or [])
    if note not in warnings:
        store.update(ident, warnings=warnings + [note])


def _hms(seconds) -> str:
    seconds = int(float(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}"


def analyze(ident, options, imported_transcript=None):
    event = events[ident]
    folder = store.folder(ident)
    try:
        if event.is_set():
            raise RuntimeError("Cancelled")
        source = store.get(ident).get("source_path")
        hot_chat = None
        hot = options.source_type == "url" and (store.get(ident).get("section") or {}).get("hot")
        if hot:
            from .sources import HotPartsFailed, import_hotspots

            fallback = (store.get(ident).get("section") or {}).get("fallback")
            try:
                source, title, hot_chat, parts = import_hotspots(
                    options.source, folder, event, lambda p, m: progress(ident, "importing", p * 0.25, m),
                    max_height=options.source_height)
                store.update(ident, title=title, source_path=str(source), section=None, parts=[list(p) for p in parts])
            except HotPartsFailed as error:
                hot = False
                if not fallback:
                    fallback = [0.0, 600.0]
                store.update(ident, section=dict(start=float(fallback[0]), end=float(fallback[1])))
                job_warn(ident, f"The whole-stream scan couldn't download its parts ({error}), so we used "
                                f"{_hms(fallback[0])}–{_hms(fallback[1])} instead.")
                progress(ident, "importing", 0.02, "Whole-stream scan was blocked: downloading your time range instead")
        if not hot and options.source_type == "url":
            section = store.get(ident).get("section")
            source, title = import_url(
                options.source,
                folder,
                options.capture_seconds,
                event,
                lambda p, m: progress(ident, "importing", p * 0.25, m),
                section=(section["start"], section["end"]) if section else None,
                captions_language=options.language if options.transcribe and imported_transcript is None else None,
                max_height=options.source_height,
            )
            store.update(ident, title=title, source_path=str(source))
        info = probe_media(source)
        if info.duration <= 0 or not math.isfinite(info.duration):
            raise ValueError("The video has no usable duration.")
        store.update(ident, duration=info.duration, media=asdict(info))
        meta_path = folder / "source-info.json"
        heat = None
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                store.update(ident, channel=str(meta.get("channel") or "")[:80])
                heat = heat_curve(meta.get("heat"), (meta.get("section") or [0])[0], info.duration)
            except (OSError, ValueError, TypeError):
                pass
        progress(ident, "analyzing", 0.24, "Looking for the streamer's face cam")
        try:
            from .facecam import detect_facecam

            store.update(ident, cam_roi=detect_facecam(source))
        except Exception:
            store.update(ident, cam_roi=None)
        audio = folder / "audio.wav"
        progress(ident, "analyzing", 0.25, "Extracting audio")
        if info.has_audio:
            extract_audio(source, audio, timeout=7200, cancel_event=event)
        transcript = imported_transcript or []
        warnings = []
        if not store.get(ident).get("cam_roi"):
            warnings.append("No streamer face cam was found in this stream, so the Shorts use the full "
                            "gameplay picture instead of a cam on top.")
        youtube = _youtube_captions(folder, info.duration) if options.source_type == "url" else None
        if youtube and options.language == "auto" and info.has_audio:
            # YouTube often files Hinglish speech as English and its English captions are then
            # nonsense; listen for a few seconds first and only keep them for real English.
            from .transcript import quick_language

            if quick_language(audio) not in (None, "en"):
                youtube = None
        if youtube and options.transcribe and imported_transcript is None:
            progress(ident, "transcribing", 0.3, "Using YouTube's own captions (no speech model needed)")
            transcript = youtube
        elif options.transcribe and imported_transcript is None and info.has_audio:
            from .transcript import model_status

            progress(
                ident,
                "transcribing",
                0.3,
                "Turning speech into captions on this PC"
                + ("; the first time, the speech model downloads once (about 150 MB)"
                   if model_status(options.model) == "download" else ""),
            )
            try:
                from .transcript import LANGUAGE_NAMES, transcribe

                heard = {}
                said = {"text": "Listening to what's said"}
                transcript = transcribe(
                    audio,
                    options.model,
                    None if options.language == "auto" else options.language,
                    progress=lambda done, total: progress(
                        ident,
                        "transcribing",
                        0.3 + 0.45 * (done / max(total, 1)),
                        said["text"],
                    ),
                    cancel_event=event,
                    task="translate" if options.captions_in == "en" else "transcribe",
                    status=lambda message: (said.update(text=message),
                                            progress(ident, "transcribing", 0.3, message)),
                    info=heard,
                )
                if heard.get("language"):
                    store.update(ident, spoken_language=heard["language"])
                    said["text"] = f"Heard {LANGUAGE_NAMES.get(heard['language'], heard['language'])}"
                if not transcript and info.duration > 20:
                    warnings.append("We couldn't make out the words, so moments were picked from the "
                                    "sound and action. Pick the spoken language in Design if it's wrong.")
            except Exception as exc:
                warnings.append(
                    "Speech analysis unavailable; audio and motion ranking used. "
                    + str(exc)[:250]
                )
        if event.is_set():
            raise RuntimeError("Cancelled")
        if options.captions_in == "roman":
            from .hinglish import romanize_segments

            transcript = romanize_segments(transcript)  # Hindi letters -> Hinglish
        (folder / "transcript.json").write_text(
            json.dumps(transcript, ensure_ascii=False), encoding="utf-8"
        )
        chat = None
        if options.source_type == "url":
            chat = hot_chat if hot else read_vod_chat(options.source, store.get(ident).get("section"), info.duration, event,
                                 lambda: progress(ident, "analyzing", 0.755, "Reading the chat replay"))
        progress(ident, "analyzing", 0.76, "Finding moments")
        result = analyze_media(
            source,
            transcript_segments=transcript,
            audio_path=audio,
            cancel_event=event,
            context_seconds=28,
            progress=lambda p, m: progress(ident, "analyzing", 0.76 + 0.23 * p, m),
            heat=heat,
            chat=chat,
        )
        if os.environ.get("CLIPSTUDIO_DIRECTOR_MODEL") and transcript:
            progress(
                ident,
                "analyzing",
                0.95,
                "Understanding moments with the configured director model",
            )
            from .director import rerank_candidates

            result.candidates, director_warnings = rerank_candidates(
                result.candidates,
                transcript,
                cancel_event=event,
                progress=lambda p, m: progress(ident, "analyzing", 0.95 + 0.04 * p, m),
            )
            warnings.extend(director_warnings)
        candidates = serialize_candidates(result.candidates, transcript)
        warnings.extend(result.warnings)
        if info.duration < 180:
            warnings.append(
                "Source is shorter than 3 minutes; the highlights video will also be shorter."
            )
        warnings.append(
            "Ranking uses activity and speech cues. Review moments for humor, context, and suitability before publishing."
        )
        if event.is_set():
            raise RuntimeError("Cancelled")
        scale = (
            10
            if any(
                any(r.startswith("Director:") for r in c["reasons"]) for c in candidates
            )
            else 1.5
        )
        store.update(
            ident,
            status="ready",
            progress=1,
            message="Analysis ready. Review moments and export.",
            candidates=candidates,
            warnings=warnings,
            score_scale=scale,
        )
        automatic = store.get(ident).get("auto_export")
        if automatic:
            if not candidates:
                store.update(
                    ident,
                    message="No clear moments found. Use Adjust clips to add a moment, then rebuild.",
                )
            else:
                export(ident, ExportRequest(**automatic))
    except Exception as exc:
        store.update(
            ident,
            status="cancelled" if event.is_set() else "failed",
            progress=0,
            message="Cancelled" if event.is_set() else "Processing failed",
            error=str(exc)[:1000],
        )


def file_output(ident, path, kind):
    return dict(
        name=Path(path).name,
        url=f"/api/jobs/{ident}/files/{Path(path).name}",
        kind=kind,
        path=str(Path(path).resolve()),
    )


def job_design(job, template, overrides=None):
    """Template + the buyer's tweaks + what we learned from the video."""
    from .design import TEMPLATES, Design, template_design

    extra = dict(overrides or {})
    mine = my_templates.all()
    base = (next((t for t in [*TEMPLATES, *mine] if t["id"] == template), {}) or {}).get("design") or {}
    keeps = extra.get("keep_boxes", base.get("keep_boxes")) and (extra.get("cam_roi") or base.get("cam_roi"))
    if not extra.get("cam_roi") and not keeps:
        extra["cam_roi"] = job.get("cam_roi")  # found in this video; the home screen can't know it
    if not extra.get("credit"):
        extra["credit"] = job.get("channel") or ""
    recipe = job.get("recipe") or {}
    extra["resolution"] = recipe.get("resolution") or 1080
    if recipe.get("aspect") and "aspect" not in extra and not recipe.get("formats"):
        extra["aspect"] = recipe["aspect"]
    design = template_design(template, mine, **extra)
    framing = (job.get("framing") or {}).get(design.aspect)
    if framing:  # "Use for all clips of this video" from the clip editor
        design = Design(**{**design.model_dump(), **framing})
    return design


def job_designs(job, captions=True):
    """[(kind, template id, Design)] for every output shape the buyer asked for."""
    out = []
    for fmt in recipe_formats(job.get("recipe")):
        design = job_design(job, fmt.get("template") or "big_captions", fmt.get("design"))
        if not captions:
            design.caption.style = "none"
        out.append((fmt.get("kind") or ("long" if design.aspect == "16:9" else "short"), fmt.get("template"), design))
    return out


def _title_context(job) -> str:
    """What the titles may use to name the game: the stream title, topic and channel."""
    return " ".join(str(job.get(k) or "") for k in ("title", "topic", "channel"))


def _clip_words(transcript, start, end):
    from .transcript import map_transcript_to_clips

    segments = map_transcript_to_clips(transcript, [dict(start=start, end=end)])[0]
    return segments, " ".join(s["text"] for s in segments)


def boost_pick(job, transcript, pool, keep):
    """Ask the AI boost (if on) to choose `keep` of `pool` and title them.
    Returns (candidates, {id(candidate): title}, note). Falls back to the local order."""
    from .boost import Booster, BoostError, blend

    booster = Booster(store.root)
    if not booster.on or not pool:
        return pool[:keep], {}, ""
    rows = []
    for i, c in enumerate(pool):
        _, text = _clip_words(transcript, c.start, c.end)
        rows.append(dict(id=str(i), start=c.start, end=c.end, score=float(c.score or 0), text=text))
    worded = [r for r in rows if len(r["text"].split()) >= 4]
    if len(worded) < max(1, len(rows) // 3):
        # Nothing to read (language not understood, music, silence): the AI would only guess and
        # once titled a silent clip "still waiting for something to happen". Sound + action decide.
        return pool[:keep], {}, ("AI boost was skipped: we couldn't make out enough words in this video, so moments "
                                 "were picked from the sound and action. Set the spoken language in Design if it's wrong.")
    try:
        picks = booster.rate(rows, job.get("channel") or "", _title_context(job))
    except BoostError as error:
        return pool[:keep], {}, f"AI boost didn't work this time ({error}) so the normal picker was used."
    chosen = blend(rows, picks, keep)
    out = [pool[int(r["id"])] for r in chosen]
    # Only moments with real words get the AI's title; the rest keep the game-aware local title.
    titles = {id(pool[int(r["id"])]): picks[r["id"]].title for r in chosen
              if r["id"] in picks and len(r["text"].split()) >= 4}
    return out, titles, "✨ AI boost picked these moments and wrote the titles."


def export_clips(ident, options):
    """Shorts/Reels with the chosen template, one FFmpeg pass per clip."""
    from .render import render_clip
    from .shorts import select_shorts
    from .titles import suggest_titles

    event = events[ident]
    folder = store.folder(ident)
    job = store.get(ident)
    try:
        transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        candidates = [
            Candidate(**{k: c[k] for k in ("start", "end", "score", "reasons", "core_start", "core_end") if k in c})
            for c in job["candidates"]
        ]
        if options.clips is not None:
            candidates = [Candidate(**c.model_dump()) for c in options.clips]
        formats = options.formats or [dict(template=options.template, design=options.design)]
        designs = []
        for fmt in formats:
            design = job_design(job, fmt.get("template") or options.template, fmt.get("design"))
            if not options.burn_captions:
                design.caption.style = "none"
            kind = fmt.get("kind") or ("long" if design.aspect == "16:9" else "short")
            designs.append((kind, fmt.get("template") or options.template, design))
        from .boost import Booster

        boosted = Booster(store.root).on
        shortlist = select_shorts(
            candidates, transcript, job["duration"], length=options.short_seconds,
            count=min(30, options.short_count * 3) if boosted else options.short_count,
        )
        if boosted:
            progress(ident, "rendering", 0.01, "✨ AI boost is choosing the best moments")
        picked, ai_titles, ai_note = boost_pick(job, transcript, shortlist, options.short_count)
        revision = job.get("export_revision", 0) + 1
        clips = []
        total = max(len(picked) * len(designs), 1)
        for i, c in enumerate(picked):
            segments, text = _clip_words(transcript, c.start, c.end)
            suggestions = suggest_titles(text, job.get("channel", ""), avoid={x["title"] for x in clips}, reasons=c.reasons,
                                         context=_title_context(job))
            if id(c) in ai_titles:
                suggestions = [ai_titles[id(c)]] + [t for t in suggestions if t != ai_titles[id(c)]][:2]
            title = suggestions[0] if suggestions else ""
            for kind, template, design in designs:
                if event.is_set():
                    raise RuntimeError("Cancelled")
                n = len(clips)
                label = f"clip {i + 1} of {len(picked)}" + (f" ({'Short' if kind == 'short' else '16:9'})" if len(designs) > 1 else "")
                progress(ident, "rendering", 0.02 + 0.96 * n / total, f"Making {label}")
                name = f"clip-{revision:02}-{i + 1:02}" + (f"-{kind}" if len(designs) > 1 else "") + ".mp4"
                render_clip(job["source_path"], c.start, c.end, folder / name, design,
                            segments, title, cancel_event=event)
                clips.append(dict(
                    index=n, moment=i, kind=kind, start=round(c.start, 2), end=round(c.end, 2), score=c.score,
                    file=name, url=f"/api/jobs/{ident}/files/{name}", title=title,
                    suggestions=suggestions, text=text[:600],
                    design=design.model_dump(), template=template,
                ))
                store.update(ident, clips=clips, outputs=[
                    file_output(ident, folder / x["file"], "clip") for x in clips
                ])
                auto_post_clip(ident, job, clips[-1])
        store.update(
            ident, status="complete", progress=1, export_revision=revision,
            last_export_options=options.model_dump(), clips=clips, ai_note=ai_note,
            outputs=[file_output(ident, folder / x["file"], "clip") for x in clips],
            message=f"{len(clips)} clips ready" if clips else
            "No clear moments found. Try a longer time range.",
        )
    except Exception as exc:
        store.update(
            ident, status="cancelled" if event.is_set() else "failed",
            error=str(exc)[:1000],
            message="Making clips stopped. You can try again.",
        )


def rerender_clip(ident, index, edit: ClipEdit):
    from .design import Design
    from .render import render_clip

    event = events[ident]
    folder = store.folder(ident)
    job = store.get(ident)
    try:
        transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        design = Design(**edit.design)
        segments, text = _clip_words(transcript, edit.start, edit.end)
        clips = job["clips"]
        version = int(clips[index].get("version", 0)) + 1
        name = f"clip-{job.get('export_revision', 1):02}-{index + 1:02}-v{version}.mp4"
        progress(ident, "rendering", 0.3, f"Updating clip {index + 1}")
        render_clip(job["source_path"], edit.start, edit.end, folder / name, design,
                    segments, edit.title, cancel_event=event)
        old = folder / clips[index]["file"]
        clips[index].update(
            start=round(edit.start, 2), end=round(edit.end, 2), title=edit.title,
            design=design.model_dump(), file=name, version=version, text=text[:600],
            url=f"/api/jobs/{ident}/files/{name}",
        )
        store.update(
            ident, status="rendering" if edit.framing_all else "complete", progress=1, clips=clips,
            outputs=[file_output(ident, folder / x["file"], "clip") for x in clips],
            message=f"Clip {index + 1} updated",
        )
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
        if edit.framing_all:
            _framing_to_all(ident, index, design, transcript, event)
    except Exception as exc:
        store.update(
            ident, status="cancelled" if event.is_set() else "complete",
            error=str(exc)[:1000], message="That change didn't work. Try again.",
        )


def _framing_to_all(ident, index, design, transcript, event):
    """Put one clip's framing (layout + boxes) on every other clip of the same shape, and remember it
    for this video's next clips (live clips too)."""
    from .design import Design, framing_of
    from .render import render_clip

    folder = store.folder(ident)
    job = store.get(ident)
    framing = framing_of(design)
    store.update(ident, framing={**(job.get("framing") or {}), design.aspect: framing})
    clips = job["clips"]
    others = [n for n, c in enumerate(clips)
              if n != index and (c.get("design") or {}).get("aspect", "9:16") == design.aspect]
    for step, n in enumerate(others):
        if event.is_set():
            raise RuntimeError("Cancelled")
        clip = clips[n]
        progress(ident, "rendering", (step + 1) / (len(others) + 1), f"Same framing on clip {n + 1}")
        new = Design(**{**(clip.get("design") or {}), **framing})
        segments, _ = _clip_words(transcript, clip["start"], clip["end"])
        version = int(clip.get("version", 0)) + 1
        name = f"clip-{job.get('export_revision', 1):02}-{n + 1:02}-v{version}.mp4"
        render_clip(job["source_path"], clip["start"], clip["end"], folder / name, new,
                    segments, clip.get("title") or "", cancel_event=event)
        old = folder / clip["file"]
        clip.update(design=new.model_dump(), file=name, version=version, url=f"/api/jobs/{ident}/files/{name}")
        store.update(ident, clips=clips, outputs=[file_output(ident, folder / x["file"], "clip") for x in clips])
        try:
            old.unlink(missing_ok=True)
        except OSError:
            pass
    store.update(ident, status="complete", progress=1,
                 message=f"Same framing on {len(others) + 1} clips" if others else f"Clip {index + 1} updated")


def export(ident, options):
    if options.mode == "clips":
        return export_clips(ident, options)
    event = events[ident]
    folder = store.folder(ident)
    job = store.get(ident)
    try:
        transcript = json.loads(
            (folder / "transcript.json").read_text(encoding="utf-8")
        )
        candidates = [
            Candidate(
                **{k: c[k] for k in ("start", "end", "score", "reasons", "core_start", "core_end") if k in c}
            )
            for c in job["candidates"]
        ]
        if options.clips is not None:
            candidates = [Candidate(**c.model_dump()) for c in options.clips]
        revision = job.get("export_revision", 0) + 1
        source = job["source_path"]
        outputs = list(job.get("outputs", []))
        manifest = dict(
            source=source,
            source_title=job["title"],
            mode=options.mode,
            highlights=[],
            shorts=[],
        )
        from .transcript import (
            segments_to_srt,
            map_transcript_to_clips,
            map_transcript_to_concatenation,
        )

        embedded_count = 0
        video_count = 0

        if options.mode in ("highlights", "both"):
            selected = select_highlights(
                candidates, job["duration"], options.target_seconds
            )
            progress(ident, "rendering", 0.02, "Rendering landscape highlights")
            path = folder / f"highlights-{revision:02}.mp4"
            render_compilation(
                source,
                selected,
                path,
                cancel_event=event,
                progress=lambda p, m: progress(ident, "rendering", 0.02 + 0.55 * p, m),
            )
            outputs.append(file_output(ident, path, "highlights"))
            video_count += 1
            video_output = outputs[-1]
            video_output["captions"] = False
            timeline = map_transcript_to_concatenation(transcript, selected)
            offset = sum(clip.end - clip.start for clip in selected)
            if timeline:
                from .captions import phrase_segments, burn_captions

                caption = path.with_suffix(".srt")
                caption.write_text(
                    segments_to_srt(phrase_segments(timeline)), encoding="utf-8"
                )
                outputs.append(file_output(ident, caption, "captions"))
                if options.burn_captions:
                    progress(ident, "rendering", 0.56, "Adding captions to highlights")
                    burn_captions(path, caption, cancel_event=event)
                    embedded_count += 1
                    video_output["captions"] = True
            manifest["highlights"] = [asdict(c) for c in selected]
            store.update(ident, outputs=outputs)
            if offset < 180:
                store.update(
                    ident,
                    warnings=job["warnings"]
                    + [
                        "Selected moments total less than 3 minutes. Add more moments for a longer export."
                    ],
                )
        if options.mode in ("shorts", "both"):
            from .shorts import select_shorts

            shorts = select_shorts(
                candidates,
                transcript,
                job["duration"],
                length=options.short_seconds,
                count=options.short_count,
            )
            for i, c in enumerate(shorts):
                if event.is_set():
                    raise RuntimeError("Cancelled")
                progress(
                    ident,
                    "rendering",
                    0.58 + 0.39 * i / max(len(shorts), 1),
                    f"Rendering Short {i + 1} of {len(shorts)}",
                )
                path = folder / f"short-{revision:02}-{i + 1:02}.mp4"
                render_short(source, c, path, layout=options.layout, cancel_event=event)
                outputs.append(file_output(ident, path, "short"))
                video_count += 1
                video_output = outputs[-1]
                video_output["captions"] = False
                mapped = map_transcript_to_clips(transcript, [c])[0]
                if mapped:
                    from .captions import phrase_segments, burn_captions

                    caption = path.with_suffix(".srt")
                    caption.write_text(
                        segments_to_srt(phrase_segments(mapped)), encoding="utf-8"
                    )
                    outputs.append(file_output(ident, caption, "captions"))
                    if options.burn_captions:
                        burn_captions(path, caption, cancel_event=event)
                        embedded_count += 1
                        video_output["captions"] = True
                store.update(ident, outputs=outputs)
            manifest["shorts"] = [asdict(c) for c in shorts]
        manifest_path = folder / f"manifest-{revision:02}.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        outputs.append(file_output(ident, manifest_path, "manifest"))
        store.update(
            ident,
            status="complete",
            progress=1,
            outputs=outputs,
            export_revision=revision,
            last_export_options=options.model_dump(),
            caption_status="not_requested"
            if not options.burn_captions
            else "embedded"
            if embedded_count == video_count
            else "partial"
            if embedded_count
            else "unavailable",
            message="Exports ready to preview and download."
            if embedded_count or not options.burn_captions
            else "Video ready. No confident speech was found for captions; add captions in Adjust clips if needed.",
        )
    except Exception as exc:
        store.update(
            ident,
            status="cancelled" if event.is_set() else "failed",
            error=str(exc)[:1000],
            message="Export stopped. You can adjust clips and export again.",
        )


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/speech-model")
def speech_model():
    """Is local speech recognition ready right away? (bundled / downloaded / download)"""
    from .transcript import model_status

    state = model_status("base")
    return {"model": "base", "state": state, "offline": state != "download",
            "note": {"bundled": "Included with the app. Works offline.",
                     "downloaded": "Ready on this PC.",
                     "download": "Downloads once (about 150 MB) the first time a video needs captions."}[state]}


@app.get("/api/health")
def health():
    from . import __version__
    from .paths import APP_DIR, bundled

    return dict(
        application="GTA Clip Studio",
        version=__version__,
        app_dir=str(Path(APP_DIR).resolve()),  # the window only attaches to an engine from its own install
        ffmpeg=bool(bundled("ffmpeg") or shutil.which("ffmpeg")),
        ffprobe=bool(bundled("ffprobe") or shutil.which("ffprobe")),
        speech=importlib.util.find_spec("faster_whisper") is not None,
        director_model=os.environ.get("CLIPSTUDIO_DIRECTOR_MODEL", ""),
    )


@app.get("/api/jobs")
def list_jobs():
    return dict(jobs=store.all())


@app.get("/api/jobs/{ident}")
def read_job(ident):
    return get_job(ident)


def enqueue_source(options: SourceRequest, extra=None):
    source = None
    try:
        if options.source_type == "path":
            source = Path(options.source.strip().strip('"')).expanduser().resolve()
            if not source.is_file():
                raise ValueError(
                    "Video file not found. Use a full local path or upload a file."
                )
            if source.suffix.lower() not in (
                ".mp4",
                ".mkv",
                ".mov",
                ".webm",
                ".avi",
                ".m4v",
                ".ts",
            ):
                raise ValueError("Unsupported video extension.")
        else:
            validate_url(options.source)
        storage.ensure_room(store.work_root, output_folder())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    job = store.create(
        source.name if source else "URL import",
        source_path=str(source) if source else None,
        options=options.model_dump(exclude={"source"}),
        **(extra or {}),
    )
    events[job["id"]] = threading.Event()
    pool.submit(analyze, job["id"], options)
    return job


@app.post("/api/jobs", status_code=202)
def create_job(options: SourceRequest):
    licence_gate()
    job = enqueue_source(options)
    licence_count()
    return job


def enqueue_upload(file, transcribe=True, language="auto", model="base", extra=None, captions_in="roman"):
    if Path(file.filename or "").suffix.lower() not in (
        ".mp4",
        ".mkv",
        ".mov",
        ".webm",
        ".avi",
        ".m4v",
        ".ts",
    ):
        raise HTTPException(400, "Upload a video file.")
    try:
        options = SourceRequest(
            source="uploaded", transcribe=transcribe, language=language, model=model, captions_in=captions_in
        )
    except ValueError:
        raise HTTPException(400, "Invalid speech options")
    try:
        storage.ensure_room(store.work_root, output_folder())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    job = store.create(
        Path(file.filename).name,
        options=options.model_dump(exclude={"source"}),
        **(extra or {}),
    )
    target = store.folder(job["id"]) / ("source" + Path(file.filename).suffix.lower())
    total = 0
    try:
        with target.open("wb") as dest:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > 20 * 1024**3:
                    raise HTTPException(
                        413,
                        "Maximum upload size is 20 GB. Import by local path for larger videos.",
                    )
                dest.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        store.update(job["id"], status="failed", error="Upload failed")
        raise
    finally:
        file.file.close()
    store.update(job["id"], source_path=str(target))
    events[job["id"]] = threading.Event()
    pool.submit(analyze, job["id"], options)
    return store.get(job["id"])


@app.post("/api/upload", status_code=202)
def upload(
    file: UploadFile = File(...),
    transcribe: bool = Form(True),
    language: str = Form("auto"),
    model: str = Form("base"),
):
    return enqueue_upload(file, transcribe, language, model)


def automatic_options(format, captions, recipe=None):
    if recipe:
        return ExportRequest(
            mode="clips",
            short_seconds=int(recipe.get("length") or 30),
            short_count=int(recipe.get("count") or 3),
            burn_captions=captions,
            template=str(recipe.get("template") or "big_captions"),
            design=recipe.get("design") or None,
            formats=recipe_formats(recipe),
        ).model_dump()
    return ExportRequest(
        mode="highlights" if format == "horizontal" else "shorts",
        layout="center",
        burn_captions=captions,
    ).model_dump()


@app.post("/api/projects", status_code=202)
def create_project(options: ProjectRequest):
    if options.from_discovery and options.source_type != "url":
        raise HTTPException(400, "A stream from search must be a video link.")
    section = None
    if options.section_start is not None or options.section_end is not None:
        if options.source_type != "url":
            raise HTTPException(400, "A time range only applies to video links.")
        if options.section_start is None or options.section_end is None:
            raise HTTPException(400, "Pick both a start and an end time.")
        if options.section_end <= options.section_start:
            raise HTTPException(400, "The end time must be after the start time.")
        if options.section_end - options.section_start > 4 * 3600:
            raise HTTPException(400, "Pick a time range of 4 hours or less.")
        section = dict(start=options.section_start, end=options.section_end)
    if options.scan_whole:
        if options.source_type != "url":
            raise HTTPException(400, "Whole-stream scan only works with a video link.")
        # keep a plain range too: if YouTube blocks the hot parts we fall back to it instead of failing
        section = dict(hot=True, fallback=[section["start"], section["end"]] if section else None)
    source_options = SourceRequest(
        **options.model_dump(include=set(SourceRequest.model_fields))
    )
    recipe = None
    if options.recipe is not None:
        try:
            recipe = Recipe(**options.recipe).model_dump()
        except ValueError as exc:
            raise HTTPException(400, f"Check your clip settings: {exc}")
    licence_gate()
    validate_auto_post(recipe)
    if options.live_rolling:
        if options.source_type != "url":
            raise HTTPException(400, "Keep clipping only works with a live stream link.")
        try:
            validate_url(options.source)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        job = store.create(
            "Live stream", source_path=None, options=source_options.model_dump(exclude={"source"}),
            topic=options.topic, recipe=recipe or Recipe().model_dump(), product_format=options.format,
            live=dict(state="starting", recorded=0, windows=0, window=live_window_seconds()),
        )
        events[job["id"]] = threading.Event()
        live_stops[job["id"]] = threading.Event()
        threading.Thread(target=run_live, args=(job["id"], source_options, options.captions),
                         name="clip-live", daemon=True).start()
        licence_count()
        return store.get(job["id"])
    job = enqueue_source(
        source_options,
        dict(
            auto_export=automatic_options(options.format, options.captions, recipe),
            product_format=options.format,
            topic=options.topic,
            section=section,
            recipe=recipe,
        ),
    )
    licence_count()
    return job


def output_folder() -> Path:
    """Where finished clips are saved: the buyer's chosen folder, else Videos\\MasterClip."""
    chosen = storage.chosen_folder()
    if chosen is not None:
        try:
            chosen.mkdir(parents=True, exist_ok=True)
            return chosen
        except OSError:
            pass  # the chosen drive is unplugged: fall back to the default
    try:
        settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        custom = str(settings.get("output_folder") or "").strip()
    except (OSError, ValueError):
        custom = ""
    folder = Path(custom).expanduser() if custom else Path.home() / "Videos" / "MasterClip"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _safe_name(text: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in " -_()'" else " " for ch in text)
    return " ".join(keep.split())[:60] or "clip"


def _open_in_explorer(path: Path):
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 - opens the user's own folder


def _autosave_live(ident, job, source: Path, clip: dict, mixed: bool) -> str | None:
    """Live clipping runs for hours: copy every finished clip into the clips folder straight away,
    so it is on the PC even if the app is closed. A failed copy never stops the recording."""
    try:
        label = _safe_name(job.get("title") or job.get("channel") or "Live")
        target = output_folder() / f"{label} - {ident[:8]}"
        target.mkdir(parents=True, exist_ok=True)
        tag = (" (Short)" if clip.get("kind") == "short" else " (YouTube 16x9)") if mixed else ""
        name = f"{clip.get('moment', clip['index']) + 1:02} {_safe_name(clip.get('title') or 'clip')}{tag}.mp4"
        shutil.copy2(source, target / name)
        return str(target)
    except OSError as exc:
        import logging

        logging.getLogger("clipstudio").warning("live clip %s not copied to the clips folder: %s", ident, exc)
        return None


@app.post("/api/jobs/{ident}/open-saved")
def open_saved(ident):
    """Open the folder live clips are being saved into."""
    folder = get_job(ident).get("saved_to")
    if not folder or not Path(folder).is_dir():
        raise HTTPException(404, "No clips saved yet.")
    _open_in_explorer(Path(folder))
    return dict(folder=folder)


@app.post("/api/jobs/{ident}/save")
def save_clips(ident):
    """Copy the finished clips into the output folder and open it."""
    job = get_job(ident)
    clips = job.get("clips") or []
    if not clips:
        raise HTTPException(400, "Make clips first.")
    target = output_folder() / _safe_name(job.get("title") or "Clips")
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for clip in clips:
        source = store.folder(ident) / clip["file"]
        if source.is_file():
            kinds = {c.get("kind") for c in clips}
            number = clip.get("moment", clip["index"]) + 1 if len(kinds) > 1 else clip["index"] + 1
            tag = (" (Short)" if clip.get("kind") == "short" else " (YouTube 16x9)") if len(kinds) > 1 else ""
            name = f"{number:02} {_safe_name(clip.get('title') or 'clip')}{tag}.mp4"
            shutil.copy2(source, target / name)
            saved.append(name)
    _open_in_explorer(target)
    return dict(folder=str(target), files=saved)


def logs_dir() -> Path:
    return Path(os.environ.get("CLIPSTUDIO_LOGS") or store.root)


class ClientError(BaseModel):
    message: str = Field(default="", max_length=2000)
    where: str = Field(default="", max_length=500)
    stack: str = Field(default="", max_length=6000)


_client_errors = {"count": 0}


@app.post("/api/client-error")
def client_error(body: ClientError):
    """The page reports its own errors here, so they end up in crash.log next to the engine's."""
    import logging
    import time as _time

    _client_errors["count"] += 1
    if _client_errors["count"] > 50:
        return dict(ok=False)
    logging.getLogger("clipstudio.page").error("page error: %s at %s\n%s", body.message, body.where, body.stack)
    try:
        with open(logs_dir() / "crash.log", "a", encoding="utf-8") as log:
            log.write(f"\n=== {_time.strftime('%Y-%m-%d %H:%M:%S')} page error ===\n"
                      f"{body.message}\n{body.where}\n{body.stack}\n")
    except OSError:
        pass
    return dict(ok=True)


@app.get("/api/support")
def support_info():
    from . import __version__

    folder = logs_dir()
    return dict(version=__version__, logs=str(folder), crash_log=str(folder / "crash.log"),
                app_log=str(folder / "app.log"))


@app.post("/api/open-logs")
def open_logs():
    folder = logs_dir()
    _open_in_explorer(folder)
    return dict(folder=str(folder))


class StorageRequest(BaseModel):
    folder: str | None = None


def _storage_info():
    chosen = storage.chosen_folder()
    clips = output_folder()
    free = storage.free_bytes(store.work_root)
    return dict(
        folder=str(chosen) if chosen else None,
        clips_folder=str(clips),
        work_folder=str(store.work_root),
        default_clips=str(Path.home() / "Videos" / "MasterClip"),
        free=storage.gb(free),
        drive=storage.drive_name(store.work_root),
        low=free is not None and free < storage.MIN_FREE_BYTES,
    )


@app.get("/api/storage")
def get_storage():
    return _storage_info()


@app.post("/api/storage")
def set_storage(body: StorageRequest):
    folder = (body.folder or "").strip().strip('"') or None
    try:
        storage.set_folder(folder, store.work_root)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    store.set_work_root(storage.work_root(_DATA))
    _use_temp_in(store.work_root)
    return _storage_info()


submissions = camp.Submissions(_DATA)


class SubmissionBody(BaseModel):
    campaign_id: str | None = Field(default=None, max_length=80)
    clip_name: str | None = Field(default=None, max_length=300)
    posted_url: str | None = Field(default=None, max_length=500)
    posted_at: str | None = Field(default=None, max_length=40)
    views: int | None = Field(default=None, ge=0, le=10**10)


def _campaign_view(fetch=None):
    feed = camp.load_feed(_DATA, fetch=fetch)
    return feed, submissions.summary(feed["campaigns"])


@app.get("/api/campaigns")
def get_campaigns():
    feed, mine = _campaign_view()
    return dict(feed, **mine)


@app.post("/api/campaigns/submissions")
def add_submission(body: SubmissionBody):
    item = submissions.add(**body.model_dump())
    return dict(item=item, **_campaign_view(lambda: None)[1])


@app.put("/api/campaigns/submissions/{sid}")
def update_submission(sid: str, body: SubmissionBody):
    if submissions.update(sid, **body.model_dump()) is None:
        raise HTTPException(404, "That entry is gone.")
    return _campaign_view(lambda: None)[1]


@app.delete("/api/campaigns/submissions/{sid}")
def delete_submission(sid: str):
    if not submissions.delete(sid):
        raise HTTPException(404, "That entry is gone.")
    return _campaign_view(lambda: None)[1]


@app.post("/api/open-output")
def open_output():
    folder = output_folder()
    _open_in_explorer(folder)
    return dict(folder=str(folder))


@app.get("/api/templates")
def list_templates():
    from .design import FONTS, LAYOUTS, TEMPLATES, Design
    from .emoji import emoji_table

    return dict(templates=[*TEMPLATES, *my_templates.all()], fonts=list(FONTS), font_files=FONTS,
                defaults=Design().model_dump(), emoji=emoji_table(), layouts=LAYOUTS)


class MyTemplateRequest(BaseModel):
    name: str = Field(default="", max_length=60)
    design: dict | None = None
    base: str = Field(default="", max_length=40)
    length: int = Field(default=30, ge=5, le=180)


@app.post("/api/my-templates", status_code=201)
def add_my_template(body: MyTemplateRequest):
    if not body.design:
        raise HTTPException(400, "Nothing to save yet.")
    try:
        return my_templates.add(body.name, body.design, body.base, body.length)
    except ValueError as exc:
        raise HTTPException(400, _friendly(exc))


@app.put("/api/my-templates/{ident}")
def update_my_template(ident: str, body: MyTemplateRequest):
    try:
        return my_templates.update(ident, body.name if body.name else None, body.design)
    except KeyError:
        raise HTTPException(404, "That template is gone.")
    except ValueError as exc:
        raise HTTPException(400, _friendly(exc))


@app.delete("/api/my-templates/{ident}")
def delete_my_template(ident: str):
    try:
        my_templates.delete(ident)
    except KeyError:
        raise HTTPException(404, "That template is gone.")
    return dict(ok=True)


def _friendly(exc: Exception) -> str:
    """Pydantic's 'String should have at least 10 characters' -> something a buyer understands."""
    text = str(exc)
    if "validation error" in text:
        return "Some settings in this template look wrong. Press Reset and try again."
    return text


class RestyleRequest(BaseModel):
    formats: list[RecipeFormat] = Field(min_length=1, max_length=2)


def restyle_clips(ident, formats: list[dict]):
    """The buyer went back to Design after the clips were made: same moments and titles, new look."""
    from .render import render_clip

    event = events[ident]
    folder = store.folder(ident)
    job = store.get(ident)
    try:
        transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        by_kind = {f["kind"]: f for f in formats}
        clips = job["clips"]
        for n, clip in enumerate(clips):
            if event.is_set():
                raise RuntimeError("Cancelled")
            fmt = by_kind.get(clip.get("kind") or "short") or formats[0]
            design = job_design(job, fmt.get("template") or "big_captions", fmt.get("design"))
            if not (job.get("options") or {}).get("transcribe", True):
                design.caption.style = "none"
            progress(ident, "rendering", n / len(clips), f"Restyling clip {n + 1} of {len(clips)}")
            segments, _ = _clip_words(transcript, clip["start"], clip["end"])
            version = int(clip.get("version", 0)) + 1
            name = f"clip-{job.get('export_revision', 1):02}-{n + 1:02}-v{version}" + (
                f"-{clip['kind']}" if len(by_kind) > 1 else "") + ".mp4"
            render_clip(job["source_path"], clip["start"], clip["end"], folder / name, design,
                        segments, clip.get("title") or "", cancel_event=event)
            old = folder / clip["file"]
            clip.update(file=name, version=version, url=f"/api/jobs/{ident}/files/{name}",
                        design=design.model_dump(), template=fmt.get("template"))
            store.update(ident, clips=clips, outputs=[file_output(ident, folder / x["file"], "clip") for x in clips])
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
        recipe = dict(job.get("recipe") or {})
        recipe["formats"] = formats
        store.update(ident, status="complete", progress=1, clips=clips, recipe=recipe,
                     message=f"{len(clips)} clips restyled")
    except Exception as exc:
        store.update(ident, status="cancelled" if event.is_set() else "complete", error=str(exc)[:1000],
                     message="Restyling stopped. The clips that were done keep the new look.")


@app.post("/api/jobs/{ident}/restyle", status_code=202)
def restyle(ident, body: RestyleRequest):
    with busy:
        job = get_job(ident)
        if job["status"] in ACTIVE:
            raise HTTPException(409, "Wait for the current step to finish.")
        if not job.get("clips"):
            raise HTTPException(400, "Make clips first.")
        formats = [f.model_dump() for f in body.formats]
        for fmt in formats:
            try:
                job_design(job, fmt["template"], fmt.get("design"))
            except ValueError as exc:
                raise HTTPException(400, f"Check the design: {_friendly(exc)}")
        events[ident] = threading.Event()
        # A new design for the whole video replaces "Use for all clips" framing from the clip editor.
        store.update(ident, status="rendering", progress=0.01, error=None, message="Restyling your clips",
                     framing=None)
        pool.submit(restyle_clips, ident, formats)
        return store.get(ident)


@app.post("/api/jobs/{ident}/clips/{index}", status_code=202)
def edit_clip(ident, index: int, edit: ClipEdit):
    from .design import Design

    with busy:
        job = get_job(ident)
        if job["status"] in ACTIVE:
            raise HTTPException(409, "Wait for the current step to finish.")
        clips = job.get("clips") or []
        if not 0 <= index < len(clips):
            raise HTTPException(404, "Clip not found")
        if edit.end <= edit.start or edit.end > job["duration"] + 0.05:
            raise HTTPException(400, "The clip must end after it starts and stay inside the video.")
        if edit.end - edit.start > 600:
            raise HTTPException(400, "Keep one clip under 10 minutes.")
        try:
            Design(**edit.design)
        except ValueError as exc:
            raise HTTPException(400, f"Check the clip design: {exc}")
        events[ident] = threading.Event()
        store.update(ident, status="rendering", progress=0.05, error=None,
                     message=f"Updating clip {index + 1}")
        pool.submit(rerender_clip, ident, index, edit)
        return store.get(ident)


@app.post("/api/inspect")
def inspect_link(options: InspectRequest):
    """Look at a link before downloading: length, live, most-replayed part."""
    import tempfile

    from .media import MediaError
    from .sources import inspect_url

    try:
        with tempfile.TemporaryDirectory(prefix="clipstudio-inspect-") as folder:
            return inspect_url(options.url.strip(), folder)
    except (ValueError, MediaError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/projects/upload", status_code=202)
def upload_project(
    file: UploadFile = File(...),
    format: Literal["horizontal", "vertical"] = Form("horizontal"),
    captions: bool = Form(True),
    language: str = Form("auto"),
    model: str = Form("base"),
    recipe: str = Form(""),
    captions_in: str = Form("roman"),
):
    licence_gate()
    parsed = None
    if recipe.strip():
        try:
            parsed = Recipe(**json.loads(recipe)).model_dump()
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"Check your clip settings: {exc}")
    validate_auto_post(parsed)
    job = enqueue_upload(
        file,
        True,
        language,
        model,
        dict(
            auto_export=automatic_options(format, captions, parsed),
            product_format=format,
            recipe=parsed,
        ),
        captions_in,
    )
    licence_count()
    return job


@app.post("/api/discover", status_code=202)
def start_discovery(options: DiscoveryRequest):
    try:
        return searches.create(options.topic, options.max_results)
    except ValueError as exc:
        raise HTTPException(429, str(exc))


@app.get("/api/discover/{ident}")
def read_discovery(ident):
    try:
        return searches.get(ident)
    except KeyError:
        raise HTTPException(404, "Search not found")


@app.post("/api/discover/{ident}/cancel")
def cancel_discovery(ident):
    try:
        return searches.cancel(ident)
    except KeyError:
        raise HTTPException(404, "Search not found")


@app.get("/api/jobs/{ident}/captions")
def read_captions(ident):
    job = get_job(ident)
    path = store.folder(ident) / "transcript.json"
    return dict(
        segments=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else [],
        duration=job.get("duration"),
    )


@app.put("/api/jobs/{ident}/captions")
def save_captions(ident, options: CaptionsRequest):
    with busy:
        job = get_job(ident)
        if job["status"] in ACTIVE:
            raise HTTPException(
                409, "Wait for processing to finish before editing captions."
            )
        if not job.get("duration"):
            raise HTTPException(400, "Import a video first.")
        from .transcript import parse_transcript

        try:
            cues = parse_transcript(json.dumps(options.segments), "json")
            if any(c["end"] > job["duration"] + 0.05 for c in cues):
                raise ValueError("Caption times must stay inside the original video.")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        path = store.folder(ident) / "transcript.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        lookup = {(c["start"], c["end"], c["text"]): c for c in previous}
        final = []
        for cue in cues:
            old = lookup.get((cue["start"], cue["end"], cue["text"]))
            if old and old.get("words"):
                cue["words"] = old["words"]
            else:
                cue.pop("words", None)
            final.append(cue)
        final.sort(key=lambda c: c["start"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return store.update(
            ident,
            caption_revision=job.get("caption_revision", 0) + 1,
            message="Caption changes saved. Rebuild to apply them.",
        )


@app.post("/api/jobs/{ident}/export", status_code=202)
def start_export(ident, options: ExportRequest):
    with busy:
        job = get_job(ident)
        if job["status"] in ACTIVE:
            raise HTTPException(409, "This job is still processing.")
        if not job.get("duration") or (not job.get("candidates") and not options.clips):
            raise HTTPException(
                400, "Analyze a video and select moments before exporting."
            )
        if options.clips is not None:
            if not options.clips:
                raise HTTPException(400, "Select at least one moment.")
            for c in options.clips:
                if c.end <= c.start or c.end > job["duration"] + 0.05:
                    raise HTTPException(
                        400, "Clip times must be inside the source and end after start."
                    )
        events[ident] = threading.Event()
        store.update(
            ident, status="rendering", progress=0, error=None, message="Export queued"
        )
        pool.submit(export, ident, options)
        return store.get(ident)


@app.post("/api/jobs/{ident}/transcript", status_code=202)
def upload_transcript(ident, file: UploadFile = File(...)):
    with busy:
        job = get_job(ident)
        if job["status"] in ACTIVE:
            raise HTTPException(
                409, "Wait for processing to finish before replacing the transcript."
            )
        if not job.get("duration"):
            raise HTTPException(400, "Analyze the video first.")
        from .transcript import parse_transcript

        try:
            raw = file.file.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                raise ValueError("Transcript limit is 5 MB.")
            segments = parse_transcript(
                raw.decode("utf-8-sig"),
                format=Path(file.filename or "").suffix.lstrip("."),
            )
            if any(s["end"] > job["duration"] + 0.05 for s in segments):
                raise ValueError("Transcript cues extend past the end of this video.")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, str(exc))
        finally:
            file.file.close()
        options = SourceRequest(source=job["source_path"], transcribe=False)
        events[ident] = threading.Event()
        store.update(
            ident,
            status="queued",
            progress=0,
            error=None,
            message="Updating speech ranking",
        )
        pool.submit(analyze, ident, options, segments)
        return store.get(ident)


@app.post("/api/jobs/{ident}/cancel")
def cancel(ident):
    job = get_job(ident)
    if ident in events and job["status"] in ACTIVE:
        events[ident].set()
        store.update(
            ident,
            message="Cancellation requested; waiting for current operation to stop.",
        )
    return store.get(ident)


@app.get("/api/jobs/{ident}/source")
def source_file(ident):
    job = get_job(ident)
    path = Path(job.get("source_path") or "")
    if not path.is_file():
        raise HTTPException(404, "Source not available")
    return FileResponse(path)


@app.get("/api/jobs/{ident}/files/{name}")
def output_file(ident, name):
    job = get_job(ident)
    known = {item["name"] for item in job.get("outputs", [])} | {v["file"] for v in job.get("long_videos") or []}
    if name not in known:
        raise HTTPException(404, "Output not found")
    path = store.folder(ident) / name
    if not path.is_file():
        raise HTTPException(404, "Output file is missing")
    return FileResponse(path, filename=name)


# ---------------- posting (buyer's own Zernio account) ----------------
from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

from .posting import Poster  # noqa: E402
from .zernio import ZernioError  # noqa: E402

poster = Poster(store.root)


def _zernio(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except ZernioError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:  # noqa: BLE001 - vault errors etc. as plain words
        raise HTTPException(400, str(error) or "Something went wrong.") from error


class KeyRequest(BaseModel):
    key: str = Field(min_length=1, max_length=400)


class PostSettings(BaseModel):
    accounts: dict[str, str | None] | None = None
    per_day: int | None = Field(default=None, ge=1, le=6)
    slots: list[str] | None = Field(default=None, max_length=6)
    timezone: str | None = Field(default=None, max_length=60)
    caption: str | None = Field(default=None, max_length=2000)
    hashtags: str | None = Field(default=None, max_length=500)
    dm: dict | None = None
    rotate: bool | None = None
    short_platforms: list[Literal["instagram", "youtube"]] | None = Field(default=None, max_length=2)
    long_youtube: bool | None = None
    cadence: Literal["hourly", "daily"] | None = None
    clips_per_hour: int | None = Field(default=None, ge=1, le=60)


class QueueRequest(BaseModel):
    job_id: str
    indexes: list[int] | None = None
    long_index: int | None = None  # a long video -> YouTube only
    # now = straight away, at = the buyer's own time (+ gap between clips), slots = daily times
    when: Literal["slots", "now", "at"] = "slots"
    at: str | None = Field(default=None, max_length=20)  # "2026-11-22T16:30", wall clock in `zone`
    zone: str | None = Field(default=None, max_length=64)  # the browser's time zone
    gap_minutes: int = Field(default=0, ge=0, le=10080)


class QueueEdit(BaseModel):
    caption: str | None = Field(default=None, max_length=2200)
    when: str | None = Field(default=None, max_length=20)


@app.get("/api/post/status")
def post_status():
    return poster.status()


@app.post("/api/post/key")
def post_key(body: KeyRequest):
    found = _zernio(poster.set_key, body.key)
    return {**poster.status(), **found}


class ExtraKeyRequest(BaseModel):
    key: str = Field(min_length=1, max_length=400)
    label: str = Field(default="", max_length=40)


@app.post("/api/post/keys")
def post_add_extra_key(body: ExtraKeyRequest):
    return _zernio(poster.add_extra_key, body.key, body.label)


@app.delete("/api/post/keys/{slot}")
def post_remove_extra_key(slot: int):
    return poster.remove_extra_key(slot)


@app.post("/api/post/keys/{slot}/{state}")
def post_toggle_extra_key(slot: int, state: Literal["on", "off"]):
    return poster.toggle_extra_key(slot, state == "on")


@app.delete("/api/post/key")
def post_forget_key():
    poster.forget_key()
    return poster.status()


@app.get("/api/post/accounts")
def post_accounts():
    return _zernio(poster.accounts)


@app.post("/api/post/connect/{platform}")
def post_connect(platform: str, request: Request):
    redirect = str(request.base_url).rstrip("/") + "/connected"
    url = _zernio(poster.connect_url, platform, redirect)
    if not os.environ.get("CLIPSTUDIO_ZERNIO_API"):  # QA runs against a fake Zernio: no real tabs
        import webbrowser

        webbrowser.open(url)
    return {"url": url}


@app.get("/connected", response_class=HTMLResponse)
def connected_page():
    return (
        "<!doctype html><meta charset=utf-8><title>Connected</title>"
        "<body style=\"font:18px system-ui;background:#FBF6EF;color:#2A211C;display:grid;place-items:center;height:90vh\">"
        "<div style=\"background:#fff;padding:40px 48px;border-radius:24px;text-align:center;box-shadow:0 10px 40px #0001\">"
        "<div style=\"font-size:48px\">✅</div><h2>Connected!</h2><p>You can close this tab and go back to MasterClip.</p></div>"
    )


@app.post("/api/post/settings")
def post_settings(body: PostSettings):
    poster.save_settings(body.model_dump(exclude_none=True))
    return poster.status()


@app.get("/api/post/queue")
def post_queue(refresh: bool = False):
    items = poster.refresh() if refresh else poster.queue()
    return {"items": items, "platform_status": poster.platform_status(), "timezone": poster.settings()["timezone"]}


@app.post("/api/post/queue")
def post_add(body: QueueRequest):
    job = get_job(body.job_id)
    if body.long_index is not None:
        videos = job.get("long_videos") or []
        if not 0 <= body.long_index < len(videos):
            raise HTTPException(404, "Video not found")
        video = videos[body.long_index]
        item = {"file": store.folder(body.job_id) / video["file"], "title": video["title"], "caption": video["description"],
                "only": ["youtube"], "kind": "long", "job_id": body.job_id, "index": f"long-{body.long_index}"}
        added = _zernio(poster.add, [item], body.when, body.at, body.gap_minutes, body.zone)
        return {"added": added, "items": poster.queue()}
    clips = job.get("clips") or []
    wanted = [c for c in clips if body.indexes is None or c["index"] in body.indexes]
    if not wanted:
        raise HTTPException(400, "Make clips first.")
    items = []
    for clip in wanted:
        path = store.folder(body.job_id) / clip["file"]
        if path.is_file():
            item = {"file": path, "title": clip.get("title") or "", "streamer": job.get("channel") or "",
                    "job_id": body.job_id, "index": clip["index"], "kind": clip.get("kind") or "short",
                    "posting_slot": (job.get("recipe") or {}).get("posting_slot", 0)}
            if item["kind"] == "long":
                item["only"] = ["youtube"]
            items.append(item)
    added = _zernio(poster.add, items, body.when, body.at, body.gap_minutes, body.zone)
    return {"added": added, "items": poster.queue()}


@app.delete("/api/jobs/{ident}/clips/{index}")
def delete_clip(ident: str, index: int):
    """The cross on a clip card: drop that clip (and its file) from the job."""
    job = get_job(ident)
    clips = job.get("clips") or []
    gone = next((c for c in clips if c["index"] == index), None)
    if not gone:
        raise HTTPException(404, "That clip is already gone.")
    folder = store.folder(ident)
    try:
        (folder / gone["file"]).unlink(missing_ok=True)
    except OSError:
        pass
    left = [c for c in clips if c["index"] != index]
    store.update(ident, clips=left, outputs=[file_output(ident, folder / x["file"], "clip") for x in left])
    return store.get(ident)


@app.post("/api/post/queue/{ident}")
def post_edit(ident: str, body: QueueEdit):
    _zernio(poster.edit, ident, body.caption, body.when)
    return {"items": poster.queue()}


@app.post("/api/post/queue/{ident}/retry")
def post_retry(ident: str):
    _zernio(poster.retry, ident)
    return {"items": poster.queue()}


@app.delete("/api/post/queue/{ident}")
def post_remove(ident: str):
    _zernio(poster.remove, ident)
    return {"items": poster.queue()}


@app.get("/api/post/queue/{ident}/thumb")
def post_thumb(ident: str):
    path = poster.folder / f"{ident}.jpg"
    if not ident.isalnum() or not path.is_file():
        raise HTTPException(404, "No picture")
    return FileResponse(path)


@app.get("/api/post/queue/{ident}/video")
def post_video(ident: str):
    item = next((i for i in poster.queue() if i["id"] == ident), None)
    path = poster.folder / (item or {}).get("file", "-")
    if not item or not path.is_file():
        raise HTTPException(404, "Video not found")
    return FileResponse(path)


# ---------------- long YouTube videos (compilation + full length) ----------------
class LongRequest(BaseModel):
    kind: Literal["compilation", "full"] = "compilation"
    moments: Literal["clips", "best"] = "best"  # my clips (with my titles) or find the best ones
    count: int = Field(default=10, ge=2, le=30)
    length: int = Field(default=40, ge=12, le=180)
    order: Literal["time", "countdown"] = "time"
    captions: bool = True
    intro: bool = True
    title: str = Field(default="", max_length=100)


LONG_DESIGN = {
    "aspect": "16:9", "layout": "full", "fill": "blur", "credit_on": False,
    "caption": {"style": "classic", "y": 0.8, "size": 54, "uppercase": False, "font": "Poppins ExtraBold", "outline": 5},
    "title": {"style": "white_box", "y": 0.1, "size": 56, "seconds": 4, "font": "Poppins ExtraBold"},
}


def _long_title(job, body: LongRequest) -> str:
    if body.title.strip():
        return body.title.strip()
    who = (job.get("channel") or "").strip()
    if body.kind == "full":
        return (job.get("title") or f"{who} full stream").strip()[:100]
    return (f"{who} best moments" if who else "Best moments").strip()[:100]


def make_long(ident, body: LongRequest):
    from .longform import Moment, make_compilation, make_full
    from .shorts import select_shorts
    from .titles import suggest_titles

    event = events[ident]
    folder = store.folder(ident)
    job = store.get(ident)
    try:
        transcript = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        design = job_design(job, "big_captions", LONG_DESIGN)
        if not body.captions:
            design.caption.style = "none"
        title = _long_title(job, body)
        streamer = job.get("channel") or ""
        footer = f"Clips from @{streamer.lstrip('@')}'s stream." if streamer else ""
        revision = len(job.get("long_videos") or []) + 1
        name = f"{'compilation' if body.kind == 'compilation' else 'full'}-{revision:02}.mp4"

        def tell(amount, message):
            progress(ident, "rendering", 0.03 + 0.95 * amount, message)

        candidates = [Candidate(**{k: c[k] for k in ("start", "end", "score", "reasons", "core_start", "core_end") if k in c}) for c in job.get("candidates") or []]
        if body.kind == "compilation":
            if body.moments == "clips" and job.get("clips"):
                # One entry per moment: a moment made as both vertical and 16:9 is two clips, not two chapters.
                by_moment = {}
                for c in job["clips"]:
                    key = (round(float(c["start"]), 2), round(float(c["end"]), 2))
                    if key not in by_moment or c.get("kind") == "long":
                        by_moment[key] = c
                picked = []
                for c in by_moment.values():
                    file = folder / str(c.get("file") or "")
                    ready = file if c.get("kind") == "long" and file.is_file() and body.captions else None
                    picked.append((c["start"], c["end"], c.get("title") or "", c.get("score", 0), ready))
            else:
                from .boost import Booster

                boosted = Booster(store.root).on
                shortlist = select_shorts(candidates, transcript, job["duration"], length=body.length,
                                          count=min(40, body.count * 2) if boosted else body.count)
                if boosted:
                    tell(0, "✨ AI boost is choosing the best moments")
                found, ai_titles, ai_note = boost_pick(job, transcript, shortlist, body.count)
                if ai_note:
                    store.update(ident, ai_note=ai_note)
                picked = [(c.start, c.end, ai_titles.get(id(c), ""), c.score, None) for c in found]
            if not picked:
                raise RuntimeError("No clear moments found. Try a longer part of the stream.")
            picked.sort(key=(lambda m: m[3]) if body.order == "countdown" else (lambda m: m[0]))
            moments, ready_parts = [], []
            for n, (start, end, own_title, _score, ready) in enumerate(picked):
                ready_parts.append(ready)
                segments, text = _clip_words(transcript, start, end)
                used = {m.title.split(" ", 1)[-1] if body.order == "countdown" else m.title for m in moments}
                label = own_title or (suggest_titles(text, streamer, avoid=used, context=_title_context(job)) or ["Moment"])[0]
                if body.order == "countdown":
                    label = f"#{len(picked) - n} {label}"
                moments.append(Moment(start, end, label, segments))
            tell(0, "Making the intro")
            result = make_compilation(job["source_path"], moments, folder / name, design, title,
                                      intro=body.intro, footer=footer, cancel_event=event, progress=tell,
                                      ready=ready_parts)
        else:
            segments, _ = _clip_words(transcript, 0, job["duration"])
            found = select_shorts(candidates, transcript, job["duration"], length=30, count=12)
            chapters = []
            for c in sorted(found, key=lambda c: c.start):
                _, text = _clip_words(transcript, c.start, c.end)
                used = {label for _, label in chapters}
                chapters.append((c.start, (suggest_titles(text, streamer, avoid=used, reasons=c.reasons, context=_title_context(job)) or ["Moment"])[0]))
            chapters = [(0.0, "Start")] + chapters
            tell(0.05, "Making the full video. Long streams take a few minutes.")
            result = make_full(job["source_path"], 0, job["duration"], segments, folder / name, design, title,
                               chapters, footer=footer, cancel_event=event)
        (folder / f"{Path(name).stem}-description.txt").write_text(result["description"], encoding="utf-8")
        videos = list(job.get("long_videos") or [])
        videos.append(dict(kind=body.kind, file=name, url=f"/api/jobs/{ident}/files/{name}", title=title, **result))
        pending = {**videos[-1], "kind": "long", "index": f"long-{len(videos) - 1}", "caption": result["description"]}
        auto_post_clip(ident, job, pending)
        for field in ("posting_ids", "posting_note"):
            if field in pending:
                videos[-1][field] = pending[field]
        outputs = [o for o in job.get("outputs", []) if o["name"] != name] + [file_output(ident, folder / name, body.kind)]
        store.update(ident, status="complete", progress=1, long_videos=videos, outputs=outputs,
                     message="Your YouTube video is ready")
    except Exception as exc:
        store.update(ident, status="cancelled" if event.is_set() else "complete", error=str(exc)[:1000],
                     message="Making the long video stopped. Try again." if not event.is_set() else "Cancelled")


@app.post("/api/jobs/{ident}/long", status_code=202)
def start_long(ident, body: LongRequest):
    with busy:
        job = get_job(ident)
        if job["status"] in ACTIVE:
            raise HTTPException(409, "Wait for the current step to finish.")
        if not (store.folder(ident) / "transcript.json").is_file() or not job.get("source_path"):
            raise HTTPException(400, "Make clips from this video first.")
        if body.moments == "clips" and not job.get("clips"):
            body.moments = "best"
        events[ident] = threading.Event()
        store.update(ident, status="rendering", progress=0.02, error=None,
                     message="Making your YouTube video")
        pool.submit(make_long, ident, body)
        return store.get(ident)


# ---------------- AI boost settings ----------------
class BoostRequest(BaseModel):
    mode: Literal["off", "api", "claude", "codex", "gemini", "gemini_api", "local"] = "off"
    key: str | None = Field(default=None, max_length=300)


class AppPathRequest(BaseModel):
    name: Literal["claude", "codex", "gemini", "ollama"]
    path: str = Field(default="", max_length=600)


@app.post("/api/ai/path")
def ai_app_path(body: AppPathRequest):
    """Buyer tells us where Claude/Codex/Gemini is when we can't find it by ourselves."""
    from .boost import BoostError, set_custom_path

    try:
        set_custom_path(body.name, body.path)
    except BoostError as exc:
        raise HTTPException(400, str(exc))
    return _booster().status()


@app.post("/api/ai/local-model")
def ai_local_model():
    from .boost import BoostError, pull_local_model

    try:
        return pull_local_model()
    except BoostError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/ai/local-model")
def ai_local_model_status():
    from .boost import pull_status

    return pull_status()


def _booster():
    from .boost import Booster

    return Booster(store.root)


@app.get("/api/ai")
def ai_status():
    return _booster().status()


@app.post("/api/ai")
def ai_save(body: BoostRequest):
    from .boost import BoostError

    try:
        return _booster().save(body.mode, body.key)
    except Exception as error:  # noqa: BLE001 - BoostError / vault errors in plain words
        raise HTTPException(400, str(error) if isinstance(error, (BoostError, RuntimeError)) else "Couldn't save that.") from error


@app.delete("/api/ai/key")
def ai_forget():
    _booster().forget_key()
    return _booster().status()


@app.post("/api/ai/test")
def ai_test():
    from .boost import BoostError

    try:
        return {"ok": True, "title": _booster().test()}
    except BoostError as error:
        raise HTTPException(400, str(error)) from error


# ---------------- rolling live capture ----------------
import time  # noqa: E402
from dataclasses import replace as _replace  # noqa: E402

live_stops: dict[str, threading.Event] = {}
live_recorders: dict = {}
live_chats: dict = {}
live_clip_now: set[str] = set()


def _live_chat(url, clock_start):
    """Start reading the Twitch chat of a live channel link (None for other sites or when the
    buyer turned chat off). Only the channel name is sent to Twitch."""
    from .livechat import LiveChat, channel_login

    if os.environ.get("CLIPSTUDIO_NO_CHAT") or os.environ.get("CLIPSTUDIO_LIVE_FAKE"):
        return None
    login = channel_login(url)
    return LiveChat(login, clock_start).start() if login else None


def _mmss(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02}"


def live_window_seconds() -> int:
    try:
        return max(20, min(900, int(os.environ.get("CLIPSTUDIO_LIVE_WINDOW", "60"))))
    except ValueError:
        return 60


def _live_recorder(ident, options, folder):
    from .live import LiveRecorder
    from .sources import MediaError, choose_live_inputs, fetch_metadata, live_record_height, ytdlp_command

    fake = os.environ.get("CLIPSTUDIO_LIVE_FAKE")  # QA only: play a local file as if it were live
    if fake:
        return LiveRecorder.from_file(fake, folder), "Test live stream", "Test streamer"
    meta = fetch_metadata(ytdlp_command(), options.source, folder, events[ident])
    if not meta.get("is_live"):
        raise MediaError("This stream isn't live right now. Go back and pick a part of the past stream instead.")
    try:
        video, audio = choose_live_inputs(meta, live_record_height())
    except MediaError:
        video, audio = choose_live_inputs(meta)
    return (LiveRecorder.from_stream(video, audio, meta, folder),
            meta.get("title") or "Live stream", meta.get("channel") or meta.get("uploader") or "")


def _live_state(ident, **values):
    live = dict(store.get(ident).get("live") or {})
    live.update(values)
    store.update(ident, live=live)


def _live_window(ident, source, start, end, number, transcript, found, clips, recipe, captions, options,
                 chat=None, count=None, clip_now=False):
    """Analyse [start, end) of the growing recording and render its best clips.
    `chat` is the live chat of this stretch (times relative to `start`). `count` overrides how many
    clips to make (a short stretch closed early by a chat spike makes fewer). `clip_now` = the
    buyer pressed Clip now: the last stretch becomes a clip whatever the scores say."""
    from .boost import Booster
    from .live import cut_window, shift
    from .moments import sentences, shape_clip
    from .render import render_clip
    from .shorts import select_shorts
    from .titles import suggest_titles

    event = events[ident]
    folder = store.folder(ident)
    work = folder / "live"
    work.mkdir(exist_ok=True)
    minute = f"{_mmss(start)}–{_mmss(end)}"
    store.update(ident, message=f"Finding moments in {minute}")
    chunk = cut_window(source, start, end - start, work / f"window-{number:03}.mp4")
    info = probe_media(chunk)
    job = store.get(ident)
    if not job.get("cam_roi"):
        try:
            from .facecam import detect_facecam

            store.update(ident, cam_roi=detect_facecam(chunk))
        except Exception:
            pass
    if not job.get("media"):
        try:
            store.update(ident, media=asdict(probe_media(source)))
        except Exception:
            pass
    audio = work / f"window-{number:03}.wav"
    local = []
    if info.has_audio:
        extract_audio(chunk, audio, timeout=1800, cancel_event=event)
        if options.transcribe:
            store.update(ident, message=f"Listening to {minute}")
            try:
                from .transcript import transcribe

                heard = {}
                # The first stretch finds the language; later stretches keep it (no flip-flopping).
                spoken = job.get("spoken_language") if options.language == "auto" else options.language
                local = transcribe(audio, options.model, spoken, cancel_event=event, info=heard,
                                   task="translate" if getattr(options, "captions_in", "spoken") == "en" else "transcribe")
                if heard.get("language") and not job.get("spoken_language"):
                    store.update(ident, spoken_language=heard["language"])
            except Exception:
                local = []
    if event.is_set():
        raise RuntimeError("Cancelled")
    if getattr(options, "captions_in", "roman") == "roman":
        from .hinglish import romanize_segments

        local = romanize_segments(local)
    transcript.extend(shift(local, start))
    (folder / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    result = analyze_media(chunk, transcript_segments=local, audio_path=audio if info.has_audio else None,
                           cancel_event=event, context_seconds=28, chat=chat or None)
    moments = [
        _replace(c, start=c.start + start, end=min(c.end + start, end),
                 core_start=None if c.core_start is None else c.core_start + start,
                 core_end=None if c.core_end is None else min(c.core_end + start, end))
        for c in result.candidates
    ]
    found.extend(moments)
    store.update(ident, duration=end, candidates=serialize_candidates(found, transcript))
    job = store.get(ident)
    boosted = Booster(store.root).on
    length = int(recipe.get("length") or 30)
    count = int(count if count is not None else 1)
    forced = []
    if clip_now:
        # the moment just happened: keep the last few seconds as the payoff, setup before it
        s, e = shape_clip(max(start, end - 12), max(start, end - 2), sentences(transcript), end, length)
        forced = [Candidate(max(start, s), e, 1.0, ["You pressed Clip now"], max(start, end - 12), end - 2)]
        count = max(0, count - 1)
    shortlist = select_shorts(moments, transcript, end, length=length,
                              count=min(30, count * 3) if boosted else count) if count else []
    shortlist = [c for c in shortlist if not any(c.start < f.end and c.end > f.start for f in forced)]
    picked, ai_titles, ai_note = boost_pick(job, transcript, shortlist, count) if shortlist else ([], {}, "")
    picked = forced + list(picked)
    designs = job_designs(job, captions)
    moments_before = len({x.get("moment", x["index"]) for x in clips})
    for i, c in enumerate(picked):
        store.update(ident, message=f"Making clip {i + 1} of {len(picked)} from {minute}")
        segments, text = _clip_words(transcript, c.start, c.end)
        used = {x["title"] for x in clips}
        suggestions = suggest_titles(text, job.get("channel", ""), avoid=used, reasons=c.reasons,
                                     context=_title_context(job))
        if id(c) in ai_titles:
            suggestions = [ai_titles[id(c)]] + [t for t in suggestions if t != ai_titles[id(c)]][:2]
        fresh = [t for t in suggestions if t not in used]
        title = (fresh or suggestions or [""])[0]
        suggestions = [title] + [t for t in suggestions if t != title][:2]
        for kind, template, design in designs:
            if event.is_set():
                raise RuntimeError("Cancelled")
            name = f"live-{number:03}-{i + 1:02}" + (f"-{kind}" if len(designs) > 1 else "") + ".mp4"
            render_clip(str(source), c.start, c.end, folder / name, design, segments, title, cancel_event=event)
            clips.append(dict(
                index=len(clips), moment=moments_before + i, kind=kind, start=round(c.start, 2), end=round(c.end, 2),
                score=c.score, file=name, url=f"/api/jobs/{ident}/files/{name}", title=title, suggestions=suggestions,
                text=text[:600], design=design.model_dump(), template=template, window=number,
            ))
            saved_to = _autosave_live(ident, job, folder / name, clips[-1], len(designs) > 1)
            auto_post_clip(ident, job, clips[-1])
            store.update(ident, clips=list(clips),
                         outputs=[file_output(ident, folder / x["file"], "clip") for x in clips],
                         **({"ai_note": ai_note} if ai_note else {}),
                         **({"saved_to": saved_to} if saved_to else {}))
    for leftover in (chunk, audio):
        leftover.unlink(missing_ok=True)
    return len(picked)


def run_live(ident, options, captions):
    from .live import finish_recording

    event, stop = events[ident], live_stops[ident]
    folder = store.folder(ident)
    job = store.get(ident)
    recipe, window = job["recipe"], job["live"]["window"]
    transcript, found, clips = [], [], []
    recorder = None
    (folder / "transcript.json").write_text("[]", encoding="utf-8")
    try:
        store.update(ident, status="live", progress=0, message="Connecting to the live stream")
        recorder, title, channel = _live_recorder(ident, options, folder)
        store.update(ident, title=title, channel=channel[:80], source_path=str(recorder.target))
        live_recorders[ident] = recorder
        recorder.start()
        chat = _live_chat(options.source, recorder.started)
        if chat:
            live_chats[ident] = chat
        _live_state(ident, state="recording", started=time.time(), chat="connecting" if chat else "none")
        wait = f"{window // 60} min" if window >= 60 else f"{window} seconds"
        store.update(ident, message=f"Recording. The first clips come after {wait}."
                     + (" Big chat reactions get clipped right away." if chat else ""))
        done_to, number = 0.0, 0
        delay = None  # seconds between starting the recorder and its first written frame
        spike_end = None
        per_window = int(recipe.get("count") or 3)
        while True:
            if event.is_set():
                raise RuntimeError("Cancelled")
            if stop.is_set() and recorder.running:
                _live_state(ident, state="stopping")
                store.update(ident, message="Stopping. Finishing the last few minutes…")
                recorder.stop()
            written = recorder.written()
            ended = not recorder.running
            values = dict(recorded=round(written, 1))
            if chat:
                if delay is None and written > 0:
                    delay = max(0.0, chat.now() - written - 2.0)
                spike = chat.new_spike()
                if spike is not None and delay is not None and spike_end is None and spike - delay > done_to:
                    # chat reacts after the moment: close the stretch a little after the spike
                    spike_end = spike - delay + 12
                meter = chat.meter()
                values.update(chat=meter["status"], hype=meter["hype"], hype_label=meter["label"],
                              chat_per_min=meter["per_min"])
            clip_now = ident in live_clip_now and recorder.running
            if clip_now:
                values["clip_now"] = True
            _live_state(ident, **values)
            if spike_end is not None:
                spike_end = max(spike_end, done_to + 40)  # never a stretch shorter than 40 s
            spike_ready = spike_end is not None and written >= spike_end
            now_ready = clip_now and written - done_to >= 15
            if written - done_to >= window or (ended and written - done_to >= 20) or spike_ready or now_ready:
                if ended:
                    end, count, reason = written, None, None
                elif now_ready:
                    end, count, reason = written, max(1, round(per_window * (written - done_to) / window)), "now"
                elif spike_ready:
                    end, count, reason = min(spike_end, done_to + window), max(1, round(per_window * (spike_end - done_to) / window)), "spike"
                else:
                    end, count, reason = done_to + window, None, None
                live_clip_now.discard(ident)
                spike_end = None
                number += 1
                _live_state(ident, state="clipping" if recorder.running else "stopping", clip_now=False)
                if reason == "spike":
                    store.update(ident, message="Chat just went wild. Clipping that moment…")
                elif reason == "now":
                    store.update(ident, message="Clipping the last few seconds…")
                window_chat = chat.between(done_to, end, delay or 0.0) if chat else None
                _live_window(ident, recorder.target, done_to, end, number, transcript, found, clips,
                             recipe, captions, options, chat=window_chat, count=count, clip_now=reason == "now")
                done_to = end
                _live_state(ident, windows=number, clipped=round(end, 1),
                            state="recording" if recorder.running else "stopping")
                if recorder.running:
                    store.update(ident, message=f"Recording. {len(clips)} clips so far.")
                continue
            if ended:
                break
            time.sleep(1)
        recorder.close()
        total = recorder.written()
        recorder.progress_file.unlink(missing_ok=True)
        shutil.rmtree(folder / "live", ignore_errors=True)
        if total < 5:
            raise RuntimeError("The live stream couldn't be recorded. Check the link and your internet, then try again.")
        _live_state(ident, state="finishing", recorded=round(total, 1))
        store.update(ident, message="Saving the full recording")
        source = recorder.target
        try:
            source = finish_recording(recorder.target)
            recorder.target.unlink(missing_ok=True)
        except Exception:
            source = recorder.target
        info = probe_media(source)
        minutes = max(1, round(info.duration / 60))
        store.update(
            ident, status="complete", progress=1, source_path=str(source), duration=info.duration,
            media=asdict(info), export_revision=1,
            message=f"Live capture done: {len(clips)} clips from {minutes} min." if clips
            else f"Recorded {minutes} min but found no clear moments.",
        )
        _live_state(ident, state="done")
    except Exception as exc:
        if recorder:
            recorder.stop(wait=5)
        cancelled = event.is_set()
        store.update(
            ident, status="cancelled" if cancelled else ("complete" if clips else "failed"), progress=0,
            message="Cancelled" if cancelled else ("Live capture stopped early. The clips made so far are saved."
                                                   if clips else "The live capture didn't work."),
            error=None if clips and not cancelled else str(exc)[:500],
        )
        _live_state(ident, state="done")
    finally:
        live_stops.pop(ident, None)
        live_recorders.pop(ident, None)
        live_clip_now.discard(ident)
        if ident in live_chats:
            live_chats.pop(ident).stop()


@app.post("/api/jobs/{ident}/live/clip")
def clip_live_now(ident):
    """Clip now: the last stretch of the live recording becomes a clip within about a minute."""
    get_job(ident)
    if ident not in live_stops or live_stops[ident].is_set():
        raise HTTPException(409, "This live capture isn't recording any more.")
    live_clip_now.add(ident)
    _live_state(ident, clip_now=True)
    return store.get(ident)


@app.post("/api/jobs/{ident}/live/stop")
def stop_live(ident):
    get_job(ident)
    if ident in live_stops:
        live_stops[ident].set()
        recorder = live_recorders.get(ident)
        if recorder and recorder.running:
            # stop recording right away, even while a window is being clipped
            threading.Thread(target=recorder.stop, daemon=True).start()
            _live_state(ident, state="stopping")
    return store.get(ident)


# ---------------- licence + free trial ----------------
class LicenceRequest(BaseModel):
    # No min length here: read_key() explains a short/typo'd key in plain words (T4: QA-TEST-1).
    key: str = Field(default="", max_length=400)


def _licence():
    from .licence import Licence

    return Licence()


def licence_gate():
    from .licence import LicenceError
    from .shop import free

    if free():
        return

    try:
        _licence().check_new_video()
    except LicenceError as error:
        raise HTTPException(402, str(error)) from error


def licence_count():
    from .shop import free

    if free():
        return
    try:
        _licence().count_new_video()
    except Exception:  # noqa: BLE001 - never fail a started job over the trial counter
        pass


@app.get("/api/licence")
def licence_status():
    from .shop import free

    if free():
        return {"free": True, "licensed": True}
    return _licence().status()


@app.post("/api/licence")
def licence_activate(body: LicenceRequest):
    from .licence import LicenceError
    from .vault import VaultError

    try:
        return _licence().activate(body.key)
    except (LicenceError, VaultError) as error:
        raise HTTPException(400, str(error)) from error


@app.delete("/api/licence")
def licence_remove():
    return _licence().remove()
