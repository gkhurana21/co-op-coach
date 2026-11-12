"""The chat's newer abilities, without AI (built-in rules): add an application, draft a message, fix the resume in one
go (with Undo), change Autopilot, "not for me", reminders (on the calendar, and they ring), opening links, and the
errors that tell the chat what to ask.

Run: .venv/bin/python -m tests.test_chat_tools
"""

import json
import os
import subprocess
import tempfile

TMP = tempfile.mkdtemp(prefix="coopcoach-chattools-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent",
                  COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import agent, autopilot, db, events, notify, routes_docs, store  # noqa: E402

RAN: list = []
subprocess.run = lambda args, **k: RAN.append(args)  # no real browser tabs or notifications
import webbrowser  # noqa: E402

webbrowser.open = lambda url, *a, **k: RAN.append(["open", url]) or True  # Linux / Windows
SENT: list = []
notify.send_text = lambda prefs, title, lines, click="": SENT.append((title, lines)) or ["test"]  # noqa: E731

RESUME = {"header": {"name": "Alex Chen", "location": "Toronto , ON"},
          "experience": [{"title": "Software Engineer Inter", "company": "Acme", "dates": "MAy 2025 - Aug 2025",
                          "bullets": ["Built a data pipeline in Python."]}],
          "skills": [{"label": "Languages", "items": "Python, Go,"}]}


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES (?,?,?,?,?)",
                        ("Tester", now, now, json.dumps({"locations": ["Toronto"], "autopilot": {"notify": False}}),
                         json.dumps({"name": "Alex Chen", "skills": ["Python"], "summary": "Year 3 CS"}))).lastrowid
    shop = store.normalize({"source": "lever:shopify", "company": "Shopify", "title": "Software Engineer Intern",
                            "url": "https://jobs.lever.co/shopify/1", "locations": ["Toronto, ON"], "description": "Python.",
                            "posted_at": now - 86400})
    store.upsert_jobs([shop], now)
    checks = {}

    a = agent.call(pid, "add_application", {"company": "Ramp", "title": "Software Engineer Intern", "status": "applied"})
    row = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, a.get("job_id")))
    checks["add_application tracks a company Co-op Coach doesn't list"] = a.get("ok") and row and row["status"] == "applied"
    b = agent.call(pid, "add_application", {"company": "Shopify", "title": "Software Engineer Intern"})
    checks["...and uses the real posting when it has it"] = b.get("matched_posting") and db.row(
        "SELECT 1 FROM applications WHERE profile_id=? AND job_id=?", (pid, shop["id"]))
    agent.call(pid, a["_undo"]["tool"], a["_undo"]["args"])
    checks["...Undo removes it"] = not db.row("SELECT 1 FROM applications WHERE profile_id=? AND job_id=?", (pid, a["job_id"]))

    m = agent.call(pid, "draft_message", {"kind": "extension", "job_id": shop["id"], "notes": "need 3 more days for the OA"})
    d = db.row("SELECT * FROM documents WHERE id=?", (m.get("doc_id"),))
    checks["draft_message saves a draft (never sent)"] = m.get("ok") and d and d["kind"] == "message" and \
        "Shopify" in d["title"] and m["_card"]["type"] == "doc"
    checks["...and Undo throws it away"] = agent.call(pid, "discard_draft", m["_undo"]["args"]).get("ok") and \
        not db.row("SELECT 1 FROM documents WHERE id=?", (m["doc_id"],))
    checks["draft_message asks which company"] = "company" in agent.call(pid, "draft_message", {"kind": "referral"}).get("error", "")

    routes_docs._save_doc(pid, "resume_master", db.dumps(RESUME), "Master resume", by="you")
    f = agent.call(pid, "fix_resume", {})
    fixed = routes_docs.master(pid)
    checks["fix_resume fixes the small things and the score goes up"] = f.get("fixed", 0) >= 3 and \
        f["score"] > f["score_before"] and fixed["experience"][0]["title"] == "Software Engineer Intern"
    agent.call(pid, f["_undo"]["tool"], f["_undo"]["args"])
    checks["...Undo puts the old resume back"] = routes_docs.master(pid)["experience"][0]["title"] == "Software Engineer Inter"

    ch = agent.call(pid, "change_autopilot", {"per_day": 7, "block_company": "Oracle", "paused_until": "2030-01-05"})
    cfg = autopilot.settings(pid)
    checks["change_autopilot changes settings"] = cfg["per_day"] == 7 and "Oracle" in cfg["blocklist"] and \
        cfg["paused_until"] == "2030-01-05"
    agent.call(pid, "change_autopilot", ch["_undo"]["args"])
    cfg = autopilot.settings(pid)
    checks["...and Undo restores them"] = cfg["per_day"] != 7 and "Oracle" not in cfg["blocklist"] and not cfg["paused_until"]

    nf = agent.call(pid, "not_for_me", {"job_id": shop["id"], "reason": "company"})
    checks["not_for_me hides it and learns (a company reason blocks the company)"] = nf.get("ok") and \
        "Shopify" in autopilot.settings(pid)["blocklist"] and shop["id"] not in [j["id"] for j in autopilot.candidates(pid)]

    soon = db.now() + 3600
    from datetime import datetime
    r = agent.call(pid, "remind_me", {"title": "Follow up with Ramp", "when": datetime.fromtimestamp(soon).isoformat(timespec="minutes")})
    checks["remind_me sets a reminder"] = r.get("ok") and agent.call(pid, "reminders", {})["reminders"][0]["title"] == "Follow up with Ramp"
    checks["...it's on the calendar link"] = any(e["id"] == f"reminder-{r['id']}" for e in events.application_dates(pid))
    checks["...a past time is refused"] = "passed" in agent.call(pid, "remind_me", {"title": "x", "when": "2020-01-01T09:00"}).get("error", "")
    with db.tx() as c:
        c.execute("UPDATE reminders SET due_at=? WHERE id=?", (db.now() - 5, r["id"]))
    n = agent.fire_reminders()
    checks["...it rings when it's time (once)"] = n == 1 and SENT and SENT[-1][1] == ["Follow up with Ramp"] and \
        agent.fire_reminders() == 0
    agent.call(pid, "cancel_reminder", {"reminder_id": r["id"]})
    checks["cancel_reminder removes it"] = not agent.call(pid, "reminders", {})["reminders"]

    o = agent.call(pid, "open_link", {"job_id": shop["id"]})
    checks["open_link opens the posting"] = o.get("ok") and RAN and RAN[-1][-1].startswith("https://")
    checks["...but never a non-web link"] = "error" in agent.call(pid, "open_link", {"url": "file:///etc/passwd"})
    checks["...and never a link that isn't already in Co-op Coach (e.g. one planted in a web page)"] = "error" in \
        agent.call(pid, "open_link", {"url": "https://evil.example/login"})
    checks["...while a known posting link opens"] = agent.call(pid, "open_link", {"url": shop["url"]}).get("ok")
    checks["research needs an AI engine and says so"] = "AI engine" in agent.call(pid, "research_company", {"company": "Ramp"}).get("error", "")
    checks["check_inbox says how to connect when there's no inbox"] = "Inbox" in agent.call(pid, "check_inbox", {}).get("error", "")

    from coopcoach import mcp_server  # noqa: F401  (the MCP server builds its tool list from the same table)
    names = set(agent.CHAT_TOOLS)
    checks["the chat has all the new tools"] = {"research_company", "draft_message", "fix_resume", "run_autopilot",
                                                "change_autopilot", "refresh_jobs", "not_for_me", "add_event_source",
                                                "open_link", "remind_me", "reminders", "check_inbox",
                                                "add_application", "log_email", "set_dates"} <= names
    checks["...every tool's schema is valid JSON"] = all(json.dumps(t[2]) for t in agent.TOOLS.values())
    checks["...and the summaries read well"] = all(agent._summary(n, {"ok": True}) for n in agent.TOOLS)

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
