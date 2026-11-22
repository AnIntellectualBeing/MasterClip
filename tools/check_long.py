"""Browser QA: make clips, then a compilation and a full-length video from them.

python tools/check_long.py --base http://127.0.0.1:8799 --video samples/facecam-45s.mp4 --out artifacts/long
"""

import argparse
import json
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8799")
parser.add_argument("--video", required=True)
parser.add_argument("--out", default="artifacts/long")
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
    page.click('.tab[data-tab="file"]')
    page.set_input_files("#fileInput", args.video)
    page.click("#toStep2")
    page.locator('.tpl[data-id="reaction_corner"]').click()
    page.click('#lengthSeg [data-len="15"]')
    page.click("#toStep3")
    page.click("#makeClips")
    page.locator("#done").wait_for(timeout=300000)
    page.wait_for_selector(".clip")
    report["clips"] = page.locator(".clip").count()
    page.locator("#longBox").scroll_into_view_if_needed()
    page.screenshot(path=str(out / "1-long-options.png"), full_page=True)
    page.fill("#longTitle", "Test compilation")
    page.click("#makeLong")
    page.wait_for_selector(".long-video", timeout=300000)
    page.wait_for_timeout(800)
    report["compilation_meta"] = page.locator(".long-video .muted").first.inner_text()
    report["compilation_description"] = page.locator(".long-video textarea").first.input_value()
    page.locator(".long-video").first.screenshot(path=str(out / "2-compilation.png"))
    page.locator('#longKind [data-v="full"]').click()
    page.screenshot(path=str(out / "3-full-options.png"), full_page=True)
    page.click("#makeLong")
    page.wait_for_function("document.querySelectorAll('.long-video').length === 2", timeout=300000)
    page.wait_for_timeout(800)
    report["full_meta"] = page.locator(".long-video .muted").first.inner_text()
    page.screenshot(path=str(out / "4-both.png"), full_page=True)
    urls = page.eval_on_selector_all(".long-video video", "els => els.map(e => e.src)")
    report["videos"] = urls
    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.goto(args.base, wait_until="networkidle")
    mobile.evaluate("document.querySelector('#step1').hidden = true; document.querySelector('#step3').hidden = false; document.querySelector('#done').hidden = false; document.querySelector('#makeBox').hidden = true")
    report["mobile_overflow"] = mobile.evaluate("document.documentElement.scrollWidth > innerWidth")
    mobile.locator("#longBox").screenshot(path=str(out / "5-mobile.png"))
    browser.close()
report["errors"] = errors
print(json.dumps(report, indent=2, ensure_ascii=False))
