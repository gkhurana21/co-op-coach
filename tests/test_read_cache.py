"""The speed layer can't be allowed to show old data. On Supabase each query is an internet round trip, so reads are kept in
memory: a write must end them at once (here, or in another process on this computer), a failed internet must fall back to
what was read last, tickets must never be kept, and the local copy of the postings must follow what changes. And the budget:
once warm, no page goes to the database at all.

Run: .venv/bin/python -m tests.test_read_cache
"""

import gzip
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-read-cache-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1",
                  COOPCOACH_READ_CACHE="1")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import db, market, path, server, store  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def trips(fn) -> tuple[int, object]:
    before = db.round_trips[0]
    out = fn()
    return db.round_trips[0] - before, out


def new_profile(name: str = "Sam") -> int:
    now = db.now()
    with db.tx() as c:
        return c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES (?,?,?,?,?)",
                         (name, db.dumps({"skills": ["Python", "SQL"], "target_categories": ["Software"]}),
                          db.dumps({"categories": ["Software"]}), now, now)).lastrowid


def reads_and_writes():
    pid = new_profile()
    q = "SELECT name FROM profiles WHERE id=?"
    n, r = trips(lambda: db.row(q, (pid,)))
    check("the first read asks the database", n == 1 and r["name"] == "Sam")
    n, r = trips(lambda: db.row(q, (pid,)))
    check("the same read again doesn't", n == 0 and r["name"] == "Sam")
    r["name"] = "changed by the caller"
    check("what a caller does to a row doesn't reach the next caller", db.row(q, (pid,))["name"] == "Sam")

    db.rows("SELECT id FROM jobs LIMIT 1")
    with db.tx() as c:
        c.execute("UPDATE profiles SET name='Sasha' WHERE id=?", (pid,))
    n, r = trips(lambda: db.row(q, (pid,)))
    check("a write ends what was read from that table, at once", n == 1 and r["name"] == "Sasha")
    check("...and only that table", trips(lambda: db.rows("SELECT id FROM jobs LIMIT 1"))[0] == 0)

    try:
        with db.tx() as c:
            c.execute("UPDATE profiles SET name='Oops' WHERE id=?", (pid,))
            raise RuntimeError("something failed halfway")
    except RuntimeError:
        pass
    check("a write that rolled back leaves the old value", db.row(q, (pid,))["name"] == "Sasha")

    joined = "SELECT p.name, a.status FROM profiles p LEFT JOIN applications a ON a.profile_id=p.id WHERE p.id=?"
    db.rows(joined, (pid,))
    with db.tx() as c:
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at) VALUES (?,?,?,?)", (pid, "j1", "applied", db.now()))
    check("a write to any table in a join ends it", db.rows(joined, (pid,))[0]["status"] == "applied")

    sub = "SELECT COUNT(*) AS n FROM (SELECT 1 FROM applications WHERE profile_id=?) x"
    check("a read through a subquery is seen too", db.row(sub, (pid,))["n"] == 1)
    with db.tx() as c:
        c.execute("DELETE FROM applications WHERE profile_id=?", (pid,))
    check("...and a delete ends it", db.row(sub, (pid,))["n"] == 0)

    db.meta_set("cache_test", "one")
    db.meta_get("cache_test")
    db.meta_set("cache_test", "two")
    check("settings kept in meta are never old", db.meta_get("cache_test") == "two")

    v = db.table_version("profiles", "jobs")
    check("a table's version holds while nothing writes", db.table_version("profiles", "jobs") == v)
    with db.tx() as c:
        c.execute("UPDATE profiles SET updated_at=? WHERE id=?", (db.now(), pid))
    check("...and moves when it's written", db.table_version("profiles", "jobs") != v)

    with db.tx() as c:
        c.execute("INSERT INTO agent_tickets (token_hash, profile_id, created_at, expires_at, label) VALUES (?,?,?,?,?)", ("t1", pid, db.now(), db.now() + 60, "x"))
    tq = "SELECT profile_id FROM agent_tickets WHERE token_hash=?"
    db.row(tq, ("t1",))
    check("tickets are always asked for: they're how access ends", trips(lambda: db.row(tq, ("t1",)))[0] == 1)


def another_process():
    pid = new_profile("Before")
    q = "SELECT name FROM profiles WHERE id=?"
    db.row(q, (pid,))
    code = ("from coopcoach import db\n"
            f"with db.tx() as c:\n    c.execute(\"UPDATE profiles SET name='After' WHERE id=?\", ({pid},))\n")
    time.sleep(0.02)
    subprocess.run([sys.executable, "-c", code], check=True, env=os.environ, cwd=Path(__file__).resolve().parent.parent, timeout=60)
    check("a write by another process on this computer (the plugin, a sync) is noticed at once", db.row(q, (pid,))["name"] == "After")


def time_and_the_internet():
    q = "SELECT COUNT(*) AS n FROM profiles"
    db.row(q)
    key = next(k for k in db._reads if k[0] == q)
    check("still fresh within its time", trips(lambda: db.row(q))[0] == 0)
    db._reads[key]["at"] -= db._READ_TTL + 1
    check("after that it's asked for again (someone may have written from another computer)", trips(lambda: db.row(q))[0] == 1)

    class OperationalError(Exception):
        pass

    def offline(*a, **k):
        raise OperationalError("no route to host")

    n = db.row(q)["n"]
    db._reads[key]["at"] -= db._READ_TTL + 1
    real, db._raw = db._raw, offline
    try:
        check("the internet dropped: what was read last answers", db.row(q)["n"] == n)
        try:
            db.row("SELECT COUNT(*) AS n FROM profiles WHERE id=-1")
            check("...a read never made before can't be answered: it says so", False)
        except OperationalError:
            check("...a read never made before can't be answered: it says so", True)
        check("refreshing while offline leaves what's there", db.refresh_reads() == 0 and key in db._reads)
    finally:
        db._raw = real

    def other_fail(*a, **k):
        raise ValueError("a bug, not the internet")
    db._reads[key]["at"] -= db._READ_TTL + 1
    db._raw = other_fail
    try:
        db.row(q)
        check("a real error isn't hidden behind old data", False)
    except ValueError:
        check("a real error isn't hidden behind old data", True)
    finally:
        db._raw = real

    # a write landing while a read is on its way must not leave that read's old answer behind
    pid = new_profile("Racer")
    rq = "SELECT name FROM profiles WHERE id=?"

    def slow(sql, params, one):
        out = real(sql, params, one)
        db._raw = real
        with db.tx() as c:
            c.execute("UPDATE profiles SET name='Won' WHERE id=?", (pid,))
        return out
    db._raw = slow
    check("a read that raced a write answers what it read...", db.row(rq, (pid,))["name"] == "Racer")
    check("...but isn't kept", db.row(rq, (pid,))["name"] == "Won")

    # what's in use is kept fresh behind the page
    db.forget_reads()
    db.row(q)
    key = next(k for k in db._reads if k[0] == q)
    db._reads[key]["at"] -= db._READ_TTL * 0.8
    check("what's in use and getting old is refreshed in the background", db.refresh_reads() == 1 and db._reads[key]["at"] > time.time() - 5)
    db._reads[key]["used"] -= 3600
    db.refresh_reads()
    check("what nobody's used for a while is let go", key not in db._reads)

    # limits
    big = db._READ_MAX_ROWS
    db._READ_MAX_ROWS = 0
    try:
        db.rows("SELECT id FROM profiles")
        check("a huge answer isn't kept in memory", trips(lambda: db.rows("SELECT id FROM profiles"))[0] == 1)
    finally:
        db._READ_MAX_ROWS = big
    most = db._READ_MAX_ENTRIES
    db._READ_MAX_ENTRIES = 8
    try:
        for i in range(40):
            db.rows("SELECT id FROM profiles WHERE id=?", (i,))
        check("the cache can't grow without end", len(db._reads) <= 8)
    finally:
        db._READ_MAX_ENTRIES = most


def first_visits():
    pid = new_profile("Quick")
    q, p = "SELECT name FROM profiles WHERE id=?", (pid,)

    # the same read at the same moment goes out once
    real, gate, calls = db._raw, threading.Event(), []

    def slow(sql, params, one):
        calls.append(sql)
        gate.wait(5)
        return real(sql, params, one)
    db.forget_reads()
    db._raw = slow
    got: list = []
    threads = [threading.Thread(target=lambda: got.append(db.row(q, p))) for _ in range(5)]
    [t.start() for t in threads]
    time.sleep(0.1)
    gate.set()
    [t.join(5) for t in threads]
    db._raw = real
    check("five asking the same thing at once is one question", calls.count(q) == 1 and len(got) == 5 and all(g["name"] == "Quick" for g in got))
    got[0]["name"] = "changed"
    check("...each gets its own copy", got[1]["name"] == "Quick")

    # asked before a write: not an answer to a question asked after it
    db.forget_reads()
    gate.clear()
    first = threading.Event()

    def stale_first(sql, params, one):
        out = real(sql, params, one)
        if not first.is_set():
            first.set()
            gate.wait(5)
        return out
    db._raw = stale_first
    old: list = []
    t1 = threading.Thread(target=lambda: old.append(db.row(q, p)))
    t1.start()
    first.wait(5)
    with db.tx() as c:
        c.execute("UPDATE profiles SET name='Quicker' WHERE id=?", p)
    check("a read begun after a write doesn't wait for the one begun before it", db.row(q, p)["name"] == "Quicker")
    gate.set()
    t1.join(5)
    db._raw = real
    check("...and the older answer isn't kept", db.row(q, p)["name"] == "Quicker")

    # a failure reaches who's waiting as a fresh try, not a hang
    db.forget_reads()

    class OperationalError(Exception):
        pass
    tries = []

    def fail_once(sql, params, one):
        tries.append(1)
        time.sleep(0.05)
        if len(tries) == 1:
            raise OperationalError("dropped")
        return real(sql, params, one)
    db._raw = fail_once
    res: list = []

    def ask():
        try:
            res.append(db.row(q, p)["name"])
        except OperationalError:
            res.append("failed")
    ts = [threading.Thread(target=ask) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(5) for t in ts]
    db._raw = real
    check("if the read on its way fails, who was waiting asks for themselves", sorted(res) == ["Quicker", "failed"])

    # what a write ended comes back behind it
    db.forget_reads()
    db._refresher[0] = True
    try:
        db.row(q, p)
        db.row("SELECT COUNT(*) AS n FROM coding_solved WHERE profile_id=?", p)
        with db.tx() as c:
            c.execute("UPDATE profiles SET name='Refilled' WHERE id=?", p)
        n = db.refill_wanted()
        trips_after, r = trips(lambda: db.row(q, p))
        check("what a write ended is asked for again behind it: the next page doesn't wait", n == 1 and trips_after == 0 and r["name"] == "Refilled")
        key = next(k for k in db._reads if k[0] == q)
        db._reads[key]["used"] -= db._REFILL_RECENT + 5
        with db.tx() as c:
            c.execute("UPDATE profiles SET name='Later' WHERE id=?", p)
        check("...but what nobody's looked at lately isn't", db.refill_wanted() == 0 and db.row(q, p)["name"] == "Later")
    finally:
        db._refresher[0] = False
        db._wanted.clear()

    # a start asks for what was in use last time
    db.forget_reads()
    time_keyed = "SELECT COUNT(*) AS n FROM events WHERE start_ts >= ?"
    this_second = "SELECT COUNT(*) AS n FROM events WHERE start_ts <= ?"
    long_text = "SELECT COUNT(*) AS n FROM contacts WHERE notes=?"
    db.row(q, p)
    db.row(time_keyed, (db.this_hour() - 86400,))
    db.row(this_second, (db.this_hour() + 7,))
    db.row(long_text, ("something a person wrote " * 10,))
    n = db.save_hot_reads()
    saved = db._hot_file().read_text(encoding="utf-8")
    check("what's saved for next time is the question, not the answer", n >= 1 and q in saved and "Later" not in saved)
    check("...not a read of one exact moment, nor long text", this_second not in saved and long_text not in saved and time_keyed in saved)
    db.forget_reads()
    check("the next start asks for it", db.warm_start() >= 1 and trips(lambda: db.row(q, p))[0] == 0)
    check("...and a read from the hour moves on to this hour", trips(lambda: db.row(time_keyed, (db.this_hour() - 86400,)))[0] == 0)
    later = json.loads(db._hot_file().read_text(encoding="utf-8"))
    later["hour"] -= 7200
    later["reads"] = [[s, [x - 7200 if isinstance(x, int) and x > 1e9 else x for x in ps], one] for s, ps, one in later["reads"]]
    db._hot_file().write_text(json.dumps(later), encoding="utf-8")
    db.forget_reads()
    db.warm_start()
    check("...even when it was saved hours ago", trips(lambda: db.row(time_keyed, (db.this_hour() - 86400,)))[0] == 0)
    db._hot_file().write_text("this isn't what was saved")
    check("a damaged file is ignored", db.warm_start() == 0)
    db._hot_file().write_text(json.dumps({"hour": db.this_hour(), "reads": [["SELECT a_column_from_an_old_version FROM profiles", [], False]]}))
    check("a query from an older version does no harm", db.warm_start() == 1)
    db._hot_file().unlink()
    check("nothing saved, nothing asked", db.warm_start() == 0)

    t0 = time.time()
    r = path.counts(pid)
    check("a time-keyed read stays the same read for the hour", db.this_hour() % 3600 == 0 and db.this_hour() <= db.now() < db.this_hour() + 3600)
    check("the Path's counts are one question, whatever it covers", trips(lambda: db.row(path._COUNTS_SQL, (pid,) * len(path._COUNTS)))[0] == 0
          and r["applied"] == 0 and time.time() - t0 < 2)
    now = db.now()
    with db.tx() as c:
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at, applied_at) VALUES (?,?,?,?,?)", (pid, "j-a", "offer", now, now))
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at, applied_at) VALUES (?,?,?,?,?)", (pid, "hack:x", "applied", now, now))
    r = path.counts(pid)
    check("...and right, split by what counts", r["applied"] == 1 and r["offers"] == 1 and r["replies"] == 1 and r["hackathons"] == 1 and r["following"] == 0)


def under_load():
    """Readers hammering the same read while someone writes: whoever just saved must read back what they saved, every time."""
    pid = new_profile("Load 0")
    q, p = "SELECT name FROM profiles WHERE id=?", (pid,)
    stop, stale, errors = threading.Event(), [], []
    real = db._raw

    def slow(sql, params, one):  # reads that take a moment, so they overlap the writes
        time.sleep(0.001)
        return real(sql, params, one)
    db._raw = slow

    def reader():
        while not stop.is_set():
            try:
                db.row(q, p)
            except Exception as e:
                errors.append(repr(e))
    threads = [threading.Thread(target=reader) for _ in range(6)]
    [t.start() for t in threads]
    rounds = 60 if db.is_postgres() else 400
    gen_before = db._all_gen[0]
    wiped = [0]
    try:
        for i in range(1, rounds):
            with db.tx() as c:
                c.execute("UPDATE profiles SET name=? WHERE id=?", (f"Load {i}", pid))
            got = db.row(q, p)["name"]
            if got != f"Load {i}":
                stale.append((i, got))
    finally:
        stop.set()
        [t.join(10) for t in threads]
        db._raw = real
        wiped[0] = db._all_gen[0] - gen_before
    check(f"under load, a save is never followed by the old value ({rounds} saves, 6 readers) {stale[:3] or ''}", not stale)
    check(f"...and nothing errors {errors[:2] or ''}", not errors)
    check(f"...and this process's own saves never wipe everything else it has read ({wiped[0]} wipes)", wiped[0] == 0)
    check("...and nobody's left waiting on a read", not db._inflight)


def saves_dont_wait():
    """A new install seeds thousands of boards behind the page: a save made meanwhile can't wait for all of them."""
    with db.tx() as c:
        c.execute("DELETE FROM companies")
    db.meta_set("seed_version", "older")
    real, opened, biggest = db.tx, [], [0]

    class Counting:
        def __init__(self, c):
            self.c = c

        def execute(self, *a):
            return self.c.execute(*a)

        def executemany(self, sql, seq):
            seq = list(seq)
            biggest[0] = max(biggest[0], len(seq))
            return self.c.executemany(sql, seq)

    @contextmanager
    def counted():
        opened.append(1)
        with real() as c:
            yield Counting(c)
    db.tx = counted
    try:
        store.seed_companies()
    finally:
        db.tx = real
    n = db.row("SELECT COUNT(*) AS n FROM companies")["n"]
    check(f"seeding {n} boards goes a batch at a time, so a save never waits for them all", n > 1000 and biggest[0] <= 500 and len(opened) >= n // 500)


def posting(job_id: str, title: str = "Software Intern", active: int = 1, changed: int | None = None):
    now = changed if changed is not None else db.now()
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO jobs (id, source, title, company, company_key, url, active, first_seen, last_seen, changed_at) VALUES"
                  " (?,?,?,?,?,?,?,?,?,?)", (job_id, "test", title, "Acme", "acme", f"https://x.test/{job_id}", active, now, now, now))


def postings_copy():
    """The list of postings on Supabase: the code path is forced here, on the test's file."""
    real = store.db.is_postgres
    store.db.is_postgres = lambda: True
    snap = store._snapshot_file()

    def fresh_run():  # as a new process starts: memory empty, the copy on disk still there
        store._mirror.update(rows=None, at=0)

    def wait_saved():
        for t in threading.enumerate():
            if t.name == "postings-snapshot":
                t.join(10)

    try:
        with db.tx() as c:
            c.execute("DELETE FROM jobs")
        posting("a")
        posting("b")
        store._mirror.update(rows=None, at=0)
        snap.unlink(missing_ok=True)
        n, rows = trips(store._active_rows)
        check("the first start reads them all and saves a copy", {r["id"] for r in rows} == {"a", "b"} and n == 1)
        wait_saved()
        check("...on disk", snap.exists() and {*json.load(gzip.open(snap, "rt"))["rows"]} == {"a", "b"})

        fresh_run()
        db.forget_reads()
        n, rows = trips(store._active_rows)
        check("the next start uses the copy: it asks only what changed, and a count", n == 2 and {r["id"] for r in rows} == {"a", "b"})

        posting("c")
        posting("a", title="Software Intern, renamed")
        rows = {r["id"]: r for r in store._active_rows()}
        check("a new posting and an edited one come through", set(rows) == {"a", "b", "c"} and rows["a"]["title"].endswith("renamed"))

        with db.tx() as c:
            c.execute("UPDATE jobs SET active=0, changed_at=? WHERE id='b'", (db.now(),))
        check("a closed posting leaves", {r["id"] for r in store._active_rows()} == {"a", "c"})

        with db.tx() as c:
            c.execute("UPDATE jobs SET active=0 WHERE id='a'")  # closed without saying when: the count catches it
        check("something changed without saying so: the count catches it, and all are read again", {r["id"] for r in store._active_rows()} == {"c"})

        old = db.now() - 400
        store._mirror["at"] = db.now()
        posting("d", changed=old)  # written just before the last read finished
        check("a posting written just before the last check isn't missed", "d" in {r["id"] for r in store._active_rows()})

        wait_saved()
        fresh_run()
        snap.write_bytes(b"not a gzip file")
        check("a damaged copy is ignored", {r["id"] for r in store._active_rows()} == {"c", "d"})
        wait_saved()
        with gzip.open(snap, "wt") as f:
            json.dump({"version": store.SNAPSHOT_VERSION + 99, "at": 1, "rows": {"ghost": {"id": "ghost"}}}, f)
        fresh_run()
        check("...and so is one from another version", "ghost" not in {r["id"] for r in store._active_rows()})
    finally:
        store.db.is_postgres = real
        store._mirror.update(rows=None, at=0)


def budget():
    """Once warm no page goes to the database, and none takes long, on a realistic number of postings."""
    server._sync_in_background = lambda *a, **k: None
    market.read_more = lambda *a, **k: 0  # no descriptions here, and no reading them from the internet
    now = db.now()
    with db.tx() as c:
        c.execute("DELETE FROM jobs")
        c.executemany("INSERT INTO jobs (id, source, title, company, company_key, url, locations, regions, category, skills, active, first_seen, last_seen,"
                      " changed_at, posted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [(f"j{i}", "test", f"{['Software', 'Data', 'Quant', 'Hardware'][i % 4]} Engineering Intern {i}", f"Company {i % 400}", f"company{i % 400}",
                        f"https://x.test/{i}", '["Toronto, ON"]', '["CA"]', ["Software", "Data", "Quant", "Hardware"][i % 4], '["Python", "SQL"]', 1, now, now, now, now - i * 60)
                       for i in range(6000)])
    pid = new_profile("Budget")
    store.invalidate_cache()
    cl = TestClient(app, base_url="http://localhost")
    pages = ["/api/state", f"/api/jobs?profile_id={pid}&limit=50", f"/api/tracks?profile_id={pid}", f"/api/tracks/bar?profile_id={pid}",
             f"/api/tracks/role:software?profile_id={pid}", f"/api/jobs?profile_id={pid}&track=role:software&limit=50",
             f"/api/tracks/role:software/companies?profile_id={pid}", f"/api/tracks/role:software/market?profile_id={pid}",
             f"/api/tracks/co:startups/companies?profile_id={pid}", f"/api/path?profile_id={pid}", f"/api/path/ladder?profile_id={pid}",
             f"/api/events?profile_id={pid}", f"/api/applications?profile_id={pid}", f"/api/recruiters?profile_id={pid}", f"/api/autopilot?profile_id={pid}"]
    cold = {}
    for u in pages:
        cold[u] = trips(lambda: cl.get(u))[0]
    slow, asked = {}, {}
    for u in pages:
        cl.get(u)
        t = time.time()
        n, r = trips(lambda: cl.get(u))
        ms = (time.time() - t) * 1000
        if r.status_code != 200 or ms > 800:
            slow[u.split("?")[0]] = (r.status_code, round(ms))
        if n:
            asked[u.split("?")[0]] = n
    check(f"once warm, no page goes to the database {asked or ''}", not asked)
    check(f"once warm, every page answers in well under a second on 6000 postings {slow or ''}", not slow)
    print("  first-visit queries:", {u.split("?")[0].replace("/api/", ""): n for u, n in cold.items()})
    check(f"a first visit to any page stays a handful of queries (worst {max(cold.values())})", max(cold.values()) <= 40)


def main():
    db.init()
    t0 = time.time()
    reads_and_writes()
    another_process()
    time_and_the_internet()
    first_visits()
    under_load()
    saves_dont_wait()
    postings_copy()
    budget()
    print(f"\n{sum(results.values())}/{len(results)} passed in {time.time() - t0:.1f}s")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
