"""Record the README screenshots and the walkthrough video from the demo (a fictional student, sample postings).

    .venv/bin/python -m tools.record_demo            # docs/screenshots/*.png, docs/demo.mp4, docs/demo.gif

Starts `python -m coopcoach demo --fresh` on a spare port, drives it with Playwright the way a person would (paste a
recruiter email, fix the resume, look around Events), and stops it again. Needs ffmpeg for the video and GIF.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SHOTS = DOCS / "screenshots"
PORT = 8797
URL = f"http://127.0.0.1:{PORT}"
INVITE = """Hi Alex,

Thanks for applying to the Software Engineering Intern role. We'd love to move you to the next step: a 30-minute
intro call with our team.

Please book a time that works for you here: https://calendly.com/northwind-robotics/intro-call

Best,
Priya

Northwind Robotics Recruiting Team"""


def wait_up(timeout: float = 120) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(f"{URL}/api/state", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit("the demo server didn't start")


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    video_dir = DOCS / ".video"
    shutil.rmtree(video_dir, ignore_errors=True)
    server = subprocess.Popen([sys.executable, "-m", "coopcoach", "demo", "--fresh", "--port", str(PORT)], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        wait_up()
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1280, "height": 800}, record_video_dir=str(video_dir),
                                      record_video_size={"width": 1280, "height": 800}, color_scheme="dark")
            page = ctx.new_page()
            pause = lambda s=1.6: page.wait_for_timeout(int(s * 1000))  # noqa: E731
            shot = lambda name: page.screenshot(path=str(SHOTS / f"{name}.png"))  # noqa: E731

            page.goto(URL)
            page.wait_for_selector(".ap-hero")
            pause(2.5)
            shot("autopilot")

            page.click('[data-tab="matches"]')
            page.wait_for_selector(".job")
            pause(2)
            shot("matches")
            page.mouse.wheel(0, 700)
            pause(1.2)
            page.mouse.wheel(0, -700)

            page.click('[data-tab="tracker"]')
            page.wait_for_selector(".app-row")
            pause(1.5)
            page.click("#paste-email")
            page.fill("#e-text", INVITE)
            pause(1.2)
            page.click("#email-form button.btn")
            page.wait_for_selector("#e-out .chat-card")
            pause(2.5)
            shot("applications")

            page.click('[data-tab="documents"]')
            page.wait_for_selector("#issues")
            pause(2)
            shot("resume-studio")
            if page.is_visible("#fix-all"):
                page.click("#fix-all")
                pause(2.5)

            page.click('[data-tab="events"]')
            page.wait_for_selector("#ev-list")
            pause(2)
            shot("events")
            page.click('[data-view="month"]')
            pause(2)
            shot("events-month")
            page.click('[data-view="hack"]')
            page.wait_for_selector(".hack-row")
            pause(2.5)
            shot("hackathons")

            page.click('[data-tab="settings"]')
            page.click('[data-sec="account"]')
            page.wait_for_selector(".stat-grid")
            pause(2)
            shot("numbers")

            page.click("#chat-fab")
            pause(2)
            shot("chat")
            ctx.close()
            browser.close()
        webm = next(video_dir.glob("*.webm"))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-vf", "scale=1280:-2", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-crf", "28", "-movflags", "+faststart", str(DOCS / "demo.mp4")], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-vf",
                        "fps=8,scale=880:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer",
                        str(DOCS / "demo.gif")], check=True)
        shutil.rmtree(video_dir, ignore_errors=True)
        for f in sorted(DOCS.glob("demo.*")) + sorted(SHOTS.glob("*.png")):
            print(f"{f.relative_to(ROOT)}  {f.stat().st_size / 1e6:.1f} MB")
    finally:
        server.terminate()
        server.wait(10)


if __name__ == "__main__":
    main()
