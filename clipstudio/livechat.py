"""Live Twitch chat while a stream is being recorded: no account, no key.

Twitch lets anyone read a channel's chat as an anonymous "justinfan" user over IRC. We only read;
nothing is ever sent to the chat, and nothing about the user goes out except the channel name.

What it gives the live capture:
  * every message with its time on the recording clock, so each window's moment ranking gets the
    chat signal (bursts, laugh emotes, "clip it") the same way past broadcasts do;
  * a spike rule: 10 s buckets compared with the last 5 minutes. Chat reacts a few seconds AFTER the
    moment, so a spike makes the capture clip the last stretch right away instead of waiting for the
    5-minute window;
  * a 0-100 hype meter for the screen.
The bucket/sigma spike rule is the common pattern of open-source live clippers (NeonClipping, MIT,
credited in NOTICE); this file is our own code.
"""

from __future__ import annotations

import re
import socket
import statistics
import threading
import time

HOST, PORT = "irc.chat.twitch.tv", 6667
BUCKET = 10        # seconds per chat bucket
HISTORY = 30       # buckets in the baseline (5 minutes)
SIGMA = 2.5        # spike = above mean + SIGMA * stdev ...
RATIO = 2.5        # ... and at least RATIO x the usual count ...
MIN_MSGS = 8       # ... and at least this many messages in the bucket
MIN_HISTORY = 6    # buckets of chat needed before anything counts as a spike
COOLDOWN = 45      # seconds between two spike clips
MAX_KEPT = 200_000

_LOGIN_RE = re.compile(r"twitch\.tv/([A-Za-z0-9_]{2,25})(?:[/?#]|$)", re.I)
_NOT_CHANNELS = {"videos", "directory", "search", "settings", "downloads", "p", "jobs", "turbo", "subscriptions", "inventory", "wallet"}
_PRIVMSG = re.compile(r"^(?:@\S+ )?:[^ ]+ PRIVMSG #\S+ :(.*)$")


def channel_login(url: str) -> str | None:
    """'https://www.twitch.tv/somechannel' -> 'somechannel'; None for VODs, clips and other sites."""
    m = _LOGIN_RE.search(str(url or ""))
    if not m or m.group(1).lower() in _NOT_CHANNELS or "/videos/" in str(url) or "/clip/" in str(url):
        return None
    return m.group(1).lower()


def is_spike(count: int, history: list[int]) -> bool:
    """True when this bucket is a clear burst against the buckets before it."""
    filled = [c for c in history if c > 0]
    if len(filled) < MIN_HISTORY or count < MIN_MSGS:
        return False
    mean = statistics.mean(history)
    spread = statistics.pstdev(history)
    return count > mean + SIGMA * spread and count >= RATIO * max(mean, 1.0)


def hype_percent(last30: int, usual_bucket: float, history_buckets: int) -> int:
    """Messages in the last 30 s as 0-100: 100 is four times this chat's usual speed.
    Until there is a minute of history an absolute scale is used (60 messages in 30 s = 100)."""
    if history_buckets < MIN_HISTORY:
        return int(min(100, round(last30 / 60 * 100)))
    usual30 = max(usual_bucket * 3.0, 3.0)
    return int(min(100, max(0, round((last30 / usual30 - 1) / 3 * 100))))


def hype_label(percent: int) -> str:
    return "peak hype" if percent >= 85 else "hot" if percent >= 55 else "building" if percent >= 25 else "quiet"


class LiveChat:
    """Reads one channel's chat in a background thread. Times are seconds since `clock_start`
    (time.monotonic()), so they line up with the recording."""

    def __init__(self, login: str, clock_start: float | None = None, connect=None):
        self.login = login.lower()
        self.clock_start = time.monotonic() if clock_start is None else clock_start
        self.messages: list[tuple[float, str]] = []
        self.status = "connecting"
        self._connect = connect or (lambda: socket.create_connection((HOST, PORT), timeout=30))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._spike_seen_bucket = -1
        self._last_spike = -1e9

    # ---- reading ----
    def start(self):
        self._thread = threading.Thread(target=self._run, name="live-chat", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def now(self) -> float:
        return time.monotonic() - self.clock_start

    def add(self, text: str, at: float | None = None):
        with self._lock:
            if len(self.messages) < MAX_KEPT:
                self.messages.append((round(self.now() if at is None else at, 2), text[:300]))

    def _run(self):
        backoff = 2
        while not self._stop.is_set():
            sock = None
            try:
                sock = self._connect()
                sock.settimeout(1.0)
                nick = f"justinfan{int(time.time() * 1000) % 90000 + 10000}"
                sock.sendall(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.login}\r\n".encode())
                self.status = "reading"
                backoff = 2
                buffer = b""
                quiet_since = time.monotonic()
                while not self._stop.is_set():
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        if time.monotonic() - quiet_since > 360:  # Twitch pings every ~5 min; silence longer = dead
                            raise OSError("chat went silent")
                        continue
                    if not data:
                        raise OSError("chat closed")
                    quiet_since = time.monotonic()
                    buffer += data
                    *lines, buffer = buffer.split(b"\r\n")
                    for raw in lines:
                        line = raw.decode("utf-8", errors="replace")
                        if line.startswith("PING"):
                            sock.sendall(("PONG" + line[4:] + "\r\n").encode())
                            continue
                        m = _PRIVMSG.match(line)
                        if m:
                            self.add(m.group(1))
            except OSError:
                if self._stop.is_set():
                    break
                self.status = "reconnecting"
                self._stop.wait(backoff)
                backoff = min(60, backoff * 2)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        self.status = "stopped"

    # ---- using it ----
    def between(self, start: float, end: float, delay: float = 0.0) -> list[tuple[float, str]]:
        """Messages in [start, end) on the recording clock, as times relative to `start`.
        `delay` moves chat earlier: the recording starts a little after the recorder process."""
        with self._lock:
            return [(round(t - delay - start, 2), m) for t, m in self.messages if start <= t - delay < end]

    def _counts(self, until: float) -> dict[int, int]:
        out: dict[int, int] = {}
        with self._lock:
            for t, _ in self.messages:
                if t < until:
                    b = int(t // BUCKET)
                    out[b] = out.get(b, 0) + 1
        return out

    def meter(self) -> dict:
        """{'hype': 0-100, 'label': ..., 'per_min': messages in the last minute, 'status': ...}"""
        now = self.now()
        with self._lock:
            last30 = sum(1 for t, _ in self.messages if now - 30 <= t < now)
            last60 = sum(1 for t, _ in self.messages if now - 60 <= t < now)
        done = int(now // BUCKET) - 1
        counts = self._counts(now)
        history = [counts.get(b, 0) for b in range(max(0, done - HISTORY), done + 1)]
        usual = statistics.mean(history) if history else 0.0
        hype = hype_percent(last30, usual, sum(1 for c in history if c > 0))
        return {"hype": hype, "label": hype_label(hype), "per_min": last60, "status": self.status}

    def new_spike(self) -> float | None:
        """Chat-clock time (middle of the bucket) of the earliest spike among the buckets that
        finished since the last call, or None. Buckets that passed while clips were being made
        are still checked, one spike per call."""
        now = self.now()
        done = int(now // BUCKET) - 1
        if done <= self._spike_seen_bucket:
            return None
        counts = self._counts(now)
        for b in range(max(self._spike_seen_bucket + 1, done - HISTORY), done + 1):
            self._spike_seen_bucket = b
            history = [counts.get(x, 0) for x in range(max(0, b - HISTORY), b)]
            at = b * BUCKET + BUCKET / 2
            if is_spike(counts.get(b, 0), history) and at - self._last_spike >= COOLDOWN:
                self._last_spike = at
                return at
        return None
