"""Browser QA for the Posting screen against tools/fake_zernio.py (no real account).

Start fake Zernio on 8798 and Clip Studio with CLIPSTUDIO_ZERNIO_API=http://127.0.0.1:8798/api/v1
CLIPSTUDIO_VAULT=memory, then:
python tools/check_posting.py --base http://127.0.0.1:8799 --video samples/facecam-45s.mp4 --out artifacts/posting
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8799")
parser.add_argument("--video", required=True)
parser.add_argument("--out", default="artifacts/posting")
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
errors, report = [], {}


def toast(page):
    page.wait_for_selector("#toast:not([hidden])")
    return page.locator("#toast").inner_text()


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "400" not in m.text else None)
    page.goto(args.base, wait_until="networkidle")
    report["dot_before_setup"] = page.locator("#postingDot").is_visible()

    # setup wizard
    page.click("#openPosting")
    page.locator("#ptab-setup").wait_for()
    page.screenshot(path=str(out / "1-setup-empty.png"))
    page.fill("#keyInput", "zk_wrong_key_000000")
    page.click("#keyTest")
    report["bad_key_toast"] = toast(page)
    page.fill("#keyInput", "zk_test_key_123456")
    page.click("#keyTest")
    page.locator("#tick1").wait_for()
    page.wait_for_selector('.acct[data-platform="instagram"].on')
    page.screenshot(path=str(out / "2-key-ok.png"))
    page.locator('.acct[data-platform="youtube"] .acct-connect').click()
    page.wait_for_timeout(600)
    page.click("#acctRefresh")
    page.wait_for_selector('.acct[data-platform="youtube"].on')
    report["accounts"] = page.locator(".acct.on .acct-state").all_inner_texts()
    page.locator('#perDay [data-v="2"]').click()
    page.locator("label.switch:has(#dmOn)").click()
    page.fill("#dmKeyword", "CLIP")
    page.fill("#dmMessage", "Here you go https://example.com/clip-studio")
    page.click("#postSave")
    report["save_toast"] = toast(page)
    page.locator("#ptab-setup").screenshot(path=str(out / "3-setup-done.png"))
    report["preview"] = page.locator("#captionPreview").inner_text()
    page.click("#postingClose")

    # make 2 clips
    page.click('.tab[data-tab="file"]')
    page.set_input_files("#fileInput", args.video)
    page.click("#toStep2")
    page.locator('.tpl[data-id="streamer_top"]').click()
    page.click('#lengthSeg [data-len="15"]')
    page.click("#countMinus")
    page.click("#toStep3")
    page.click("#makeClips")
    page.locator("#done").wait_for(timeout=300000)
    page.wait_for_timeout(800)
    page.screenshot(path=str(out / "4-clips.png"), full_page=True)

    # schedule all -> queue
    page.click("#postAll")
    page.locator("#ptab-queue").wait_for()
    page.wait_for_function("[...document.querySelectorAll('.qitem .pill')].length === 2 && [...document.querySelectorAll('.qitem .pill')].every(p => p.textContent === 'Scheduled')", timeout=60000)
    page.screenshot(path=str(out / "5-queue-scheduled.png"))
    report["queue_when"] = page.locator(".qitem .qtext .muted").all_inner_texts()
    page.click("#queueRefresh")
    page.wait_for_timeout(800)
    page.click("#queueRefresh")
    page.wait_for_function("[...document.querySelectorAll('.qitem .pill')].every(p => p.textContent === 'Posted')", timeout=20000)
    page.screenshot(path=str(out / "6-queue-posted.png"))
    report["posted_rows"] = page.locator(".qitem .qtext").all_inner_texts()

    # one that fails, then Retry
    page.click('.tab[data-ptab="setup"]')
    page.fill("#captionTpl", "FAIL {title}")
    page.click("#postSave")
    page.click("#postingClose")
    page.locator(".clip").first.get_by_text("📤 Post").click()
    page.locator("#ptab-queue").wait_for()
    page.wait_for_function("document.querySelector('.qitem:last-child .pill').textContent === 'Scheduled'", timeout=60000)
    page.click("#queueRefresh")
    page.wait_for_timeout(800)
    page.click("#queueRefresh")
    page.wait_for_function("document.querySelector('.qitem:last-child .pill').textContent === 'Failed'", timeout=20000)
    page.screenshot(path=str(out / "7-queue-failed.png"))
    report["failed_error"] = page.locator(".qitem:last-child .err").inner_text()
    page.locator(".qitem:last-child").get_by_text("Retry").click()
    page.wait_for_function("document.querySelector('.qitem:last-child .pill').textContent === 'Posted'", timeout=20000)
    report["after_retry"] = page.locator(".qitem:last-child .pill").inner_text()
    page.screenshot(path=str(out / "8-queue-retried.png"))
    report["dot_after_setup"] = page.locator("#postingDot").is_visible()

    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.on("pageerror", lambda e: errors.append("mobile: " + str(e)))
    mobile.goto(args.base, wait_until="networkidle")
    mobile.click("#openPosting")
    mobile.wait_for_timeout(800)
    mobile.screenshot(path=str(out / "9-mobile-queue.png"))
    mobile.click('.tab[data-ptab="setup"]')
    mobile.wait_for_timeout(400)
    mobile.screenshot(path=str(out / "10-mobile-setup.png"))
    report["mobile_overflow"] = mobile.evaluate("document.querySelector('.posting-body').scrollWidth > document.querySelector('.posting-body').clientWidth")
    browser.close()
report["errors"] = errors
print(json.dumps(report, indent=2, ensure_ascii=False))
