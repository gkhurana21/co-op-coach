"""Pasting a recruiting email (chat or Applications → Paste an email): it reads who it's from (even when only the
sign-off names the company), finds the posting you never tracked, moves the status, keeps the booking / assessment
link, puts a stated time or deadline on the calendar, starts a prep sheet, and Undo puts everything back.
No AI (built-in rules).

Run: .venv/bin/python -m tests.test_email_paste
"""

import json
import os
import tempfile
import time
from datetime import datetime

TMP = tempfile.mkdtemp(prefix="coopcoach-paste-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent",
                  COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import agent, db, mail, store  # noqa: E402

INVITE = """Hi Alex,

We've reviewed your application for the Intern - Software/ML Engineering role, and we'd like to move to the next step in the interview process!

Could you please book a time for an intro call with us here: https://app.ashbyhq.com/meeting/0000demo-intro-call/

All the best,

Sam

Lumen Robotics Hiring Team"""

OA = """From: Priya Shah <priya@ramp.com>
Subject: Ramp - Software Engineer Intern - Online Assessment

Hi Alex! Please complete the CodeSignal assessment within 7 days: https://app.codesignal.com/test/abc123
Thanks, Priya | Recruiting, Ramp"""

SOON = datetime.fromtimestamp(time.time() + 5 * 86400)
CONFIRMED = f"""Hi Alex, confirming your interview on {SOON.strftime('%A, %B')} {SOON.day} at 2:30 PM ET with the team.
Join here: https://zoom.us/j/123456
Best, The Shopify Recruiting Team"""


def wait_for(cond, seconds: float = 20) -> bool:
    """Background work (prep sheets) finishes on its own thread: give it time on a busy machine."""
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return bool(cond())


def job(company, title):
    return store.normalize({"source": f"lever:{company.lower()}", "company": company, "title": title, "locations": ["Toronto, ON"],
                            "url": f"https://jobs.lever.co/{company.lower()}/{abs(hash(title)) % 10**8}",
                            "description": "Python.", "posted_at": db.now() - 86400})


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES (?,?,?,?,?)",
                        ("Tester", now, now, json.dumps({"locations": ["Toronto"], "autopilot": {"notify": False}}),
                         json.dumps({"name": "Alex Chen", "skills": ["Python"], "summary": "Year 3 CS"}))).lastrowid
    hcl = job("Lumen Robotics", "Software/ML Engineering Intern")
    shop = job("Shopify", "Software Engineer Intern")
    store.upsert_jobs([hcl, shop, job("Lumen Robotics", "Electrical Engineer Intern")], now)
    agent.call(pid, "track", {"job_id": shop["id"], "status": "applied"})
    checks = {}

    r = mail.read_pasted(INVITE)
    checks["reads the company from the sign-off, not the person who wrote it"] = r["company"] == "Lumen Robotics"
    r2 = mail.read_pasted("Hi Alex,\n\nThanks for applying to the Software Engineering Intern role. We'd love to move you to the "
                          "next step: a 30-minute intro call.\n\nBook here: https://calendly.com/x/intro\n\nBest,\nPriya\n\n"
                          "Northwind Robotics Recruiting Team")
    checks["'applying to the X Intern role' is the role; the sign-off is the company"] = \
        r2["company"] == "Northwind Robotics" and r2["role"] == "Software Engineering Intern"
    checks["...and it's an interview with a booking link"] = r["status"] == "interviewing" and \
        r["links"]["book"].startswith("https://app.ashbyhq.com/meeting/")

    out = agent.call(pid, "log_email", {"text": INVITE}, source="chat")
    a = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, out.get("job_id")))
    checks["finds the posting you never tracked and tracks it as interviewing"] = out.get("job_id") == hcl["id"] and \
        a and a["status"] == "interviewing" and out["was"] == "not tracked"
    checks["keeps the booking link on the application"] = "Book a time: https://app.ashbyhq.com/meeting/" in (a["notes"] or "")
    checks["shows a card with the booking button"] = out["_card"]["type"] == "email" and out["_card"]["book"]
    checks["says the next step (book, then tell me the time)"] = any("Book a time" in x for x in out["next_steps"])
    ev = db.row("SELECT * FROM email_events WHERE profile_id=? AND job_id=?", (pid, hcl["id"]))
    checks["it shows in Inbox activity"] = ev and ev["classified_by"] == "pasted" and ev["status"] == "interviewing"
    checks["starts an interview prep sheet"] = wait_for(lambda: db.row(  # written in the background
        "SELECT 1 FROM documents WHERE profile_id=? AND job_id=? AND kind='prep'", (pid, hcl["id"])))
    again = agent.call(pid, "log_email", {"text": INVITE})
    checks["pasting the same email twice doesn't duplicate it"] = again["already_logged"] and db.row(
        "SELECT COUNT(*) AS n FROM email_events WHERE profile_id=?", (pid,))["n"] == 1

    when = "2026-12-03T15:00"
    sd = agent.call(pid, "set_dates", {"job_id": hcl["id"], "interview_at": when})
    a = db.row("SELECT interview_at FROM applications WHERE profile_id=? AND job_id=?", (pid, hcl["id"]))
    checks["'I booked Dec 3 at 3pm' puts it on the calendar"] = sd.get("ok") and \
        a["interview_at"] == int(datetime.fromisoformat(when).timestamp())
    from coopcoach import events
    checks["...and the calendar feed has it"] = any(e["id"] == f"interview-{hcl['id']}" for e in events.application_dates(pid))
    agent.call(pid, "set_dates", sd["_undo"]["args"])
    checks["undoing the time clears it"] = db.row("SELECT interview_at FROM applications WHERE profile_id=? AND job_id=?",
                                                  (pid, hcl["id"]))["interview_at"] is None

    oa = agent.call(pid, "log_email", {"text": OA})
    a = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, oa["job_id"]))
    checks["an assessment from a company with no posting is added by hand"] = oa["job_id"].startswith("ext:") and \
        a["company"] == "Ramp" and a["status"] == "oa" and a["title"] == "Software Engineer Intern"
    checks["...with its link and 'within 7 days' as the deadline"] = "Assessment: https://app.codesignal.com" in a["notes"] \
        and a["oa_due_at"] and abs(a["oa_due_at"] - (db.now() + 7 * 86400)) < 120
    agent.call(pid, "forget_email", oa["_undo"]["args"])
    checks["Undo removes an application the email added"] = db.row(
        "SELECT 1 FROM applications WHERE profile_id=? AND job_id=?", (pid, oa["job_id"])) is None

    conf = agent.call(pid, "log_email", {"text": CONFIRMED})
    a = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, shop["id"]))
    checks["a confirmation moves your Shopify application to interviewing"] = conf["job_id"] == shop["id"] and \
        a["status"] == "interviewing" and conf["was"] == "applied"
    checks["...with the stated time (2:30 PM ET) and the call link"] = a["interview_at"] and \
        datetime.fromtimestamp(a["interview_at"]).date() == SOON.date() and "Call link: https://zoom.us/j/123456" in a["notes"]
    agent.call(pid, "forget_email", conf["_undo"]["args"])
    a = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, shop["id"]))
    checks["Undo puts it back to applied, without the time or link"] = a["status"] == "applied" and \
        a["interview_at"] is None and "zoom" not in (a["notes"] or "")

    # in a Claude Code / ChatGPT chat the tools run in a helper process that exits after answering: the app writes the
    # prep sheet on its next tick instead of a background thread that would be cut off
    wait_for(lambda: not agent._prepping)  # earlier pastes' prep sheets are done
    agent.SHORT_LIVED = True
    with db.tx() as c:
        c.execute("DELETE FROM documents WHERE profile_id=? AND kind='prep'", (pid,))
    again2 = agent.call(pid, "log_email", {"text": INVITE.replace("All the best", "Thanks")})
    checks["in a Claude Code chat it promises the prep sheet within minutes, not now"] = any(
        "5 minutes" in x for x in again2["next_steps"]) and (pid, hcl["id"]) not in agent._prepping
    agent.SHORT_LIVED = False
    started = agent.prep_pending(pid)
    checks["...and the app's tick writes it"] = started >= 1 and wait_for(lambda: db.row(
        "SELECT 1 FROM documents WHERE profile_id=? AND job_id=? AND kind='prep'", (pid, hcl["id"])))
    wait_for(lambda: not agent._prepping)
    checks["...only once"] = agent.prep_pending(pid) == 0

    bad = agent.call(pid, "log_email", {"text": "Hi, thanks for your interest. We will be in touch with next steps soon."})
    checks["asks which company when it can't tell"] = "company" in bad.get("error", "")
    checks["the chat knows the new tools"] = {"log_email", "set_dates"} <= set(agent.CHAT_TOOLS) and \
        {"log_email", "set_dates"} <= set(agent.GROQ_TOOLS) and "forget_email" not in agent.CHAT_TOOLS

    from coopcoach import routes_docs
    rules = routes_docs._pasted_email(pid, CONFIRMED)
    checks["without an AI engine the chat still tracks a pasted email"] = rules and "Shopify" in rules["reply"] and \
        rules["cards"][0]["type"] == "email" and rules["undo"]

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
