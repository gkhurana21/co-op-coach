"""Meetings and deadlines from email, on the calendar: which kind of meeting an email is arranging (an interview, an
assessment, a recruiter call, a coffee chat, an info session, a career fair), the dates and times written the ways
emails and invites write them, a calendar invite attached to an email (and its later move or cancellation), a booking
link with no time yet, several interview rounds, coffee chats that don't make fake applications, the calendar link
(each with a reminder, no duplicates of the tracker's dates), the Events tab and Today, and undoing or ignoring the
email taking its items off again. Only the item and a link back to the email are kept.

Run: .venv/bin/python -m tests.test_calendar_items
"""

import email.message
import os
import tempfile
import time
from datetime import datetime

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-calitems-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, db, events, mail, meetings  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def email_(subject, body, frm, addr, mid, when=None, ics=""):
    return {"message_id": mid, "subject": subject, "body": body, "from_name": frm, "from_addr": addr,
            "received_at": when or db.now(), "web_link": f"https://mail.example/{mid}", "ics": ics}


def at(days: int, hour: int, minute: int = 0) -> datetime:
    d = datetime.fromtimestamp(time.time() + days * 86400)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0)


def fmt(d: datetime) -> str:  # "Tuesday, November 3 at 2:30 PM"
    return f"{d:%A, %B} {d.day} at {d.hour % 12 or 12}:{d:%M} {'PM' if d.hour >= 12 else 'AM'}"


def invite(uid: str, start: datetime, minutes: int, summary: str, method: str = "REQUEST", status: str = "CONFIRMED") -> str:
    from datetime import timezone
    s = start.astimezone(timezone.utc)
    e = datetime.fromtimestamp(start.timestamp() + minutes * 60, timezone.utc)
    return "\r\n".join(["BEGIN:VCALENDAR", f"METHOD:{method}", "BEGIN:VEVENT", f"UID:{uid}",
                        f"DTSTART:{s:%Y%m%dT%H%M%SZ}", f"DTEND:{e:%Y%m%dT%H%M%SZ}", f"SUMMARY:{summary}",
                        "LOCATION:Google Meet", "DESCRIPTION:Join: https://meet.google.com/abc-defg-hij",
                        f"STATUS:{status}", "END:VEVENT", "END:VCALENDAR"])


def items(pid: int, **where) -> list[dict]:
    sql = "SELECT * FROM calendar_items WHERE profile_id=?" + "".join(f" AND {k}=?" for k in where)
    return db.rows(sql + " ORDER BY id", (pid, *where.values()))


def kinds_and_dates():
    k = meetings.kind_of
    check("what an email is arranging: interview, assessment, recruiter call, coffee chat, info session, career fair",
          k("Interview invitation", "We'd like to invite you to an interview") == "interview"
          and k("Next steps", "Please complete the HackerRank assessment") == "oa"
          and k("Intro call", "Let's set up a quick intro call about the role you applied to") == "recruiter_call"
          and k("Coffee chat?", "Would love to grab a virtual coffee and chat about your career") == "coffee_chat"
          and k("RBC info session", "Join our info session on campus") == "info_session"
          and k("Career fair", "See you at the career fair") == "career_fair"
          and k("Your order shipped", "Track your package") is None)
    check("...\"your interview is confirmed\" is an interview even when the subject doesn't say so",
          k("Re: next week", "Your interview is scheduled for Friday at 10am") == "interview")
    now = time.mktime((2026, 10, 30, 10, 0, 0, 0, 0, -1))  # a Friday morning
    m = lambda t: [(time.strftime("%a %b %d %H:%M", time.localtime(x["start"])),  # noqa: E731
                    x["end"] and time.strftime("%H:%M", time.localtime(x["end"])), x["due"]) for x in meetings.moments(t, now)]
    check("dates and times the ways emails write them", m("on Tuesday, November 3 at 2:30 PM") == [("Tue Nov 03 14:30", None, False)]
          and m("Tue Nov 3, 2026 3pm - 3:30pm") == [("Tue Nov 03 15:00", "15:30", False)]
          and m("scheduled for 11/04/2026 at 15:00") == [("Wed Nov 04 15:00", None, False)]
          and m("2026-11-05 09:30") == [("Thu Nov 05 09:30", None, False)]
          and m("see you tomorrow at 3pm") == [("Sat Oct 31 15:00", None, False)]
          and m("join us next Thursday at 6pm") == [("Thu Nov 05 18:00", None, False)]
          and m("complete it by Nov 6, 11:59 PM") == [("Fri Nov 06 23:59", None, True)])
    utc = meetings.moments("When: Tuesday, November 3, 2026 3:00 PM-3:30 PM (UTC-05:00) Eastern Time", now)
    pt = meetings.moments("Monday, November 2 at 10:00 AM PT", now)
    check("...with time zones, written as names or as offsets", utc and utc[0]["start"] == 1793736000
          and utc[0]["end"] == 1793737800 and pt and pt[0]["start"] == 1793642400)
    check("\"Jan 12\" written in late October means next January",
          time.localtime(meetings.moments("Jan 12 at 9am", now)[0]["start"]).tm_year == 2027)


def inbox(pid: int):
    first = at(4, 14, 30)
    stats = mail.process_messages(pid, None, [email_(
        "Interview invitation: Software Developer Intern",
        f"Hi Alex,\n\nThanks for applying to the Software Developer Intern role at Northwind Robotics. We'd like to "
        f"invite you to an interview on {fmt(first)} with our team on Zoom: https://zoom.us/j/123456789\n\nBest,\n"
        "Priya Shah\nNorthwind Robotics", "Priya Shah", "priya@northwindrobotics.com", "m-int-1")])
    it = items(pid, kind="interview")
    app = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, it[0]["job_id"])) if it else None
    check("an interview email puts the interview on the calendar, with its Zoom link and who it's with",
          stats.get("meetings") == 1 and len(it) == 1 and it[0]["start_ts"] == int(first.timestamp())
          and it[0]["end_ts"] == it[0]["start_ts"] + 3600 and it[0]["join_url"] == "https://zoom.us/j/123456789"
          and "priya@northwindrobotics.com" in it[0]["who"] and it[0]["email_link"] == "https://mail.example/m-int-1")
    check("...on the right application, which shows the interview time too", app and app["status"] == "interviewing"
          and app["interview_at"] == it[0]["start_ts"])
    check("...and only the item is kept: no email body anywhere in it",
          "Thanks for applying" not in str(it[0]) and len(it[0]["email_subject"]) < 200)

    moved = at(5, 11)
    mail.process_messages(pid, None, [email_(
        "Re: Interview invitation: Software Developer Intern",
        f"Hi Alex, could we move your interview to {fmt(moved)}? Same Zoom link.\n\nPriya Shah\nNorthwind Robotics",
        "Priya Shah", "priya@northwindrobotics.com", "m-int-2")])
    it = items(pid, kind="interview")
    check("a new time for the same interview moves it (not a second one)", len(it) == 1
          and it[0]["start_ts"] == int(moved.timestamp()))

    with db.tx() as c:  # the interview happened
        c.execute("UPDATE calendar_items SET start_ts=?, end_ts=? WHERE id=?",
                  (db.now() - 3 * 86400, db.now() - 3 * 86400 + 3600, it[0]["id"]))
    second = at(9, 10)
    mail.process_messages(pid, None, [email_(
        "Final round interview: Software Developer Intern",
        f"Hi Alex, great news! We'd like to invite you to a final round interview on {fmt(second)}.\n\nPriya Shah\n"
        "Northwind Robotics", "Priya Shah", "priya@northwindrobotics.com", "m-int-3")])
    check("a later round is a second item: every round stays on the calendar", len(items(pid, kind="interview")) == 2)

    due = at(6, 23, 59)
    mail.process_messages(pid, None, [email_(
        "Lumen Robotics: online assessment", f"Hi Alex, please complete the online assessment for the Data Analyst "
        f"Intern role by {due:%B} {due.day}, 11:59 PM: https://www.hackerrank.com/test/abc123\n\nLumen Robotics Talent",
        "Lumen Talent", "talent@lumenrobotics.com", "m-oa")])
    oa = items(pid, kind="oa")
    app = db.row("SELECT oa_due_at FROM applications WHERE profile_id=? AND job_id=?", (pid, oa[0]["job_id"])) if oa else {}
    check("an assessment's deadline goes on the calendar (and the application), with the link to start it",
          len(oa) == 1 and oa[0]["due_ts"] == int(due.timestamp()) and oa[0]["start_ts"] is None
          and oa[0]["join_url"].startswith("https://www.hackerrank.com/") and app.get("oa_due_at") == oa[0]["due_ts"])

    apps_before = db.row("SELECT COUNT(*) AS n FROM applications WHERE profile_id=?", (pid,))["n"]
    chat = at(3, 15)
    mail.process_messages(pid, None, [email_(
        "Coffee chat?", f"Hi Alex! Great meeting you at the career night. Would you want to grab a virtual coffee "
        f"{fmt(chat)}? Happy to chat about my path at Shopify.\n\nJane Doe\nShopify", "Jane Doe", "jane@shopify.com",
        "m-coffee")])
    cc = items(pid, kind="coffee_chat")
    check("a coffee chat goes on the calendar as a coffee chat, with whoever it's with",
          len(cc) == 1 and cc[0]["start_ts"] == int(chat.timestamp()) and cc[0]["end_ts"] == cc[0]["start_ts"] + 1800
          and cc[0]["company"] == "Shopify" and cc[0]["job_id"] is None)
    check("...and it doesn't make a fake application (it used to: \"interviewing at Jane Doe\")",
          db.row("SELECT COUNT(*) AS n FROM applications WHERE profile_id=?", (pid,))["n"] == apps_before)

    mail.process_messages(pid, None, [email_(
        "Interview invitation: Backend Intern", "Hi Alex, we'd love to move you forward. Please book a time that works "
        "for you: https://calendly.com/acme-recruiting/interview\n\nSam\nAcme", "Sam", "sam@acme.com", "m-book")])
    tb = items(pid, status="to_book")
    check("an invitation to book a time, with no time yet, is \"still to book\" with its link",
          len(tb) == 1 and tb[0]["book_url"] == "https://calendly.com/acme-recruiting/interview" and tb[0]["start_ts"] is None)
    booked = at(7, 13)
    mail.process_messages(pid, None, [email_(
        "Confirmed: Interview with Acme", f"Your interview is confirmed for {fmt(booked)}. Join with Google Meet: "
        "https://meet.google.com/xyz-abcd-efg\n\nAcme Recruiting", "Acme Recruiting", "sam@acme.com", "m-booked")])
    tb2 = db.row("SELECT * FROM calendar_items WHERE id=?", (tb[0]["id"],))
    check("...and the confirmation fills in that same item: booked, with the time and the call link",
          tb2["status"] == "confirmed" and tb2["start_ts"] == int(booked.timestamp())
          and tb2["join_url"] == "https://meet.google.com/xyz-abcd-efg")

    start = at(8, 16)
    mail.process_messages(pid, None, [email_(
        "Invitation: Info session with Wealthsimple", "You have been invited to the following event.",
        "Google Calendar", "calendar-notification@google.com", "m-inv-1",
        ics=invite("evt-777@google.com", start, 60, "Wealthsimple info session"))])
    inv = items(pid, ics_uid="evt-777@google.com")
    check("a calendar invite attached to an email goes on the calendar as written in it",
          len(inv) == 1 and inv[0]["kind"] == "info_session" and inv[0]["start_ts"] == int(start.timestamp())
          and inv[0]["join_url"] == "https://meet.google.com/abc-defg-hij" and inv[0]["status"] == "confirmed")
    later = at(8, 18)
    mail.process_messages(pid, None, [email_(
        "Updated invitation: Info session with Wealthsimple", "This event has been changed.", "Google Calendar",
        "calendar-notification@google.com", "m-inv-2", ics=invite("evt-777@google.com", later, 60, "Wealthsimple info session"))])
    inv = items(pid, ics_uid="evt-777@google.com")
    check("...the organizer moving it moves it", len(inv) == 1 and inv[0]["start_ts"] == int(later.timestamp()))
    mail.process_messages(pid, None, [email_(
        "Canceled event: Info session with Wealthsimple", "This event has been canceled.", "Google Calendar",
        "calendar-notification@google.com", "m-inv-3",
        ics=invite("evt-777@google.com", later, 60, "Wealthsimple info session", "CANCEL", "CANCELLED"))])
    check("...and cancelling it cancels it", items(pid, ics_uid="evt-777@google.com")[0]["status"] == "cancelled")

    raw = email.message.EmailMessage()
    raw["Subject"], raw["From"], raw["Message-ID"] = "Invitation: Coffee chat", "Jane <jane@shopify.com>", "<x@y>"
    raw.set_content("You're invited.")
    raw.add_attachment(invite("u1", at(3, 9), 30, "Coffee chat").encode(), maintype="text", subtype="calendar",
                       filename="invite.ics")
    parsed = mail._parse_message(raw.as_bytes())
    check("an invite attached to a real email is read from it", "BEGIN:VEVENT" in parsed["ics"]
          and "UID:u1" in parsed["ics"])


def calendar_and_ui(pid: int):
    feed = events.ics_for(events.application_dates(pid))
    check("the calendar link has the interviews, the deadline and the coffee chat, each with a reminder",
          feed.count("BEGIN:VEVENT") >= 5 and "TRIGGER:-PT60M" in feed and "TRIGGER:-PT1440M" in feed
          and "TRIGGER:-PT30M" in feed and "Coffee chat: Shopify" in feed and "(due)" in feed)
    check("...without the tracker's own copy of the same interview, and without cancelled ones",
          feed.count("SUMMARY:Interview: Northwind Robotics") == 2 and "Wealthsimple info session" not in feed
          and "UID:interview-" not in feed)
    from coopcoach import server
    server._sync_in_background = lambda: None
    cl = TestClient(server.app, base_url="http://localhost")
    d = cl.get(f"/api/events?profile_id={pid}").json()
    mine = d["mine"]
    check("the Events tab gets yours: what's coming, and what's still to book", {m["kind"] for m in mine} >= {
        "interview", "oa", "coffee_chat"} and any(m["status"] == "to_book" for m in mine) is False
          and all(m["status"] != "cancelled" for m in mine))
    up = cl.get(f"/api/autopilot?profile_id={pid}").json()["upcoming"]
    check("Today's coming-up list has the coffee chat and the assessment", {u["kind"] for u in up} >= {
        "coffee_chat", "assessment"})
    cc = items(pid, kind="coffee_chat")[0]
    check("taking one off the calendar (and not someone else's)", cl.delete(
        f"/api/calendar/{cc['id']}?profile_id={pid + 99}").status_code in (404, 423)
          and cl.delete(f"/api/calendar/{cc['id']}?profile_id={pid}").json()["ok"] and not items(pid, kind="coffee_chat"))


def pasted_and_undo(pid: int):
    apps_before = db.row("SELECT COUNT(*) AS n FROM applications WHERE profile_id=?", (pid,))["n"]
    chat = at(2, 12)
    out = agent.call(pid, "log_email", {"text": f"From: Maya Chen <maya@cohere.com>\nSubject: Coffee chat\n\nHi Alex, "
                                                  f"it was great to meet you! Want to grab a coffee {fmt(chat)}? Happy to "
                                                  "chat about my path.\n\nMaya Chen\nCohere"}, source="chat")
    check("a pasted coffee chat goes on the calendar (no application made), with who it's with",
          out.get("ok") and out["meeting"] == "Coffee chat" and out["start_ts"] == int(chat.timestamp())
          and db.row("SELECT COUNT(*) AS n FROM applications WHERE profile_id=?", (pid,))["n"] == apps_before
          and items(pid, kind="coffee_chat") and "on your calendar" in out["next_steps"][0])
    agent.undo_action(pid, db.row("SELECT id FROM agent_actions WHERE tool='log_email' ORDER BY id DESC LIMIT 1")["id"])
    check("...and undoing it takes it off again", not items(pid, kind="coffee_chat"))

    when = at(5, 10)
    out = agent.call(pid, "log_email", {"text": f"From: Kim Lee <kim@globex.com>\nSubject: Interview at Globex\n\nHi "
                                                f"Alex, we'd like to invite you to an interview for the Data Engineer "
                                                f"Intern role on {fmt(when)}.\n\nKim Lee\nGlobex Recruiting"}, source="chat")
    pasted = [i for i in items(pid, kind="interview") if i["source"] == "pasted"]
    check("a pasted interview email puts the interview on the calendar too", out.get("ok") and pasted
          and pasted[0]["start_ts"] == int(when.timestamp()))
    ev = db.row("SELECT id FROM email_events WHERE profile_id=? AND job_id=? ORDER BY created_at DESC LIMIT 1",
                (pid, out["job_id"]))
    from coopcoach import server
    cl = TestClient(server.app, base_url="http://localhost")
    cl.put(f"/api/email/events/{ev['id']}", json={"ignored": True})
    check("marking an email \"not about a job\" takes its items off the calendar",
          not [i for i in items(pid, kind="interview") if i["source"] == "pasted"])


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json) VALUES (?,?,?,?)",
                        ("Alex", now, now, db.dumps({"track_email": True}))).lastrowid
    kinds_and_dates()
    inbox(pid)
    calendar_and_ui(pid)
    pasted_and_undo(pid)
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
