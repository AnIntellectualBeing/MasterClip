"""Make emoji/<code>.png (160 px, transparent) from Twemoji SVGs (CC-BY 4.0) with headless Chrome.

python tools/make_emoji.py --download <empty folder for the SVGs>
Run once by the developer; the PNGs are committed and shipped. Not used at runtime.
"""

import argparse
import base64
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from clipstudio.emoji import EMOJI  # noqa: E402

TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/svg/{}.svg"
SIZE = 160

parser = argparse.ArgumentParser()
parser.add_argument("--download", required=True, help="empty folder to keep the downloaded SVGs in")
args = parser.parse_args()
svg_dir = Path(args.download)
svg_dir.mkdir(parents=True, exist_ok=True)
out = ROOT / "emoji"
out.mkdir(exist_ok=True)

for code in EMOJI:
    target = svg_dir / f"{code}.svg"
    if not target.is_file():
        with urllib.request.urlopen(TWEMOJI.format(code), timeout=20) as response:
            data = response.read(512 * 1024)
        if not data.lstrip().startswith(b"<svg"):
            raise SystemExit(f"{code}: not an SVG")
        target.write_bytes(data)

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": SIZE, "height": SIZE})
    for code in EMOJI:
        svg = base64.b64encode((svg_dir / f"{code}.svg").read_bytes()).decode()
        page.set_content(
            f'<html><body style="margin:0;background:transparent">'
            f'<img id="e" src="data:image/svg+xml;base64,{svg}" width="{SIZE}" height="{SIZE}"></body></html>'
        )
        page.wait_for_function("document.getElementById('e').complete")
        page.locator("#e").screenshot(path=str(out / f"{code}.png"), omit_background=True)
    browser.close()
print(f"{len(EMOJI)} emoji in {out}")
