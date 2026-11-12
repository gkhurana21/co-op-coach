"""A faster sync that reads the same postings: Lever's big boards read through their board page (only the
internships in full), small ones through the API's one list; a busy Workday site retried instead of failing; YC in a
lane of its own that never holds up the other boards; quiet boards spread across the day's syncs; closed postings
found in one query; and each part of the sync timed in its report.

Run: .venv/bin/python -m tests.test_sync_speed
"""

import contextlib
import json
import os
import tempfile
import threading
import time

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-syncspeed-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent",
                  COOPCOACH_SYNC_PROCESSES="1")

import httpx  # noqa: E402

from coopcoach import autopilot, config, db, store, sync  # noqa: E402
from coopcoach.sources import ats  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def mock(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return lambda: contextlib.nullcontext(client)


def lever_post(pid, title, commitment="Full-time"):
    return {"id": pid, "text": title, "hostedUrl": f"https://jobs.lever.co/acme/{pid}", "createdAt": 1760000000000,
            "categories": {"commitment": commitment, "location": "Toronto, ON"}, "descriptionPlain": "Build things."}


def lever_page(posts):
    return "".join(f'<div class="posting" data-qa-posting-id="{p["id"]}"><a class="posting-title" href="#">'
                   f'<h5 data-qa="posting-name">{p["text"].replace("&", "&amp;")}</h5><div class="posting-categories"><span class="sort-by-'
                   f'commitment posting-category small-category-label commitment">{p["categories"]["commitment"]}'
                   f'</span></div></a></div>' for p in posts)


def lever_checks():
    small = [lever_post("s1", "Software Engineer Intern"), lever_post("s2", "Staff Engineer")]
    big = [lever_post(f"b{i}", "Account Executive") for i in range(70)] + [
        lever_post("i1", "Data Science Co-op"), lever_post("i2", "Analyst", commitment="Internship"),
        lever_post("i3", "Machine Learning Intern & Researcher"), lever_post("gone", "Design Intern")]
    asked, flaky = [], {"i1": 1}

    def handler(req):
        path = req.url.path
        asked.append(path)
        board = "big" if "/big" in path else "small"
        posts = big if board == "big" else small
        if req.url.host == "jobs.lever.co":
            return httpx.Response(200, text=lever_page(posts))
        pid = path.rsplit("/", 1)[-1]
        if pid in (board, "custom"):
            return httpx.Response(200, json=posts)
        if pid == "gone":
            return httpx.Response(404)
        if flaky.get(pid):
            flaky[pid] -= 1
            return httpx.Response(502)
        return httpx.Response(200, json=next(p for p in posts if p["id"] == pid))

    real = ats.pooled
    ats.pooled = mock(handler)
    try:
        got = ats.lever("small", "Acme", None, set())
        check("a small Lever board is read from the API's one list, as before",
              [j["title"] for j in got] == ["Software Engineer Intern"] and "/v0/postings/small" in asked)
        asked.clear()
        got = ats.lever("big", "Acme", None, set())
        titles = sorted(j["title"] for j in got)
        check("a big Lever board: only its internships are read in full (by title or commitment, entities decoded)",
              titles == ["Analyst", "Data Science Co-op", "Machine Learning Intern & Researcher"]
              and "/v0/postings/big" not in asked and len({p for p in asked if "/v0/postings/big/" in p}) == 4)
        check("...a posting closed since its page was read is skipped, and a 502 is asked again",
              "Design Intern" not in titles and asked.count("/v0/postings/big/i1") == 2)
        check("...and gives the same fields the full list does", got[0]["description"] == "Build things."
              and got[0]["locations"] == ["Toronto, ON"] and got[0]["source"] == "lever")
    finally:
        ats.pooled = real


def workday_board(postings, busy=0):
    """A fake Workday site: postings are (title, filter value or None); its filters include an internship one."""
    calls = {"n": 0, "bodies": []}

    def handler(req):
        calls["n"] += 1
        body = json.loads(req.content)
        calls["bodies"].append(body)
        if calls["n"] <= busy:
            return httpx.Response(429, headers={"retry-after": "0"})
        wanted = (body["appliedFacets"] or {}).get("workerSubType")
        q = body["searchText"].lower()
        hits = [{"title": t, "externalPath": f"/job/{i}"} for i, (t, f) in enumerate(postings)
                if (f in wanted if wanted else (not q or q.replace("-", "") in t.lower().replace("-", "")))]
        page = hits[body["offset"]:body["offset"] + body["limit"]]
        facets = [{"facetParameter": "workerSubType", "values": [
            {"id": "int1", "descriptor": "Intern (Fixed Term) (Trainee)", "count": 5},
            {"id": "intl", "descriptor": "International Associate", "count": 3},
            {"id": "reg", "descriptor": "Regular", "count": 9}]}]
        return httpx.Response(200, json={"jobPostings": page, "total": len(hits), "facets": facets})
    return handler, calls


def workday_checks():
    real, real_sleep = ats.pooled, ats.time.sleep
    ats.time.sleep = lambda s: None
    site = "https://acme.wd3.myworkdayjobs.com/Careers"
    try:
        handler, calls = workday_board([("Software Intern", "int1"), ("Engineering Co-op", "int1")], busy=2)
        ats.pooled = mock(handler)
        got = ats.workday(site, "Acme", None, set())
        check("a busy Workday site (429) is asked again instead of the board failing; its searches merged",
              sorted(j["title"] for j in got) == ["Engineering Co-op", "Software Intern"])

        # the internship filter finds what the fuzzy search misses ("Summer Analyst" has no "intern" in it)
        handler, calls = workday_board([("Software Intern", "int1"), ("Summer Analyst Program 2027", "int1"),
                                        ("Senior Engineer", "reg"), ("Global Trade Analyst", "intl")])
        ats.pooled = mock(handler)
        plan = {}
        got = ats.workday(site, "Acme", None, set(), plan=plan)
        check("a full Workday read also lists the board's internship filter, finding what its search misses",
              sorted(j["title"] for j in got) == ["Software Intern", "Summer Analyst Program 2027"])
        check("...takes the filter's intern values only (not 'International Associate')",
              plan["facets"] == {"workerSubType": ["int1"]} and plan["trusted"] and plan["full_by"] > time.time())
        calls["n"], calls["bodies"] = 0, []
        again = ats.workday(site, "Acme", None, set(), plan=plan)
        check("...and once the filter holds everything, later syncs read just the filter: one request, not six",
              calls["n"] == 1 and calls["bodies"][0]["appliedFacets"] and len(again) == 2)
        plan["full_by"] = time.time() - 1
        calls["n"] = 0
        ats.workday(site, "Acme", None, set(), plan=plan)
        check("...with a full read again about a day later", calls["n"] > 3)

        # an internship the filter doesn't tag: between full reads, the filter plus just the search that finds it
        handler, calls = workday_board([("Software Intern", "int1"), ("Data Intern", "reg")])
        ats.pooled = mock(handler)
        plan = {}
        got = ats.workday(site, "Acme", None, set(), plan=plan)
        calls["n"], calls["bodies"] = 0, []
        again = ats.workday(site, "Acme", None, set(), plan=plan)
        check("a board whose filter misses an internship: later syncs add only the search that finds it",
              len(got) == 2 and plan["trusted"] is False and len(plan["queries"]) == 1 and len(again) == 2
              and calls["n"] == 2 and {b["searchText"] for b in calls["bodies"]} == {"", plan["queries"][0]})

        # no filter at all: the fewest searches that find everything
        handler, calls = workday_board([("Software Intern", None), ("Engineering Co-op", None)])
        ats.pooled = mock(handler)
        plan = {}
        ats.workday(site, "Acme", None, set(), plan=plan)
        check("...and a board with no filter keeps only the searches that find something", sorted(plan["queries"])
              == ["co-op", "intern"])
    finally:
        ats.pooled, ats.time.sleep = real, real_sleep


def peek_checks():
    real, real_sleep = ats.pooled, ats.time.sleep
    ats.time.sleep = lambda s: None
    try:
        handler, calls = workday_board([(f"Intern {i}", "int1") for i in range(45)])
        ats.pooled = mock(handler)
        info = {}
        got = ats.workday("https://acme.wd3.myworkdayjobs.com/Careers", "Acme", None, set(),
                          plan={"facets": {"workerSubType": ["int1"]}, "queries": [], "full_by": 0}, peek=True, info=info)
        check("the quick check reads one Workday page (the newest postings come first) and says it's partial",
              calls["n"] == 1 and len(got) == 20 and info["partial"])
    finally:
        ats.pooled, ats.time.sleep = real, real_sleep
    stored = "https://jobs.lever.co/big/i1"
    big = [lever_post(f"b{i}", "Account Executive") for i in range(70)] + [lever_post("i1", "Data Co-op"),
                                                                           lever_post("i2", "Design Intern")]
    asked = []

    def handler(req):
        asked.append(req.url.path)
        if req.url.host == "jobs.lever.co":
            return httpx.Response(200, text=lever_page(big))
        return httpx.Response(200, json=next(p for p in big if p["id"] == req.url.path.rsplit("/", 1)[-1]))
    ats.pooled = mock(handler)
    try:
        info = {}
        got = ats.lever("big", "Acme", None, {stored}, peek=True, info=info)
    finally:
        ats.pooled = real
    check("...and on a big Lever board reads in full only the internships it hasn't stored",
          [j["title"] for j in got] == ["Design Intern"] and "/v0/postings/big/i1" not in asked and info["partial"])


def quiet_checks():
    now = db.now()
    boards = [{"ats": "greenhouse", "slug": f"firm{i}", "last_checked": now - 13 * 3600} for i in range(400)]
    due13 = sum(sync._quiet_due(b, now, 24) for b in boards)
    due37 = sum(sync._quiet_due({**b, "last_checked": now - 37 * 3600}, now, 24) for b in boards)
    due11 = sum(sync._quiet_due({**b, "last_checked": now - 11 * 3600}, now, 24) for b in boards)
    check("quiet boards come due spread across the day (some after 13h, none before 12h, all by 36h)",
          0 < due13 < 100 and due11 == 0 and due37 == 400)
    b = boards[7]
    check("...each at its own fixed interval", sync._quiet_due(b, now, 24) == sync._quiet_due(dict(b), now, 24))


def deactivate_checks():
    now = db.now()
    old = now - 3600
    rows = [("a1", "greenhouse:acme"), ("a2", "greenhouse:acme2"), ("w1", "workday:https://x.wd5.myworkdayjobs.com/Ext_1"),
            ("w2", "workday:https://x.wd5.myworkdayjobs.com/Ext_10"), ("s1", "simplify"), ("seen", "greenhouse:acme")]
    with db.tx() as c:
        for jid, src in rows:
            c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, first_seen, last_seen, active)"
                      " VALUES (?,?,?,?,?,?,?,?,1)", (jid, src, "Acme", "acme", "Intern", f"https://x.test/{jid}", old, old))
    closed = store.deactivate_missing(["greenhouse:acme", "workday:https://x.wd5.myworkdayjobs.com/Ext_1", "simplify"],
                                      {"seen"}, now)
    active = {r["id"] for r in db.rows("SELECT id FROM jobs WHERE active=1")}
    check("closed postings: only boards read this sync, matched exactly (acme isn't acme2, Ext_1 isn't Ext_10)",
          closed == 3 and active == {"a2", "w2", "seen"})


def fingerprint_checks():
    now = db.now()
    raw = {"source": "greenhouse:fp", "company": "Fingerprint Co", "title": "Software Engineer Intern",
           "url": "https://x.test/fp1", "locations": ["Toronto, ON"], "description": "Python and SQL. " * 30}
    j = store.normalize(dict(raw))
    store.upsert_jobs([j], now - 600)
    with db.tx() as c:  # read in full later (the application form): longer text and who it's open to
        c.execute("UPDATE jobs SET description=?, requirements=?, form_checked=1 WHERE id=?",
                  ("Longer text from the form. " * 40, db.dumps([{"kind": "citizen"}]), j["id"]))
        c.execute("UPDATE jobs SET active=0 WHERE id=?", (j["id"],))
    sent = []
    real = db.tx

    class Watched:  # the transaction, noting every posting written in full
        def __init__(self, c):
            self.c = c

        def __getattr__(self, name):
            return getattr(self.c, name)

        def executemany(self, sql, seq):
            seq = list(seq)
            sent.extend(seq)
            return self.c.executemany(sql, seq)

    @contextlib.contextmanager
    def watching():
        with real() as c:
            yield Watched(c)
    db.tx = watching
    try:
        new = store.upsert_jobs([store.normalize(dict(raw))], now)
    finally:
        db.tx = real
    row = db.row("SELECT * FROM jobs WHERE id=?", (j["id"],))
    check("a posting the board sends unchanged isn't sent again: only 'still open' is updated",
          not new and not sent and row["last_seen"] == now and row["active"] == 1)
    check("...so what was read in full later (longer text, who it's open to) stays",
          row["description"].startswith("Longer text") and row["form_checked"] == 1 and "citizen" in row["requirements"])
    raw2 = {**raw, "url": "https://x.test/fp2"}
    store.upsert_jobs([store.prepare(dict(raw2), store.stored_fingerprints())], now)
    real_norm, calls = store.normalize, []
    store.normalize = lambda r: calls.append(r) or real_norm(r)
    try:
        again = store.prepare(dict(raw2), store.stored_fingerprints())
        changed = store.prepare({**raw2, "description": "Rust and Go. " * 30}, store.stored_fingerprints())
    finally:
        store.normalize = real_norm
    check("a sync doesn't re-read (skills, flags, who it's open to) a posting the board sends as last time",
          again["unchanged"] and len(calls) == 1 and "Rust" in changed["skills"] and not changed.get("unchanged"))
    store.upsert_jobs([again], now + 9)
    check("...and saving it only records it's still open", db.row("SELECT last_seen FROM jobs WHERE id=?",
                                                                  (again["id"],))["last_seen"] == now + 9)
    store.upsert_jobs([store.normalize({**raw, "title": "Software Engineer Intern (Summer 2027)"})], now + 5)
    check("a posting that changed is written in full", db.row("SELECT title FROM jobs WHERE id=?", (j["id"],))["title"]
          == "Software Engineer Intern (Summer 2027)")


def lane_checks():
    now = db.now()
    with db.tx() as c:
        c.execute("INSERT INTO sync_runs (started_at, finished_at) VALUES (?,?)", (now - 7200, now - 7000))
        for i in range(8):
            c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('yc', ?, ?, ?, 1)",
                      (f"yc{i}", f"YC {i}", now - 3600))
        for i in range(20):
            c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('greenhouse', ?, ?,"
                      " ?, 1)", (f"gh{i}", f"GH {i}", now - 3600))
    yc_gate = threading.Event()
    finished = {}

    def task(co, known):
        if co["ats"] == "yc":
            yc_gate.wait(5)  # YC is slow today
        else:
            finished[co["slug"]] = time.time()
            if len(finished) == 20:
                yc_gate.set()  # every other board finished while YC was still waiting
        return [{"company": co["name"], "title": "Software Engineer Intern", "url": f"https://x.test/{co['slug']}",
                 "locations": ["Toronto, ON"]}]

    stand_ins = [(sync, "_company_task", task), (sync.simplify, "fetch", lambda repos: []),
                 (sync.linkedin, "fetch", lambda *a, **k: []), (sync.indeed, "available", lambda: False),
                 (sync.companies, "refresh_yc", lambda *a, **k: 0), (sync.events, "sync_feeds", lambda: (0, {})),
                 (sync, "enrich_top", lambda prof, n=60: 0)]
    from coopcoach import routes_email
    stand_ins.append((routes_email, "_start_email_sync", lambda pid: True))
    real = [(mod, name, getattr(mod, name)) for mod, name, _ in stand_ins]
    for mod, name, fn in stand_ins:
        setattr(mod, name, fn)
    with db.tx() as c:  # with an inbox connected: its check starts beside the sync, and the sync still finishes
        c.execute("INSERT INTO email_accounts (profile_id, kind, address, created_at) VALUES (1, 'imap', 'x@y.test', ?)", (db.now(),))
    try:
        t = time.time()
        out = sync.run(notify_users=False)
        took = time.time() - t
    finally:
        for mod, name, fn in real:
            setattr(mod, name, fn)
        with db.tx() as c:
            c.execute("DELETE FROM email_accounts WHERE address='x@y.test'")
    check("with an inbox connected, the sync starts its check and still records that it finished",
          out["email"] == {"started": 1} and out["seconds"] < 60
          and db.row("SELECT finished_at FROM sync_runs ORDER BY id DESC LIMIT 1")["finished_at"])
    check("slow YC pages never hold up the other boards (their own lane)", len(finished) == 20 and took < 4)
    check("every board is saved, with its status, in one batch",
          out["sources"]["yc:yc3"]["ok"] and db.row("SELECT COUNT(*) AS n FROM companies WHERE last_checked=?",
                                                     (out_started(out),))["n"] == 28)
    check("the report says how long each part took, and which boards were slowest",
          {"fetching", "saving", "events"} <= set(out["timings"]) and len(out["slowest"]) == 8
          and all("secs" in v for v in out["sources"].values() if v.get("ok")))


def stand_in(pairs):
    real = [(mod, name, getattr(mod, name)) for mod, name, _ in pairs]
    for mod, name, fn in pairs:
        setattr(mod, name, fn)
    return lambda: [setattr(mod, name, fn) for mod, name, fn in real]


BASICS = [(sync.simplify, "fetch", lambda repos: []), (sync.linkedin, "fetch", lambda *a, **k: []),
          (sync.indeed, "available", lambda: False), (sync.companies, "refresh_yc", lambda *a, **k: 0),
          (sync.events, "sync_feeds", lambda: (0, {})), (sync, "enrich_top", lambda prof, n=60: 0),
          (sync, "backfill_descriptions", lambda **k: 0)]


def posting(co, title="Software Engineer Intern"):
    return [{"company": co["name"], "title": title, "url": f"https://x.test/{co['ats']}/{co['slug']}",
             "locations": ["Toronto, ON"]}]


def staged_checks():
    now = db.now()
    with db.tx() as c:
        c.execute("UPDATE companies SET enabled=0")
        c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('ashby', 'early', 'Early',"
                  " ?, 1)", (now - 3600,))
        c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('workday',"
                  " 'https://late.wd1.myworkdayjobs.com/Ext', 'Late', ?, 1)", (now - 3600,))
    seen_early = {}

    def task(co, known):
        if co["ats"] == "workday":
            for _ in range(50):  # Workday is slow: are the other boards' postings already in?
                if db.row("SELECT 1 FROM jobs WHERE url='https://x.test/ashby/early'"):
                    seen_early["yes"] = True
                    break
                time.sleep(0.1)
            co["_plan"] = {"facets": {"workerSubType": ["i"]}, "trusted": True, "full_by": now + 9}
        return posting(co)

    def ai_score(prof):  # when AI scoring starts, the sync already shows as done
        seen_early["ai_running"] = sync.state["running"]
        seen_early["ai_finished"] = db.row("SELECT finished_at FROM sync_runs ORDER BY id DESC LIMIT 1")["finished_at"]
        return "scored 0"

    with db.tx() as c:
        c.execute("INSERT INTO profiles (name, profile_json, created_at, updated_at) VALUES (?,?,?,?)",
                  ("Alex", db.dumps({"skills": ["Python"]}), now, now))
    undo = stand_in(BASICS + [(sync, "_company_task", task), (sync.ai, "available", lambda: True),
                              (sync, "score_with_ai", ai_score)])
    cfg = config.settings().setdefault("sync", {})
    cfg["save_every_seconds"] = 0  # (15 in real use)
    try:
        sync.run(notify_users=False)
    finally:
        undo()
        cfg.pop("save_every_seconds")
    check("boards read so far are saved (and show) while slower ones are still being read", seen_early.get("yes"))
    check("the sync shows as done once postings are in: AI scoring and alerts finish after",
          seen_early.get("ai_running") is False and seen_early.get("ai_finished"))
    plan = db.row("SELECT plan FROM companies WHERE ats='workday'")["plan"]
    check("how a Workday board is best read is kept for the next sync", db.loads(plan, {}).get("trusted") is True)

    # a posting stored from the company's own board isn't overwritten by an aggregate list's copy
    j = store.normalize({"source": "ashby:early", "company": "Early", "title": "Software Engineer Intern",
                         "url": "https://x.test/ashby/early", "locations": ["Toronto, ON"]})
    store.upsert_jobs([store.normalize({"source": "simplify", "company": "Early", "title": "SWE Intern (list copy)",
                                        "url": "https://x.test/ashby/early", "locations": ["Remote"]})], now + 30)
    row = db.row("SELECT title, source, last_seen FROM jobs WHERE id=?", (j["id"],))
    check("a list's copy of a posting the company's board already gave only marks it still open",
          row["title"] == "Software Engineer Intern" and row["source"] == "ashby:early" and row["last_seen"] == now + 30)


def quick_checks():
    now = db.now()
    pid = db.row("SELECT id FROM profiles WHERE name='Alex'")["id"]
    with db.tx() as c:
        c.execute("UPDATE companies SET enabled=0")
        for slug, origin in (("mine", "you"), ("starred", None), ("applied", None), ("fresh", None), ("other", None)):
            c.execute("INSERT INTO companies (ats, slug, name, origin, last_checked, last_count) VALUES"
                      " ('greenhouse', ?, ?, ?, ?, 0)", (slug, f"Firm {slug}", origin, now - 600))
        c.execute("INSERT INTO favourites (profile_id, kind, key, label, created_at) VALUES (?, 'company', ?, ?, ?)",
                  (pid, "firm starred", "Firm starred", now))
        for slug, first in (("applied", now - 90 * 86400), ("fresh", now - 2 * 86400), ("other", now - 60 * 86400)):
            c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, first_seen, last_seen, active)"
                      " VALUES (?,?,?,?,?,?,?,?,?)", (f"q{slug}", f"greenhouse:{slug}", f"Firm {slug}", f"firm {slug}",
                                                      "Intern", f"https://x.test/q/{slug}", first, first,
                                                      int(slug != "other")))  # "other": nothing open, long quiet
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at) VALUES (?, 'qapplied', 'applied',"
                  " ?)", (pid, now))
    boards = sync.priority_boards()
    check("the quick check covers boards you added, starred, applied to, behind your matches, and ones that just"
          " posted; not the rest",
          {"greenhouse:mine", "greenhouse:starred", "greenhouse:applied", "greenhouse:fresh"} <= boards
          and "greenhouse:other" not in boards)
    read, alerted = [], []
    undo = stand_in(BASICS + [(sync, "_company_task", lambda co, known, peek=False: read.append((co["slug"], peek))
                                or posting(co, "Machine Learning Intern")),
                              (sync, "notify_profiles", lambda ids, started: alerted.append(set(ids)) or {}),
        (autopilot, "on_sync", lambda ids: None)])
    try:
        out = sync.run_quick()
    finally:
        undo()
    check("...reads only those, and a new internship there is alerted right away",
          sorted(read) == [(b, True) for b in ("applied", "fresh", "mine", "starred")] and alerted
          and len(alerted[0]) == 4
          and out["kind"] == "quick")
    # a quick read that saw only the newest postings closes nothing and leaves the board's numbers alone
    def partial(co, known, peek=False):
        co["_partial"] = True
        return []
    with db.tx() as c:  # (the full read above closed it: that board's list no longer had it)
        c.execute("UPDATE jobs SET active=1 WHERE id='qapplied'")
    before = db.row("SELECT last_count, last_checked FROM companies WHERE slug='applied'")
    undo = stand_in(BASICS + [(sync, "_company_task", partial), (autopilot, "on_sync", lambda ids: None)])
    try:
        sync.run_quick()
    finally:
        undo()
    after = db.row("SELECT last_count, last_checked FROM companies WHERE slug='applied'")
    check("...and where it saw only the newest postings, closes nothing and leaves the board's numbers",
          db.row("SELECT active FROM jobs WHERE id='qapplied'")["active"] == 1 and before == after)
    check("...without counting as the last full sync", sync.last_run(full_only=True)["id"] !=
          sync.last_run()["id"] and sync.last_run()["report"]["kind"] == "quick")


def retry_checks():
    tries = []

    def flaky(slug, name, tier, known, info=None):
        tries.append(slug)
        if len(tries) == 1:
            raise httpx.ReadTimeout("slow")
        return []
    real = ats.FETCHERS["ashby"]
    ats.FETCHERS["ashby"] = flaky
    try:
        out = sync._company_task({"ats": "ashby", "slug": "slowco", "name": "SlowCo", "tier": None}, set())
    finally:
        ats.FETCHERS["ashby"] = real
    check("a board that times out is read once more before it counts as failed", out == [] and len(tries) == 2)


def backfill_checks():
    """Workday postings (title only on the board) get their full text read after a sync, with skills worked out."""
    now = db.now()
    with db.tx() as c:
        for i in range(3):
            c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, first_seen, last_seen, active)"
                      " VALUES (?,?,?,?,?,?,?,?,1)", (f"wd{i}", "workday:https://acme.wd5.myworkdayjobs.com/Ext", "Acme",
                                                      "acme", "Software Intern", f"https://acme.wd5.myworkdayjobs.com/Ext/job/{i}",
                                                      now, now))
    real = sync.jobdetail.fetch_description
    sync.jobdetail.fetch_description = lambda url: "" if url.endswith("/2") else "Build services in Python and SQL. " * 12
    try:
        n = sync.backfill_descriptions(limit=10, seconds=30)
    finally:
        sync.jobdetail.fetch_description = real
    row = db.row("SELECT description, skills FROM jobs WHERE id='wd0'")
    check("Workday postings get their full text read after a sync, with their skills worked out",
          n >= 2 and "Python" in row["description"] and "Python" in db.loads(row["skills"], [])
          and not db.row("SELECT description FROM jobs WHERE id='wd2'")["description"])


def worker_checks():
    """Real worker processes (they can't be stood in for): boards on a closed port fail fast, and each is reported."""
    now = db.now()
    with db.tx() as c:
        c.execute("UPDATE companies SET enabled=0")
        for i in range(3):
            c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('successfactors', ?,"
                      " ?, ?, 1)", (f"127.0.0.1:{9 + i}", f"Closed {i}", now - 3600))
    cfg = config.settings().setdefault("sync", {})
    cfg["processes_from"], os.environ["COOPCOACH_SYNC_PROCESSES"] = 1, "2"
    undo = stand_in(BASICS)
    try:
        t = time.time()
        out = sync.run(notify_users=False)
        took = time.time() - t
    finally:
        undo()
        cfg.pop("processes_from")
        os.environ["COOPCOACH_SYNC_PROCESSES"] = "1"
    boards = {k: v for k, v in out["sources"].items() if k.startswith("successfactors:")}
    check("company boards are read in worker processes, each reported back (here: failing fast on a closed port)",
          len(boards) == 3 and all(not v["ok"] and "not read" not in v["error"] for v in boards.values())
          and took < 30)


def first_filter_checks():
    now = db.now()
    with db.tx() as c:
        c.execute("UPDATE companies SET enabled=0")
        c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('workday',"
                  " 'https://bigco.wd5.myworkdayjobs.com/Ext', 'BigCo', ?, 3)", (now - 3600,))
    listed = ["Software Intern (long open, missed by search)"]

    def fake_workday(site, name, tier, known, max_results=120, plan=None, **kw):
        plan.update(facets={"workerSubType": ["i"]}, trusted=True, full_by=int(time.time()) + 86400)
        return [{"source": "workday", "company": name, "title": t, "url": f"https://bigco.test/{i}",
                 "locations": ["Toronto, ON"]} for i, t in enumerate(listed)]

    alerted = []
    real = ats.FETCHERS["workday"]
    ats.FETCHERS["workday"] = fake_workday
    undo = stand_in(BASICS + [(sync, "notify_profiles", lambda ids, started: alerted.append(set(ids)) or {}),
                              (autopilot, "on_sync", lambda ids: None)])
    try:
        sync.run()
        listed.append("Data Intern (just posted)")
        sync.run()
    finally:
        ats.FETCHERS["workday"] = real
        undo()
    first, second = alerted
    check("internships a board's filter turns up the first time were open all along: saved, but no alert",
          not first and db.row("SELECT 1 FROM jobs WHERE url='https://bigco.test/0'"))
    check("...and the next new one there alerts as usual", second == {store.job_id("https://bigco.test/1")})


def out_started(out):
    return db.row("SELECT started_at FROM sync_runs ORDER BY id DESC LIMIT 1")["started_at"]


def shared_load_checks():
    """Several requests at once while the postings aren't in memory (the page opening right after a start)."""
    loads, real = [], store._active_rows
    def slow():
        loads.append(1)
        time.sleep(0.2)
        return real()
    store._active_rows = slow
    store.invalidate_cache()
    got = []
    try:
        ts = [threading.Thread(target=lambda: got.append(store.collapsed_jobs())) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    finally:
        store._active_rows = real
    check("requests at the same moment share one load of the postings", len(loads) == 1 and len(got) == 6
          and all(g is got[0] for g in got))


def page_doesnt_wait_checks():
    """On Supabase over a slow connection, the first state answers before the postings are in."""
    from coopcoach import server
    release, real, real_pg = threading.Event(), store._active_rows, db.is_postgres
    rows = real()  # read now: the stand-in Postgres below is only for choosing not to wait
    store._active_rows = lambda: (release.wait(10), [dict(r) for r in rows])[1]
    db.is_postgres = lambda: True
    store.invalidate_cache()
    try:
        t = time.time()
        first = server._term_options()
        quick = time.time() - t < 1
        release.set()
        store.collapsed_jobs()  # waits for the background load
        check("the page's first state doesn't wait for the postings to download", first == [] and quick)
    finally:
        store._active_rows, db.is_postgres = real, real_pg
    check("...and once they're in, its terms are there", store.jobs_ready() and isinstance(server._term_options(), list))


def offline_checks():
    """The internet drops with the data on Supabase: a message, not a crash."""
    import psycopg
    from fastapi.testclient import TestClient

    from coopcoach import server
    real = sync.last_run

    def down(**_):
        raise psycopg.OperationalError("could not receive data from server")
    sync.last_run = down
    try:
        r = TestClient(server.app, base_url="http://localhost", raise_server_exceptions=False).get("/api/state")
    finally:
        sync.last_run = real
    check("with the database unreachable, the page's state says so (503, and in words) instead of crashing",
          r.status_code == 503 and r.json()["offline"] and "internet" in r.json()["detail"])


def main():
    db.init()
    lever_checks()
    workday_checks()
    peek_checks()
    quiet_checks()
    deactivate_checks()
    fingerprint_checks()
    lane_checks()
    staged_checks()
    quick_checks()
    first_filter_checks()
    retry_checks()
    backfill_checks()
    worker_checks()
    shared_load_checks()
    page_doesnt_wait_checks()
    offline_checks()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
