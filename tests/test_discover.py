"""Finding new job boards: board names read from Common Crawl's index (all three ATS hosts, junk paths dropped, a
busy index server asked again), each board checked live with its company name from the board itself, and only the
live new ones added (internships open: read next sync; none: spread over the day). Boards someone removed stay
removed. Nothing here touches the network: Common Crawl and the boards are stand-ins.

Run: .venv/bin/python -m tests.test_discover
"""

import json
import os
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-discover-"), GROQ_API_KEY="",
                  ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import db, discover  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


CDX = "https://index.example/CC-TEST-index"
CRAWL = {
    "jobs.ashbyhq.com": ["https://jobs.ashbyhq.com/Rocketship", "https://jobs.ashbyhq.com/rocketship/123abc",
                         "https://jobs.ashbyhq.com/favicon.ico", "https://jobs.ashbyhq.com/oldco"],
    "boards.greenhouse.io": ["https://boards.greenhouse.io/embed/job_board?for=widgetco&b=x",
                             "https://boards.greenhouse.io/deadco/jobs/1"],
    "job-boards.greenhouse.io": ["https://job-boards.greenhouse.io/widgetco/jobs/99",
                                 "https://job-boards.greenhouse.io/removedco"],
    "jobs.lever.co": ["https://jobs.lever.co/leverly/abc", "https://jobs.lever.co/emptyco"],
}
busy = {"left": 1}
asked = []


def handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    asked.append(u)
    if u.startswith("https://index.commoncrawl.org/collinfo.json"):
        return httpx.Response(200, json=[{"cdx-api": CDX}])
    if u.startswith(CDX):
        host = req.url.params["url"].split("/")[0]
        if req.url.params.get("showNumPages"):
            return httpx.Response(200, json={"pages": 1})
        if host == "jobs.lever.co" and busy["left"]:
            busy["left"] -= 1
            return httpx.Response(504)
        return httpx.Response(200, text="\n".join(json.dumps({"url": x}) for x in CRAWL[host]))
    if "raw.githubusercontent.com" in u:
        return httpx.Response(200, json=[{"url": "https://jobs.ashbyhq.com/simplyfound/1", "company_name": "Simply Found"}])
    boards = {
        "https://boards-api.greenhouse.io/v1/boards/widgetco/jobs": {"jobs": [{"title": "Software Engineer Intern"},
                                                                          {"title": "Staff Engineer"}]},
        "https://boards-api.greenhouse.io/v1/boards/widgetco": {"name": "WidgetCo"},
        "https://api.ashbyhq.com/posting-api/job-board/rocketship": {"jobs": [{"title": "Account Executive"}]},
        "https://api.ashbyhq.com/posting-api/job-board/simplyfound": {"jobs": [{"title": "Data Intern"}]},
        "https://api.lever.co/v0/postings/leverly?mode=json": [{"text": "Design Co-op"}],
        "https://api.lever.co/v0/postings/emptyco?mode=json": [],
    }
    if u in boards:
        return httpx.Response(200, json=boards[u])
    if u == "https://jobs.ashbyhq.com/rocketship":
        return httpx.Response(200, text="<html><title>Rocketship Jobs</title></html>")
    if u == "https://jobs.lever.co/leverly":
        return httpx.Response(200, text="<html><title>Leverly Inc.</title></html>")
    return httpx.Response(404)


def main():
    db.init()
    discover._client = lambda timeout=30: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    discover.time.sleep = lambda s: None
    with db.tx() as c:  # one board already read, one someone removed
        c.execute("INSERT INTO companies (ats, slug, name, origin) VALUES ('ashby', 'oldco', 'OldCo', 'curated')")
        c.execute("INSERT INTO companies (ats, slug, name, origin, removed) VALUES ('greenhouse', 'removedco',"
                  " 'RemovedCo', 'you', 1)")

    found = discover.crawl_slugs(1)
    check("Common Crawl's index gives every board slug on all three hosts (embeds too), junk paths dropped",
          found == {("ashby", "rocketship"), ("ashby", "oldco"), ("greenhouse", "widgetco"), ("greenhouse", "deadco"),
                    ("greenhouse", "removedco"), ("lever", "leverly"), ("lever", "emptyco")})
    check("...and a busy index server (504) is asked again", busy["left"] == 0)

    w = discover.check("greenhouse", "widgetco")
    check("a live board's company name comes from the board, with its roles and internships counted",
          w == {"ats": "greenhouse", "slug": "widgetco", "name": "WidgetCo", "roles": 2, "interns": 1})
    check("Ashby's and Lever's names come from their board pages",
          discover.check("ashby", "rocketship")["name"] == "Rocketship"
          and discover.check("lever", "leverly")["name"] == "Leverly Inc.")
    check("a dead board, or one with no roles, isn't added", discover.check("greenhouse", "deadco") is None
          and discover.check("lever", "emptyco") is None)

    busy["left"] = 0
    out = discover.run(1)
    rows = {(r["ats"], r["slug"]): r for r in db.rows("SELECT * FROM companies")}
    check("new live boards are added (from Common Crawl and SimplifyJobs links), nothing dead",
          {("greenhouse", "widgetco"), ("ashby", "rocketship"), ("lever", "leverly"), ("ashby", "simplyfound")}
          <= set(rows) and ("greenhouse", "deadco") not in rows and out["added"] == 4)
    check("...a board someone removed stays removed", rows[("greenhouse", "removedco")]["removed"] == 1)
    check("...one with internships is read next sync; one without is spread over the day",
          rows[("greenhouse", "widgetco")]["last_count"] == 1 and rows[("greenhouse", "widgetco")]["last_checked"] is None
          and rows[("ashby", "rocketship")]["last_checked"] is not None)
    check("...and what it found is noted for Settings", out["with_internships"] == 3
          and discover.status()["last"]["added"] == 4 and not discover.due())
    with TestClient(app, base_url="http://localhost") as cl:
        s = cl.get("/api/discover").json()
        check("Settings can read how the last search for boards went", s["last"]["added"] == 4 and not s["running"])
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
