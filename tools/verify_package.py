"""Smoke test an actual packaged EXE with isolated app data and no credentials."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import requests
from playwright.sync_api import sync_playwright

exe = Path(sys.argv[1]).resolve()
assert exe.is_file(), exe
root = Path(__file__).resolve().parent.parent
out = root / "artifacts" / "package-smoke"
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="masterclip-package-") as temp:
    env = dict(os.environ, LOCALAPPDATA=temp, CLIPSTUDIO_VAULT="memory", CLIPSTUDIO_DATA=str(Path(temp) / "projects"),
               CLIPSTUDIO_LOGS=str(Path(temp) / "Clip Studio"), CLIPSTUDIO_ZERNIO_API="http://127.0.0.1:1/never-called")
    child = subprocess.Popen([str(exe), "--headless"], env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        log = Path(temp) / "Clip Studio" / "app.log"
        deadline = time.monotonic() + 60
        base = None
        while time.monotonic() < deadline:
            assert child.poll() is None, log.read_text(errors="replace") if log.exists() else "app exited"
            if log.exists():
                found = re.findall(r"engine at (http://127\.0\.0\.1:\d+)", log.read_text(errors="replace"))
                if found:
                    base = found[-1]
                    break
            time.sleep(0.2)
        assert base, log.read_text(errors="replace") if log.exists() else "No startup log"
        health = requests.get(base + "/api/health", timeout=10).json()
        assert health["version"] == "1.1.0", health
        assert health["ffmpeg"] and health["ffprobe"] and health["speech"]
        assert requests.get(base + "/api/speech-model").json()["state"] == "bundled"
        assert requests.get(base + "/api/post/status").json()["ready"] is False
        assert "anintellectualcompany.contact@gmail.com" in requests.get(base + "/help").text
        errors = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.wait_for_function("window.ClipStudio && window.ClipStudio.state.templates.length > 0")
            page.locator('[data-postmode="auto"]').click()
            page.wait_for_selector("#posting:not([hidden])")
            assert page.locator("#postCadence").input_value() == "hourly"
            assert page.locator("#clipsPerHour").is_visible()
            assert not page.locator("#dailyTimesField").is_visible()
            page.screenshot(path=str(out / "posting-setup.png"), full_page=True)
            assert "locked" in page.locator("#pstep3").get_attribute("class")
            assert page.locator("#shortInstagram").is_checked()
            assert page.locator("#shortYoutube").is_checked()
            assert page.locator("#longYoutube").is_checked()
            assert not errors, errors
            browser.close()
        # A real source-file import and render through the packaged engine.
        source = out / "sample.mp4"
        ffmpeg = exe.parent / "_internal" / "bin" / "ffmpeg.exe"
        subprocess.run([str(ffmpeg), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=640x360:rate=24:duration=8", "-f", "lavfi", "-i", "sine=duration=8",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(source)], check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        recipe = {"length": 5, "count": 1, "resolution": 720, "design": {
            "caption": {"style": "none"}, "shout": False, "title": {"show": False}, "credit_on": False}}
        with source.open("rb") as handle:
            response = requests.post(base + "/api/projects/upload", files={"file": ("qa.mp4", handle, "video/mp4")},
                                     data={"format": "vertical", "captions": "false", "language": "en", "recipe": json.dumps(recipe)}, timeout=30)
        response.raise_for_status()
        ident = response.json()["id"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = requests.get(base + "/api/jobs/" + ident, timeout=15).json()
            if job["status"] in ("complete", "failed"):
                break
            time.sleep(1)
        assert job["status"] == "complete" and job.get("clips"), job
        clip = requests.get(base + job["clips"][0]["url"], timeout=30)
        clip.raise_for_status()
        assert len(clip.content) > 1000
        (out / "packaged-export.mp4").write_bytes(clip.content)
        report = dict(health=health, clips=len(job["clips"]), speech_model="bundled", browser_errors=errors,
                      export_bytes=len(clip.content), no_real_accounts=True)
        (out / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        child.terminate()
        child.wait(timeout=15)
