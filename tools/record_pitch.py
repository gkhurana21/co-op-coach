"""Record the pitch film from the demo (a fictional student, sample postings): docs/pitch.mp4.

    PYTHONPATH=<folder with Pillow> .venv/bin/python -m tools.record_pitch [--draft] [--compose]

One story, one internship, start to finish. The app is filmed in Chromium at twice the final resolution with nothing
drawn over it except a cursor; the camera moves, window framing, crossfades and captions are added afterwards, so a
zoom stays sharp and a caption never sits on the interface. --draft makes a quick 960x540 cut; --compose rebuilds the
film from the last recording. Needs ffmpeg and Pillow. Nothing real is touched: the demo's own data folder only, the
form is a made-up company's page served from here, and it is never submitted.
"""

import base64
import bisect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "pitch.mp4"
WORK = ROOT / "docs" / ".pitch"
DEMO = ROOT / "data-demo"
PORT = 8796
URL = f"http://127.0.0.1:{PORT}"
FORM_URL = "https://careers.northwind.example/apply/software-engineering-intern"  # .example: can't exist; served below
VIEW = (1440, 810)
DSF = 2
STAGE = 0.885  # how much of the frame the app window fills when it isn't zoomed
ALLOWED = ("https://fonts.googleapis.com", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com",
           "https://cdn.jsdelivr.net", "https://unpkg.com")


def interview_day() -> datetime:
    """A Wednesday inside the calendar's rolling week, so the interview lands on screen whenever this is recorded."""
    d = datetime.now() + timedelta(days=1)
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d


INVITE = f"""Hi Alex,

Thanks for applying to the Software Engineering Intern role at Northwind Robotics. We'd love to invite you to a technical
interview on {interview_day():%A, %B} {interview_day().day} at 11:00 AM ET (45 minutes, on Zoom).

Join here: https://zoom.us/j/5550123456

Best,
Priya

Northwind Robotics Recruiting Team"""

OVERLAY = r"""
(() => {
  if (window.__pitch) return;
  const css = `
  #pitch-ui, #pitch-ui * { pointer-events: none; box-sizing: border-box; }
  #pitch-ui { position: fixed; inset: 0; z-index: 2147483647; }
  #p-cursor { position: fixed; left: 0; top: 0; width: 24px; height: 24px; margin: -3px 0 0 -3px; opacity: 0;
    transition: transform .55s cubic-bezier(.45,.05,.25,1), opacity .3s; filter: drop-shadow(0 3px 6px rgba(0,0,0,.55)); }
  #p-cursor.on { opacity: 1; }
  .p-ripple { position: fixed; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%; border: 2px solid #5cf2b0;
    animation: p-rip .6s ease-out forwards; }
  @keyframes p-rip { from { transform: scale(.6); opacity: 1; } to { transform: scale(3.6); opacity: 0; } }
  #p-card { position: fixed; inset: 0; display: grid; place-items: center; color: #e9eef0; opacity: 0; transition: opacity .55s ease;
    font-family: "Geist", system-ui, sans-serif; background: radial-gradient(760px 460px at 50% 44%, rgba(92,242,176,.075), transparent 70%), #0b0e10; }
  #p-card.on { opacity: 1; }
  #p-in { display: flex; flex-direction: column; align-items: center; text-align: center; animation: p-fade .45s ease both; }
  @keyframes p-fade { from { opacity: 0; } to { opacity: 1; } }
  #p-card .eb { font: 500 18px/1 "Geist Mono", monospace; letter-spacing: .14em; text-transform: uppercase; color: #8a97a0; margin-bottom: 28px; }
  #p-card .st { font: 400 86px/1.06 "Instrument Serif", Georgia, serif; letter-spacing: -.012em; margin: 0; max-width: 1180px; }
  #p-card .st em { color: #5cf2b0; font-style: italic; }
  #p-card .w { display: inline-block; opacity: 0; transform: translateY(16px); animation: p-rise .6s cubic-bezier(.2,.8,.2,1) forwards; }
  @keyframes p-rise { to { opacity: 1; transform: none; } }
  #p-card .src { font: 400 18px/1 "Geist Mono", monospace; color: #8a97a0; margin-top: 34px; }
  #p-card .p-jv { filter: drop-shadow(0 0 34px rgba(92,242,176,.5)); animation: p-glow 1.5s ease both; }
  @keyframes p-glow { from { opacity: 0; filter: drop-shadow(0 0 0 rgba(92,242,176,0)); } to { opacity: 1; } }
  #p-card .p-boot { font: 400 22px/1.8 "Geist Mono", monospace; color: #8a97a0; margin-top: 30px; min-height: 130px; }
  #p-card .p-boot div { opacity: 0; animation: p-fade .25s ease forwards; }
  #p-card .p-boot .ok { color: #5cf2b0; }
  #p-card .wm { font: 400 96px/1 "Instrument Serif", Georgia, serif; letter-spacing: -.015em; margin: 26px 0 0; }
  #p-card .tag { font: 400 34px/1.2 "Instrument Serif", Georgia, serif; font-style: italic; color: #5cf2b0; margin-top: 16px; }
  #p-card .rcpt { display: grid; grid-template-columns: repeat(4, 262px); gap: 0; text-align: left; }
  #p-card .rcpt > div { border-left: 1px solid #222a31; padding: 6px 24px 8px; opacity: 0; animation: p-fade .5s ease forwards; }
  #p-card .rcpt b { display: block; font: 400 88px/1 "Instrument Serif", Georgia, serif; font-variant-numeric: tabular-nums; }
  #p-card .rcpt .z { color: #5cf2b0; }
  #p-card .rcpt span { display: block; font: 400 18px/1.42 "Geist Mono", monospace; color: #8a97a0; margin-top: 16px; }
  #p-card .two { display: grid; grid-template-columns: 340px 900px; gap: 56px; align-items: center; text-align: left; }
  #p-card .two img { width: 340px; border-radius: 14px; border: 1px solid #222a31; box-shadow: 0 30px 80px rgba(0,0,0,.6); opacity: 0; animation: p-fade .6s ease forwards; }
  #p-card .lab { font: 500 16px/1 "Geist Mono", monospace; letter-spacing: .14em; text-transform: uppercase; color: #8a97a0; margin-bottom: 16px; }
  #p-card .term { background: #0e1215; border: 1px solid #222a31; border-radius: 14px; padding: 22px 26px 26px; font: 400 23px/1.6 "Geist Mono", monospace;
    box-shadow: 0 30px 80px rgba(0,0,0,.6); }
  #p-card .term .dots { display: flex; gap: 8px; margin-bottom: 20px; } #p-card .term .dots i { width: 12px; height: 12px; border-radius: 50%; background: #222a31; }
  #p-card .term .l { opacity: 0; animation: p-fade .22s ease forwards; white-space: pre-wrap; }
  #p-card .term .m { color: #5cf2b0; } #p-card .term .d { color: #8a97a0; } #p-card .term .a { color: #ffb547; }
  `;
  const ensure = () => {
    if (document.getElementById("pitch-ui")) return;
    const st = document.createElement("style"); st.textContent = css; document.documentElement.appendChild(st);
    const ui = document.createElement("div"); ui.id = "pitch-ui";
    ui.innerHTML = `<div id="p-card"></div>
      <svg id="p-cursor" viewBox="0 0 24 24"><path d="M3 2l7.5 19 2.6-7.9L21 10.5z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
    document.documentElement.appendChild(ui);
  };
  const $ = (id) => { ensure(); return document.getElementById(id); };
  window.__pitch = {
    cursor(x, y, ms) { const k = $("p-cursor"); k.style.transitionDuration = (ms ?? 550) + "ms, 300ms"; k.style.transform = `translate(${x}px, ${y}px)`; k.classList.add("on"); },
    cursorOff() { $("p-cursor").classList.remove("on"); },
    ripple(x, y) { const r = document.createElement("div"); r.className = "p-ripple"; r.style.left = x + "px"; r.style.top = y + "px"; $("pitch-ui").appendChild(r); setTimeout(() => r.remove(), 700); },
    card(html) {
      const c = $("p-card");
      c.innerHTML = `<div id="p-in">${html.replace(/\{\{jarvis:(\w+):(\d+)\}\}/g, (_, m, s) => window.jarvisSvg ? window.jarvisSvg(m, +s, { label: "Jarvis" }) : "")}</div>`;
      c.classList.add("on");
      c.querySelectorAll("[data-to]").forEach((el) => {
        const to = +el.dataset.to, dp = +(el.dataset.dp || 0), t0 = performance.now() + (+el.dataset.wait || 0);
        const tick = (t) => { const k = Math.min(1, Math.max(0, (t - t0) / 1100)); el.textContent = (to * (1 - Math.pow(1 - k, 3))).toFixed(dp); if (k < 1) requestAnimationFrame(tick); };
        el.textContent = (0).toFixed(dp); requestAnimationFrame(tick);
      });
      c.querySelectorAll("[data-type]").forEach((el) => {
        const s = el.dataset.type; let i = 0; const go = () => { el.textContent = s.slice(0, ++i); if (i < s.length) setTimeout(go, 38); }; setTimeout(go, 500);
      });
    },
    cardOff() { $("p-card").classList.remove("on"); },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ensure); else ensure();
})();
"""

FORM = """<!doctype html><html><head><meta charset="utf-8"><title>Northwind Robotics · Software Engineering Intern</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #f4f5f9; font-family: -apple-system, "Inter", "Segoe UI", sans-serif; color: #111827; }
  header { background: #0f172a; color: #fff; padding: 16px 0; }
  .wrap { width: 860px; margin: 0 auto; }
  header .wrap { display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 18px; }
  .mark { width: 30px; height: 30px; border-radius: 8px; background: linear-gradient(135deg,#22d3ee,#3b82f6); display: grid; place-items: center; font-size: 17px; }
  h1 { font-size: 26px; margin: 22px 0 2px; letter-spacing: -.01em; }
  .sub { color: #6b7280; font-size: 14px; margin-bottom: 14px; }
  form { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 18px 22px 20px; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: #6b7280; margin: 4px 0 8px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px 14px; margin-bottom: 12px; }
  label { display: block; font-size: 12.5px; font-weight: 600; color: #374151; margin-bottom: 3px; }
  input, select, textarea { width: 100%; font: inherit; font-size: 14px; padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 8px;
    background: #fff; transition: border-color .3s, box-shadow .3s, color .25s; }
  .pending input:not([type=file]), .pending select { color: transparent; }
  .filled { border-color: #22c55e !important; box-shadow: 0 0 0 3px rgba(34,197,94,.18); color: #111827 !important; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 14px; margin-bottom: 12px; }
  .file { border: 1.5px dashed #cbd5e1; border-radius: 10px; padding: 9px 12px; font-size: 13.5px; color: #6b7280; display: flex; align-items: center; gap: 10px; }
  .file input { width: auto; border: 0; padding: 0; font-size: 13px; }
  .file.filled { border-style: solid; }
  .eeo { background: #f9fafb; border: 1px solid #eef0f4; border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; }
  .eeo p { margin: 0 0 6px; font-size: 12.5px; color: #6b7280; }
  .actions { display: flex; justify-content: flex-end; }
  .why { font: 500 10.5px/1 ui-monospace, Menlo, monospace; font-style: normal; color: #0a7a52; background: #d9f7ea; border-radius: 5px; padding: 2px 6px; margin-left: 7px; }
  button { font: inherit; font-weight: 700; color: #fff; background: #2563eb; border: 0; border-radius: 9px; padding: 10px 20px; }
</style></head><body>
<header><div class="wrap"><span class="mark">◆</span>Northwind Robotics · Careers</div></header>
<div class="wrap">
  <h1>Software Engineering Intern</h1><div class="sub">Winter 2027 · Waterloo, ON · Hybrid</div>
  <form class="pending" onsubmit="window.SUBMITTED = true; return false">
    <h2>Personal information</h2>
    <div class="grid">
      <div><label for="fn">First name *</label><input id="fn"></div>
      <div><label for="ln">Last name *</label><input id="ln"></div>
      <div><label for="em">Email *</label><input id="em"></div>
      <div><label for="ph">Phone *</label><input id="ph"></div>
      <div><label for="lo">Location (city) *</label><input id="lo"></div>
      <div><label for="sc">School *</label><input id="sc"></div>
    </div>
    <div class="row2">
      <div><label for="li">LinkedIn profile</label><input id="li"></div>
      <div><label for="gh">GitHub URL</label><input id="gh"></div>
    </div>
    <div class="row2">
      <div><label for="mj">Major / field of study *</label><input id="mj"></div>
      <div><label for="gd">Expected graduation *</label><input id="gd"></div>
    </div>
    <div class="row2">
      <div class="file" id="rbox"><label for="cv" style="margin:0">Resume / CV *</label><input type="file" id="cv"></div>
      <div><label for="au">Are you legally authorized to work in Canada? *</label>
        <select id="au"><option value=""></option><option>Yes</option><option>No</option></select></div>
    </div>
    <div class="row2">
      <div><label for="sp">Will you now or in the future require sponsorship? *</label>
        <select id="sp"><option value=""></option><option>Yes</option><option>No</option></select></div>
      <div><label for="hear">How did you hear about us?</label><input id="hear"></div>
    </div>
    <div class="eeo"><p>Voluntary self-identification. Answering is optional and doesn't affect your application.</p>
      <div class="row2" style="margin:0"><div><label for="gender">Gender</label>
        <select id="gender"><option value=""></option><option>Woman</option><option>Man</option><option>Non-binary</option><option>Prefer not to say</option></select></div>
      <div><label for="race">Race / ethnicity</label><select id="race"><option value=""></option><option>Prefer not to say</option></select></div></div></div>
    <div class="actions"><button id="submit" type="submit">Submit application</button></div>
  </form>
</div></body></html>"""


def wait_up(timeout: float = 180) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(f"{URL}/api/state", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit("the demo server didn't start")


def demo_env(**extra) -> dict:
    return {**os.environ, "COOPCOACH_DATA": str(DEMO), "COOPCOACH_DEMO": "1", **extra}


NO_AI = {"GROQ_API_KEY": "", "ANTHROPIC_API_KEY": "", "COOPCOACH_CLAUDE_BIN": "/nonexistent", "COOPCOACH_CODEX_BIN": "/nonexistent"}


def build_demo() -> None:
    """A fresh demo student (resume read by the plain rules, so nothing depends on an AI being up) and the film's one
    posting: Northwind Robotics, opened two hours ago, whose application form is served below."""
    shutil.rmtree(DEMO, ignore_errors=True)
    DEMO.mkdir(parents=True)
    code = """
import json, sys
from pathlib import Path
from coopcoach import db, demo
from coopcoach.textutil import company_key
demo.build(Path(sys.argv[1]))
now, url = db.now(), sys.argv[2]
with db.tx() as c:  # the demo resume has typos on purpose, for the app's resume fixer; the film's student has fixed them
    for table, col in (("documents", "content"), ("profiles", "profile_json"), ("profiles", "resume_text")):
        for old, new in (("Software Engineer Inter ", "Software Engineer Intern "), ('Software Engineer Inter"', 'Software Engineer Intern"'), ("MAy", "May"), ("Toronto , ON", "Toronto, ON")):
            c.execute(f"UPDATE {table} SET {col}=REPLACE({col}, ?, ?)", (old, new))
ago = now - 2 * 3600
with db.tx() as c:
    c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, locations, regions, terms, category, tier, skills,"
              " description, posted_at, first_seen, last_seen, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
              ("northwind-film", "ashby:northwind", "Northwind Robotics", company_key("Northwind Robotics"),
               "Software Engineering Intern (Winter 2027)", url, json.dumps(["Waterloo, ON"]), json.dumps(["Canada"]),
               json.dumps(["Winter 2027"]), "Software", "startup", json.dumps(["Python", "SQL", "React", "AWS", "Git", "Docker"]),
               "Build the software that runs our warehouse robots. Python, SQL and React. Open to co-op students in Canada.",
               ago, ago, now))
"""
    subprocess.run([sys.executable, "-c", code, str(DEMO), FORM_URL], cwd=ROOT, env=demo_env(**NO_AI), check=True,
                   stdout=subprocess.DEVNULL)


def demo_facts() -> tuple[dict, str]:
    pdf = WORK / "Alex_Chen_Resume.pdf"
    code = ("import json, sys; from pathlib import Path; from coopcoach import apply, render, store, routes_docs; "
            "p = store.get_profile(1); r = routes_docs._resume_for(1, None) or {}; "
            "Path(sys.argv[1]).write_bytes(render.pdf(render.resume_html(r, p['prefs'].get('resume_template', 'jake')))) if r else None; "
            "print(json.dumps(apply._basics(p, r)))")
    out = subprocess.run([sys.executable, "-c", code, str(pdf)], cwd=ROOT, env=demo_env(), capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1]), str(pdf) if pdf.exists() else ""


def daily_brief() -> dict:
    """The real answer the Claude Code plugin's daily_brief tool gives, from the demo's data."""
    code = ("import json; from coopcoach.tools import profile; d = profile.daily_brief(1); d.pop('_card', None); "
            "print('@@' + json.dumps(d, default=str))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=demo_env(), capture_output=True, text=True, check=True)
    return json.loads(out.stdout.split("@@", 1)[1])


def panel_shot(pw) -> str:
    """The real Chrome extension's side panel, paired to the demo, as an image (data: URL). Empty if it can't load."""
    target = WORK / "panel.png"
    prof = tempfile.mkdtemp(prefix="pitch-chrome-")
    try:
        code = httpx.post(f"{URL}/api/extension/code", json={"profile_id": 1}, headers={"Origin": URL}, timeout=20).json()["code"]
        ext_dir = ROOT / "extension"
        ctx = pw.chromium.launch_persistent_context(prof, channel="chromium", headless=True, viewport={"width": 400, "height": 760},
                                                    device_scale_factor=2, color_scheme="dark",
                                                    args=[f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"])
        worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
        p = ctx.new_page()
        p.goto(f"chrome-extension://{worker.url.split('/')[2]}/panel.html")
        p.fill("#base", URL)
        p.fill("#code", code)
        p.locator("#pair button").click()
        p.wait_for_selector("#today:not([hidden])", timeout=20000)
        p.wait_for_timeout(2500)
        p.screenshot(path=str(target))
        ctx.close()
        return "data:image/png;base64," + base64.b64encode(target.read_bytes()).decode()
    except Exception as e:
        print(f"(no extension panel in this take: {e})", flush=True)
        return ""
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def words(s: str, start: float = 0.0, step: float = 0.075) -> str:
    out = []
    for i, w in enumerate(s.split()):
        span = f'<span class="w" style="animation-delay:{start + i * step:.2f}s">{w.strip("*")}</span>'
        out.append(f"<em>{span}</em>" if w.startswith("*") else span)
    return " ".join(out)


def statement(text: str, eyebrow: str = "", source: str = "") -> str:
    delay = len(text.split()) * 0.075 + 0.25
    return ((f'<div class="eb">{eyebrow}</div>' if eyebrow else "") + f'<h1 class="st">{words(text)}</h1>' +
            (f'<div class="src w" style="animation-delay:{delay:.2f}s">{source}</div>' if source else ""))


def main() -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("needs ffmpeg (brew install ffmpeg)")
    draft = "--draft" in sys.argv
    if "--compose" in sys.argv:
        return compose(draft)
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "f").mkdir(parents=True)
    build_demo()
    server = subprocess.Popen([sys.executable, "-m", "coopcoach", "demo", "--port", str(PORT)], cwd=ROOT, env=os.environ.copy(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    frames: list[tuple[float, str]] = []
    segs: list[list[float]] = []
    keys: list[dict] = []
    caps: list[dict] = []
    facts: dict = {}
    try:
        wait_up()
        same = {"Origin": URL}
        pid = httpx.get(f"{URL}/api/state", timeout=30).json()["profiles"][0]["id"]
        basics, resume = demo_facts()
        os.environ.update(COOPCOACH_DATA=str(DEMO), COOPCOACH_DEMO="1")  # the filler's module, never the real data
        from coopcoach.apply import _basic_answers, _fill_choices, _fill_page, asker

        with sync_playwright() as pw:
            panel = panel_shot(pw)
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": VIEW[0], "height": VIEW[1]}, device_scale_factor=DSF, color_scheme="dark")
            ctx.add_init_script(OVERLAY)

            def router(route):
                u = route.request.url
                if u.startswith(URL) or u.startswith("data:") or u.startswith(ALLOWED):
                    return route.continue_()
                if u.startswith(FORM_URL):
                    return route.fulfill(status=200, content_type="text/html", body=FORM)
                return route.abort()  # no trackers, no real sites

            ctx.route("**/*", router)
            page = ctx.new_page()
            cdp = ctx.new_cdp_session(page)
            n = [0]

            def on_frame(ev):
                path = WORK / "f" / f"{n[0]:05d}.jpg"
                n[0] += 1
                path.write_bytes(base64.b64decode(ev["data"]))
                frames.append((ev["metadata"]["timestamp"], str(path)))
                try:
                    cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
                except Exception:
                    pass

            cdp.on("Page.screencastFrame", on_frame)
            hold = lambda s: page.wait_for_timeout(int(s * 1000))  # noqa: E731
            pos = [720, 400]

            def P(fn, *a):
                return page.evaluate(f"(a) => window.__pitch.{fn}(...a)", list(a))

            def cam(target=None, zoom=1.0, ms=1000, stage=None, dy=0.0, left=False, x_at=None):
                """Point the camera (added in post): at a selector, or back to the whole window."""
                x, y = VIEW[0] / 2, VIEW[1] / 2
                if target:
                    b = page.locator(target).first.bounding_box()
                    if b:
                        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2 + dy
                        if left:
                            x = b["x"] - 46 + VIEW[0] / zoom / 2
                        if x_at is not None:
                            x = x_at
                keys.append({"t": time.time(), "x": x, "y": y, "z": zoom, "s": STAGE if (zoom <= 1.001 if stage is None else stage) else 1.0, "d": ms / 1000})

            def cap(text=None):
                now = time.time()
                if caps and caps[-1]["off"] is None:
                    caps[-1]["off"] = now
                if text:
                    caps.append({"on": now, "off": None, "text": text})

            def glide(sel, click=True, ms=650):
                loc = page.locator(sel).first
                loc.scroll_into_view_if_needed()
                b = loc.bounding_box()
                x, y = b["x"] + min(b["width"] / 2, 70), b["y"] + b["height"] / 2
                P("cursor", x, y, ms)
                page.mouse.move(x, y, steps=8)
                hold(ms / 1000 + 0.08)
                pos[:] = [x, y]
                if click:
                    P("ripple", x, y)
                    loc.click()

            def drag(frm, to, seconds=1.1):
                a, b = page.locator(frm).first.bounding_box(), page.locator(to).first.bounding_box()
                x0, y0 = a["x"] + a["width"] / 2, a["y"] + min(a["height"] / 2, 14)
                x1, y1 = b["x"] + b["width"] * 0.62, y0
                P("cursor", x0, y0, 500)
                page.mouse.move(x0, y0, steps=6)
                hold(0.6)
                page.mouse.down()
                steps = 44
                for i in range(1, steps + 1):
                    k = i / steps
                    k = k * k * (3 - 2 * k)
                    x, y = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
                    P("cursor", x, y, 0)
                    page.mouse.move(x, y)
                    page.wait_for_timeout(int(seconds * 1000 / steps))
                hold(0.15)
                page.mouse.up()
                pos[:] = [x1, y1]

            def rec(on: bool) -> None:
                """Start or stop the camera: a stop makes a cut, so loading never shows as dead time."""
                if on:
                    segs.append([time.time(), 0.0])
                    cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 94, "maxWidth": VIEW[0] * DSF, "maxHeight": VIEW[1] * DSF, "everyNthFrame": 1})
                else:
                    hold(0.05)
                    cdp.send("Page.stopScreencast")
                    segs[-1][1] = time.time()
                    cap(None)

            def sweep_popups():
                if page.is_visible("#ms-ok"):
                    page.click("#ms-ok")

            # ---- cold open: Jarvis wakes up
            page.goto(URL)
            page.wait_for_selector(".ph-today")
            page.click('#tabs [data-group="jobs"]')
            page.evaluate("() => document.fonts.ready")
            sweep_popups()
            cam(None, 1, ms=0, stage=False)
            P("card", '<div class="p-jv">{{jarvis:happy:170}}</div><div class="p-boot"><div style="animation-delay:.8s">waking up</div>'
                      '<div style="animation-delay:1.5s">reading the job boards</div><div class="ok" style="animation-delay:2.2s">ready.</div></div>')
            hold(0.2)
            rec(True)
            hold(3.3)
            P("card", '{{jarvis:happy:110}}<div class="wm">Co-op Coach</div>')
            hold(2.0)

            # ---- the problem, in three lines
            P("card", statement("A tech internship gets *273* applications.", source="Handshake Internship Index, 2024–25"))
            hold(3.1)
            P("card", statement("*62%* of HR staff are more likely to reject an uncustomized AI resume.", source="Resume Now, 2025"))
            hold(3.6)
            P("card", statement("So apply to *fewer.* Apply like you *mean* it."))
            hold(2.8)
            P("card", statement("Follow one student from *posting* to *interview.*", eyebrow="Winter 2027 co-op search",
                                source="Alex is made up. Everything after this is the real app."))
            hold(3.4)

            # ---- Today
            page.click('#tabs [data-group="today"]')
            hold(0.1)
            cam(None, 1, ms=0)
            P("cardOff")
            P("cursor", pos[0], pos[1], 0)
            cap("Every morning, <em>one sentence.</em>")
            hold(2.3)
            cam(".ph-sentence", 1.62, dy=-16, left=True)
            hold(2.3)
            cap("Then the one thing to do next.")
            cam(".next-move", 1.12)
            hold(2.5)

            # ---- Matches: the posting
            cam()
            glide('#tabs [data-group="jobs"]', ms=600)
            page.wait_for_selector('#subnav [data-view="matches"]')
            page.click('#subnav [data-view="matches"]')
            page.wait_for_selector(".job")
            sweep_popups()
            cap("Every open internship, ranked against <em>your</em> resume.")
            hold(2.7)
            for _ in range(14):
                page.keyboard.press("j")
                hold(0.3)
                if "northwind" in (page.evaluate("document.querySelector('.job.kfocus')?.dataset.company || ''")):
                    break
            else:
                raise SystemExit("the film's posting isn't in the first rows of Matches")
            score = page.evaluate("document.querySelector('.job.kfocus .num').innerText")
            facts["score"] = score
            cap(f"Posted two hours ago. A <em>{score}</em> for Alex.")
            cam(".job.kfocus", 1.6, left=True)
            hold(1.9)
            glide(".job.kfocus .job-more summary", ms=500)
            cap("And it shows its work.")
            hold(2.5)

            # ---- the kit
            cap("One key: answers, resume and letter, ready.")
            hold(0.9)
            page.keyboard.press("a")
            hold(1.2)
            rec(False)
            page.wait_for_selector('#modal-root .modal:has-text("Fill in Chrome")', timeout=120000)
            cam(None, 1, ms=0)
            hold(0.7)
            P("cursor", pos[0], pos[1], 0)
            rec(True)
            cap("Orange means only Alex can answer it. <em>Jarvis won't guess.</em>")
            hold(2.5)
            page.evaluate("""() => { const q = document.querySelector('#modal-root .kit-q.needs'); if (q) q.scrollIntoView({ block: 'center', behavior: 'smooth' }); }""")
            hold(0.8)
            cam("#modal-root .kit-q.needs", 1.28)
            hold(3.0)

            # ---- the resume's look
            try:
                cap(None)
                rec(False)
                page.keyboard.press("Escape")
                if page.locator("#modal-root .modal").count():
                    page.locator("#modal-root .modal button").first.click(timeout=2000)
                page.wait_for_function("!document.querySelector('#modal-root .modal')", timeout=5000)
                page.click('#tabs [data-group="apps"]')
                hold(1.0)
                page.click('#subnav [data-view="documents"]')
                page.wait_for_selector("#docs-body", timeout=20000)
                hold(1.5)
                page.locator('#main button:text-is("Design")').click(timeout=10000)
                page.wait_for_selector("#te-ask", timeout=20000)
                hold(0.8)
                cam(None, 1, ms=0)
                P("cursor", 720, 400, 0)
                rec(True)
                cap("Describe the look. <em>The resume changes.</em>")
                hold(1.4)
                glide("#te-ask", ms=500)
                page.locator("#te-ask").press_sequentially("bold startup, deep green headings", delay=34)
                hold(0.35)
                glide("#te-ai-go", ms=450)
                hold(0.9)
                rec(False)
                page.wait_for_function("!document.querySelector('#te-ai-go').disabled", timeout=45000)
                hold(1.3)
                rec(True)
                cam("#te-stage", 1.22)
                hold(2.5)
            except Exception as e:
                print(f"(look scene skipped: {e})", flush=True)
                if segs[-1][1] == 0.0:
                    rec(False)
            if segs[-1][1] == 0.0:
                rec(False)

            # ---- the form: Jarvis fills it, on camera, and stops
            page.goto(FORM_URL)
            page.wait_for_selector("#submit")
            t0 = time.time()
            _fill_page(page, _basic_answers(basics), {"resume": resume} if resume and Path(resume).exists() else {})
            _fill_choices(page, asker({"job": {"regions": ["Canada"], "terms": []}, "basics": basics}, {"citizenship": "Canadian"}))
            facts["fill_seconds"] = round(time.time() - t0, 1)
            facts["filled"] = page.evaluate("""() => {
                const els = [...document.querySelectorAll('form input, form select')];
                const done = els.filter((e) => e.type === 'file' ? e.files.length : e.value);
                els.forEach((e) => { e.style.color = 'transparent'; });
                document.querySelector('form').classList.remove('pending');
                window.reveal = (ids, why) => ids.forEach((id, i) => setTimeout(() => {
                  const e = document.getElementById(id); if (!done.includes(e)) return;
                  e.style.color = ''; (e.type === 'file' ? e.closest('.file') : e).classList.add('filled');
                  const l = document.querySelector(`label[for="${id}"]`); if (l) l.insertAdjacentHTML('beforeend', `<i class="why">${why}</i>`);
                }, 170 * i));
                return done.length; }""")
            assert not page.evaluate("window.SUBMITTED || false"), "the form must never be submitted"
            cam(None, 1, ms=0)
            P("cursorOff")
            hold(0.2)
            rec(True)
            cap("Jarvis fills it from what Alex really wrote.")
            hold(1.3)
            cam(".grid", 1.5, dy=20, x_at=720)
            hold(0.9)
            page.evaluate("reveal(['fn','ln','em','ph','lo','sc'], 'resume')")
            hold(2.3)
            cam("#mj", 1.5, dy=-20, ms=800, x_at=720)
            hold(0.6)
            page.evaluate("reveal(['li','gh','mj','gd'], 'resume')")
            hold(1.6)
            cam("#au", 1.5, dy=40, ms=800, x_at=720)
            hold(0.6)
            page.evaluate("reveal(['cv'], 'resume'); setTimeout(() => reveal(['au','sp'], 'settings'), 170); setTimeout(() => reveal(['hear'], 'default'), 510)")
            hold(2.0)
            cam(".eeo", 1.6, ms=850, x_at=720)
            page.evaluate("""() => { const b = document.querySelector('.eeo'); b.style.transition = 'box-shadow .5s, border-color .5s'; b.style.borderColor = '#ffb547';
                b.style.boxShadow = '0 0 0 4px rgba(255,181,71,.2)'; }""")
            cap("Demographic questions stay blank. <em>Always.</em>")
            hold(2.8)
            cam("#submit", 2.0, ms=900)
            P("cursor", 1040, 560, 0)
            glide("#submit", click=False, ms=800)
            page.evaluate("""() => { const b = document.getElementById('submit'); b.animate([{ boxShadow: '0 0 0 0 rgba(37,99,235,.65)' }, { boxShadow: '0 0 0 18px rgba(37,99,235,0)' }], { duration: 1200, iterations: 3 }); }""")
            cap("It stops here. <em>You</em> press Submit.")
            hold(3.2)
            assert not page.evaluate("window.SUBMITTED || false"), "the form must never be submitted"
            rec(False)

            # ---- four days later: the inbox moves the board
            httpx.post(f"{URL}/api/applications/manual", headers=same, timeout=30,
                       json={"profile_id": pid, "company": "Northwind Robotics", "title": "Software Engineering Intern", "status": "applied"})
            page.goto(URL)
            page.wait_for_selector('#tabs [data-group="apps"]')
            hold(1.5)
            sweep_popups()
            page.click('#tabs [data-group="apps"]')
            page.click('#subnav [data-view="tracker"]')
            page.wait_for_selector('.kcol-applied .kcard:has-text("Northwind")')
            before = page.locator('.kcard:has-text("Northwind")').first.bounding_box()
            cam(None, 1, ms=0, stage=False)
            P("cursorOff")
            P("card", statement("Four days later."))
            hold(0.2)
            rec(True)
            hold(1.7)
            cam(None, 1, ms=0)
            P("cardOff")
            P("cursor", 900, 480, 0)
            cap("An email arrives.")
            hold(1.5)
            glide("#paste-email", ms=600)
            page.wait_for_selector("#e-text")
            page.fill("#e-text", INVITE)
            hold(0.6)
            glide("#email-form button.btn", ms=500)
            page.wait_for_function("document.querySelector('#e-out')?.innerText.trim()", timeout=30000)
            cam("#email-form", 1.55, left=True)
            cap("Jarvis reads it. Status, date and link, <em>done.</em>")
            hold(3.0)
            rec(False)
            page.click('#tabs [data-group="events"]')
            page.click('#tabs [data-group="apps"]')
            page.wait_for_selector('.kcol-interviewing .kcard:has-text("Northwind")')
            cam(None, 1, ms=0)
            page.evaluate("(b) => { const el = [...document.querySelectorAll('.kcol-interviewing .kcard')].find((k) => k.innerText.includes('Northwind'));"
                          " el.style.visibility = 'hidden'; window.__fly = () => { el.style.visibility = ''; const r = el.getBoundingClientRect();"
                          " el.style.position = 'relative'; el.style.zIndex = 50; el.animate([{ transform: `translate(${b.x - r.x}px, ${b.y - r.y}px) scale(1.04)` }, { transform: 'none' }],"
                          " { duration: 1150, easing: 'cubic-bezier(.6,.05,.2,1)' }); setTimeout(() => { window.jarvisConfetti?.(el.closest('.kcol')); window.jarvisSay?.('interview! let\\'s prep.', { mood: 'cheer' }); }, 1000); }; }", before)
            hold(0.5)
            rec(True)
            hold(0.7)
            cap("The board moved itself.")
            page.evaluate("window.__fly()")
            hold(3.2)

            # ---- the calendar
            glide('#tabs [data-group="events"]', ms=600)
            page.wait_for_selector(".cpane-yours .cb")
            cap("The interview is already on the calendar.")
            cam(".cpane-yours", 1, stage=False, ms=800)
            hold(1.0)
            target = '.cpane-yours .cb:has-text("Northwind")'
            if page.locator(target).count():
                cam(target, 1.75, ms=900)
            hold(2.4)
            cam(None, 1, stage=False, ms=800)
            cap("See something worth going to? <em>Drag it on.</em>")
            hold(0.9)
            free = page.evaluate("""() => { const c = [...document.querySelectorAll('.cpane-events .cb-pub')].find((x) => { const r = x.getBoundingClientRect(); return r.top > 180 && r.bottom < 760 && r.height > 28; });
                if (!c) return false; c.id = 'film-drag'; return true; }""")
            if free:
                drag("#film-drag", ".cpane-yours")
            hold(2.0)

            # ---- the Path and Jarvis
            cam()
            glide('#tabs [data-group="today"]', ms=600)
            page.wait_for_selector('#subnav [data-view="path"]')
            glide('#subnav [data-view="path"]', ms=500)
            page.wait_for_selector(".lanes, .trail-sec", timeout=20000)
            sweep_popups()
            cap("Jarvis keeps track of where Alex really is.")
            hold(2.6)
            glide("#chat-fab", ms=600)
            page.wait_for_selector("#chat-input")
            cap("Ask it anything. <em>It already knows.</em>")
            bots = page.locator("#chat-log .msg.bot").count()
            page.locator("#chat-input").press_sequentially("What's coming up?", delay=44)
            hold(0.2)
            page.press("#chat-input", "Enter")
            page.wait_for_function(f"document.querySelectorAll('#chat-log .msg.bot').length > {bots}", timeout=30000)
            cam("#chat-panel", 1.32, ms=850)
            hold(3.0)
            rec(False)

            # ---- everywhere else it lives
            brief = daily_brief()
            ready = [r["company"] for r in brief["ready_to_apply"] if r["company"] and "University" not in r["company"]][:3]
            prep = next((p for p in brief["prep_for"] if "Northwind" in p["company"]), None) or (brief["prep_for"] or [{}])[0]
            lines = ['<span class="d">coop-coach · daily_brief</span>',
                     f'<span class="m">{len(brief["ready_to_apply"])} ready to apply:</span> {", ".join(ready)}',
                     f'<span class="a">Prep:</span> {prep.get("company", "")}, {prep.get("status", "")}',
                     f'<span class="m">{brief["new_matches"]["count"]} new match{"" if brief["new_matches"]["count"] == 1 else "es"}</span> today',
                     f'{len(brief["quiet_3_weeks"])} quiet for 3+ weeks: <span class="d">want a follow-up drafted?</span>']
            if brief["events_this_week"]:
                lines.insert(3, f'<span class="m">This week:</span> {brief["events_this_week"][0]["title"][:46]}')
            term = "".join(f'<div class="l" style="animation-delay:{1.6 + i * 0.32:.2f}s">{ln}</div>' for i, ln in enumerate(lines))
            left = (f'<div><div class="lab">Chrome extension</div><img src="{panel}"></div>' if panel else "")
            P("cursorOff")
            cam(None, 1, ms=0, stage=False)
            P("card", f'<div class="eb">It isn&rsquo;t stuck in a tab</div><div class="two" style="{"" if panel else "grid-template-columns:900px"}">{left}'
                      '<div><div class="lab">Claude Code plugin</div><div class="term"><div class="dots"><i></i><i></i><i></i></div>'
                      '<div><span class="m">&gt;</span> <span data-type="what should I do today?"></span></div><br>' + term + "</div></div></div>")
            hold(0.25)
            rec(True)
            hold(6.3)

            # ---- the receipts, and out
            cells = [(facts["filled"], 0, "fields filled from Alex&rsquo;s resume and settings", ""),
                     (int(facts["score"]), 0, "match score, with its reasons shown", ""),
                     (1, 0, "email pasted. Status, date and link moved themselves", ""),
                     (0, 0, "applications sent without Alex pressing Submit", "z")]
            P("card", '<div class="eb">This run</div><div class="rcpt">' + "".join(
                f'<div style="animation-delay:{i * 0.22:.2f}s"><b class="{cl}" data-to="{v}" data-dp="{dp}" data-wait="{i * 220}">0</b><span>{t}</span></div>'
                for i, (v, dp, t, cl) in enumerate(cells)) + "</div>")
            hold(4.6)
            P("card", '<div class="p-jv">{{jarvis:happy:150}}</div><div class="wm">Co-op Coach</div><div class="tag w" style="animation-delay:.5s">You press Submit.</div>')
            hold(3.6)
            rec(False)
            ctx.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(15)
        except subprocess.TimeoutExpired:
            server.kill()
    (WORK / "timeline.json").write_text(json.dumps({"frames": frames, "segs": segs, "keys": keys, "caps": caps, "facts": facts}))
    compose(draft)


CAPTION = """<!doctype html><meta charset=utf-8>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&display=block" rel="stylesheet">
<style>html,body{margin:0;background:transparent}
#c{display:inline-block;padding:14px 38px 18px;border-radius:20px;background:rgba(9,12,14,.82);border:1px solid #26313a;box-shadow:0 18px 60px rgba(0,0,0,.45);
font:400 60px/1.12 "Instrument Serif",Georgia,serif;letter-spacing:-.006em;color:#eef2f3;white-space:nowrap}
em{color:#5cf2b0;font-style:italic}</style><div id=c></div>"""


def compose(draft: bool = False) -> None:
    """Camera, window framing, crossfades and captions over the recording, written straight to ffmpeg."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    tl = json.loads((WORK / "timeline.json").read_text())
    frames = sorted((t, p) for t, p in tl["frames"])
    stamps = [t for t, _ in frames]
    segs = [s for s in tl["segs"] if s[1] > s[0]]
    slows = [s[2] if len(s) > 2 else 1.0 for s in segs]  # a segment recorded in slow motion plays back at speed
    W, H, FPS = (960, 540, 30) if draft else (1920, 1080, 60)
    f = W / 1920

    # captions, set in the film's own type
    capdir = WORK / "caps"
    shutil.rmtree(capdir, ignore_errors=True)
    capdir.mkdir()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": 1900, "height": 200}, device_scale_factor=1)
        p.set_content(CAPTION, wait_until="networkidle")
        p.evaluate("() => Promise.all([document.fonts.load('60px \"Instrument Serif\"'), document.fonts.load('italic 60px \"Instrument Serif\"')])")
        for i, c in enumerate(tl["caps"]):
            p.evaluate("(t) => { document.getElementById('c').innerHTML = t; }", c["text"])
            p.wait_for_timeout(40)
            p.locator("#c").screenshot(path=str(capdir / f"{i}.png"), omit_background=True)
        b.close()
    caps = []
    for i, c in enumerate(tl["caps"]):
        im = Image.open(capdir / f"{i}.png").convert("RGBA")
        if f != 1:
            im = im.resize((round(im.width * f), round(im.height * f)), Image.LANCZOS)
        off = c["off"] or next((s[1] for s in segs if s[0] <= c["on"] <= s[1]), c["on"] + 3)
        caps.append((c["on"], off, im))

    # the room behind the window
    sx, sy, sw, sh = round((W - W * STAGE) / 2), round(20 * f), round(W * STAGE), round(H * STAGE)
    radius = round(18 * f)
    glow = Image.new("L", (192, 108), 0)
    ImageDraw.Draw(glow).ellipse((46, 44, 146, 134), fill=46)
    glow = glow.filter(ImageFilter.GaussianBlur(20)).resize((W, H), Image.BICUBIC)
    bg = Image.composite(Image.new("RGB", (W, H), (22, 74, 58)), Image.new("RGB", (W, H), (9, 12, 14)), glow)
    grain = Image.effect_noise((W, H), 5).convert("RGB")
    bg = ImageChops.add(bg, grain, scale=1, offset=-128)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((sx, sy + 14, sx + sw, sy + sh + 14), radius, fill=170)
    bg = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), bg, shadow.filter(ImageFilter.GaussianBlur(26 * f)))

    # the camera
    chain = []
    state = {"x": VIEW[0] / 2, "y": VIEW[1] / 2, "z": 1.0, "s": STAGE}

    def ease(p: float) -> float:
        return 4 * p * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2

    def at(k, ts):
        p = 1.0 if k["d"] <= 0 else min(1.0, max(0.0, (ts - k["t"]) / k["d"]))
        e = ease(p)
        return {n: k["from"][n] + (k[n] - k["from"][n]) * e for n in ("x", "y", "z", "s")}

    for k in tl["keys"]:
        k = dict(k)
        k["from"] = at(chain[-1], k["t"]) if chain else state
        chain.append(k)
    times = [k["t"] for k in chain]

    def camera(ts):
        i = bisect.bisect_right(times, ts) - 1
        return at(chain[max(i, 0)], ts) if chain else state

    masks: dict = {}

    def mask(w, h):
        if (w, h) not in masks:
            scale = 3
            m = Image.new("L", (w * scale, h * scale), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, w * scale - 1, h * scale - 1), radius * scale, fill=255)
            masks.clear()
            masks[(w, h)] = m.resize((w, h), Image.LANCZOS)
        return masks[(w, h)]

    durs = [(s[1] - s[0]) / k for s, k in zip(segs, slows)]
    total = sum(durs)
    count = int(total * FPS)
    starts = [sum(durs[:i]) for i in range(len(durs))]
    out = WORK / "out.mp4"
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                           "-c:v", "libx264", "-preset", "veryfast" if draft else "slow", "-crf", "24" if draft else "16",
                           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    loaded = (-1, None)
    last = None
    last_key = None
    seg_tail = None
    seg_i = -1
    cross = float(os.environ.get("PITCH_CROSS", "0.42"))
    edges = not os.environ.get("PITCH_NOEDGE")
    resample = Image.BILINEAR if draft else Image.BICUBIC
    t0 = time.time()
    for n in range(count):
        T = n / FPS
        i = bisect.bisect_right(starts, T) - 1
        if i != seg_i:
            seg_tail, seg_i = last, i
        a, b_ = segs[i][0], segs[i][1]
        ts = min(a + (T - starts[i]) * slows[i], b_)
        lo = bisect.bisect_left(stamps, a)
        hi = bisect.bisect_right(stamps, b_)
        idx = min(max(bisect.bisect_right(stamps, ts) - 1, lo), max(hi - 1, lo))
        c = camera(ts)
        alpha_caps = []
        for on, off, im in caps:
            if on <= ts <= off:
                al = min(1.0, (ts - on) / (0.28 * slows[i]), (off - ts) / (0.22 * slows[i]))
                alpha_caps.append((round(al, 2), im))
        fade_in = T - starts[i] < cross and i > 0 and seg_tail is not None
        edge = edges and (T < 0.6 or total - T < 0.9)
        key = (idx, round(c["x"], 1), round(c["y"], 1), round(c["z"], 3), round(c["s"], 3), tuple(a_ for a_, _ in alpha_caps), id(alpha_caps[0][1]) if alpha_caps else 0)
        if key != last_key or fade_in or edge:
            if idx != loaded[0]:
                loaded = (idx, Image.open(frames[idx][1]).convert("RGB"))
            src = loaded[1]
            sW, sH = src.size
            cw, ch = sW / c["z"], sH / c["z"]
            kx, ky = sW / VIEW[0], sH / VIEW[1]
            l = min(max(c["x"] * kx - cw / 2, 0), sW - cw)
            t = min(max(c["y"] * ky - ch / 2, 0), sH - ch)
            p = (c["s"] - STAGE) / (1 - STAGE)
            w, h = (W, H) if c["s"] > 0.999 else (round(W * c["s"]), round(H * c["s"]))
            win = src.resize((w, h), resample, box=(l, t, l + cw, t + ch), reducing_gap=2.0)
            if c["s"] > 0.999:
                frame = win
            else:
                frame = bg.copy()
                x, y = round((W - w) / 2), round(sy * (1 - p))
                frame.paste(win, (x, y), mask(w, h))
                ImageDraw.Draw(frame).rounded_rectangle((x, y, x + w - 1, y + h - 1), radius, outline=(44, 54, 62), width=1)
            for al, im in alpha_caps:
                m = im.getchannel("A").point(lambda v: int(v * al))
                frame.paste(im.convert("RGB"), (round((W - im.width) / 2), H - round(26 * f) - im.height), m)
            if fade_in:
                frame = Image.blend(seg_tail, frame, ease((T - starts[i]) / cross))
            if edges and T < 0.6:
                frame = Image.blend(Image.new("RGB", (W, H)), frame, T / 0.6)
            if edges and total - T < 0.9:
                frame = Image.blend(Image.new("RGB", (W, H)), frame, max(0.0, (total - T) / 0.9))
            last = frame
            last_key = key
        ff.stdin.write(last.tobytes())
        if n % (FPS * 10) == 0:
            print(f"  {T:.0f}/{total:.0f} s", flush=True)
    ff.stdin.close()
    ff.wait()
    target = WORK / "draft.mp4" if draft else OUT
    shutil.move(str(out), str(target))
    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1) if stamps[i + 1] - stamps[i] < 1]
    print(f"{target}  {total:.1f} s  {target.stat().st_size / 1e6:.1f} MB  {len(frames)} source frames, median gap {sorted(gaps)[len(gaps) // 2] * 1000:.0f} ms, composed in {time.time() - t0:.0f} s")
    print(json.dumps(tl["facts"]))


if __name__ == "__main__":
    main()
