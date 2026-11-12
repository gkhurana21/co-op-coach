"""The slow paths stay fast without going stale: Tracks after a sync, the minute-by-minute state, PDFs. Scratch data only.

Run: .venv/bin/python -m tests.test_speed_paths
"""

import os
import tempfile
import time

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-speed-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import db, render, server, store, track_discovery, tracks  # noqa: E402
from coopcoach.textutil import company_key  # noqa: E402


def add(c, jid, company, title):
    now = db.now()
    c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, locations, terms, tier, category, active,"
              " first_seen, last_seen, changed_at) VALUES (?,?,?,?,?,?,'[\"Toronto, ON\"]','[\"Summer 2027\"]','startup','Software',1,?,?,?)",
              (jid, "ashby:x", company, company_key(company), title, f"https://x/{jid}", now, now, now))


def main():
    db.init()
    now = db.now()
    checks: dict[str, bool] = {}
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Robin', ?, ?)", (now, now)).lastrowid
        add(c, "a", "Acme", "Software Engineer Intern")
    store.invalidate_cache()

    # Tracks: a fast rebuild just happens; a slow one serves the last answer and refreshes behind it
    first = tracks.index(pid)
    role = "role:software"
    checks["the first answer is worked out"] = "a" in first[role]
    with db.tx() as c:
        add(c, "b", "Bravo", "Software Developer Intern")
    store.invalidate_cache()
    checks["on a small feed a sync's changes show at once"] = "b" in tracks.index(pid)[role]
    tracks._index_secs[pid] = 6.0
    with db.tx() as c:
        add(c, "c", "Charlie", "Software Engineer Co-op")
    store.invalidate_cache()
    real, gate, started = tracks._build_index, __import__("threading").Event(), []
    def slow(p, stamp):
        started.append(p)
        gate.wait(5)
        return real(p, stamp)
    tracks._build_index = slow
    t0 = time.time()
    stale = tracks.index(pid)
    took = time.time() - t0
    again = tracks.index(pid)
    checks["on a big feed the page doesn't wait for the rebuild"] = took < 1 and "c" not in stale[role] and "b" in stale[role]
    checks["...and asking twice starts one rebuild, not two"] = again is stale and len(started) == 1
    gate.set()
    tracks._build_index = real
    for _ in range(200):
        if pid not in tracks._index_building:
            break
        time.sleep(0.02)
    checks["...then the new postings are in"] = "c" in tracks.index(pid)[role]

    tracks._index_secs[pid] = 6.0
    made = tracks._stamp(pid)
    old_stamp = tracks._stamp
    tracks._stamp = lambda p: (*made[:3], made[3] + 1)  # someone just made a track
    fresh = tracks.index(pid)
    tracks._stamp = old_stamp
    checks["a track someone just made is worked out first, not served stale"] = fresh is not stale and tracks._index_cache[pid][0][3] == made[3] + 1
    tracks._index_cache.pop(pid)

    # the one-look phrase matching agrees with asking each found track in turn
    job = {"title": "Forward Deployed Engineer Intern", "company": "Acme", "locations": ["Toronto"], "flags": [], "category": "Software", "tier": "startup", "company_key": "acme", "id": "z"}
    autos = [{"id": "auto:fde", "kind": "auto", "phrases": ["forward deployed"], "name": "F"}, {"id": "auto:gtm", "kind": "auto", "phrases": ["go to market"], "name": "G"}]
    lens = tracks._Lenses(pid, autos)
    checks["found tracks match a posting by phrase, as before"] = lens.tracks_of(job) == [t["id"] for t in autos if track_discovery.matcher(t["phrases"])(job)] == ["auto:fde"]

    # the terms asked for every minute are counted once per change
    store.collapsed_jobs()  # (loaded, as they are in the running app)
    calls = []
    real_jobs = store.collapsed_jobs
    store.collapsed_jobs = lambda: (calls.append(1), real_jobs())[1]
    a, b = server._term_options(), server._term_options()
    store.invalidate_cache()
    real_jobs()
    server._term_options()
    store.collapsed_jobs = real_jobs
    checks["terms are counted once, and again after a sync"] = a == b and len(calls) == 2

    # PDFs: the same page prints once
    prints = []
    def fake(doc, info=None):
        prints.append(doc)
        if info is not None:
            info.update(level=2, pages=1)
        return b"%PDF" + doc.encode()
    render._print_pdf = fake
    i1, i2 = {}, {}
    d1, d2 = render.pdf("<p>one</p>", i1), render.pdf("<p>one</p>", i2)
    checks["the same page prints once, with its one-page result"] = d1 == d2 and len(prints) == 1 and i2 == {"level": 2, "pages": 1}
    render.pdf("<p>two</p>")
    checks["a changed page prints again"] = len(prints) == 2
    for n in range(60):
        render.pdf(f"<p>{n}</p>")
    checks["only the recent ones are kept"] = len(render._pdf_cache) == 40

    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print(f"\n{sum(bool(v) for v in checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
