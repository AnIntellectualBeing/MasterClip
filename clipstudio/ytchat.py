"""YouTube live chat and "is this channel live?" without an account or a key.

Reads the same public chat page anyone can open in a browser (youtube.com/live_chat?v=...),
then asks for new messages every few seconds. Only the video id is sent; nothing is ever written
to the chat. Messages feed the same spike rule and hype meter as Twitch chat (livechat.py).
"""

from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from .livechat import LiveChat

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
CHAT_PAGE = "https://www.youtube.com/live_chat?is_popout=1&v={}"
CHAT_API = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={}&prettyPrint=false"
TIMEOUT = 20
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def video_id(url: str) -> str | None:
    """The 11-letter id of a YouTube video / live link, or None (channel pages, other sites)."""
    try:
        parsed = urlparse(str(url or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    candidate = None
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        if parsed.path == "/watch" or parsed.path == "/live_chat":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("live", "shorts", "embed"):
                candidate = parts[1]
    return candidate if candidate and _ID.match(candidate) else None


def is_youtube(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "youtu.be" or host.endswith("youtube.com")


def _get(url: str, data: bytes | None = None) -> str:
    headers = {"User-Agent": UA, "Accept-Language": "en", "Cookie": "CONSENT=YES+1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=data, headers=headers), timeout=TIMEOUT) as response:
        return response.read(8 * 1024 * 1024).decode("utf-8", errors="replace")


# ---- parsing (pure, tested with a saved sample) ----
def parse_chat_page(html: str) -> dict | None:
    """{'key', 'version', 'continuation'} from the chat page, or None when chat is off / not live."""
    key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html or "")
    version = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html or "")
    cont = re.search(r'"continuation":"([^"]+)"', html or "")
    if not (key and version and cont):
        return None
    return {"key": key.group(1), "version": version.group(1), "continuation": cont.group(1)}


def _runs_text(message: dict) -> str:
    out = []
    for run in (message or {}).get("runs") or []:
        if "text" in run:
            out.append(str(run["text"]))
        elif isinstance(run.get("emoji"), dict):
            emoji = run["emoji"]
            shortcut = (emoji.get("shortcuts") or [None])[0]
            out.append(str(shortcut or emoji.get("emojiId") or ""))
    return "".join(out).strip()


_RENDERERS = ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer", "liveChatMembershipItemRenderer")


def parse_chat_response(data: dict) -> tuple[list[tuple[float, str]], str | None, float]:
    """(messages as (unix seconds, text), next continuation or None, seconds to wait)."""
    chat = ((data or {}).get("continuationContents") or {}).get("liveChatContinuation") or {}
    messages = []
    for action in chat.get("actions") or []:
        item = ((action or {}).get("addChatItemAction") or {}).get("item") or {}
        for name in _RENDERERS:
            renderer = item.get(name)
            if not isinstance(renderer, dict):
                continue
            text = _runs_text(renderer.get("message") or renderer.get("headerSubtext") or {})
            try:
                at = int(renderer.get("timestampUsec")) / 1_000_000
            except (TypeError, ValueError):
                at = time.time()
            if text or name != "liveChatTextMessageRenderer":
                messages.append((at, text or "[support]"))
    nxt, wait = None, 5.0
    for entry in chat.get("continuations") or []:
        for value in (entry or {}).values():
            if isinstance(value, dict) and value.get("continuation"):
                nxt = value["continuation"]
                try:
                    wait = float(value.get("timeoutMs", 5000)) / 1000
                except (TypeError, ValueError):
                    wait = 5.0
                break
        if nxt:
            break
    return messages, nxt, max(1.0, min(wait, 8.0))


def live_video_from_page(html: str) -> str | None:
    """A channel's /live page: the live video's id when the channel is live right now, else None."""
    canonical = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"', html or "")
    if canonical and re.search(r'"isLiveNow":\s*true', html):
        return canonical.group(1)
    return None


def channel_live_url(channel: str) -> str:
    """'https://www.youtube.com/@name' or '@name' -> 'https://www.youtube.com/@name/live'."""
    text = str(channel or "").strip().rstrip("/")
    if text.startswith("@"):
        return f"https://www.youtube.com/{text}/live"
    return text if text.endswith("/live") else text + "/live"


def channel_live(channel_url: str, fetch=_get) -> str | None:
    """Watch link of the channel's current live stream, or None. One small page read."""
    try:
        vid = live_video_from_page(fetch(channel_live_url(channel_url)))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None
    return f"https://www.youtube.com/watch?v={vid}" if vid else None


class YouTubeChat(LiveChat):
    """Same clock, meter and spike rule as Twitch chat; only the reading is different."""

    def __init__(self, vid: str, clock_start: float | None = None, fetch=None):
        super().__init__(vid, clock_start)
        self.vid = vid
        self._fetch = fetch or _get
        self.joined = time.time() - max(0.0, self.now())  # wall time of the chat clock's zero

    def _add_unix(self, unix_seconds: float, text: str):
        # chat clock = seconds since clock_start (monotonic); place by the message's own time
        at = self.now() - max(0.0, time.time() - unix_seconds)
        self.add(text, at=max(0.0, at))

    def _run(self):
        backoff = 3
        while not self._stop.is_set():
            try:
                page = parse_chat_page(self._fetch(CHAT_PAGE.format(self.vid)))
                if not page:
                    self.status = "no chat"
                    self._stop.wait(60)
                    continue
                cont = page["continuation"]
                self.status = "reading"
                backoff = 3
                while cont and not self._stop.is_set():
                    body = json.dumps({"context": {"client": {"clientName": "WEB", "clientVersion": page["version"], "hl": "en"}},
                                       "continuation": cont}).encode()
                    data = json.loads(self._fetch(CHAT_API.format(page["key"]), body))
                    messages, cont, wait = parse_chat_response(data)
                    for at, text in messages:
                        if at >= self.joined - 5:  # older lines are history from before we joined
                            self._add_unix(at, text)
                    self._stop.wait(wait)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError):
                if self._stop.is_set():
                    break
                self.status = "reconnecting"
                self._stop.wait(backoff)
                backoff = min(60, backoff * 2)
        self.status = "stopped"
