"""Browser QA for AI boost (uses the Claude app installed on this PC, one short call per step).

python tools/check_ai.py --base http://127.0.0.1:8799 --video samples/facecam-45s.mp4 --out artifacts/ai --mode claude
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8799")
parser.add_argument("--video", required=True)
parser.add_argument("--out", default="artifacts/ai")
parser.add_argument("--mode", default="claude", choices=["claude", "codex"])
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
    page.locator(f'#aiMode [data-v="{args.mode}"]').click()
    page.wait_for_selector("#aiTest:not([hidden])")
    page.click("#aiTest")
    page.wait_for_function("document.querySelector('#aiState').className === 'good' || document.querySelector('#aiState').className === 'bad'", timeout=300000)
    report["test"] = page.locator("#aiState").inner_text()
    page.locator(".ai-setting").screenshot(path=str(out / "1-ai-setting.png"))
    page.locator('.tpl[data-id="streamer_top"]').click()
    page.click('#lengthSeg [data-len="15"]')
    page.click("#toStep3")
    page.click("#makeClips")
    page.locator("#done").wait_for(timeout=600000)
    page.wait_for_selector(".clip")
    page.wait_for_timeout(600)
    report["note"] = page.locator("#aiNote").inner_text() if page.locator("#aiNote").is_visible() else ""
    report["titles"] = page.eval_on_selector_all(".clip-title", "els => els.map(e => e.value)")
    page.screenshot(path=str(out / "2-clips.png"), full_page=True)
    page.goto(args.base, wait_until="networkidle")
    page.click('.tab[data-tab="file"]')
    page.set_input_files("#fileInput", args.video)
    page.click("#toStep2")
    page.locator('#aiMode [data-v="off"]').click()
    page.wait_for_timeout(500)
    report["after_off"] = page.locator("#aiState").inner_text()
    browser.close()
report["errors"] = errors
print(json.dumps(report, indent=2, ensure_ascii=False))
