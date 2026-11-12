"""Render the paper-cutout film (tools/collage/collage.html) to docs/film-273-collage.mp4: every frame drawn by the page's own
renderAt(t), narration from tools.voice, and a score and paper sound effects synthesized here.

    PYTHONPATH=<Pillow and numpy> .venv/bin/python -m tools.render_collage [--silent]
"""

import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "handoff" / "film-shots"
WORK = ROOT / "docs" / ".collage"
OUT = ROOT / "docs" / "film-273-collage.mp4"
FPS, SR = 30, 44100
LINES = [  # (when, words)
    (0.8, "Meet Alex. First year. First application."),
    (5.4, "He's one of two hundred and seventy-three."),
    (10.9, "And to a recruiter, they all look the same."),
    (17.3, "So in January, Alex stopped blending in."),
    (24.3, "One application that's actually his."),
    (27.5, "Same experience. Finally said properly."),
    (30.8, "Every morning, it tells him what matters."),
    (33.4, "It ranks every internship against his resume."),
    (36.1, "It drafts answers only from what he's really done."),
    (38.8, "It fills the application, then stops, so Alex presses submit."),
    (41.9, "Then it helps him prep. The NeetCode one-fifty, tracked."),
    (44.6, "And when he's stuck, he just asks Jarvis."),
    (48.6, "Four days later: an interview."),
    (54.4, "Don't be one of two hundred and seventy-three."),
    (57.7, "Co-op Coach. You press submit."),
]


def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)


def assets() -> None:
    a = WORK / "assets"
    a.mkdir(parents=True)
    for src, name in (("alex.jpg", "alex.jpg"), ("f2.jpg", "hall.jpg"), ("f4.jpg", "goose.jpg"), ("f7.jpg", "calm.jpg")):
        shutil.copy(SHOTS / src, a / name)
    ff("-i", str(SHOTS / "alex.jpg"), "-vf", "crop=380:330:500:80", str(a / "face.jpg"))
    ff("-i", str(SHOTS / "f2.jpg"), "-vf", "crop=270:290:553:378", str(a / "hero.jpg"))
    want = {"today.jpg": ("a", "matters today", 1.6), "matches.jpg": ("a", "ranked", 1.7), "kit.jpg": ("a", "really done", 1.4),
            "form.jpg": ("a", "stops here", 0.9), "path.jpg": ("b", "Log every try", 2.2), "jarvis.jpg": ("b", "Ask Jarvis", 2.4)}
    marks = {k: json.loads((SHOTS / f"app_{k}.json").read_text()) for k in ("a", "b")}
    for name, (part, needle, after) in want.items():
        t = next(m["t"] for m in marks[part] if needle in m["text"]) + after
        ff("-ss", str(t), "-i", str(SHOTS / f"app_{part}.mp4"), "-frames:v", "1", "-vf", "scale=1200:-1", "-q:v", "2", str(a / name))
    from . import cut_film
    cut_film.WORK = WORK
    shutil.copy(cut_film.render_good(), a / "good.png")


# ---------------- sound
rng = np.random.default_rng(9)


def lowpass(x, cut):
    a = 1 - np.exp(-2 * np.pi * cut / SR)
    y = np.empty_like(x)
    v = 0.0
    for i in range(len(x)):
        v += a * (x[i] - v)
        y[i] = v
    return y


def put(buf, t, s, g=1.0, pan=0.0):
    i = int(t * SR)
    n = min(len(s), buf.shape[0] - i)
    if n > 0 and i >= 0:
        buf[i:i + n, 0] += s[:n] * g * (1 - max(pan, 0))
        buf[i:i + n, 1] += s[:n] * g * (1 + min(pan, 0))


def env(n, a, d):
    x = np.arange(n) / SR
    return np.minimum(1, x / a) * np.exp(-x / d)


def pluck(f, dur=1.4):
    n = int(dur * SR)
    x = np.arange(n) / SR
    return (np.sin(2 * np.pi * f * x) + .35 * np.sin(2 * np.pi * 2 * f * x) * np.exp(-x * 6) + .12 * np.sin(2 * np.pi * 3.01 * f * x) * np.exp(-x * 9)) * env(n, .004, .45)


def noise(dur, cut, a=.005, d=.08):
    n = int(dur * SR)
    return lowpass(rng.normal(0, 1, n), cut) * env(n, a, d)


def score(total: float) -> np.ndarray:
    buf = np.zeros((int(total * SR) + SR, 2))
    bpm = 92
    b = 60 / bpm
    C = [261.63, 293.66, 329.63, 392.0, 440.0]
    prog = [(130.81, [0, 2, 4]), (110.0, [4, 2, 0]), (87.31, [0, 1, 3]), (98.0, [1, 3, 4])]
    t = 0.0
    k = 0
    while t < total - 1:
        night = 16.8 <= t < 29.8
        full = 30 <= t < 48.5 or t >= 54
        root, idx = prog[(k // 8) % 4]
        if k % 8 == 0:  # a soft pad for each bar
            n = int(b * 8 * SR)
            x = np.arange(n) / SR
            pad = sum(np.sin(2 * np.pi * f * x + np.sin(2 * np.pi * .3 * x)) for f in (root, root * 1.5, root * 2 * (1.189 if night else 1.26)))
            put(buf, t, pad * np.minimum(1, x / .6) * np.minimum(1, (b * 8 - x) / .6) * (.05 if night else .04))
        if not night or k % 2 == 0:
            f = C[idx[k % 3]] * (1 if k % 4 else 2) * (0.84 if night else 1)
            put(buf, t, pluck(f), .13 if full else .1, .3 * np.sin(k))
        if full:
            put(buf, t + b / 2, noise(.06, 6000, .002, .03), .05, .4)
            if k % 2 == 0:
                put(buf, t, pluck(root / 2, .9), .12)
        t += b / 2
        k += 1
    return buf


def sfx(buf):
    def snip(t):
        put(buf, t, noise(.03, 7000, .001, .012), .5)
        put(buf, t + .05, noise(.04, 5000, .001, .02), .45)

    def slide(t, g=.35):
        n = int(.35 * SR)
        put(buf, t, lowpass(rng.normal(0, 1, n), 2500) * np.sin(np.linspace(0, np.pi, n)) * .6, g)

    def tape(t):
        put(buf, t, noise(.18, 3500, .004, .06), .45)

    def pop(t, g=.25):
        n = int(.12 * SR)
        x = np.arange(n) / SR
        put(buf, t, np.sin(2 * np.pi * (600 - 2200 * x) * x) * env(n, .002, .04), g)

    def thunk(t):
        n = int(.5 * SR)
        x = np.arange(n) / SR
        put(buf, t, np.sin(2 * np.pi * (70 + 80 * np.exp(-x * 25)) * x) * np.exp(-x * 9), .9)
        put(buf, t, noise(.2, 1500, .001, .05), .5)

    slide(.2)
    tape(.8)
    for i in range(70):
        pop(5.35 + 3.6 * (i / 70) ** 1.3, .07)
    for i in range(10):
        put(buf, 10.7 + i * .12, noise(.07, 3000, .002, .03), .3)
    for i in range(16):
        slide(11.6 + i * .22, .12)
    slide(14.1, .4)
    for i in range(18):
        snip(19.7 + i * .17)
    slide(23.0, .45)
    slide(25.4)
    for i in range(9):  # the bad resume crumpling
        put(buf, 27.15 + i * .05, noise(.05, 6000, .001, .02), .35)
    slide(27.9)
    tape(28.1)
    for at in (30.6, 33.2, 35.9, 38.6, 41.6, 44.2):
        slide(at + .1, .3)
        tape(at + .6)
    slide(48.8, .45)
    thunk(50.15)
    tape(51.3)
    slide(54.2, .4)
    for i in range(10):
        pop(56.9 + i * .07, .18)
    put(buf, 58.4, pluck(523.25, 2.5), .25)
    put(buf, 58.55, pluck(783.99, 2.5), .2)


def main() -> None:
    silent = "--silent" in sys.argv
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    assets()
    shutil.copy(ROOT / "tools" / "collage" / "collage.html", WORK / "index.html")
    frames = WORK / "f"
    frames.mkdir()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": 1920, "height": 1080})
        p.goto((WORK / "index.html").as_uri())
        p.evaluate("() => window.ready")
        p.wait_for_timeout(800)
        total = p.evaluate("window.DURATION")
        n = int(total * FPS)
        for i in range(n):
            p.evaluate(f"window.renderAt({i / FPS})")
            p.screenshot(path=str(frames / f"{i:05d}.jpg"), type="jpeg", quality=93)
            if i % 300 == 0:
                print(f"  frame {i}/{n}", flush=True)
        b.close()
    audio = score(total)
    sfx(audio)
    if not silent:
        from . import voice
        vo = np.zeros_like(audio)
        end = 0.0
        for at, words in LINES:
            w = voice.say(words)
            with wave.open(str(w)) as f:
                v = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float64) / 32768
            v = np.interp(np.arange(0, len(v), 24000 / SR), np.arange(len(v)), v)  # 24 kHz to 44.1 kHz
            at = max(at, end + 0.2)
            put(vo, at, v, 1.0)
            end = at + len(v) / SR
        duck = np.convolve(np.abs(vo[:, 0]), np.ones(4410) / 4410, mode="same")
        audio *= (1 - 0.6 * np.clip(duck * 8, 0, 1))[:, None]
        audio += vo * 1.35
    audio = np.tanh(audio * 1.1)
    audio /= max(1e-6, np.abs(audio).max() / 0.9)
    wav = WORK / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio[: int(total * SR)] * 32767).astype(np.int16).tobytes())
    ff("-framerate", str(FPS), "-i", str(frames / "%05d.jpg"), "-i", str(wav), "-vf", "fade=t=in:st=0:d=0.5,fade=t=out:st=" + f"{total - 0.8:.2f}" + ":d=0.8,format=yuv420p",
       "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(OUT))
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"{OUT}  {total:.1f} s")


if __name__ == "__main__":
    main()
