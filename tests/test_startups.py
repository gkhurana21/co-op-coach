"""The Startups in Canada track: which startups are in (YC companies located in Canada, the Waterloo & Toronto list, founders
you know; not listed companies, giants or a near name), ranked by how fast they hire (new internships in 30 days, roles
on their boards against a month ago), the filters, Watch, tracking hiring YC startups, each sync noting role counts,
the chat tool and the API. Also: an empty table left in an old layout is made again, and the page's scripts load
together.

Run: .venv/bin/python -m tests.test_startups
"""

import os
import re
import shutil
import subprocess
import tempfile
import time

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-startups-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, auth, companies, contacts, db, discover, startups, store, sync  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}
DAY = 86400


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


YC = [
    {"name": "Lumen Robotics", "slug": "lumen-robotics", "batch": "Winter 2025", "team_size": 14, "status": "Active",
     "is_hiring": True, "one_liner": "Robots for warehouses", "locations": "Waterloo, ON, Canada", "industry": "Industrials",
     "logo": "https://example.com/lumen.png", "website": "https://lumen.example"},
    {"name": "Maple Ledger", "slug": "maple-ledger", "batch": "Summer 2024", "team_size": 6, "status": "Active",
     "is_hiring": True, "one_liner": "Bookkeeping for small business", "locations": "Toronto, ON, Canada; Remote"},
    {"name": "Quiet Co", "slug": "quiet-co", "batch": "Winter 2021", "team_size": 30, "status": "Active",
     "is_hiring": False, "one_liner": "Not hiring right now", "locations": "Kitchener, ON, Canada"},
    {"name": "Old Listed", "slug": "old-listed", "batch": "Summer 2012", "team_size": 400, "status": "Public",
     "is_hiring": True, "locations": "Toronto, ON, Canada"},
    {"name": "Giant Corp", "slug": "giant", "batch": "Winter 2010", "team_size": 7000, "status": "Active",
     "is_hiring": True, "locations": "Toronto, ON, Canada"},
    {"name": "Bay Startup", "slug": "bay-startup", "batch": "Fall 2025", "team_size": 5, "status": "Active",
     "is_hiring": True, "locations": "San Francisco, CA, USA"},
    {"name": "Yotta", "slug": "yotta", "batch": "Summer 2020", "team_size": 8, "status": "Active", "is_hiring": True,
     "locations": "New York, NY, USA"},
]


def posting(company, title, url, locations=("Waterloo, ON",), first_seen=None, posted_at=None):
    raw = {"source": "greenhouse:x", "company": company, "title": title, "url": url, "locations": list(locations),
           "description": "Build things with Python. " * 20}
    j = store.normalize(raw)
    store.upsert_jobs([j], first_seen or db.now())
    if posted_at:
        with db.tx() as c:
            c.execute("UPDATE jobs SET posted_at=? WHERE id=?", (posted_at, j["id"]))
    return j


def names(d):
    return [r["name"] for r in d["startups"]]


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, created_at, updated_at) VALUES (?,?,?,?)",
                        ("Alex", db.dumps({"university": "University of Waterloo", "skills": ["Python"]}), now,
                         now)).lastrowid
    real = (companies.yc_all, companies.yc_lookup)
    by_key = {companies.company_key(c["name"]): c for c in YC}
    companies.yc_all = lambda: [dict(c) for c in YC]
    companies.yc_lookup = lambda name: by_key.get(companies.company_key(name)) or (
        by_key["yotta"] if companies.company_key(name).startswith("yotta") else None)
    try:
        run(pid, now)
    finally:
        companies.yc_all, companies.yc_lookup = real
    stale_tables()
    scripts_load_together()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


def run(pid, now):
    check("where a startup is: Waterloo Region, Toronto, elsewhere in Canada, or not",
          [startups.area_of(x) for x in ("Kitchener, ON, Canada", "Toronto, ON, Canada; Remote", "Montréal, QC",
                                         "San Francisco, CA, USA")] == ["waterloo", "toronto", "canada", "elsewhere"])
    # hiring: three new internships at Lumen this month, one old one at Maple Ledger
    for i in range(3):
        posting("Lumen Robotics", f"Robotics Software Intern {i}", f"https://x.test/lumen/{i}")
    posting("Maple Ledger", "Software Engineering Intern", "https://x.test/maple/1", ("Toronto, ON",),
            first_seen=now - 90 * DAY, posted_at=now - 90 * DAY)
    posting("Yotta Labs", "Data Intern", "https://x.test/yotta/1", ("Toronto, ON",))  # a near name: not YC's Yotta
    contacts.import_linkedin(pid, "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
                                  "Nia,Patel,https://www.linkedin.com/in/nia,,Quiet Co,Co-Founder & CEO,01 Feb 2026\n")
    store.refresh_scores()

    d = startups.feed(pid, "all")
    got = names(d)
    check("YC companies based in Canada are in; listed companies, giants and ones based elsewhere aren't",
          {"Lumen Robotics", "Maple Ledger", "Quiet Co"} <= set(got) and not {"Old Listed", "Giant Corp",
                                                                            "Bay Startup"} & set(got))
    check("...and a posting from a company with a near name isn't taken for a YC startup", "Yotta Labs" not in got)
    check("the Waterloo & Toronto list is in too", "Wealthsimple" in got and "Magnet Forensics" in got)
    lumen = next(r for r in d["startups"] if r["name"] == "Lumen Robotics")
    check("fastest-hiring first: new internships this month count most", got[0] == "Lumen Robotics"
          and lumen["new30"] == 3 and lumen["pace"] == "fast" and lumen["open_to_you"] == 3)
    check("each startup has its facts: YC batch, team, what it does, logo, alumni search",
          lumen["yc"] == "Winter 2025" and lumen["team"] == 14 and lumen["one_liner"] == "Robots for warehouses"
          and lumen["logo"] and lumen["alumni"].startswith("https://www.linkedin.com/school/uwaterloo/"))
    quiet = next(r for r in d["startups"] if r["name"] == "Quiet Co")
    check("a founder you know shows on their startup", quiet["founders"] == ["Nia Patel"] and quiet["pace"] == "quiet")

    w = startups.feed(pid, "waterloo")
    check("the Waterloo filter keeps Waterloo Region only, and each area is counted",
          "Lumen Robotics" in names(w) and "Maple Ledger" not in names(w) and w["counts"]["toronto"] >= 1
          and w["counts"]["waterloo"] == len(w["startups"]))
    check("'Hiring now' drops the quiet ones; 'People you know' keeps founders' startups",
          "Quiet Co" not in names(startups.feed(pid, "all", hiring=True))
          and names(startups.feed(pid, "all", known=True)) == ["Quiet Co"])
    check("YC only, and search by what they do", "Wealthsimple" not in names(startups.feed(pid, "all", yc_only=True))
          and names(startups.feed(pid, "all", q="bookkeeping")) == ["Maple Ledger"])
    from coopcoach import routes_docs
    quick = routes_docs._rules_reply(pid, "Any startups hiring?")
    check("without an AI, the chat's quick answers list Waterloo startups hiring, fastest first",
          quick.splitlines()[1].startswith("• Lumen Robotics: Robots for warehouses (3 open)"))

    # the same internship on two boards is one; one only in the US isn't a Canadian opening
    raw = {"source": "lever:lumen", "company": "Lumen Robotics", "title": "Robotics Software Intern 0",
           "url": "https://jobs.lever.co/lumen/0", "locations": ["Waterloo, ON"], "description": "Python. " * 50}
    store.upsert_jobs([store.normalize(raw)], db.now())
    posting("Lumen Robotics", "Hardware Intern (US)", "https://x.test/lumen/us", ("Austin, TX, USA",))
    store.invalidate_cache()
    lumen = next(r for r in startups.feed(pid, "all")["startups"] if r["name"] == "Lumen Robotics")
    check("new this month counts each internship once, however many boards list it, and only Canadian ones",
          lumen["new30"] == 3)

    # how fast boards grow: role counts a sync noted a month apart
    day = lambda t: time.strftime("%Y-%m-%d", time.localtime(t))  # noqa: E731
    with db.tx() as c:
        c.executemany("INSERT INTO board_roles (board, day, roles, interns) VALUES (?,?,?,?)",
                      [("yc:maple-ledger", day(now - 29 * DAY), 4, 0), ("yc:maple-ledger", day(now), 16, 1)])
    maple = next(r for r in startups.feed(pid, "toronto")["startups"] if r["name"] == "Maple Ledger")
    check("roles on its board against a month ago: +12, so it's hiring fast", maple["roles"] == 16
          and maple["growth"] == 12 and maple["pace"] == "fast")

    # watching
    out = startups.watch(pid, "Maple Ledger")
    row = db.row("SELECT * FROM companies WHERE ats='yc' AND slug='maple-ledger'")
    check("Watch has the sync read its YC jobs page", out.get("ok") and row and row["origin"] == "startups"
          and next(r for r in startups.feed(pid, "toronto")["startups"] if r["name"] == "Maple Ledger")["watched"])
    check("...and a startup without a known board says so", "error" in startups.watch(pid, "Bridgit"))
    with db.tx() as c:
        c.execute("UPDATE companies SET removed=1 WHERE ats='yc' AND slug='maple-ledger'")
    n = discover.track_hiring_yc()
    tracked = {r["slug"] for r in db.rows("SELECT slug FROM companies WHERE ats='yc' AND removed=0")}
    check("each sync reads every YC company that says it's hiring (not quiet ones, giants, or ones you removed)",
          n == 3 and tracked == {"lumen-robotics", "bay-startup", "yotta"})

    # a sync notes every board's role count
    with db.tx() as c:
        c.execute("UPDATE companies SET enabled=0")
        c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('ashby', 'growco', 'GrowCo',"
                  " ?, 1)", (now - 3600,))
        c.execute("INSERT INTO sync_runs (started_at, finished_at) VALUES (?,?)", (now - 7200, now - 7000))

    def task(co, known, peek=False):
        co["_roles"] = 42
        return [{"company": "GrowCo", "title": "Software Intern", "url": "https://x.test/growco/1", "locations": ["Toronto"]}]
    stand_ins = [(sync, "_company_task", task), (sync.simplify, "fetch", lambda repos: []),
                 (sync.linkedin, "fetch", lambda *a, **k: []), (sync.indeed, "available", lambda: False),
                 (sync.companies, "refresh_yc", lambda *a, **k: 0), (sync.events, "sync_feeds", lambda: (0, {})),
                 (sync, "enrich_top", lambda prof, n=60: 0)]
    keep = [(m, k, getattr(m, k)) for m, k, _ in stand_ins]
    for m, k, f in stand_ins:
        setattr(m, k, f)
    try:
        sync.run(notify_users=False)
    finally:
        for m, k, f in keep:
            setattr(m, k, f)
    r = db.row("SELECT * FROM board_roles WHERE board='ashby:growco'")
    check("each sync notes how many roles every board lists (and its internships)", r and r["roles"] == 42
          and r["interns"] == 1 and r["day"] == day(time.time()))

    res = agent.call(pid, "startup_feed", {"area": "waterloo"}, source="chat")
    check("the chat finds Waterloo startups hiring fastest", res["startups"][0]["name"] == "Lumen Robotics"
          and res["startups"][0]["new_internships_30_days"] == 3)
    with TestClient(app, base_url="http://localhost") as cl:
        api = cl.get(f"/api/startups?profile_id={pid}&area=toronto").json()
        check("the API answers with the feed", "Maple Ledger" in names(api) and api["area"] == "toronto")
        check("...refuses an unknown area", cl.get(f"/api/startups?profile_id={pid}&area=mars").status_code == 400)
        w = cl.post("/api/startups/watch", json={"profile_id": pid, "name": "Wealthsimple"})
        check("...and Watch through it", w.status_code == 200 and db.row(
            "SELECT 1 FROM companies WHERE ats='ashby' AND slug='wealthsimple' AND removed=0"))
        auth.set_passcode(pid, "maple-1234")
        check("a locked profile's startups stay locked", cl.get(f"/api/startups?profile_id={pid}").status_code == 423)
        auth.clear_passcode(pid)


def stale_tables():
    """A table an older version made with other columns: empty, it's made again; with data, it's left alone."""
    with db.tx() as c:
        c.execute("DROP TABLE niche_firms")
        c.execute("CREATE TABLE niche_firms (id INTEGER PRIMARY KEY, niche_id INTEGER, company_key TEXT)")
        c.execute("DROP TABLE board_roles")
        c.execute("CREATE TABLE board_roles (board TEXT, roles INTEGER)")
        c.execute("INSERT INTO board_roles VALUES ('yc:x', 3)")
    db.meta_set("schema_shape", "an older version")  # (Supabase skips the check when the tables are as they were last time)
    db.init()
    cols = ({r["column_name"] for r in db.rows("SELECT column_name FROM information_schema.columns WHERE"
                                                " table_schema=current_schema() AND table_name='niche_firms'")}
            if db.is_postgres() else {r["name"] for r in db.conn().execute("PRAGMA table_info(niche_firms)")})
    check("an empty table left in an old layout is made again with today's columns",
          {"profile_id", "key", "info", "verified"} <= cols)
    check("...but one holding data is left as it is", db.row("SELECT roles FROM board_roles")["roles"] == 3)


def scripts_load_together():
    """The page loads its scripts one after another into one page: two of them declaring the same name stops every
    script after it (a clash once broke the People tab)."""
    node = shutil.which("node")
    if not node:
        print("SKIP the page's scripts together: node isn't installed")
        return
    static = os.path.join(os.path.dirname(store.__file__), "static")
    html = open(os.path.join(static, "index.html"), encoding="utf-8").read()
    srcs = re.findall(r'<script src="/static/([\w.-]+\.js)"', html)
    with tempfile.TemporaryDirectory() as tmp:
        allf = os.path.join(tmp, "all.js")
        with open(allf, "w", encoding="utf-8") as fh:
            for s in srcs:
                fh.write(open(os.path.join(static, s), encoding="utf-8").read() + "\n;\n")
        run = subprocess.run([node, "--check", allf], capture_output=True, text=True, timeout=60)
    check(f"the page's {len(srcs)} scripts load together (no name declared twice){' ' + run.stderr[-300:] if run.returncode else ''}",
          run.returncode == 0 and "tracks.js" in srcs)


if __name__ == "__main__":
    main()
