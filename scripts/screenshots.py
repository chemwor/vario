"""README screenshots from the demo instance (made-up data only).

    uv run --no-project --with playwright python scripts/screenshots.py      (needs `vario demo` running)

Writes docs/inbox.png (night theme, a decision selected with its evidence) and docs/briefing.png. Refuses to
run against anything but the demo port. Uses the installed Google Chrome.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8797"
DOCS = Path(__file__).resolve().parent.parent / "docs"


def api(path, body=None):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=5))


def main():
    if not api("/api/profile").get("demo"):
        sys.exit("The demo isn't running on 8797 (vario demo), or that isn't the demo. Nothing captured.")
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2,
                            permissions=["microphone"])
        ctx.add_init_script("localStorage.setItem('vario-theme','dark')")
        page = ctx.new_page()
        page.goto(URL)
        page.get_by_text("Migration 0042", exact=False).first.wait_for(timeout=15000)
        page.get_by_text("Migration 0042", exact=False).first.click()
        time.sleep(2.5)
        page.screenshot(path=DOCS / "inbox.png")
        (DOCS / "inbox.txt").write_text(page.inner_text("body"))
        page.keyboard.press("Escape")
        api("/api/briefing", {"value": True})
        time.sleep(4)
        page.screenshot(path=DOCS / "briefing.png")
        (DOCS / "briefing.txt").write_text(page.inner_text("body"))
        api("/api/briefing", {"value": False})
        b.close()
    print("wrote", DOCS / "inbox.png", DOCS / "briefing.png")


if __name__ == "__main__":
    main()
