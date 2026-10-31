"""Record a short walkthrough video of the running app.

Usage: python scripts/record_demo.py [base_url] [output.webm]
Requires: pip install playwright && playwright install chromium
"""
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/demo.webm")


def scroll(page, y, ms=1800):
    page.evaluate(f"window.scrollTo({{top: {y}, behavior: 'smooth'}})")
    page.wait_for_timeout(ms)


with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 720},
        record_video_dir=tmp,
        record_video_size={"width": 1280, "height": 720},
    )
    page = ctx.new_page()
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(3000)
    for y in (500, 1000, 1600, 2400):
        scroll(page, y)
    scroll(page, 0, 1200)

    page.fill("#price", "92.5")
    page.click("#recalc")
    page.wait_for_timeout(2500)

    page.fill("#question", "Say hi")
    page.click("#brief")
    page.wait_for_timeout(6000)

    page.goto(f"{BASE}/ops", wait_until="networkidle")
    page.wait_for_timeout(3000)
    scroll(page, 600)
    scroll(page, 0, 1000)

    ctx.close()
    browser.close()
    video = next(Path(tmp).glob("*.webm"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(video), OUT)
print(f"saved {OUT}")
