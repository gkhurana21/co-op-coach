"""The calendar link: making it (only a hash is stored for the cloud feed), the local feed with saved events plus
interview / online-assessment dates from the tracker, a fresh link revoking the old one, and the Supabase function's
address when the cloud is on.

Run: .venv/bin/python -m tests.test_calendar
"""

import hashlib
import json
import os
import tempfile

TMP = tempfile.mkdtemp(prefix="coopcoach-caltest-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="", ANTHROPIC_API_KEY="")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import db, routes_events, server, supa  # noqa: E402


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES"
                        " ('Tester', ?, ?, '{}', '{}')", (now, now)).lastrowid
        c.execute("INSERT INTO event_feeds (id, url, label, created_at) VALUES (1, 'https://x', 'Meetup', ?)", (now,))
        c.execute("INSERT INTO events (id, feed_id, title, start_ts, location, url, description, kind, first_seen,"
                  " last_seen, region) VALUES ('e1', 1, 'Hack Night, Waterloo', ?, 'Waterloo, ON', 'https://e', '',"
                  " 'hackathon', ?, ?, 'Waterloo Region')", (now + 86400, now, now))
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at, company, title) VALUES"
                  " (?, 'j1', 'interviewing', ?, 'Shopify', 'Software Intern')", (pid, now))
    cl = TestClient(server.app, base_url="http://localhost")
    checks = {}
    cl.put("/api/events/e1/mark", json={"profile_id": pid, "mark": "going"})
    r = cl.put("/api/applications/dates", json={"profile_id": pid, "job_id": "j1", "interview_at": now + 3 * 86400})
    checks["interview dates are saved"] = r.status_code == 200 and \
        db.row("SELECT interview_at FROM applications WHERE job_id='j1'")["interview_at"] == now + 3 * 86400
    link = cl.post("/api/events/calendar-feed", json={"profile_id": pid}).json()
    token = link["calendar_feed"].split("/")[-1].removesuffix(".ics")
    prefs = json.loads(db.row("SELECT prefs_json FROM profiles WHERE id=?", (pid,))["prefs_json"])
    checks["making a link stores its hash for the cloud feed"] = prefs["calendar_token_hash"] == hashlib.sha256(
        token.encode()).hexdigest() and len(token) >= 30
    ics = cl.get(link["calendar_feed"]).text
    checks["the feed has the saved event"] = "SUMMARY:Hack Night\\, Waterloo" in ics
    checks["...and the interview from the tracker"] = "Interview: Shopify · Software Intern" in ics
    if supa.enabled():  # the suites on Postgres run inside a Supabase project, so the cloud link is there
        checks["with Supabase on, the link is also the cloud function's"] = link["calendar_public"] == \
            f"https://{supa.settings()['ref']}.supabase.co/functions/v1/calendar/{token}.ics"
    else:
        checks["without Supabase there's no cloud link"] = link["calendar_public"] == ""
    new = cl.post("/api/events/calendar-feed", json={"profile_id": pid, "new": True}).json()
    checks["a new link revokes the old one"] = cl.get(link["calendar_feed"]).status_code == 404 and \
        cl.get(new["calendar_feed"]).status_code == 200
    real = (supa.settings, supa.enabled)
    supa.settings, supa.enabled = (lambda: {"backend": "postgres", "ref": "abcdefghijklmnop"}), (lambda: True)
    try:
        urls = routes_events._calendar_urls({"prefs": {"calendar_token": "tok-123"}})
    finally:
        supa.settings, supa.enabled = real
    checks["with Supabase the link is the cloud function's"] = urls["calendar_public"] == \
        "https://abcdefghijklmnop.supabase.co/functions/v1/calendar/tok-123.ics"
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
