"""Real unpacked Chromium extension against scratch-only API and practice forms."""
import os
import socket
import tempfile
import threading
from pathlib import Path

scratch = Path(tempfile.mkdtemp(prefix="coopcoach-browser-extension-"))
os.environ.update(COOPCOACH_DATA=str(scratch), COOPCOACH_PG_SETTINGS=str(scratch / "none.json"),
                  COOPCOACH_DATABASE_URL="", COOPCOACH_PG_SCHEMA="", GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")
import uvicorn  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from coopcoach import auth, db, store  # noqa: E402
from coopcoach.server import app  # noqa: E402
from tests.test_autofill import LIKE_REAL  # noqa: E402
from tools.build_extension import source  # noqa: E402

root = Path(__file__).resolve().parents[1]
assert (root / "extension/form_shared.js").read_text() == source(), "Regenerate shared rules"
db.init()
c = TestClient(app, base_url="http://localhost")
pid = c.post("/api/profiles", json={"name": "Extension Demo", "passcode": "scratch-123"}).json()["id"]
code = c.post("/api/extension/code", json={"profile_id": pid}).json()["code"]
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
profile = {"name": "Alex Chen", "email": "alex@example.com", "country": "Canada", "university": "University of Waterloo",
           "program": "Computer Science", "degree_level": "Bachelor's", "skills": ["Python"]}
resume = {"header": {"name": "Alex Chen", "email": "alex@example.com", "phone": "4165550199", "country": "Canada",
                     "location": "Toronto, ON", "links": ["https://github.com/alex"]}}
kit = {"files": {"resume_pdf": "prepared"}, "questions": [], "job": {}}
with db.tx() as conn:
    conn.execute("UPDATE profiles SET profile_json=?, prefs_json=? WHERE id=?", (db.dumps(profile), db.dumps({"citizenship": "Canadian"}), pid))
    conn.execute("INSERT INTO documents (profile_id, kind, title, content, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                 (pid, "resume_master", "Fictional resume", db.dumps(resume), db.now(), db.now()))
    conn.execute("INSERT INTO jobs (id, source, company, company_key, title, url, regions, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("extension-practice", "manual", "Practice Company", "practice", "Software Intern", base + "/__practice", '["Canada"]', db.now(), db.now()))
    conn.execute("INSERT INTO ready_queue (profile_id, job_id, status, kit, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                 (pid, "extension-practice", "ready", db.dumps(kit), db.now(), db.now()))
store.refresh_scores()


@app.get("/__practice", response_class=HTMLResponse)
def practice():
    extra = '''<label for="resume">Resume</label><input id="resume" type="file">
<label for="auto">Autofill from resume</label><input id="auto" type="file">
<label for="hidden-email">Email</label><input id="hidden-email" style="opacity:0">
<form><label>Email<input id="login-email" autocomplete="username"></label><input type="password"></form>
<iframe src="/__frame" title="Application details"></iframe>
<iframe title="captcha challenge" src="about:blank"></iframe>'''
    uploads, rest = extra.split('<label for="hidden-email">', 1)
    return LIKE_REAL.replace("</form>", uploads + "</form>").replace("</body>", '<label for="hidden-email">' + rest + "</body>")


@app.get("/__frame", response_class=HTMLResponse)
def frame():
    return '<label for="frame-name">First Name</label><input id="frame-name"><button onclick="window.PRESSED=true">Submit</button>'


server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
t.start()
try:
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(scratch / "chrome"), channel="chromium", headless=True,
            args=[f'--disable-extensions-except={root / "extension"}', f'--load-extension={root / "extension"}'])
        worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        ext = worker.url.split("/")[2]
        p = ctx.new_page()
        p.goto(f"chrome-extension://{ext}/panel.html")
        p.fill("#base", base)
        p.fill("#code", code)
        p.locator("#pair button").click()
        p.wait_for_selector("#today:not([hidden])", timeout=15000)
        expect(p.locator("#message")).to_have_text("", timeout=30000)
        assert p.locator("#person").inner_text() == "Extension Demo"
        print("PASS real unpacked extension pairs and loads Today", flush=True)
        form = ctx.new_page()
        form.goto(base + "/__practice")
        form.bring_to_front()
        p.click("#fill")
        expect(p.locator("#fill-result")).to_contain_text("Check everything", timeout=60000)
        report = p.locator("#fill-result").inner_text()
        print(report, flush=True)
        picked = form.evaluate("window.PICKED")
        assert picked == {"country": "Canada", "school": "University of Waterloo", "degree": "Bachelor's Degree", "auth": "Yes", "sponsor": "No"}, picked
        assert form.locator("#l-email").input_value() == "alex@example.com"
        assert form.locator("#l-phone").input_value() == "416-555-0199"
        assert form.locator("#r-yes").is_checked() and not form.locator("#r-no").is_checked()
        assert form.locator("#l-sp").input_value() == "No"
        assert form.frame_locator('iframe[src="/__frame"]').locator("#frame-name").input_value() == "Alex"
        print("PASS practice dropdowns, radio, native select, contact fields and iframe filled", flush=True)
        assert all(form.locator(sel).input_value() == "" for sel in ("#trap", "#why2", "#l-odd", "#hidden-email", "#login-email"))
        assert not form.locator("#r-he").is_checked()
        assert not form.evaluate("window.SUBMITTED || window.PRESSED || false")
        assert not form.frames[1].evaluate("window.PRESSED || false")
        assert "CAPTCHA" in report and "Have you applied" in report
        print("PASS demographics, credentials, invisible fields, trap, unknown questions and Submit untouched", flush=True)
        assert form.locator("#resume").evaluate("e => e.files.length === 1 && e.files[0].type === 'application/pdf'")
        assert form.locator("#auto").evaluate("e => e.files.length") == 0
        print("PASS prepared PDF attached; resume-autofill box untouched", flush=True)
        assert not db.row("SELECT status FROM applications WHERE profile_id=? AND job_id=?", (pid, "extension-practice"))
        p.click("#submitted")
        expect(p.locator("#fill-result")).to_contain_text("Marked applied", timeout=15000)
        assert db.row("SELECT status FROM applications WHERE profile_id=? AND job_id=?", (pid, "extension-practice"))["status"] == "applied"
        assert not form.evaluate("window.SUBMITTED || window.PRESSED || false")
        print("PASS explicit I've submitted action updates scratch tracker only", flush=True)
        c.post("/api/auth/lock")
        p.click("#refresh")
        expect(p.locator("#message")).to_contain_text("locked", timeout=10000)
        print("PASS locking app blocks running extension", flush=True)
        c.post("/api/auth/unlock", json={"profile_id": pid, "passcode": "scratch-123"})
        p.click("#refresh")
        expect(p.locator("#message")).to_have_text("", timeout=30000)
        print("PASS unlocking restores Today", flush=True)
        p.screenshot(path="/tmp/coopcoach-extension-today.png")
        p.click("#disconnect")
        p.wait_for_selector("#pair:not([hidden])")
        print("PASS disconnect returns to pairing", flush=True)
        ctx.add_cookies([{"name": auth.COOKIE, "value": c.cookies.get(auth.COOKIE), "url": base}])
        ui = ctx.new_page()
        errors = []
        ui.on("pageerror", lambda e: errors.append(str(e)))
        ui.goto(base)
        ui.bring_to_front()
        ui.click("#settings-btn")
        ui.click('[data-sec="tracking"]')
        expect(ui.locator("#set-inbox")).to_contain_text("inbox", ignore_case=True)
        expect(ui.locator("#settings-body")).not_to_contain_text("Workday")
        assert ui.locator("#workday-card").count() == 0
        print("PASS Inbox settings renders without Workday account monitoring", flush=True)
        ui.evaluate("switchTab('path')")
        ui.click("#open-ladder")
        ui.click('[data-try="two-sum"]')
        ui.fill("#ld-notes", "Keep this unsaved draft")
        ui.fill("#ld-min", "12")
        ui.evaluate("window.scrollTo(0, 1000)")
        position = ui.evaluate("window.scrollY")
        result = c.post("/api/path/attempt", json={"profile_id": pid, "slug": "valid-anagram", "result": "solved", "seconds": 120})
        assert result.status_code == 200, result.text
        ui.wait_for_function("PATH.data.coding.solved === 1", timeout=25000)
        expect(ui.locator(".next-up")).to_contain_text("1/150")
        expect(ui.locator("#ld-notes")).to_have_value("Keep this unsaved draft")
        expect(ui.locator("#ld-min")).to_have_value("12")
        assert abs(ui.evaluate("window.scrollY") - position) < 3
        assert not errors, errors
        print("PASS automatic Path refresh preserves open draft and page position", flush=True)
        ctx.close()
finally:
    server.should_exit = True
    t.join(10)
