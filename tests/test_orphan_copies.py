"""Copies of a posting on LinkedIn, Indeed and Simplify close when the employer's own board no longer lists it.
Rebuilds the real case: Cohere took its Winter 2027 intern posting off Ashby, but the LinkedIn copy kept ranking at
the top of Matches and led to "Not currently accepting applications". Scratch data only.

Run: .venv/bin/python -m tests.test_orphan_copies
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-orphans-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="")

from coopcoach import db, store  # noqa: E402
from coopcoach.textutil import company_key  # noqa: E402

DAY = 86400


def add(c, jid, source, company, title, url, active, last_seen):
    c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, locations, active, first_seen, last_seen,"
              " changed_at) VALUES (?,?,?,?,?,?,'[]',?,?,?,?)",
              (jid, source, company, company_key(company), title, url, active, last_seen - 30 * DAY, last_seen, last_seen))


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        # Cohere: closed on its own board this week; the LinkedIn copy (a LinkedIn link, so matched by title) lingers
        add(c, "ash-cohere", "ashby:cohere", "Cohere", "Software Engineer Intern (Winter 2027)",
            "https://jobs.ashbyhq.com/cohere/8c035d3d-081d-4c8a-914a-72f4efaad254", 0, now - 4 * DAY)
        add(c, "li-cohere", "linkedin", "Cohere", "Software Engineer Intern (Winter 2027)",
            "https://www.linkedin.com/jobs/view/4407957965", 1, now - 10 * DAY)
        # Bree: still on its board, so the Simplify copy pointing at it stays open
        add(c, "ash-bree", "ashby:bree", "Bree", "Software Engineer, Product (Co-op)",
            "https://jobs.ashbyhq.com/bree/17d8dd15-5f97-4003-8d6c-170dca13ff88", 1, now - 4 * DAY)
        add(c, "sim-bree", "simplify", "Bree", "Software Engineer Co-op - Product",
            "http://jobs.ashbyhq.com/bree/17d8dd15-5f97-4003-8d6c-170dca13ff88/application?embed=true", 1, now - 10 * DAY)
        # Acme: its board was read after the Simplify copy was last seen, and no longer lists that posting
        add(c, "gh-acme", "greenhouse:acme", "Acme", "Data Intern", "https://job-boards.greenhouse.io/acme/jobs/111", 1, now - DAY)
        add(c, "sim-acme", "simplify", "Acme", "Platform Intern", "https://boards.greenhouse.io/acme/jobs/222", 1, now - 5 * DAY)
        # Globex: its board hasn't been read since the copy was seen, so nothing is concluded
        add(c, "gh-globex", "greenhouse:globex", "Globex", "QA Intern", "https://job-boards.greenhouse.io/globex/jobs/1", 1, now - 9 * DAY)
        add(c, "sim-globex", "simplify", "Globex", "Dev Intern", "https://job-boards.greenhouse.io/globex/jobs/2", 1, now - 3 * DAY)
        # an old closure (a month ago) doesn't close a copy listed under the same title today: it may be a re-post
        add(c, "ash-old", "ashby:initech", "Initech", "Intern", "https://jobs.ashbyhq.com/initech/aaaaaaaa-0000-0000-0000-000000000000", 0, now - 30 * DAY)
        add(c, "li-old", "linkedin", "Initech", "Intern", "https://www.linkedin.com/jobs/view/1", 1, now - DAY)

    checks: dict[str, bool] = {}
    preview = {j["id"] for j in store.close_orphan_copies(now, dry_run=True)}
    checks["a dry run changes nothing"] = db.row("SELECT active FROM jobs WHERE id='li-cohere'")["active"] == 1
    checks["Cohere's LinkedIn copy closes: Cohere took it off its own board"] = "li-cohere" in preview
    checks["Acme's Simplify copy closes: its board was read since and no longer lists it"] = "sim-acme" in preview
    checks["Bree's copy stays: the posting is still on Bree's board"] = "sim-bree" not in preview
    checks["Globex's copy stays: its board hasn't been read since the copy was seen"] = "sim-globex" not in preview
    checks["a month-old closure doesn't close today's same-titled listing"] = "li-old" not in preview
    store.close_orphan_copies(now)
    checks["closing marks exactly those postings closed"] = \
        {r["id"] for r in db.rows("SELECT id FROM jobs WHERE active=0")} == {"ash-cohere", "ash-old", "li-cohere", "sim-acme"}

    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
