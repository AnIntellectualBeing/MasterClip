"""Browser QA for Twitch search: search -> Twitch filter -> pick a Twitch past broadcast ->
download only a 2-minute range -> clips.

python tools/check_twitch.py --base http://127.0.0.1:8766 --topic "GTA 5 RP" --out artifacts/twitch
"""

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8766")
parser.add_argument("--topic", default="GTA 5 RP")
parser.add_argument("--out", default="artifacts/twitch")
parser.add_argument("--start", type=float, default=900, help="range start in seconds")
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
    t0 = time.time()
    page.fill("#searchInput", args.topic)
    page.press("#searchInput", "Enter")
    page.wait_for_selector("#siteFilter:not([hidden])", timeout=60000)
    report["search_s"] = round(time.time() - t0, 1)
    report["filter"] = page.eval_on_selector_all("#siteFilter [data-site]", "els => els.map(e => e.textContent)")
    report["note"] = page.locator("#searchNote").inner_text()
    page.wait_for_function("[...document.querySelectorAll('.result img')].slice(0, 6).every(i => i.complete)", timeout=20000)
    report["thumbs_loaded"] = page.evaluate("[...document.querySelectorAll('.result img')].slice(0, 6).map(i => i.naturalWidth > 0)")
    page.screenshot(path=str(out / "1-all.png"))
    page.click('#siteFilter [data-site="Twitch"]')
    report["twitch_cards"] = page.locator(".result").count()
    report["all_twitch"] = page.eval_on_selector_all(".site-badge", "els => els.every(e => e.textContent === 'Twitch')")
    page.screenshot(path=str(out / "2-twitch.png"), full_page=True)
    vod = page.locator(".result", has=page.locator(".badge.vod")).first
    report["picked"] = vod.locator("strong").inner_text()
    vod.click()
    page.wait_for_selector("#rangeBox:not([hidden])", timeout=90000)
    report["picked_meta"] = page.locator("#picked").inner_text()
    page.evaluate("([s]) => setRange(s, s + 120)", [args.start])
    report["range"] = page.locator("#rangeLabel").inner_text()
    page.locator("#picked").screenshot(path=str(out / "3-picked.png"))
    page.click("#toStep2")
    page.locator('.tpl[data-id="big_captions"]').click()
    page.click('#lengthSeg [data-len="15"]')
    page.click("#toStep3")
    report["summary"] = page.locator("#summary").inner_text()
    t1 = time.time()
    page.click("#makeClips")
    page.locator("#working").wait_for()
    page.locator("#done").wait_for(timeout=900000)
    page.wait_for_timeout(1500)
    report["clips_after_s"] = round(time.time() - t1)
    report["done_title"] = page.locator("#doneTitle").inner_text() if page.locator("#doneTitle").count() else ""
    report["clips"] = page.eval_on_selector_all(".clip-title", "els => els.map(e => e.value)")
    page.screenshot(path=str(out / "4-done.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    report["overflow_390"] = page.evaluate("document.documentElement.scrollWidth > innerWidth")
    browser.close()
report["errors"] = errors
print(json.dumps(report, indent=2, ensure_ascii=False))
