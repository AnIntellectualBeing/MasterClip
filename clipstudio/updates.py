"""Is there a newer MasterClip? Asks GitHub at most once a day, never installs anything.

The only thing sent is a plain request for the latest public release of
AnIntellectualBeing/MasterClip. The answer is kept in update-check.json so the app asks again
only after a day. The buyer sees a banner with a Download button that opens the release page.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = "AnIntellectualBeing/MasterClip"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
EVERY = 24 * 3600


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.6.0' / '1.6' / 'MasterClip 1.10.2' -> (1, 6, 0); None when there is no version."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


def _fetch() -> dict:
    request = Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "MasterClip-update-check"})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read(1024 * 1024).decode("utf-8"))


def check(root: Path, current: str, fetch=_fetch, now=time.time, force=False) -> dict:
    """{'available', 'latest', 'current', 'url', 'notes', 'checked'}; never raises."""
    path = Path(root) / "update-check.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if os.environ.get("CLIPSTUDIO_NO_UPDATE_CHECK"):
        cached = {**cached, "checked": now()}  # QA / tests: never reach the internet
    elif (force or not isinstance(cached, dict) or not cached.get("checked")
          or now() - float(cached.get("checked") or 0) >= EVERY):
        try:
            data = fetch()
            cached = {"checked": now(), "latest": str(data.get("tag_name") or data.get("name") or ""),
                      "url": str(data.get("html_url") or PAGE),
                      "notes": str(data.get("body") or "")[:600]}
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, AttributeError):
            # offline: keep the last answer, try again in an hour (not every screen load)
            cached = {**(cached if isinstance(cached, dict) else {}), "checked": now() - EVERY + 3600}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cached), encoding="utf-8")
        except OSError:
            pass
    latest = str(cached.get("latest") or "")
    url = str(cached.get("url") or PAGE)
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = PAGE  # only ever send the buyer to our own release page
    return {"available": is_newer(latest, current), "latest": latest.lstrip("vV"), "current": current,
            "url": url, "notes": str(cached.get("notes") or ""), "checked": cached.get("checked")}
