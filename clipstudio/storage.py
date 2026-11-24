"""Where MasterClip keeps big files: the finished clips and the work files (downloads, renders).

By default both live on C: (Videos\\MasterClip and the app's data folder). Buyers with a full C: drive
pick another folder, e.g. D:\\MasterClip. Clips then go straight into that folder and work files into
its "MasterClip work files" subfolder. The choice is kept in storage.json in the app's data folder.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

WORK_NAME = "MasterClip work files"
# A 30-minute 1080p section plus renders needs roughly this much room.
MIN_FREE_BYTES = 2 * 1024**3


def _config_dir() -> Path:
    return Path(os.environ.get("CLIPSTUDIO_LOGS") or os.environ.get("CLIPSTUDIO_DATA") or ".")


def config_path() -> Path:
    return _config_dir() / "storage.json"


def load() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def chosen_folder() -> Path | None:
    folder = str(load().get("folder") or "").strip()
    return Path(folder).expanduser() if folder else None


def work_root(default: Path) -> Path:
    folder = chosen_folder()
    return folder / WORK_NAME if folder else Path(default)


def old_work_roots() -> list[Path]:
    """Work folders used before the last change, so their projects still show in the app."""
    return [Path(p) for p in load().get("old_work", []) if isinstance(p, str) and p]


def check_writable(folder: Path) -> None:
    """Raises ValueError with a plain-words reason if MasterClip can't use this folder."""
    text = str(folder).strip()
    if not text:
        raise ValueError("Pick a folder first.")
    folder = Path(text).expanduser()
    if not folder.is_absolute():
        raise ValueError("Use a full folder path, like D:\\MasterClip.")
    anchor = Path(folder.anchor)
    if not anchor.exists():
        raise ValueError(f"The drive {folder.anchor} isn't there. Plug it in or pick another folder.")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".masterclip-{uuid.uuid4().hex[:8]}.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"MasterClip can't save into {folder} ({exc.strerror or exc}). Pick another folder.") from exc


def set_folder(folder: str | None, current_work: Path) -> None:
    data = load()
    old = [p for p in data.get("old_work", []) if isinstance(p, str)]
    if str(current_work) not in old:
        old.append(str(current_work))
    data["old_work"] = old[-10:]
    if folder:
        check_writable(Path(folder))
        data["folder"] = str(Path(folder).expanduser())
    else:
        data.pop("folder", None)
    save(data)


def free_bytes(folder: Path) -> int | None:
    path = Path(folder)
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def gb(n: int | None) -> str:
    return "?" if n is None else f"{n / 1024**3:.1f} GB"


def drive_name(folder: Path) -> str:
    anchor = Path(folder).anchor.rstrip("\\/")
    return f"{anchor} drive" if anchor else "disk"


def ensure_room(*folders: Path) -> None:
    """Stops a job before it starts if a disk is nearly full (the download would fail halfway)."""
    for folder in folders:
        free = free_bytes(folder)
        if free is not None and free < MIN_FREE_BYTES:
            raise ValueError(
                f"Your {drive_name(folder)} is almost full (only {gb(free)} free). MasterClip needs about 2 GB "
                "to download and make clips. Click \U0001f4c1 Save folder at the top and pick another drive "
                "(like D:), or free some space."
            )
