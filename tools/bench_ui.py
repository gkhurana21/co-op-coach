"""How fast does it feel? A real browser on real-sized postings: time from pressing a tab until its content is on screen.

    .venv/bin/python -m tools.bench_ui --jobs-from data/coopcoach.db [--out report.json]

Builds a scratch database with only the postings copied over and a fictional student. Budgets: page usable under 1 s after a start; a tab under 300 ms on a revisit.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TABS = ["autopilot", "path", "matches", "tracks", "track:role:software", "track:co:quant-firms", "tracker", "documents", "recruiters", "events", "settings"]
SETTLED = """() => { const m = document.querySelector('#main'); return !!m && m.innerText.trim().length > 20 && !m.querySelector('.spinner'); }"""


def scratch(source: str) -> str:
    d = tempfile.mkdtemp(prefix="coopcoach-benchui-")
    env = {**os.environ, "COOPCOACH_DATA": d}
    code = f"""
import os, sqlite3
from pathlib import Path
from coopcoach import db
db.init()
src = sqlite3.connect("file:{Path(source).resolve()}?mode=ro", uri=True)
cols = [r[1] for r in src.execute("PRAGMA table_info(jobs)")]
mine = set(db.COLUMNS["jobs"]) | {{"opened_at","changed_at","fp","req_id","requirements","form_checked"}}
use = [c for c in cols if c in mine]
rows = src.execute(f"SELECT {{','.join(use)}} FROM jobs WHERE active=1").fetchall()
now = db.now()
with db.tx() as c:
    c.executemany(f"INSERT OR REPLACE INTO jobs ({{','.join(use)}}) VALUES ({{','.join('?'*len(use))}})", rows)
    from coopcoach import store
    c.execute("INSERT INTO sync_runs (started_at, finished_at, kind) VALUES (?,?,'full')", (now, now))
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('flags_version', ?), ('skills_version', ?), ('requirements_version', ?), ('req_ids_v1', '1')", (store.FLAGS_VERSION, store.SKILLS_VERSION, store.REQUIREMENTS_VERSION))
    c.execute("INSERT INTO profiles (name, resume_text, profile_json, prefs_json, created_at, updated_at) VALUES (?,?,?,?,?,?)", ("Bench", "Python SQL React developer", db.dumps({{"skills":["Python","SQL","React"],"target_categories":["Software"],"grad_year":2028,"year_of_study":2}}), db.dumps({{"categories":["Software"],"locations":["Canada","Remote"]}}), now, now))
print(len(rows))
"""
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    print("postings:", out.stdout.strip() or out.stderr[-300:])
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs-from", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--port", type=int, default=8797)
    args = ap.parse_args()
    d = scratch(args.jobs_from)
    url = f"http://127.0.0.1:{args.port}"
    env = {**os.environ, "COOPCOACH_DATA": d, "GROQ_API_KEY": "", "ANTHROPIC_API_KEY": "", "COOPCOACH_CLAUDE_BIN": "/nonexistent", "COOPCOACH_CODEX_BIN": "/nonexistent",
           "COOPCOACH_READ_CACHE": "1"}
    t0 = time.time()
    srv = subprocess.Popen([sys.executable, "-m", "coopcoach", "serve", "--port", str(args.port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {}
    try:
        for _ in range(400):
            try:
                if httpx.get(url + "/api/state", timeout=5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        report["server_up_s"] = round(time.time() - t0, 2)
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            ctx = b.new_context(viewport={"width": 1280, "height": 900})
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(url) or r.request.resource_type in ("image", "font", "stylesheet") else r.abort())
            p = ctx.new_page()
            t = time.time()
            p.goto(url)
            p.wait_for_function(SETTLED, timeout=120000)
            report["first_page_s"] = round(time.time() - t, 2)
            report["tabs"] = {}
            for name in TABS:
                row = []
                for _ in range(3):
                    p.evaluate("document.querySelector('#main').innerHTML = ''; document.querySelectorAll('.milestone-bg').forEach((e) => e.remove()); document.querySelector('#modal-root').innerHTML = ''")
                    t = time.time()
                    p.evaluate("(v) => goView(v)", name)
                    p.wait_for_function(SETTLED, timeout=60000)
                    row.append(round((time.time() - t) * 1000))
                report["tabs"][name] = row
                print(f"{name:26} first {row[0]:6} ms   again {row[1]:6} ms  {row[2]:6} ms", flush=True)
            b.close()
    finally:
        srv.terminate()
        shutil.rmtree(d, ignore_errors=True)
    print(json.dumps({k: v for k, v in report.items() if k != "tabs"}))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
