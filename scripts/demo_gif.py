"""Record the 20-second README loop from `vario demo` and write docs/vario.gif (made-up data only).

    make demo-gif            (resets and starts the demo, records, converts; needs ffmpeg and Google Chrome)
    HEADED=1 make demo-gif   (watch it happen)

The loop: the inbox, a decision opened with its evidence, "approve" given in the command bar (the same path a
spoken answer takes, through the confirm card), and the session carrying on to done.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8797"
OUT = Path(__file__).resolve().parent.parent / "docs" / "vario.gif"
W, H = 1280, 800


def demo_running() -> bool:
    try:
        return bool(json.load(urllib.request.urlopen(URL + "/api/profile", timeout=3)).get("demo"))
    except OSError:
        return False


def main():
    if not demo_running():
        sys.exit("The demo isn't running on 8797 (vario demo). Nothing recorded.")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is needed to make the GIF (brew install ffmpeg / sudo apt install ffmpeg).")
    reports = json.load(urllib.request.urlopen(URL + "/api/reports", timeout=3))["reports"]
    sid = next(r["session_id"] for r in reports if "Migration 0042" in (r.get("summary") or ""))
    tmp = Path(tempfile.mkdtemp())
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=not os.environ.get("HEADED"),
                              args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        ctx = b.new_context(viewport={"width": W, "height": H}, permissions=["microphone"],
                            record_video_dir=str(tmp), record_video_size={"width": W, "height": H})
        ctx.add_init_script("localStorage.setItem('vario-theme','dark')")
        page = ctx.new_page()
        page.goto(URL)
        decision = page.get_by_text("Migration 0042", exact=False).first
        decision.wait_for(timeout=15000)
        time.sleep(2.5)                                   # the inbox: decisions first
        decision.click()
        time.sleep(3.5)                                   # the decision and its evidence
        # a spoken "approve", as Whisper would hand it over (the mic's own endpoint, with its confidence)
        page.evaluate("""sid => fetch('/api/voice/utterance', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: 'approve', source: 'mic', selected_session_id: sid, confidence: 0.93})})""", sid)
        time.sleep(4.5)                                   # caption, confirm card, then "Sent to billing-service."
        page.keyboard.press("Escape")
        time.sleep(7)                                     # the session runs the migration and finishes
        video = page.video.path()
        ctx.close()
        b.close()
    pal = tmp / "palette.png"
    vf = "fps=8,scale=880:-1:flags=lanczos"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", video, "-vf", f"{vf},palettegen=max_colors=96:stats_mode=diff", pal],
                   check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", video, "-i", pal, "-lavfi",
                    f"{vf}[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle", OUT], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
