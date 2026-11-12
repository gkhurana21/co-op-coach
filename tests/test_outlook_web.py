"""Outlook on the web, end to end against a local stand-in for Outlook (no real account): connecting waits on the
sign-in page and finishes once the mailbox shows; each check signs in with the remembered window, searches the way
Gmail is searched, opens only what the search found and wasn't read before, skips emails the student sent, and
hands them to the inbox reader (which moves the application along); a sign-in that ran out says "sign in again".
Also: which Outlook a school or personal address opens, and the API around it.

Run: .venv/bin/python -m tests.test_outlook_web
"""

import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-owa-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import db, mail, outlook_web  # noqa: E402

results: dict[str, bool] = {}
STATE = {"signed_in": False, "searched": [], "opened": [], "cookies": []}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


MAILS = {
    "c1": ("Acme Recruiting <jobs@acme.com>", "Your application to Acme: next steps", "Tue 2026-10-27 3:14 PM",
           "Hi Robin, as the next step for the Software Developer Intern role, please complete your online assessment "
           "on HackerRank by Friday: https://www.hackerrank.com/test/abc123"),
    "c2": ("Robin Doe <robin@uwaterloo.ca>", "Re: application", "Tue 2026-10-27 4:00 PM", "Thanks!"),
    "c3": ("Globex Talent <talent@globex.com>", "Update on your Globex application", "Mon 2026-10-26 9:00 AM",
           "Unfortunately we won't be moving forward with your application."),
}
# like Microsoft's page: once the student has signed in, it moves on to the mailbox by itself
SIGN_IN = ("<!doctype html><html><body><h1>Sign in to your account</h1><input type=email><script>setInterval(() =>"
           " fetch('/state').then((r) => r.text()).then((t) => t === 'in' && location.reload()), 500)</script>"
           "</body></html>")
MAILBOX = """<!doctype html><html><body>
<div role="listbox" style="display:none"></div><!-- like Outlook: hidden ones around, so the first match says nothing -->
<div role="search"><input id="topSearchInput" aria-label="Search"></div>
<div role="listbox" aria-label="Message list" id="list"></div>
<div role="main"><div aria-label="Reading Pane" id="pane"></div></div>
<script>
const mails = %s;
function list(ids) {
  document.getElementById('list').innerHTML = ids.map((id) => `<div role="option" data-convid="${id}"
    aria-label="${mails[id][0]} ${mails[id][1]}" onclick="openMail('${id}')">${mails[id][1]}</div>`).join('');
}
function openMail(id) {
  fetch('/opened?id=' + id);
  const m = mails[id];
  document.getElementById('pane').innerHTML = `<div role="heading" aria-level="2">${m[1]}</div>
    <span title="${m[0].replace(/"/g, '')}">${m[0].replace(/</g, '&lt;')}</span><span title="${m[2]}">${m[2]}</span>
    <div aria-label="Message body">${m[3]}</div>`;
  history.replaceState(null, '', '/mail/inbox/id/' + id);
}
list(['c3']);
document.getElementById('topSearchInput').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  fetch('/searched?q=' + encodeURIComponent(e.target.value));
  list(['c1', 'c2', 'c3']);
});
</script></body></html>"""


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/searched":
            STATE["searched"].append(q.get("q", [""])[0])
        if u.path == "/opened":
            STATE["opened"].append(q.get("id", [""])[0])
        import json
        page = MAILBOX % json.dumps(MAILS) if STATE["signed_in"] else SIGN_IN
        if u.path == "/state":
            page = "in" if STATE["signed_in"] else "out"
        STATE["cookies"].append(self.headers.get("Cookie") or "")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if STATE["signed_in"]:  # a school's sign-in: a cookie that ends when the browser closes
            self.send_header("Set-Cookie", "ADFSsession=1; Path=/")
        self.end_headers()
        self.wfile.write(page.encode())


def main():
    db.init()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/mail/"
    check("a school address opens Microsoft 365's Outlook, a personal one Outlook.com",
          outlook_web.home("student@uwaterloo.ca") == outlook_web.SCHOOL
          and outlook_web.home("someone@hotmail.com") == outlook_web.PERSONAL)
    check("the sender and date lines Outlook shows are read", outlook_web._sender("Acme Recruiting <jobs@acme.com>") ==
          ("Acme Recruiting", "jobs@acme.com") and time.localtime(outlook_web._when("Tue 2026-10-27 3:14 PM")).tm_hour == 15)

    row = outlook_web._when("Mon 2026-08-24 00:51")
    scan = outlook_web._when("Tue 2026-11-03 21:27")
    same_day = outlook_web._when("Mon 2026-08-24 00:51:30 AM") or row
    check("an email is dated by its own row, not by the scan time the reading pane showed",
          outlook_web._received(row, scan) == row and outlook_web._received(row, same_day) == same_day
          and outlook_web._received(None, scan) == scan)

    title = outlook_web._MAILBOX_TITLE.match
    check("the mailbox's tab title is known from the sign-in pages' titles",
          title("Mail - Robin Doe - Outlook") and title("Inbox - robin@uwaterloo.ca - Outlook") and not any(
              title(t) for t in ("Outlook", "Microsoft Authentication", "Sign in to your account", "Working...")))
    check("reading goes to the Outlook they landed on, even if the address looked like the other kind",
          outlook_web._open_url({"host": "outlook.cloud.microsoft", "address": "x@outlook.com"})
          == "https://outlook.cloud.microsoft/mail/"
          and outlook_web._open_url({"host": "evil.example", "address": "student@uwaterloo.ca"}) == outlook_web.SCHOOL)

    outlook_web.home = lambda address: base  # the stand-in, not Microsoft
    outlook_web._HOSTS = (*outlook_web._HOSTS, f"127.0.0.1:{srv.server_port}")
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Robin', ?, ?)", (now, now)).lastrowid
        for jid, co, title in (("j1", "Acme", "Software Developer Intern"), ("j2", "Globex", "Data Analyst Intern")):
            c.execute("INSERT INTO applications (profile_id, job_id, status, company, title, updated_at, applied_at)"
                      " VALUES (?,?, 'applied', ?, ?, ?, ?)", (pid, jid, co, title, now, now))

    import coopcoach.outlook_web as ow
    real_launch = None
    from coopcoach import workday
    real_launch = workday._launch
    workday._launch = lambda p, profile_dir, headless: real_launch(p, profile_dir, True)  # never a visible window here
    first = []
    ow._process_first = lambda a, msgs: first.extend(msgs)
    try:
        fid = ow.connect(pid, "robin@uwaterloo.ca")
        time.sleep(6)
        waiting = ow.flow(fid)["status"]
        STATE["signed_in"] = True  # the student finishes signing in (Co-op Coach never types anything)
        for _ in range(120):
            if ow.flow(fid)["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        acct = db.row("SELECT * FROM email_accounts WHERE profile_id=?", (pid,))
        check("connecting waits on the sign-in page, and finishes once the mailbox shows",
              waiting == "waiting" and ow.flow(fid)["status"] == "done" and acct and acct["kind"] == "outlook_web")
        check("...saving no password or token (the window remembers the sign-in)", acct["secret"] == "")
        check("...and the Outlook it landed on", acct["host"] == f"127.0.0.1:{srv.server_port}")
        check("the first read happens in the signed-in window, before it closes",
              sorted(m["message_id"] for m in first) == ["owa:c1", "owa:c3"] and ow.flow(fid)["found"] == 2)
        db.meta_set(f"owa_seen:{acct['id']}", "")  # from here on, the checks after it
        STATE["cookies"].clear()

        got = ow.fetch(acct, now - 120 * 86400)
        check("a school's end-of-browser sign-in cookie survives the window closing (the real browser, restarted)",
              any("ADFSsession=1" in c for c in STATE["cookies"]))
        check("a check searches Outlook for the words recruiting emails use",
              STATE["searched"] and "interview" in STATE["searched"][-1] and "hackerrank" in STATE["searched"][-1])
        check("...opens what it found, and leaves out the one the student sent",
              sorted(m["message_id"] for m in got) == ["owa:c1", "owa:c3"]
              and {m["from_addr"] for m in got} == {"jobs@acme.com", "talent@globex.com"})
        m1 = next(m for m in got if m["message_id"] == "owa:c1")
        check("...with the subject, sender, date and text the inbox reader needs",
              m1["subject"].startswith("Your application to Acme") and "HackerRank" in m1["body"]
              and time.localtime(m1["received_at"]).tm_mday == 27)
        before = len(STATE["opened"])
        again = ow.fetch(acct, now - 120 * 86400)
        check("the next check doesn't open emails it already read", again == [] and len(STATE["opened"]) == before)

        db.meta_set(f"owa_seen:{acct['id']}", "")
        out = mail.sync_account(db.row("SELECT * FROM email_accounts WHERE id=?", (acct["id"],)))
        statuses = {r["job_id"]: r["status"] for r in db.rows("SELECT job_id, status FROM applications WHERE profile_id=?",
                                                              (pid,))}
        check("the inbox check moves the applications along: Acme's assessment, Globex's rejection",
              out.get("job_emails", 0) >= 2 and statuses == {"j1": "oa", "j2": "rejected"})

        STATE["signed_in"] = False  # the remembered sign-in ran out
        mail.sync_account(db.row("SELECT * FROM email_accounts WHERE id=?", (acct["id"],)))
        st = db.row("SELECT last_status FROM email_accounts WHERE id=?", (acct["id"],))["last_status"]
        check("when Outlook wants a sign-in again, the inbox says so (Settings shows Sign in again)",
              "sign in again" in st.lower())

        # the search box isn't where it should be: signed in all the same, and what the page is made of is noted
        class Page:
            url = "https://outlook.office.com/mail/"
            def locator(self, _):
                raise TimeoutError("no search box")
            def evaluate(self, _js, arg=None):
                return dict.fromkeys(arg, 0)
        try:
            ow._read(Page(), dict(acct), 0)
            failed = False
        except RuntimeError:
            failed = True
        probe = db.loads(db.meta_get(f"owa_probe:{acct['id']}"), {})
        check("a page Outlook changed says what happened, and notes its structure with no text from it",
              failed and probe.get("host") == "outlook.office.com" and set(probe["counts"].values()) == {0})
    finally:
        workday._launch = real_launch

    from fastapi.testclient import TestClient

    from coopcoach import server
    server._sync_in_background = lambda: None
    cl = TestClient(server.app, base_url="http://localhost")
    check("the API wants a real address before opening Outlook",
          cl.post("/api/email/outlook-web/start", json={"profile_id": pid, "address": "nope"}).status_code == 400
          and cl.get("/api/email/outlook-web/unknown").status_code == 404)
    srv.shutdown()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
