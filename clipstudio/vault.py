"""The buyer's own secrets (Zernio key, AI key) live in Windows Credential Manager via
keyring. Never in a file, never in a log, never sent back to the page."""

from __future__ import annotations

import os

SERVICE = "Clip Studio"
_MEMORY: dict[str, str] | None = {} if os.environ.get("CLIPSTUDIO_VAULT") == "memory" else None  # QA only


class VaultError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError as error:  # pragma: no cover - packaged app always has it
        raise VaultError("The secure key store isn't available on this computer.") from error
    if isinstance(keyring.get_keyring(), FailKeyring):
        raise VaultError("The secure key store isn't available on this computer.")
    return keyring


def get(name: str) -> str | None:
    if _MEMORY is not None:
        return _MEMORY.get(name)
    try:
        return _keyring().get_password(SERVICE, name) or None
    except VaultError:
        return None
    except Exception:  # noqa: BLE001 - a broken store must not crash the app
        return None


def put(name: str, value: str):
    value = (value or "").strip()
    if not value:
        raise VaultError("The key is empty.")
    if _MEMORY is not None:
        _MEMORY[name] = value
        return
    try:
        _keyring().set_password(SERVICE, name, value)
    except VaultError:
        raise
    except Exception as error:  # noqa: BLE001
        raise VaultError("Couldn't save the key in Windows Credential Manager.") from error


def delete(name: str):
    if _MEMORY is not None:
        _MEMORY.pop(name, None)
        return
    try:
        _keyring().delete_password(SERVICE, name)
    except Exception:  # noqa: BLE001 - already gone is fine
        pass


def has(name: str) -> bool:
    return bool(get(name))


def hint(value: str | None) -> str:
    """Last 4 characters only, for 'key ending in ...' labels."""
    return f"…{value[-4:]}" if value and len(value) > 8 else ""
