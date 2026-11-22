"""Where things live, in a dev checkout and in the installed app (PyInstaller one-folder).

Installed layout:
  Clip Studio\\ClipStudio.exe           the window (no console)
  Clip Studio\\clipstudio-ytdlp.exe     yt-dlp as its own console program (run hidden)
  Clip Studio\\_internal\\static, fonts, models, bin\\ffmpeg.exe ffprobe.exe deno.exe
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else RESOURCES
BIN = RESOURCES / "bin"


def bundled(name: str) -> str | None:
    exe = BIN / f"{name}.exe"
    return str(exe) if exe.is_file() else None


def ytdlp_base() -> list[str]:
    """The yt-dlp program plus the JavaScript runtime YouTube needs."""
    if FROZEN:
        command = [str(APP_DIR / "clipstudio-ytdlp.exe")]
    else:
        command = [sys.executable, "-m", "yt_dlp"]
    deno = bundled("deno")
    if deno:
        runtime = f"deno:{deno}"
    elif shutil.which("deno"):
        runtime = "deno"
    else:
        runtime = "node"
    return command + ["--js-runtimes", runtime]
