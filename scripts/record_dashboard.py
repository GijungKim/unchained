"""Record the dashboard in headless Chrome at full framerate.

The T3 preview-tab recorder captures ~5fps (background throttled). Headless
Chrome renders rAF normally, so Playwright's video capture comes out smooth.

Usage:
    uv run python scripts/record_dashboard.py --seconds 45 --out demo/take.mp4

Requires the server running (`uv run unchained serve`) and, for a lively take,
a live stream or fresh replay beforehand.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

URL = "http://127.0.0.1:8080/"
WIDTH, HEIGHT = 1280, 800


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=45)
    parser.add_argument("--out", default="demo/take.mp4")
    parser.add_argument("--url", default=URL)
    parser.add_argument("--top", action="store_true", help="scroll to top first")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="unchained-rec-") as tmp:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(
                viewport={"width": WIDTH, "height": HEIGHT},
                record_video_dir=tmp,
                record_video_size={"width": WIDTH, "height": HEIGHT},
            )
            page = context.new_page()
            page.goto(args.url, wait_until="networkidle")
            if args.top:
                page.evaluate("window.scrollTo(0, 0)")
            # let one poll cycle settle so tiles/charts are populated
            time.sleep(3)
            print(f"recording {args.seconds}s …", flush=True)
            time.sleep(args.seconds)
            webm = Path(page.video.path())
            context.close()
            browser.close()
        print("encoding mp4 …", flush=True)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(webm),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
             "-movflags", "+faststart", str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(f"ffmpeg failed: {proc.stderr[-500:]}", file=sys.stderr)
            return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
