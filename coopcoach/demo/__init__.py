"""A demo anyone can run: python -m coopcoach demo

Builds a separate database (data-demo/, never your own) for a fictional student, Alex Chen, from a snapshot of public
postings and events (postings.json, events.json: dates are stored relative to the export, so they stay current), with
applications at every stage, a pasted interview invite, Autopilot's prepared applications and brief, saved events and
a reminder. No AI and no network are needed: matching, the resume checker and the tracker all work with built-in
rules, and the scheduler (syncs, inbox, Autopilot) stays off.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent

RESUME = """Alex Chen
alex.chen@example.com | 416-555-0123 | Toronto , ON | github.com/alexchen-demo | linkedin.com/in/alexchen-demo

EDUCATION
University of Waterloo | Waterloo, ON
Bachelor of Computer Science, Co-op | Sept 2023 - Apr 2028

EXPERIENCE
Software Engineer Inter | Northwind Analytics | Toronto, ON | MAy 2025 - Aug 2025
- Built a FastAPI service that ingests 2M daily events into PostgreSQL, cutting dashboard load time by 40%.
- Wrote React components for an internal metrics dashboard used by 30 analysts.
- Added pytest coverage to the billing pipeline (from 35% to 80%).

Data Engineering Intern | Maple Health | Waterloo, ON | Sept 2024 - Dec 2024
- Automated a nightly ETL job in Python and Airflow that replaced 6 hours of manual work a week.
- Built anomaly alerts for claims data with pandas and scikit-learn.

PROJECTS
Pathfinder: Campus Route Planner | TypeScript, Next.js, Mapbox
- Shortest-path routing across 120 campus buildings with accessibility filters; 1.5k monthly users.
TradeLens: Paper Trading Bot | Python, Go, Docker
- Backtested momentum strategies on 5 years of minute data; a Go order simulator handles 50k orders/s.

SKILLS
Languages: Python, TypeScript, Go, Java, SQL, C++,
Tools: FastAPI, React, Next.js, PostgreSQL, Docker, AWS, Airflow, pandas, scikit-learn, Git
"""

APPS = [  # (company to look for in the snapshot, status, days since applied, extras)
    ("startup", "interviewing", 12, {"book": True}),
    ("quant", "oa", 6, {"oa_days": 4}),
    ("bigtech", "applied", 9, {}),
    ("bigtech", "applied", 25, {}),
    ("finance", "applied", 3, {}),
    ("yc", "rejected", 30, {}),
    ("startup", "saved", 0, {}),
    ("other", "saved", 0, {}),
]


def build(data_dir: Path) -> None:
    """Fill a fresh database in data_dir (the caller has pointed COOPCOACH_DATA at it before importing coopcoach)."""
    from .. import autopilot, db, events, routes_docs, server, store

    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json) VALUES (?,?,?,?)",
                        ("Alex", now, now, db.dumps({
                            "locations": ["Canada", "US", "Remote", "Toronto", "Waterloo"], "citizenship": "Canadian",
                            "terms": ["Summer 2027", "Winter 2027"], "aim": "balanced", "us_open": True,
                            "autopilot": {"on": False, "notify": False, "prep": False}, "track_email": False}))).lastrowid
    server._ingest_resume(pid, "alex_chen_resume.txt", RESUME.encode(), background_ai=False)

    snap = json.loads((HERE / "postings.json").read_text())["postings"]
    jobs = []
    for p in snap:
        j = {**p, "description": "", "posted_at": now - p.pop("posted_ago", 0), "form_checked": 0}
        jobs.append(j)
    store.upsert_jobs(jobs, now)
    store.refresh_flags()

    with db.tx() as c:  # one source per kind of event, like a real install
        feeds = {}
        for e in json.loads((HERE / "events.json").read_text())["events"]:
            key = (e["source"] or "Events", e["group"])
            if key not in feeds:
                feeds[key] = c.execute("INSERT INTO event_feeds (url, label, career_only, created_at, grp, last_status)"
                                       " VALUES (?,?,1,?,?,'ok')", (f"https://demo.invalid/{len(feeds)}", key[0], now,
                                                                    key[1])).lastrowid
            start = now + e["starts_in"]
            c.execute("INSERT INTO events (id, feed_id, title, start_ts, end_ts, all_day, location, url, description, kind,"
                      " first_seen, last_seen, region) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (e["uid"], feeds[key], e["title"], start, start + e["length"], int(e["all_day"]), e["location"],
                       e["url"], "", e["kind"], now, now, e["region"]))

    scored = store.scored_jobs(pid)
    by_tier: dict[str, list] = {}
    for j in sorted(scored, key=lambda x: -x["score"]):  # Alex's best fits, like Autopilot would pick
        if j.get("eligibility") != "no" and j["category"] in ("Software", "AI/ML/Data", "Quant"):
            by_tier.setdefault(j["tier"], []).append(j)
    used = set()

    def take(tier):
        for j in by_tier.get(tier, []):
            if j["company_key"] not in used:
                used.add(j["company_key"])
                return j
        return None

    with db.tx() as c:
        for tier, status, days, extra in APPS:
            j = take(tier)
            if not j:
                continue
            notes = "Book a time: https://calendly.com/demo-recruiter/intro" if extra.get("book") else None
            c.execute("INSERT INTO applications (profile_id, job_id, status, notes, source, applied_at, updated_at,"
                      " interview_at, oa_due_at, last_email_at, last_email_subject) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, j["id"], status, notes, "board", now - days * 86400 if status != "saved" else None,
                       now - max(days - 2, 0) * 86400, now + 3 * 86400 + 5 * 3600 if extra.get("book") else None,
                       now + extra["oa_days"] * 86400 if extra.get("oa_days") else None,
                       now - 86400 if status in ("interviewing", "oa", "rejected") else None,
                       {"interviewing": f"Next steps with {j['company']}", "oa": f"{j['company']} online assessment",
                        "rejected": f"Update on your {j['company']} application"}.get(status)))
            if status in ("interviewing", "oa", "rejected"):
                c.execute("INSERT INTO email_events (id, profile_id, message_id, received_at, from_name, from_addr, subject,"
                          " snippet, status, company, role, job_id, classified_by, changed_status, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f"demo-{j['id']}", pid, f"demo-{j['id']}", now - 86400, f"{j['company']} Recruiting",
                           "recruiting@example.com", f"About your {j['title']} application", "(demo email)", status,
                           j["company"], j["title"], j["id"], "rules", 1, now))
        for n, tier in enumerate(("startup", "bigtech", "quant")):
            j = take(tier)
            if j:
                c.execute("INSERT INTO ready_queue (profile_id, job_id, status, why, source, run_id, created_at, updated_at)"
                          " VALUES (?,?,?,?,?,?,?,?)", (pid, j["id"], "ready", f"Strong {j['category']} fit for your "
                                                        "Python / TypeScript work; open to students in Canada.",
                                                        "autopilot", "demo-run", now - 3600, now - 3600))
        c.execute("INSERT INTO autopilot_runs (profile_id, started_at, finished_at, trigger, engine, run_id, queued, brief)"
                  " VALUES (?,?,?,?,?,?,?,?)", (pid, now - 3700, now - 3600, "schedule", "rules", "demo-run", 3,
                                                "I prepared 3 applications for you to review and submit.\n\n"
                                                "Needs you: one interview to book and an online assessment due this week."))
        upcoming = events.upcoming(30)
        for e in [x for x in upcoming if x["region"] in ("Waterloo Region", "Toronto / GTA")][:2]:
            c.execute("INSERT INTO event_marks (profile_id, event_id, mark, created_at) VALUES (?,?,?,?)",
                      (pid, e["id"], "interested", now))
        c.execute("INSERT INTO reminders (profile_id, title, due_at, created_at) VALUES (?,?,?,?)",
                  (pid, "Follow up on applications from last month", now + 2 * 86400, now))
    routes_docs.build_master(pid, use_ai=False)
    autopilot.save_settings(pid, {"on": False})
    db.meta_set("ai_engine", "auto")
    db.meta_set("demo", "1")
