"""Browser QA for buying a licence, end to end, with a FAKE Razorpay (no money, nothing deployed).

1. node server/licence-worker/dev.mjs --port 8899 --private <seller pem>
2. start the engine with CLIPSTUDIO_LICENCE_API=http://127.0.0.1:8899 and CLIPSTUDIO_VAULT=memory
3. python tools/check_buy.py --base http://127.0.0.1:8790 --out artifacts/buy

Trial over -> licence window -> Buy button -> shop page -> fake Razorpay payment -> key on the page ->
paste in the app -> Licensed (activated against the shop's 2-PC limit) -> "lost key" page finds it again.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

# Stands in for https://checkout.razorpay.com/v1/checkout.js: "pays" at once with a properly signed answer.
FAKE_CHECKOUT = """
window.Razorpay = function (opts) {
  this.opts = opts;
  this.on = function () {};
  this.open = async function () {
    window.__razorpayOpened = { amount: opts.amount, currency: opts.currency, email: opts.prefill && opts.prefill.email };
    const r = await fetch('/__fake/pay?order=' + encodeURIComponent(opts.order_id));
    opts.handler(await r.json());
  };
};
"""

parser = argparse.ArgumentParser()
parser.add_argument("--base", default="http://127.0.0.1:8790")
parser.add_argument("--out", default="artifacts/buy")
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
errors, report = [], {}

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 860})
    context.route("https://checkout.razorpay.com/**", lambda route: route.fulfill(
        status=200, content_type="application/javascript", body=FAKE_CHECKOUT))
    page = context.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(args.base, wait_until="networkidle")
    page.click("#openLicence")
    page.wait_for_selector("#licence:not([hidden])")
    page.wait_for_timeout(400)
    buy = page.locator("#licenceBuy")
    report["buy_button"] = buy.inner_text() if buy.is_visible() else None
    report["buy_href"] = buy.get_attribute("href")
    report["buy_note"] = page.locator("#licenceBuyNote").is_visible()
    report["lost_link"] = page.locator("#licenceRecover").get_attribute("href") if page.locator("#licenceLost").is_visible() else None
    page.locator(".licence-shell").screenshot(path=str(out / "1-licence-window.png"))

    with context.expect_page() as shop_info:
        buy.click()  # target=_blank, like the system browser in the desktop app
    shop = shop_info.value
    shop.on("pageerror", lambda e: errors.append("shop: " + str(e)))
    shop.wait_for_load_state("networkidle")
    report["shop_title"] = shop.title()
    report["shop_demo_banner"] = shop.locator(".demo").count() > 0
    shop.screenshot(path=str(out / "2-shop.png"), full_page=True)
    shop.fill("#email", "qa-buyer@example.com")
    shop.click("#pay")
    shop.wait_for_selector("#done:not([hidden])", timeout=20000)
    report["razorpay_opened_with"] = shop.evaluate("window.__razorpayOpened")
    key = shop.locator("#key").inner_text().strip()
    report["key_shown"] = key.startswith("CS1-")
    shop.screenshot(path=str(out / "3-key.png"), full_page=True)

    page.bring_to_front()
    page.fill("#licenceKey", key)
    page.click("#licenceForm button[type=submit]")
    page.wait_for_selector("#licenceOk:not([hidden])", timeout=20000)
    report["app_after_key"] = page.locator("#licenceOk").inner_text()
    report["chip"] = page.locator("#licenceChip").inner_text()
    page.locator(".licence-shell").screenshot(path=str(out / "4-licensed.png"))

    lost = context.new_page()
    lost.goto(report["lost_link"] or args.base, wait_until="networkidle")
    report["lost_page_title"] = lost.title()
    lost.screenshot(path=str(out / "5-lost-key.png"), full_page=True)

    shop.set_viewport_size({"width": 390, "height": 844})
    report["shop_overflow_390"] = shop.evaluate("document.documentElement.scrollWidth > innerWidth")
    browser.close()
report["errors"] = errors
print(json.dumps(report, indent=2, ensure_ascii=False))
