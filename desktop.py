"""Clip Studio desktop window: starts the local engine and shows the app in its own
window (pywebview / Edge WebView2). No terminal. Closing the window stops everything.

Run with pythonw (no console):  .venv\\Scripts\\pythonw.exe desktop.py
Add --browser to open in the normal web browser instead (for testing), or --headless for
the engine alone (automated QA).
"""

from __future__ import annotations

import ctypes
import faulthandler
import json
import logging
import platform
import traceback
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
APP_NAME = "MasterClip"  # what the buyer sees
DATA_NAME = "Clip Studio"  # data folder name: never change, or old projects and logs vanish


def data_dir() -> Path:
    # Packaged app: per-user folder. Dev checkout: ./data as before.
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / DATA_NAME
    else:
        base = ROOT / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def setup_logging(folder: Path):
    logging.basicConfig(
        filename=folder / "app.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # pythonw has no console: send stray prints/tracebacks to the log, not into the void.
    if sys.stdout is None or sys.stderr is None:
        log = open(folder / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


_crash_file = None


def crash_log(folder: Path) -> Path:
    return folder / "crash.log"


def setup_crash_log(folder: Path):
    """Native crashes (faulthandler) and Python errors on any thread go to crash.log."""
    global _crash_file
    path = crash_log(folder)
    try:
        if path.is_file() and path.stat().st_size > 2_000_000:
            path.replace(path.with_suffix(".old.log"))
        _crash_file = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - kept open for faulthandler
    except OSError:
        return
    faulthandler.enable(file=_crash_file, all_threads=True)

    def write(kind, exc_type, exc, tb):
        try:
            from clipstudio import __version__
        except Exception:  # noqa: BLE001
            __version__ = "?"
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _crash_file.write(
            f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {kind} | Clip Studio {__version__} | "
            f"Windows {platform.version()} ===\n{text}"
        )
        logging.error("%s: %s", kind, text)

    def main_hook(exc_type, exc, tb):
        write("crash", exc_type, exc, tb)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        write(f"error in thread {args.thread.name if args.thread else '?'}", args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def note_crash(message: str):
    if _crash_file:
        _crash_file.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    logging.error(message)


def settings() -> dict:
    try:
        return json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def running_studio(url: str) -> bool:
    """True only for a Clip Studio engine from THIS install. (The owner's window once attached to a
    developer's test engine on the same port, and died with it.)"""
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            health = json.loads(response.read())
    except (OSError, ValueError):
        return False
    if health.get("application") != "GTA Clip Studio":
        return False
    return os.path.normcase(str(health.get("app_dir") or "")) == os.path.normcase(str(ROOT.resolve()))


def free_port(preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 20)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_engine(port: int):
    import uvicorn

    os.environ.setdefault("CLIPSTUDIO_LOGS", str(data_dir()))
    os.environ.setdefault("CLIPSTUDIO_DATA", str(data_dir() / "projects" if getattr(sys, "frozen", False) else data_dir()))
    config = uvicorn.Config(
        "clipstudio.app:app", host="127.0.0.1", port=port, log_config=None, access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="clip-engine", daemon=True)
    thread.start()
    return server, thread


def wait_until_ready(url: str, seconds: float = 30) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if running_studio(url):
            return True
        time.sleep(0.2)
    return False


def keep_awake(on: bool):
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if on else 0x80000000)


def show_error(message: str):
    logging.error(message)
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)


def friendly_crash(folder: Path, what: str):
    note_crash(what)
    show_error(
        "Something went wrong and MasterClip had to close.\n\n"
        "Here's what to do:\n"
        "1. Open MasterClip again. Your finished clips are safe.\n"
        "2. If it keeps happening, restart your PC.\n"
        "3. Still stuck? Email us the file below so we can fix it:\n"
        f"   {crash_log(folder)}"
    )


def watch_webview_process(folder: Path):
    """If the page's process dies (out of memory, GPU driver), reload it instead of a blank window."""
    try:
        from webview.platforms import edgechromium
    except Exception:  # noqa: BLE001 - other backends
        return
    original = edgechromium.EdgeChrome.on_webview_ready

    def on_ready(self, sender, args):
        original(self, sender, args)
        if not getattr(args, "IsSuccess", False):
            note_crash(f"WebView2 didn't start: {getattr(args, 'InitializationException', '')}")
            return

        def failed(core, info):
            kind = str(getattr(info, "ProcessFailedKind", "?"))
            note_crash(f"WebView2 process failed: {kind}")
            if "Render" in kind or "Frame" in kind:
                try:
                    core.Reload()
                except Exception as error:  # noqa: BLE001
                    note_crash(f"reload after failure didn't work: {error}")
            elif "Browser" in kind:
                friendly_crash(folder, "WebView2 browser process exited")

        try:
            sender.CoreWebView2.ProcessFailed += failed
        except Exception as error:  # noqa: BLE001
            logging.warning("couldn't watch the WebView2 process: %s", error)

    edgechromium.EdgeChrome.on_webview_ready = on_ready


class DesktopWindows:
    """Additional windows share the running engine; each page owns its own session."""

    def __init__(self, url, backend):
        self.url = url.rstrip("/")
        self.backend = backend

    def new_window(self):
        self.backend.create_window(APP_NAME, self.url + "/?new=1", width=1280, height=880,
                                   min_size=(900, 640), background_color="#FBF6EF", text_select=True,
                                   js_api=self)
        return {"opened": True}


def main():
    folder = data_dir()
    setup_logging(folder)
    setup_crash_log(folder)
    use_browser = "--browser" in sys.argv
    headless = "--headless" in sys.argv  # QA: engine only, no window
    port = int(settings().get("port", 8766))
    url = f"http://127.0.0.1:{port}"
    server = None
    if not running_studio(url):
        port = free_port(port)
        url = f"http://127.0.0.1:{port}"
        server, _ = start_engine(port)
        if not wait_until_ready(url):
            show_error("MasterClip couldn't start. Please restart your computer and try again.\n\nDetails are in " + str(folder / "app.log"))
            return 1
    logging.info("Clip Studio engine at %s", url)
    keep_awake(True)
    try:
        if use_browser or headless:
            if use_browser:
                webbrowser.open(url)
            while server is None or not server.should_exit:
                time.sleep(1)
            return 0
        try:
            import webview
        except ImportError:
            webbrowser.open(url)
            while True:
                time.sleep(3600)
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        watch_webview_process(folder)
        windows = DesktopWindows(url, webview)
        window = webview.create_window(
            APP_NAME, url, width=1280, height=880, min_size=(900, 640),
            background_color="#FBF6EF", text_select=True, js_api=windows,
        )
        user_closed = threading.Event()
        window.events.closing += lambda *_: user_closed.set()
        try:
            webview.start(private_mode=False, storage_path=str(folder / "webview"))
        except Exception:  # noqa: BLE001
            note_crash("window crashed:\n" + traceback.format_exc())
            friendly_crash(folder, "window crashed")
            return 1
        if not user_closed.is_set():
            friendly_crash(folder, "the window closed without the user closing it")
            return 1
        logging.info("window closed by the user")
        return 0
    finally:
        keep_awake(False)
        if server is not None:
            server.should_exit = True


if __name__ == "__main__":
    sys.exit(main())
