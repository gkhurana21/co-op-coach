"""Does a company hire interns, and the New AI startups track. Scratch data only.

Run: .venv/bin/python -m tests.test_interns
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-interns-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import db, interns, store, tracks  # noqa: E402
from coopcoach.textutil import company_key  # noqa: E402

DAY = 86400


def add(c, jid, source, company, title, active, terms="[]", posted=None, tier="startup", category="Software"):
    now = db.now()
    c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, locations, terms, tier, category, active,"
              " posted_at, first_seen, last_seen, changed_at) VALUES (?,?,?,?,?,?,'[\"Toronto, ON\"]',?,?,?,?,?,?,?,?)",
              (jid, source, company, company_key(company), title, f"https://x/{jid}", terms, tier, category, active,
               posted, now - 20 * DAY, now, now))


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Robin', ?, ?)", (now, now)).lastrowid
        # Northwind: one internship open on its board and the same one on LinkedIn, plus an old one closed
        add(c, "n1", "ashby:northwind", "Northwind", "Software Engineer Intern (Winter 2027)", 1, '["Winter 2027"]', now - 5 * DAY)
        add(c, "n2", "linkedin", "Northwind", "Software Engineer Intern", 1, '["Winter 2027"]', now - 4 * DAY)
        add(c, "n3", "ashby:northwind", "Northwind", "Data Intern", 0, '["Summer 2026"]', now - 200 * DAY)
        # Globex: only full-time jobs
        add(c, "g1", "greenhouse:globex", "Globex", "Senior Platform Engineer", 1)
        # Initech: interns before, none now
        add(c, "i1", "lever:initech", "Initech", "Co-op Student, QA", 0, '["Fall 2026"]', now - 90 * DAY)
        # the AI track: a YC company hiring for AI, a startup named for AI, and a plain startup
        add(c, "a1", "yc:vecta", "Vecta", "Machine Learning Intern", 1, tier="yc", category="AI/ML/Data")
        add(c, "a2", "ashby:neuronet", "Neuronet AI", "Software Intern", 1)
        add(c, "a3", "ashby:shoebox", "Shoebox", "Software Intern", 1)
    store.invalidate_cache()

    checks: dict[str, bool] = {}
    r = interns.lookup("northwind")
    checks["a company with an internship open says yes, counting the LinkedIn copy once"] = \
        r["verdict"] == "yes" and r["open_count"] == 1 and r["answer"].startswith("Yes. Northwind has 1 internship open")
    checks["...and lists the seasons it hired for"] = r["seasons"] == ["Summer 2026", "Winter 2027"]
    r = interns.lookup("Initech")
    checks["interns before, none now: yes, with when they usually open"] = r["verdict"] == "yes" and \
        "None open right now" in r["answer"] and "usually open around" in r["answer"]
    r = interns.lookup("Globex")
    checks["only full-time jobs: not a yes"] = r["verdict"] == "unclear" and "no internships yet" in r["answer"]
    checks["a company never seen says so"] = interns.lookup("Hooli")["verdict"] == "unknown"
    checks["an empty question gets a hint, not a crash"] = "error" in interns.lookup(" ")
    checks["a company on a curated list is a yes even with nothing seen (Anthropic, AI labs)"] = \
        interns.lookup("Anthropic")["verdict"] == "yes"

    got = {j["company"] for j in tracks.members(pid, "co:ai-startups")}
    checks["New AI startups: a YC company hiring for AI and a startup named for AI, not a plain startup"] = \
        {"Vecta", "Neuronet AI"} <= got and "Shoebox" not in got
    checks["Tracks show in lean mode"] = "tracks" not in __import__("coopcoach.features", fromlist=["x"]).hidden()

    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print(f"\n{sum(bool(v) for v in checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
