# PyInstaller one-folder build of Clip Studio. Run through packaging/build.ps1.
# Needs env CLIPSTUDIO_VENDOR = folder with bin\ffmpeg.exe, bin\ffprobe.exe, bin\deno.exe.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
VENDOR = Path(os.environ["CLIPSTUDIO_VENDOR"])
ICON = str(ROOT / "packaging" / "clipstudio.ico")

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "fonts"), "fonts"),
    (str(ROOT / "emoji"), "emoji"),  # Twemoji PNGs for the Emoji caption switch (CC-BY 4.0)
    (str(ROOT / "models"), "models"),
]
datas += collect_data_files("faster_whisper")
datas += collect_data_files("tzdata")
binaries = [(str(VENDOR / "bin" / name), "bin") for name in ("ffmpeg.exe", "ffprobe.exe", "deno.exe")]
binaries += collect_dynamic_libs("ctranslate2")
hidden = (
    collect_submodules("clipstudio")
    + collect_submodules("uvicorn")
    + collect_submodules("tzdata")
    + ["keyring.backends.Windows", "multipart", "python_multipart"]
)
dev_only = ["tkinter", "playwright", "pytest", "_pytest", "IPython", "matplotlib", "PyInstaller"]  # PIL draws the cards (overlays.py)

app = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=dev_only,
    noarchive=False,
)
# One package ships an old msvcp140.dll (14.29); newer code (ctranslate2 / Whisper) crashes on it with an
# access violation. Ship the current Microsoft C++ runtime from System32 instead (app-local is allowed).
RUNTIME = ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
app.binaries = [b for b in app.binaries if Path(b[0]).name.lower() not in RUNTIME]
app.binaries += [(name, str(system32 / name), "BINARY") for name in RUNTIME if (system32 / name).is_file()]

ytdlp = Analysis(
    [str(ROOT / "packaging" / "ytdlp_entry.py")],
    pathex=[str(ROOT)],
    datas=collect_data_files("yt_dlp_ejs"),
    hiddenimports=collect_submodules("yt_dlp_ejs"),
    excludes=dev_only + ["PIL", "numpy", "cv2", "faster_whisper", "ctranslate2", "onnxruntime", "av", "webview"],
)

app_exe = EXE(
    PYZ(app.pure),
    app.scripts,
    [],
    exclude_binaries=True,
    name="ClipStudio",
    console=False,
    icon=ICON,
    version=str(ROOT / "packaging" / "version.txt"),
)
ytdlp_exe = EXE(
    PYZ(ytdlp.pure),
    ytdlp.scripts,
    [],
    exclude_binaries=True,
    name="clipstudio-ytdlp",
    console=True,
    icon=ICON,
)
COLLECT(
    app_exe, app.binaries, app.datas,
    ytdlp_exe, ytdlp.binaries, ytdlp.datas,
    name="Clip Studio",
)
