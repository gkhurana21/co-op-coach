"""The real-app plates for the "273" film: a fast all-features montage, full frame, no window framing, hard cuts.

    PYTHONPATH=<folder with Pillow> .venv/bin/python -m tools.record_film

Writes docs/handoff/film-shots/app_a.mp4 (Today to Submit) and app_b.mp4 (inbox to Claude Code). The demo's own data only,
with a first-year resume for Alex. The form is a made-up company's page served from here and is never submitted.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from . import record_pitch as rp

os.environ["PITCH_CROSS"] = "0.2"
rp.VIEW = (1280, 720)  # closer than a desktop window, so the app reads big on screen
rp.DSF = 2.25
os.environ["PITCH_NOEDGE"] = "1"
SHOTS_ROOT = None
SHOTS = rp.ROOT / "docs" / "handoff" / "film-shots"
BASE = rp.ROOT / "docs" / ".film"
FORM = """<!doctype html><html><head><meta charset="utf-8"><title>Software Engineering Intern · Northwind Robotics</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=block" rel="stylesheet">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #0c1116; font-family: Inter, -apple-system, sans-serif; color: #e6edf3; }
  header { height: 58px; border-bottom: 1px solid #1d2630; display: flex; align-items: center; gap: 12px; padding: 0 36px; font-weight: 600; font-size: 16px; }
  .mark { width: 28px; height: 28px; border-radius: 8px; background: linear-gradient(135deg,#38bdf8,#6366f1); display: grid; place-items: center; font-size: 15px; color: #fff; }
  header nav { margin-left: auto; display: flex; gap: 22px; font-weight: 500; font-size: 14px; color: #8b98a5; }
  .page { display: grid; grid-template-columns: 330px 1fr; gap: 28px; padding: 22px 36px; }
  .job h1 { font-size: 24px; margin: 4px 0 6px; letter-spacing: -.01em; }
  .crumb { font-size: 13px; color: #8b98a5; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 16px; }
  .chips span { font-size: 12px; padding: 4px 9px; border-radius: 999px; background: #16202a; border: 1px solid #223040; color: #b7c4d0; }
  .job h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: #8b98a5; margin: 14px 0 6px; }
  .job p, .job li { font-size: 13.5px; line-height: 1.55; color: #c3ced8; margin: 0 0 6px; }
  .job ul { padding-left: 18px; margin: 0; }
  #form-card { background: #121a22; border: 1px solid #1f2b37; border-radius: 16px; padding: 18px 22px 16px; }
  .tabs { display: flex; gap: 18px; font-size: 14px; color: #8b98a5; border-bottom: 1px solid #1f2b37; margin: -4px 0 14px; }
  .tabs b { color: #e6edf3; border-bottom: 2px solid #38bdf8; padding-bottom: 9px; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .07em; color: #8b98a5; margin: 2px 0 8px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px 14px; margin-bottom: 10px; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 14px; margin-bottom: 10px; }
  label { display: block; font-size: 12.5px; font-weight: 500; color: #b7c4d0; margin-bottom: 4px; }
  input, select { width: 100%; font: inherit; font-size: 14px; padding: 8px 11px; border: 1px solid #263544; border-radius: 9px;
    background: #0e151c; color: #e6edf3; transition: border-color .35s, box-shadow .35s, color .3s, background .35s; }
  .pending input:not([type=file]), .pending select { color: transparent; }
  .filled { border-color: #5cf2b0 !important; box-shadow: 0 0 0 3px rgba(92,242,176,.16); background: #0f1d19 !important; color: #e6edf3 !important; }
  .file { border: 1.5px dashed #2c3d4e; border-radius: 10px; padding: 9px 12px; font-size: 13.5px; color: #8b98a5; display: flex; align-items: center; gap: 10px; }
  .file input { width: auto; border: 0; padding: 0; font-size: 13px; background: transparent; color: #c3ced8; }
  .file.filled { border-style: solid; }
  .eeo { background: #0f161d; border: 1px solid #1d2833; border-radius: 12px; padding: 10px 12px; margin-bottom: 12px; }
  .eeo p { margin: 0 0 6px; font-size: 12.5px; color: #8b98a5; }
  .actions { display: flex; justify-content: space-between; align-items: center; }
  .actions small { color: #6b7a88; font-size: 12px; }
  button { font: inherit; font-weight: 600; color: #04121c; background: #38bdf8; border: 0; border-radius: 10px; padding: 10px 22px; }
</style></head><body>
<header><span class="mark">◆</span>Northwind Robotics<nav><span>Robots</span><span>Customers</span><span>Careers</span></nav></header>
<div class="page">
  <div class="job"><div class="crumb">Careers / Engineering</div><h1>Software Engineering Intern</h1>
    <div class="chips"><span>Winter 2027</span><span>Waterloo, ON</span><span>Hybrid</span><span>Co-op</span></div>
    <h3>About the role</h3><p>Build the software that keeps 400 warehouse robots moving: routing, perception tooling and the dashboards our operators live in.</p>
    <h3>You might be a fit if</h3><ul><li>You've built and shipped projects in Python or C++</li><li>You like robots, data, or both</li><li>You're a co-op student in Canada</li></ul></div>
  <div id="form-card"><div class="tabs"><span>Overview</span><b>Application</b></div>
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
    <div class="actions"><small>Powered by a made-up careers page</small><button id="submit" type="submit">Submit application</button></div>
  </form></div>
</div></body></html>"""


FIRST_YEAR = """Alex Chen
alex.chen@example.com | 416-555-0123 | Waterloo, ON | github.com/alexchen-demo | linkedin.com/in/alexchen-demo | alexchen.dev

EDUCATION
University of Waterloo | Waterloo, ON
Bachelor of Computer Science, Honours, Co-op | Sept 2025 - Apr 2030
- 94% cumulative average, Dean's Honours List (Fall 2025, Winter 2026). Coursework: Data Structures, Algorithms, Object-Oriented Design, Linear Algebra, Probability.

EXPERIENCE
Software Engineering Intern | Northstar AI | Toronto, ON | May 2026 - Aug 2026
- Built an LLM evaluation pipeline in Python and FastAPI that scores 40,000 model outputs a night, cutting manual review time by 70%.
- Shipped a retrieval layer with vector embeddings and PostgreSQL that raised answer accuracy on the internal benchmark from 71% to 86%.
- Cut p95 API latency from 820 ms to 240 ms (71% faster) with Redis caching and batched embedding calls.
- Wrote the load tests and Grafana dashboards that took the service from beta to its first 3 paying customers.

Undergraduate Research Assistant | Waterloo Vision and Robotics Lab | Waterloo, ON | Jan 2026 - Present
- Train PyTorch depth-estimation models for low-light robot navigation; my augmentation pipeline cut depth error by 18% on the lab's night dataset.
- Built a CUDA data loader that tripled training throughput on the lab's two-GPU workstation, freeing 9 GPU-hours a week.
- Co-authoring a workshop paper on night-time depth estimation (in submission).

Software Lead | Campus Rover Design Team | Waterloo, ON | Sept 2025 - Present
- Lead 6 engineers on perception and control in C++ and ROS 2; moved obstacle detection to the GPU, cutting latency from 210 ms to 65 ms (69%).
- Set up CI with simulated test drives on GitHub Actions, catching 30+ regressions before they reached the robot.
- Helped the team place 4th of 36 at the Canadian International Rover Challenge 2026.

PROJECTS
Gradient: Autodiff Engine from Scratch | C++, Python, pybind11
- Reverse-mode autodiff library with a NumPy-like API; trains a 2-layer MLP on MNIST to 98% accuracy and has 1.2k GitHub stars.
- Fused kernels and an arena allocator make it 4x faster than a naive Python version on the same model.
Pathfinder: Campus Route Planner | TypeScript, React, Python, PostgreSQL
- Accessible routing across 120 campus buildings for 1,500 monthly users; won Best Social Impact at CampusHacks (400 participants).
- Custom A* search over indoor and outdoor graphs answers routes in under 40 ms at p95.
RoomBot: Study Room Scheduler | Python, AWS Lambda, DynamoDB
- Serverless Discord bot that has booked 2,000+ study rooms for a 900-member first-year server, for under $2 a month.

SKILLS
Languages: Python, C++, TypeScript, Go, SQL
Frameworks: PyTorch, React, Next.js, FastAPI, ROS 2
AI and ML: LLM APIs, RAG, Vector Embeddings, Model Evaluation, CUDA, OpenCV
Infrastructure: PostgreSQL, Redis, AWS (Lambda, EC2, S3), Docker, Kubernetes, GitHub Actions

AWARDS
President's Scholarship of Distinction, University of Waterloo (2025)
Canadian Computing Competition, Senior Division, top 1% (2024)
Best Social Impact, CampusHacks 2025 (400 participants)
"""


# the page Co-op Coach first builds from his old one (the film's makeover): clean and honest, before Resume Studio
OKAY = """Alex Chen
alex.chen@example.com | 416-555-0123 | Waterloo, ON | github.com/alexchen-demo | linkedin.com/in/alexchen-demo

EDUCATION
University of Waterloo | Waterloo, ON
Bachelor of Computer Science, Co-op | Sept 2025 - Apr 2030
- Relevant coursework: Data Structures, Algorithms, Object-Oriented Design, Linear Algebra, Probability

EXPERIENCE
Software Engineering Intern | Northstar AI | Toronto, ON | May 2026 - Aug 2026
- Built an evaluation pipeline in Python and FastAPI that scores model outputs every night, so the team reviews far fewer by hand.
- Added a retrieval layer with vector embeddings and PostgreSQL that improved answer accuracy on the team's benchmark.
- Wrote load tests and Grafana dashboards for the service before it launched to its first customers.

Undergraduate Research Assistant | Waterloo Vision and Robotics Lab | Waterloo, ON | Jan 2026 - Present
- Train PyTorch depth-estimation models that help robots navigate in low light.
- Built a faster CUDA data loader for the lab's two-GPU training workstation.

Software Lead | Campus Rover Design Team | Waterloo, ON | Sept 2025 - Present
- Lead a team of 6 engineers on perception and control in C++ and ROS 2.
- Moved obstacle detection to the GPU and set up CI with simulated test drives on GitHub Actions.

PROJECTS
Gradient: Autodiff Engine from Scratch | C++, Python
- Autodiff library with a NumPy-like API that trains a small neural network on MNIST.
Pathfinder: Campus Route Planner | TypeScript, React, PostgreSQL
- Accessible routes across campus buildings; won Best Social Impact at CampusHacks.
RoomBot: Study Room Scheduler | Python, AWS Lambda
- Discord bot that books study rooms for a first-year server.

SKILLS
Languages: Python, C++, TypeScript, Go, SQL
Tools: PyTorch, React, FastAPI, ROS 2, PostgreSQL, AWS, Docker, Git

AWARDS
President's Scholarship of Distinction, University of Waterloo; Canadian Computing Competition, Senior Division, top 1% (2024)
"""


# spacing that makes each page a full page (the renderer only ever shrinks to fit)
BEST_FILL = {"body_size": 10.8, "line_height": 1.27, "section_space": 1.25, "name_size": 25.0, "margin": 0.55}
OKAY_FILL = {"body_size": 11.8, "line_height": 1.42, "section_space": 1.9, "name_size": 28.0, "margin": 0.6}

# Slow motion for the busy moments: the page's clock runs slower while they're filmed (CSS and Web Animations through
# CDP, timers and requestAnimationFrame through this), and the film plays them back at speed, so every frame is there.
TIME_SHIM = r"""
(() => {
  if (window.__timeScale) return;
  let k = 1, r0 = performance.now(), v0 = r0;
  const real = performance.now.bind(performance);
  const now = () => v0 + (real() - r0) * k;
  window.__timeScale = (x) => { v0 = now(); r0 = real(); k = x; };
  performance.now = now;
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb) => raf(() => cb(now()));
  const st = window.setTimeout.bind(window), si = window.setInterval.bind(window);
  window.setTimeout = (f, ms, ...a) => st(f, (ms || 0) / k, ...a);
  window.setInterval = (f, ms, ...a) => si(f, (ms || 0) / k, ...a);
})();
"""

# Resume Studio's page up close: first the whole page at a glance, then near enough to read every line, gliding down.
SHEET = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; height: 100%; overflow: hidden; }
body { background: radial-gradient(900px 520px at 50% 18%, rgba(92,242,176,.09), transparent 70%), #0b0e10; }
#wrap { position: absolute; left: 0; top: 0; }
#paper { width: 816px; height: 1056px; background: #fff; border-radius: 3px; overflow: hidden; zoom: 1.42;
  box-shadow: 0 50px 140px rgba(0,0,0,.65), 0 0 0 1px rgba(255,255,255,.06); }
#wrap { transform-origin: 0 0; will-change: transform; }
iframe { border: 0; width: 816px; height: 1056px; display: block; }
</style></head><body><div id="wrap"><div id="paper"><iframe id="f"></iframe></div></div>
<script>
document.getElementById("f").srcdoc = decodeURIComponent(escape(atob("%DOC%")));
const START = %START%;
// laid out once at full size (sharp), and moved and scaled on the GPU, so every frame is smooth
const wrap = document.getElementById("wrap"), K1 = 1.42, FULL = 816 * K1;
const put = (x, y, k) => { wrap.style.transform = `translate(${x}px, ${y}px) scale(${k})`; };
put(START.x, START.y, START.w / FULL);
window.glide = () => {
  const ease = (t) => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  const k0 = START.w / FULL, X1 = (1280 - FULL) / 2, Y1 = 30, travel = 1056 * K1 - 720 + 64;
  let t0 = null;
  const step = (now) => {
    if (t0 === null) t0 = now;
    const t = (now - t0) / 1000;
    const a = ease(Math.min(1, t / 1.6));
    const y = travel * ease(Math.min(1, Math.max(0, (t - 1.5) / 4.6)));
    put(START.x + (X1 - START.x) * a, START.y + (Y1 - START.y) * a - y, k0 + (1 - k0) * a);
    if (t < 6.2) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
};
</script></body></html>"""

# Chrome with the extension's side panel, and Terminal with Claude Code using the plugin: two Mac windows on a desktop.
MAC = """<!doctype html><html><head><meta charset="utf-8"><style>
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; overflow: hidden; }
body { font-family: -apple-system, "SF Pro Text", "Helvetica Neue", sans-serif; color: #e8ecef;
  background: radial-gradient(1100px 700px at 18% 110%, #1f6f5c 0%, transparent 60%), radial-gradient(900px 600px at 95% -10%, #23466e 0%, transparent 60%),
    radial-gradient(800px 500px at 55% 55%, #16283a 0%, transparent 70%), #0c141c; }
.menubar { position: fixed; top: 0; left: 0; right: 0; height: 26px; display: flex; align-items: center; gap: 18px; padding: 0 14px;
  font-size: 13px; background: rgba(18,22,28,.42); backdrop-filter: blur(24px) saturate(1.6); color: #f2f4f6; }
.menubar b { font-weight: 700; } .menubar .r { margin-left: auto; display: flex; gap: 16px; align-items: center; opacity: .95; }
.apple { font-size: 15px; margin-top: -2px; }
.win { position: absolute; border-radius: 12px; overflow: hidden; box-shadow: 0 30px 90px rgba(0,0,0,.6), 0 0 0 .5px rgba(255,255,255,.18);
  opacity: 0; transform: translateY(18px) scale(.985); animation: rise .9s cubic-bezier(.2,.8,.2,1) forwards; }
@keyframes rise { to { opacity: 1; transform: none; } }
.lights { display: flex; gap: 8px; } .lights i { width: 12px; height: 12px; border-radius: 50%; display: block; }
.lights i:nth-child(1) { background: #ff5f57; } .lights i:nth-child(2) { background: #febc2e; } .lights i:nth-child(3) { background: #28c840; }
/* Chrome */
#chrome { left: 50px; top: 66px; width: 600px; height: 540px; background: #202124; }
.tabs { height: 40px; background: #1a1b1e; display: flex; align-items: flex-end; gap: 10px; padding: 0 12px; }
.tabs .lights { align-self: center; margin-right: 6px; }
.tab { height: 32px; width: 300px; white-space: nowrap; overflow: hidden; background: #35363a; border-radius: 9px 9px 0 0; display: flex; align-items: center; gap: 8px; padding: 0 12px; font-size: 12px; color: #e8eaed; }
.tab .fav { width: 14px; height: 14px; border-radius: 4px; background: linear-gradient(135deg,#38bdf8,#6366f1); }
.bar { height: 40px; background: #35363a; display: flex; align-items: center; gap: 12px; padding: 0 12px; }
.nav { color: #9aa0a6; font-size: 15px; letter-spacing: 6px; }
.url { flex: 1; height: 28px; border-radius: 14px; background: #202124; display: flex; align-items: center; padding: 0 14px; font-size: 12.5px; color: #bdc1c6; gap: 8px; }
.url .lock { opacity: .7; font-size: 11px; }
.ext { width: 26px; height: 26px; border-radius: 7px; display: grid; place-items: center; font-size: 13px; color: #9aa0a6; }
.ext.on { background: rgba(92,242,176,.16); color: #5cf2b0; box-shadow: 0 0 0 1px rgba(92,242,176,.35); }
.body { display: flex; height: calc(100% - 80px); }
.site { flex: 1; background: #0c1116; padding: 18px 16px; }
.site .h { height: 14px; width: 60%; border-radius: 4px; background: #1d2731; margin-bottom: 10px; }
.site .h.s { width: 40%; height: 10px; } .site .f { height: 26px; border-radius: 7px; background: #121a22; border: 1px solid #5cf2b0; margin: 10px 0; box-shadow: 0 0 0 3px rgba(92,242,176,.12); }
.site .f.e { border-color: #263544; box-shadow: none; }
.side { width: 300px; background: #111417; border-left: 1px solid #2a2f35; overflow: hidden; }
.side img { width: 100%; display: block; }
/* Terminal */
#term { left: 730px; top: 96px; width: 500px; height: 480px; background: rgba(22,24,28,.93); backdrop-filter: blur(30px); animation-delay: .25s; }
.tbar { height: 30px; display: flex; align-items: center; padding: 0 12px; background: rgba(40,42,48,.9); border-bottom: 1px solid rgba(0,0,0,.5); }
.tbar .t { flex: 1; text-align: center; font-size: 12.5px; color: #b9bfc6; font-weight: 600; margin-right: 52px; }
.tx { padding: 14px 16px; font: 12.6px/1.55 Menlo, Monaco, monospace; color: #e6e6e6; }
.box { border: 1px solid #d97757; border-radius: 8px; padding: 8px 12px; margin-bottom: 14px; color: #c9ccd1; }
.box b { color: #fff; } .o { color: #d97757; } .g { color: #8b949e; } .m { color: #5cf2b0; } .y { color: #ffb547; }
.l { opacity: 0; animation: fade .25s ease forwards; white-space: pre-wrap; } @keyframes fade { to { opacity: 1; } }
.caret { display: inline-block; width: 7px; height: 14px; background: #e6e6e6; vertical-align: -2px; animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.lab { position: absolute; font: 500 12px/1 "SF Mono", Menlo, monospace; letter-spacing: .16em; text-transform: uppercase; color: rgba(232,236,239,.72);
  opacity: 0; animation: fade .6s ease forwards; animation-delay: .9s; }
</style></head><body>
<div class="menubar"><span class="apple">&#63743;</span><b>Google Chrome</b><span>File</span><span>Edit</span><span>View</span><span>History</span><span>Window</span>
  <span class="r"><span>&#9673;</span><span>&#9776;</span><span>%CLOCK%</span></span></div>
<div class="lab" style="left:52px;top:620px">Chrome extension</div>
<div class="lab" style="left:732px;top:590px">Claude Code plugin</div>
<div class="win" id="chrome">
  <div class="tabs"><div class="lights"><i></i><i></i><i></i></div><div class="tab"><span class="fav"></span>Software Engineering Intern · Northwind</div></div>
  <div class="bar"><span class="nav">&#8592;&#8594;&#8635;</span><div class="url"><span class="lock">&#128274;</span>careers.northwind-robotics.example/apply</div>
    <span class="ext">&#9881;</span><span class="ext on">%ICON%</span></div>
  <div class="body"><div class="site"><div class="h"></div><div class="h s"></div><div class="f"></div><div class="f"></div><div class="f"></div><div class="f"></div><div class="f e"></div><div class="f e"></div></div>
    <div class="side"><img src="%PANEL%"></div></div>
</div>
<div class="win" id="term">
  <div class="tbar"><div class="lights"><i></i><i></i><i></i></div><div class="t">alex — claude — 80×30</div></div>
  <div class="tx">
    <div class="box"><span class="o">&#10043;</span> Welcome to <b>Claude Code</b><br><span class="g">  /help for help · cwd: ~/coop</span></div>
    <div><span class="g">&gt;</span> <span id="q"></span><span class="caret" id="caret"></span></div>
    <div id="out"></div>
  </div>
</div>
<script>
window.play = (lines) => {
  const q = "what should I do today?", el = document.getElementById("q"); let i = 0;
  const type = () => { el.textContent = q.slice(0, ++i); if (i < q.length) setTimeout(type, 55); else setTimeout(answer, 420); };
  const answer = () => {
    document.getElementById("caret").remove();
    document.getElementById("out").innerHTML = lines.map((ln, n) => `<div class="l" style="animation-delay:${0.15 + n * 0.32}s">${ln}</div>`).join("");
  };
  setTimeout(type, 900);
};
</script></body></html>"""


def build() -> None:
    shutil.rmtree(rp.DEMO, ignore_errors=True)
    rp.DEMO.mkdir(parents=True)
    code = """
import json, sys
from pathlib import Path
from coopcoach import db, demo
from coopcoach.textutil import company_key
demo.RESUME = sys.argv[3]
demo.build(Path(sys.argv[1]))
now, url = db.now(), sys.argv[2]
ago = now - 2 * 3600
with db.tx() as c:
    prefs = db.loads(c.execute("SELECT prefs_json FROM profiles WHERE id=1").fetchone()["prefs_json"], {}) or {}
    prefs["resume_template"] = "signature"
    c.execute("UPDATE profiles SET prefs_json=? WHERE id=1", (db.dumps(prefs),))
    for d in c.execute("SELECT id, content FROM documents WHERE profile_id=1 AND kind='resume_master'").fetchall():
        body = db.loads(d["content"], {}) or {}
        from coopcoach import resume_theme as theme
        body["style"] = {**(body.get("style") or {}), **theme.PRESETS["signature"]["style"], "one_page": True, **json.loads(sys.argv[4]), "heading_font": "cmu", "body_font": "cmu", "accent": "#1a1a1a"}  # a plain start, so the look Alex asks for is a visible change
        c.execute("UPDATE documents SET content=? WHERE id=?", (db.dumps(body), d["id"]))
    c.execute("INSERT INTO email_accounts (profile_id, kind, address, last_sync, last_status, created_at) VALUES (1,'outlook','alex.chen@example.com',?,'ok',?)", (now - 300, now))
    c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, locations, regions, terms, category, tier, skills,"
              " description, posted_at, first_seen, last_seen, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
              ("northwind-film", "ashby:northwind", "Northwind Robotics", company_key("Northwind Robotics"),
               "Software Engineering Intern (Winter 2027)", url, json.dumps(["Waterloo, ON"]), json.dumps(["Canada"]),
               json.dumps(["Winter 2027"]), "Software", "startup", json.dumps(["Python", "SQL", "React", "AWS", "Git", "Docker"]),
               "Build the software that runs our warehouse robots. Python, SQL and React. Open to first-year co-op students in Canada.",
               ago, ago, now))
"""
    subprocess.run([sys.executable, "-c", code, str(rp.DEMO), rp.FORM_URL, FIRST_YEAR, json.dumps(BEST_FILL)], cwd=rp.ROOT, env=rp.demo_env(**rp.NO_AI),
                   check=True, stdout=subprocess.DEVNULL)


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(BASE, ignore_errors=True)
    (BASE / "a" / "f").mkdir(parents=True)
    (BASE / "b" / "f").mkdir(parents=True)
    rp.WORK = BASE / "a"
    build()
    server = subprocess.Popen([sys.executable, "-m", "coopcoach", "demo", "--ai", "--port", str(rp.PORT)], cwd=rp.ROOT, env=os.environ.copy(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    parts = {"a": {"frames": [], "segs": [], "keys": [], "caps": [], "facts": {}}, "b": {"frames": [], "segs": [], "keys": [], "caps": [], "facts": {}}}
    now_part = ["a"]
    only = os.environ.get("FILM_ONLY", "")  # "b": skip part a while working on part b
    try:
        rp.wait_up()
        URL, same = rp.URL, {"Origin": rp.URL}
        pid = httpx.get(f"{URL}/api/state", timeout=30).json()["profiles"][0]["id"]
        basics, resume = rp.demo_facts()
        os.environ.update(COOPCOACH_DATA=str(rp.DEMO), COOPCOACH_DEMO="1")
        from coopcoach.apply import _basic_answers, _fill_choices, _fill_page, asker
        with sync_playwright() as pw:
            panel = rp.panel_shot(pw)
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": rp.VIEW[0], "height": rp.VIEW[1]}, device_scale_factor=rp.DSF, color_scheme="dark")
            ctx.add_init_script(TIME_SHIM)
            ctx.add_init_script(rp.OVERLAY)

            def router(route):
                u = route.request.url
                if u.startswith(URL) or u.startswith("data:") or u.startswith(rp.ALLOWED):
                    return route.continue_()
                if u.startswith(rp.FORM_URL):
                    return route.fulfill(status=200, content_type="text/html", body=FORM)
                return route.abort()

            ctx.route("**/*", router)
            page = ctx.new_page()
            cdp = ctx.new_cdp_session(page)
            cdp.send("Animation.enable")
            n = [0]

            def on_frame(ev):
                part = now_part[0]
                path = BASE / part / "f" / f"{n[0]:05d}.jpg"
                n[0] += 1
                path.write_bytes(base64.b64decode(ev["data"]))
                parts[part]["frames"].append((ev["metadata"]["timestamp"], str(path)))
                try:
                    cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
                except Exception:
                    pass

            cdp.on("Page.screencastFrame", on_frame)
            SLOW = [1.0]

            def speed(k):
                SLOW[0] = k
                cdp.send("Animation.setPlaybackRate", {"playbackRate": 1 / k})
                try:
                    page.evaluate("(k) => window.__timeScale && window.__timeScale(1 / k)", k)
                except Exception:
                    pass

            hold = lambda s: page.wait_for_timeout(int(s * 1150 * SLOW[0]))  # noqa: E731
            pos = [720.0, 400.0]
            P = lambda fn, *a: page.evaluate(f"(a) => window.__pitch.{fn}(...a)", list(a))  # noqa: E731
            p = lambda: parts[now_part[0]]  # noqa: E731

            def cam(target=None, zoom=1.0, ms=1050, dy=0.0, left=False, x_at=None, dx=0.0):
                x, y = rp.VIEW[0] / 2, rp.VIEW[1] / 2
                if target:
                    b = page.locator(target).first.bounding_box()
                    if b:
                        x, y = b["x"] + b["width"] / 2 + dx, b["y"] + b["height"] / 2 + dy
                        if left:
                            x = b["x"] - 46 + rp.VIEW[0] / zoom / 2
                        if x_at is not None:
                            x = x_at
                p()["keys"].append({"t": time.time(), "x": x, "y": y, "z": zoom, "s": 1.0, "d": ms / 1000 * SLOW[0]})

            def cap(text=None):
                t = time.time()
                if p()["caps"] and p()["caps"][-1]["off"] is None:
                    p()["caps"][-1]["off"] = t
                if text:
                    p()["caps"].append({"on": t, "off": None, "text": text})

            def rec(on, slow=2.0):
                if on:
                    speed(slow)
                    p()["segs"].append([time.time(), 0.0, slow])
                    cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 88, "maxWidth": rp.VIEW[0] * rp.DSF, "maxHeight": rp.VIEW[1] * rp.DSF, "everyNthFrame": 1})
                else:
                    hold(0.05)
                    cdp.send("Page.stopScreencast")
                    p()["segs"][-1][1] = time.time()
                    cap(None)
                    speed(1.0)

            def glide(sel, click=True, ms=380, at=None):
                loc = page.locator(sel).first
                loc.scroll_into_view_if_needed()
                b = loc.bounding_box()
                x, y = (b["x"] + min(b["width"] / 2, 70), b["y"] + b["height"] / 2) if at is None else (b["x"] + at[0], b["y"] + at[1])
                P("cursor", x, y, ms)
                page.mouse.move(x, y, steps=5)
                hold(ms / 1000 + 0.05)
                pos[:] = [x, y]
                if click:
                    P("ripple", x, y)
                    loc.click()

            def drag(x0, y0, x1, y1, ms=1300, lift=0.0):
                """A hand's drag: press, a beat, then an eased glide with a slight arc, and let go."""
                P("cursor", x0, y0, 280)
                page.mouse.move(x0, y0, steps=4)
                hold(0.35)
                page.mouse.down()
                hold(0.12)
                steps = 70
                for i in range(1, steps + 1):
                    k = i / steps
                    e = 4 * k ** 3 if k < 0.5 else 1 - (-2 * k + 2) ** 3 / 2
                    x, y = x0 + (x1 - x0) * e, y0 + (y1 - y0) * e - lift * 4 * k * (1 - k)
                    P("cursor", x, y, 0)
                    page.mouse.move(x, y)
                    page.wait_for_timeout(int(ms * SLOW[0] / steps))
                hold(0.12)
                page.mouse.up()
                pos[:] = [x1, y1]

            def tab(group, view=None):
                page.click(f'#tabs [data-group="{group}"]')
                hold(0.5)
                if view:
                    page.click(f'#subnav [data-view="{view}"]')
                    hold(0.6)
                if page.is_visible("#ms-ok"):
                    page.click("#ms-ok")

            def shot(zero=True, slow=2.0):
                cam(None, 1, ms=0)
                if zero:
                    P("cursor", pos[0], pos[1], 0)
                rec(True, slow)

            def type_in(sel, text, delay=34):
                page.locator(sel).press_sequentially(text, delay=int(delay * SLOW[0]))

            def find_northwind():
                for _ in range(60):
                    page.keyboard.press("j")
                    hold(0.04)
                    if "northwind" in page.evaluate("document.querySelector('.job.kfocus')?.dataset.company || ''"):
                        return
                raise SystemExit("the film's posting isn't in Matches")

            page.goto(URL)
            page.wait_for_selector('#tabs [data-group="apps"]')
            hold(1.5)
            page.evaluate("() => document.fonts.ready")
            page.add_style_tag(content=".milestone-bg { display: none !important; }")
            tab("jobs", "matches")

            if only != "b":
                # Today
                page.click('#tabs [data-group="today"]')
                shot()
                cap('<em>Co-op Coach</em> tells you what matters today.')
                hold(1.3)
                cam(".ph-sentence", 1.5, dy=-16, left=True, ms=800)
                hold(1.6)
                rec(False)

                # Matches and why
                tab("jobs", "matches")
                page.wait_for_selector(".job")
                shot()
                cap("Every open internship, <em>ranked against your resume.</em>")
                hold(1.7)
                rec(False)
                find_northwind()
                hold(0.8)
                box = page.locator(".job.kfocus").first.bounding_box()
                P("cursor", box["x"] + 200, box["y"] + 20, 0)
                pos[:] = [box["x"] + 200, box["y"] + 20]
                cam(".job.kfocus", 1.55, ms=0, left=True)
                rec(True)
                page.locator(".job.kfocus .job-more summary").click()
                cap("It shows <em>why.</em>")
                hold(2.0)
                rec(False)

                # Does it hire interns?
                tab("jobs", "tracks")
                page.wait_for_selector('#main input[placeholder*="Notion"]')
                shot()
                cap("Does this company even hire interns? <em>Ask.</em>")
                type_in('#main input[placeholder*="Notion"]', "Northwind", 45)
                hold(0.15)
                page.locator('#main button:text-is("Check")').click()
                hold(0.7)
                cam('#main input[placeholder*="Notion"]', 1.35, dy=70, left=True, ms=600)
                hold(1.5)
                rec(False)

                # the kit
                tab("jobs", "matches")
                page.wait_for_selector(".job")
                find_northwind()
                page.keyboard.press("a")
                page.wait_for_selector('#modal-root .modal:has-text("Fill in Chrome")', timeout=120000)
                hold(0.7)
                page.evaluate("() => { const q = document.querySelector('#modal-root .kit-q.needs'); if (q) q.scrollIntoView({ block: 'center' }); }")
                hold(0.4)
                shot()
                cap("Answers only from <em>what you've really done.</em>")
                hold(2.6)
                rec(False)

                # Resume Studio: describe the look, then the page up close
                page.keyboard.press("Escape")
                if page.locator("#modal-root .modal").count():
                    page.locator("#modal-root .modal button").first.click(timeout=2000)
                hold(0.5)
                tab("apps", "documents")
                page.wait_for_selector("#docs-body")
                hold(1.3)
                page.locator('#main button:text-is("Design")').click(timeout=10000)
                page.wait_for_selector("#te-ask", timeout=20000)
                page.wait_for_load_state("networkidle")
                hold(2.5)  # the panel redraws once its fit check is back; typing before that is wiped
                ask = "modern and vibrant: Inter font, deep teal name and headings, crisp rules"
                shot()
                cap("Describe the look. <em>The resume changes.</em>")
                glide("#te-ask", ms=380)
                type_in("#te-ask", ask, 34)
                hold(0.3)
                if page.input_value("#te-ask") != ask:
                    page.fill("#te-ask", ask)
                page.locator("#te-ai-go").click()
                hold(0.4)
                rec(False)
                page.wait_for_function("!document.querySelector('#te-ai-go').disabled", timeout=60000)
                hold(1.5)  # the look is saved; the Signature preview only redraws fonts when the page reloads, so reopen it
                page.reload()
                page.wait_for_selector('#tabs [data-group="apps"]')
                page.add_style_tag(content=".milestone-bg { display: none !important; }")
                hold(1.0)
                tab("apps", "documents")
                page.wait_for_selector("#docs-body")
                hold(1.0)
                page.locator('#main button:text-is("Design")').click(timeout=10000)
                page.wait_for_selector("#te-frame", timeout=20000)
                page.wait_for_load_state("networkidle")
                page.wait_for_function("() => { const d = document.querySelector('#te-frame')?.contentDocument; return !!(d && d.body && d.body.innerText.length > 200); }", timeout=20000)
                font = page.evaluate("() => getComputedStyle(document.querySelector('#te-frame').contentDocument.body).fontFamily")
                assert "CMU" not in font, f"the look didn't change: {font}"
                hold(1.0)
                hold(1.4)
                cam("#te-stage", 1.3, ms=0)
                rec(True)
                cap("Describe the look. <em>The resume changes.</em>")
                hold(2.0)
                rec(False)
                # where the page sits in that last frame (the camera is centred on the stage at 1.3x, kept inside the
                # screen), so the close-up starts exactly there: one move, no cut
                st_, pp = page.locator("#te-stage").bounding_box(), page.locator("#te-paper").bounding_box()
                Z, (VW, VH) = 1.3, rp.VIEW
                cx = min(max(st_["x"] + st_["width"] / 2, VW / Z / 2), VW - VW / Z / 2)
                cy = min(max(st_["y"] + st_["height"] / 2, VH / Z / 2), VH - VH / Z / 2)
                start = {"x": (pp["x"] - cx) * Z + VW / 2, "y": (pp["y"] - cy) * Z + VH / 2, "w": pp["width"] * Z}
                doc = page.evaluate("document.querySelector('#te-frame').srcdoc")
                page.set_content(SHEET.replace("%DOC%", base64.b64encode(doc.encode("utf-8")).decode()).replace("%START%", json.dumps(start)))
                page.wait_for_timeout(900)
                P("cursorOff")
                shot(zero=False, slow=2.0)
                cap("One page. <em>Every line, his.</em>")
                page.evaluate("glide()")
                hold(6.4)
                rec(False)

                # the form
                page.goto(rp.FORM_URL)
                page.wait_for_selector("#submit")
                _fill_page(page, _basic_answers(basics), {"resume": resume} if resume and Path(resume).exists() else {})
                _fill_choices(page, asker({"job": {"regions": ["Canada"], "terms": []}, "basics": basics}, {"citizenship": "Canadian"}))
                page.evaluate("""() => {
                    const els = [...document.querySelectorAll('form input, form select')];
                    const done = els.filter((e) => e.type === 'file' ? e.files.length : e.value);
                    els.forEach((e) => { e.style.color = 'transparent'; });
                    document.querySelector('form').classList.remove('pending');
                    window.reveal = (ids) => ids.forEach((id, i) => setTimeout(() => {
                      const e = document.getElementById(id); if (!done.includes(e)) return;
                      e.style.color = ''; (e.type === 'file' ? e.closest('.file') : e).classList.add('filled');
                    }, 70 * i)); }""")
                assert not page.evaluate("window.SUBMITTED || false")
                cam("#form-card", 1.36, ms=0, left=True, dy=-40)
                P("cursorOff")
                rec(True)
                cap("Fills the whole form for you.")
                hold(0.2)
                page.evaluate("reveal(['fn','ln','em','ph','lo','sc','li','gh','mj','gd','cv','au','sp','hear'])")
                hold(1.5)
                cam("#form-card", 1.36, ms=1100, left=True, dy=110)
                hold(0.9)
                cam(".eeo", 1.36, ms=1000, x_at=None, left=False, dy=0)
                page.evaluate("""() => { const b = document.querySelector('.eeo'); b.style.transition = 'box-shadow .4s, border-color .4s'; b.style.borderColor = '#ffb547'; b.style.boxShadow = '0 0 0 4px rgba(255,181,71,.25)'; }""")
                cap("Never the private questions.")
                hold(1.5)
                cam("#submit", 1.6, ms=1000)
                P("cursor", 980, 520, 0)
                glide("#submit", click=False, ms=450)
                page.evaluate("""() => document.getElementById('submit').animate([{ boxShadow: '0 0 0 0 rgba(37,99,235,.65)' }, { boxShadow: '0 0 0 18px rgba(37,99,235,0)' }], { duration: 1000, iterations: 3 })""")
                cap("And it stops here.")
                hold(2.1)
                assert not page.evaluate("window.SUBMITTED || false"), "the form must never be submitted"
                rec(False)

            # ---- part b: Jarvis reads the inbox and carries the card
            now_part[0] = "b"
            httpx.post(f"{URL}/api/applications/manual", headers=same, timeout=30,
                       json={"profile_id": pid, "company": "Northwind Robotics", "title": "Software Engineering Intern", "status": "applied"})
            page.goto(URL)
            page.wait_for_selector('#tabs [data-group="apps"]')
            page.add_style_tag(content=".milestone-bg { display: none !important; }")
            hold(1.4)
            page.evaluate("() => { localStorage.setItem('apps.layout', 'board'); }")
            tab("apps", "tracker")
            page.wait_for_selector('.kcol-applied .kcard:has-text("Northwind")')
            hold(0.8)  # the board now remembers Northwind in Applied
            r = httpx.post(f"{URL}/api/applications/from-email", headers=same, timeout=60, json={"profile_id": pid, "text": rp.INVITE})
            assert r.status_code == 200, r.text
            page.evaluate("() => { const k = document.querySelector('.kstage'); if (k) window.scrollTo(0, k.getBoundingClientRect().top + window.scrollY - 150); }")
            hold(0.3)
            cam(None, 1.0, ms=0)
            P("cursorOff")
            rec(True, 3.0)
            cap("Jarvis reads the email. <em>Your board moves itself.</em>")
            page.evaluate("renderTracker()")
            hold(3.6)
            rec(False)

            # the calendar: drag an event onto Yours
            page.click('#tabs [data-group="events"]')
            page.wait_for_selector(".cpane-yours .cb")
            hold(0.6)
            page.evaluate("""() => { const c = [...document.querySelectorAll('.cpane-events .cb-pub')].find((x) => { const r = x.getBoundingClientRect(); return r.top > 160 && r.bottom < 690 && r.height > 28; }); if (c) c.id = 'film-drag'; }""")
            page.evaluate("() => { const b = document.querySelector('.cal-board'); if (b) window.scrollTo(0, b.getBoundingClientRect().top + window.scrollY - 70); }")
            hold(0.3)
            cam(".cal-board", 1.08, ms=0, dy=-20)
            P("cursor", 900, 330, 0)
            rec(True, 3.0)
            cap("Interviews land on your calendar. <em>Drag events on.</em>")
            hold(0.5)
            if page.locator("#film-drag").count():
                a = page.locator("#film-drag").bounding_box()
                x1 = page.evaluate("""() => { const el = document.getElementById('film-drag'); const days = [...document.querySelectorAll('.cpane-events .cday')];
                    const i = days.indexOf(el.closest('.cday')); const t = document.querySelectorAll('.cpane-yours .cday')[Math.max(0, i)].getBoundingClientRect();
                    return t.left + t.width / 2; }""")
                x0, y0 = a["x"] + a["width"] / 2, a["y"] + min(a["height"] / 2, 14)
                drag(x0, y0, x1, y0, ms=1500, lift=0)
            hold(1.6)
            rec(False)

            # the Path: Jarvis on the trail, a real try logged, and he hops to the next stop
            path = httpx.get(f"{URL}/api/path?profile_id={pid}", timeout=30).json()
            nxt = path["coding"]["next"]
            page.evaluate("([pid, slug]) => { localStorage.setItem('path.lane', 'code'); localStorage.setItem(`cc-timer:${pid}:${slug}`, String(Date.now() - 23 * 60 * 1000)); }", [pid, nxt["slug"]])
            tab("today", "path")
            page.wait_for_selector(".lane-code .node.here", timeout=20000)
            hold(0.6)
            page.evaluate("window.scrollTo(0, 0)")
            P("cursorOff")
            shot(zero=False)
            cap("Interview booked. <em>Now the real prep.</em>")
            cam(".path-head", 1.25, ms=0, left=True)
            hold(2.0)
            cap("NeetCode 150: <em>the patterns interviews actually test.</em>")
            here_y = page.evaluate("document.querySelector('.lane-code .node.here').getBoundingClientRect().top + window.scrollY")
            page.evaluate("""(y) => { const y0 = window.scrollY, y1 = Math.max(0, y - 300), t0 = performance.now();
                const ease = (t) => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
                const step = (now) => { const k = Math.min(1, (now - t0) / 1900); window.scrollTo(0, y0 + (y1 - y0) * ease(k)); if (k < 1) requestAnimationFrame(step); };
                requestAnimationFrame(step); }""", here_y)
            cam(None, 1.0, ms=1900)
            hold(2.0)
            cam(".lane-code .node.here", 1.45, ms=900, dy=40)
            hold(1.6)
            rec(False)
            cam(".lane-code .node.here", 1.45, ms=0, dy=40)
            hb = page.locator(".lane-code .node.here").bounding_box()
            pos[:] = [hb["x"] + 160, hb["y"] + 90]
            P("cursor", pos[0], pos[1], 0)
            rec(True)
            cap("Log every try. <em>It comes back for review.</em>")
            hold(0.3)
            glide(".lane-code .node.here", ms=480, at=(hb["width"] / 2, hb["height"] / 2))
            page.wait_for_selector("#st-log", timeout=8000)
            cam("#modal-root .modal", 1.35, ms=700)
            hold(0.9)
            glide("#st-log", ms=420)
            page.wait_for_selector("#ld-min", timeout=10000)
            hold(0.3)
            cam("#ld-panel", 1.5, ms=800, dy=10)
            hold(0.7)
            glide('#ld-panel label.chip:has-text("Solved")', ms=380)
            hold(0.35)
            glide("#ld-notes", ms=380)
            type_in("#ld-notes", "Sort, fix one number, two pointers on the rest. Skip duplicates.", 30)
            hold(0.4)
            glide("#ld-save", ms=380)
            hold(1.3)
            row = f'[data-row="{nxt["slug"]}"]'
            if page.locator(row).count():
                page.evaluate("(r) => document.querySelector(r)?.scrollIntoView({ block: 'center', behavior: 'smooth' })", row)
                cam(row, 1.55, ms=900)
                page.evaluate("(r) => { const e = document.querySelector(r); if (e) e.animate([{ boxShadow: '0 0 0 0 rgba(92,242,176,.0)' }, { boxShadow: '0 0 0 3px rgba(92,242,176,.55)' }, { boxShadow: '0 0 0 0 rgba(92,242,176,0)' }], { duration: 1600 }); }", row)
                hold(2.2)
            rec(False)
            cam(None, 1.0, ms=0)
            P("cursorOff")
            hold(0.3)
            rec(True, 3.0)
            cap("Jarvis walks the path <em>with you.</em>")
            glide("#ld-close", ms=360)
            page.wait_for_timeout(250)
            cam(".lane-code .node-jarvis", 1.35, ms=700, dy=60)
            hold(2.6)
            rec(False)

            # Jarvis, asked something real
            page.click("#chat-fab")
            page.wait_for_selector("#chat-input")
            hold(0.4)
            cam("#chat-panel", 1.32, ms=0)
            rec(True)
            cap("Ask Jarvis. <em>It already knows you.</em>")
            bots = page.locator("#chat-log .msg.bot").count()
            type_in("#chat-input", "My Northwind interview is Wednesday. What should I practice?", 28)
            hold(0.2)
            page.press("#chat-input", "Enter")
            hold(0.8)
            rec(False)
            page.wait_for_function(f"!document.getElementById('typing') && document.querySelectorAll('#chat-log .msg.bot:not(.typing)').length > {bots}", timeout=240000)
            page.wait_for_timeout(2500)
            answer = page.evaluate("[...document.querySelectorAll('#chat-log .msg.bot')].pop()?.innerText || ''")
            parts["b"]["facts"]["jarvis_answer"] = answer
            page.evaluate("""() => { const m = [...document.querySelectorAll('#chat-log .msg.bot')].pop(); if (!m) return;
                const kids = [...m.querySelectorAll('p, li, .card, pre, h3, h4')].filter((x) => !x.closest('li') || x.tagName === 'LI');
                const parts = kids.length ? kids : [m];
                parts.forEach((x) => { x.style.opacity = 0; x.style.transform = 'translateY(6px)'; });
                window.__answer = () => parts.forEach((x, i) => setTimeout(() => { x.style.transition = 'opacity .45s, transform .45s'; x.style.opacity = 1; x.style.transform = 'none'; }, 160 * i));
                const l = document.querySelector('#chat-log'); l.scrollTop += m.closest('.msg-row').getBoundingClientRect().top - l.getBoundingClientRect().top - 70; }""")
            cam("#chat-panel", 1.32, ms=0)
            rec(True)
            page.evaluate("window.__answer()")
            hold(1.6)
            page.evaluate("""() => { const l = document.querySelector('#chat-log'); const y0 = l.scrollTop, y1 = l.scrollHeight; const t0 = performance.now();
                const step = (now) => { const k = Math.min(1, (now - t0) / 3200); l.scrollTop = y0 + (y1 - y0) * k * k * (3 - 2 * k); if (k < 1) requestAnimationFrame(step); }; requestAnimationFrame(step); }""")
            hold(3.4)
            rec(False)

            # Settings: your own AI, Co-op Coach inside it, the extension
            page.keyboard.press("Escape")
            hold(0.2)
            if page.is_visible("#chat-panel"):
                page.click("#chat-fab")
            page.click("#settings-btn")
            page.wait_for_selector('[data-sec="ai"]')
            page.click('[data-sec="ai"]')
            page.wait_for_selector("#cc-connect", timeout=20000)
            hold(1.2)
            page.evaluate("document.querySelector('.engine')?.scrollIntoView({ block: 'center' })")
            hold(0.4)
            cam(".engine", 1.45, ms=0, dy=60)
            P("cursor", 760, 600, 0)
            pos[:] = [760, 600]
            rec(True)
            cap("Sign in to <em>Claude Code</em> once. Jarvis runs on your plan.")
            glide(".engine", click=False, ms=520, at=(120, 30))
            hold(2.0)
            rec(False)
            page.evaluate("document.querySelector('#cc-connect').scrollIntoView({ block: 'center' })")
            hold(0.4)
            cam("#cc-connect", 1.4, ms=0, dy=40, dx=180)
            rec(True)
            cap("Then use Co-op Coach <em>inside Claude Code.</em>")
            glide("#cc-connect", click=False, ms=480)
            hold(0.4)
            sm = page.locator('summary:has-text("Commands you can type there")').first
            if sm.count():
                glide('summary:has-text("Commands you can type there")', ms=420)
                hold(0.4)
                cam(".ap-skills", 1.4, ms=900, dy=10)
            hold(2.0)
            rec(False)
            page.evaluate("""() => { const h = [...document.querySelectorAll('h2')].find((x) => x.textContent.includes('Chrome extension')); if (!h) return;
                const card = h.closest('.card') || h.parentElement; const btn = [...card.querySelectorAll('button')].pop();
                const bottom = (btn || card).getBoundingClientRect().bottom + window.scrollY;
                window.scrollTo(0, Math.max(h.getBoundingClientRect().top + window.scrollY - 96, bottom - 600)); }""")
            hold(0.4)
            cam(None, 1.0, ms=0)
            rec(True)
            cap("And a <em>Chrome extension</em> that fills forms where you are.")
            hold(2.6)
            rec(False)

            # the Mac: Chrome with the side panel, Terminal with Claude Code
            brief = rp.daily_brief()
            ready = [x["company"] for x in brief["ready_to_apply"] if x["company"] and "University" not in x["company"]][:3]
            prep = next((x for x in brief["prep_for"] if "Northwind" in x["company"]), None) or (brief["prep_for"] or [{}])[0]
            lines = ['<span class="m">&#9210;</span> <b>coop-coach - daily_brief</b> <span class="g">(MCP)</span>',
                     '<span class="g">  &#9151;  ' + f'{len(brief["ready_to_apply"])} ready · {brief["new_matches"]["count"]} new · 1 interview</span>',
                     "",
                     '<span class="m">&#9210;</span> Here\'s your day:',
                     f'  <span class="y">1.</span> Prep for <b>{prep.get("company", "Northwind Robotics")}</b>: your interview is Wednesday.',
                     f'  <span class="y">2.</span> {len(brief["ready_to_apply"])} applications are ready: {", ".join(ready)}.',
                     '  <span class="y">3.</span> NeetCode: one review is due today.',
                     "",
                     '<span class="g">  Want me to open the first application in Chrome?</span>']
            clock = time.strftime("%a %b %-d") + "  9:41 AM"
            mac = (MAC.replace("%PANEL%", panel or "").replace("%ICON%", "&#10022;").replace("%CLOCK%", clock))
            page.set_content(mac)
            page.wait_for_timeout(300)
            P("cursorOff")
            cam(None, 1, ms=0)
            rec(True)
            cap("Also in <em>Chrome</em> and <em>Claude Code.</em>")
            page.evaluate("(l) => play(l)", lines)
            hold(6.4)
            rec(False)
            ctx.close()
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(15)
        except subprocess.TimeoutExpired:
            server.kill()
    for part in ("a", "b"):
        if only and part != only:
            continue
        rp.WORK = BASE / part
        rp.OUT = SHOTS / f"app_{part}.mp4"
        tl = parts[part]
        (rp.WORK / "timeline.json").write_text(json.dumps(tl))
        segs = [x for x in tl["segs"] if x[1] > x[0]]
        marks, acc = [], 0.0
        for a_, b_, k in segs:  # where each caption lands in the finished plate, for the narration
            marks += [{"t": round(acc + (c["on"] - a_) / k, 2), "text": c["text"]} for c in tl["caps"] if a_ <= c["on"] < b_]
            acc += (b_ - a_) / k
        (SHOTS / f"app_{part}.json").write_text(json.dumps(marks, indent=1))
        if tl["facts"]:
            (SHOTS / f"app_{part}_facts.json").write_text(json.dumps(tl["facts"], indent=1))
        rp.compose(False)
    shutil.rmtree(BASE, ignore_errors=True)


if __name__ == "__main__":
    main()
