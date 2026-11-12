"""The spreadsheet export: five sheets (summary, matches best-first, applications, ready to apply, startups), a filter
and a frozen header on each, colour rules for score / who can apply / kind of company / status, real dates and links;
postings they can't apply to left out unless asked for; through the API as an .xlsx; locked profiles stay locked.

Run: .venv/bin/python -m tests.test_export
"""

import io
import os
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-export-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from coopcoach import auth, db, export, store  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES (?,?,?,?,?)",
                        ("Alex", db.dumps({"skills": ["Python", "SQL"], "target_categories": ["Software"]}),
                         db.dumps({"locations": ["Toronto", "Canada"], "citizenship": "Canadian"}), now, now)).lastrowid
    raw = [("Shopify", "Software Engineer Intern", "Toronto, ON", "Python and SQL. Open to Canadian students."),
           ("Wealthsimple", "Backend Developer Co-op", "Toronto, ON", "Python, Postgres."),
           ("Usonly", "Software Engineer Intern", "Austin, TX", "Python. Must be a US citizen.")]
    jobs = [store.normalize({"source": f"lever:{co.lower()}", "company": co, "title": t, "url": f"https://jobs.lever.co/{co.lower()}/1",
                             "locations": [w], "description": d * 3, "posted_at": now - 86400}) for co, t, w, d in raw]
    store.upsert_jobs(jobs, now)
    with db.tx() as c:
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at, applied_at, interview_at) VALUES"
                  " (?,?,?,?,?,?)", (pid, jobs[1]["id"], "interviewing", now, now - 5 * 86400, now + 2 * 86400))
        c.execute("INSERT INTO ready_queue (profile_id, job_id, status, why, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                  (pid, jobs[0]["id"], "ready", "Python fit", now, now))
    store.invalidate_cache()

    wb = load_workbook(io.BytesIO(export.workbook(pid)))
    check("five sheets: summary, matches, applications, ready to apply, startups",
          wb.sheetnames == ["Summary", "Matches", "Applications", "Ready to apply", "Startups"])
    m = wb["Matches"]
    names = [m.cell(row=r, column=3).value for r in range(2, m.max_row + 1)]
    scores = [m.cell(row=r, column=1).value for r in range(2, m.max_row + 1)]
    check("matches are best-first, and ones they can't apply to are left out", scores == sorted(scores, reverse=True)
          and "Usonly" not in names and {"Shopify", "Wealthsimple"} <= set(names))
    check("every sheet has a filter on each column and a frozen header",
          all(wb[s].auto_filter.ref and wb[s].freeze_panes for s in wb.sheetnames[1:4]))
    rules = {r.formula[0] for cf in m.conditional_formatting for r in cf.rules if r.formula}
    check("colours by who can apply, kind of company and status, and a red→green scale for the score",
          any('"Open to you"' in f or '"Probably open"' in f for f in rules) and any('"Big Tech"' in f for f in rules)
          and any(r.type == "colorScale" for cf in m.conditional_formatting for r in cf.rules))
    row = {m.cell(row=1, column=c).value: m.cell(row=2, column=c) for c in range(1, m.max_column + 1)}
    check("dates are real dates, and each posting links to itself", row["Posted"].number_format == "yyyy-mm-dd"
          and row["Link"].hyperlink is not None and row["Link"].hyperlink.target.startswith("https://jobs.lever.co/"))
    a = wb["Applications"]
    check("applications: status, applied date and the interview time", a.cell(row=2, column=3).value == "Interviewing"
          and a.cell(row=2, column=7).value is not None)
    check("ready to apply: what Jarvis prepared and why", wb["Ready to apply"].cell(row=2, column=5).value == "Python fit")
    allwb = load_workbook(io.BytesIO(export.workbook(pid, everything=True)))
    check("...and 'everything' keeps the postings they can't apply to too", "Usonly" in [
        allwb["Matches"].cell(row=r, column=3).value for r in range(2, allwb["Matches"].max_row + 1)])

    with TestClient(app, base_url="http://localhost") as cl:
        r = cl.get(f"/api/export/xlsx?profile_id={pid}")
        check("the API sends it as an .xlsx download", r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
              and ".xlsx" in r.headers["content-disposition"] and r.content[:2] == b"PK")
        auth.set_passcode(pid, "maple-1234")
        check("a locked profile's spreadsheet stays locked", cl.get(f"/api/export/xlsx?profile_id={pid}").status_code == 423)
        auth.clear_passcode(pid)
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
