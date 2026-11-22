"""Browser QA for the video type cards, the template editor (drag/resize/snap, my templates) and
the captions panel. Screenshots go to the folder given.

python tools/check_designer.py http://127.0.0.1:8790 <out folder> [--make]
--make also makes clips from artifacts/caption-qa/gameplay-30s.mp4 with "Both" and checks the shapes.
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
url, out = sys.argv[1], Path(sys.argv[2])
make = "--make" in sys.argv
out.mkdir(parents=True, exist_ok=True)
SAMPLE = ROOT / "artifacts" / "caption-qa" / "gameplay-30s.mp4"
report = {}
errors = []


def shot(page, name):
    page.screenshot(path=str(out / f"{name}.png"), full_page=False)


def box(page, sel):
    return page.locator(sel).first.bounding_box()


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(url)
    page.evaluate("localStorage.clear()")
    page.reload()
    page.wait_for_selector(".vtype")
    shot(page, "1-video-type")
    report["vtype_cards"] = page.locator(".vtype").count()

    # pick the file, then Both
    page.click('.tab[data-tab="file"]')
    page.set_input_files("#fileInput", str(SAMPLE))
    page.click('.vtype[data-kind-pick="both"]')
    page.click("#toStep2")
    page.wait_for_selector(".stage-card.long .stage.st")
    page.wait_for_timeout(1200)
    shot(page, "2-both")
    report["stages_for_both"] = page.locator(".stage-card").count()
    report["template_rows_for_both"] = page.locator(".tpl-row").count()
    sb, lb = box(page, ".stage-card.short"), box(page, ".stage-card.long")
    report["both_side_by_side"] = abs(sb["y"] - lb["y"]) < 5 and lb["x"] > sb["x"] + sb["width"] - 1
    report["long_templates"] = page.locator(".templates.long .tpl").count()
    report["short_templates"] = page.locator(".templates.short .tpl").count()

    page.set_viewport_size({"width": 1280, "height": 1500})  # whole designer on screen for the drags
    page.wait_for_timeout(300)
    page.screenshot(path=str(out / "2b-both-full.png"), full_page=True)
    # drag the short caption toward the centre line: a guide should show
    cap = box(page, ".stage-card.short .st-caption")
    stage = box(page, ".stage-card.short .stage.st")
    page.mouse.move(cap["x"] + cap["width"] / 2, cap["y"] + cap["height"] / 2)
    page.mouse.down()
    page.mouse.move(stage["x"] + stage["width"] * 0.505, stage["y"] + stage["height"] * 0.3, steps=8)
    report["guide_visible_while_dragging"] = page.locator(".stage-card.short .st-guide.v:not([hidden])").count() > 0
    shot(page, "3-drag-snap")
    page.mouse.up()
    new_cap = box(page, ".stage-card.short .st-caption")
    report["caption_moved_up"] = new_cap["y"] < cap["y"] - 20
    report["caption_snapped_to_centre"] = abs((new_cap["x"] + new_cap["width"] / 2) - (stage["x"] + stage["width"] / 2)) < 2

    # resize the short cam box from its bottom-right corner
    page.hover(".stage-card.short .st-cam")
    cam = box(page, ".stage-card.short .st-cam")
    handle = box(page, ".stage-card.short .st-cam .st-handle.se")
    page.mouse.move(handle["x"] + 7, handle["y"] + 7)
    page.mouse.down()
    page.mouse.move(handle["x"] - 60, handle["y"] - 40, steps=6)
    page.mouse.up()
    cam2 = box(page, ".stage-card.short .st-cam")
    report["cam_resized"] = cam2["width"] < cam["width"] - 40

    # shrink the long gameplay from a corner, then move it
    page.hover(".stage-card.long .st-game")
    gh = box(page, ".stage-card.long .st-game .st-handle.se")
    page.mouse.move(gh["x"] + 7, gh["y"] + 7)
    page.mouse.down()
    page.mouse.move(gh["x"] - 120, gh["y"] - 60, steps=6)
    page.mouse.up()
    game = box(page, ".stage-card.long .st-game")
    page.mouse.move(game["x"] + game["width"] / 2, game["y"] + game["height"] / 2)
    page.mouse.down()
    page.mouse.move(game["x"] + game["width"] / 2 + 40, game["y"] + game["height"] / 2, steps=5)
    page.mouse.up()
    report["game_rect_moved"] = page.evaluate("window.ClipDesigner.payload().formats[1].design.game_rect")
    page.hover(".stage-card.long .st-game")
    report["full_frame_handle_visible"] = page.locator(".stage-card.long .st-game .st-handle.se").is_visible()

    # resize the title text from its corner (text size changes)
    title = box(page, ".stage-card.short .st-title")
    page.hover(".stage-card.short .st-title")
    th = box(page, ".stage-card.short .st-title .st-handle.se")
    page.mouse.move(th["x"] + 7, th["y"] + 7)
    page.mouse.down()
    page.mouse.move(th["x"] + 50, th["y"] + 20, steps=5)
    page.mouse.up()
    report["title_bigger"] = box(page, ".stage-card.short .st-title")["width"] > title["width"] + 10

    # captions panel
    page.click('#capPresets [data-preset="twoline"]')
    page.click("label:has(#capEmoji)")
    page.click('#capFonts button[data-font="Bangers"]')
    page.wait_for_timeout(2500)
    shot(page, "4-captions-twoline-emoji")
    payload = page.evaluate("window.ClipDesigner.payload()")
    report["both_formats"] = [f["kind"] for f in payload["formats"]]
    report["caption_style_both"] = [f["design"]["caption"]["style"] for f in payload["formats"]]
    report["emoji_both"] = [f["design"]["caption"]["emoji"] for f in payload["formats"]]
    report["font_both"] = [f["design"]["caption"]["font"] for f in payload["formats"]]
    report["game_rect_long"] = payload["formats"][1]["design"]["game_rect"]
    report["cam_rect_short"] = payload["formats"][0]["design"]["cam_rect"]
    report["emoji_preview_visible"] = page.locator(".st-emoji:not([hidden])").count() > 0
    page.click('#capPresets [data-preset="pop1"]')
    page.wait_for_timeout(700)
    shot(page, "5-one-word-pop")

    # save as my template, rename it, then delete it
    page.click('.stage-card.short [data-act="saveas"]')
    page.fill(".stage-card.short .save-as input", "My GTA style")
    page.click('.stage-card.short .save-as button[type="submit"]')
    page.wait_for_selector('.templates.short .tpl.mine')
    report["my_template_saved"] = page.locator(".templates.short .tpl.mine.on").count() == 1
    shot(page, "6-my-template")
    page.click('.templates.short .tpl.mine .tpl-tools [title="Rename"]')
    page.fill(".tpl-rename", "Speed edits")
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    report["renamed"] = page.locator(".templates.short .tpl.mine strong").inner_text() == "Speed edits"
    # change something, then update the saved template
    page.click('#capPresets [data-preset="boxed"]')
    report["update_button_shows"] = page.locator('.stage-card.short [data-act="update"]:not([hidden])').count() == 1
    page.click('.stage-card.short [data-act="update"]')
    page.wait_for_timeout(400)
    saved = page.evaluate("fetch('/api/templates').then(r => r.json())")
    mine = [t for t in saved["templates"] if t.get("mine")]
    report["update_saved"] = bool(mine) and mine[0]["design"]["caption"]["style"] == "boxed"
    # reset to default
    page.click('.stage-card.long [data-act="reset"]')
    report["reset_long"] = page.evaluate("window.ClipDesigner.payload().formats[1].design.game_rect") is None

    # Short only: one row, one preview
    page.click('.kind-seg [data-kind-pick="short"]')
    page.wait_for_timeout(500)
    report["short_only_stages"] = page.locator(".stage-card").count()
    report["short_only_long_row"] = page.locator(".templates.long").count()
    shot(page, "7-short-only")
    page.click('.kind-seg [data-kind-pick="long"]')
    page.wait_for_timeout(500)
    report["long_only_stage_is_wide"] = page.locator(".stage-card.long").count() == 1 and page.locator(".stage-card.short").count() == 0
    shot(page, "8-long-only")

    # delete my template
    page.click('.kind-seg [data-kind-pick="short"]')
    page.click('.templates.short .tpl.mine .tpl-tools [title="Delete"]')
    page.click('.tpl-confirm .danger')
    page.wait_for_timeout(400)
    report["deleted"] = page.locator(".templates.short .tpl.mine").count() == 0

    page.set_viewport_size({"width": 1280, "height": 900})
    # phone width: no sideways scroll
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    report["phone_no_hscroll"] = page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    shot(page, "9-phone")
    page.set_viewport_size({"width": 1280, "height": 900})

    if make:
        page.click('.kind-seg [data-kind-pick="both"]')
        page.click('#capPresets [data-preset="twoline"]')
        page.click("label:has(#capEmoji)")
        page.click('#lengthSeg [data-len="15"]')
        page.click("#countMinus")
        page.click("#countMinus")
        page.click("#toStep3")
        page.click("#makeClips")
        page.wait_for_selector("#done:not([hidden])", timeout=600000)
        page.wait_for_function("document.querySelectorAll('#clips .clip').length >= 2", timeout=600000)
        page.wait_for_timeout(1500)
        shot(page, "10-both-clips")
        job = page.evaluate("window.ClipStudio.state.job")
        report["made"] = [(c.get("kind"), c["design"]["aspect"], c["design"]["caption"]["style"]) for c in job["clips"]]
        report["cards_landscape"] = page.locator("#clips .clip.landscape").count()
        # reload: the welcome-back card should offer the clips
        page.reload()
        page.wait_for_selector("#welcomeBack:not([hidden])", timeout=10000)
        report["welcome_back"] = page.locator("#wbTitle").inner_text()
        shot(page, "11-welcome-back")

    browser.close()

report["page_errors"] = errors
print(json.dumps(report, indent=1))
