"""The shared job feed: postings record when they last changed and whether they're a real new opening; an app on the
feed pulls only what changed (in pages, looking back 10 minutes, new vs. a board's backlog, closures, edits), keeps the
full text and form answers it read itself, learns which boards the feed reads and still reads the ones added here;
its sync skips everything the crawler does; the crawler's schedule matches the workflow; the crawler refuses to run
on someone's own database; and the SQL that would open the feed to the public isn't run by anything.

Run: .venv/bin/python -m tests.test_central
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-central-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent",
                  COOPCOACH_SYNC_PROCESSES="1")

import httpx  # noqa: E402
import yaml  # noqa: E402

from coopcoach import autopilot, central, config, db, store, sync  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def raw(i, title="Software Engineer Intern", company="Acme", board="greenhouse:acme"):
    return {"source": board, "company": company, "title": title, "url": f"https://x.test/{board}/{i}",
            "locations": ["Toronto, ON"], "description": "Build things in Python."}


def stand_in(pairs):
    real = [(mod, name, getattr(mod, name)) for mod, name, _ in pairs]
    for mod, name, fn in pairs:
        setattr(mod, name, fn)
    return lambda: [setattr(mod, name, fn) for mod, name, fn in real]


BASICS = [(sync.simplify, "fetch", lambda repos: []), (sync.linkedin, "fetch", lambda *a, **k: []),
          (sync.indeed, "available", lambda: False), (sync.companies, "refresh_yc", lambda *a, **k: 0),
          (sync.events, "sync_feeds", lambda: (0, {})), (sync, "enrich_top", lambda prof, n=60: 0),
          (sync, "backfill_descriptions", lambda **k: 0), (autopilot, "on_sync", lambda ids: None)]


def tracking_checks():
    j = store.prepare(raw(1), {})
    store.upsert_jobs([j], db.now())
    row = lambda: db.row("SELECT active, changed_at, opened_at FROM jobs WHERE id=?", (j["id"],))  # noqa: E731
    check("a new posting records when it changed", abs((row()["changed_at"] or 0) - db.now()) < 5)
    with db.tx() as c:
        c.execute("UPDATE jobs SET changed_at=1 WHERE id=?", (j["id"],))
    store.upsert_jobs([store.prepare(raw(1), store.stored_fingerprints())], db.now())
    check("...seeing it again unchanged doesn't count as a change (apps don't pull it again)", row()["changed_at"] == 1)
    store.deactivate_missing(["greenhouse:acme"], set(), db.now() + 10)
    check("...closing it does", row()["active"] == 0 and row()["changed_at"] > 1)
    with db.tx() as c:
        c.execute("UPDATE jobs SET changed_at=1 WHERE id=?", (j["id"],))
    store.upsert_jobs([store.prepare(raw(1), {})], db.now() + 20)
    check("...and so does it reopening", row()["active"] == 1 and row()["changed_at"] > 1)
    wd = {**raw(4, board="workday:https://acme.wd1.myworkdayjobs.com/Careers"), "posted_text": "Posted 3 Days Ago"}
    check("a Workday posting read again an hour later isn't a change ('Posted 3 Days Ago' is compared, not the date)",
          store.fingerprint({**wd, "posted_at": 1000}) == store.fingerprint({**wd, "posted_at": 4600})
          and store.fingerprint(wd) != store.fingerprint({**wd, "posted_text": "Posted 4 Days Ago"}))
    store.mark_opened([j["id"]])
    first = row()["opened_at"]
    with db.tx() as c:
        c.execute("UPDATE jobs SET opened_at=? WHERE id=?", (first - 500, j["id"]))
    store.mark_opened([j["id"]])
    check("a real new opening is marked once (marking it again keeps when it went up)",
          row()["opened_at"] == first - 500)

    # a sync marks what's new on a board it already read, but not what a board it's reading for the first time
    # already listed
    now = db.now()
    with db.tx() as c:
        c.execute("INSERT INTO sync_runs (started_at, finished_at, kind) VALUES (?,?, 'full')", (now - 9000, now - 8000))
        c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('greenhouse','acme','Acme',?,1)",
                  (now - 4000,))
        c.execute("INSERT INTO companies (ats, slug, name) VALUES ('greenhouse','newco','NewCo')")
    tasks = {"acme": [raw(2)], "newco": [raw(3, company="NewCo", board="greenhouse:newco")]}
    undo = stand_in(BASICS + [(sync, "_company_task", lambda co, known, peek=False: tasks.get(co["slug"], [])),
                              (sync, "notify_profiles", lambda ids, started: {})])
    try:
        sync.run(notify_users=False)
    finally:
        undo()
    opened = {r["url"].rsplit("/", 1)[-1]: r["opened_at"] for r in db.rows(
        "SELECT url, opened_at FROM jobs WHERE url LIKE 'https://x.test/greenhouse:%'")}
    check("a sync marks a new posting on a board it already read as opened, not a new board's backlog",
          opened.get("2") and opened.get("3") is None)


# --- a fake Supabase REST API in front of the feed ------------------------------------------------------------

FEED = {"jobs": [], "companies": [], "board_roles": [], "runs": []}
asked: list[dict] = []


def feed_job(i, changed, opened=None, active=1, title="Data Science Intern", description=""):
    jid = f"f{i:03d}"
    return {"id": jid, "source": "lever:feedco", "company": "FeedCo", "company_key": "feedco", "title": title,
            "url": f"https://jobs.lever.co/feedco/{jid}", "locations": '["Toronto, ON"]', "regions": '["Canada"]',
            "terms": "[]", "category": "Data", "tier": "other", "yc_batch": None, "skills": '["Python"]',
            "flags": "[]", "description": description, "posted_at": None, "first_seen": changed, "last_seen": changed,
            "active": active, "req_id": None, "requirements": "[]", "form_checked": 0, "fp": f"fp{i}",
            "changed_at": changed, "opened_at": opened}


def handler(req: httpx.Request) -> httpx.Response:
    view = req.url.path.rsplit("/", 1)[-1]
    q = dict(req.url.params)
    asked.append({"view": view, **q})
    if req.headers.get("apikey") != "pk_test":
        return httpx.Response(401)
    if view == "coopcoach_jobs":
        rows = list(FEED["jobs"])
        if "id" in q:
            rows = [r for r in rows if r["id"] == q["id"][3:]]
        if "changed_at" in q:
            rows = [r for r in rows if r["changed_at"] >= int(q["changed_at"][4:])]
        if "or" in q:
            m = re.fullmatch(r"\(changed_at\.gt\.(\d+),and\(changed_at\.eq\.(\d+),id\.gt\.(\w+)\)\)", q["or"])
            t, _, last = int(m.group(1)), m.group(2), m.group(3)
            rows = [r for r in rows if r["changed_at"] > t or (r["changed_at"] == t and r["id"] > last)]
        if q.get("active") == "eq.1":
            rows = [r for r in rows if r["active"] == 1]
        rows.sort(key=lambda r: (r["changed_at"], r["id"]))
        rows = rows[:int(q.get("limit", 10 ** 6))]
    elif view == "coopcoach_companies":
        rows = sorted(FEED["companies"], key=lambda r: (r["ats"], r["slug"]))
        off = int(q.get("offset", 0))
        rows = rows[off:off + int(q.get("limit", 10 ** 6))]
    elif view == "coopcoach_board_roles":
        rows = [r for r in FEED["board_roles"] if r["day"] >= q["day"][4:]]
    elif view == "coopcoach_runs":
        rows = sorted(FEED["runs"], key=lambda r: -r["finished_at"])[:1]
    else:
        return httpx.Response(404)
    cols = q.get("select", "").split(",")
    return httpx.Response(200, json=[{k: r.get(k) for k in cols} if q.get("select") else r for r in rows])


def rest_checks():
    now = db.now()
    central.SETTINGS.write_text(json.dumps({"on": True, "mode": "rest", "url": "https://feed.test", "key": "pk_test"}))
    central._client = lambda: httpx.Client(transport=httpx.MockTransport(handler))
    central.PAGE = 2  # pages of two, so a pull has to page through
    check("with a feed address and key set, this app is on the shared feed (rest mode)", central.mode() == "rest")
    FEED["jobs"] = [feed_job(i, now - 3000 - i) for i in range(5)] + [feed_job(9, now - 5000, active=0)]
    FEED["companies"] = [{"ats": "lever", "slug": "feedco", "name": "FeedCo", "tier": "other", "last_status": "ok",
                          "last_count": 5, "last_checked": now - 600, "enabled": 1, "removed": 0},
                         {"ats": "greenhouse", "slug": "acme", "name": "Acme", "tier": "other", "last_status": "ok",
                          "last_count": 1, "last_checked": now - 600, "enabled": 1, "removed": 0}]
    FEED["board_roles"] = [{"board": "lever:feedco", "day": "2099-01-01", "roles": 40, "interns": 5}]
    FEED["runs"] = [{"finished_at": now - 300, "kind": "full", "new_jobs": 5}]
    with db.tx() as c:  # a board someone added on this computer, which the feed doesn't read
        c.execute("INSERT INTO companies (ats, slug, name, origin) VALUES ('ashby','mine','Mine','you')")
    asked.clear()
    out = central.pull()
    ids = {r["id"] for r in db.rows("SELECT id FROM jobs WHERE source='lever:feedco'")}
    check("joining the feed copies what's open, a page at a time (closed postings don't come along)",
          ids == {f"f{i:03d}" for i in range(5)} and len([a for a in asked if a["view"] == "coopcoach_jobs"]) == 3)
    check("...without the postings' full text (that's fetched when one is opened)",
          all("description" not in a.get("select", "") for a in asked if a["view"] == "coopcoach_jobs"))
    check("...and nothing in it alerts: it was all open before this app joined", out["new"] and out["opened"] == [])
    check("...and the boards the feed reads are marked, so this app doesn't read them too",
          db.row("SELECT central FROM companies WHERE slug='feedco'")["central"] == 1
          and db.row("SELECT central FROM companies WHERE slug='acme'")["central"] == 1
          and [b["slug"] for b in central.local_boards()] == ["mine"])
    check("...with how fast each one is hiring (for the startup feed)",
          db.row("SELECT roles FROM board_roles WHERE board='lever:feedco'")["roles"] == 40)

    # something this app read itself: the full text, and who the role is open to from its application form
    with db.tx() as c:
        c.execute("UPDATE jobs SET description='Long text read here. ' || 'x', requirements='[\"citizens\"]',"
                  " form_checked=1 WHERE id='f001'")
    since = int(db.meta_get("central_since"))
    late = feed_job(7, since - 120, opened=since - 120)  # committed late, a minute before the last pull's high mark
    FEED["jobs"] += [feed_job(5, now + 5, opened=now + 5), feed_job(6, now + 6), late]
    FEED["jobs"][0] = feed_job(0, now + 7, active=0)
    FEED["jobs"][1] = feed_job(1, now + 8, title="Data Science Intern (Fall)")
    asked.clear()
    out = central.pull()
    check("the next pull brings what changed since, looking back 10 minutes for rows that committed late",
          set(out["new"]) == {"f005", "f006", "f007"} and out["changed"] == 5  # f002-f004 seen again, not rewritten
          and db.row("SELECT 1 AS x FROM jobs WHERE id='f009'") is None)
    check("...alerts only real new openings (a board's backlog doesn't)", sorted(out["opened"]) == ["f005", "f007"])
    check("...closes what closed and updates what was edited",
          out["closed"] == 1 and db.row("SELECT active FROM jobs WHERE id='f000'")["active"] == 0
          and db.row("SELECT title FROM jobs WHERE id='f001'")["title"] == "Data Science Intern (Fall)")
    kept = db.row("SELECT description, requirements, form_checked FROM jobs WHERE id='f001'")
    check("...and keeps the full text and form answers this app read itself", kept["description"].startswith(
        "Long text") and "citizens" in kept["requirements"] and kept["form_checked"] == 1)
    check("boards are only fetched again after a new crawl", not any(a["view"] == "coopcoach_companies" for a in asked))

    FEED["jobs"][2] = feed_job(2, now - 3002, description="The full posting, read by the crawler. " * 10)
    check("a posting's full text comes from the feed when someone opens it", central.description("f002").startswith(
        "The full posting"))
    check("a key the feed doesn't know gets nothing in", _bad_key())


def _bad_key() -> bool:
    s = json.loads(central.SETTINGS.read_text())
    central.SETTINGS.write_text(json.dumps({**s, "key": "nope"}))
    try:
        central.pull()
        return False
    except httpx.HTTPStatusError:
        return True
    finally:
        central.SETTINGS.write_text(json.dumps(s))


def feed_sync_checks():
    """On the feed, a sync pulls, then reads only the boards added here: no lists, no LinkedIn, no Workday backfill."""
    now = db.now()
    FEED["jobs"].append(feed_job(8, now + 30, opened=now + 30))
    FEED["runs"].append({"finished_at": now + 40, "kind": "quick", "new_jobs": 1})
    read, alerted, lists = [], [], []
    undo = stand_in(BASICS + [
        (sync.simplify, "fetch", lambda repos: lists.append("simplify") or []),
        (sync.linkedin, "fetch", lambda *a, **k: lists.append("linkedin") or []),
        (sync, "backfill_descriptions", lambda **k: lists.append("backfill") or 0),
        (sync, "_company_task", lambda co, known, peek=False: read.append((co["slug"], peek)) or []),
        (sync, "notify_profiles", lambda ids, started: alerted.append(set(ids)) or {})])
    try:
        out = sync.run()
        full_read, read[:] = list(read), []
        quick = sync.run_quick()
    finally:
        undo()
    check("on the feed, a full sync reads only the boards added on this computer", full_read == [("mine", False)])
    check("...and the quick check also reads the boards that matter to this person (here: ones that just posted),"
          " newest postings only", ("mine", True) in read and ("feedco", True) in read and all(p for _, p in read))
    check("...skips the lists, LinkedIn and reading full Workday postings (the crawler does those)", lists == [])
    check("...and alerts the feed's new openings like its own", alerted and "f008" in alerted[0])
    check("...and says what it got from the feed", out.get("shared_feed", {}).get("new") == 1
          and quick.get("shared_feed") is not None)
    check("the companies list is copied again after the feed's next crawl",
          db.meta_get("central_boards_at") == str(now + 40))
    saved, read = list(FEED["runs"]), []
    FEED["runs"] = [{"finished_at": now - 13 * 3600, "kind": "full", "new_jobs": 0}]
    with db.tx() as c:  # (the quick check just read these: a quiet board isn't due again for hours)
        c.execute("UPDATE companies SET last_checked=? WHERE slug IN ('feedco', 'acme')", (now - 3 * 86400,))
    undo = stand_in(BASICS + [(sync, "_company_task", lambda co, known, peek=False: read.append(co["slug"]) or [])])
    try:
        stale = sync.run(notify_users=False)
    finally:
        undo()
        FEED["runs"] = saved
    check("if the feed's crawler stops for half a day, this app reads every board itself until it's back",
          {"feedco", "acme"} <= set(read) and stale.get("shared_feed") is not None  # (the feed's boards too)
          and central.status()["stale"] is False)
    from fastapi.testclient import TestClient

    from coopcoach import server
    with TestClient(server.app, base_url="http://localhost") as cl:
        st = cl.get("/api/central").json()
        check("Settings shows the feed: on, its last crawl, the crawl times, the boards read here",
              st["on"] and st["last_crawl"]["kind"] == "quick" and len(st["crawls"]) == 3
              and st["local_boards"] == 1 and st["open"] > 0)
        cl.post("/api/central", json={"on": False})
        check("...and it can be turned off (every board read here again)", not central.enabled()
              and not cl.get("/api/central").json()["on"])
        r = cl.post("/api/central", json={"on": True})
        check("turning it on needs the feed in this Supabase project (a local file can't copy it)",
              r.status_code == 400 if not db.is_postgres() else r.status_code in (200, 400))


def schedule_checks():
    wf = yaml.safe_load((ROOT / ".github/workflows/crawl.yml").read_text(encoding="utf-8"))
    crons = [c["cron"] for c in wf[True]["schedule"]]  # PyYAML reads the key `on` as True
    want = central.CRAWLS["full"] + central.CRAWLS["quick"] + central.CRAWLS["discover"]
    check("the crawler's workflow runs at the times the app shows", sorted(crons) == sorted(want))
    text = (ROOT / ".github/workflows/crawl.yml").read_text(encoding="utf-8")
    check("...quick checks are the quick crons, and the discover run is weekly",
          all(c in text.split("kind=quick")[0] for c in central.CRAWLS["quick"]) and "crawl --discover" in text)
    check("...and it reads its database address from a secret, never the file",
          "secrets.COOPCOACH_CENTRAL_DB_URL" in text and "postgresql://" not in text)
    times = central.crawl_times()
    check("the crawl times show in this computer's time ('7:30 AM')",
          len(times) == 3 and all(re.fullmatch(r"\d{1,2}:\d\d [AP]M", t) for t in times))


def safety_checks():
    env = {**os.environ, "COOPCOACH_DATA": tempfile.mkdtemp(prefix="coopcoach-crawl-")}
    env.pop("COOPCOACH_DATABASE_URL", None)
    env.pop("COOPCOACH_PG_SCHEMA", None)
    r = subprocess.run([sys.executable, "-m", "coopcoach", "crawl"], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=120)
    check("the crawler refuses to run without the feed's own schema (it never crawls into someone's database)",
          r.returncode != 0 and "COOPCOACH_PG_SCHEMA" in (r.stdout + r.stderr))
    sql = central.setup_sql("SCRAM-SHA-256$4096:x$y:z")
    check("the crawler gets its own schema; the app may only read it", "AUTHORIZATION coopcoach_crawler" in sql
          and "GRANT SELECT ON TABLES TO coopcoach_app" in sql and "coopcoach." not in sql.replace("coopcoach_", ""))
    pub = central.publish_sql()
    check("the SQL that would open the feed publicly shows only postings and boards, read-only, through RLS",
          "security_invoker = on" in pub and pub.count("ENABLE ROW LEVEL SECURITY") == 4
          and not re.search(r"profiles|applications|documents|email|resume", pub) and "INSERT" not in pub)
    used = [p for p in (ROOT / "coopcoach").rglob("*.py") if "publish_sql(" in p.read_text(encoding="utf-8")
            and p.name != "central.py"]
    check("...and nothing runs it: making the feed public is its owner's call", used == [])
    seed = central.seed_sql()
    check("seeding the feed copies postings, boards and hiring history, never anyone's data",
          len(seed) >= 5 and not any(re.search(r"profiles|applications|documents|email_|favourites", s) for s in seed))


def postgres_checks():
    """On Supabase: an app in the same project copies the feed inside the database (schema mode)."""
    if not db.is_postgres():
        print("     (schema mode is checked in the --postgres run)")
        return
    central.SETTINGS.write_text(json.dumps({"on": True, "mode": "schema", "schema": central.SCHEMA}))
    db.meta_set("central_since", "")
    with db.tx() as c:
        c.execute("DELETE FROM meta WHERE key LIKE 'central_%'")
    if not central.available():
        print("     (no shared feed in this Supabase project yet: schema mode not checked)")
        return
    out = central.pull()
    total = db.row(f"SELECT COUNT(*) AS n FROM {central.SCHEMA}.jobs")["n"]
    mine = db.row("SELECT COUNT(*) AS n FROM jobs WHERE source != 'lever:feedco' AND url NOT LIKE 'https://x.test/%'")["n"]
    check("in the same Supabase project, joining copies the whole feed inside the database",
          total > 0 and mine >= total and out["opened"] == [])
    pick = db.row(f"SELECT id, title, active FROM {central.SCHEMA}.jobs ORDER BY changed_at DESC LIMIT 1")
    check("...row for row", db.row("SELECT title, active FROM jobs WHERE id=?", (pick["id"],)) == {
        "title": pick["title"], "active": pick["active"]})
    again = central.pull()
    check("...and the next pull only looks at the last hour or so", again["changed"] <= out["changed"])


def main():
    db.init()
    config.settings().setdefault("sync", {})["save_every_seconds"] = 0
    tracking_checks()
    rest_checks()
    feed_sync_checks()
    schedule_checks()
    safety_checks()
    postgres_checks()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
