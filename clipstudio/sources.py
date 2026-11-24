from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .media import MediaError, find_tool

_ALLOWED_PROTOCOLS = {"http", "https", "m3u8", "m3u8_native"}
_MAX_LIVE_HEIGHT = 1080
LIVE_RECORD_HEIGHT = 720  # CPU-only PCs: light enough to re-encode in real time


def live_record_height() -> int:
    """1080p when a graphics-card encoder works (sharp corner-cam / zoomed Shorts), else 720p."""
    try:
        from .encoders import encoder

        return 1080 if encoder() != "x264" else LIVE_RECORD_HEIGHT
    except Exception:
        return LIVE_RECORD_HEIGHT
_MIN_CAPTURE_SECONDS = 30
_MAX_CAPTURE_SECONDS = 10800
# HLS first: YouTube's HLS playlists seek by segment, so a 30 s section of a
# 4 h VOD downloads in seconds. DASH seeking through ffmpeg stalled for minutes.
SECTION_FORMAT = (
    "bv*[protocol^=m3u8][height<=1080]+ba[protocol^=m3u8]"
    "/b[protocol^=m3u8][height<=1080]"
    "/bv*[height<=1080]+ba/b[height<=1080]/b"
)
FULL_FORMAT = "bv*[height<=1080]+ba/b[height<=1080]/b"
MAX_SECTION_SECONDS = 4 * 3600


def download_format(max_height=1080, section=False):
    height = int(max_height)
    if height not in (720, 1080, 2160):
        raise ValueError("Choose HD, Full HD or 4K quality.")
    # A 4K request must prefer the highest rendition, including DASH when HLS is only 1080p.
    template = SECTION_FORMAT if section and height <= 1080 else FULL_FORMAT
    return template.replace("1080", str(height))


def ytdlp_command(*extra: str) -> list[str]:
    """One place builds the yt-dlp command (the packaged app swaps this later)."""
    from .paths import ytdlp_base

    return [
        *ytdlp_base(),
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout",
        "20",
        "--retries",
        "3",
        *extra,
    ]


def child_env() -> dict:
    """UTF-8 everywhere so emoji titles do not turn into mojibake on Windows."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


_FRIENDLY = (
    (("no space left", "errno 28", "not enough space", "disk is full", "winerror 112", "there is not enough space"),
     "Your disk is full, so the video couldn't be saved. Click \U0001f4c1 Save folder at the top and pick another drive (like D:), or free some space."),
    (("permission denied", "winerror 5", "access is denied"),
     "MasterClip isn't allowed to save in its folder. Click \U0001f4c1 Save folder at the top and pick another folder."),
    (("private video", "this video is private"), "This video is private, so it can't be opened."),
    (("members-only", "join this channel", "members only"), "This video is for channel members only. Pick another one."),
    (("sign in to confirm your age", "age-restricted", "age restricted"), "YouTube needs a signed-in, age-checked account for this video. Pick another one."),
    (("not available in your country", "geo restrict", "blocked it in your country"), "This video is blocked in your country. Pick another one."),
    (("video unavailable", "has been removed", "does not exist", "http error 404"), "That video doesn't exist any more. Check the link."),
    (("unsupported url", "no suitable extractor", "is not a valid url"), "That link isn't a video page we can open. Paste a YouTube, Twitch or Kick video or stream link."),
    (("premieres in", "will begin in", "this live event will begin"), "This stream hasn't started yet. Try again once it's live."),
    (("sign in to confirm you", "not a bot"), "YouTube is asking for a sign-in check right now. Wait a few minutes and try again."),
    (("timed out", "unable to download webpage", "getaddrinfo", "connection", "network"), "We couldn't reach the video site. Check your internet and try again."),
    (("http error 429", "too many requests"), "The video site is busy (too many requests). Wait a few minutes and try again."),
)


def friendly_error(log_text: str, fallback: str) -> str:
    text = (log_text or "").lower()
    for needles, message in _FRIENDLY:
        if any(needle in text for needle in needles):
            return message
    # Unknown reason: add the downloader's own last error line (links removed, they can be signed)
    # so a buyer's screenshot tells support what actually happened.
    reason = re.sub(r"https?://\S+", "[link]", _last_error_line(log_text or ""))
    reason = re.sub(r"\[[\w:]+\]\s*", "", reason, count=1).strip()
    return f"{fallback} (Reason: {reason[:140]})" if reason else fallback


def _last_error_line(log: str) -> str:
    lines = [l.strip() for l in (log or "").splitlines() if "ERROR" in l]
    return lines[-1].replace("ERROR:", "").strip()[:160] if lines else ""


def _log_tail(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return ""


def validate_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Paste a full video link that starts with https://")
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise ValueError(
            "We couldn't find that website. Check the link and your internet."
        ) from exc
    if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError(
            "That link points to a private network address. Paste a public video link."
        )
    return url


def _valid_stream_url(fmt):
    url = fmt.get("url")
    protocol = str(fmt.get("protocol") or "").lower()
    return (
        protocol in _ALLOWED_PROTOCOLS
        and isinstance(url, str)
        and urlparse(url).scheme.lower() in {"http", "https"}
        and bool(urlparse(url).hostname)
    )


def _quality(fmt):
    def number(name):
        try:
            return float(fmt.get(name) or 0)
        except (TypeError, ValueError):
            return 0.0

    return (number("height"), number("fps"), number("tbr"), number("source_preference"))


def choose_live_inputs(metadata, max_height=_MAX_LIVE_HEIGHT):
    """Choose supported live video/audio URLs, preferring a combined stream."""
    formats = metadata.get("formats") if isinstance(metadata, dict) else None
    if not isinstance(formats, list):
        raise MediaError("Live stream metadata did not contain supported formats.")

    combined, videos, audios = [], [], []
    for fmt in formats:
        if not isinstance(fmt, dict) or not _valid_stream_url(fmt):
            continue
        vcodec, acodec = fmt.get("vcodec"), fmt.get("acodec")
        # YouTube live HLS lists audio-only renditions with vcodec "none" and
        # acodec unknown (None), so "not video" counts as audio there.
        video = vcodec not in (None, "none") or (vcodec is None and fmt.get("height"))
        audio = acodec not in (None, "none") or (vcodec == "none" and acodec is None)
        try:
            height = int(fmt.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        if video and not (0 < height <= max_height):
            continue
        if video and audio:
            combined.append(fmt)
        elif video:
            videos.append(fmt)
        elif audio:
            audios.append(fmt)

    if combined:
        selected = max(combined, key=_quality)
        return selected["url"], None
    if videos and audios:
        video = max(videos, key=_quality)
        audio = max(audios, key=_quality)
        return video["url"], audio["url"]
    raise MediaError(
        "We couldn't record this live stream: it has no supported video and audio formats. Try another stream."
    )


def run_process(command, log_path, cancel_event, timeout=7200):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if cancel_event and cancel_event.is_set():
        raise MediaError("Cancelled.")
    start = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as log:
        try:
            proc = subprocess.Popen(
                command,
                stdout=log,
                stderr=log,
                env=child_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise MediaError(
                "The video downloader couldn't start. Reinstall Clip Studio."
            ) from exc
        while proc.poll() is None:
            if (
                cancel_event
                and cancel_event.is_set()
                or time.monotonic() - start > timeout
            ):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                if cancel_event and cancel_event.is_set():
                    raise MediaError("Cancelled.")
                raise MediaError(
                    "The download took too long. Pick a shorter time range and try again."
                )
            time.sleep(0.2)
    if proc.returncode:
        # Importer errors can contain signed URLs; details stay in the local job log.
        raise MediaError(
            friendly_error(
                _log_tail(log_path),
                "We couldn't get this video. Try another time range or another video.",
            )
        )


def fetch_metadata(base, url, folder, cancel_event, timeout=120):
    """Read yt-dlp metadata to a temporary file while polling for cancellation."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / "import.log"
    command = base + ["--dump-single-json", "--skip-download", url]
    if cancel_event and cancel_event.is_set():
        raise MediaError("Cancelled.")
    started = time.monotonic()
    with (
        tempfile.TemporaryFile(mode="w+b") as stdout,
        open(log_path, "w", encoding="utf-8") as log,
    ):
        try:
            proc = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=log,
                env=child_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise MediaError(
                "The video downloader couldn't start. Reinstall Clip Studio."
            ) from exc
        while proc.poll() is None:
            cancelled = bool(cancel_event and cancel_event.is_set())
            timed_out = time.monotonic() - started > timeout
            if cancelled or timed_out:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                if cancelled:
                    raise MediaError("Cancelled.")
                raise MediaError(
                    "The video site took too long to answer. Check your internet and try again."
                )
            time.sleep(0.2)
        if proc.returncode:
            log.flush()
            raise MediaError(
                friendly_error(
                    _log_tail(log_path),
                    "We couldn't open that link. Check it's a public video or stream page (YouTube, Twitch, Kick…).",
                )
            )
        stdout.seek(0, 2)
        if stdout.tell() > 64 * 1024 * 1024:
            raise MediaError("That link returned something we can't read. Try another link.")
        stdout.seek(0)
        try:
            metadata = json.loads(stdout.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MediaError(
                "That link returned something we can't read. Try another link."
            ) from exc
    if not isinstance(metadata, dict):
        raise MediaError("That link returned something we can't read. Try another link.")
    if metadata.get("_type") == "playlist" or metadata.get("entries"):
        raise MediaError(
            "That's a playlist or channel link. Open one video or stream and paste its link."
        )
    return metadata


def _format_for_url(metadata, url):
    return next(
        (
            fmt
            for fmt in metadata.get("formats", [])
            if isinstance(fmt, dict) and fmt.get("url") == url
        ),
        {},
    )


def _request_headers(metadata, fmt):
    headers = {}
    for collection in (metadata.get("http_headers", {}), fmt.get("http_headers", {})):
        if not isinstance(collection, dict):
            continue
        for key, value in collection.items():
            normalized = str(key).lower()
            if normalized in {"user-agent", "referer"} and isinstance(value, str):
                value = value.replace("\r", "").replace("\n", "").strip()
                if value:
                    headers[normalized] = value
    names = {"user-agent": "User-Agent", "referer": "Referer"}
    return "".join(f"{names[key]}: {value}\r\n" for key, value in headers.items())


def _input_args(url, headers):
    args = ["-rw_timeout", "15000000"]
    if headers:
        args += ["-headers", headers]
    return args + ["-i", url]


def _capture_live(
    video_url, audio_url, metadata, folder, capture_seconds, cancel_event
):
    """Capture selected inputs; kept separate so local HLS streams can be tested directly."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    video_format = _format_for_url(metadata, video_url)
    command = [find_tool("ffmpeg"), "-nostdin", "-y"]
    command += _input_args(video_url, _request_headers(metadata, video_format))
    if audio_url:
        audio_format = _format_for_url(metadata, audio_url)
        command += _input_args(audio_url, _request_headers(metadata, audio_format))
    command += ["-t", str(int(capture_seconds)), "-map", "0:v:0"]
    command += ["-map", "1:a:0" if audio_url else "0:a:0"]
    target = folder / "source.mp4"
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(target),
    ]
    run_process(
        command, folder / "import.log", cancel_event, int(capture_seconds) + 180
    )
    return target


def heat_points(metadata) -> list[dict]:
    """YouTube 'most replayed' buckets as [{start, end, value}], or []."""
    points = []
    for item in metadata.get("heatmap") or []:
        try:
            start = float(item["start_time"])
            end = float(item["end_time"])
            value = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start:
            points.append({"start": start, "end": end, "value": max(0.0, value)})
    return sorted(points, key=lambda p: p["start"])


def suggest_range(duration, heat, window=600.0):
    """Pick the hottest window of `window` seconds; skip the intro spike most VODs have."""
    duration = float(duration or 0)
    if duration <= 0:
        return 0.0, 0.0, "whole video"
    window = min(window, duration)
    if window >= duration:
        return 0.0, duration, "whole video"
    if not heat:
        # No replay data: skip the usual "starting soon" screen.
        start = min(max(0.0, duration * 0.05), duration - window)
        return round(start, 1), round(start + window, 1), "start of the stream"
    skip = duration * 0.03
    best_start, best_score = None, -1.0
    for point in heat:
        start = point["start"]
        if start < skip or start + window > duration:
            continue
        score = sum(
            p["value"] * max(0.0, min(p["end"], start + window) - max(p["start"], start))
            for p in heat
        )
        if score > best_score:
            best_start, best_score = start, score
    if best_start is None:
        best_start = max(0.0, duration - window)
    return round(best_start, 1), round(best_start + window, 1), "most replayed part"


def inspect_url(url, folder, cancel_event=None) -> dict:
    """Read a link without downloading: length, live or not, replay heat, chapters."""
    validate_url(url)
    metadata = fetch_metadata(ytdlp_command(), url, folder, cancel_event, timeout=60)
    live = bool(metadata.get("is_live"))
    try:
        duration = float(metadata.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not math.isfinite(duration):
        duration = 0.0
    heat = heat_points(metadata)
    start, end, why = suggest_range(duration, heat) if not live else (0, 0, "live")
    chapters = []
    for chapter in metadata.get("chapters") or []:
        try:
            chapters.append(
                {
                    "start": float(chapter["start_time"]),
                    "end": float(chapter["end_time"]),
                    "title": str(chapter.get("title") or "")[:120],
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    views = metadata.get("concurrent_view_count") if live else metadata.get("view_count")
    extractor = str(metadata.get("extractor_key") or metadata.get("extractor") or "")
    title = str(metadata.get("title") or "Video")
    if extractor == "TwitchStream" and str(metadata.get("description") or "").strip():
        title = str(metadata["description"]).strip()  # yt-dlp titles Twitch lives "<name> (live) <date>"
    return {
        "url": metadata.get("webpage_url") or url,
        "title": title[:300],
        "channel": str(
            metadata.get("channel") or metadata.get("uploader") or ""
        )[:200],
        "thumbnail": metadata.get("thumbnail"),
        "duration": duration,
        "is_live": live,
        "views": views if isinstance(views, int) else None,
        "heat": heat,
        "chapters": chapters,
        "suggested": {"start": start, "end": end, "reason": why},
        "site": site_name(extractor),
    }


def site_name(extractor: str) -> str:
    """'TwitchVod' -> 'Twitch', 'Youtube' -> 'YouTube'; anything else as yt-dlp names it."""
    low = extractor.lower()
    for key, name in (("youtube", "YouTube"), ("twitch", "Twitch"), ("kick", "Kick"), ("tiktok", "TikTok")):
        if low.startswith(key):
            return name
    return extractor


def _section_download(url, folder, start, end, cancel_event, progress, max_height=1080):
    folder = Path(folder)
    start, end = float(start), float(end)
    if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
        raise MediaError("Pick a time range where the end is after the start.")
    if end - start > MAX_SECTION_SECONDS:
        raise MediaError("Pick a time range of 4 hours or less.")
    minutes = max(1, round((end - start) / 60))
    progress(0.05, f"Downloading only the {minutes} min you picked")
    run_process(
        ytdlp_command(
            "-f",
            download_format(max_height, section=True),
            "--download-sections",
            f"*{start:.2f}-{end:.2f}",
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "-o",
            str(folder / "source.%(ext)s"),
            url,
        ),
        folder / "import.log",
        cancel_event,
        timeout=max(600, int((end - start) * 2)),
    )


def import_url(
    url, folder, capture_seconds, cancel_event, progress, section=None, captions_language=None, max_height=1080
):
    """Fetch a link. VODs: only `section` (start, end) is downloaded when given.
    Live: records `capture_seconds` from now. With `captions_language` ('auto' or a language code),
    YouTube's own word-timed captions for the downloaded part are saved as captions.json, so the
    speech model doesn't have to run."""
    if cancel_event and cancel_event.is_set():
        raise MediaError("Cancelled.")
    if not (_MIN_CAPTURE_SECONDS <= int(capture_seconds) <= _MAX_CAPTURE_SECONDS):
        raise MediaError(
            "Live recording length must be between 30 seconds and 3 hours."
        )
    validate_url(url)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    progress(0.02, "Opening the link")
    metadata = fetch_metadata(ytdlp_command(), url, folder, cancel_event)
    title = metadata.get("title") or "Imported video"
    target = folder / "source.mp4"
    if metadata.get("is_live"):
        try:
            video_url, audio_url = choose_live_inputs(metadata, live_record_height())
        except MediaError:
            video_url, audio_url = choose_live_inputs(metadata)
        seconds = int(capture_seconds)
        length = f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"
        progress(0.05, f"Recording the live stream for {length}")
        target = _capture_live(
            video_url, audio_url, metadata, folder, capture_seconds, cancel_event
        )
    elif section:
        _section_download(url, folder, section[0], section[1], cancel_event, progress, max_height=max_height)
    else:
        progress(0.05, "Downloading the video")
        run_process(
            ytdlp_command(
                "-f",
                download_format(max_height),
                "--merge-output-format",
                "mp4",
                "--remux-video",
                "mp4",
                "--max-filesize",
                "20G",
                "-o",
                str(folder / "source.%(ext)s"),
                url,
            ),
            folder / "import.log",
            cancel_event,
        )
    if not target.is_file():
        raise MediaError("The download finished but no video came out. Try another time range.")
    try:
        (folder / "source-info.json").write_text(
            json.dumps(
                {
                    "title": title,
                    "channel": metadata.get("channel") or metadata.get("uploader") or "",
                    "url": metadata.get("webpage_url") or url,
                    "is_live": bool(metadata.get("is_live")),
                    "section": list(section) if section else None,
                    "heat": heat_points(metadata),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    (folder / "captions.json").unlink(missing_ok=True)
    if captions_language and not metadata.get("is_live"):
        try:
            from .ytcaptions import fetch

            start = float(section[0]) if section else 0.0
            length = float(section[1]) - start if section else None
            found = fetch(metadata, start, length, captions_language)
            if found:
                (folder / "captions.json").write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  # the speech model is the fallback
    return target, title


# ---- Whole stream, hottest parts only -------------------------------------------------------
# A 9 h VOD is far too big to download and listen to. Instead we read signals for the WHOLE
# stream without downloading the video (YouTube "most replayed", Twitch chat replay), pick the
# hottest few minutes, and download only those parts (in parallel), joined into one video.
HOT_WINDOW = 150.0  # seconds per hot part


def pick_hot_windows(duration, heat=(), chat_times=(), count=8, window=HOT_WINDOW):
    """[(start, end)] of the `count` best non-overlapping windows, in time order, and a reason."""
    duration = float(duration or 0)
    if duration <= window * 1.5:
        return [(0.0, duration)], "whole video"
    bucket = 30.0
    n = int(math.ceil(duration / bucket))
    heat_b, chat_b = [0.0] * n, [0.0] * n
    for p in heat or []:
        for b in range(max(0, int(p["start"] // bucket)), min(n, int(math.ceil(p["end"] / bucket)))):
            heat_b[b] = max(heat_b[b], float(p["value"]))
    for t in chat_times or []:
        if 0 <= t < duration:
            chat_b[int(t // bucket)] += 1
    top_h, top_c = max(heat_b) or 0, max(chat_b) or 0
    if not top_h and not top_c:
        # No signal at all: look at evenly spread parts (skipping the intro).
        step = (duration * 0.95) / count
        spots = [(duration * 0.04 + i * step, duration * 0.04 + i * step + window) for i in range(count)]
        return [(round(s, 1), round(min(e, duration), 1)) for s, e in spots if s < duration - 10], "spread-out parts"
    score = [(heat_b[i] / top_h if top_h else 0) + (chat_b[i] / top_c if top_c else 0) for i in range(n)]
    span = max(1, int(round(window / bucket)))
    skip = int(duration * 0.03 // bucket)
    sums = sorted(((sum(score[i:i + span]), i) for i in range(skip, max(skip + 1, n - span))), reverse=True)
    chosen = []
    for value, i in sums:
        if len(chosen) >= count or value <= 0:
            break
        if all(abs(i - j) >= span for j in chosen):
            chosen.append(i)
    out = sorted((round(i * bucket, 1), round(min(duration, i * bucket + window), 1)) for i in chosen)
    reason = ("most replayed parts" if top_h and not top_c
              else "parts where chat went crazy" if top_c and not top_h else "hottest parts")
    return out, reason


def _concat(parts, target, cancel_event, folder):
    listing = folder / "parts.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    ffmpeg = find_tool("ffmpeg")
    base = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
    try:
        run_process(base + ["-c", "copy", "-movflags", "+faststart", str(target)],
                    folder / "concat.log", cancel_event, timeout=1800)
        if target.is_file() and target.stat().st_size > 0:
            return
    except MediaError:
        pass
    run_process(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                        "-movflags", "+faststart", str(target)], folder / "concat.log", cancel_event, timeout=7200)


class HotPartsFailed(MediaError):
    """None of the hot parts downloaded (the caller falls back to a plain time range)."""


def import_hotspots(url, folder, cancel_event, progress, max_height=1080):
    """Download only the hottest parts of a long VOD. Returns (path, title, chat in joined-video
    seconds or None, parts [(vod start, vod end, joined start)])."""
    from concurrent.futures import ThreadPoolExecutor

    from .media import probe_media

    validate_url(url)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    progress(0.02, "Opening the link")
    metadata = fetch_metadata(ytdlp_command(), url, folder, cancel_event)
    if metadata.get("is_live"):
        raise MediaError("This stream is live right now. Pick a live recording length instead.")
    title = metadata.get("title") or "Imported video"
    duration = float(metadata.get("duration") or 0)
    if duration <= 0:
        raise MediaError("We couldn't read how long this video is. Pick a time range instead.")
    heat = heat_points(metadata)
    chat = []
    try:
        from .twitch import vod_chat, vod_id

        vid = vod_id(url)
        if vid:
            progress(0.04, "Reading the whole stream's chat (no download needed)")
            chat = vod_chat(vid, 0.0, duration, cancel_event, deadline=150)
    except Exception:
        chat = []
    count = max(4, min(10, int(round(3 + duration / 3600 * 0.8))))
    windows, reason = pick_hot_windows(duration, heat, [t for t, _ in chat], count=count)
    total_min = max(1, round(sum(e - s for s, e in windows) / 60))
    progress(0.08, f"Downloading only the {len(windows)} {reason} ({total_min} of {round(duration / 60)} min)")
    done = {"n": 0}

    def grab(item):
        index, (start, end) = item
        if cancel_event and cancel_event.is_set():
            raise MediaError("Cancelled.")
        out = folder / f"part-{index:02}.mp4"
        try:
            run_process(
                ytdlp_command("-f", download_format(max_height, section=True), "--download-sections", f"*{start:.2f}-{end:.2f}",
                              "--merge-output-format", "mp4", "--remux-video", "mp4",
                              "-o", str(folder / f"part-{index:02}.%(ext)s"), url),
                folder / f"import-{index:02}.log", cancel_event, timeout=max(600, int((end - start) * 4)),
            )
        except MediaError:
            if cancel_event and cancel_event.is_set():
                raise
            return None  # one part failing shouldn't sink the rest
        done["n"] += 1
        progress(0.08 + 0.85 * done["n"] / len(windows), f"Got {done['n']} of {len(windows)} hot parts")
        return out

    with ThreadPoolExecutor(max_workers=3) as pool:
        files = list(pool.map(grab, enumerate(windows)))
    missing = [i for i, f in enumerate(files) if not (f and f.is_file() and f.stat().st_size > 0)]
    if missing and len(missing) == len(windows):
        # Three downloads at once can trip YouTube's bot check: try again one at a time.
        progress(0.1, "Trying the hot parts again, one at a time")
        for i in missing:
            if cancel_event and cancel_event.is_set():
                raise MediaError("Cancelled.")
            time.sleep(2)
            files[i] = grab((i, windows[i]))
    files_ok = [(f, w) for f, w in zip(files, windows) if f and f.is_file() and f.stat().st_size > 0]
    if not files_ok:
        log = "\n".join(_log_tail(folder / f"import-{i:02}.log") for i in range(len(windows)))
        raise HotPartsFailed(friendly_error(log, _last_error_line(log) or "YouTube refused the part downloads"))
    parts, joined = [], 0.0
    for f, (start, end) in files_ok:
        try:
            length = probe_media(f).duration or (end - start)
        except Exception:
            length = end - start
        parts.append((start, start + length, joined))
        joined += length
    target = folder / "source.mp4"
    progress(0.95, "Joining the hot parts")
    if len(files_ok) == 1:
        files_ok[0][0].replace(target)
    else:
        _concat([f for f, _ in files_ok], target, cancel_event, folder)
        for f, _ in files_ok:
            f.unlink(missing_ok=True)

    def to_joined(t):
        for start, end, at in parts:
            if start <= t < end:
                return at + (t - start)
        return None

    mapped_heat = []
    for p in heat:
        for start, end, at in parts:
            a, b = max(p["start"], start), min(p["end"], end)
            if b > a:
                mapped_heat.append({"start": at + a - start, "end": at + b - start, "value": p["value"]})
    joined_chat = [(to_joined(t), text) for t, text in chat]
    joined_chat = [(t, text) for t, text in joined_chat if t is not None]
    try:
        (folder / "source-info.json").write_text(json.dumps({
            "title": title,
            "channel": metadata.get("channel") or metadata.get("uploader") or "",
            "url": metadata.get("webpage_url") or url,
            "is_live": False, "section": None, "heat": mapped_heat,
            "parts": [list(p) for p in parts],
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    (folder / "captions.json").unlink(missing_ok=True)
    return target, title, (joined_chat or None), parts
