"""Outlook on the web with a mailbox like a real one: 180 recruiting emails over 200 days, a list that draws only the rows on
screen (like Outlook's), search results and the reading pane that take a moment, dates in Outlook's different formats, and job
alerts mixed in. The first read reaches back the whole window (not just the newest), dates come from Outlook rather than the
time of reading, alerts aren't opened, a check that runs out of time is carried on by the next, new mail is read at once,
nothing older than the window is opened, and a sign-in that ran out is reported in seconds. Timed throughout.

Run: .venv/bin/python -m tests.test_outlook_backfill
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-owa2-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import db, mail, outlook_web  # noqa: E402

results: dict[str, bool] = {}
STATE = {"signed_in": True, "opened": [], "mails": {}}
NOW = datetime.now().replace(second=0, microsecond=0)


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)



def ftime(when, fmt: str) -> str:
    """strftime with %-I, %-m and %-d (no leading zero), which Windows' strftime doesn't have."""
    return when.strftime(fmt.replace("%-I", "{I}").replace("%-m", "{m}").replace("%-d", "{d}")).format(
        I=int(when.strftime("%I")), m=when.month, d=when.day)

def build(n: int = 180) -> dict:
    """Newest first, one every ~27 hours back: some job alerts among them, and dates the way Outlook writes them."""
    mails = {}
    for i in range(n):
        when = NOW - timedelta(hours=27 * i + 2)
        alert = i % 9 == 4
        subject = "Jobs you may be interested in" if alert else f"Your application to Firm{i}: next steps"
        sender = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>" if alert else f"Firm{i} Recruiting <jobs@firm{i}.com>"
        # the list shows a short date; the open email the full one, in one of the formats Outlook uses
        short = ftime(when, "%-I:%M %p") if when.date() == NOW.date() else ftime(when, "%a %-m/%-d") \
            if (NOW - when).days < 300 else ftime(when, "%-m/%-d/%Y")
        full = [ftime(when, "%a %Y-%m-%d %-I:%M %p"), ftime(when, "%a, %b %-d, %Y %-I:%M %p"),
                ftime(when, "%a %-m/%-d/%Y %-I:%M %p"), ftime(when, "%A, %B %-d, %Y %-I:%M %p")][i % 4]
        body = ("We have new roles for you." if alert else
                f"Hi Robin, thanks for applying to Firm{i}. Please complete your online assessment on HackerRank.")
        mails[f"m{i:03d}"] = {"from": sender, "subject": subject, "short": short, "full": full, "body": body,
                              "ts": int(when.timestamp())}
    return mails


MAILBOX = """<!doctype html><html><head><title>Mail - Robin Doe - Outlook</title><style>
#list { height: 600px; overflow-y: auto; position: relative } .row { position: absolute; left: 0; right: 0; height: 60px }
</style></head><body>
<div role="search"><input id="topSearchInput" aria-label="Search"></div>
<div role="listbox" aria-label="Message list" id="list"><div id="spacer"></div></div>
<div role="main"><div aria-label="Reading Pane" id="pane"></div></div>
<script>
let mails = {}, ids = [];
const list = document.getElementById('list');
function draw() {  // like Outlook: only the rows on screen are in the page
  const first = Math.max(0, Math.floor(list.scrollTop / 60) - 2), last = Math.min(ids.length, first + 14);
  [...list.querySelectorAll('.row')].forEach((e) => e.remove());
  document.getElementById('spacer').style.height = (ids.length * 60) + 'px';
  for (let i = first; i < last; i++) {
    const id = ids[i], m = mails[id];
    const r = document.createElement('div');
    r.className = 'row'; r.style.top = (i * 60) + 'px'; r.setAttribute('role', 'option'); r.setAttribute('data-convid', id);
    r.setAttribute('aria-label', 'Unread ' + m.from.replace(/<.*>/, '') + ' ' + m.subject + ' ' + m.short + ' ' + m.body.slice(0, 40));
    r.innerHTML = `<span>${m.from.replace(/<.*>/, '')}</span><span>${m.subject}</span><span title="${m.short}">${m.short}</span>`;
    r.onclick = () => openMail(id);
    list.appendChild(r);
  }
}
let drawTimer;
list.addEventListener('scroll', () => { clearTimeout(drawTimer); drawTimer = setTimeout(draw, 120); });
function openMail(id) {
  fetch('/opened?id=' + id);
  setTimeout(() => {  // the reading pane takes a moment
    const m = mails[id];
    document.getElementById('pane').innerHTML = `<div role="heading" aria-level="2">${m.subject}</div>
      <span title="${m.from.replace(/"/g, '')}">${m.from.replace(/</g, '&lt;')}</span><span title="${m.full}">${m.full}</span>
      <div aria-label="Message body">${m.body}</div>`;
  }, 180);
}
document.getElementById('topSearchInput').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  setTimeout(() => fetch('/mails').then((r) => r.json()).then((m) => {  // results take a moment too
    mails = m; ids = Object.keys(m).sort(); list.scrollTop = 0; draw();
  }), 700);
});
</script></body></html>"""
SIGN_IN = "<!doctype html><html><head><title>Sign In</title></head><body><h1>Sign in</h1></body></html>"


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.path)
        if u.path == "/opened":
            STATE["opened"].append(parse_qs(u.query)["id"][0])
        if u.path.startswith("/mail") and not STATE["signed_in"]:  # Outlook sends a signed-out visitor to the sign-in page
            self.send_response(302)
            self.send_header("Location", f"http://adfs.localhost:{self.server.server_port}/adfs/ls/")
            self.end_headers()
            return
        page, kind = (json.dumps(STATE["mails"]), "application/json") if u.path == "/mails" else \
            (SIGN_IN, "text/html") if u.path.startswith("/adfs") else (MAILBOX, "text/html")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(page.encode())


def main():
    db.init()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"127.0.0.1:{srv.server_port}"
    outlook_web._HOSTS = (*outlook_web._HOSTS, host)
    outlook_web.SIGN_IN_HOSTS = __import__("re").compile(r"^adfs\.localhost:\d+$")

    when = outlook_web._when
    ref = datetime(2026, 11, 5, 10, 0).timestamp()
    on = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else None  # noqa: E731
    check("Outlook's dates are read in each format it uses", [on(when(t, ref)) for t in (
        "Tue 2026-10-27 3:14 PM", "Tue, Oct 27, 2026 3:14 PM", "Tue 10/27/2026 3:14 PM", "Tuesday, October 27, 2026 3:14 PM",
        "27/10/2026 15:14", "Tue 10/27", "Oct 27", "3:14 PM", "Yesterday", "Mon 9:00 AM", "Dec 30")] == [
        "2026-10-27 15:14", "2026-10-27 15:14", "2026-10-27 15:14", "2026-10-27 15:14", "2026-10-27 15:14",
        "2026-10-27 12:00", "2026-10-27 12:00", "2026-11-05 15:14", "2026-11-04 12:00", "2026-11-02 09:00", "2025-12-30 12:00"])
    check("...and something with no date in it isn't guessed as now", when("Robin Doe", ref) is None and when("", ref) is None)

    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Robin', ?, ?)", (now, now)).lastrowid
        aid = c.execute("INSERT INTO email_accounts (profile_id, kind, address, host, secret, created_at) VALUES"
                        " (?, 'outlook_web', 'robin@uwaterloo.ca', ?, '', ?)", (pid, host, now)).lastrowid
    acct = db.row("SELECT * FROM email_accounts WHERE id=?", (aid,))
    outlook_web._open_url = lambda a: f"http://{host}/mail/"
    from coopcoach import workday
    real_launch = workday._launch
    workday._launch = lambda p, profile_dir, headless: real_launch(p, profile_dir, True)

    STATE["mails"] = build()
    floor = int(time.time() - 120 * 86400)
    in_window = {k for k, m in STATE["mails"].items() if m["ts"] >= floor}
    alerts = {k for k, m in STATE["mails"].items() if "Jobs you may" in m["subject"]}
    try:
        t = time.time()
        got = outlook_web.fetch(acct, floor, budget=600)
        took = time.time() - t
        st = outlook_web.state(aid)
        ids = {m["message_id"][4:] for m in got}
        check(f"the first read reaches back the whole 120 days, not only the newest ({len(ids)} emails in {took:.0f} s)",
              ids == in_window - alerts and st["finished"])
        check("...without opening anything older than that", not set(STATE["opened"]) - in_window)
        check("...or the job alerts, judged from the list", not set(STATE["opened"]) & alerts and st["skipped"] == len(in_window & alerts))
        check("...each dated when it arrived, not when it was read", all(
            abs(m["received_at"] - STATE["mails"][m["message_id"][4:]]["ts"]) < 60 for m in got))
        per = took / max(1, len(ids))
        check(f"...at about {per:.2f} s an email, with the stand-in's own delays (no fixed waits)", per < 1.2)

        STATE["opened"].clear()
        t = time.time()
        again = outlook_web.fetch(acct, floor)
        check(f"the next check opens nothing it read before ({time.time() - t:.1f} s to look)", again == [] and not STATE["opened"])

        newest = NOW + timedelta(minutes=5)
        STATE["mails"]["l000"] = {"from": "Zeta Careers <careers@zeta.com>", "subject": "Interview invitation from Zeta",
                                  "short": ftime(newest, "%-I:%M %p"), "full": ftime(newest, "%a %Y-%m-%d %-I:%M %p"),
                                  "body": "We'd like to invite you to an interview.", "ts": int(newest.timestamp())}
        STATE["mails"] = dict(sorted(STATE["mails"].items(), key=lambda kv: -kv[1]["ts"]))  # (newest at the top)
        renamed = {f"m{i:03d}": m for i, m in enumerate(STATE["mails"].values())}  # ids sort newest first in the stand-in
        old_ids = {id(m): k for k, m in STATE["mails"].items()}
        STATE["mails"] = renamed
        # the ids are new names in this stand-in, so what it read before is told by the same objects under new names
        db.meta_set(f"owa_seen:{aid}", " ".join(k for k, m in renamed.items() if old_ids[id(m)] != "l000"))
        STATE["opened"].clear()
        got = outlook_web.fetch(acct, floor)
        check("a new email at the top is read at the next check, and only that", [m["subject"] for m in got] ==
              ["Interview invitation from Zeta"] and len(STATE["opened"]) == 1)

        # a check that runs out of time: the next carries on from where it stopped
        db.meta_set(f"owa_seen:{aid}", "")
        STATE["opened"].clear()
        first = outlook_web.fetch(acct, floor, max_open=30)
        st1 = outlook_web.state(aid)
        second = outlook_web.fetch(acct, floor, max_open=30)
        both = {m["message_id"] for m in first} | {m["message_id"] for m in second}
        check("a check that runs out of time stops, and the next one carries on further back",
              len(first) == 30 and len(second) == 30 and len(both) == 60 and not st1["finished"]
              and min(m["received_at"] for m in second) < min(m["received_at"] for m in first))
        check("...without opening any email twice", len(STATE["opened"]) == len(set(STATE["opened"])) == 60)

        # the inbox check end to end: the status says how far back it read
        db.meta_set(f"owa_seen:{aid}", "")
        out = mail.sync_account(db.row("SELECT * FROM email_accounts WHERE id=?", (aid,)), budget=600)
        status = db.row("SELECT last_status FROM email_accounts WHERE id=?", (aid,))["last_status"]
        check(f"the inbox check records them and says how far back it read ({status!r})",
              out.get("job_emails", 0) >= 90 and "read back to" in status and "more at the next check" not in status)

        STATE["signed_in"] = False  # the remembered sign-in ran out: Outlook sends the window to the school's sign-in page
        t = time.time()
        try:
            outlook_web.fetch(acct, floor)
            said = ""
        except outlook_web.SignInNeeded as e:
            said = str(e)
        took = time.time() - t
        check(f"a sign-in that ran out is said in seconds, not after waiting 45 ({took:.0f} s)", "sign in again" in said and took < 15)
    finally:
        workday._launch = real_launch
        srv.shutdown()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
