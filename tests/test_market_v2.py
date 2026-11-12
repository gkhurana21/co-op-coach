"""A track's Market (market v2): a posting's own headings split required from nice-to-have skills; stated pay is read
as written (hourly, yearly pro rata, "k", a bare "$" by the posting's country) and money that isn't pay ("$10M
raised", a term stipend) is left out; which postings count (open to you, internships, the last 120 days, only ones
with full text for skills); trends only with enough postings on both sides; every number opens a list of exactly that
many postings; facts are kept and read again when a description changes; and Resume Studio's fit for a track is the
track page's fit.

Run: .venv/bin/python -m tests.test_market_v2
"""

import os
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-market2-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1")

from coopcoach import companies, db, market, routes_docs, store, tracks  # noqa: E402

results: dict[str, bool] = {}
DAY = 86400


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def desc(req="Python, SQL", nice="Docker", pay="The pay range for this role is $30 - $40 per hour."):
    return (f"About the team\nWe build data tools.\nWhat you'll do\nShip features every week.\n"
            f"Requirements:\n- Experience with {req}\n- Comfortable with Git\nNice to have\n- {nice}\n"
            f"Compensation\n{pay}\nEqual opportunity employer.\n")


def pay(text, regions=None):
    p = market.pay_of(text, regions or [])
    return p and (p["unit"], p["currency"], p["hourly_low"], p["hourly_high"])


def readers():
    s = market.sections(desc())
    check("the posting's headings split its text: requirements, nice to have, the rest",
          "Python, SQL" in s["req"] and "Docker" in s["nice"] and "Ship features" in s["other"])
    s2 = market.sections("Qualifications: Java and C++\nKubernetes experience is a plus\nBenefits\nDental")
    check("...a heading with text after it, and a line saying 'a plus' inside requirements",
          "Java and C++" in s2["req"] and "Kubernetes" in s2["nice"] and "Dental" in s2["other"])
    f = market.facts_of("Data Intern", desc() * 2, ["Canada"])
    check("facts: the skills asked, which are required and which nice to have",
          {"Python", "SQL"} <= set(f["req"]) and "Docker/Kubernetes" in f["nice"] and f["headed"]
          and set(f["req"]) <= set(f["skills"]))
    check("...no description, no facts", market.facts_of("Data Intern", "Apply here.") == {"described": False})

    check("pay: an hourly range", pay("The pay range is $25 - $35 per hour.") == ("hour", "$", 25, 35))
    check("pay: a yearly salary, per hour (2,080 hours a year)",
          pay("Salary: $80,000 - $100,000 per year", ["US"]) == ("year", "USD", 38.46, 48.08))
    check("pay: '$80k-$100k annually'", pay("Base pay $80k-$100k annually", ["US"])[:2] == ("year", "USD")
          and pay("Base pay $80k-$100k annually", ["US"])[2] == 38.46)
    check("pay: CAD written out, per month", pay("Stipend of CAD $7,000/month") == ("month", "CAD", 40.38, 40.38))
    check("pay: a bare '$' is the posting's own country's dollar", pay("Hourly rate: $30-$40", ["Canada"])[1] == "CAD"
          and pay("Hourly rate: $30-$40", ["US"])[1] == "USD" and pay("Hourly rate: $30-$40", ["Canada", "US"])[1] == "$")
    check("pay: a currency written after the number wins", pay("Pay: $30 - $30 USD per hour", ["Canada"])[1] == "USD")
    check("pay: money that isn't pay isn't pay", pay("We raised a $10M Series A last year.") is None
          and pay("Since 2018 we've raised $4 billion of capital.") is None
          and pay("We offer a $6,000/summer stipend, paid monthly.") is None
          and pay("Win a $100 gift card at our booth.") is None)
    check("pay: a figure with no unit only counts next to a word about pay, and only if it's clearly hourly or yearly",
          pay("Compensation: $45") == ("hour", "$", 45, 45) and pay("Our office is at $45 Main St") is None
          and pay("Compensation: $2,500") is None)
    q = market.pay_of("Lots of text here. The hourly wage range for this position is $21.00 - $40.00 and more.", [])
    check("pay: a short quote of where it's written", q and "$21.00 - $40.00" in q["quote"] and len(q["quote"]) < 160)


def posting(company, title, d, n=0, days_ago=1, locations=("Toronto, ON, Canada",)):
    raw = {"source": "greenhouse:x", "company": company, "title": title, "url": f"https://x.test/{company}/{n}",
           "locations": list(locations), "description": d}
    j = store.normalize(raw)
    store.upsert_jobs([j], db.now() - days_ago * DAY)
    return j["id"]


def track_market():
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES"
                        " (?,?,?,?,?)", ("Sam", db.dumps({"skills": ["Python"]}), db.dumps({"categories": ["Software"]}),
                                         now, now)).lastrowid
    # ten older postings that ask for Java, ten recent ones that ask for Rust: Rust is up, Java down
    fresh = []
    for i in range(10):
        posting(f"Old Co {i}", f"Software Engineering Intern {i}", desc(req="Java, SQL", pay="") * 2, i, days_ago=60)
        fresh.append(posting(f"New Co {i}", f"Software Developer Intern {i}", desc(req="Rust, SQL") * 2, i, days_ago=5))
    posting("Short Co", "Software Engineering Intern", "Apply on our site.", days_ago=5)  # no full text
    gone = posting("Stale Co", "Software Engineering Intern", desc() * 2, days_ago=200)  # older than 120 days
    routes_docs._save_doc(pid, "resume_master", db.dumps({
        "name": "Sam", "skills": [{"label": "Languages", "items": "Python, SQL, Java"}],
        "experience": [{"title": "Developer", "company": "Club", "bullets": ["Built a site with Git and Docker"]}]}),
        "Master resume", by="you")
    us_only = posting("US Co", "Software Engineering Intern (US citizens only)",
                      desc() * 2 + "\nMust be a U.S. citizen. US citizenship required.", locations=("Austin, TX, USA",))
    store.invalidate_cache()
    tid = "role:software"
    m = market.track_market(pid, tid)
    jobs = {j["id"]: j for j in store.scored_jobs(pid)}
    check("the population: open to you, internships, the last 120 days", m["size"] == 21 and gone not in {
        j["id"] for j in market.population(pid, tracks.members(pid, tid))} and (jobs[us_only]["eligibility"] != "no"
                                                                              or m["size"] == 21))
    check("...skill shares only over the ones with full text, and that number is said", m["described"] == 20)
    sk = {s["skill"]: s for s in m["skills"]}
    check("skills: share, required count, and a trend (10 postings each side)", sk["SQL"]["share"] == 1.0
          and sk["SQL"]["req"] == 20 and m["trend_ready"] and sk["Rust"]["trend"] == "up" and sk["Java"]["trend"] == "down")
    check("every skill's number opens exactly that many postings", all(
        s["count"] == store.list_jobs(pid, track=tid, facet=f"skill:{s['skill']}", population="market")["total"]
        for s in m["skills"]))
    p = m["glance"]["pay"]
    check("pay: only stated pay, per hour, in its currency, with how many state it",
          p["n"] == 10 and p["currency"] == "CAD" and p["median"] == 35 and p["p25"] == 35 and p["quotes"])
    check("...and 'states pay' opens those postings", p["stated"] == store.list_jobs(
        pid, track=tid, facet="pay", population="market")["total"])
    city = m["where"]["cities"][0]
    check("where: cities, each opening its postings", city["city"] == "Toronto" and city["n"] == store.list_jobs(
        pid, track=tid, facet="city:Toronto", population="market")["total"])
    who = m["where"]["who"]
    check("who can apply: over every open posting here, each opening its list", sum(who.values()) == len(
        tracks.members(pid, tid)) and all(k == store.list_jobs(pid, track=tid, facet=f"elig:{e}",
                                                                show_ineligible=True)["total"] for e, k in who.items()))
    check("posted per month: twelve months", len(m["history"]) == 12 and sum(h["n"] for h in m["history"]) >= 21)
    check("at a glance: open to you, fit, gaps, pay, peak months", m["glance"]["open_to_you"] == m["size"]
          and m["glance"]["gaps"] == ["Rust"] and 0 < m["glance"]["fit"] < 100 and m["glance"]["peak"])
    check("...each skill knows whether the resume shows it", sk["SQL"]["status"] == "listed"
          and sk["Rust"]["status"] == "missing")
    studio = market.fit(pid, None, track=tid)
    check("Resume Studio's fit for a track is the track page's fit", studio.get("coverage") == m["fit"].get("coverage")
          and [r["skill"] for r in studio["skills"]] == [r["skill"] for r in m["fit"]["skills"]]
          and studio["market"]["track"] == tid)
    check("...an unknown track says so", market.fit(pid, None, track="role:nope").get("error"))

    one = fresh[0]
    kept = db.row("SELECT version, facts FROM job_facts WHERE job_id=?", (one,))
    check("facts are kept with the length of the text they came from", kept and kept["version"].startswith(
        market.FACTS_VERSION + ":"))
    with db.tx() as c:
        c.execute("UPDATE jobs SET description=? WHERE id=?", (desc(req="Go, SQL") * 3, one))
    f = market.facts_for([{**jobs[one], "skills": None}])[one]
    check("...and read again when the description changes", "Go" in f["req"])
    few = market.track_skills(pid, tid)
    check("trends need enough postings on both sides", few["trend_ready"])
    with db.tx() as c:
        # nine of the ten older postings went up recently after all (posted_at is what a board says)
        c.execute("UPDATE jobs SET posted_at=? WHERE company LIKE 'Old Co%' AND company != 'Old Co 1'", (now - 5 * DAY,))
    store.invalidate_cache()
    check("...with one old posting left, no trend is shown", not market.track_skills(pid, tid)["trend_ready"]
          and all(s["trend"] is None for s in market.track_skills(pid, tid)["skills"]))


def main():
    db.init()
    real = (companies.yc_all, companies.yc_lookup)
    companies.yc_all, companies.yc_lookup = (lambda: []), (lambda name: None)
    try:
        readers()
        track_market()
    finally:
        companies.yc_all, companies.yc_lookup = real
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
