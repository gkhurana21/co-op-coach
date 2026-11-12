"""Hackathon packages. Applying to attend: details from the resume, MLH's level of study, short answers drafted only
from real projects (an AI draft with an invented number is dropped), the student's own words kept, t-shirt / dietary /
travel remembered without touching other settings, MLH's boxes and demographics never answered, a zip to download,
the form filled the internship way, and the tracker's own statuses (kept out of the job numbers, no cover letter).
The Devpost write-up: public github.com repos only, read through the public-only client; "Built with" only from the
repo's languages, dependency files and topics; Devpost's own headings; [brackets] where only the team knows; prizes
only when the team says so; an AI section with an invented number replaced; the project onto the resume with undo.
And the internship form answers that used to land in the wrong field.

Run: .venv/bin/python -m tests.test_hackathon_packages
"""

import base64
import io
import json
import os
import re
import tempfile
import zipfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-hack-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, ai, apply, db, github, hackathons, mail, routes_docs  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


RESUME = {"header": {"name": "Robin Doe", "email": "robin@example.com", "phone": "5195550123", "location": "Waterloo, ON",
                     "links": ["github.com/robindoe", "linkedin.com/in/robindoe", "devpost.com/robindoe", "robin.dev"]},
          "education": [{"school": "University of Waterloo", "degree": "BASc Computer Engineering", "dates": "2024 - 2029",
                         "details": []}],
          "experience": [{"title": "Software Developer Intern", "company": "Acme", "dates": "May 2025 - Aug 2025",
                          "bullets": ["Built REST APIs in Python and FastAPI for the billing team"]}],
          "projects": [{"name": "Trailmix", "tech": "React, Node.js", "dates": "2025", "link": "github.com/robindoe/trailmix",
                        "bullets": ["Built a trip planner that suggests hiking routes", "Added offline maps with service workers"]}],
          "skills": [{"label": "Languages", "items": "Python, TypeScript, C++"}, {"label": "Tools", "items": "React, Git"}]}


def new_profile() -> int:
    now = db.now()
    prof = {"name": "Robin Doe", "university": "University of Waterloo", "program": "Computer Engineering",
            "degree_level": "Bachelor's", "year_of_study": 2, "grad_year": 2029, "skills": ["Python", "React"]}
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, resume_text, profile_json, prefs_json, created_at, updated_at) VALUES"
                        " (?,?,?,?,?,?)", ("Robin", "Robin Doe resume", db.dumps(prof),
                                           db.dumps({"resume_template": "jake", "locations": ["Toronto"]}), now, now)).lastrowid
    routes_docs._save_doc(pid, "resume_master", db.dumps(RESUME), "Master resume")
    return pid


def add_devpost_event() -> str:
    now = db.now()
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO event_feeds (url, label, career_only, created_at, grp) VALUES"
                  " ('https://devpost.com/hackathons', 'Devpost hackathons', 1, ?, 'Hackathons')", (now,))
        fid = c.execute("SELECT id FROM event_feeds WHERE url='https://devpost.com/hackathons'").fetchone()[0]
        c.execute("INSERT INTO events (id, feed_id, title, start_ts, end_ts, all_day, location, url, description,"
                  " kind, first_seen, last_seen, region) VALUES ('ev123', ?, 'GreenCode Jam 2026', ?, ?, 0, 'Online',"
                  " 'https://greencode.devpost.com/', 'Hackathon by Green Org. Sustainability, Machine Learning/AI',"
                  " 'hackathon', ?, ?, 'Online')", (fid, now + 30 * 86400, now + 32 * 86400, now, now))
    return "ev-ev123"


def apply_checks(pid: int, cl: TestClient):
    h = hackathons.find(pid, "hack-the-north")
    check("a major hackathon is found by its name, with its site and usual month", h and h["name"] == "Hack the North"
          and h["site"].startswith("https://hackthenorth") and h["when"] == "September")
    ev = add_devpost_event()
    e = hackathons.find(pid, ev)
    check("...and any other hackathon in Events by its id, with the themes Devpost lists",
          e and e["name"] == "GreenCode Jam 2026" and e["themes"] == ["Sustainability", "Machine Learning/AI"])

    kit = hackathons.app_kit(pid, "hack-the-north")
    you = {q["label"]: q["answer"] for q in kit["questions"] if q["group"] == "you"}
    check("your details come from the resume and profile (Devpost link included, not taken as your website)",
          you["First Name"] == "Robin" and you["School"] == "University of Waterloo" and you["Graduation Year"] == "2029"
          and you["Devpost"] == "https://devpost.com/robindoe" and you["Personal Website"] == "https://robin.dev"
          and you["GitHub"] == "https://github.com/robindoe")
    check("...with MLH's level of study", you["Level of Study"] == "Undergraduate University (3+ year)")
    ans = {q["id"]: q for q in kit["questions"] if q["group"] == "answers"}
    check("without an AI, the short answers start from real projects, with [brackets] for what only you can say",
          "Trailmix" in ans["project"]["answer"] and "trip planner" in ans["project"]["answer"]
          and "[" in ans["why"]["answer"] and kit["drafted_by"] == "rules")
    check("...and nothing claims hackathons the resume doesn't mention (the count is left to you)",
          ans["count"]["answer"] == "" and "first-timers" in ans["count"]["note"])
    labels = " ".join(q["label"] for q in kit["questions"])
    check("MLH's Code of Conduct and privacy boxes, and demographics, are never among the answers",
          not hackathons.YOURS.search(labels) and not apply._EEO.search(labels)
          and any("Code of Conduct" in x for x in kit["left_for_you"]))
    check("the kit has the internship kit's shape, so the same filler fills the form",
          kit["job"]["apply_url"] == h["site"] and kit["files"]["resume_pdf"].startswith("/api/resume/export"))

    # an AI draft: one answer is fine, one brings in a number the resume doesn't have
    real = (ai.available, ai._call_json)
    ai.available = lambda: True
    ai._call_json = lambda *a, **k: {"answers": [
        {"label": "Why do you want to attend GreenCode Jam 2026?", "answer": "I want to build for sustainability with Python."},
        {"label": "Tell us about a project you're proud of.", "answer": "I've won 3 hackathons with Trailmix."}]}
    try:
        k2 = hackathons.app_kit(pid, ev)
    finally:
        ai.available, ai._call_json = real
    calls = []
    ai.available, ai._call_json = (lambda: True), (lambda *a, **k: calls.append(1) or {"answers": []})
    try:
        k2b = hackathons.app_kit(pid, ev)
    finally:
        ai.available, ai._call_json = real
    check("the AI's drafts are kept, so opening it again doesn't ask the AI again", not calls and [
        q["answer"] for q in k2b["questions"] if q.get("id") == "why"][0].startswith("I want to build for sustainability"))
    a2 = {q["id"]: q for q in k2["questions"] if q["group"] == "answers"}
    check("an AI draft is used when it sticks to the resume (numbers from the hackathon's name are fine)",
          a2["why"]["answer"].startswith("I want to build for sustainability") and k2["drafted_by"] == "ai")
    check("...and one with an invented number is dropped for the resume-based start",
          "won 3" not in a2["project"]["answer"] and "Trailmix" in a2["project"]["answer"])

    label = ans["why"]["label"]
    untouched = {q["label"]: q["answer"] for q in kit["questions"] if q["group"] == "answers"}
    cl.put("/api/hackathons/hack-the-north/apply", json={"profile_id": pid, "answers": untouched})
    check("saving a draft you didn't change doesn't make it yours", all(
        q["note"] != "Yours" for q in hackathons.app_kit(pid, "hack-the-north")["questions"] if q["group"] == "answers"))
    r = cl.put("/api/hackathons/hack-the-north/apply", json={"profile_id": pid, "answers": {label: "My own reason.",
               "Not a question": "x"}, "prefs": {"tshirt": "M", "dietary": "Vegetarian", "evil": "x"}})
    k3 = hackathons.app_kit(pid, "hack-the-north")
    a3 = {q["label"]: q for q in k3["questions"]}
    check("your own words are kept (and only answers to its questions)",
          r.status_code == 200 and a3[label]["answer"] == "My own reason." and a3[label]["note"] == "Yours")
    raw = db.loads(db.row("SELECT prefs_json FROM profiles WHERE id=?", (pid,))["prefs_json"], {})
    check("t-shirt and dietary are remembered for the next hackathon, and nothing else in your settings changes",
          a3["T-shirt size"]["answer"] == "M" and raw["hackathon"] == {"tshirt": "M", "dietary": "Vegetarian"}
          and raw["locations"] == ["Toronto"] and "evil" not in json.dumps(raw))
    check("...and where you'd travel from starts as where you live",
          a3["Where will you be travelling from?"]["answer"] == "Waterloo, ON")
    k4 = hackathons.app_kit(pid, "hack-the-north", redraft=True)
    check("Redraft writes the answers again", {q["label"]: q for q in k4["questions"]}[label]["answer"] != "My own reason.")

    z = cl.get(f"/api/hackathons/hack-the-north/apply/download?profile_id={pid}")
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist() if z.status_code == 200 else []
    md = zipfile.ZipFile(io.BytesIO(z.content)).read([n for n in names if n.endswith(".md")][0]).decode() if names else ""
    check("the download has your resume and the answers, with what's left for you",
          any(n.endswith("_Resume.pdf") for n in names) and "## Left for you" in md and "Code of Conduct" in md)

    filled = []
    real_fill = apply.fill
    apply.fill = lambda pid_, job_id, kit=None, answers=None: filled.append(kit)
    try:
        r = cl.post("/api/hackathons/hack-the-north/apply/fill", json={"profile_id": pid})
    finally:
        apply.fill = real_fill
    check("Fill opens the hackathon's site with the kit (the student signs in and submits)",
          r.status_code == 200 and filled and filled[0]["job"]["apply_url"].startswith("https://hackthenorth"))

    letters = db.row("SELECT COUNT(*) AS n FROM documents WHERE kind='cover_letter'")["n"]
    r = cl.post("/api/hackathons/hack-the-north/track", json={"profile_id": pid, "status": "applied"})
    row = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id='hack:hack-the-north'", (pid,))
    apps = cl.get(f"/api/applications?profile_id={pid}").json()
    listed = [a for a in apps["applications"] if a["job_id"] == "hack:hack-the-north"]
    check("I applied puts it on the tracker as a hackathon application", r.status_code == 200 and row["source"] ==
          "hackathon" and row["company"] == "Hack the North" and row["status"] == "applied" and listed)
    cl.put("/api/applications", json={"profile_id": pid, "job_id": "hack:hack-the-north", "status": "offer"})
    check("...accepted is the tracker's offer, and no cover letter is written for it",
          db.row("SELECT status FROM applications WHERE job_id='hack:hack-the-north'")["status"] == "offer"
          and db.row("SELECT COUNT(*) AS n FROM documents WHERE kind='cover_letter'")["n"] == letters)
    check("...and it stays out of your internship numbers", mail.stats(pid)["applied_total"] == 0)

    out = agent.call(pid, "hackathon_application", {"hackathon": "hack the north"})
    check("the chat drafts one by name, and lists what's still yours to do",
          out.get("ok") and out["key"] == "hack:hack-the-north" and out["still_needs_you"])
    t = agent.call(pid, "track_hackathon", {"hackathon": "Hack the North", "status": "accepted"})
    check("...and tracks it (accepted = offer), with undo", t.get("status") == "offer" and t["_undo"]["args"]["status"]
          == "offer")
    check("an unknown hackathon is a clear error", "error" in agent.call(pid, "hackathon_application",
                                                                         {"hackathon": "Nonexistent Hacks 3000"}))


# --- a fake GitHub ----------------------------------------------------------------------------------------------

README = """# PantryPal

![build](https://img.shields.io/badge/build-passing-green)

PantryPal turns what's in your fridge into recipes. Snap a photo and it suggests three meals.

## Features
- Photo to ingredient list with a vision model
- Recipes ranked by what you already have
- Shopping list for what's missing

## Tech stack
- Next.js front end
- FastAPI service

## Roadmap
- Meal plans for the week
"""
asked: list[str] = []


def gh(req: httpx.Request) -> httpx.Response:
    asked.append(str(req.url))
    p = req.url.path
    if req.url.host == "api.github.com":
        if p == "/repos/team/pantrypal":
            return httpx.Response(200, json={"name": "pantrypal", "html_url": "https://github.com/team/pantrypal",
                                             "description": "Recipes from what's in your fridge", "topics": ["hackathon", "openai"],
                                             "homepage": "https://pantrypal.app", "default_branch": "main", "stargazers_count": 4})
        if p == "/repos/team/pantrypal/languages":
            return httpx.Response(200, json={"TypeScript": 9000, "Python": 4000, "CSS": 50})
        if p == "/repos/team/pantrypal/readme":
            return httpx.Response(200, json={"content": base64.b64encode(README.encode()).decode()})
        if p == "/repos/team/pantrypal/git/trees/main":
            return httpx.Response(200, json={"tree": [{"path": "package.json"}, {"path": "backend/requirements.txt"},
                                                      {"path": "node_modules/x/package.json"}, {"path": "a/b/c/go.mod"}]})
        if p.startswith("/repos/team/secret"):
            return httpx.Response(404)
        if p.startswith("/repos/team/limit"):
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    if req.url.host == "raw.githubusercontent.com":
        if p.endswith("/package.json"):
            return httpx.Response(200, text=json.dumps({"dependencies": {"next": "15", "react": "19", "openai": "4"},
                                                        "devDependencies": {"tailwindcss": "4"}}))
        if p.endswith("/backend/requirements.txt"):
            return httpx.Response(200, text="fastapi==0.115\nuvicorn\n# a comment\npillow>=10\n")
    return httpx.Response(404)


def devpost_checks(pid: int, cl: TestClient):
    real_client = github.client
    clients = []

    def fake_client(timeout=30, public_only=False):
        clients.append(public_only)
        return httpx.Client(transport=httpx.MockTransport(gh), follow_redirects=True)
    github.client = fake_client
    try:
        bad = [u for u in ("https://gitlab.com/team/x", "https://github.com.evil.test/team/x", "javascript:alert(1)",
                           "https://github.com/team", "github.com/../etc") if _parses(u)]
        check("only github.com repo links are read", bad == [] and github.parse("github.com/team/pantrypal.git") ==
              ("team", "pantrypal") and github.parse("https://github.com/team/pantrypal/tree/main") == ("team", "pantrypal"))
        rp = github.repo("https://github.com/team/pantrypal")
        check("...through the client that refuses this computer and the home network", clients and all(clients))
        check("Built with comes only from the repo: its main languages, dependency files (not node_modules), topics",
              rp["built_with"][:2] == ["typescript", "python"] and "css" not in rp["built_with"]  # (a sliver of CSS)
              and {"next.js", "react", "openai", "tailwind", "fastapi"} <= set(rp["built_with"])
              and "express.js" not in rp["built_with"] and rp["manifests"] == ["backend/requirements.txt", "package.json"])
        n = len(asked)
        github.repo("https://github.com/team/pantrypal")
        check("...and a repo read in the last six hours isn't read again", len(asked) == n)
        check("a private or missing repo says so plainly", "isn't public" in hackathons.submission(
            pid, "", "https://github.com/team/secret").get("error", ""))
        check("...and so does GitHub's hourly limit", "limit" in hackathons.submission(
            pid, "", "https://github.com/team/limit").get("error", ""))

        r = cl.post("/api/hackathons/hack-the-north/submission", json={
            "profile_id": pid, "repo": "https://github.com/team/pantrypal", "challenge": "Our vision model kept "
            "confusing eggs and lemons", "mine": "Built the FastAPI recipe ranking service"})
        s = r.json()
        heads = re.findall(r"^## (.+)$", s["markdown"], re.M)
        check("the write-up uses Devpost's own headings", r.status_code == 200 and heads == [
            "Inspiration", "What it does", "How we built it", "Challenges we ran into",
            "Accomplishments that we're proud of", "What we learned", "What's next for Pantrypal"])
        sec = s["sections"]
        check("...says what it does from the repo, its features from the README, and what it's built with",
              sec["what"].startswith("Recipes from what's in your fridge") and "Shopping list" in sec["what"]
              and "FastAPI" in sec["how"] and "Next.js" in sec["how"])
        check("...uses the team's own lines, and the README's roadmap for what's next",
              "eggs and lemons" in sec["challenges"] and "Meal plans" in sec["next"])
        check("...leaves [brackets] for what only the team knows, and claims no prize",
              sec["inspiration"].startswith("[") and sec["learned"].startswith("[") and "won" not in s["markdown"].lower()
              and s["brackets"] >= 3)
        check("the elevator pitch fits Devpost's 200 characters", 0 < len(s["tagline"]) <= 200)
        check("the demo script is two minutes and shows the product", "1:40-2:00" in s["video_script"]
              and "Photo to ingredient list" in s["video_script"])
        e = s["resume_entry"]
        check("the resume entry says what you did, with the stack and the repo, and adds no numbers",
              e["name"] == "Pantrypal" and e["tech"].startswith("TypeScript, Python") and e["link"] ==
              "https://github.com/team/pantrypal" and "Built the FastAPI recipe ranking service" in e["bullets"]
              and "at Hack the North" in e["bullets"][0] and not re.search(r"\d", " ".join(e["bullets"])))
        won = hackathons.submission(pid, "", "https://github.com/team/pantrypal", prize="Best Use of AI")
        check("a prize is only mentioned when the team says they won it", "We won Best Use of AI" in
              won["sections"]["proud"] and "Won Best Use of AI" in won["resume_entry"]["bullets"])

        real = (ai.available, ai._call_json, ai.provider)
        ai.available, ai.provider = (lambda: True), (lambda: "claude_code")
        ai._call_json = lambda *a, **k: {"tagline": "PantryPal: dinner from your fridge", "inspiration": "[Your story]",
                                         "what": "PantryPal is used by 500 students every day.",
                                         "how": "We paired a Next.js front end with a FastAPI service.",
                                         "challenges": "Telling eggs from lemons.", "proud": "[What you're proud of]",
                                         "learned": "[What you learned]", "next": "Meal plans for the week."}
        try:
            a = hackathons.submission(pid, "hack-the-north", "https://github.com/team/pantrypal")
        finally:
            ai.available, ai._call_json, ai.provider = real
        check("with an AI, its sections are used when they stick to the facts",
              a["by"] == "claude_code" and a["sections"]["how"] == "We paired a Next.js front end with a FastAPI service.")
        check("...and a section that invents a number is replaced by the README's version",
              "500" not in a["markdown"] and a["sections"]["what"].startswith("Recipes from what's in your fridge"))

        doc = a["doc_id"]
        r = cl.put(f"/api/devpost/{doc}", json={"profile_id": pid, "sections": {"inspiration": "Our roommate's fridge.",
                                                                                 "bogus": "x"}})
        saved = hackathons._saved(pid, "devpost", "hack:hack-the-north")
        check("your edits are saved and the markdown follows them",
              r.status_code == 200 and "Our roommate's fridge." in saved["markdown"] and "bogus" not in saved["sections"])
        md = cl.get(f"/api/devpost/{doc}/download?profile_id={pid}").text
        check("the download has the write-up, Built with and the demo script", "## Built with" in md and
              "## Demo video script" in md and "## What it does" in md)
        again = cl.get(f"/api/hackathons/hack-the-north/submission?profile_id={pid}").json()
        check("opening it again brings back the saved write-up", again["submission"]["doc_id"] == doc)

        before = routes_docs.master(pid)
        out = agent.call(pid, "add_project_to_resume", {"doc_id": doc})
        after = routes_docs.master(pid)
        check("the project goes first in your resume's Projects", out.get("ok") and after["projects"][0]["name"] ==
              "Pantrypal" and after["projects"][1]["name"] == "Trailmix")
        agent.call(pid, "add_project_to_resume", {"doc_id": doc})
        check("...adding it again replaces it instead of listing it twice",
              [p["name"] for p in routes_docs.master(pid)["projects"]].count("Pantrypal") == 1)
        aid = db.row("SELECT id FROM agent_actions WHERE profile_id=? AND tool='add_project_to_resume' ORDER BY id"
                     " LIMIT 1", (pid,))["id"]
        agent.undo_action(pid, aid)
        check("...and undo puts the resume back as it was", routes_docs.master(pid)["projects"] == before["projects"])
        r = cl.post(f"/api/devpost/{doc}/resume", json={"profile_id": pid, "entry": {"bullets": ["My own bullet"]}})
        cl.post("/api/devpost/restore", json={"profile_id": pid, "backup_id": r.json()["backup_id"]})
        check("the page's Add to my resume takes your edited bullets, and its Undo restores it",
              r.status_code == 200 and routes_docs.master(pid)["projects"] == before["projects"])

        filled = []
        real_fill = apply.fill
        apply.fill = lambda pid_, job_id, kit=None, answers=None: filled.append((kit, answers))
        try:
            cl.post(f"/api/devpost/{doc}/fill", json={"profile_id": pid, "url": "https://evil.example/devpost.com"})
            cl.post(f"/api/devpost/{doc}/fill", json={"profile_id": pid, "url": "https://devpost.com/software/pantrypal/edit"})
        finally:
            apply.fill = real_fill
        check("Fill on Devpost only opens Devpost, with the pitch and the write-up as answers",
              filled[0][0]["job"]["apply_url"] == "https://devpost.com/software/new"
              and filled[1][0]["job"]["apply_url"] == "https://devpost.com/software/pantrypal/edit"
              and {"Elevator pitch", "About the project", "Project name"} <= {x["label"] for x in filled[1][1]})
        out = agent.call(pid, "devpost_submission", {"repo_url": "https://github.com/team/pantrypal",
                                                     "hackathon": "GreenCode Jam"})
        check("the chat writes one too, for a hackathon named in Events", out.get("ok") and "## Inspiration" in
              out["about_the_project"] and out["brackets_to_fill"] >= 1)
    finally:
        github.client = real_client


def _parses(u: str) -> bool:
    try:
        github.parse(u)
        return True
    except github.NotARepo:
        return False


def form_answer_checks():
    b = {"first_name": "Robin", "last_name": "Doe", "full_name": "Robin Doe", "email": "r@x.com", "phone": "519-555-0123",
         "linkedin": "", "github": "", "website": "", "school": "UW", "discipline": "Computer Engineering", "degree": "",
         "grad_date": "2029", "gpa": "", "current_company": "", "current_title": "", "location": ""}
    ans = lambda label, opts=(): apply._rule_answer(label, "input_text", list(opts), b, {"regions": []}, {}, "")  # noqa: E731
    check("'What programming languages do you know?' isn't answered with your program",
          ans("What programming languages do you know?")[0] == "")
    check("'Open-source contributions' isn't taken for 'how did you hear about us'",
          ans("Tell us about your open-source contributions")[0] == "")
    check("'Mobile development experience' doesn't get your phone number", ans("Mobile development experience")[0] == "")
    check("'Languages you speak' isn't an age question", ans("Languages you speak")[2] == "" and
          ans("Languages you speak")[0] == "")
    check("...while program, mobile number, how you heard and age still are",
          ans("Program of study")[0] == "Computer Engineering" and ans("Mobile number")[0] == "519-555-0123"
          and ans("How did you hear about us?", ["Company website", "Other"])[0] == "Company website"
          and ans("What is your age?")[1])
    p = {"profile": {"program": "Computer Science", "degree_level": "Master's"}}
    check("the degree uses the level on your profile", apply._basics(p, {})["degree"] == "Master's in Computer Science")


def main():
    from coopcoach import server
    server._sync_in_background = lambda: None  # the app's startup sync isn't what's tested here
    db.init()
    pid = new_profile()
    with TestClient(app, base_url="http://localhost") as cl:
        apply_checks(pid, cl)
        devpost_checks(pid, cl)
    form_answer_checks()
    check("without an AI, the chat says where to apply to a hackathon and how to write the Devpost",
          "📝 Apply" in routes_docs._rules_reply(pid, "How do I apply to a hackathon?")
          and "GitHub" in routes_docs._rules_reply(pid, "help me write my devpost"))
    from coopcoach import path
    c = path.counts(pid)
    reached = {k for k, _, ok in path.MILESTONES if ok(c, 0, 1)}
    check("on the Path, hackathons count on their own (not as job applications or offers)",
          c["hackathons"] == 1 and c["hackathons_in"] == 1 and c["applied"] == 0 and c["offers"] == 0
          and c["devposts"] >= 1 and {"first_hackathon", "hackathon_in", "first_devpost"} <= reached
          and "first_offer" not in reached)
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
