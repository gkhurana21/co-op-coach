"""Assemble the "273" film from the shots in docs/handoff/film-shots: docs/film-273.mp4 and a web copy.

    PYTHONPATH=<folder with Pillow and numpy> .venv/bin/python -m tools.cut_film [--draft]

All words are set here in the film's type, the hall going dark is done here, and the soundtrack is synthesized here, so there is
nothing to license.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "handoff" / "film-shots"
WORK = ROOT / "docs" / ".cut"
OUT, WEB = ROOT / "docs" / "film-273.mp4", ROOT / "docs" / "film-273-web.mp4"
SNAP = 4.67  # the frame where the reveal turns into 273 of him
FPS = 60
SR = 44100
MINT = "#5cf2b0"

FONTS = '<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist+Mono:wght@400;500&display=block" rel="stylesheet">'
BASE_CSS = f"""html,body{{margin:0;width:1920px;height:1080px;background:transparent;color:#eef2f3;overflow:hidden}}
.c{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}}
.s{{font:400 104px/1.08 "Instrument Serif",Georgia,serif;letter-spacing:-.012em;max-width:1560px;text-shadow:0 4px 40px rgba(0,0,0,.75)}}
em{{color:{MINT};font-style:italic}}
.src{{font:400 22px/1 "Geist Mono",monospace;color:#c3ccd1;margin-top:34px;text-shadow:0 2px 12px rgba(0,0,0,.9)}}
.chip{{position:absolute;left:50%;bottom:92px;transform:translateX(-50%);font:500 30px/1 "Geist Mono",monospace;padding:18px 28px;border-radius:14px;background:rgba(8,11,13,.88);border:1px solid #2c3840;color:{MINT};white-space:nowrap}}
.n{{position:absolute;right:76px;top:58px;font:400 150px/1 "Instrument Serif",Georgia,serif;font-variant-numeric:tabular-nums;text-shadow:0 4px 34px rgba(0,0,0,.85)}}
.u{{position:absolute;font:400 italic 64px/1 "Instrument Serif",Georgia,serif;color:#ff8f82;white-space:nowrap;text-shadow:0 3px 18px rgba(0,0,0,.9)}}
.wm{{font:400 150px/1 "Instrument Serif",Georgia,serif;letter-spacing:-.015em;margin-top:30px}}
.endtag{{font:400 italic 58px/1.2 "Instrument Serif",Georgia,serif;color:{MINT};margin-top:18px}}
.fine{{position:absolute;bottom:48px;left:0;right:0;text-align:center;font:400 22px/1 "Geist Mono",monospace;color:#7f8c95}}
.sv{{filter:drop-shadow(0 0 44px rgba(92,242,176,.55))}}"""


def render_type() -> dict:
    d = WORK / "type"
    d.mkdir(parents=True)
    out: dict = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        p.set_content(f"<!doctype html><meta charset=utf-8>{FONTS}<style>{BASE_CSS}</style><body><div id=r></div>", wait_until="networkidle")
        p.evaluate("""() => Promise.all(['104px "Instrument Serif"','italic 104px "Instrument Serif"','30px "Geist Mono"'].map((f) => document.fonts.load(f)))""")

        def shot(name, html):
            p.evaluate("(h) => { document.getElementById('r').innerHTML = h; }", html)
            p.wait_for_timeout(25)
            path = d / f"{name}.png"
            p.screenshot(path=str(path), omit_background=True)
            out[name] = path

        def card(name, text, extra=""):
            shot(name, f'<div class=c><div class=s>{text}</div>{extra}</div>')

        shot("sent", '<div class=chip>Application sent ✓</div>')
        for v in sorted(set(round(273 * (k / 60) ** 2.2) or 1 for k in range(61))):
            shot(f"n{v}", f'<div class=n>{v}</div>')
        card("stat", "The average tech internship gets <em>273</em> applications.", '<div class=src>Handshake Internships Index, 2024–25</div>')
        card("same", "To a recruiter, <em>you all look the same.</em>")
        card("jan", "So Alex stops applying <em>like everyone else.</em>")
        card("yours", "One application that's <em>actually his.</em>")
        card("one", "One application. <em>Yours.</em>")
        card("four", "Four days later.")
        rows = [("Co-op Office", "Reminder: job search workshop this Thursday"), ("Residence Life", "Winter term newsletter"), ("Job Alerts", "12 new roles you might like"), ("Prof. Rao", "A3 solutions are posted")]
        row = '<div style="display:flex;gap:30px;padding:22px 30px;border-top:1px solid #222a31;font:400 27px/1.2 Geist,system-ui,sans-serif"><b style="width:300px;font-weight:500">{}</b><span style="color:#8a97a0">{}</span></div>'
        head = '<div style="position:absolute;inset:0;background:#0b0e10"></div><div style="position:absolute;left:330px;width:1260px;top:150px;background:#12161a;border:1px solid #222a31;border-radius:20px;overflow:hidden"><div style="padding:28px 30px;display:flex;align-items:baseline;justify-content:space-between"><span style="font:400 54px/1 Instrument Serif,serif">Inbox</span><span style="font:400 21px/1 Geist Mono,monospace;color:#5cf2b0">● connected · alex.chen@example.com</span></div>'
        shot("inbox0", head + "<div style='height:89px'></div>" + "".join(row.format(a, b) for a, b in rows) + "</div>")
        shot("inbox1", '<div style="position:absolute;left:331px;width:1258px;top:262px;background:#17271f;border-left:5px solid #5cf2b0;box-sizing:border-box">' + row.format("Northwind Robotics", "<span style='color:#eef2f3'>Interview invitation: Software Engineering Intern</span>") + "</div>")
        shot("inbox2", '<div class=chip style="bottom:150px;font-size:32px">Jarvis read it → Interview · Wed 11:00 AM · link saved</div>')
        left = '<div style="position:absolute;left:120px;top:0;bottom:0;width:640px;display:flex;align-items:center"><div class=s style="font-size:96px;text-align:left">{}</div></div>'
        shot("b1", left.format("His first <em>resume.</em>"))
        shot("b2", left.format("Same Alex. <em>Finally said properly.</em>"))
        note = '<div style="position:absolute;right:{}px;top:{}px;font:500 26px/1 \'Geist Mono\',monospace;background:#ff6b5e;color:#1a0806;padding:9px 15px;border-radius:9px;transform:rotate({}deg)">{}</div>'
        shot("notes", note.format(250, 205, -3, "says nothing") + note.format(180, 470, 2, "typo") + note.format(230, 735, -2, "no numbers"))
        p.set_viewport_size({"width": 1920, "height": 1080})
        BAD = """<!doctype html><meta charset=utf-8><style>body{margin:0;background:#fff;color:#111;font:13.5px/1.28 'Times New Roman',serif}#p{width:760px;height:984px;padding:26px 30px;box-sizing:border-box;overflow:hidden}
        h1{font:700 30px 'Comic Sans MS','Times New Roman',serif;text-align:center;margin:0 0 3px}.c{text-align:center;margin-bottom:12px}b{text-decoration:underline}p{margin:0 0 11px}</style><div id=p>
        <h1>ALEX CHEN - RESUME</h1><div class=c>alexchen2007_coolguy@hotmail.com &nbsp; 416 555 0123 &nbsp; Waterloo</div>
        <p><b>OBJECTIVE</b><br>I am a hardworking and motivated team player looking for a oppurtunity to grow my skills in a fast paced enviroment and learn new things from experienced people at your company.</p>
        <p><b>EDUCATION</b><br>University of Waterloo, Computer Science. Currently in first year.<br>High School Diploma, Honour Roll</p>
        <p><b>WORK EXPERIENCE</b><br>Campus Rover Design Team<br>- Worked on the software for the rover<br>- Went to team meetings every week<br>- Responsable for helping other members with code and various tasks as needed<br><br>Maple Street Community Pantry<br>- Helped out with the computer system<br>- Good communication with volunteers<br>- Did other duties as assigned</p>
        <p><b>PROJECTS</b><br>Made a app for finding buildings on campus<br>Made a stock trading program<br>Made a discord bot</p>
        <p><b>SKILLS</b><br>Microsoft Word, Microsoft Excel, PowerPoint, Python, Java, Typing, Fast Learner, Leadership, Time Management, Problem Solving, Hard Worker</p>
        <p><b>INTERESTS</b><br>Video games, Basketball, Hanging out with friends, Coding</p>
        <p><b>REFERENCES</b><br>Avaliable upon request</p></div>"""
        pg = b.new_page(viewport={"width": 800, "height": 1020}, device_scale_factor=1)
        pg.set_content(BAD)
        pg.locator("#p").screenshot(path=str(d / "bad.png"))
        pg.close()
        out["bad"] = d / "bad.png"
        shot("not", '<div class=c style="align-items:flex-start;padding-left:120px;box-sizing:border-box"><div class=s style="max-width:760px;text-align:left">Not <em>1 of 273.</em></div></div>')
        rng = random.Random(273)
        spots = [(rng.randint(-60, 1660), rng.randint(10, 1020), rng.uniform(0.55, 1.5), rng.randint(-6, 6)) for _ in range(150)]
        for k in range(1, 26):
            html = "".join(f'<div class=u style="left:{x}px;top:{y}px;font-size:{int(64 * s)}px;transform:rotate({r}deg)">unfortunately,</div>' for x, y, s, r in spots[:k * 6])
            shot(f"flood{k}", html)
        p.add_style_tag(path=str(ROOT / "coopcoach/static/style.css"))
        p.add_style_tag(path=str(ROOT / "coopcoach/static/phosphor.css"))
        p.add_style_tag(content="html,body{background:transparent!important}")
        p.add_script_tag(content="var S = {};")
        src = (ROOT / "coopcoach/static/jarvis.js").read_text()
        svg = p.evaluate("(s) => { const f = new Function('const esc = (x) => String(x);' + s + '; return jarvisSvg(\"hi\", 210, { label: \"Jarvis\" });'); return f(); }", src)
        shot("end", f'<div class=c><div class=sv>{svg}</div><div class=wm>Co-op Coach</div><div class=endtag>You press Submit.</div></div><div class=fine>Made in Waterloo, for everyone chasing their first co-op.</div>')
        b.close()
    return out


def render_good() -> Path:
    """The page Co-op Coach first makes from his old one (OKAY in record_film), printed by the app's own renderer, sharp enough to zoom into."""
    import os
    import tempfile
    pdf = WORK / "good.pdf"
    code = ("import re, sys; from coopcoach import render, resume, resume_studio as rs; "
            "src = open('tools/record_film.py').read(); t = re.search(r'OKAY = \"\"\"(.*?)\"\"\"', src, re.S).group(1); "
            "p = resume.heuristic_parse(t); r = rs.build_from_text(t, p); "
            "from coopcoach import resume_theme as theme; r['style'] = {**(r.get('style') or {}), **theme.PRESETS['signature']['style'], 'one_page': True, **eval(re.search(r'OKAY_FILL = (\\{.*?\\})', src).group(1))}; "
            "r = rs.normalize(r); open(sys.argv[1], 'wb').write(render.pdf(render.resume_html(r, 'signature')))")
    env = {**os.environ, "COOPCOACH_DATA": tempfile.mkdtemp(), "COOPCOACH_DEMO": "1", "GROQ_API_KEY": "", "ANTHROPIC_API_KEY": "", "COOPCOACH_CLAUDE_BIN": "/nonexistent", "COOPCOACH_CODEX_BIN": "/nonexistent"}
    subprocess.run([sys.executable, "-c", code, str(pdf)], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["pdftoppm", "-r", "300", "-png", "-singlefile", str(pdf), str(WORK / "good")], check=True)
    return WORK / "good.png"


def clip_frames(name: str, src: Path, start: float, dur: float | None) -> list[Path]:
    d = WORK / name
    d.mkdir(parents=True)
    smooth = "minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1:scd=none," if name in ("v1", "goose", "v3") else ""  # 24 fps AI clips, properly up to 60
    args = ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(src)]
    if dur:
        args += ["-t", str(dur)]
    subprocess.run(args + ["-vf", f"{smooth}fps={FPS},scale=1920:1080:flags=lanczos", "-q:v", "2", str(d / "%05d.jpg")], check=True)
    return sorted(d.glob("*.jpg"))


def dur_of(p: Path) -> float:
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).strip())


# --------- sound
def ms(s):
    return int(s * SR)


def lowpass(x, cut):
    a = 1 - np.exp(-2 * np.pi * cut / SR)
    y = np.zeros_like(x)
    v = 0.0
    for i in range(len(x)):
        v += a * (x[i] - v)
        y[i] = v
    return y


rng = np.random.default_rng(273)


def click_sound():
    n = ms(0.035)
    e = np.exp(-np.linspace(0, 9, n))
    return (rng.normal(0, 1, n) * 0.5 + np.sin(2 * np.pi * 2300 * np.arange(n) / SR) * 0.5) * e


def place(buf, t, s, gain=1.0, pan=0.0):
    i = ms(t)
    if i >= buf.shape[0]:
        return
    n = min(len(s), buf.shape[0] - i)
    buf[i:i + n, 0] += s[:n] * gain * (1 - max(pan, 0))
    buf[i:i + n, 1] += s[:n] * gain * (1 + min(pan, 0))


def score(total: float, t: dict) -> np.ndarray:
    """Clicks, hits, buzzes, hum and an original synth score that comes in when the hall goes dark."""
    buf = np.zeros((ms(total) + 1, 2))
    click = click_sound()
    place(buf, 0.75, click, 0.9)
    for _ in range(900):  # 273 people pressing Enter, thickening towards the snap
        at = 1.5 + (SNAP - 1.55) * rng.random() ** 0.45
        place(buf, at, click, 0.05 + 0.13 * (at - 1.5) / 3.2, rng.uniform(-0.8, 0.8))

    def hit(at, gain=1.0):
        n = ms(1.8)
        x = np.arange(n) / SR
        s = np.sin(2 * np.pi * (46 + 60 * np.exp(-x * 30)) * x) * np.exp(-x * 2.4) + rng.normal(0, 1, n) * np.exp(-x * 26) * 0.35
        place(buf, at, s, gain)

    hit(SNAP, 1.0)
    # hum under the statements
    a, b = t["stat"], t["goose"]
    n = ms(b - a)
    x = np.arange(n) / SR
    hum = (np.sin(2 * np.pi * 55 * x) + 0.4 * np.sin(2 * np.pi * 110.4 * x)) * np.minimum(1, x / 0.5) * np.minimum(1, (b - a - x) / 0.2)
    place(buf, a, hum, 0.13)
    # phones
    fa, fb = t["flood"], t["goose"]
    for _ in range(46):
        at = fa + (fb - fa) * rng.random() ** 0.7
        m = ms(0.32)
        y = np.arange(m) / SR
        place(buf, at, np.sign(np.sin(2 * np.pi * 148 * y)) * np.sin(np.pi * y / 0.32) * (np.sin(2 * np.pi * 22 * y) > -0.3), 0.075, rng.uniform(-0.9, 0.9))

    # the score: A minor, F, C, G at 112 bpm
    bpm = 112
    beat = 60 / bpm
    chords = [(110.0, [220.0, 261.63, 329.63]), (87.31, [174.61, 220.0, 261.63]), (130.81, [196.0, 261.63, 329.63]), (98.0, [196.0, 246.94, 293.66])]
    def section(start, end, drums_from):
        dur = end - start
        m = np.zeros((ms(dur) + 1, 2))
        bars = int(dur / (beat * 4)) + 2
        for b_i in range(bars):
            root, notes = chords[b_i % 4]
            at = b_i * 4 * beat
            ln = ms(beat * 4)
            x = np.arange(ln) / SR
            pad = np.zeros(ln)
            for f in notes:
                for det in (-0.6, 0.6):
                    pad += 2 * ((x * (f + det)) % 1) - 1
            pad = lowpass(pad / 6, 700 + 900 * min(1, at / max(dur, 1)))
            env = np.minimum(1, x / 0.08) * np.minimum(1, (beat * 4 - x) / 0.08)
            bass = np.sin(2 * np.pi * root / 2 * x)
            i0 = ms(at)
            n_ = min(ln, m.shape[0] - i0)
            if n_ <= 0:
                continue
            m[i0:i0 + n_, 0] += (pad * env * 0.34 + bass * 0.16)[:n_]
            m[i0:i0 + n_, 1] += (pad * env * 0.34 + bass * 0.16)[:n_]
            for s16 in range(16):  # arpeggio
                f = (notes + [notes[0] * 2])[[0, 1, 2, 3, 2, 1, 0, 2][s16 % 8]] * 2
                st = at + s16 * beat / 4
                ln2 = ms(0.17)
                y = np.arange(ln2) / SR
                pl = (2 * ((y * f) % 1) - 1) * np.exp(-y * 15)
                i1 = ms(st)
                n2 = min(ln2, m.shape[0] - i1)
                if n2 > 0:
                    pan = 0.45 * np.sin(s16)
                    m[i1:i1 + n2, 0] += pl[:n2] * 0.075 * (1 - pan)
                    m[i1:i1 + n2, 1] += pl[:n2] * 0.075 * (1 + pan)
            for q in range(4):  # kick and hats, once the app is on screen
                st = at + q * beat
                if start + st < drums_from:
                    continue
                ln3 = ms(0.26)
                y = np.arange(ln3) / SR
                kick = np.sin(2 * np.pi * (48 + 110 * np.exp(-y * 34)) * y) * np.exp(-y * 11)
                i2 = ms(st)
                n3 = min(ln3, m.shape[0] - i2)
                if n3 > 0:
                    m[i2:i2 + n3, 0] += kick[:n3] * 0.5
                    m[i2:i2 + n3, 1] += kick[:n3] * 0.5
                hl = ms(0.05)
                h = rng.normal(0, 1, hl) * np.exp(-np.arange(hl) / SR * 90)
                i3 = ms(st + beat / 2)
                n4 = min(hl, m.shape[0] - i3)
                if n4 > 0:
                    m[i3:i3 + n4, :] += (h[:n4] * 0.11)[:, None]
        L = m.shape[0]
        x = np.arange(L) / SR
        m *= np.minimum(1, x / 1.2)[:, None] * np.minimum(1, (dur - x) / 0.5).clip(0)[:, None]
        i = ms(start)
        n = min(L, buf.shape[0] - i)
        buf[i:i + n] += m[:n]

    section(t["dark"], t["press"], t["appa"])
    section(t["inbox"], total, t["board"])
    hit(t["makeover"] + 2.3, 0.6)
    hit(t["appa"], 0.55)
    hit(t["board"], 0.55)
    hit(t["inbox"] + 0.8, 0.3)
    place(buf, t["inbox"] + 1.75, click, 0.5)
    hit(t["end"], 0.45)

    def embed(name, at, gain):
        src = SHOTS / name
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"], capture_output=True, check=True).stdout
        a = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
        i = ms(at)
        n = min(a.shape[0], buf.shape[0] - i)
        buf[i:i + n] += a[:n] * gain

    embed("goose.mp4", t["goose"], 1.0)
    embed("v3.mp4", t["press"], 1.1)
    buf = np.tanh(buf * 1.05)
    buf /= max(1e-6, np.abs(buf).max() / 0.92)
    return buf


FILM_LINES = [  # (segment, seconds in, words) or (plate, caption it follows, seconds after, words): a story told over the pictures, with room to breathe
    ("reveal", 0.6, "This is Alex. He just applied to his dream internship."),
    ("stat", 0.6, "So did two hundred and seventy-two other people."),
    ("jan", 0.2, "So he stops sending more,"),
    ("dark", 1.0, "and starts sending better ones."),
    ("makeover", 0.4, "Co-op Coach rebuilds his résumé from what he's actually done."),
    ("a", "matters today", 0.3, "Every morning, it tells him what's worth his time."),
    ("a", "ranked", 0.2, "Every open internship, ranked against his résumé. And why."),
    ("a", "hire interns", 0.2, "Not sure a company even hires interns? Just ask."),
    ("a", "really done", 0.2, "Every answer comes from his real experience."),
    ("a", "Describe the look", 0.3, "He describes the look he wants, and Resume Studio redesigns it."),
    ("a", "One page", 0.6, "One full page. Every line, something he really did."),
    ("a", "whole form", 0.2, "Then it fills out the whole application,"),
    ("a", "stops here", 0.1, "and stops, right before submit."),
    ("press", 0.9, "That click is his."),
    ("four", 0.1, "Four days later,"),
    ("inbox", 1.0, "Northwind writes back."),
    ("b", "board moves itself", 0.3, "Jarvis reads it, and moves the card for him,"),
    ("b", "calendar", 0.2, "and the interview lands right on his calendar."),
    ("b", "real prep", 0.2, "Now the real prep. The NeetCode one-fifty: the patterns behind most interviews."),
    ("b", "Log every try", 0.3, "Every try gets logged, and the tough ones come back before he forgets them."),
    ("b", "walks the path", 0.3, "And Jarvis walks the path with him, one problem at a time."),
    ("b", "Ask Jarvis", 0.2, "Stuck? Jarvis already knows his week."),
    ("b", "Sign in to", 0.3, "Sign in to Claude Code once. Jarvis runs on his plan."),
    ("b", "inside Claude Code", 0.3, "Co-op Coach works inside Claude Code,"),
    ("b", "Chrome extension", 0.3, "and in Chrome, right where he applies."),
    ("calm", 0.6, "Now, he's not one of two hundred and seventy-three."),
    ("end", 1.0, "Co-op Coach. You just press submit."),
]


def total_len(t: dict) -> float:
    return t["end"] + 5.0


def narration(audio: np.ndarray, t: dict) -> np.ndarray:
    """The voice over the film: each line where its moment starts, read at its own pace (never sped up), never on top of
    the line before; the music eases down under it and back up in the gaps."""
    from . import kvoice
    marks = {k: json.loads((SHOTS / f"app_{k}.json").read_text()) for k in ("a", "b")}
    seg = {"a": "appa", "b": "board"}
    vo = np.zeros_like(audio)
    end = 0.0
    plan = []
    for item in FILM_LINES:
        if len(item) == 4:
            part, needle, off, text = item
            plan.append((t[seg[part]] + next(m["t"] for m in marks[part] if needle in m["text"]) + off, text))
        else:
            key, off, text = item
            plan.append((t[key] + off, text))

    def read(path):  # a warm, even read: rumble cut, gentle compression, every line at the same loudness, soft edges
        af = "highpass=f=75,lowshelf=f=180:g=1.5,acompressor=threshold=-20dB:ratio=2.5:attack=8:release=120,silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse"
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-af", af, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
        v = np.frombuffer(raw, dtype=np.float32).astype(np.float64).copy()
        v *= 0.11 / max(1e-6, np.sqrt(np.mean(v ** 2)))
        f = min(len(v) // 2, int(0.02 * SR))
        v[:f] *= np.linspace(0, 1, f)
        v[-f:] *= np.linspace(1, 0, f)
        return v

    worst = 0.0
    for n_, (at, text) in enumerate(plan):
        v = read(kvoice.say(text))
        late = end + 0.25 - at
        at = max(at, end + 0.25)
        place(vo, at, v, 1.0)
        end = at + len(v) / SR
        worst = max(worst, late)
        print(f"  {at:6.1f}-{end:6.1f}s {'(late %.1fs) ' % late if late > 0 else ''}{text}", flush=True)
    print(f"  latest push {worst:.2f}s", flush=True)
    duck = np.convolve(np.abs(vo[:, 0]), np.ones(15000) / 15000, mode="same")
    duck = np.clip(duck * 14, 0, 1)
    out = audio * 0.62 * (1 - 0.6 * duck)[:, None] + vo
    peak = np.abs(out).max()
    return out * (0.9 / peak) if peak > 0.9 else out

def main() -> None:
    draft = "--draft" in sys.argv
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    T = render_type()
    good_png = render_good()
    W, H = (960, 540) if draft else (1920, 1080)
    a_len, b_len, g_len = dur_of(SHOTS / "app_a.mp4"), dur_of(SHOTS / "app_b.mp4"), 3.75
    t: dict = {}
    segs = []
    clock = 0.0

    def add(key, kind, dur, **kw):
        nonlocal clock
        t[key] = clock
        segs.append({"key": key, "kind": kind, "dur": dur, **kw})
        clock += dur

    add("reveal", "video", SNAP, frames=clip_frames("v1", SHOTS / "v1.mp4", 0, SNAP))
    add("stat", "still", 6.2, img="f2.jpg", zoom=(1.0, 1.07), dim=0.5)
    add("flood", "still", 2.8, img="f2.jpg", zoom=(1.07, 1.1), dim=0.5)
    add("goose", "video", g_len, frames=clip_frames("goose", SHOTS / "goose.mp4", 0, g_len))
    add("jan", "black", 1.4)
    add("dark", "dark", 4.2)
    add("makeover", "makeover", 8.2)
    add("appa", "video", a_len, frames=clip_frames("a", SHOTS / "app_a.mp4", 0, None))
    add("press", "video", 4.0, frames=clip_frames("v3", SHOTS / "v3.mp4", 0, 4.0))
    add("four", "black", 1.3)
    add("inbox", "black", 3.4)
    add("board", "video", b_len, frames=clip_frames("b", SHOTS / "app_b.mp4", 0, None))
    add("calm", "still", 3.2, img="f7.jpg", zoom=(1.0, 1.05), dim=0.82)
    add("end", "black", 5.0)
    total = clock

    ov = [("sent", 0.85, SNAP - 0.12), ("stat", t["stat"] + 0.25, t["stat"] + 3.05), ("same", t["stat"] + 3.3, t["stat"] + 6.05), ("jan", t["jan"] + 0.1, t["jan"] + 1.3),
          ("yours", t["dark"] + 1.0, t["dark"] + 4.05), ("b1", t["makeover"] + 0.15, t["makeover"] + 2.1), ("notes", t["makeover"] + 0.75, t["makeover"] + 2.15), ("b2", t["makeover"] + 2.75, t["makeover"] + 4.6), ("one", t["press"] + 2.75, t["press"] + 3.95), ("four", t["four"] + 0.05, t["four"] + 1.2), ("inbox0", t["inbox"], t["inbox"] + 3.5), ("inbox1", t["inbox"] + 0.8, t["inbox"] + 3.5), ("inbox2", t["inbox"] + 1.75, t["inbox"] + 3.5),
          ("not", t["calm"] + 0.4, t["calm"] + 3.1), ("end", t["end"] + 0.2, total + 1)]
    numbers = sorted(int(k.lstrip("n")) for k in T if k[0] == "n" and k[1:].isdigit())

    def load_rgba(k, cache={}):
        if k not in cache:
            im = Image.open(T[k]).convert("RGBA")
            cache[k] = im if (W, H) == (1920, 1080) else im.resize((W, H), Image.LANCZOS)
        return cache[k]

    papers: dict = {}
    PH = round(H * 0.91)

    def load_paper(k):
        if k not in papers:
            im = Image.open(T["bad"] if k == "bad" else good_png).convert("RGB")
            papers[k] = im.resize((round(im.width * PH / im.height), PH), Image.LANCZOS)
        return papers[k]

    room = Image.new("RGB", (W, H), (9, 13, 14))
    glow = Image.new("L", (192, 108), 0)
    from PIL import ImageDraw, ImageFilter
    ImageDraw.Draw(glow).ellipse((96, 12, 190, 104), fill=70)
    room = Image.composite(Image.new("RGB", (W, H), (28, 104, 78)), room, glow.filter(ImageFilter.GaussianBlur(22)).resize((W, H), Image.BICUBIC))
    wav = WORK / "a.wav"
    audio = score(total, t)
    if not os.environ.get("FILM_SILENT"):
        audio = narration(audio, t)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

    target = WORK / "out.mp4"
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(wav),
                           "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast" if draft else "slow", "-crf", "24" if draft else "16",
                           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(target)], stdin=subprocess.PIPE)
    stills: dict = {}
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    last_frame = None
    grains = [np.random.default_rng(k).normal(0, 4.5, (H, W, 1)).astype(np.int16) for k in range(6)]
    for seg in segs:
        n = round(seg["dur"] * FPS)
        into = last_frame
        for i in range(n):
            T_ = t[seg["key"]] + i / FPS
            p = i / max(n - 1, 1)
            kind = seg["kind"]
            if kind == "video":
                fs = seg["frames"]
                frame = Image.open(fs[min(int(i), len(fs) - 1)]).convert("RGB")
                if (W, H) != (1920, 1080):
                    frame = frame.resize((W, H), Image.BILINEAR)
            elif kind == "makeover":
                frame = room.copy()
                bad_, good_ = load_paper("bad"), load_paper("good")
                px, py = round(W * 0.72 - bad_.width / 2), round((H - bad_.height) / 2)
                frame.paste(bad_, (px, py))
                wipe = max(0.0, min(1.0, (i / FPS - 2.15) / 0.55))
                wipe = wipe * wipe * (3 - 2 * wipe)
                if wipe > 0:
                    w_ = max(1, round(good_.width * wipe))
                    frame.paste(good_.crop((0, 0, w_, good_.height)), (px, py))
                    if wipe < 1:
                        frame.paste(Image.new("RGB", (max(2, round(5 * W / 1920)), good_.height), (92, 242, 176)), (px + w_, py))
                # then close enough to read: the page comes to the middle, grows to fill the frame and glides down it
                tt = i / FPS
                ez = lambda v: 0.0 if v <= 0 else 1.0 if v >= 1 else (4 * v ** 3 if v < 0.5 else 1 - (-2 * v + 2) ** 3 / 2)  # noqa: E731
                z, pan = ez((tt - 4.5) / 1.3), ez((tt - 5.7) / 2.4)
                if z > 0:
                    if "hi" not in papers:
                        papers["hi"] = Image.open(good_png).convert("RGB")
                    hi = papers["hi"]
                    zoom = 1 + (W * 0.84 / good_.width - 1) * z
                    dw, dh = good_.width * zoom, good_.height * zoom
                    x0 = W * 0.72 * (1 - z) + W * 0.5 * z - dw / 2
                    top = py * (1 - z) + 40 * (W / 1920) * z - pan * (dh - H + 80 * (W / 1920))
                    ix0, iy0 = max(0, int(x0)), max(0, int(top))
                    ix1, iy1 = min(W, int(x0 + dw + 1)), min(H, int(top + dh + 1))
                    sc = hi.width / dw
                    frame = room.copy()
                    box = (max(0.0, (ix0 - x0) * sc), max(0.0, (iy0 - top) * sc), min(hi.width, (ix1 - x0) * sc), min(hi.height, (iy1 - top) * sc))
                    frame.paste(hi.resize((ix1 - ix0, iy1 - iy0), Image.BICUBIC, box=box, reducing_gap=2.0), (ix0, iy0))
            elif kind == "black":
                frame = Image.new("RGB", (W, H), (8, 11, 13))
            else:
                name = seg.get("img", "f2.jpg")
                if name not in stills:
                    stills[name] = Image.open(SHOTS / name).convert("RGB")
                src = stills[name]
                z0, z1 = seg.get("zoom", (1.1, 1.1))
                z = z0 + (z1 - z0) * p
                cw, ch = src.width / z, src.height / z
                ox, oy = (src.width - cw) / 2, (src.height - ch) * 0.62
                frame = src.resize((W, H), Image.BICUBIC, box=(ox, oy, ox + cw, oy + ch))
                arr = np.asarray(frame, dtype=np.float32)
                if kind == "dark":  # the hall sinks into dark from the walls in; one desk turns mint
                    cx, cy = W * 0.5, H * 0.7
                    r = np.sqrt(((xx - cx) / W) ** 2 + ((yy - cy) / H) ** 2)
                    e = p * p * (3 - 2 * p)
                    radius = 1.05 * (1 - e) + 0.075 * e
                    keep = np.clip((radius + 0.05 - r) / 0.1, 0, 1)
                    mint = np.clip(1 - r / 0.16, 0, 1) ** 1.5 * e
                    arr = arr * (0.05 + 0.95 * keep)[..., None]
                    arr += mint[..., None] * np.array([46.0, 150.0, 104.0])
                else:
                    arr = arr * seg.get("dim", 1.0)
                frame = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
            for k, a, b in ov:
                if a <= T_ <= b:
                    al = min(1.0, (T_ - a) / 0.22, (b - T_) / 0.16)
                    im = load_rgba(k)
                    frame.paste(im.convert("RGB"), (0, 0), im.getchannel("A").point(lambda v, al=al: int(v * al)))
            if seg["key"] == "reveal" and 1.2 <= T_:
                k = min(1.0, (T_ - 1.2) / (SNAP - 1.2 - 0.05))
                v = min(numbers, key=lambda q: abs(q - 273 * k ** 2.2))
                im = load_rgba(f"n{v}")
                frame.paste(im.convert("RGB"), (0, 0), im.getchannel("A"))
            if seg["key"] == "flood":
                k = max(1, min(25, 1 + int(24 * p ** 0.75)))
                im = load_rgba(f"flood{k}")
                frame.paste(im.convert("RGB"), (0, 0), im.getchannel("A"))
            if seg["key"] not in ("appa", "board"):  # film grain on the camera shots only; the app stays crisp
                g = grains[(i + len(seg["key"])) % len(grains)]
                frame = Image.fromarray(np.clip(np.asarray(frame, dtype=np.int16) + g, 0, 255).astype(np.uint8))
            if into is not None and seg["key"] != "stat" and i < 18:  # a short dissolve between sections; the snap stays a hard hit
                k_ = (i + 1) / 18
                frame = Image.blend(into, frame, k_ * k_ * (3 - 2 * k_))
            last_frame = frame
            ff.stdin.write(frame.tobytes())
    ff.stdin.close()
    ff.wait()
    if draft:
        shutil.move(str(target), str(ROOT / "docs" / ".film-draft.mp4"))
    else:
        shutil.move(str(target), str(OUT))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(OUT), "-vf", "scale=1280:720", "-c:v", "libx264", "-crf", "26", "-preset", "slow", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", str(WEB)], check=True)
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"total {total:.1f} s; " + ", ".join(f"{k}={v:.1f}" for k, v in t.items()))


if __name__ == "__main__":
    main()
