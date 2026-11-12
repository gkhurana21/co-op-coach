"""MATCH: what the market asks for, the resume against it, and niches (the firm lists behind employer tracks: the quant
list, following, finding firms you're missing with a live check, opening dates and reminders), through the functions,
the API and the chat tools. No AI and no network: the AI and the live checks are stand-ins. The pages themselves are
tracks now (tests/test_tracks.py tests them, tracks.js included).

Run: .venv/bin/python -m tests.test_match
"""

import datetime
import json
import os
import tempfile

TMP = tempfile.mkdtemp(prefix="coopcoach-matchtest-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent",
                  COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, ai, autopilot, db, market, niches, notify, routes_docs, server, store, sync  # noqa: E402
from coopcoach.textutil import classify_category  # noqa: E402
from coopcoach.tools import explore as explore_tools  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


DESC = {
    "swe": "We build trading systems. You will write C++ and Python on Linux, work with Kubernetes, and use SQL. " * 4,
    "web": "Join our web team. React, TypeScript, Kubernetes and SQL. You will ship features to millions of users. " * 4,
    "data": "Analyze data with Python and SQL. Statistics, regression and dashboards in Tableau for the business. " * 4,
}


def posting(company: str, title: str, url: str, desc: str = "", **kw) -> dict:
    j = store.normalize({"source": kw.pop("source", "greenhouse:test"), "company": company, "title": title, "url": url,
                         "locations": kw.pop("locations", ["Toronto, ON"]), "description": desc,
                         "terms": kw.pop("terms", ["Summer 2027"]), **kw})
    return j


def firm_by_key(d: dict, key: str) -> dict | None:
    return next((f for s in d["segments"] for f in s["firms"] if f["key"] == key), None)


def main():
    db.init()
    niches._catalog()["quant"].setdefault("season", "Summer 2027")
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES (?,?,?,?,?)",
                        ("Tester", now - 86400, now - 86400,
                         json.dumps({"locations": ["Canada", "Remote", "US"], "categories": ["Software"]}),
                         json.dumps({"name": "Alex Chen", "skills": ["Python", "React", "SQL", "Go"],
                                     "target_categories": ["Software"]}))).lastrowid
    jobs = [
        posting("Shopify", "Software Engineer Intern", "https://x.test/1", DESC["web"], posted_at=now - 2 * 86400),
        posting("Wealthsimple", "Software Developer Intern", "https://x.test/2", DESC["web"]),
        posting("Ramp", "Software Engineer Intern, Platform", "https://x.test/3", DESC["swe"]),
        posting("Cohere", "Backend Software Engineer Intern", "https://x.test/4", DESC["swe"]),
        posting("Jane Street", "Software Engineer Intern", "https://x.test/5", DESC["swe"], locations=["New York, NY"]),
        posting("Citadel Securities", "Quantitative Trader Intern", "https://x.test/6", DESC["swe"],
                locations=["Chicago, IL"]),
        posting("Citadel", "Quantitative Research Intern", "https://x.test/7", DESC["data"], locations=["New York, NY"]),
        posting("Susquehanna International Group (SIG)", "Quantitative Trader Intern", "https://x.test/8", DESC["swe"],
                locations=["Bala Cynwyd, PA"]),
        posting("Akuna Capital University", "Software Engineer Intern - Python", "https://x.test/9", DESC["swe"],
                locations=["Chicago, IL"]),
        posting("Talos", "Quantitative Analyst Intern", "https://x.test/10", DESC["data"], locations=["New York, NY"]),
        posting("QuEra Computing", "Quantum Error Correction Research Intern", "https://x.test/11", ""),
        posting("Shopify", "Senior Staff Engineer", "https://x.test/12", DESC["web"]),
        posting("Old Mission", "Software Engineer Intern (US citizens only)", "https://x.test/13",
                "Applicants must be US citizens. " + DESC["swe"], locations=["Chicago, IL"]),
    ]
    store.upsert_jobs(jobs, now - 3600)
    ids = {j["company"] + "|" + j["title"]: j["id"] for j in jobs}

    # --- M: the market ------------------------------------------------------------------------
    m = market.summary(pid)
    names = [s["skill"] for s in m["skills"]]
    check("the market is the person's target role (Software), internships they can apply to",
          m["label"] == "Software" and all("Senior" not in j["title"] for j in m["jobs"]) and
          not any(j["company"] == "Old Mission" for j in m["jobs"]) and m["size"] >= 4)
    check("skills are counted across the sample, most asked first",
          names[:1] in (["SQL"], ["Kubernetes"], ["Docker/Kubernetes"], ["C++"], ["Python"]) and "Docker/Kubernetes" in names)
    k8s = next(s for s in m["skills"] if s["skill"] == "Docker/Kubernetes")
    check("...and say how postings write them ('Kubernetes', not 'Docker/Kubernetes')", k8s["forms"][:1] == ["Kubernetes"])
    check("gaps are skills a big share asks for that the person doesn't have",
          "Docker/Kubernetes" in [g["skill"] for g in m["gaps"]] and "SQL" not in [g["skill"] for g in m["gaps"]])
    q = market.summary(pid, "Quant")
    check("another market can be read (Quant)", q["label"] == "Quant" and all(j["company"] != "Shopify" for j in q["jobs"]))
    check("'Quantum' isn't a quant role", classify_category("Quantum Error Correction Research Intern") != "Quant")

    pat = market.pattern(pid)
    # (this used to expect "about 20", which the panel never waited for: it fills in from 5 picks)
    check("under 5 picks, the pattern says it needs 5", pat["count"] < 5 and "5 postings" in pat.get("note", ""))
    for key in ("Citadel Securities|Quantitative Trader Intern", "Citadel|Quantitative Research Intern",
                "Susquehanna International Group (SIG)|Quantitative Trader Intern", "Talos|Quantitative Analyst Intern",
                "Jane Street|Software Engineer Intern"):
        server.upsert_application({"profile_id": pid, "job_id": ids[key], "status": "applied"})
    pat = market.pattern(pid)
    kinds = {i["kind"] for i in pat["insights"]}
    check("after enough picks: 'you target Software but mostly apply to Quant'", "category" in kinds and
          any(i.get("category") == "Quant" for i in pat["insights"]))
    check("...and 'mostly quant firms: explore the niche'", "tier" in kinds)

    # --- A: the resume against it ---------------------------------------------------------------
    resume = {"header": {"name": "Alex Chen"},
              "experience": [{"title": "Software Engineer Intern", "company": "Acme", "dates": "May 2025 - Aug 2025",
                              "bullets": ["Built a pricing service in C++ with a Python test harness"]}],
              "projects": [{"name": "Tradebot", "tech": "Go", "bullets": ["Backtests on minute data"]}],
              "skills": [{"label": "Languages", "items": "Python, Go,"}, {"label": "Tools", "items": "React, Git"}]}
    routes_docs._save_doc(pid, "resume_master", db.dumps(resume), "Master resume", by="you")
    f = market.fit(pid)
    st = {r["skill"]: r for r in f["skills"]}
    check("in Skills: listed", st["Python"]["status"] == "listed")
    check("in a bullet but not Skills: surface it, and say where", st["C++"]["status"] == "surface" and
          "Acme" in st["C++"]["where"])
    check("not anywhere: missing (confirm or plan a project)", st["Docker/Kubernetes"]["status"] == "missing" and
          st["Docker/Kubernetes"]["add_as"] == "Kubernetes")
    check("SQL, which they said they know but the resume doesn't show: 'known'", st["SQL"]["status"] == "known")
    check("'Go' written as a skills item counts (free text needs more context)", "Go" in market._listed(market.rs.normalize(resume)))
    check("coverage is a percentage of what the market asks", 0 < f["coverage"] < 100)

    r1, line = market.add_skill(resume, "C++", "C++")
    check("a language goes on the languages line (the stray comma is tidied)",
          line == "Languages" and r1["skills"][0]["items"] == "Python, Go, C++")
    r2, line = market.add_skill(r1, "Kubernetes", "Docker/Kubernetes")
    check("a tool goes on the tools line", line == "Tools" and r2["skills"][1]["items"].endswith("Kubernetes"))
    r3, _ = market.add_skill(r2, "kubernetes", "Docker/Kubernetes")
    check("adding it twice changes nothing", r3["skills"] == r2["skills"])
    r4, line = market.add_skill({"header": {}}, "Rust", "Rust")
    check("no skills section yet: one is started", line == "Languages" and r4["skills"] == [{"label": "Languages", "items": "Rust"}])
    f2 = market.fit(pid, r2)
    check("after adding, the fit shows them as listed", {r["skill"]: r["status"] for r in f2["skills"]}.get("C++") == "listed")

    # one click only ever writes a skill's name, never a plain word the skill patterns happen to match
    pm = "Partner with stakeholders across teams. Portfolio reviews every week. Containers for every service. " * 4
    store.upsert_jobs([posting(c, "Product Manager Intern", f"https://x.test/7{i}", pm)
                       for i, c in enumerate(["Acme Apps", "Bright Labs", "Cobalt Tools"])], now - 3600)
    pm_resume = {"header": {"name": "Alex Chen"}, "skills": [{"label": "Tools", "items": "Jira"}],
                 "experience": [{"title": "Product Intern", "company": "Acme", "bullets": [
                     "Ran weekly reviews with stakeholders of the product portfolio and moved services to containers"]}]}
    pf = {r["skill"]: r for r in market.fit(pid, pm_resume, "Product/Design")["skills"]}
    check("plain words ('stakeholders', 'portfolio', 'containers') aren't read as skills at all",
          not {"Product Sense", "Finance", "Docker/Kubernetes"} & set(pf))
    # and if a pattern ever matches a plain word again, one click still writes the skill's name, not the word
    check("one-click Add to Skills never writes 'Stakeholders', 'Portfolio' or 'Containers'",
          market._names("Product Sense", [("Stakeholders", True)]) == [] and
          market._names("Finance", [("Portfolio", True), ("Equity", True)]) == [] and
          market._names("Docker/Kubernetes", [("Containers", True), ("Kubernetes", True)]) == ["Kubernetes"])
    check("...it writes the skill's own name instead ('Product Sense', 'Finance', 'Docker')",
          [market._short(s) for s in ("Product Sense", "Finance", "Docker/Kubernetes")] ==
          ["Product Sense", "Finance", "Docker"])
    check("...while a tool's name postings write like one is kept ('Kubernetes')", st["Docker/Kubernetes"]["add_as"] == "Kubernetes")

    plan = market.project_plan(pid, "C++")
    check("a project plan without AI: steps and a resume line template with placeholders",
          plan["by"] == "rules" and len(plan["plan"]["steps"]) == 4 and "[" in plan["plan"]["resume_line"])
    check("...saved as a document", db.row("SELECT 1 FROM documents WHERE id=? AND kind='plan'", (plan["doc_id"],)))
    other = market.project_plan(pid, "Figma")
    check("any skill gets a plan", other["plan"]["steps"] and "Figma" in other["plan"]["title"] + other["plan"]["why"])

    # --- T: the quant niche ---------------------------------------------------------------------
    quant = niches.get(pid, "quant")
    firms = [f for s in quant["segments"] for f in s["firms"]]
    keys = [f["key"] for f in firms]
    check("the quant niche ships 12 segments and 140+ firms", len(quant["segments"]) == 12 and len(firms) >= 140)
    check("every firm is in the list once", len(keys) == len(set(keys)))
    boards = [f["board"] for f in firms if f["board"]]
    check("every board names a reader Co-op Coach has", all(b.split(":", 1)[0] in niches.ats.FETCHERS for b in boards))
    check("opening dates are real dates from the last season (2026)",
          all(datetime.date.fromisoformat(f["opened"]).year == 2026 for f in firms if f["opened"]))
    check("a Canadian segment, and Canadian offices flagged elsewhere",
          any(s["id"] == "canada" for s in quant["segments"]) and
          next(f for f in firms if f["name"] == "DRW")["canada"] == "Montreal")
    check("careers links are web links", all(f["careers"].startswith("http") for f in firms if f["careers"]))

    mt = niches.Matcher(firms)
    name = lambda k: (mt.match(k) or {}).get("name")  # noqa: E731
    check("'Citadel Securities' is the market maker, not the hedge fund",
          name("citadel securities") == "Citadel Securities" and name("citadel") == "Citadel")
    check("'Susquehanna International Group (SIG)' is SIG", name("susquehanna international") ==
          "Susquehanna International Group (SIG)" and name("sig") == "Susquehanna International Group (SIG)")
    check("'Akuna Capital University' and 'Optiver US' match their firms",
          name("akuna capital university") == "Akuna Capital" and name("optiver us") == "Optiver")
    check("one-word names don't swallow other companies ('Cumberland Farms', 'Man Energy')",
          name("cumberland farms") is None and name("man energy solutions") is None and name("man") == "Man Group")

    d = niches.detail(pid, "quant")
    byname = {f["name"]: f for s in d["segments"] for f in s["firms"]}
    check("open roles per firm come from the postings", byname["Citadel Securities"]["open_roles"] == 1 and
          byname["Akuna Capital"]["open_roles"] == 1 and byname["Jane Street"]["open_roles"] == 1)
    check("roles they can't apply to are counted apart", byname["Old Mission"]["open_roles"] == 0 and
          byname["Old Mission"]["closed_to_you"] == 1)
    check("a firm without a careers page links its board", byname["Gelber Group"]["link"].startswith("https://"))
    check("the busiest opening month is known", d["windows"]["busiest"] in ("July", "August") and d["stats"]["firms"] >= 140)
    scratch = niches.create(pid, "Scratch")
    check("the niche says which season its opening dates are for (one you start has none)",
          d["season"] == "Summer 2027" and d["windows"]["season"] == "Summer 2027" and
          niches.get(pid, scratch["id"])["season"] == "")
    niches.delete(pid, scratch["id"])

    # following: boards are read, and the niche filters Matches
    with db.tx() as c:
        c.execute("INSERT INTO companies (ats, slug, name, removed, enabled) VALUES ('greenhouse', 'fiveringsllc',"
                  " 'Five Rings', 1, 0)")
    out = niches.follow(pid, "quant")
    check("following adds the firms' job boards to the sync", out["following"] and out["boards_added"] > 50 and
          db.row("SELECT 1 FROM companies WHERE ats='greenhouse' AND slug='janestreet'"))
    check("trading firms' boards are tagged quant; an energy company's isn't", db.row(
        "SELECT tier FROM companies WHERE slug='dvtrading'")["tier"] == "quant" and db.row(
        "SELECT tier FROM companies WHERE slug LIKE '%chevron%'")["tier"] is None)
    check("...but a board someone removed stays removed",
          db.row("SELECT removed FROM companies WHERE slug='fiveringsllc'")["removed"] == 1)
    check("following twice adds nothing new", niches.follow(pid, "quant")["boards_added"] == 0)
    lj = store.list_jobs(pid, niche="quant", limit=50)
    check("Matches → only the niche's firms", lj["total"] >= 4 and
          {j["company"] for j in lj["jobs"]} <= {"Citadel Securities", "Citadel", "Susquehanna International Group (SIG)",
                                                 "Akuna Capital University", "Jane Street", "Old Mission"})

    # a new posting at a niche firm alerts even with a low score
    sent = []
    notify.send = lambda name, n, picks, evs, ups: sent.append(picks) or ["test"]
    low = posting("Wolverine Trading", "Business Operations Intern", "https://x.test/20", "Operations work.",
                  locations=["Chicago, IL"])
    store.upsert_jobs([low], db.now())
    with db.tx() as c:
        c.execute("UPDATE jobs SET first_seen=? WHERE id=?", (db.now(), low["id"]))
    store.invalidate_cache()
    sync.notify_profiles({low["id"]}, db.now() - 10)
    picked = [j for batch in sent for j in batch if j["id"] == low["id"]]
    check("a followed niche's new posting alerts, marked 🎯", picked and picked[0]["niche_hit"] and
          notify._mark(picked[0]) == "🎯 ")
    niches.follow(pid, "quant", on=False)
    check("unfollowing stops that", niches.followed_matcher(pid) is None)

    # --- at a bank, an energy company or an exchange, only the niche's kind of role counts ----------------
    banks = [posting("TD Bank", "HR Coordinator Co-op", "https://x.test/50", "People and culture work."),
             posting("TD Bank", "Quantitative Analyst Co-op", "https://x.test/51", DESC["data"]),
             posting("Royal Bank of Canada", "Branch Customer Service Co-op", "https://x.test/52", "Help clients."),
             posting("Chevron", "Chemical Engineering Intern", "https://x.test/53", "Plant work.", locations=["Houston, TX"])]
    store.upsert_jobs(banks, db.now())
    bid = {j["title"]: j["id"] for j in banks}
    d = niches.detail(pid, "quant")
    td = firm_by_key(d, "td securities")
    check("at a bank only the niche's roles count: TD's quant co-op, not its HR co-op", td["open_roles"] == 1 and
          td["best"]["title"] == "Quantitative Analyst Co-op" and firm_by_key(d, "rbc capital markets")["open_roles"] == 0
          and firm_by_key(d, "chevron")["open_roles"] == 0)
    lj = store.list_jobs(pid, niche="quant", limit=200)
    shown = {j["title"] for j in lj["jobs"]}
    check("...the same in Show postings", "Quantitative Analyst Co-op" in shown and not shown & {
        "HR Coordinator Co-op", "Branch Customer Service Co-op", "Chemical Engineering Intern"})
    check("...in the niche's role count", next(o for o in niches.summary_list(pid) if o["id"] == "quant")["roles"] == lj["total"])
    ex = agent.call(pid, "explore_niche", {"niche": "quant", "segment": "canadian"})
    check("...and in the chat's explore_niche", next(f for f in ex["segments"][0]["firms"]
                                                     if f["name"] == "TD Securities")["open_roles"] == 1)
    niches.follow(pid, "quant")
    fm = niches.followed_matcher(pid)
    sj = {j["id"]: j for j in store.scored_jobs(pid)}
    check("...and in alerts: TD's quant co-op is a 🎯 niche role, its HR co-op isn't",
          fm.firm(sj[bid["Quantitative Analyst Co-op"]]) and not fm.firm(sj[bid["HR Coordinator Co-op"]]) and
          not fm.firm(sj[bid["Chemical Engineering Intern"]]))
    yours = niches.create(pid, "Canadian banks")
    niches.add_firm(pid, yours["id"], "TD Bank")
    mine = {j["title"] for j in store.list_jobs(pid, niche=yours["id"])["jobs"]}
    check("a niche of your own counts every internship at the firms you put in it",
          {"HR Coordinator Co-op", "Quantitative Analyst Co-op"} <= mine)
    niches.delete(pid, yours["id"])

    # niche roles take their place by score: ten of them don't push out a better match
    small = ["Belvedere Trading", "Geneva Trading", "Gelber Group", "DV Trading", "Maven Securities", "PEAK6",
             "TransMarket Group", "Chicago Trading Company", "Flow Traders", "Virtu Financial"]
    ops = [posting(c, "Business Operations Intern", f"https://x.test/ops{i}", "Operations work.",
                   locations=["Chicago, IL"]) for i, c in enumerate(small)]
    good = posting("Stripe", "Software Engineer Intern", "https://x.test/89", DESC["web"])
    store.upsert_jobs(ops + [good], db.now())
    sent.clear()
    sync.notify_profiles({j["id"] for j in ops + banks} | {good["id"]}, db.now() - 10)
    batch = sent[-1] if sent else []
    hits = [p for p in batch if p["niche_hit"]]
    check("alerts: a better match isn't pushed out by niche roles", len({j["id"] for j in ops + [good]}) == 11 and
          "Stripe" in [p["company"] for p in batch] and hits and len(batch) == 10)
    check("...everything is in score order, favourites aside", [p["score"] for p in batch] ==
          sorted((p["score"] for p in batch), reverse=True))
    niches.follow(pid, "quant", on=False)

    # --- a board read for the first time lists what was already open: not new, not an opening ---------------
    with db.tx() as c:
        c.execute("INSERT INTO sync_runs (started_at, finished_at) VALUES (?,?)", (now - 7200, now - 7000))
        c.execute("INSERT INTO companies (ats, slug, name) VALUES ('greenhouse', 'brandnewfirm', 'Brand New Firm')")
        c.execute("INSERT INTO companies (ats, slug, name, last_checked, last_count) VALUES ('greenhouse', 'oldfirm',"
                  " 'Old Firm', ?, 3)", (now - 3600,))
    boards = {f"greenhouse:{slug}": [{"company": name, "title": "Software Engineer Intern - Summer 2028", "url": url,
                                      "locations": ["Toronto, ON"], "terms": ["Summer 2028"], "description": DESC["web"]}]
              for slug, name, url in (("brandnewfirm", "Brand New Firm", "https://x.test/60"),
                                      ("oldfirm", "Old Firm", "https://x.test/61"))}
    recorded, woke = [], []
    stand_ins = [(sync, "_company_task", lambda co, known: [dict(r) for r in boards.get(f"{co['ats']}:{co['slug']}", [])]),
                 (sync.simplify, "fetch", lambda repos: []), (sync.linkedin, "fetch", lambda *a, **k: []),
                 (sync.indeed, "available", lambda: False), (sync.companies, "refresh_yc", lambda *a, **k: 0),
                 (sync.events, "sync_feeds", lambda: (0, {})), (sync, "enrich_top", lambda prof, n=60: 0),
                 (niches, "record_openings", lambda ids: recorded.append(set(ids)) or 0),
                 (autopilot, "on_sync", lambda ids: woke.append(set(ids)))]
    real = [(mod, name, getattr(mod, name)) for mod, name, _ in stand_ins]
    for mod, name, fn in stand_ins:
        setattr(mod, name, fn)
    sent.clear()
    try:
        sync.run(notify_users=True)
    finally:
        for mod, name, fn in real:
            setattr(mod, name, fn)
    new_id, old_id = store.job_id("https://x.test/60"), store.job_id("https://x.test/61")
    alerted = {p["id"] for b in sent for p in b}
    check("a board read for the first time: its postings don't alert, an old board's new one does",
          db.row("SELECT 1 FROM jobs WHERE id=?", (new_id,)) and new_id not in alerted and old_id in alerted)
    check("...don't wake Instant Autopilot", woke and new_id not in woke[0] and old_id in woke[0])
    check("...and aren't taken as when the firm opened", recorded and new_id not in recorded[0] and old_id in recorded[0])

    # opening dates learned from syncs
    first, second, third = now - 20 * 86400, now - 9 * 86400, now - 2 * 86400
    a = posting("Jane Street", "Software Engineer Intern - Summer 2028", "https://x.test/30", DESC["swe"],
                terms=["Summer 2028"], posted_at=second)
    store.upsert_jobs([a], db.now())
    niches.record_openings({a["id"]})
    b = posting("Jane Street", "Quant Trader Intern - Summer 2028", "https://x.test/31", DESC["swe"],
                terms=["Summer 2028"], posted_at=third)
    c_ = posting("Jane Street", "FPGA Engineer Intern - Summer 2028", "https://x.test/32", DESC["swe"],
                 terms=["Summer 2028"], posted_at=first)  # seen later, but it went up first
    store.upsert_jobs([b, c_], db.now())
    niches.record_openings({b["id"]})
    row = db.row("SELECT * FROM firm_openings WHERE company_key='jane street' AND term='Summer 2028'")
    check("the first internship for a term is when the firm opened (a later one doesn't move it)",
          row and row["opened_at"] == second)
    niches.record_openings({c_["id"]})
    row = db.row("SELECT * FROM firm_openings WHERE company_key='jane street' AND term='Summer 2028'")
    check("...an earlier one seen in a later sync does", row["opened_at"] == first)
    js = next(f for s in niches.detail(pid, "quant")["segments"] for f in s["firms"] if f["name"] == "Jane Street")
    check("...and the niche shows it", js["seen_opening"] and js["seen_opening"]["term"] == "Summer 2028")

    # reminders before each month firms usually open
    today = datetime.date(2026, 10, 14)
    rem = niches.window_reminders(pid, "quant", today=today)
    titles = [r["title"] for r in rem["created"]]
    # (titles now end with the season they hire for, so these look for "in July" inside the title)
    july = next((r for r in rem["created"] if " in July " in r["title"]), None)
    check("one reminder per month firms open, next season's", len(rem["created"]) == rem["months"] >= 6 and july and
          datetime.date.fromtimestamp(july["due_at"]).year == 2027)
    check("...two weeks before the first firm that month", july and
          datetime.date.fromtimestamp(july["due_at"]) == datetime.date(2027, 7, 1) - datetime.timedelta(days=14))
    check("...named after the firms", july and "Quant & Trading:" in july["title"] and "more" in july["title"])
    check("...and say they're for next season's hiring, from the niche's season",
          july and july["title"].endswith("in July (Summer 2028 hiring)"))
    check("asking again doesn't duplicate them", niches.window_reminders(pid, "quant", today=today)["created"] == [])
    with db.tx() as c:
        c.execute("DELETE FROM reminders WHERE profile_id=?", (pid,))
    only = niches.window_reminders(pid, "quant", months=[8], today=today)
    check("or just the months picked", len(only["created"]) == 1 and " in August " in only["created"][0]["title"])
    check("titles fit the calendar", all(len(t) <= 200 for t in titles))
    jul = niches.window_reminders(pid, "quant", months=[7], today=today)["created"]
    check("a reminder remembers which niche and month it's for",
          jul and db.row("SELECT ref FROM reminders WHERE id=?", (jul[0]["id"],))["ref"] == "niche:quant:2027-07")
    niches.set_firm(pid, "quant", "imc trading", "hidden")  # the first July firm: the title changes
    check("...so a changed firm list doesn't set the same month twice",
          niches.window_reminders(pid, "quant", months=[7], today=today)["created"] == [])
    niches.set_firm(pid, "quant", "imc trading", "shown")

    # an opening learned for a later season: kept after the posting closes, and the windows go by it
    may = int(datetime.datetime(2027, 5, 1, 12).timestamp())
    h = posting("Hudson River Trading", "Software Engineering Intern - Summer 2028", "https://x.test/33", DESC["swe"],
                terms=["Summer 2028"], posted_at=may)
    store.upsert_jobs([h], db.now())
    niches.record_openings({h["id"]})
    with db.tx() as c:
        c.execute("UPDATE jobs SET active=0 WHERE id=?", (h["id"],))
    store.invalidate_cache()
    d = niches.detail(pid, "quant")
    hrt = firm_by_key(d, "hudson river trading")
    check("a learned opening still shows once the firm's postings have closed",
          hrt["open_roles"] == 0 and hrt["seen_opening"] and hrt["seen_opening"]["term"] == "Summer 2028")
    w = niches.windows([f for s in d["segments"] for f in s["firms"] if not f["hidden"]], today, d["season"])
    by_mo = {m["month"]: m for m in w["months"]}
    check("...a later season's opening moves the firm's window (July -> May, for Summer 2028 hiring)",
          [f["seen"] for f in by_mo[5]["firms"] if f["name"] == "Hudson River Trading"] == [True] and
          not any(f["name"] == "Hudson River Trading" for f in by_mo[7]["firms"]) and by_mo[5]["season"] == "Summer 2028"
          and w["seen"] >= 1)
    with db.tx() as c:
        c.execute("DELETE FROM reminders WHERE profile_id=?", (pid,))
    mayr = niches.window_reminders(pid, "quant", months=[5], today=today)["created"]
    check("...and so do the reminders", mayr and mayr[0]["title"].startswith("Quant & Trading: Hudson River Trading,") and
          datetime.date.fromtimestamp(mayr[0]["due_at"]) == datetime.date(2027, 4, 17))

    # --- finding firms you're missing -----------------------------------------------------------
    fm = niches.find_more(pid, "quant", use_ai=False)
    sugg = {s["name"]: s for s in fm["suggestions"]}
    check("firms posting quant roles that aren't on the list are found in the postings",
          "Talos" in sugg and sugg["Talos"]["origin"] == "postings" and "1 open" in sugg["Talos"]["verified"])
    check("...not quantum-computing companies, and not firms already listed",
          "QuEra Computing" not in sugg and "Citadel" not in sugg and "Jane Street" not in sugg)
    niches.set_firm(pid, "quant", sugg["Talos"]["key"], "dismissed")
    again = niches.find_more(pid, "quant", use_ai=False)
    check("a dismissed suggestion doesn't come back", "Talos" not in {s["name"] for s in again["suggestions"]})

    # the live check
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "boards-api.greenhouse.io" and req.url.path.endswith("/vatiq"):
            return httpx.Response(200, json={"name": "Vatiq"})
        if req.url.host == "careers.realfirm.test":
            return httpx.Response(200, text="<h1>Careers</h1>")
        return httpx.Response(404)
    niches.client = lambda timeout=30, public_only=False: httpx.Client(transport=httpx.MockTransport(handler),
                                                                       follow_redirects=True)
    check("verify: a firm already in the postings", niches.verify("Talos")["ok"])
    check("verify: its careers page answers", niches.verify("Real Firm", "https://careers.realfirm.test/jobs")["ok"])
    check("verify: a Greenhouse board under its name, with the same company name",
          niches.verify("Vatiq")["board"] == "greenhouse:vatiq")
    check("verify: nothing answers → not ok", not niches.verify("Imaginary Partners", "https://nope.realfirm.test/")["ok"])
    try:
        niches.verify("Sneaky", "http://127.0.0.1:8765/api/state")
        check("verify: links into this computer are refused", False)
    except niches.BlockedURL:
        check("verify: links into this computer are refused", True)

    # the AI's ideas only arrive checked
    ai.available = lambda: True
    ai.label = lambda: "test AI"
    ai.find_firms = lambda n, known, student: ({"segments": [
        {"name": "Smaller prop shops", "new": False, "why": "", "firms": [
            {"name": "Real Firm", "careers_url": "https://careers.realfirm.test/jobs", "where": "Chicago", "why": "Options MM"},
            {"name": "Imaginary Partners", "careers_url": "", "where": "", "why": "made up"},
            {"name": "Jane Street", "careers_url": "", "where": "", "why": "already listed"}]},
        {"name": "Weather & climate trading", "new": True, "why": "Power traders price weather", "firms": [
            {"name": "Vatiq", "careers_url": "", "where": "", "why": "weather derivatives"},
            {"name": "Inside Job", "careers_url": "http://192.168.0.10/careers", "where": "", "why": "private link"}]}]},
        False)
    res = niches.find_more(pid, "quant")
    got = {s["name"]: s for s in res["suggestions"]}
    check("AI suggestions that check out arrive, with what the check found",
          "Real Firm" in got and got["Real Firm"]["verified"] == "its careers page answers" and "Vatiq" in got)
    check("ones that don't check out (or point into a private network) are left out, and said so",
          "Imaginary Partners" not in got and "Inside Job" not in got and
          {x["name"] for x in res["dropped"]} == {"Imaginary Partners", "Inside Job"})
    check("firms already listed aren't suggested again", "Jane Street" not in got)
    check("new segments are reported", [s["name"] for s in res["new_segments"]] == ["Weather & climate trading"])
    niches.set_firm(pid, "quant", got["Vatiq"]["key"], "added")
    d = niches.detail(pid, "quant")
    seg = next((s for s in d["segments"] if s["name"] == "Weather & climate trading"), None)
    check("accepting a suggestion adds it to its (new) segment", seg and seg["firms"][0]["name"] == "Vatiq" and
          seg["firms"][0]["board"] == "greenhouse:vatiq")
    niches.set_firm(pid, "quant", "jane street", "hidden")
    d = niches.detail(pid, "quant")
    js = next(f for s in d["segments"] for f in s["firms"] if f["key"] == "jane street")
    check("a firm can be taken off the list (and its roles leave the niche)", js["hidden"] and
          not niches.matcher(pid, "quant").match("jane street"))
    niches.set_firm(pid, "quant", "jane street", "shown")
    check("...and put back", niches.matcher(pid, "quant").match("jane street"))

    # firms you added (or accepted) can be taken off and put back too
    niches.add_firm(pid, "quant", "Tiny Prop Shop")
    niches.set_firm(pid, "quant", "tiny prop shop", "hidden")
    tiny = firm_by_key(niches.detail(pid, "quant"), "tiny prop shop")
    check("a firm you added and took off is listed under 'Taken off your list'", tiny and tiny["hidden"] and
          not niches.matcher(pid, "quant").match("tiny prop shop"))
    niches.set_firm(pid, "quant", "tiny prop shop", "shown")
    tiny = firm_by_key(niches.detail(pid, "quant"), "tiny prop shop")
    check("...and putting it back makes it yours again", tiny and not tiny["hidden"] and tiny["origin"] == "you" and
          db.row("SELECT status FROM niche_firms WHERE profile_id=? AND key='tiny prop shop'", (pid,))["status"] == "added")
    niches.set_firm(pid, "quant", got["Vatiq"]["key"], "hidden")
    niches.set_firm(pid, "quant", got["Vatiq"]["key"], "shown")
    vq = firm_by_key(niches.detail(pid, "quant"), got["Vatiq"]["key"])
    check("...the same for a find you accepted, with what was found about it",
          vq and not vq["hidden"] and vq["board"] == "greenhouse:vatiq")

    # hiding a firm never hands its postings to a firm with a similar name
    niches.set_firm(pid, "quant", "citadel securities", "hidden")
    d = niches.detail(pid, "quant")
    cit = firm_by_key(d, "citadel")
    check("hiding Citadel Securities doesn't make its roles Citadel's", cit["open_roles"] == 1 and
          cit["best"]["title"] == "Quantitative Research Intern" and niches.matcher(pid, "quant").match("citadel securities") is None)
    niches.follow(pid, "quant")
    cs = next(j for j in store.scored_jobs(pid) if j["company_key"] == "citadel securities")
    check("...and stops its alerts", niches.followed_matcher(pid).firm(cs) is None)
    niches.follow(pid, "quant", on=False)
    niches.set_firm(pid, "quant", "citadel securities", "shown")

    # adding a firm: a name that names nothing, or one already on the list
    firms_before = niches.detail(pid, "quant")["stats"]["firms"]
    check("adding 'LLC' is refused, kindly", "full name" in niches.add_firm(pid, "quant", "LLC").get("error", ""))
    dup = niches.add_firm(pid, "quant", "Jane Street")
    check("adding a firm already on the list says where it is, and adds nothing",
          "already on the list, under Market makers" in dup.get("error", "") and
          niches.detail(pid, "quant")["stats"]["firms"] == firms_before)
    niches.set_firm(pid, "quant", "hudson river trading", "hidden")
    back = niches.add_firm(pid, "quant", "HRT")
    check("adding a firm you'd taken off puts it back", back.get("restored") and back["name"] == "Hudson River Trading"
          and not firm_by_key(niches.detail(pid, "quant"), "hudson river trading")["hidden"])

    # a niche of your own
    made = niches.create(pid, "Robotics & autonomy")
    robo = posting("Contoro", "Robotics Engineer Intern", "https://x.test/40", "", locations=["Austin, TX"])
    hw = posting("Micron", "Product Engineer Intern - DRAM", "https://x.test/41", "", locations=["Boise, ID"])
    store.upsert_jobs([robo, hw], db.now())
    ai.available = lambda: False
    mine = niches.find_more(pid, made["id"])
    check("a niche of your own fills from postings matching its keywords, not the whole category",
          {s["name"] for s in mine["suggestions"]} == {"Contoro"})
    check("your own niche is listed with the shipped ones",
          [n["id"] for n in niches.summary_list(pid)] == [*niches.shipped_ids(), made["id"]])

    # --- the API ----------------------------------------------------------------------------------
    cl = TestClient(server.app, base_url="http://localhost")
    check("GET /api/market", cl.get(f"/api/market?profile_id={pid}").json()["label"] == "Software")
    check("POST /api/market/fit with the resume being edited",
          cl.post("/api/market/fit", json={"profile_id": pid, "resume": r2}).json()["coverage"] is not None)
    add = cl.post("/api/market/add-skill", json={"profile_id": pid, "resume": resume, "text": "Rust", "skill": "Rust"}).json()
    check("POST /api/market/add-skill", add["line"] == "Languages" and "Rust" in add["resume"]["skills"][0]["items"])
    check("GET /api/niches", "quant" in [n["id"] for n in cl.get(f"/api/niches?profile_id={pid}").json()["niches"]])
    check("GET /api/niches/quant", cl.get(f"/api/niches/quant?profile_id={pid}").json()["name"] == "Quant & Trading")
    check("an unknown niche is a 404", cl.get(f"/api/niches/nope?profile_id={pid}").status_code == 404)
    check("PUT follow", cl.put("/api/niches/quant/follow", json={"profile_id": pid, "on": True}).json()["following"])
    kept = db.row("SELECT COUNT(*) AS n FROM niche_firms WHERE profile_id=? AND niche_id='quant'", (pid,))["n"]
    check("a shipped niche can't be deleted, and nothing of yours goes", niches.delete(pid, "quant").get("error") and
          cl.delete(f"/api/niches/quant?profile_id={pid}").status_code == 400 and "quant" in niches.followed(pid) and
          db.row("SELECT COUNT(*) AS n FROM niche_firms WHERE profile_id=? AND niche_id='quant'", (pid,))["n"] == kept > 0)
    check("POST /api/market/fit with an n that isn't a number is a 400, not a 500",
          cl.post("/api/market/fit", json={"profile_id": pid, "n": "abc"}).status_code == 400)
    asked, real_fit = [], market.fit
    market.fit = lambda p, r=None, c="", n=market.SAMPLE, track="": asked.append(n) or real_fit(p, r, c, n, track)
    try:
        codes = [cl.post("/api/market/fit", json={"profile_id": pid, "n": n}).status_code for n in (100000, 3)]
    finally:
        market.fit = real_fit
    check("...and n is kept to 10-60", codes == [200, 200] and asked == [60, 10])
    check("adding a firm with a link into this computer is refused",
          cl.post("/api/niches/quant/firms", json={"profile_id": pid, "name": "X", "url": "http://localhost:8765/"}).status_code == 400)
    a = cl.post("/api/niches/quant/firms", json={"profile_id": pid, "name": "Real Firm Two",
                                                 "url": "https://careers.realfirm.test/", "segment": "prop-shops"}).json()
    check("adding a firm you know checks it and keeps it", a["found"] and any(
        f["name"] == "Real Firm Two" for s in niches.get(pid, "quant")["segments"] if s["id"] == "prop-shops" for f in s["firms"]))
    check("GET /api/jobs?niche=", cl.get(f"/api/jobs?profile_id={pid}&niche=quant").json()["total"] >= 4)
    check("someone else's profile id doesn't exist here", cl.get("/api/market?profile_id=999").status_code == 404)
    gone = cl.get(f"/api/jobs?profile_id={pid}&niche=custom-gone").json()
    check("a niche filter for a niche that's gone shows nothing and says so", gone["total"] == 0 and not gone["jobs"]
          and gone.get("niche_unknown") is True and not store.list_jobs(pid, niche="quant").get("niche_unknown"))
    with db.tx() as c:
        pid2 = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES"
                         " ('Friend', ?, ?, '{}', '{}')", (now, now)).lastrowid
    theirs = niches.create(pid2, "Their niche")
    check("...and so does another person's niche", store.list_jobs(pid, niche=theirs["id"]).get("niche_unknown") is True)
    check("deleting your own niche", cl.delete(f"/api/niches/{made['id']}?profile_id={pid}").json()["ok"] and
          not niches.get(pid, made["id"]))

    # --- the chat's tools -------------------------------------------------------------------------
    mk = agent.call(pid, "my_market", {})
    check("chat: my_market", mk.get("market") == "Software" and mk["top_skills"] and "coverage_percent" in mk)
    ex = agent.call(pid, "explore_niche", {"niche": "quant", "segment": "pension"})
    check("chat: explore_niche (one segment)", ex.get("niche") == "Quant & Trading" and len(ex["segments"]) == 1 and
          ex["_card"]["type"] == "niche")
    fo = agent.call(pid, "follow_niche", {"niche": "quant", "on": False})
    check("chat: follow_niche, with Undo", fo.get("ok") and not niches.followed(pid) and
          agent.call(pid, fo["_undo"]["tool"], fo["_undo"]["args"]).get("following") and niches.followed(pid))
    check("chat: unknown niche says which exist", "quant" in json.dumps(agent.call(pid, "explore_niche", {"niche": "zzz"})).lower())
    role = explore_tools._role
    check("chat: my_market reads role words whole ('retail analytics' and 'email marketing' aren't AI)",
          (role("retail analytics"), role("email marketing"), role("maintenance engineering")) == ("", "", "") and
          (role("AI/ML"), role("ML engineer"), role("quantitative trading"), role("software")) ==
          ("AI/ML/Data", "AI/ML/Data", "Quant", "Software"))
    check("the tools are in the chat's list with summaries",
          all(t in agent.CHAT_TOOLS for t in ("my_market", "explore_niche", "follow_niche")) and
          agent._summary("follow_niche", {"following": True, "niche": "Quant & Trading"}) == "following Quant & Trading")

    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
