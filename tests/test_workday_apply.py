"""Filling a Workday application, end to end in a real (headless) Chrome, against a local mock of Workday's wizard:
Apply → Apply Manually → owner sign-in → My Information (dropdowns, a hidden "beecatcher"
robot trap) → My Experience (resume upload) → Application Questions (Yes/No, a dropdown) → stops at Voluntary
Disclosures (theirs to answer). It never presses Submit, stops for a question it has no answer for, and asks for a
login instead of creating an account. No real Workday site or account is involved.

Run: .venv/bin/python -m tests.test_workday_apply
"""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from coopcoach import vault, workday_apply

USER, PASS = "test.student@example.com", vault.generate_password()
SAVED: list[dict] = []
SUBMITTED: list[dict] = []
results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


JOB = """<!doctype html><html><body><h1>Software Developer Co-op</h1>
<a data-automation-id="adventureButton" href="#" onclick="document.getElementById('start').style.display='block';return false">Apply</a>
<div id="start" style="display:none"><a data-automation-id="autofillWithResume" href="/login?next=/wizard">Autofill with Resume</a>
<a data-automation-id="applyManually" href="/login?next=/wizard">Apply Manually</a></div></body></html>"""

LOGIN = """<!doctype html><html><body><form onsubmit="return false">
<label>Email Address<input data-automation-id="email" type="text"></label>
<label>Password<input data-automation-id="password" type="password"></label>
<input data-automation-id="beecatcher" type="text" style="position:absolute;left:-9999px" aria-hidden="true">
<button data-automation-id="signInSubmitButton" type="submit" onclick="go()">Sign In</button>
<div data-automation-id="errorMessage" role="alert" style="display:none"></div></form>
<script>async function go(){const r=await fetch('/session',{method:'POST',body:JSON.stringify({email:document.querySelector('[data-automation-id=email]').value,password:document.querySelector('[data-automation-id=password]').value})});
if(r.ok){location.href=new URLSearchParams(location.search).get('next')||'/wizard'}else{const e=document.querySelector('[data-automation-id=errorMessage]');e.textContent='Wrong email or password';e.style.display='block'}}</script>
</body></html>"""

WIZARD = """<!doctype html><html><body><div data-automation-id="progressBar"><span data-automation-id="progressBarActiveStep" id="step"></span></div>
<div id="page"></div><div data-automation-id="errorMessage" id="err" style="display:none"></div>
<button data-automation-id="pageFooterNextButton" id="next">Save and Continue</button>
<script>
const steps = %(steps)s;
let i = Number(new URLSearchParams(location.search).get('at') || 0);
const dd = (id, label, opts) => `<div data-automation-id="formField-${id}"><label>${label}</label><button aria-haspopup="listbox" data-automation-id="${id}" onclick="openList(this, ${JSON.stringify(opts).replace(/"/g, '&quot;')})">Select One</button></div>`;
const txt = (id, label, val) => `<div data-automation-id="formField-${id}"><label for="${id}">${label}</label><input type="text" id="${id}" data-automation-id="${id}" value="${val || ''}"></div>`;
const yesno = (id, label) => `<fieldset data-automation-id="formField-${id}"><legend>${label}</legend><label><input type="radio" name="${id}" value="Yes">Yes</label><label><input type="radio" name="${id}" value="No">No</label></fieldset>`;
const pages = {
  "My Information": () => txt('legalNameSection_firstName', 'Given Name(s)') + txt('legalNameSection_lastName', 'Family Name') +
    txt('email', 'Email Address') + txt('phone-number', 'Phone Number') + dd('phone-device-type', 'Phone Device Type', ['Mobile', 'Landline']) +
    txt('addressSection_city', 'City') + '<div data-automation-id="formField-trap"><label>Email</label><input type="text" data-automation-id="beecatcher" style="position:absolute;left:-9999px" aria-hidden="true"></div>',
  "My Experience": () => '<div data-automation-id="formField-resume"><label>Resume/CV</label><input type="file" data-automation-id="file-upload-input-ref"></div>' + txt('linkedinQuestion', 'LinkedIn'),
  "Application Questions": () => yesno('auth', 'Are you legally authorized to work in Canada?') +
    dd('sponsor', 'Will you now or in the future require sponsorship?', ['Yes', 'No']) + (%(extra)s ? '<div data-automation-id="formField-why"><label>Why do you want to work at Northwind?</label><textarea></textarea></div>' : ''),
  "Voluntary Disclosures": () => dd('gender', 'Gender', ['Male', 'Female', 'I do not wish to answer']),
  "Review": () => '<p>Review your application</p>',
};
function openList(btn, opts) { const ul = document.createElement('ul'); ul.setAttribute('role', 'listbox');
  for (const o of opts) { const li = document.createElement('li'); li.setAttribute('role', 'option'); li.textContent = o; li.onclick = () => { btn.textContent = o; ul.remove(); }; ul.appendChild(li); }
  document.body.appendChild(ul); }
function draw() { document.getElementById('step').textContent = steps[i]; document.getElementById('page').innerHTML = pages[steps[i]]();
  document.getElementById('next').textContent = steps[i] === 'Review' ? 'Submit' : 'Save and Continue'; document.getElementById('err').style.display = 'none'; }
document.getElementById('next').onclick = async () => {
  const vals = {}; document.querySelectorAll('#page input[type=text], #page textarea').forEach((e) => vals[e.dataset.automationId || e.id || 'text'] = e.value);
  document.querySelectorAll('#page button[aria-haspopup]').forEach((b) => vals[b.dataset.automationId] = b.textContent);
  document.querySelectorAll('#page input[type=radio]:checked').forEach((r) => vals[r.name] = r.value);
  const f = document.querySelector('#page input[type=file]'); if (f) vals.resume = f.files.length ? f.files[0].name : '';
  const r = await fetch(steps[i] === 'Review' ? '/submit' : '/save', {method: 'POST', body: JSON.stringify({step: steps[i], vals})});
  if (!r.ok) { const e = document.getElementById('err'); e.textContent = 'Required'; e.style.display = 'block'; return; }
  if (steps[i] !== 'Review') { i++; draw(); } };
draw();
</script></body></html>"""

STATE = {"extra": False}


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: str, code: int = 200, headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/job":
            return self._send(JOB)
        if u.path == "/login":
            if "signed_in=1" in (self.headers.get("Cookie") or ""):
                return self._send("", 302, {"Location": parse_qs(u.query).get("next", ["/wizard"])[0]})
            return self._send(LOGIN)
        if u.path == "/wizard":
            steps = json.dumps(["My Information", "My Experience", "Application Questions", "Voluntary Disclosures",
                                "Review"])
            return self._send(WIZARD % {"steps": steps, "extra": "true" if STATE["extra"] else "false"})
        return self._send("not found", 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/session":
            ok = body.get("email") == USER and body.get("password") == PASS
            return self._send("", 200 if ok else 401, {"Set-Cookie": "signed_in=1; Path=/"} if ok else None)
        if self.path == "/save":
            SAVED.append(body)
            return self._send("{}")
        if self.path == "/submit":
            SUBMITTED.append(body)
            return self._send("{}")
        return self._send("", 404)


KIT = {"job": {"apply_url": "", "ats": "workday"},
       "questions": [{"label": "Are you legally authorized to work in Canada?", "answer": "Yes"},
                     {"label": "Will you now or in the future require sponsorship?", "answer": "No"}],
       "basics": {"first_name": "Alex", "last_name": "Chen", "email": "alex@example.com", "phone": "519-555-0101",
                  "location": "Waterloo, ON", "linkedin": "https://www.linkedin.com/in/alexchen"}}


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP Playwright isn't installed")
        return
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    resume = os.path.join(tempfile.mkdtemp(), "Alex_Chen_resume.pdf")
    with open(resume, "wb") as fh:
        fh.write(b"%PDF-1.4 test")
    kit = {**KIT, "job": {**KIT["job"], "apply_url": f"{base}/job"}}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                print(f"SKIP no Chrome for Playwright here: {e}")
                return
        page = browser.new_page()
        out = workday_apply.run(page, kit, {"resume": resume}, (USER, PASS))
        check("even with credentials supplied it leaves sign-in entirely to the person",
              "Sign in yourself" in out["message"] and not SAVED
              and page.locator('input[data-automation-id="email"]').input_value() == ""
              and page.locator('input[data-automation-id="password"]').input_value() == "")

        page = browser.new_context().new_page()
        page.context.add_cookies([{"name": "signed_in", "value": "1", "url": base}])
        out = workday_apply.run(page, kit, {"resume": resume}, (USER, PASS))
        info = next((s["vals"] for s in SAVED if s["step"] == "My Information"), {})
        exp = next((s["vals"] for s in SAVED if s["step"] == "My Experience"), {})
        qs = next((s["vals"] for s in SAVED if s["step"] == "Application Questions"), {})
        check("an existing signed-in session fills My Information: name, email, phone, its dropdown, city",
              info.get("legalNameSection_firstName") == "Alex" and info.get("legalNameSection_lastName") == "Chen"
              and info.get("email") == "alex@example.com" and info.get("phone-number") == "519-555-0101"
              and info.get("phone-device-type") == "Mobile" and info.get("addressSection_city") == "Waterloo")
        check("...never the hidden robot trap", info.get("beecatcher") == "")
        check("...uploads the resume on My Experience", exp.get("resume") == "Alex_Chen_resume.pdf"
              and exp.get("linkedinQuestion") == "https://www.linkedin.com/in/alexchen")
        check("...answers the application questions it has answers for (a Yes/No, a dropdown)",
              qs.get("auth") == "Yes" and qs.get("sponsor") == "No")
        check("...and stops at Voluntary Disclosures: those answers are theirs", out["step"] == "Voluntary Disclosures"
              and "yours to answer" in out["message"] and not any(s["step"] == "Voluntary Disclosures" for s in SAVED))

        page.goto(f"{base}/wizard?at=4")
        page.wait_for_timeout(500)
        kit_review = {**kit, "job": {**kit["job"], "apply_url": f"{base}/wizard?at=4"}}
        out = workday_apply.run(page, kit_review, {"resume": resume}, (USER, PASS))
        check("at Review it stops and leaves Submit to them: nothing is ever submitted",
              out["step"] == "Review" and "press Submit yourself" in out["message"] and not SUBMITTED)

        STATE["extra"] = True
        SAVED.clear()
        page = browser.new_context().new_page()
        kit_q = {**kit, "job": {**kit["job"], "apply_url": f"{base}/wizard?at=2"}}
        page.context.add_cookies([{"name": "signed_in", "value": "1", "url": base}])
        out = workday_apply.run(page, kit_q, {"resume": resume}, (USER, PASS))
        check("a question it has no answer for stops it, named, before moving on",
              out["left"] == ["Why do you want to work at Northwind?"] and "only you can answer" in out["message"]
              and not SAVED)
        browser.close()
    srv.shutdown()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
