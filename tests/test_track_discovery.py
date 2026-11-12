"""New kinds of role found in the feed (track_discovery.py), on a made-up feed: Forward Deployed Engineer spreading to
new companies is found and called new; one company's big program (32 postings at one firm) isn't a kind of role;
"GTM" and "Go-to-Market" are one track; cities, terms, years and the company's own name never make a phrase; a role
a shipped track already names stays out; one plain word isn't a role, an acronym in capitals is; the same feed
always gives the same tracks; and stored tracks go active → steady → faded, or fold into a shipped track.

Run: .venv/bin/python -m tests.test_track_discovery
"""

import os
import random
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-discovery-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import db, tracks  # noqa: E402
from coopcoach import track_discovery as td  # noqa: E402

results: dict[str, bool] = {}
DAY = 86400
NOW = 1_800_000_000


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


_n = [0]


def row(company, title, days_ago=5, where="Toronto, ON, Canada", active=1):
    _n[0] += 1
    return {"id": f"j{_n[0]}", "company": company, "company_key": company.lower(), "title": title,
            "locations": [where], "flags": [], "category": "Other", "tier": "other",
            "first_seen": NOW - days_ago * DAY, "posted_at": None, "active": active}


def feed(extra=()):
    rows = []
    # an old, steady background so the feed has history before the last 30 days
    for i in range(40):
        rows.append(row(f"Old Co {i}", "Software Engineering Intern", days_ago=100 + i % 15))
    for i in range(7):  # Forward Deployed Engineer: seven companies (nine postings), all in the last 30 days
        rows.append(row(f"Agent Co {i}", "Forward Deployed Engineer Intern" if i % 2 else
                        "Forward Deployed Software Engineer, Internship - Summer 2027", days_ago=3 + i))
    rows += [row("Agent Co 0", "Forward Deployed Engineer Intern (AI)", 4), row("Agent Co 1", "FDE Intern", 5),
             row("Agent Co 2", "Software Engineer (FDE)", 6)]
    for i in range(32):  # one company's big program
        rows.append(row("Bigfirm", f"Deployment Strategist Intern - {['US Government', 'Commercial'][i % 2]}", days_ago=20))
    for i in range(4):
        rows.append(row(f"Field Co {i}", "Deployment Strategist Intern", days_ago=20))
    for i in range(5):  # GTM, written two ways
        rows.append(row(f"Startup {i}", "GTM Intern", days_ago=40 + i))
        rows.append(row(f"Startup {i}", "GTM Associate Intern", days_ago=50 + i))
    for i in range(3):
        rows.append(row(f"Scaleup {i}", "Go-to-Market Strategy Intern - Toronto", days_ago=45))
    for i in range(10):  # a city, a term and a team in every title: none of them is a role
        rows.append(row(f"Bank {i}", f"Toronto Summer 2027 Team Intern {2026 + i % 2}", days_ago=60))
    for i in range(10):  # a role a shipped track names (Mobile)
        rows.append(row(f"App Co {i}", "iOS Developer Intern", days_ago=70))
    for i in range(10):  # one plain word, not a role
        rows.append(row(f"Uni Lab {i}", "Thesis Intern", days_ago=70))
    for i in range(6):  # an acronym written in capitals
        rows.append(row(f"Plant {i}", "EHS Intern", days_ago=70))
        rows.append(row(f"Plant {i}", "EHS Coordinator Intern", days_ago=72))
    return rows + list(extra)


def by_label(found):
    return {d["label"]: d for d in found}


def main():
    db.init()
    rows = feed()
    found = td.find(rows, NOW, baseline=0)
    got = by_label(found)
    fde = got.get("Forward Deployed Engineer")
    check("Forward Deployed Engineer is found, with the ways it's written", fde and "forward deployed" in fde["phrases"]
          and fde["stats"]["companies"] == 7 and "fde" in fde["phrases"])
    check("...and it's new: no company posted it before the last 30 days", fde and fde["trend"] == "new")
    check("one company's 32 postings plus four others aren't a kind of role (at most 3 count per company)",
          not any("strategist" in d["label"].lower() for d in found))
    more = td.find(rows + [row(f"Field Co {i}", "Deployment Strategist Intern", days_ago=20) for i in range(4, 7)], NOW)
    check("...three more companies posting it, and it is", any(d["label"] == "Deployment Strategist" for d in more))
    gtm = got.get("GTM (Go-to-Market)")
    check("GTM and Go-to-Market are one track", gtm and set(gtm["phrases"]) >= {"gtm", "go to market"}
          and gtm["stats"]["companies"] == 8 and not any(d["label"].startswith("Go-to-Market") for d in found))
    words = {w for d in found for p in d["phrases"] for w in p.split()}
    check("no city, term, year, team or company name makes a phrase",
          not words & {"toronto", "summer", "2027", "2026", "team", "agent", "bigfirm", "startup", "intern"})
    ios = got.get("iOS Developer")
    check("a role a shipped track already names is marked covered (and never stored)", ios and ios["covered"] == "role:mobile")
    check("one plain word isn't a role; an acronym written in capitals is", "Thesis" not in got and "EHS" in got)
    shuffled = rows[:]
    random.Random(4).shuffle(shuffled)
    check("the same feed gives the same tracks, in the same order", td.find(shuffled, NOW) == found)
    check("a young feed (under a month before the last 30 days) calls nothing new or rising",
          all(d["trend"] == "established" for d in td.find(rows, NOW, baseline=NOW - 40 * DAY)))
    rising = td.find(rows + [row(f"Late Co {i}", "Agentic Engineer Intern", days_ago=d) for i, d in
                             enumerate([2, 3, 4, 5, 6, 7, 8, 9, 50, 80])], NOW)
    th = next((d for d in rising if d["label"] == "Agentic Engineer"), None)
    check("rising: twice as many companies in the last 30 days as a usual month before", th and th["trend"] == "rising"
          and th["stats"]["pace"] >= 2)
    check("the card says it in one line", td.card(th).startswith(
        "Rising · Agentic Engineer · 10 open at 10 companies · ") and td.card(th).endswith("× this month") if th else False)

    # stored tracks over time
    r = td.run(NOW, rows)
    stored = {d["id"]: d for d in td.listed()}
    check("a run stores what it found (never the covered ones), active", "forward-deployed" in stored
          and stored["forward-deployed"]["status"] == "active" and "ios" not in stored and r["found"] >= 2)
    check("...and notes the top candidates for the sync report", db.loads(db.meta_get("track_discovery_report"), []))
    check("...once a day", td.run_if_due(NOW + 3600) == {} and td.run_if_due(NOW + 21 * 3600) != {})
    quiet = [x for x in rows if "Forward Deployed" not in x["title"]]
    td.run(NOW + 10 * DAY, quiet)
    check("gone from the feed for a while: steady (still listed)", td.get("forward-deployed")["status"] == "steady")
    td.run(NOW + 45 * DAY, quiet)
    check("...a month on: faded, out of ＋ Tracks, but a pin still opens it",
          td.get("forward-deployed")["status"] == "faded" and tracks.get(0, "auto:forward-deployed") is not None
          and "auto:forward-deployed" not in {t["id"] for t in tracks.all_tracks(0)})
    roles = [("role:infra", __import__("re").compile(r"\bforward deployed\b", 2), lambda j: True)]
    td.REVISION += 1
    folded = td.find(rows, NOW + 50 * DAY, roles=roles)
    check("a shipped track that later names it covers it", next(d for d in folded if d["id"] == "forward-deployed")
          ["covered"] == "role:infra")
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
