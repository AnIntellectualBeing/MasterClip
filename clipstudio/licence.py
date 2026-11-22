"""Licence keys and the free trial.

Keys are signed offline with the seller's Ed25519 private key (never in this app or repo).
The app only holds the public key, so a key works with no server at all.

Key text:  CS1-XXXXX-XXXXX-...   (base32 of  payload_json + 64-byte signature)
payload:   {"v":1,"id":"<order id>","s":<seats>,"t":"<yyyymmdd>"}

Optional 2-PC limit: when shop.LICENCE_API is set (clipstudio/shop.py, or env for QA), the key is
activated once per PC against that server. With no server, the signature check alone decides.

Free trial: TRIAL_VIDEOS new videos without a key. The count lives in Windows Credential Manager.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time

from . import vault
from .shop import BUY_URL, LICENCE_API, PRICE_TEXT, RECOVER_URL

# The seller's PUBLIC key (hex). Replace with the output of `tools/licence_admin.py keygen`.
PUBLIC_KEY = "39f5639faae48925e3e9cc5e8d345650476db42489355e735baa5b3fb69e4247"
TRIAL_VIDEOS = 3
KEY_NAME = "licence_key"
TRIAL_NAME = "trial_used"
PREFIX = "CS1-"


class LicenceError(RuntimeError):
    pass


# ---------------- key format ----------------
def _b32(data: bytes) -> str:
    return base64.b32encode(data).decode().rstrip("=")


def _unb32(text: str) -> bytes:
    text = text.upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def pretty(raw: str) -> str:
    return PREFIX + "-".join(raw[i : i + 5] for i in range(0, len(raw), 5))


def normalise(text: str) -> str:
    text = re.sub(r"[\s\-]", "", (text or "").strip().upper())
    if text.startswith("CS1"):
        text = text[3:]
    return text


def make_key(private_key, order_id: str, seats: int = 2, issued: str | None = None) -> str:
    """Seller side (tools/licence_admin.py, or the shop's server)."""
    payload = json.dumps(
        {"v": 1, "id": str(order_id)[:40], "s": int(seats), "t": issued or time.strftime("%Y%m%d")},
        separators=(",", ":"), sort_keys=True,
    ).encode()
    return pretty(_b32(payload + private_key.sign(payload)))


def read_key(text: str, public_hex: str | None = None) -> dict:
    """Checks the signature and returns the payload, or raises LicenceError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public_hex = public_hex or PUBLIC_KEY
    if not normalise(text):
        raise LicenceError("Paste your licence key first. It's in the email you got after buying.")
    if len(normalise(text)) < 20:
        raise LicenceError("That key is too short. Copy the whole key from your email (it starts with CS1-).")
    try:
        blob = _unb32(normalise(text))
    except (ValueError, base64.binascii.Error) as error:
        raise LicenceError("That key has a typo. Copy it again from your email.") from error
    if len(blob) < 64 + 10:
        raise LicenceError("That key is too short. Copy the whole key from your email.")
    payload, signature = blob[:-64], blob[-64:]
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex)).verify(signature, payload)
        data = json.loads(payload)
    except (InvalidSignature, ValueError) as error:
        raise LicenceError("That key isn't valid for Clip Studio.") from error
    if data.get("v") != 1 or not data.get("id"):
        raise LicenceError("That key isn't valid for Clip Studio.")
    return data


# ---------------- this PC ----------------
def machine_id() -> str:
    raw = ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            raw = winreg.QueryValueEx(key, "MachineGuid")[0]
    except OSError:
        raw = os.environ.get("COMPUTERNAME", "") + os.environ.get("USERNAME", "")
    return hashlib.sha256(("clipstudio:" + raw).encode()).hexdigest()[:24]


def activate_online(key_text: str, data: dict, api: str, http=None) -> None:
    """Ask the seller's server to count this PC (max data['s'] PCs per key)."""
    import requests

    http = http or requests
    try:
        reply = http.post(api.rstrip("/") + "/activate", timeout=15, json={
            "key": normalise(key_text), "id": data["id"], "machine": machine_id(),
        })
    except Exception as error:  # noqa: BLE001
        raise LicenceError("Couldn't reach the licence server. Check your internet and try again.") from error
    if reply.status_code == 200:
        return
    try:
        message = reply.json().get("error")
    except ValueError:
        message = None
    if reply.status_code == 409:
        raise LicenceError(message or f"This key is already used on {data.get('s', 2)} PCs. "
                                      "Remove it from an old PC first, or contact support.")
    raise LicenceError(message or "The licence server said no. Contact support with your order id.")


# ---------------- state the app uses ----------------
class Licence:
    def __init__(self, public_hex: str | None = None, api: str | None = None, http=None):
        self.public_hex = public_hex or PUBLIC_KEY
        self.api = LICENCE_API if api is None else api
        self.http = http

    def _used(self) -> int:
        try:
            return max(0, int(vault.get(TRIAL_NAME) or 0))
        except (TypeError, ValueError):
            return TRIAL_VIDEOS

    def status(self) -> dict:
        shop = {"buy_url": BUY_URL, "recover_url": RECOVER_URL, "price": PRICE_TEXT}
        key = vault.get(KEY_NAME)
        if key:
            try:
                data = read_key(key, self.public_hex)
                return {"licensed": True, "order": data["id"], "seats": data.get("s", 2), **shop}
            except LicenceError:
                pass
        used = self._used()
        return {"licensed": False, "trial_total": TRIAL_VIDEOS, "trial_left": max(0, TRIAL_VIDEOS - used), **shop}

    def activate(self, key_text: str) -> dict:
        data = read_key(key_text, self.public_hex)
        if self.api:
            activate_online(key_text, data, self.api, self.http)
        vault.put(KEY_NAME, pretty(normalise(key_text)))
        return self.status()

    def remove(self) -> dict:
        key = vault.get(KEY_NAME)
        if key and self.api:
            try:
                import requests

                (self.http or requests).post(self.api.rstrip("/") + "/deactivate", timeout=10,
                                             json={"key": normalise(key), "machine": machine_id()})
            except Exception:  # noqa: BLE001 - removing locally still works offline
                pass
        vault.delete(KEY_NAME)
        return self.status()

    def check_new_video(self) -> None:
        """Raise LicenceError when the trial is used up and there is no key."""
        state = self.status()
        if not state["licensed"] and state["trial_left"] <= 0:
            raise LicenceError(f"Your {TRIAL_VIDEOS} free videos are used up. Enter your licence key to keep making clips.")

    def count_new_video(self) -> None:
        if not self.status()["licensed"]:
            vault.put(TRIAL_NAME, str(self._used() + 1))
