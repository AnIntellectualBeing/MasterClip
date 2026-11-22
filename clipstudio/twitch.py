"""Twitch stream search with no account or API key.

Uses Twitch's public web GraphQL endpoint with the public web client id, the same one the
twitch.tv website and yt-dlp use. Two requests per search (~0.5 s each):
  1. channels matching the topic (live ones come with their stream);
  2. the latest past broadcasts (VODs) of those channels, in one batched query.
Nothing about the buyer is sent; only the search words.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GQL_URL = "https://gql.twitch.tv/gql"
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # public, embedded in twitch.tv for every visitor
TIMEOUT = 12
MAX_CHANNELS = 20
VOD_CHANNELS = 8
VODS_PER_CHANNEL = 2
MIN_VOD_SECONDS = 120
_LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{2,25}$")
_ID_RE = re.compile(r"^\d{1,15}$")

SEARCH_QUERY = """query($q:String!){ searchFor(userQuery:$q, platform:"web", options:{targets:[{index:CHANNEL}]}){
 channels{ edges{ item{ ... on User{ login displayName
  stream{ id title viewersCount createdAt previewImageURL(width:640,height:360) game{ name } } } } } } } }"""

VODS_QUERY = """query($logins:[String!]){ users(logins:$logins){ login displayName stream{ id createdAt }
 videos(first:%d, type:ARCHIVE, sort:TIME){ edges{ node{ id title lengthSeconds viewCount publishedAt
  previewThumbnailURL(width:640,height:360) game{ name } } } } } }""" % VODS_PER_CHANNEL


class TwitchError(RuntimeError):
    """Twitch search did not answer."""


def _gql(query: str, variables: dict, cancel_event=None) -> dict:
    if cancel_event is not None and cancel_event.is_set():
        raise TwitchError("Cancelled.")
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(
        GQL_URL,
        data=body,
        headers={"Client-ID": WEB_CLIENT_ID, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(8 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TwitchError("Twitch search didn't answer.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise TwitchError("Twitch search didn't answer.")
    return data["data"]


def _int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _when(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_live(user: dict) -> dict | None:
    """A live channel from search -> a result card, or None if it isn't live."""
    if not isinstance(user, dict):
        return None
    login, stream = user.get("login"), user.get("stream")
    if not isinstance(login, str) or not _LOGIN_RE.fullmatch(login) or not isinstance(stream, dict):
        return None
    game = (stream.get("game") or {}).get("name") if isinstance(stream.get("game"), dict) else None
    return {
        "id": f"twitch-live-{login.lower()}",
        "title": str(stream.get("title") or f"{user.get('displayName') or login} is live")[:300],
        "channel": str(user.get("displayName") or login)[:200],
        "url": f"https://www.twitch.tv/{login.lower()}",
        "thumbnail": stream.get("previewImageURL") if isinstance(stream.get("previewImageURL"), str) else None,
        "duration": None,
        "views": _int(stream.get("viewersCount")),
        "live": True,
        "live_status": "is_live",
        "site": "Twitch",
        "game": game,
    }


def normalize_vods(user: dict) -> list[dict]:
    """Finished past broadcasts of one channel. Skips the one still being recorded right now."""
    if not isinstance(user, dict) or not isinstance(user.get("login"), str):
        return []
    stream = user.get("stream") if isinstance(user.get("stream"), dict) else None
    live_since = _when(stream.get("createdAt")) if stream else None
    out = []
    for edge in ((user.get("videos") or {}).get("edges") or []):
        node = (edge or {}).get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict) or not _ID_RE.fullmatch(str(node.get("id") or "")):
            continue
        length = _int(node.get("lengthSeconds")) or 0
        thumb = node.get("previewThumbnailURL") if isinstance(node.get("previewThumbnailURL"), str) else None
        published = _when(node.get("publishedAt"))
        recording = (thumb and "404_processing" in thumb) or (
            live_since and published and published >= live_since - dt.timedelta(minutes=5)
        )
        if recording or length < MIN_VOD_SECONDS:
            continue
        game = (node.get("game") or {}).get("name") if isinstance(node.get("game"), dict) else None
        out.append({
            "id": f"twitch-vod-{node['id']}",
            "title": str(node.get("title") or "Past broadcast")[:300],
            "channel": str(user.get("displayName") or user["login"])[:200],
            "url": f"https://www.twitch.tv/videos/{node['id']}",
            "thumbnail": thumb,
            "duration": float(length),
            "views": _int(node.get("viewCount")),
            "live": False,
            "live_status": "was_live",
            "site": "Twitch",
            "game": game,
            "published_at": node.get("publishedAt"),
        })
    return out


def search(topic: str, cancel_event=None) -> dict:
    """{"live": [...], "vod": [...]} for a topic. Raises TwitchError if Twitch doesn't answer."""
    found = _gql(SEARCH_QUERY, {"q": topic}, cancel_event)
    edges = (((found.get("searchFor") or {}).get("channels") or {}).get("edges") or [])[:MAX_CHANNELS]
    users = [e.get("item") for e in edges if isinstance(e, dict) and isinstance(e.get("item"), dict)]
    live, seen = [], set()
    for user in users:
        item = normalize_live(user)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            live.append(item)
    live.sort(key=lambda i: i["views"] or 0, reverse=True)

    logins = []
    for user in users:
        login = user.get("login")
        if isinstance(login, str) and _LOGIN_RE.fullmatch(login) and login.lower() not in logins:
            logins.append(login.lower())
    vods = []
    if logins:
        try:
            data = _gql(VODS_QUERY, {"logins": logins[:VOD_CHANNELS]}, cancel_event)
        except TwitchError as exc:
            if "cancelled" in str(exc).lower():
                raise
            data = {}  # live results are still worth showing
        rows = [normalize_vods(u) for u in (data.get("users") or []) if isinstance(u, dict)]
        # newest broadcast of each channel first, then the older ones
        for depth in range(VODS_PER_CHANNEL):
            vods += [row[depth] for row in rows if len(row) > depth]
    return {"live": live, "vod": vods}


CHAT_QUERY = """query($id:ID!,$off:Int){ video(id:$id){
 comments(contentOffsetSeconds:$off){ edges{ node{ id contentOffsetSeconds
  message{ fragments{ text } } } } pageInfo{ hasNextPage } } } }"""
MAX_CHAT_PAGES = 3000
CHAT_SECONDS = 90  # never hold up a job longer than this for chat
_VOD_URL_RE = re.compile(r"twitch\.tv/(?:videos/|[A-Za-z0-9_]+/v(?:ideo)?/)(\d{1,15})")


def vod_id(url: str) -> str | None:
    """'https://www.twitch.tv/videos/123' -> '123' (None for anything else)."""
    m = _VOD_URL_RE.search(str(url or ""))
    return m.group(1) if m else None


def vod_chat(video_id: str, start: float, end: float, cancel_event=None, deadline: float | None = CHAT_SECONDS) -> list[tuple[float, str]]:
    """Chat replay of a past broadcast between start and end (VOD seconds), as (seconds, text).
    Only the video id is sent. Returns what it got so far if Twitch stops answering.
    Pages are asked for by time (Twitch's next-page cursor needs a browser token); messages seen
    twice at a page edge are dropped by id."""
    if not _ID_RE.match(str(video_id)):
        return []
    out: list[tuple[float, str]] = []
    seen: set[str] = set()
    offset = max(0, int(start))
    stop_at = time.monotonic() + deadline if deadline else None
    for _ in range(MAX_CHAT_PAGES):
        if stop_at is not None and time.monotonic() > stop_at:
            break
        try:
            data = _gql(CHAT_QUERY, {"id": str(video_id), "off": offset}, cancel_event)
        except TwitchError:
            break
        comments = ((data.get("video") or {}).get("comments")) or {}
        edges = comments.get("edges") or []
        last = None
        for edge in edges:
            node = edge.get("node") or {}
            t = _int(node.get("contentOffsetSeconds"))
            ident = str(node.get("id") or "")
            if t is None:
                continue
            last = t if last is None else max(last, t)
            if ident in seen or not (start <= t <= end):
                continue
            seen.add(ident)
            text = "".join(str(f.get("text") or "") for f in ((node.get("message") or {}).get("fragments") or []))
            out.append((float(t), text[:300]))
        if not edges or last is None or last > end or not (comments.get("pageInfo") or {}).get("hasNextPage"):
            break
        # Same second again only if the page didn't move (a very busy second); then step past it.
        offset = last if last > offset else offset + 1
    out.sort(key=lambda x: x[0])
    return out
