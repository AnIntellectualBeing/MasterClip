"""Small Zernio REST client for the buyer's own account (their key, their accounts).

Endpoints used: GET /accounts, GET /profiles, GET /connect/{platform}, POST /media/presign
(+ PUT upload), POST /posts, GET /posts/{id}, POST /posts/{id}/retry,
POST /comment-automations.
"""

from __future__ import annotations

import os
import uuid
import datetime as dt
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

API = os.environ.get("CLIPSTUDIO_ZERNIO_API", "https://zernio.com/api/v1")  # override only for local QA
PLATFORMS = ("instagram", "youtube")


class ZernioError(RuntimeError):
    def __init__(self, message, *, status_code=None, retry_after=None, platform=None, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.platform = platform
        self.details = details or {}


def _friendly(response: requests.Response) -> str:
    try:
        data = response.json()
        detail = data.get("error") or data.get("message") or ""
    except ValueError:
        data = {}
        detail = ""
    if detail and (data.get("platform") or any(t in str(detail).lower() for t in ("quota", "upload limit", "uploadlimit"))):
        return str(detail)
    if response.status_code in (401, 403):
        return "Zernio didn't accept that key. Copy it again from zernio.com → Settings → API keys."
    if response.status_code == 429:
        return str(detail) if detail else "Zernio is temporarily limiting requests. Retrying later."
    if response.status_code >= 500:
        return "Zernio is having trouble right now. Try again in a few minutes."
    return f"Zernio said: {detail}" if detail else f"Zernio returned an error ({response.status_code})."


class Zernio:
    def __init__(self, key: str, session: requests.Session | None = None, api: str = API):
        if not key:
            raise ZernioError("Add your Zernio key first.")
        self.api = api.rstrip("/")
        self.http = session or requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {key}", "User-Agent": "ClipStudio/1.0"})

    def _call(self, method: str, path: str, *, timeout: float = 30, **kwargs):
        try:
            response = self.http.request(method, f"{self.api}{path}", timeout=timeout, **kwargs)
        except requests.RequestException as error:
            raise ZernioError("Couldn't reach Zernio. Retrying when your connection returns.", retry_after=60) from error
        if response.status_code >= 400:
            try:
                data = response.json()
            except ValueError:
                data = {}
            details = data.get("details") or {}
            wait = response.headers.get("Retry-After") or details.get("retryAfterSeconds")
            try:
                wait = max(1, float(wait)) if wait is not None else None
            except (TypeError, ValueError):
                try:
                    wait = max(1, (parsedate_to_datetime(wait) - dt.datetime.now(dt.timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    wait = None
            if wait is None and response.status_code >= 500:
                wait = 120
            raise ZernioError(_friendly(response), status_code=response.status_code, retry_after=wait,
                              platform=data.get("platform"), details=details)
        try:
            return response.json()
        except ValueError:
            return {}

    # --- setup ---
    def accounts(self) -> list[dict]:
        data = self._call("GET", "/accounts")
        items = data.get("accounts", data if isinstance(data, list) else [])
        out = []
        for item in items:
            out.append({
                "id": item.get("_id") or item.get("id"),
                "platform": item.get("platform"),
                "username": item.get("username") or item.get("displayName") or item.get("name") or "",
                "profile_id": item.get("profileId") if isinstance(item.get("profileId"), str)
                else (item.get("profileId") or {}).get("_id"),
                "active": item.get("isActive", True) is not False,
            })
        return [a for a in out if a["id"]]

    def default_profile(self) -> str | None:
        data = self._call("GET", "/profiles")
        profiles = data.get("profiles", data if isinstance(data, list) else [])
        for profile in profiles:
            if profile.get("isDefault"):
                return profile.get("_id")
        return profiles[0].get("_id") if profiles else None

    def connect_url(self, platform: str, profile_id: str, redirect_url: str | None = None) -> str:
        if platform not in PLATFORMS:
            raise ZernioError("Only Instagram and YouTube can be connected here.")
        params = {"profileId": profile_id}
        if redirect_url:
            params["redirect_url"] = redirect_url
        data = self._call("GET", f"/connect/{platform}", params=params)
        url = data.get("authUrl")
        if not url:
            raise ZernioError("Zernio didn't return a sign-in link. Try again.")
        return url

    # --- posting ---
    def upload(self, path: Path, content_type: str = "video/mp4") -> str:
        size = os.path.getsize(path)
        presign = self._call("POST", "/media/presign", json={"filename": path.name, "contentType": content_type, "size": size})
        try:
            with open(path, "rb") as handle:
                put = requests.put(presign["uploadUrl"], data=handle, headers={"Content-Type": content_type}, timeout=900)
        except (KeyError, requests.RequestException) as error:
            raise ZernioError("The video upload didn't finish. Retrying automatically.", retry_after=60) from error
        if put.status_code >= 400:
            raise ZernioError("The video upload didn't finish.", status_code=put.status_code,
                              retry_after=120 if put.status_code == 429 or put.status_code >= 500 else None)
        return presign["publicUrl"]

    def create_post(self, *, video_url: str, caption: str, title: str, accounts: dict[str, str],
                    when: str | None, timezone: str, idempotency_key: str | None = None, short: bool = False,
                    thumbnail: str | None = None) -> dict:
        platforms = []
        for platform, account_id in accounts.items():
            entry = {"platform": platform, "accountId": account_id, "customContent": caption}
            if platform == "youtube":
                yt_title, description = (title or "Clip").strip(), caption
                if short:
                    # YouTube files vertical videos of 3 min or less as Shorts; #Shorts makes it certain.
                    if "#shorts" not in yt_title.lower():
                        yt_title = (yt_title[: 100 - 8].rstrip() + " #Shorts")
                    if "#shorts" not in description.lower():
                        description = description.rstrip() + "\n\n#Shorts"
                entry["customContent"] = description[:4900]  # YouTube description (chapters live here)
                entry["platformSpecificData"] = {"title": yt_title[:100], "visibility": "public", "madeForKids": False}
            else:
                entry["customContent"] = caption[:2200]
            platforms.append(entry)
        media = {"url": video_url, "type": "video"}
        if thumbnail:
            media["thumbnail"] = thumbnail  # cover image; Zernio applies it where the platform allows
        body = {"content": caption[:2200], "mediaItems": [media], "platforms": platforms, "timezone": timezone}
        if when:
            body["scheduledFor"] = when
        else:
            body["publishNow"] = True
        data = self._call("POST", "/posts", json=body, timeout=300,
                          headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())})
        post = data.get("post") or {}
        if not post.get("_id"):
            raise ZernioError("Waiting for Zernio to confirm the post. Retrying with the same post identity.", retry_after=60)
        return post

    def get_post(self, post_id: str) -> dict:
        data = self._call("GET", f"/posts/{post_id}")
        return data.get("post") or data

    def retry(self, post_id: str) -> dict:
        data = self._call("POST", f"/posts/{post_id}/retry", timeout=300)
        return data.get("post") or data

    def delete_post(self, post_id: str):
        return self._call("DELETE", f"/posts/{post_id}")

    def comment_dm(self, *, profile_id: str, account_id: str, keyword: str, message: str, reply: str, name: str,
                   post_id: str | None = None, platform_post_id: str | None = None) -> dict:
        """Comment-to-DM on one reel. With post_id (Zernio's id) it can be created before the
        reel is live: Zernio arms it when the post publishes."""
        body = {
            "profileId": profile_id,
            "accountId": account_id,
            "name": name[:80],
            "trigger": "comment",
            "keywords": [keyword],
            "matchMode": "word",
            "dmMessage": message,
            "repeatPolicy": {"mode": "once"},
        }
        if post_id:
            body["postId"] = post_id
        elif platform_post_id:
            body["platformPostId"] = platform_post_id
        if reply:
            body["commentReply"] = reply
        return self._call("POST", "/comment-automations", json=body)
