from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

_MAX_TOPIC_CHARS = 160
_MAX_CANDIDATES = 20
_MAX_VERIFY = 12
_DEFAULT_RESULTS = 6
_API_TIMEOUT = 20
_SEARCH_TIMEOUT = 20
_VERIFY_TIMEOUT = 12
_TOTAL_BUDGET = 180
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
_RESTRICTED_AVAILABILITY = {
    "private",
    "unavailable",
    "needs_auth",
    "login_required",
    "subscriber_only",
    "premium_only",
    "geo_restricted",
    "age_restricted",
    "needs_subscription",
}


def _child_env():
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class DiscoveryError(RuntimeError):
    """A discovery request failed or was cancelled."""


def sanitize_topic(topic: str) -> str:
    if not isinstance(topic, str):
        raise TypeError("Topic must be text.")
    clean = " ".join(topic.replace("\r", " ").replace("\n", " ").split())[
        :_MAX_TOPIC_CHARS
    ].strip()
    if not clean:
        raise ValueError("Enter a topic to search for.")
    return clean


def parse_iso_duration(value: str) -> float:
    if not isinstance(value, str):
        raise TypeError("Duration must be an ISO-8601 string.")
    match = _DURATION_RE.fullmatch(value)
    if not match:
        raise ValueError("Invalid ISO-8601 duration.")
    parts = match.groupdict()
    seconds = (
        int(parts["days"] or 0) * 86400
        + int(parts["hours"] or 0) * 3600
        + int(parts["minutes"] or 0) * 60
        + float(parts["seconds"] or 0)
    )
    if seconds <= 0:
        raise ValueError("Duration must be positive.")
    return seconds


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise DiscoveryError("Cancelled.")


def _run_json_command(command: list[str], timeout: float, cancel_event=None) -> dict:
    """Run yt-dlp without emitting metadata or signed URLs to logs."""
    _check_cancel(cancel_event)
    started = time.monotonic()
    with (
        tempfile.TemporaryFile(mode="w+b") as stdout,
        tempfile.TemporaryFile(mode="w+b") as stderr,
    ):
        try:
            process = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=stderr,
                env=_child_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise DiscoveryError(
                "Could not start yt-dlp. Install it and try discovery again."
            ) from exc
        while process.poll() is None:
            cancelled = cancel_event is not None and cancel_event.is_set()
            if cancelled or time.monotonic() - started >= timeout:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if cancelled:
                    raise DiscoveryError("Cancelled.")
                raise DiscoveryError("YouTube discovery timed out. Try again later.")
            time.sleep(0.1)
        if process.returncode:
            raise DiscoveryError(
                "YouTube search is unavailable or was blocked. Try again later or configure a YouTube Data API key."
            )
        stdout.seek(0, os.SEEK_END)
        if stdout.tell() > 64 * 1024 * 1024:
            raise DiscoveryError("YouTube returned unexpectedly large metadata.")
        stdout.seek(0)
        try:
            data = json.loads(stdout.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DiscoveryError(
                "YouTube returned invalid discovery metadata."
            ) from exc
    if not isinstance(data, dict):
        raise DiscoveryError("YouTube returned invalid discovery metadata.")
    return data


def _yt_base() -> list[str]:
    from .paths import ytdlp_base

    return [
        *ytdlp_base(),
        "--no-warnings",
        "--no-call-home",
        "--socket-timeout",
        "10",
        "--retries",
        "1",
    ]


def _yt_search(
    topic: str, max_candidates: int, cancel_event=None, timeout=_SEARCH_TIMEOUT
) -> dict:
    query = f"ytsearch{max_candidates}:{topic} livestream"
    command = _yt_base() + [
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        query,
    ]
    return _run_json_command(command, timeout, cancel_event)


def _yt_metadata(url: str, cancel_event=None, timeout=_VERIFY_TIMEOUT) -> dict:
    command = _yt_base() + [
        "--no-playlist",
        "--dump-single-json",
        "--skip-download",
        url,
    ]
    return _run_json_command(command, timeout, cancel_event)


def _iso_timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return (
                dt.datetime.fromtimestamp(value, dt.UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        clean = value.strip()
        try:
            parsed = dt.datetime.fromisoformat(clean)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    return None


def _published_at(info: dict) -> str | None:
    timestamp = _iso_timestamp(info.get("timestamp"))
    if timestamp:
        return timestamp
    upload_date = info.get("upload_date")
    if isinstance(upload_date, str) and re.fullmatch(r"\d{8}", upload_date):
        try:
            parsed = dt.datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=dt.UTC)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return _iso_timestamp(info.get("published_at"))


def _video_id(info: dict) -> str | None:
    ident = info.get("id") or info.get("video_id")
    return ident if isinstance(ident, str) and _ID_RE.fullmatch(ident) else None


def _thumbnail(info: dict) -> str | None:
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        for item in reversed(thumbnails):
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                return item["url"]
    thumbnail = info.get("thumbnail")
    return thumbnail if isinstance(thumbnail, str) else None


def _available(info: dict) -> bool:
    availability = str(info.get("availability") or "").lower()
    return (
        availability not in _RESTRICTED_AVAILABILITY
        and not info.get("is_private")
        and not info.get("is_unavailable")
        and info.get("upload_status") not in {"deleted", "rejected", "failed"}
    )


def normalize_ytdlp_archive(info: dict) -> dict | None:
    """Return a safe result only for metadata explicitly marked was_live."""
    if (
        not isinstance(info, dict)
        or info.get("live_status") != "was_live"
        or not _available(info)
    ):
        return None
    ident = _video_id(info)
    try:
        duration = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    if not ident or not math.isfinite(duration) or duration <= 0:
        return None
    url = f"https://www.youtube.com/watch?v={ident}"
    ended_at = _iso_timestamp(info.get("actual_end_time") or info.get("actualEndTime"))
    try:
        views = (
            int(info.get("view_count")) if info.get("view_count") is not None else None
        )
    except (TypeError, ValueError):
        views = None
    return {
        "id": ident,
        "title": str(info.get("title") or "Untitled livestream archive")[:300],
        "channel": str(
            info.get("channel")
            or info.get("uploader")
            or info.get("channel_name")
            or ""
        )[:200],
        "url": url,
        "thumbnail": _thumbnail(info),
        "duration": duration,
        "ended_at": ended_at,
        "published_at": _published_at(info),
        "views": views,
        "live_status": "was_live",
    }


def _api_get(endpoint: str, params: dict, cancel_event=None) -> dict:
    _check_cancel(cancel_event)
    from urllib.parse import urlencode

    query = urlencode(params)
    request = Request(
        f"https://www.googleapis.com/youtube/v3/{endpoint}?{query}",
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=_API_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise DiscoveryError(
            f"YouTube Data API request failed with HTTP {exc.code}."
        ) from exc
    except (
        URLError,
        TimeoutError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise DiscoveryError(
            "YouTube Data API is unavailable. Check the key and connection."
        ) from exc
    if not isinstance(data, dict):
        raise DiscoveryError("YouTube Data API returned invalid metadata.")
    return data


def _api_candidate(item: dict) -> dict | None:
    ident = item.get("id")
    snippet = item.get("snippet") or {}
    content = item.get("contentDetails") or {}
    live = item.get("liveStreamingDetails") or {}
    status = item.get("status") or {}
    actual_end = _iso_timestamp(live.get("actualEndTime"))
    try:
        duration = parse_iso_duration(content.get("duration"))
    except ValueError:
        return None
    if not isinstance(ident, str) or not _ID_RE.fullmatch(ident) or not actual_end:
        return None
    if status.get("privacyStatus") not in (None, "public", "unlisted") or status.get(
        "uploadStatus"
    ) in ("deleted", "rejected", "failed"):
        return None
    thumbnails = snippet.get("thumbnails") or {}
    thumb = next(
        (
            thumbs.get("url")
            for key in ("maxres", "standard", "high", "medium", "default")
            if isinstance((thumbs := thumbnails.get(key)), dict) and thumbs.get("url")
        ),
        None,
    )
    try:
        views = int((item.get("statistics") or {}).get("viewCount"))
    except (TypeError, ValueError):
        views = None
    return {
        "id": ident,
        "title": str(snippet.get("title") or "Untitled livestream archive")[:300],
        "channel": str(snippet.get("channelTitle") or "")[:200],
        "url": f"https://www.youtube.com/watch?v={ident}",
        "thumbnail": thumb,
        "duration": duration,
        "ended_at": actual_end,
        "published_at": _iso_timestamp(snippet.get("publishedAt")),
        "views": views,
        "live_status": "was_live",
    }


def _sort_limit(results: list[dict], max_results: int) -> list[dict]:
    # Keep search relevance stable, while putting longer archives before short broadcasts.
    return sorted(results, key=lambda item: item["duration"] >= 300, reverse=True)[
        :max_results
    ]


def _find_with_api(
    topic: str, max_results: int, cancel_event=None, progress=None
) -> dict:
    key = os.environ["YOUTUBE_API_KEY"]
    search_count = min(_MAX_CANDIDATES, max(_MAX_VERIFY, max_results * 2))
    if progress:
        progress(0.1, "Searching completed livestream archives")
    search = _api_get(
        "search",
        {
            "part": "snippet",
            "q": topic,
            "type": "video",
            "eventType": "completed",
            "maxResults": search_count,
            "key": key,
        },
        cancel_event,
    )
    ids = []
    for item in search.get("items", []):
        ident = (
            ((item.get("id") or {}).get("videoId")) if isinstance(item, dict) else None
        )
        if isinstance(ident, str) and _ID_RE.fullmatch(ident) and ident not in ids:
            ids.append(ident)
        if len(ids) >= _MAX_VERIFY:
            break
    results = []
    for offset in range(0, len(ids), 50):
        _check_cancel(cancel_event)
        details = _api_get(
            "videos",
            {
                "part": "snippet,contentDetails,liveStreamingDetails,statistics,status",
                "id": ",".join(ids[offset : offset + 50]),
                "key": key,
            },
            cancel_event,
        )
        for item in details.get("items", []):
            if isinstance(item, dict):
                candidate = _api_candidate(item)
                if candidate:
                    results.append(candidate)
    return {
        "topic": topic,
        "results": _sort_limit(results, max_results),
        "warnings": []
        if results
        else ["No available completed livestream archives matched this topic."],
        "provider": "youtube-api",
    }


def _find_with_ytdlp(
    topic: str, max_results: int, cancel_event=None, progress=None
) -> dict:
    started = time.monotonic()
    if progress:
        progress(0.1, "Searching YouTube for livestream archives")
    search_count = min(_MAX_CANDIDATES, max(12, max_results * 3))
    search = _yt_search(
        topic, search_count, cancel_event, min(_SEARCH_TIMEOUT, _TOTAL_BUDGET)
    )
    entries = search.get("entries")
    if not isinstance(entries, list):
        entries = [search] if search.get("id") else []
    checked, results = 0, []
    seen_ids = set()
    warnings = []
    for entry in entries[:_MAX_CANDIDATES]:
        _check_cancel(cancel_event)
        if (
            not isinstance(entry, dict)
            or entry.get("live_status") != "was_live"
            or not _available(entry)
        ):
            continue
        ident = _video_id(entry)
        webpage_url = entry.get("webpage_url")
        try:
            if isinstance(webpage_url, str):
                webpage_id = _extract_video_id(webpage_url)
                if ident and ident != webpage_id:
                    continue
                ident = webpage_id
        except ValueError:
            ident = None
        if not ident:
            continue
        if ident in seen_ids:
            continue
        seen_ids.add(ident)
        url = f"https://www.youtube.com/watch?v={ident}"
        if checked >= _MAX_VERIFY:
            break
        remaining = _TOTAL_BUDGET - (time.monotonic() - started)
        if remaining <= 0:
            warnings.append(
                "Discovery reached its time limit before all archives could be verified."
            )
            break
        checked += 1
        if progress:
            progress(
                0.2 + 0.7 * (checked / _MAX_VERIFY),
                f"Verifying archive {checked}/{_MAX_VERIFY}",
            )
        try:
            full = _yt_metadata(url, cancel_event, min(_VERIFY_TIMEOUT, remaining))
        except DiscoveryError as exc:
            if "cancelled" in str(exc).lower():
                raise
            warnings.append(
                "Some search results could not be verified as completed livestreams."
            )
            continue
        candidate = normalize_ytdlp_archive(full)
        if candidate and candidate["id"] != ident:
            candidate = None
        if candidate:
            results.append(candidate)
        if len(results) >= max_results:
            break
    if not results and not warnings:
        warnings.append(
            "No verified ended livestream archives matched this topic. Try a broader topic or an API key."
        )
    return {
        "topic": topic,
        "results": _sort_limit(results, max_results),
        "warnings": list(dict.fromkeys(warnings)),
        "provider": "yt-dlp",
    }


def find_completed_streams(
    topic: str, max_results: int = _DEFAULT_RESULTS, cancel_event=None, progress=None
) -> dict:
    clean_topic = sanitize_topic(topic)
    try:
        requested = int(max_results)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_results must be an integer from 1 to 20.") from exc
    if requested < 1:
        raise ValueError("max_results must be at least 1.")
    requested = min(requested, _MAX_CANDIDATES)
    _check_cancel(cancel_event)
    if os.environ.get("YOUTUBE_API_KEY", "").strip():
        try:
            return _find_with_api(clean_topic, requested, cancel_event, progress)
        except DiscoveryError as exc:
            if "cancelled" in str(exc).lower():
                raise
            return {
                "topic": clean_topic,
                "results": [],
                "warnings": [str(exc)],
                "provider": "youtube-api",
            }
    try:
        return _find_with_ytdlp(clean_topic, requested, cancel_event, progress)
    except DiscoveryError as exc:
        if "cancelled" in str(exc).lower():
            raise
        return {
            "topic": clean_topic,
            "results": [],
            "warnings": [str(exc)],
            "provider": "yt-dlp",
        }


# --- Fast search for the product home screen -------------------------------
# One flat yt-dlp call per kind (about 3 s each, run side by side). We do not
# open every result: the chosen one is inspected when the user clicks it.
_LIVE_FILTER = "EgJAAQ%253D%253D"  # YouTube "Live" filter
_RECENT_SORT = "CAI%253D"  # YouTube "Upload date" sort


def _search_url(topic: str, sp: str, suffix: str = "") -> str:
    from urllib.parse import quote_plus

    return (
        "https://www.youtube.com/results?search_query="
        + quote_plus((topic + suffix).strip())
        + "&sp="
        + sp
    )


def normalize_search_entry(info: dict) -> dict | None:
    if not isinstance(info, dict) or not _available(info):
        return None
    status = info.get("live_status")
    if status not in ("is_live", "was_live"):
        return None
    ident = _video_id(info)
    if not ident:
        return None
    try:
        duration = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if status == "was_live" and (not math.isfinite(duration) or duration < 120):
        return None
    views_raw = (
        info.get("concurrent_view_count")
        if status == "is_live"
        else info.get("view_count")
    )
    try:
        views = int(views_raw) if views_raw is not None else None
    except (TypeError, ValueError):
        views = None
    return {
        "id": ident,
        "title": str(info.get("title") or "Untitled stream")[:300],
        "channel": str(info.get("channel") or info.get("uploader") or "")[:200],
        "url": f"https://www.youtube.com/watch?v={ident}",
        "thumbnail": f"https://i.ytimg.com/vi/{ident}/hqdefault.jpg",
        "duration": duration if status == "was_live" else None,
        "views": views,
        "live": status == "is_live",
        "live_status": status,
        "site": "YouTube",
    }


SITES = ("youtube", "twitch")


def pack_site(live: list[dict], vod: list[dict], cap: int) -> list[dict]:
    """Up to `cap` results for one site: about half live (most watched first), the rest past streams."""
    if cap <= 0:
        return []
    half = max(1, cap // 2)
    take = live[:half]
    rest = vod[: cap - len(take)]
    if len(take) + len(rest) < cap:
        take = live[: cap - len(rest)]
    return take + rest


def merge_sites(found: dict[str, dict], limit: int) -> list[dict]:
    """found = {site: {"live": [...], "vod": [...]}}. Share `limit` slots fairly between sites (a site
    with few results gives its slots away), fill each site's share half live / half past streams, then
    list live streams first, then past streams, each taking turns per site (YouTube's flat search
    has no viewer counts, so sorting by viewers would always bury YouTube lives under Twitch)."""
    names = [name for name in SITES if name in found]
    if not names:
        return []
    have = {name: len(found[name]["live"]) + len(found[name]["vod"]) for name in names}
    share = {name: 0 for name in names}
    left = limit
    while left > 0:
        moved = False
        for name in names:
            if left and share[name] < have[name]:
                share[name] += 1
                left -= 1
                moved = True
        if not moved:
            break
    chosen = {name: pack_site(found[name]["live"], found[name]["vod"], share[name]) for name in names}
    def take_turns(lists):
        out = []
        for depth in range(max((len(x) for x in lists), default=0)):
            out += [x[depth] for x in lists if len(x) > depth]
        return out

    live = take_turns([[i for i in chosen[n] if i.get("live")] for n in names])
    vods = take_turns([[i for i in chosen[n] if not i.get("live")] for n in names])
    return live + vods


def find_streams(
    topic: str, max_results: int = 12, cancel_event=None, progress=None, sites=SITES
) -> dict:
    """Live streams first, then recent finished streams (VODs), from YouTube and Twitch."""
    from concurrent.futures import ThreadPoolExecutor

    from . import twitch

    clean = sanitize_topic(topic)
    limit = max(1, min(int(max_results), 24))
    sites = tuple(s for s in SITES if s in sites) or SITES
    if progress:
        progress(0.15, "Searching " + " and ".join(s.title() if s == "twitch" else "YouTube" for s in sites))

    def run_youtube(url):
        command = _yt_base() + [
            "--flat-playlist",
            "--dump-single-json",
            "--skip-download",
            "--playlist-end",
            "30",
            url,
        ]
        return _run_json_command(command, _SEARCH_TIMEOUT, cancel_event)

    found = {"youtube": {"live": [], "vod": []}, "twitch": {"live": [], "vod": []}}
    failed = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        if "youtube" in sites:
            futures["yt-live"] = pool.submit(run_youtube, _search_url(clean, _LIVE_FILTER))
            futures["yt-vod"] = pool.submit(run_youtube, _search_url(clean, _RECENT_SORT, " livestream"))
        if "twitch" in sites:
            futures["twitch"] = pool.submit(twitch.search, clean, cancel_event)
        for key, future in futures.items():
            try:
                data = future.result()
            except (DiscoveryError, twitch.TwitchError) as exc:
                if "cancelled" in str(exc).lower():
                    raise DiscoveryError("Cancelled.") from exc
                failed.append("Twitch" if key == "twitch" else "YouTube")
                continue
            if key == "twitch":
                found["twitch"] = data
                continue
            kind = "live" if key == "yt-live" else "vod"
            seen = set()
            for entry in data.get("entries") or []:
                item = normalize_search_entry(entry)
                if not item or item["id"] in seen or (kind == "live") != item["live"]:
                    continue
                seen.add(item["id"])
                found["youtube"][kind].append(item)
    _check_cancel(cancel_event)
    found["youtube"]["live"].sort(key=lambda i: i["views"] or 0, reverse=True)
    results = merge_sites({site: found[site] for site in sites}, limit)
    failed = list(dict.fromkeys(failed))
    warnings = []
    if not results:
        if failed and len(failed) >= len(sites):
            warnings = ["We couldn't search right now. Paste a link instead, or try again in a minute."]
        else:
            warnings = ["Nothing found. Try a shorter search like a game or streamer name."]
    elif failed:
        warnings = [f"{' and '.join(failed)} didn't answer, so these are from the other site only."]
    return {"topic": clean, "results": results, "warnings": warnings, "provider": "+".join(sites)}


def _extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname
        not in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
        }
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Select a valid YouTube video URL.")
    if parsed.hostname == "youtu.be":
        ident = parsed.path.strip("/").split("/", 1)[0]
    else:
        query_id = parse_qs(parsed.query).get("v", [None])[0]
        parts = parsed.path.strip("/").split("/")
        ident = query_id or (
            parts[1]
            if len(parts) == 2 and parts[0] in {"live", "shorts", "embed"}
            else None
        )
    if not isinstance(ident, str) or not _ID_RE.fullmatch(ident):
        raise ValueError("Select a valid YouTube video URL.")
    return ident


def inspect_completed_stream(url: str, cancel_event=None) -> dict | None:
    """Strictly recheck a selected YouTube URL before handing it to later processing."""
    ident = _extract_video_id(url)
    _check_cancel(cancel_event)
    metadata = _yt_metadata(
        f"https://www.youtube.com/watch?v={ident}", cancel_event, _VERIFY_TIMEOUT
    )
    candidate = normalize_ytdlp_archive(metadata)
    return candidate if candidate and candidate["id"] == ident else None
