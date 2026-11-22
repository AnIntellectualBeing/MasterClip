"""Browser QA for the 3-step home + editor against a running local server (needs Chrome).

python tools/check_ui.py --base http://127.0.0.1:8799 --video samples/facecam-45s.mp4 --out artifacts/ui
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8766")
parser.add_argument("--video", required=True)
parser.add_argument("--out", default="artifacts/ui")
parser.add_argument("--template", default="streamer_top")
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
    page.screenshot(path=str(out / "1-home.png"), full_page=True)
    # step 1: my file
    page.click('.tab[data-tab="file"]')
    page.set_input_files("#fileInput", args.video)
    page.locator("#picked").wait_for()
    page.screenshot(path=str(out / "2-picked.png"), full_page=True)
    page.click("#toStep2")
    page.locator(f'.tpl[data-id="{args.template}"]').click()
    page.click('#lengthSeg [data-len="15"]')
    page.click("#countMinus")
    page.screenshot(path=str(out / "3-templates.png"), full_page=True)
    page.click("#toStep3")
    page.click("#makeClips")
    page.locator("#working").wait_for()
    page.screenshot(path=str(out / "4-working.png"))
    page.locator("#done").wait_for(timeout=300000)
    page.wait_for_timeout(1500)
    page.screenshot(path=str(out / "5-done.png"), full_page=True)
    report["clips"] = page.locator(".clip").count()
    # editor
    page.locator(".clip .ghost").first.click()
    page.locator("#editor").wait_for()
    page.wait_for_timeout(2500)
    page.screenshot(path=str(out / "6-editor.png"))
    cap = page.locator("#stageHost .st-caption").bounding_box()
    stage = page.locator("#stageHost .stage").bounding_box()
    page.mouse.move(cap["x"] + cap["width"] / 2, cap["y"] + cap["height"] / 2)
    page.mouse.down()
    page.mouse.move(stage["x"] + stage["width"] / 2, stage["y"] + stage["height"] * 0.8, steps=8)
    page.mouse.up()
    page.click('.tab[data-etab="captions"]')
    page.locator('#eFonts button[data-font="Bangers"]').click()
    page.locator('#eCapColor button[data-c="#7CFFB2"]').click()
    page.wait_for_timeout(400)
    page.screenshot(path=str(out / "7-editor-captions.png"))
    page.click('.tab[data-etab="title"]')
    page.fill("#eTitle", "Edited in the browser")
    page.click('#eTitleStyle [data-v="red_box"]')
    page.wait_for_timeout(300)
    page.screenshot(path=str(out / "8-editor-title.png"))
    page.click("#editorSave")
    page.wait_for_timeout(1500)
    page.locator("#done").wait_for(timeout=120000)
    page.wait_for_function("document.querySelector('.clip-title') && document.querySelector('.clip-title').value === 'Edited in the browser'", timeout=120000)
    page.screenshot(path=str(out / "9-after-save.png"), full_page=True)
    # phone width
    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.on("pageerror", lambda e: errors.append("mobile: " + str(e)))
    mobile.goto(args.base, wait_until="networkidle")
    report["mobile_overflow"] = mobile.evaluate("document.documentElement.scrollWidth > innerWidth")
    mobile.screenshot(path=str(out / "10-mobile.png"), full_page=True)
    browser.close()
report["errors"] = errors
print(json.dumps(report, indent=2))
