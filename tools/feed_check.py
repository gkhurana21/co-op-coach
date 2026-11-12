"""Every track on the live job feed: does each number open a list of that size, do the pages answer, are the picks sensible?

    .venv/bin/python -m tools.feed_check            # copies the postings from your Supabase, read-only
    .venv/bin/python -m tools.feed_check feed.db    # or uses a copy made before

Only the jobs table is copied (no personal data), over a read-only connection, into a throwaway file. Three fictional students
then use the real pages on a scratch database. Nothing real is written.
"""
import collections
import os
import random
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

PULL = r'''
import json, os, sqlite3, sys, time
import psycopg
from coopcoach import supa
out = sys.argv[1]
info = supa.conninfo()   # the password stays inside this process; it's never printed
t = time.time()
with psycopg.connect(info, autocommit=True, row_factory=psycopg.rows.dict_row) as c:
    c.execute("SET default_transaction_read_only = on")
    cols = [r["column_name"] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='jobs' ORDER BY ordinal_position")]
    schema = c.execute("SELECT current_schema() AS s").fetchone()["s"]
    rows = c.execute(f"SELECT {','.join(cols)} FROM jobs WHERE active=1").fetchall()
    print("schema", schema, "columns", len(cols), "active postings", len(rows), f"{time.time()-t:.1f}s", flush=True)
    desc = sum(1 for r in rows if r.get("description"))
    print("with full text:", desc)
db = sqlite3.connect(out)
db.execute(f"CREATE TABLE jobs ({','.join(cols)})")
db.executemany(f"INSERT INTO jobs VALUES ({','.join('?'*len(cols))})", [[r[c] for c in cols] for r in rows])
db.commit(); db.close()
print("saved", os.path.getsize(out) // 1_000_000, "MB")
'''
if len(sys.argv) > 1:
    FEED = sys.argv[1]
else:
    FEED = os.path.join(tempfile.mkdtemp(prefix="coopcoach-feedcopy-"), "feed.db")
    subprocess.run([sys.executable, "-c", PULL, FEED], check=True)
d = tempfile.mkdtemp(prefix="coopcoach-feed-")
os.environ.update(COOPCOACH_DATA=d, GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1", COOPCOACH_READ_CACHE="1")
from fastapi.testclient import TestClient

from coopcoach import db, jobdetail, market, server, store, track_discovery

market.read_more = lambda *a, **k: 0
jobdetail.ensure_description = lambda *a, **k: ""
db.init(); server._sync_in_background = lambda *a, **k: None; server._seed()
src = sqlite3.connect(FEED); cols = [r[1] for r in src.execute("PRAGMA table_info(jobs)")]
mine = set(db.COLUMNS["jobs"]) | {"opened_at", "changed_at", "fp", "req_id", "requirements", "form_checked"}
use = [c for c in cols if c in mine]
rows = src.execute(f"SELECT {','.join(use)} FROM jobs").fetchall()
now = db.now()
students = {
 "Waterloo SWE": ({"skills":["Python","Java","React","SQL","Git","AWS"],"target_categories":["Software"],"grad_year":2028,"year_of_study":2,"university":"University of Waterloo","degree_level":"Bachelor's"}, {"categories":["Software"],"locations":["Canada","Remote"],"citizenship":"Canadian"}, "Python Java React SQL developer. Built REST APIs on AWS with Git."),
 "UBC data": ({"skills":["Python","SQL","Pandas","TensorFlow","Machine Learning","R"],"target_categories":["AI/ML/Data"],"grad_year":2027,"year_of_study":3,"university":"UBC","degree_level":"Bachelor's"}, {"categories":["AI/ML/Data"],"locations":["Canada","US"],"citizenship":"Canadian"}, "Python SQL Pandas TensorFlow machine learning projects."),
 "F-1 hardware": ({"skills":["C++","Verilog","Python","MATLAB"],"target_categories":["Hardware"],"grad_year":2027,"year_of_study":3,"university":"Purdue","degree_level":"Master's"}, {"categories":["Hardware"],"locations":["US"],"citizenship":"F1"}, "C++ Verilog MATLAB embedded firmware."),
}
with db.tx() as c:
    c.executemany(f"INSERT OR REPLACE INTO jobs ({','.join(use)}) VALUES ({','.join('?'*len(use))})", rows)
    pids = {n: c.execute("INSERT INTO profiles (name, resume_text, profile_json, prefs_json, created_at, updated_at) VALUES (?,?,?,?,?,?)", (n, res, db.dumps(pf), db.dumps(pr), now, now)).lastrowid for n, (pf, pr, res) in students.items()}
store.invalidate_cache()
cl = TestClient(server.app, base_url="http://localhost")
problems, slow, seen_tracks = [], [], 0
def get(u):
    t = time.time(); r = cl.get(u); ms = (time.time() - t) * 1000
    if r.status_code != 200: problems.append(f"{r.status_code} {u[:90]}")
    if ms > 1000: slow.append((round(ms), u.split("?")[0][:60]))
    return r.json() if r.status_code == 200 else {}
pays = []
t0 = time.time()
for name, pid in pids.items():
    b = get(f"/api/tracks?profile_id={pid}")
    ids = {c["id"] for k, v in b.items() if isinstance(v, list) for c in v if isinstance(c, dict) and c.get("id")}
    for tid in sorted(ids):
        seen_tracks += 1
        h = get(f"/api/tracks/{tid}?profile_id={pid}")
        if not h: continue
        base = f"/api/jobs?profile_id={pid}&track={tid}&limit=5"
        a = get(base)["total"] if get(base) else None
        o = get(base + "&show_ineligible=true").get("total")
        n7 = get(base + "&since_days=7").get("total")
        if a != h["open_to_you"]: problems.append(f"{name} {tid}: says {h['open_to_you']} open to you, list has {a}")
        if o != h["open"]: problems.append(f"{name} {tid}: says {h['open']} open, list has {o}")
        if n7 != h["new_week"]: problems.append(f"{name} {tid}: says {h['new_week']} new this week, list has {n7}")
        c = get(f"/api/tracks/{tid}/companies?profile_id={pid}")
        m = get(f"/api/tracks/{tid}/market?profile_id={pid}")
        if not m: continue
        mk = f"/api/jobs?profile_id={pid}&track={tid}&population=market&limit=5"
        said = m["glance"]["open_to_you"]; lst = get(mk).get("total")
        if said != lst: problems.append(f"{name} {tid}: market says {said} open to you, list has {lst}")
        random.seed(tid)
        for s in random.sample(m.get("skills") or [], min(4, len(m.get("skills") or []))):
            lst = get(mk + "&facet=" + "skill:" + s["skill"].replace("+", "%2B").replace("#", "%23")).get("total")
            if lst != s["count"]: problems.append(f"{name} {tid}: {s['skill']} says {s['count']}, list has {lst}")
        pay = m["glance"].get("pay") or {}
        if pay.get("n"):
            lst = get(mk + "&facet=pay").get("total")
            if lst != pay.get("stated"): problems.append(f"{name} {tid}: pay says {pay.get('stated')} postings, list has {lst}")
            pays.append((pay.get("median"), pay.get("currency"), pay["n"], tid))
            if not (8 <= (pay.get("median") or 0) <= 150): problems.append(f"{name} {tid}: typical pay {pay.get('median')} {pay.get('currency')}/h from {pay['n']}")
        for t in (m.get("where") or {}).get("terms", [])[:2]:
            lst = get(mk + "&facet=term:" + t["term"].replace(" ", "%20")).get("total")
            if lst != t["n"]: problems.append(f"{name} {tid}: term {t['term']} says {t['n']}, list has {lst}")
print(f"checked {seen_tracks} track pages for {len(pids)} students in {time.time()-t0:.0f}s on {len(rows)} postings")
print("problems:", len(problems)); [print("  ", p) for p in problems[:30]]
print("slow over 1s:", collections.Counter(u for _, u in slow).most_common(8), "worst", max([s[0] for s in slow] or [0]))
pid = pids["Waterloo SWE"]
jobs = store.scored_jobs(pid)
print("categories:", collections.Counter(j["category"] for j in jobs).most_common())
b = get(f"/api/tracks?profile_id={pid}")
print("browse keys:", {k: len(v) for k, v in b.items() if isinstance(v, list)}, "card:", list(b["roles"][0].keys()))
for k, v in b.items():
    if isinstance(v, list) and v and isinstance(v[0], dict) and v[0].get("name"):
        print(" ", k, [(c["name"][:22], c.get("open"), c.get("open_to_you")) for c in v][:16])
sw = store.list_jobs(pid, track="role:software", limit=5000)["jobs"]
print("software track, odd titles:", [(j["title"][:52], j["category"]) for j in sw if re.search(r"manufactur|civil|chemical|mechanical|process eng|construct|nurs|legal|account", j["title"], re.I)][:14], "of", len(sw))
random.seed(9); print("software sample:", [j["title"][:40] for j in random.sample(sw, 24)])
r = track_discovery.run(now)
print("discovery ran:", {k: (len(v) if hasattr(v, "__len__") else v) for k, v in r.items()} if isinstance(r, dict) else r)
fnd = db.rows("SELECT * FROM tracks_discovered ORDER BY id LIMIT 40")
print("found:", [(x.get("name", "")[:28], x.get("trend") or x.get("status")) for x in fnd][:30])
if pays:
    print("typical pay: median of medians", statistics.median(p[0] for p in pays), "range", min(p[0] for p in pays), max(p[0] for p in pays), "| samples", sorted(set((round(p[0]), p[1], p[3][:22]) for p in pays))[:6])
for name, pid in pids.items():
    p = __import__("coopcoach.autopilot").autopilot.preview(pid, 8)
    print(f"\npicks for {name}:"); [print(f"  {x['score']:3} {x['company'][:22]:22} {x['title'][:44]:44} {x['where'][:26]:26} {'; '.join(x['why'][1:3])[:34]}") for x in p["picks"][:8]]
shutil.rmtree(d, ignore_errors=True)
