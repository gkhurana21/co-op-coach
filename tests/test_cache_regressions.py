"""Delayed feed edits reach the local snapshot; completed/failed description reads refresh Market.

Uses a temporary database, synthetic feed responses and description text. On SQLite, the Supabase snapshot branch
is forced and the schema-import SQL runs against an attached in-memory feed: neither is a live Postgres check.
"""

import gzip
import json
import os
import tempfile
import threading
import time

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-cache-regressions-"), GROQ_API_KEY="",
                  ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import central, db, jobdetail, market, store  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def wait_saved() -> None:
    for thread in threading.enumerate():
        if thread.name == "postings-snapshot":
            thread.join(10)


def posting(title: str = "Software Intern") -> str:
    j = store.normalize({"source": "lever:cache-test", "company": "Cache Test", "title": title,
                         "url": "https://example.test/cache", "locations": ["Toronto, ON"], "description": ""})
    store.upsert_jobs([j], db.now() - 7200)
    return j["id"]


def delayed_imports() -> None:
    actual_pg, real_pg, real_rows, real_schema = db.is_postgres(), db.is_postgres, central._rest_rows, central._schema
    db.is_postgres = lambda: True  # exercise the snapshot with the test's existing backend
    snap = store._snapshot_file()
    try:
        with db.tx() as c:
            c.execute("DELETE FROM jobs")
        jid = posting()
        store._mirror.update(rows=None, at=0)
        snap.unlink(missing_ok=True)
        before = {r["id"]: r for r in store._active_rows()}
        wait_saved()
        check("a snapshot starts with the old posting", before[jid]["title"] == "Software Intern")
        received = db.now()
        edited = {**db.row("SELECT * FROM jobs WHERE id=?", (jid,)), "title": "Hardware Intern",
                  "flags": '["us_citizen"]', "changed_at": received - 3600}
        central._rest_rows = lambda *a, **k: [edited]
        out = central._pull_rest(0)
        saved = db.row("SELECT changed_at, imported_at FROM jobs WHERE id=?", (jid,))
        check("a delayed REST edit keeps the crawler timestamp and stamps local receipt", out["changed"] == 1
              and saved["changed_at"] == received - 3600 and saved["imported_at"] >= received)
        check("the local timestamp is not requested from older published feed views", "imported_at" not in central.JOB_COLS)
        # A restart must reconcile a copy written before the import, even with the same active row count.
        store._mirror.update(rows=None, at=0)
        db.forget_reads()
        trips = db.round_trips[0]
        after = {r["id"]: r for r in store._active_rows()}
        check("after restart, the delta updates the title and eligibility flags without a full reload",
              after[jid]["title"] == "Hardware Intern" and after[jid]["flags"] == '["us_citizen"]'
              and db.round_trips[0] - trips == 2)
        check("unchanged feed rows are not rewritten or restamped", central._pull_rest(0)["changed"] == 0
              and db.row("SELECT imported_at FROM jobs WHERE id=?", (jid,))["imported_at"] == saved["imported_at"])
        wait_saved()

        if not actual_pg:
            # The schema branch runs actual INSERT ... SELECT ... ON CONFLICT SQL, with no remote connection.
            with db.tx() as c:
                c.execute("ATTACH DATABASE ':memory:' AS cache_feed_test")
                c.execute("CREATE TABLE cache_feed_test.jobs AS SELECT * FROM main.jobs")
                c.execute("UPDATE cache_feed_test.jobs SET title='Data Intern', changed_at=?", (received - 1800,))
            central._schema = lambda: "cache_feed_test"
            out = central._pull_schema(0)
            after = {r["id"]: r for r in store._active_rows()}
            check("a delayed schema import also reaches the snapshot", out["changed"] == 1 and after[jid]["title"] == "Data Intern"
                  and db.row("SELECT imported_at FROM jobs WHERE id=?", (jid,))["imported_at"] >= received)
            check("an unchanged schema import stays a no-op", central._pull_schema(0)["changed"] == 0)
            with db.tx() as c:
                c.execute("DETACH DATABASE cache_feed_test")
            wait_saved()

        with gzip.open(snap, "wt", encoding="utf-8") as f:
            json.dump({"version": 1, "at": received, "rows": before}, f)
        store._mirror.update(rows=None, at=0)
        check("snapshots made before import timestamps are rebuilt once", {r["id"]: r for r in store._active_rows()}[jid]["title"]
              == ("Hardware Intern" if actual_pg else "Data Intern"))
    finally:
        wait_saved()
        db.is_postgres, central._rest_rows, central._schema = real_pg, real_rows, real_schema
        store._mirror.update(rows=None, at=0)
        store.invalidate_cache()


def market_reads() -> None:
    real_feed, real_fetch = central.description, jobdetail.fetch_description
    started, finish = threading.Event(), threading.Event()
    description = ("Requirements:\nPython and SQL experience.\nNice to have:\nDocker.\n"
                   "Compensation:\nCAD $30 - $40 per hour.\n" + "Build reliable software with our team. " * 12)
    try:
        with db.tx() as c:
            c.execute("DELETE FROM jobs")
            pid = c.execute("INSERT INTO profiles (name, profile_json, created_at, updated_at) VALUES (?,?,?,?)",
                            ("Cache Student", db.dumps({"skills": ["Python"], "target_categories": ["Software"]}),
                             db.now(), db.now())).lastrowid
        jid = posting()
        store.invalidate_cache()
        central.description = lambda *a: ""

        def fetched(_url):
            started.set()
            if not finish.wait(5):
                raise TimeoutError("test did not release the description reader")
            return description

        jobdetail.fetch_description = fetched
        tid, key = "role:software", (pid, "role:software")
        market._market_cache.clear()
        market._skills_cache.clear()
        market._reading.clear()
        market._unreadable.clear()
        check("Resume Studio can warm skills before any full text arrives", market.track_skills(pid, tid)["described"] == 0)
        initial = market.track_market(pid, tid)
        check("Market reports its background description read", started.wait(5) and initial["reading"] == 1)
        finish.set()
        for thread in threading.enumerate():
            if thread.name == "market-read-more":
                thread.join(10)
        check("the real reader saves its synthetic description", len(db.row("SELECT description FROM jobs WHERE id=?", (jid,))["description"]) > 200)
        stamp, value, at = market._market_cache[key]
        market._market_cache[key] = (stamp, value, at - market.READING_REFRESH - 1)
        updated = market.track_market(pid, tid)
        check("the next refresh has current skills/pay and stops the reading indicator", updated["described"] == 1
              and updated["reading"] == 0 and updated["glance"]["pay"]["median"] == 35
              and any(s["skill"] == "SQL" for s in updated["skills"]))
        check("Resume Studio's already-warm skills refresh too", market.track_skills(pid, tid)["described"] == 1)

        # A failed fetch writes no jobs/facts, so the time-bound reading result itself has to expire.
        with db.tx() as c:
            c.execute("UPDATE jobs SET description='' WHERE id=?", (jid,))
            c.execute("DELETE FROM job_facts WHERE job_id=?", (jid,))
        store.invalidate_cache()
        market._market_cache.clear()
        market._skills_cache.clear()
        started.clear()
        finish.clear()
        description = ""
        failed = market.track_market(pid, tid)
        check("an unreadable posting initially shows a running read", started.wait(5) and failed["reading"] == 1)
        finish.set()
        for thread in threading.enumerate():
            if thread.name == "market-read-more":
                thread.join(10)
        stamp, value, at = market._market_cache[key]
        market._market_cache[key] = (stamp, value, at - market.READING_REFRESH - 1)
        check("a failed read also clears its indicator without waiting for another write", market.track_market(pid, tid)["reading"] == 0
              and jid in market._unreadable)
    finally:
        finish.set()
        central.description, jobdetail.fetch_description = real_feed, real_fetch


def main() -> None:
    db.init()
    started = time.time()
    delayed_imports()
    market_reads()
    print(f"\n{sum(results.values())}/{len(results)} passed in {time.time() - started:.1f}s")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
