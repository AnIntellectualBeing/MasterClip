"""Start the local studio; settings apply only to this process."""

import argparse
import json
import os
import threading
import time
import urllib.request
import webbrowser
import ctypes
from pathlib import Path
import uvicorn


def resolve_director_model(value):
    if value != "auto":
        return value
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=1
        ) as response:
            installed = json.loads(response.read()).get("models", [])
        return next(
            (
                model["name"]
                for model in installed
                if str(model.get("name", "")).startswith("phi4-mini")
            ),
            "",
        )
    except (OSError, ValueError, TypeError):
        return ""


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="GTA Clip Studio local server")
    parser.add_argument("--port", type=int, default=settings.get("port", 8765))
    parser.add_argument(
        "--no-director",
        action="store_true",
        help="Use audio/motion/speech cues without model reranking",
    )
    parser.add_argument("--director-model", default=settings.get("director_model", ""))
    parser.add_argument(
        "--open", action="store_true", help="Open the dashboard in your browser"
    )
    parser.add_argument(
        "--tool", choices=["highlights", "shorts"], default="highlights"
    )
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    try:
        running = json.loads(
            urllib.request.urlopen(url + "/api/health", timeout=1).read()
        )
        if running.get("application") == "GTA Clip Studio":
            webbrowser.open(url + "/?tool=" + args.tool)
            raise SystemExit(0)
    except (OSError, ValueError):
        pass
    if args.no_director:
        os.environ.pop("CLIPSTUDIO_DIRECTOR_MODEL", None)
    elif model := resolve_director_model(args.director_model):
        os.environ.setdefault("CLIPSTUDIO_DIRECTOR_MODEL", model)
    print(f"GTA Clip Studio: http://127.0.0.1:{args.port}")
    print("Leave this window open while editing. Press Ctrl+C to stop.")
    (root / "data").mkdir(exist_ok=True)
    pid_file = root / "data" / f"server-{args.port}.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    if args.open:

        def open_when_ready():
            for attempt in range(40):
                try:
                    urllib.request.urlopen(url + "/api/health", timeout=1).close()
                    webbrowser.open(url + "/?tool=" + args.tool)
                    return
                except OSError:
                    time.sleep(0.25)

        threading.Thread(target=open_when_ready, daemon=True).start()
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    try:
        uvicorn.run("clipstudio.app:app", host="127.0.0.1", port=args.port)
    finally:
        if os.name == "nt":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip() == str(
            os.getpid()
        ):
            pid_file.unlink()
