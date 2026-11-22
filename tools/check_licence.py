"""Browser QA for the free trial + licence window. Run the server with CLIPSTUDIO_VAULT=memory so the
real Credential Manager (and this PC's real trial count) is never touched.

python tools/check_licence.py --base http://127.0.0.1:8796 --video samples/facecam-45s.mp4 --key CS1-... --out artifacts/licence
"""

import argparse
import json
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8796")
parser.add_argument("--video", required=True)
parser.add_argument("--key", required=True)
parser.add_argument("--out", default="artifacts/licence")
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
errors, report = [], {}
video = str(Path(args.video).resolve())

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "402" not in m.text else None)
    page.goto(args.base, wait_until="networkidle")
    page.wait_for_function("document.querySelector('#licenceChip').textContent.includes('left')")
    report["chip_start"] = page.locator("#licenceChip").inner_text()
    # use up the trial through the API (each new video counts), then stop those jobs
    for _ in range(3):
        job = requests.post(args.base + "/api/jobs", json={"source_type": "path", "source": video, "transcribe": False}).json()
        requests.post(f"{args.base}/api/jobs/{job['id']}/cancel")
    page.reload(wait_until="networkidle")
    page.wait_for_function("document.querySelector('#licenceChip').textContent.includes('Trial over')")
    report["chip_after_trial"] = page.locator("#licenceChip").inner_text()
    # making clips now opens the licence window with the reason
    page.click('.tab[data-tab="file"]')
    page.set_input_files("#fileInput", video)
    page.click("#toStep2")
    page.click("#toStep3")
    page.click("#makeClips")
    page.wait_for_selector("#licence:not([hidden])", timeout=15000)
    report["why"] = page.locator("#licenceWhy").inner_text()
    page.locator(".licence-shell").screenshot(path=str(out / "1-trial-over.png"))
    page.fill("#licenceKey", "CS1-ABCDE-FGHIJ-KLMNO-PQRST-UVWXY-Z2345-67ABC-DEFGH-IJKLM-NOPQR-STUVW")
    page.click("#licenceForm button")
    page.wait_for_function("document.querySelector('#licenceMsg').classList.contains('bad')")
    report["bad_key"] = page.locator("#licenceMsg").inner_text()
    page.fill("#licenceKey", args.key.lower())
    page.click("#licenceForm button")
    page.wait_for_selector("#licenceOk:not([hidden])")
    report["licensed"] = page.locator("#licenceOk").inner_text()
    report["chip_licensed"] = page.locator("#licenceChip").inner_text()
    page.locator(".licence-shell").screenshot(path=str(out / "2-licensed.png"))
    page.click("#licenceClose")
    page.click("#makeClips")
    page.wait_for_selector("#working:not([hidden]), #done:not([hidden])", timeout=20000)
    report["can_make_after_key"] = page.locator("#licence").is_hidden()
    page.set_viewport_size({"width": 390, "height": 844})
    report["overflow_390"] = page.evaluate("document.documentElement.scrollWidth > innerWidth")
    browser.close()
report["errors"] = errors
print(json.dumps(report, indent=2, ensure_ascii=False))
