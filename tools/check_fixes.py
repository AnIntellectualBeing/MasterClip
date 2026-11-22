"""Browser QA for T4's findings and the crash screen:
reload during making -> back to the progress; editor on the new stage; friendly licence errors;
trial used up -> licence window + clear box (no freeze); engine gone -> "Something went wrong".

python tools/check_fixes.py http://127.0.0.1:8790 <out folder> <engine data folder>
Uses up the free trial of that (scratch, memory-vault) engine.
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
url, out, data = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
SAMPLE = ROOT / "artifacts" / "caption-qa" / "gameplay-30s.mp4"
report, errors = {}, []


def shot(page, name):
    page.screenshot(path=str(out / f"{name}.png"))


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.evaluate("localStorage.clear()")
    page.reload()
    page.wait_for_selector(".vtype")

    # 1. reload while clips are being made -> straight back to the progress
    page.click('.vtype[data-kind-pick="short"]')
    page.click('.tab[data-tab="file"]')
    page.set_input_files("#fileInput", str(SAMPLE))
    page.click("#toStep2")
    page.click('#lengthSeg [data-len="15"]')
    page.click("#countMinus")
    page.click("#countMinus")
    page.click("#toStep3")
    page.click("#makeClips")
    page.wait_for_selector("#working:not([hidden])")
    page.wait_for_timeout(800)
    page.reload()
    page.wait_for_timeout(1500)
    report["reload_mid_job_shows_step3"] = page.locator("#step3:not([hidden])").count() == 1
    report["reload_mid_job_shows_progress"] = page.locator("#working:not([hidden]), #done:not([hidden])").count() >= 1
    shot(page, "1-reload-mid-job")
    page.wait_for_selector("#done:not([hidden])", timeout=600000)
    page.wait_for_function("document.querySelectorAll('#clips .clip').length >= 1", timeout=600000)

    # 2. editor on the shared stage: drag the caption, resize the credit, save
    page.click("#clips .clip button:has-text('Edit')")
    page.wait_for_selector("#editor:not([hidden]) .stage.st")
    page.wait_for_timeout(1500)
    shot(page, "2-editor")
    cap = page.locator("#stageHost .st-caption").bounding_box()
    page.mouse.move(cap["x"] + cap["width"] / 2, cap["y"] + cap["height"] / 2)
    page.mouse.down()
    page.mouse.move(cap["x"] + cap["width"] / 2, cap["y"] - 120, steps=6)
    page.mouse.up()
    report["editor_caption_dragged"] = page.locator("#stageHost .st-caption").bounding_box()["y"] < cap["y"] - 60
    page.click("#editorSave")
    page.wait_for_function("window.ClipStudio.state.job && window.ClipStudio.state.job.clips[0].version >= 1", timeout=300000)
    job = page.evaluate("window.ClipStudio.state.job")
    report["editor_saved_caption_y"] = job["clips"][0]["design"]["caption"]["y"]

    # 3. licence key typed wrong -> plain words
    page.click("#openLicence")
    for key in ["QA-TEST-1", ""]:
        page.fill("#licenceKey", key)
        page.click("#licenceForm button[type=submit]")
        page.wait_for_function("document.querySelector('#licenceMsg').textContent && !/Checking/.test(document.querySelector('#licenceMsg').textContent)")
        report[f"licence_msg_{key or 'empty'}"] = page.locator("#licenceMsg").inner_text()
    shot(page, "3-licence-message")
    page.click("#licenceClose")

    # 4. use up the trial, then press Make my clips -> licence window + pay box, nothing stuck
    while True:
        lic = page.evaluate("fetch('/api/licence').then(r => r.json())")
        if lic.get("licensed") or lic["trial_left"] <= 0:
            break
        made = page.request.post(url + "/api/projects/upload", multipart={
            "file": {"name": "x.mp4", "mimeType": "video/mp4", "buffer": SAMPLE.read_bytes()},
            "format": "vertical", "captions": "false", "language": "auto", "recipe": "",
        })
        page.request.post(url + f"/api/jobs/{made.json()['id']}/cancel")
    page.click("#startOver")
    page.click('.tab[data-tab="file"]')
    page.set_input_files("#fileInput", str(SAMPLE))
    page.click("#toStep2")
    page.click("#toStep3")
    page.click("#makeClips")
    page.wait_for_selector("#licence:not([hidden])", timeout=5000)
    report["trial_over_opens_licence"] = True
    report["trial_over_pay_box"] = page.locator("#payBox:not([hidden])").count() == 1
    report["trial_over_not_stuck"] = page.locator("#working[hidden]").count() == 1 and page.locator("#makeBox:not([hidden])").count() == 1
    report["trial_over_why"] = page.locator("#licenceWhy").inner_text()
    shot(page, "4-trial-over")
    page.click("#licenceClose")
    shot(page, "4b-trial-over-box")

    # 5. a script error -> soft bar + crash.log; engine gone -> full screen help
    page.evaluate("setTimeout(() => { throw new Error('qa boom from the page'); })")
    page.wait_for_selector(".crash-soft", timeout=5000)
    report["soft_bar"] = True
    page.wait_for_timeout(500)
    crash_log = data / "crash.log"
    report["page_error_in_crash_log"] = crash_log.is_file() and "qa boom from the page" in crash_log.read_text(encoding="utf-8")
    shot(page, "5-soft-bar")
    page.route("**/api/**", lambda route: route.abort())
    page.wait_for_selector("#crash", timeout=25000)
    report["engine_gone_screen"] = page.locator("#crashTitle").inner_text()
    shot(page, "6-engine-gone")
    page.unroute("**/api/**")
    page.click('#crash [data-act="retry"]')
    page.wait_for_load_state()
    page.wait_for_selector(".vtype")
    report["retry_reloads_when_back"] = page.locator("#crash").count() == 0
    browser.close()

report["page_errors"] = [e for e in errors if "qa boom" not in e]
print(json.dumps(report, indent=1))
