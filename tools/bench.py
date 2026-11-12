"""How fast is it? Times every page's data on real-sized postings, cold and warm, with response sizes.

    .venv/bin/python -m tools.bench --jobs-from data/coopcoach.db [--out bench.json]

Builds a scratch database in a temporary folder with only the postings table copied over and one fictional student, so no
personal data is involved and nothing real is written. Budgets: first state 1 s, each page's data 200 ms warm.
"""

import argparse
import json
import os
import shutil
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

RTT_MS = 190


def scratch(source: str) -> str:
    d = tempfile.mkdtemp(prefix="coopcoach-bench-")
    os.environ.update(COOPCOACH_DATA=d, GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent",
                      COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1", COOPCOACH_READ_CACHE="1")
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs-from", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    d = scratch(args.jobs_from)
    t0 = time.time()
    from fastapi.testclient import TestClient

    from coopcoach import db, server, store
    from coopcoach.server import app
    imported = time.time() - t0
    db.init()
    server._sync_in_background = lambda *a, **k: None
    server._seed()  # the boards every install has
    src = sqlite3.connect(f"file:{Path(args.jobs_from).resolve()}?mode=ro", uri=True)
    cols = [r[1] for r in src.execute("PRAGMA table_info(jobs)")]
    mine = set(db.COLUMNS["jobs"]) | {"opened_at", "changed_at", "fp", "req_id", "requirements", "form_checked"}
    use = [c for c in cols if c in mine]
    rows = src.execute(f"SELECT {','.join(use)} FROM jobs WHERE active=1").fetchall()
    with db.tx() as c:
        c.executemany(f"INSERT OR REPLACE INTO jobs ({','.join(use)}) VALUES ({','.join('?' * len(use))})", rows)
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES (?,?,?,?,?)",
                        ("Bench", db.dumps({"skills": ["Python", "SQL", "React"], "target_categories": ["Software", "Quant"],
                                            "grad_year": 2028, "year_of_study": 2}),
                         db.dumps({"categories": ["Software", "Quant"], "locations": ["Canada", "Remote"]}), now, now)).lastrowid
    print(f"{len(rows)} postings, import {imported:.2f}s")
    store.invalidate_cache()
    timings = {}
    t = time.time(); store.collapsed_jobs(); timings["load postings"] = time.time() - t
    t = time.time(); store.scored_jobs(pid); timings["score postings"] = time.time() - t
    t = time.time(); store.scored_jobs(pid); timings["score again"] = time.time() - t
    from coopcoach import tracks
    t = time.time(); tracks.index(pid); timings["track index"] = time.time() - t
    cl = TestClient(app, base_url="http://localhost")
    pages = {"state": "/api/state", "jobs": f"/api/jobs?profile_id={pid}&limit=50", "jobs search": f"/api/jobs?profile_id={pid}&q=python&limit=50",
             "track list": f"/api/tracks?profile_id={pid}", "track bar": f"/api/tracks/bar?profile_id={pid}",
             "track head": f"/api/tracks/role:software?profile_id={pid}", "track jobs": f"/api/jobs?profile_id={pid}&track=role:software&limit=50",
             "track companies": f"/api/tracks/role:software/companies?profile_id={pid}", "track market": f"/api/tracks/role:software/market?profile_id={pid}",
             "niche companies": f"/api/tracks/co:quant-firms/companies?profile_id={pid}", "startups": f"/api/tracks/co:startups/companies?profile_id={pid}",
             "path": f"/api/path?profile_id={pid}", "ladder": f"/api/path/ladder?profile_id={pid}", "events": f"/api/events?profile_id={pid}",
             "applications": f"/api/applications?profile_id={pid}", "people": f"/api/recruiters?profile_id={pid}", "autopilot": f"/api/autopilot?profile_id={pid}",
             "activity": f"/api/activity?profile_id={pid}", "favourites": f"/api/favourites?profile_id={pid}"}
    table = {}
    # on Supabase each query is a round trip over the internet (about 190 ms measured): queries are what you feel
    for name, url in pages.items():
        times = []
        size = status = queries = 0
        for _ in range(4):
            before = db.round_trips[0]
            t = time.time()
            r = cl.get(url)
            times.append((time.time() - t) * 1000)
            size, status, queries = len(r.content), r.status_code, db.round_trips[0] - before
        table[name] = {"first_ms": round(times[0]), "warm_ms": round(statistics.median(times[1:])), "kb": round(size / 1024, 1),
                       "status": status, "queries": queries, "on_supabase_ms": round(statistics.median(times[1:]) + queries * RTT_MS)}
    for k, v in timings.items():
        print(f"{k:16} {v * 1000:8.0f} ms")
    print(f"{'page':18}{'first':>7}{'warm':>7}{'queries':>9}{'supabase':>10}{'KB':>8} status")
    for k, v in table.items():
        flag = "  <-- over budget" if v["on_supabase_ms"] > 200 else ""
        print(f"{k:18}{v['first_ms']:7}{v['warm_ms']:7}{v['queries']:9}{v['on_supabase_ms']:10}{v['kb']:8} {v['status']}{flag}")
    static = Path(server.PKG) / "static"
    print("static KB:", {f.name: round(f.stat().st_size / 1024) for f in sorted(static.iterdir()) if f.suffix in (".js", ".css")})
    if args.out:
        Path(args.out).write_text(json.dumps({"timings": timings, "pages": table}, indent=1))
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
