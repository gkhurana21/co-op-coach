"""Record the README screenshots and the walkthrough video from the demo (a fictional student, sample postings).

    .venv/bin/python -m tools.record_demo            # docs/screenshots/*.png, docs/demo.mp4, docs/demo.gif

Starts `python -m coopcoach demo --fresh` on a spare port, drives it with Playwright the way a person would (Jarvis prepares
applications and takes a plan in plain words, a NeetCode try logged on the Path with Jarvis's milestones, ＋ Tracks and a
track's companies, market and postings, a pasted recruiter email, a resume fix, Events, a question for Jarvis in the
chat), and stops it again. Needs ffmpeg for the video and GIF.
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
GROUP = {"autopilot": "today", "path": "today", "matches": "jobs", "tracks": "jobs", "tracker": "apps",
         "documents": "apps", "recruiters": "people", "events": "events"}  # the tabs in app.js NAV
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

            def celebrate() -> None:
                """Let Jarvis's milestone card (and any after it) play, then move on."""
                try:
                    page.wait_for_selector("#ms-ok", timeout=4000)
                except Exception:
                    return
                while page.is_visible("#ms-ok"):
                    pause(2.6)
                    page.click("#ms-ok")
                    pause(0.6)

            def go(view: str) -> None:
                """Click the group tab, then the section under it, the way a person moves around."""
                page.click(f'#tabs [data-group="{GROUP[view]}"]')
                if page.is_visible(f'#subnav [data-view="{view}"]'):
                    page.click(f'#subnav [data-view="{view}"]')

            page.goto(URL)
            page.wait_for_selector(".today-hero")
            pause(2.5)
            shot("autopilot")
            page.click("#ap-run")  # Jarvis prepares five applications, with progress as it goes
            page.wait_for_selector("#ap-run:not([disabled])", timeout=90000)
            pause(2.5)
            celebrate()
            page.fill("#ap-tell-text", "10 a day, only startups in Toronto, skip Amazon")
            pause(1)
            page.click("#ap-tell button")
            pause(2.5)
            page.click('[data-preset="6"]')  # back to any company, so the rest of the tour isn't filtered
            pause(1.2)

            go("path")
            page.wait_for_selector(".trail-sec")
            celebrate()
            pause(2)
            shot("path")
            page.click("[data-log]")  # Next up: log a NeetCode try (solved, 14 minutes)
            page.wait_for_selector("#ld-save")
            pause(1.2)
            page.fill("#ld-min", "14")
            page.fill("#ld-notes", "two pointers")
            pause(1)
            page.click("#ld-save")
            pause(2)
            celebrate()
            if page.is_visible("#ld-close"):
                page.click("#ld-close")
            pause(1)

            go("matches")
            page.wait_for_selector(".job")
            pause(2)
            shot("matches")
            page.mouse.wheel(0, 700)
            pause(1.2)
            page.mouse.wheel(0, -700)

            def track(tid: str, sec: str) -> None:
                page.evaluate("([t, s]) => openTrack(t, s)", [tid, sec])
                page.wait_for_selector("#t-head .t-counts")

            go("tracks")
            page.wait_for_selector(".track-card")
            pause(2)
            shot("tracks")
            track("co:startups", "companies")
            page.wait_for_selector(".co-row")
            pause(2)
            shot("startups")
            track("co:quant-firms", "companies")
            page.wait_for_selector(".co-row")
            pause(1.5)
            shot("track-companies")
            page.click('#t-head [data-sec="market"]')
            page.wait_for_selector(".tiles")
            pause(2)
            page.locator('#t-body [data-facet^="term:"]').first.click()  # every number opens the postings behind it
            page.wait_for_selector("#tp-facet")
            pause(2)

            go("tracker")
            page.wait_for_selector(".app-row")
            pause(1.5)
            page.click("#paste-email")
            page.fill("#e-text", INVITE)
            pause(1.2)
            page.click("#email-form button.btn")
            page.wait_for_selector("#e-out .chat-card")
            pause(2.5)
            shot("applications")

            go("recruiters")
            page.wait_for_selector(".rc-person")
            pause(2)
            shot("people")

            go("documents")
            page.wait_for_selector("#issues")
            pause(2)
            shot("resume-studio")
            if page.is_visible("#fix-all"):
                page.click("#fix-all")
                pause(2.5)

            go("events")
            page.wait_for_selector("#ev-list")
            pause(2)
            shot("events")
            page.click('#main [data-view="month"]')
            pause(2)
            shot("events-month")
            page.click('#main [data-view="hack"]')
            page.wait_for_selector(".hack-row")
            pause(2.5)
            shot("hackathons")

            page.click("#settings-btn")
            page.click('[data-sec="account"]')
            page.wait_for_selector(".stat-grid")
            pause(2)
            shot("numbers")

            page.click("#chat-fab")
            pause(1.5)
            page.fill("#chat-input", "How's my streak?")
            pause(0.8)
            bots = page.locator("#chat-log .msg.bot").count()
            page.press("#chat-input", "Enter")
            page.wait_for_function(f"document.querySelectorAll('#chat-log .msg.bot').length > {bots}", timeout=30000)
            pause(2.5)
            shot("chat")
            ctx.close()
            browser.close()
        webm = next(video_dir.glob("*.webm"))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-vf", "scale=1280:-2", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", str(DOCS / "demo.mp4")], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-vf",
                        "setpts=PTS/1.6,fps=7,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=80:stats_mode=diff[p];"
                        "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",  # 1.6x speed: ~6 MB for the README
                        str(DOCS / "demo.gif")], check=True)
        shutil.rmtree(video_dir, ignore_errors=True)
        for f in sorted(DOCS.glob("demo.*")) + sorted(SHOTS.glob("*.png")):
            print(f"{f.relative_to(ROOT)}  {f.stat().st_size / 1e6:.1f} MB")
    finally:
        server.terminate()
        server.wait(10)


if __name__ == "__main__":
    main()
