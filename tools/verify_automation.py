"""Isolated release smoke test: real FFmpeg capture/render, fake Zernio, browser UI.

Run from the repository with the development Python. No real keys or social posts are used.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts" / "automation-smoke"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["CLIPSTUDIO_DATA"] = str(OUT / f"data-{int(time.time())}")
os.environ["CLIPSTUDIO_VAULT"] = "memory"
os.environ["CLIPSTUDIO_LIVE_WINDOW"] = "20"  # shorten the clock, keep the actual capture pipeline
os.environ["CLIPSTUDIO_ZERNIO_API"] = "http://127.0.0.1:1/never-called"

from clipstudio.media import find_tool, probe_media
from clipstudio.zernio import ZernioError
from clipstudio import app as module
import clipstudio.transcript
import uvicorn
from playwright.sync_api import sync_playwright

sample = OUT / "sample.mp4"
subprocess.run([find_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
    "testsrc2=size=640x360:rate=24:duration=100", "-f", "lavfi", "-i",
    "sine=frequency=660:duration=100", "-af", "volume='if(lt(mod(t,10),2),0.9,0.1)':eval=frame",
    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(sample)], check=True)
os.environ["CLIPSTUDIO_LIVE_FAKE"] = str(sample)
module.output_folder = lambda: OUT / "saved"
# The fixture has a sine tone, not speech. Exercise capture, ranking and rendering without a model download.
clipstudio.transcript.transcribe = lambda *a, **kw: []
calls = []

class FakeZernio:
    def __init__(self, key):
        assert key == "qa-key-not-a-real-secret"
    def accounts(self):
        return [{"id": p + "-qa", "platform": p, "username": p + "_qa", "profile_id": "qa", "active": True}
                for p in ("instagram", "youtube")]
    def upload(self, path):
        assert probe_media(path).duration > 0
        return "https://example.invalid/qa.mp4"
    def create_post(self, **kwargs):
        platform = next(iter(kwargs["accounts"]))
        calls.append(platform)
        if platform == "youtube":
            raise ZernioError("YouTube daily upload limit reached. Try again later.", status_code=429, retry_after=600)
        return {"_id": "qa-" + kwargs["idempotency_key"], "status": "scheduled", "platforms": []}
    def delete_post(self, post_id):
        calls.append("cancel:" + post_id)
    def get_post(self, post_id):
        return {"_id": post_id, "status": "scheduled", "platforms": []}

module.poster.client_factory = FakeZernio
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(module.app, host="127.0.0.1", port=port, log_level="warning"))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
deadline = time.monotonic() + 20
while not server.started and time.monotonic() < deadline:
    time.sleep(0.1)
assert server.started
base = f"http://127.0.0.1:{port}"
errors = []

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        def inspect(route):
            route.fulfill(json={"url": "https://www.youtube.com/watch?v=qa-automation", "is_live": True,
                               "duration": 0, "title": "QA live stream", "site": "YouTube", "channel": "QA"})
        context.route("**/api/inspect", inspect)
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        page.wait_for_function("window.ClipStudio && window.ClipStudio.state.templates.length > 0")
        page.locator('[data-postmode="auto"]').click()
        page.locator("#keyInput").fill("qa-key-not-a-real-secret")
        page.locator("#keyTest").click()
        page.wait_for_function("!document.querySelector('#keySet').hidden")
        page.locator("#clipsPerHour").fill("12")
        page.locator("#postSave").click()
        page.wait_for_function("document.querySelector('#posting').hidden")
        page.locator('[data-tab="link"]').click()
        page.locator("#linkInput").fill("https://www.youtube.com/watch?v=qa-automation")
        page.locator('#linkForm button').click()
        page.locator("#toStep2").click()
        assert page.locator("#continuousNote").is_visible()
        assert not page.locator("#clipCountSetting").is_visible()
        page.locator('[data-len="custom"]').click()
        page.locator("#customLength").fill("5")
        page.locator("#customLength").dispatch_event("change")
        page.locator("#toStep3").click()
        page.locator("#makeClips").click()
        page.wait_for_function("window.ClipStudio.state.job && window.ClipStudio.state.job.status === 'live'", timeout=20000)
        first_id = page.evaluate("window.ClipStudio.state.job.id")
        with page.expect_popup() as opened:
            page.locator("#newWindow").click()
        second = opened.value
        second.on("pageerror", lambda e: errors.append(str(e)))
        second.wait_for_function("window.ClipStudio && window.ClipStudio.state.templates.length > 0")
        assert second.evaluate("window.ClipStudio.state.job") is None
        second.locator('[data-tab="link"]').click()
        second.locator("#linkInput").fill("https://www.youtube.com/watch?v=qa-second")
        second.locator('#linkForm button').click()
        second.locator("#toStep2").click()
        second.locator('[data-len="custom"]').click()
        second.locator("#customLength").fill("5")
        second.locator("#customLength").dispatch_event("change")
        second.locator("#toStep3").click()
        second.locator("#makeClips").click()
        second.wait_for_function("window.ClipStudio.state.job && window.ClipStudio.state.job.status === 'live'", timeout=20000)
        second_id = second.evaluate("window.ClipStudio.state.job.id")
        assert first_id != second_id
        page.wait_for_function("(window.ClipStudio.state.job.clips || []).length >= 2", timeout=180000)
        second.wait_for_function("(window.ClipStudio.state.job.clips || []).length >= 1", timeout=180000)
        page.wait_for_function("document.querySelector('#platformStatus').textContent.includes('YouTube') && document.querySelector('#platformStatus').textContent.includes('paused')", timeout=15000)
        assert "instagram" in calls and "youtube" in calls
        assert page.locator(".clip-post-line.ready").count() >= 1
        assert page.locator(".clip-post-line.paused").count() >= 1
        assert not page.locator("#postAll").is_visible()
        assert page.evaluate("window.ClipStudio.state.job.id") == first_id
        page.screenshot(path=str(OUT / "continuous-posting.png"), full_page=True)
        # Cancel a scheduled destination and verify the remote cancellation path.
        page.locator(".clip-post-line.ready button").first.click()
        page.wait_for_timeout(500)
        assert any(c.startswith("cancel:") for c in calls)
        second.locator("#liveStop").click()
        page.locator("#liveStop").click()
        page.wait_for_function("window.ClipStudio.state.job.status === 'complete'", timeout=120000)
        second.wait_for_function("window.ClipStudio.state.job.status === 'complete'", timeout=120000)
        assert not errors, errors
        saved_folders = [module.store.get(ident)["saved_to"] for ident in (first_id, second_id)]
        assert saved_folders[0] != saved_folders[1]
        assert all(len(list(Path(folder).glob("*.mp4"))) >= 2 for folder in saved_folders)
        report = dict(first_session=first_id, second_session=second_id,
            first_clips=page.evaluate("window.ClipStudio.state.job.clips.length"),
            second_clips=second.evaluate("window.ClipStudio.state.job.clips.length"),
            simulated_posts=calls, browser_errors=errors, saved_folders=saved_folders)
        (OUT / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        browser.close()
finally:
    for ident in list(module.live_stops):
        module.live_stops[ident].set()
        if ident in module.live_recorders:
            module.live_recorders[ident].stop(wait=5)
    server.should_exit = True
    thread.join(timeout=10)
