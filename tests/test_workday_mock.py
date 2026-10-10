"""End-to-end Workday check against a local mock of Workday's candidate portal (no real accounts involved).

Run: .venv/bin/python -m tests.test_workday_mock
The mock copies the real sign-in page's automation IDs: email/password inputs, the hidden "beecatcher" robot
trap, the cookie notice, and the invisible click_filter overlay on the Sign In button.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from coopcoach import vault, workday

TEST_USER = "test.student@example.com"
TEST_PASS = vault.generate_password()
HONEYPOT_FILLED = []

LOGIN = """<!doctype html><html><body>
<div data-automation-id="legalNotice">We use cookies <button data-automation-id="legalNoticeDeclineButton"
  onclick="this.parentNode.remove()">Decline</button><button data-automation-id="legalNoticeAcceptButton">Accept</button></div>
%(captcha)s
<form data-automation-id="signInFormo" onsubmit="return false">
  <label>Email Address<input data-automation-id="email" type="text"></label>
  <label>Password<input data-automation-id="password" type="password"></label>
  <input data-automation-id="beecatcher" type="text" style="position:absolute;left:-9999px" aria-hidden="true">
  <div style="position:relative;display:inline-block">
    <button data-automation-id="signInSubmitButton" type="submit">Sign In</button>
    <div data-automation-id="click_filter" aria-label="Sign In" role="button"
         style="position:absolute;inset:0;cursor:pointer" onclick="go()"></div>
  </div>
  <div data-automation-id="errorMessage" role="alert" style="display:none"></div>
</form>
<script>
async function go() {
  const body = JSON.stringify({email: document.querySelector('[data-automation-id=email]').value,
    password: document.querySelector('[data-automation-id=password]').value,
    beecatcher: document.querySelector('[data-automation-id=beecatcher]').value});
  const r = await fetch('session', {method: 'POST', body});
  if (r.ok) location.href = 'userHome';
  else { const e = document.querySelector('[data-automation-id=errorMessage]'); e.textContent = 'Wrong email address or password.'; e.style.display = 'block'; }
}
</script></body></html>"""

HOME = """<!doctype html><html><body><h2>My Applications</h2>
<div role="tablist"><button role="tab" onclick="show('active')">Active (2)</button>
<button role="tab" onclick="show('inactive')">Inactive (1)</button></div>
<table id="t"><thead><tr><th>Job Title</th><th>Job Req ID</th><th>Date Submitted</th><th>Status</th></tr></thead><tbody></tbody></table>
<script>
const data = {active: [["Software Developer Co-op (Winter 2027)", "R-0000181145", "09/28/2026", "Under Consideration"],
                       ["Test Automation Intern", "R-0000182001", "09/30/2026", "Interview Scheduled"]],
              inactive: [["Data Analytics Co-op", "R-0000179900", "08/14/2026", "No Longer Under Consideration"]]};
function show(k) { document.querySelector('#t tbody').innerHTML = data[k].map(r => '<tr>' + r.map(c => '<td>' + c + '</td>').join('') + '</tr>').join(''); }
show('active');
</script></body></html>"""


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body="", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        site, _, page = urlparse(self.path).path.strip("/").partition("/")
        authed = "wdsession=ok" in (self.headers.get("Cookie") or "")
        if page == "userHome":
            if authed:
                return self._send(200, HOME)
            return self._send(302, headers={"Location": f"/{site}/login?redirect=/{site}/userHome"})
        if page == "login":
            captcha = '<iframe title="captcha challenge" src="about:blank"></iframe>' if site == "CAPTCHA" else ""
            return self._send(200, LOGIN % {"captcha": captcha})
        self._send(404)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if data.get("beecatcher"):
            HONEYPOT_FILLED.append(True)
            return self._send(403)
        if data.get("email") == TEST_USER and data.get("password") == TEST_PASS:
            return self._send(200, "{}", {"Set-Cookie": "wdsession=ok; Path=/"})
        self._send(401)


def main() -> None:
    srv = HTTPServer(("127.0.0.1", 8797), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = {"profile_id": 999, "username": TEST_USER, "company": "Mockbank"}
    keys = []
    results = {}
    try:
        for i, (site, pw) in enumerate([("MOCK", TEST_PASS), ("WRONG", "not-the-password"), ("CAPTCHA", TEST_PASS)]):
            acct = {**base, "id": 90000 + i, "site_url": f"http://127.0.0.1:8797/{site}"}
            key = vault.workday_key(999, acct["site_url"], TEST_USER)
            vault.put(key, pw)
            keys.append(key)
            results[site] = workday.check_account(acct)
            print(site, "->", results[site]["state"], "|", results[site]["message"])
            for a in results[site]["apps"]:
                print("    ", a["title"], "|", a["status_raw"], "->", workday.map_status(a["status_raw"]))
    finally:
        for k in keys:
            vault.delete(k)
        srv.shutdown()
        import shutil
        for i in range(3):
            shutil.rmtree(workday.PROFILES / f"workday-{90000 + i}", ignore_errors=True)

    ok = results["MOCK"]
    checks = {
        "signs in and reads 3 applications (both tabs)": ok["state"] == "ok" and len(ok["apps"]) == 3,
        "maps statuses": sorted(workday.map_status(a["status_raw"]) for a in ok["apps"]) == ["applied", "interviewing", "rejected"],
        "never fills the robot trap": not HONEYPOT_FILLED,
        "wrong password is reported, not retried": results["WRONG"]["state"] == "error" and "Wrong" in results["WRONG"]["message"],
        "CAPTCHA hands control to the person": results["CAPTCHA"]["state"] == "needs_manual",
    }
    for name, passed in checks.items():
        print(("PASS " if passed else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
