"""Browser QA for rolling live capture.

Start the server with a short window first, e.g. CLIPSTUDIO_LIVE_WINDOW=30, then:
python tools/check_live.py --base http://127.0.0.1:8797 --url https://www.youtube.com/watch?v=jfKfPfyJRdk --out artifacts/live
"""

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8797")
parser.add_argument("--url", required=True, help="a link that is live right now")
parser.add_argument("--out", default="artifacts/live")
parser.add_argument("--clips-before-stop", type=int, default=1)
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
errors, report = [], {}

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(args.base, wait_until="networkidle")
    page.click('.tab[data-tab="link"]')
    page.fill("#linkInput", args.url)
    page.press("#linkInput", "Enter")
    page.wait_for_selector("#liveBox:not([hidden])", timeout=90000)
    report["live_hint"] = page.locator("#liveHint").inner_text()
    page.locator("#liveBox").screenshot(path=str(out / "1-live-box.png"))
    page.click("#toStep2")
    page.locator('.tpl[data-id="big_captions"]').click()
    page.click('#lengthSeg [data-len="15"]')
    page.click("#toStep3")
    report["summary"] = page.locator("#summary").inner_text()
    page.click("#makeClips")
    page.wait_for_selector("#liveBar:not([hidden])", timeout=120000)
    t0 = time.time()
    page.wait_for_function("document.querySelectorAll('.clip').length >= %d" % args.clips_before_stop, timeout=600000)
    report["first_clip_after_s"] = round(time.time() - t0)
    report["bar_while_live"] = page.locator("#liveBar").inner_text()
    report["edit_disabled_while_live"] = page.locator(".clip-actions button").first.is_disabled()
    page.screenshot(path=str(out / "2-live.png"), full_page=True)
    page.click("#liveStop")
    page.wait_for_selector("#liveBar[hidden]", state="attached", timeout=600000)
    page.wait_for_timeout(800)
    report["done_title"] = page.locator("#doneTitle").inner_text()
    report["clips"] = page.eval_on_selector_all(".clip-title", "els => els.map(e => e.value)")
    report["edit_enabled_after"] = not page.locator(".clip-actions button").first.is_disabled()
    report["long_panel_shown"] = page.locator("#longBox").is_visible()
    page.screenshot(path=str(out / "3-done.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    report["overflow_390"] = page.evaluate("document.documentElement.scrollWidth > innerWidth")
    browser.close()
report["errors"] = errors
print(json.dumps(report, indent=2, ensure_ascii=False))
