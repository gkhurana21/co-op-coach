"""Is what gets picked right for the student? Titles read correctly (a manufacturing engineer isn't a software role), degree
requirements read correctly (BS/MS is open to undergrads, an MS role isn't), remote means where it means, and Autopilot puts
reachable roles first, one per employer however its name is written.

Run: .venv/bin/python -m tests.test_picks
"""

import os
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-picks-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1")

from coopcoach import agent, autopilot, db, jobdetail, matching, store  # noqa: E402
from coopcoach.textutil import classify_category, degree_flags, detect_flags, refine_category, us_only  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def titles():
    want = {
        "Manufacturing Engineering Intern": "Hardware",
        "Process Engineering Intern - Summer": "Hardware",
        "Mechanical Engineering Co-op": "Hardware",
        "Civil Engineering Internship - Site Design": "Other",
        "Parsons Civil/Environmental Engineering Intern": "Other",
        "Chemical Engineering Intern": "Other",
        "Biomedical Engineering Co-op": "Other",
        "Software Engineering Intern": "Software",
        "Software Engineer Intern, Manufacturing Systems": "Software",
        "Web Developer Co-op, Construction Tech": "Software",
        "Machine Learning Intern, Manufacturing": "AI/ML/Data",
        "Data Analyst Intern, Environmental": "AI/ML/Data",
        "Business Operations Intern, Commercial Ops & Analytics": "Business/Finance",
        "Supply Chain Analyst Intern": "Business/Finance",
        "Marketing Analytics Intern": "Business/Finance",
        "Engineering Intern": "Software",
        "Quantitative Research Intern": "Quant",
        "Summer 2027 Legal Intern, National Security Project": "Business/Finance",
        "Preconstruction Engineer Intern": "Other",
        "Summer 2027 Aeromechanical Engineering Intern": "Hardware",
        "Biomechanical Engineering Intern": "Hardware",
        "Packaging Engineering Intern - Winter": "Hardware",
        "Summer Intern - IT Manufacturing Systems": "Software",
        "Privacy and Civil Liberties Software Engineer Intern": "Software",
    }
    wrong = {t: classify_category(t) for t, c in want.items() if classify_category(t) != c}
    check(f"titles are read for what the job is {wrong or ''}", not wrong)
    check("a stored label gives way to what the title plainly says", refine_category("Manufacturing Engineering Intern", "Software") == "Hardware"
          and refine_category("Civil Engineering Intern", "Software") == "Other" and refine_category("Business Operations Intern, Analytics", "AI/ML/Data") == "Business/Finance")
    check("...and stands when the title doesn't say", refine_category("Technical Intern", "Software") == "Software" and refine_category("Intern", None) == "Other"
          and refine_category("Product Design Intern", "Product/Design") == "Product/Design")


def degrees():
    want = {
        "2027 Summer Intern, MS, Software Engineering, Behavior Test": ["masters"],
        "2027 Summer Intern, MS/PhD, Machine Learning Engineer": ["masters"],
        "2027 Summer Intern, MS, PhD, Software Engineer": ["masters"],
        "IC Validation Engineer Intern, MS - Summer 2027": ["masters"],
        "Physical Design Engineer Intern - MS - Multiple Teams": ["masters"],
        "Firmware Engineer Intern, MS": ["masters"],
        "MBA Summer Associate": ["masters"],
        "Master's Software Intern": ["masters"],
        "AI Solution Architect - Graduate Intern": ["masters"],
        "Quantitative Research Intern - PhD/Postdoctoral": ["phd"],
        "Research Intern (PhD)": ["phd"],
        "Quantitative Research Intern (BS/MS) - Summer 2027": [],
        "Structural Dynamics Analyst Co-op - BS or MS (Gloucester, MA)": [],
        "Research Intern, BS/MS/PhD": [],
        "Machine Learning Undergraduate Intern": [],
        "Mechanical Engineering Co-op 2027 (Hattiesburg, MS)": [],
        "Field Science Intern (Possible locations include: FL, MD, MS, & OK)": [],
        "MS Office Support Intern": [],
        "Software Engineering Intern": [],
    }
    wrong = {t: degree_flags(t) for t, f in want.items() if degree_flags(t) != f}
    check(f"a title's degree is read right, and a state or MS Office isn't a degree {wrong or ''}", not wrong)
    check("flags from the text still come through", "coop" in detect_flags("Software Co-op") and "masters" in detect_flags("Intern, MS - Summer 2027"))


def us_only_rule():
    fine = ["We don't discriminate by age, disability, veteran or military status.", "Humanitarian work, universitario, sanitary conditions and a guitar",
            "Copart participates in the Department of Homeland Security U.S. Citizenship and Immigration Services' E-Verify program",
            "U.S. citizen or legal right to work in the U.S.", "Must be a U.S. Citizen or National of the U.S., an alien lawfully admitted for permanent residence, or an alien authorized to work",
            "US citizen, permanent resident or otherwise authorized to work for Michelin", "U.S. citizenship is not required", "No security clearance needed",
            "Must be eligible for a Canadian security clearance (Reliability Status)", "This role needs BPSS security clearance", "Great citizen of the US open source community"]
    wrong = [x for x in fine if us_only(x)]
    check(f"ordinary text isn't read as US citizens only {wrong or ''}", not wrong)
    real = ["U.S. citizenship is required", "Must be a U.S. citizen", "Candidates must be US citizens able to obtain a Secret clearance", "Applicant must be a (i) U.S. citizen or national, (ii) lawful permanent resident",
            "Must be a United States Citizen or Permanent Resident", "This position requires the ability to obtain a security clearance", "Ability to obtain a Secret clearance", "ITAR Requirements: to conform to export regulations",
            "this position is open only to applicants who qualify as “U.S. Persons”", "you meet the status of a U.S. Person", "requires U.S. citizenship", "Due to contract requirements, U.S. Citizenship or Permanent Work Authorization is required", "U.S. citizenship is a pre-requisite"]
    missed = [x for x in real if not us_only(x)]
    check(f"what really says it is still caught {missed or ''}", not missed)
    check("...and it reaches the flag", "us_citizen" in detect_flags("Intern", "U.S. citizenship is required") and "us_citizen" not in detect_flags("Intern", "regardless of military status"))
    check("SimplifyJobs saying so still counts", "us_citizen" in detect_flags("Intern", "", "U.S. Citizenship is Required"))

    # postings stored with the old flag get it cleared
    now = db.now()
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO jobs (id, source, company, company_key, title, url, description, flags, active, first_seen, last_seen, changed_at) VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
                      [("old-mil", "greenhouse:x", "Polaris", "polaris", "Intern", "https://x.test/a", "military and commercial vehicles", '["us_citizen"]', now, now, now),
                       ("old-real", "greenhouse:y", "Leidos", "leidos", "Intern", "https://x.test/b", "U.S. citizenship is required", '["us_citizen"]', now, now, now),
                       ("old-simp", "simplify:z", "Acme", "acme", "Intern", "https://x.test/c", "Build things", '["us_citizen"]', now, now, now),
                       ("old-none", "greenhouse:z", "Beta", "beta", "Intern", "https://x.test/d", "", '["us_citizen"]', now, now, now)])
    db.meta_set("flags_version", "4")
    store.refresh_flags()
    has = lambda i: "us_citizen" in db.loads(db.row("SELECT flags FROM jobs WHERE id=?", (i,))["flags"], [])  # noqa: E731
    check("a stored posting wrongly flagged gets it cleared", not has("old-mil") and has("old-real"))
    check("...while SimplifyJobs' word and postings with no text to re-read keep it", has("old-simp") and has("old-none"))
    with db.tx() as c:
        c.execute("DELETE FROM jobs WHERE id LIKE 'old-%'")
    store.invalidate_cache()


def closed_since_the_crawl():
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES (?,?,?,?,?)",
                        ("Cy", db.dumps({"skills": ["Python"]}), db.dumps({"categories": ["Software"]}), now, now)).lastrowid
    posting("was-open", "Autodesk", "Software Development Intern", ["Canada"], ["Toronto, ON"])
    posting("rolling", "Cambridge", "Software Intern", ["Canada"], ["Toronto, ON"])
    with db.tx() as c:
        c.execute("UPDATE jobs SET description='' WHERE id IN ('was-open', 'rolling')")
    store.invalidate_cache()
    real = jobdetail.fetch_description
    pages = {"https://x.test/was-open": "No longer accepting applications. Software Development Intern at Autodesk.",
             "https://x.test/rolling": "We recruit on a rolling basis: this posting stays open until the position is filled. " + "Build things. " * 20}
    jobdetail.fetch_description = lambda url: pages.get(url, "")
    try:
        check("a posting whose page says it's closed comes back with nothing...", jobdetail.ensure_description("was-open") == "")
        check("...and leaves the list and the picks", "was-open" not in {j["id"] for j in store.scored_jobs(pid)} and "was-open" not in {j["id"] for j in autopilot.candidates(pid, min_score=0, limit=50)})
        check("'open until the position is filled' isn't closed", "rolling basis" in jobdetail.ensure_description("rolling") and "rolling" in {j["id"] for j in store.scored_jobs(pid)})
    finally:
        jobdetail.fetch_description = real


def refused_says_why():
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES (?,?,?,?,?)",
                        ("Rae", db.dumps({"skills": ["Python"]}), db.dumps({"categories": ["Software"], "citizenship": "Canadian"}), now, now)).lastrowid
    posting("needs-us", "Lockheed", "Software Intern", ["US"], ["Denver, CO"], flags=["us_citizen"])
    store.invalidate_cache()
    r = agent.call(pid, "queue_application", {"job_id": "needs-us", "why": "x"}, source="test")
    check("a refused posting says why, whichever rule refused it", "US citizens" in r.get("error", ""))


def posting(job_id: str, company: str, title: str, regions: list[str], locations: list[str], cat: str = "Software", flags: list[str] | None = None):
    now = db.now()
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO jobs (id, source, company, company_key, title, url, locations, regions, category, skills, flags, active, first_seen,"
                  " last_seen, changed_at, posted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (job_id, "test", company, store.company_key(company), title, f"https://x.test/{job_id}", db.dumps(locations), db.dumps(regions), cat,
                   db.dumps(["Python", "SQL"]), db.dumps(flags or []), 1, now, now, now, now))


def scoring():
    job = {"title": "Software Engineering Intern", "category": "Software", "skills": ["Python"], "first_seen": 0}
    prof = {"skills": ["Python"], "year_of_study": 2}
    prefs = {"categories": ["Software"], "locations": ["Canada", "Remote"], "citizenship": "Canadian", "us_open": False}
    parts = lambda s: next(r for r in s[1] if r["k"] == "parts")["v"]  # noqa: E731
    abroad = parts(matching.score({**job, "regions": ["Remote", "Other"], "locations": ["IN, Remote (IN)"]}, prof, prefs))
    home = parts(matching.score({**job, "regions": ["Remote", "Canada"], "locations": ["Remote, Canada"]}, prof, prefs))
    anywhere = parts(matching.score({**job, "regions": ["Remote"], "locations": ["Remote"]}, prof, prefs))
    check("remote in another country isn't remote for you", abroad["Location"] == 0 and home["Location"] == 15 and anywhere["Location"] == 15)
    for where in (["Colombia, Remote"], ["Remote-MO"], ["Remote in UK"], ["IN", "Remote (IN)"]):
        check(f"...{where[-1]} isn't either", parts(matching.score({**job, "regions": ["Remote"], "locations": where}, prof, prefs))["Location"] == 0)
    for where in (["Remote"], ["Remote/Homebased"], ["Remote Globally"], ["Remote - Nationwide"]):
        check(f"...{where[0]} still is", parts(matching.score({**job, "regions": ["Remote"], "locations": where}, prof, prefs))["Location"] == 15)
    us = {**prefs, "citizenship": "US", "locations": ["US", "Remote"]}
    check("...and a US student keeps Remote-MO", parts(matching.score({**job, "regions": ["Remote"], "locations": ["Remote-MO"]}, prof, us))["Location"] == 15)
    plain = matching.score({**job, "regions": ["Canada"], "locations": ["Toronto"]}, prof, prefs)[0]
    desk = matching.score({**job, "title": "IT Co-op, Help Desk", "regions": ["Canada"], "locations": ["Guelph"]}, prof, prefs)[0]
    check("a help desk doesn't top a first-year's list either", desk < plain)
    ms = matching.score({**job, "title": "Intern, MS", "flags": ["masters"], "regions": ["Canada"], "locations": ["Toronto"]}, {**prof, "degree_level": "Bachelor's"}, prefs)[0]
    mine = matching.score({**job, "title": "Intern, MS", "flags": ["masters"], "regions": ["Canada"], "locations": ["Toronto"]}, {**prof, "degree_level": "Master's"}, prefs)[0]
    check("an MS role drops for an undergrad, not for a master's student", ms < mine - 15)


def picks():
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES (?,?,?,?,?)",
                        ("Pat", db.dumps({"skills": ["Python", "SQL"], "year_of_study": 2}),
                         db.dumps({"categories": ["Software"], "locations": ["Canada", "Remote"], "citizenship": "Canadian", "us_open": True}), now, now)).lastrowid
    posting("us1", "Faraway", "Software Engineering Intern", ["US"], ["New York, NY"])
    posting("ca1", "Nearby", "Software Engineering Intern", ["Canada"], ["Toronto, ON"])
    posting("drw1", "DRW", "Software Engineer Intern", ["Canada"], ["Toronto, ON"])
    posting("drw2", "DRW - University Jobs", "Software Developer Intern", ["Canada"], ["Toronto, ON"])
    posting("bmo1", "BMO", "Software Developer Intern", ["Canada"], ["Toronto, ON"])
    posting("bmo2", "Bank of Montreal", "Software Engineering Co-op", ["Canada"], ["Toronto, ON"])
    posting("td1", "TD", "Software Developer Intern", ["Canada"], ["Toronto, ON"])
    posting("td2", "TD Synnex", "Software Engineering Intern", ["Canada"], ["Toronto, ON"])
    posting("mfg", "Metalworks", "Manufacturing Engineering Intern", ["Canada"], ["Toronto, ON"], cat="Software")
    posting("phd", "Deep Lab", "Research Intern (PhD)", ["Canada"], ["Toronto, ON"])
    store.invalidate_cache()
    got = autopilot.candidates(pid, min_score=0, limit=20)
    ids = [j["id"] for j in got]
    check("a role where you picked comes before one that needs a visa, all else equal", ids.index("ca1") < ids.index("us1"))
    check("...and says why", "in a place you picked" in next(j for j in got if j["id"] == "ca1")["pick_reasons"])
    check("one application per employer, however the name is written", len({"drw1", "drw2"} & set(ids)) == 1 and len({"bmo1", "bmo2"} & set(ids)) == 1)
    check("...without merging different companies", {"td1", "td2"} <= set(ids))
    scored = {j["id"]: j for j in store.scored_jobs(pid)}
    check("the corrections reach what's scored: a manufacturing role isn't a software match, whatever was stored", scored["mfg"]["category"] == "Hardware"
          and scored["mfg"]["score"] < scored["ca1"]["score"] - 15)
    check("...and a PhD role is well below for an undergrad, even with no flag stored", "phd" in scored["phd"]["flags"] and scored["phd"]["score"] < scored["ca1"]["score"] - 25)


def main():
    db.init()
    titles()
    degrees()
    scoring()
    us_only_rule()
    refused_says_why()
    closed_since_the_crawl()
    picks()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
