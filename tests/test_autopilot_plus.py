"""Autopilot's options: settings checks, what it picks (filters, blocklist, company mix, learning from "Not for me"),
weekly-goal pacing, pause and schedule, cleanup, follow-up / thank-you / prep drafts, auto-saved events, instant mode,
quiet hours, preview and the apply session. No AI (built-in rules), no alerts sent.

Run: .venv/bin/python -m tests.test_autopilot_plus
"""

import json
import os
import tempfile
import time
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="coopcoach-aptest-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent")

from coopcoach import agent, autopilot, db, notify, store  # noqa: E402

SENT: list = []
notify.send = lambda *a, **k: SENT.append(("send", k.get("title", ""))) or ["test"]  # noqa: E731
notify.send_text = lambda prefs, title, lines, click="": SENT.append(("text", title, lines)) or ["test"]  # noqa: E731


def job(company, title, where, desc, tier_src="lever", posted_days=1):
    return {"source": f"{tier_src}:{company.lower()}", "company": company, "title": title,
            "url": f"https://jobs.lever.co/{company.lower()}/{abs(hash(title)) % 10**8}", "locations": [where],
            "description": desc, "posted_at": db.now() - posted_days * 86400}


def seed():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES (?,?,?,?,?)",
                        ("Tester", now, now, json.dumps({"locations": ["Toronto", "Canada", "Remote"], "citizenship": "Canadian",
                                                         "autopilot": {"notify": False}}),
                         json.dumps({"name": "Alex Chen", "email": "alex@example.com", "skills": ["Python", "Go", "React"],
                                     "target_categories": ["Software"], "summary": "Year 3 CS"}))).lastrowid
        c.execute("INSERT INTO event_feeds (id, url, label, created_at) VALUES (1, 'https://x', 'Meetup', ?)", (now,))
        c.execute("INSERT INTO events (id, feed_id, title, start_ts, location, url, description, kind, first_seen,"
                  " last_seen, region) VALUES ('e1',1,'Waterloo Hackathon',?, 'Waterloo, ON','https://e','', 'hackathon',?,?,"
                  " 'Waterloo Region')", (now + 3 * 86400, now, now))
    raw = [job("Shopify", "Software Engineer Intern", "Toronto, ON", "Python and Go. Canada based applicants only."),
           job("Wealthsimple", "Backend Developer Co-op", "Toronto, ON", "Python, Go, Postgres."),
           job("Cohere", "ML Engineer Intern", "Toronto, ON", "Python, PyTorch."),
           job("Stripe", "Software Engineer Intern", "Seattle, WA", "Python, Go, Ruby."),
           job("Usonly", "Software Engineer Intern (USA)", "Toronto, ON", "Python. US based applicants only."),
           job("Oldco", "Software Developer Intern", "Toronto, ON", "Python, Go.", posted_days=40)]
    jobs = [store.normalize(r) for r in raw]
    store.upsert_jobs(jobs, now)
    return pid, {j["company"]: j["id"] for j in jobs}


def main():
    pid, ids = seed()
    checks = {}

    cfg = autopilot.save_settings(pid, {"days": [0, 9, 2], "time": "25:99", "per_day": 400, "min_score": -5,
                                        "eligibility": "weird", "fill_with": "claude_code", "mix": {"bigtech": 2, "x y": 1},
                                        "paused_until": "not-a-date", "blocklist": ["  Cohere ", ""]})
    checks["settings are checked: bad days/times/numbers are fixed or ignored"] = cfg["days"] == [0, 2] and \
        cfg["time"] == "08:00" and cfg["per_day"] == autopilot.MAX_PER_RUN and cfg["min_score"] == 0 and cfg["eligibility"] == "any"
    checks["...and good ones stick"] = cfg["fill_with"] == "claude_code" and cfg["mix"] == {"bigtech": 2} and \
        cfg["blocklist"] == ["Cohere"] and cfg["paused_until"] == ""

    autopilot.save_settings(pid, {"min_score": 0, "mix": {}, "per_day": 3})
    picks = autopilot.candidates(pid)
    names = [j["company"] for j in picks]
    checks["never picks a posting they can't apply to"] = "Usonly" not in names
    checks["respects the blocklist"] = "Cohere" not in names and "Shopify" in names
    autopilot.save_settings(pid, {"posted_within": 7, "blocklist": []})
    checks["'posted within' drops old postings"] = "Oldco" not in [j["company"] for j in autopilot.candidates(pid)]
    autopilot.save_settings(pid, {"locations": ["Seattle"]})
    checks["places narrow the picks"] = [j["company"] for j in autopilot.candidates(pid)] == ["Stripe"]
    autopilot.save_settings(pid, {"locations": [], "eligibility": "fit"})
    checks["'clearly open to me' keeps only explicit fits"] = [j["company"] for j in autopilot.candidates(pid)] == ["Shopify"]
    autopilot.save_settings(pid, {"eligibility": "any", "posted_within": 0})
    first = autopilot.candidates(pid)[0]
    checks["each pick says why"] = first["pick_reasons"] and "match" in first["pick_reasons"][0]

    p = autopilot.preview(pid)
    checks["preview shows what it would prepare without preparing"] = p["would_prepare"] == 3 and \
        sum(x["chosen"] for x in p["picks"]) == 3 and not db.row("SELECT 1 FROM ready_queue")

    r = autopilot.run(pid, "manual")
    queued = db.rows("SELECT job_id, status FROM ready_queue WHERE profile_id=?", (pid,))
    checks["a run prepares the top picks"] = r.get("queued") == 3 and len(queued) == 3
    checks["...with a brief"] = "Ready to apply" in r["brief"]

    stripe = ids["Stripe"]
    if any(q["job_id"] == stripe for q in queued):
        autopilot.skip(pid, stripe, "location")
    else:
        agent.call(pid, "queue_application", {"job_id": stripe})
        autopilot.skip(pid, stripe, "location")
    pen = autopilot.learned(pid)
    checks["'Not for me: wrong place' teaches it to avoid that city"] = pen["places"].get("seattle") == 4
    autopilot.skip(pid, ids["Wealthsimple"], "company") if db.row(
        "SELECT 1 FROM ready_queue WHERE job_id=?", (ids["Wealthsimple"],)) else None
    if db.row("SELECT 1 FROM ready_queue WHERE job_id=?", (ids["Wealthsimple"],)):
        checks["'Not for me: that company' adds it to the blocklist"] = "Wealthsimple" in autopilot.settings(pid)["blocklist"]

    from coopcoach import routes_auto
    loose = next(j for j in ids.values() if not db.row("SELECT 1 FROM ready_queue WHERE job_id=?", (j,)))
    routes_auto.queue_status(loose, {"profile_id": pid, "status": "skipped", "reason": "role"})
    row = db.row("SELECT status, skip_reason FROM ready_queue WHERE profile_id=? AND job_id=?", (pid, loose))
    checks["'Not for me' straight from Matches (never prepared) is kept and learned from"] = bool(row) and \
        row["status"] == "skipped" and row["skip_reason"] == "role"

    with db.tx() as c:
        c.execute("UPDATE jobs SET active=0 WHERE id=?", (ids["Shopify"],))
    store.invalidate_cache()
    tidy = autopilot.cleanup(pid, autopilot.settings(pid))
    note = db.row("SELECT status, note FROM ready_queue WHERE job_id=?", (ids["Shopify"],))
    checks["a prepared application whose posting closed leaves Ready to apply"] = tidy["closed"] == 1 and \
        note["status"] == "skipped" and note["note"] == "posting closed"
    with db.tx() as c:
        c.execute("UPDATE jobs SET active=1 WHERE id=?", (ids["Shopify"],))
    store.invalidate_cache()
    agent.call(pid, "queue_application", {"job_id": ids["Cohere"]})
    with db.tx() as c:
        c.execute("UPDATE ready_queue SET created_at=? WHERE job_id=?", (db.now() - 20 * 86400, ids["Cohere"]))
    checks["...and so does one left unopened too long"] = autopilot.cleanup(pid, autopilot.settings(pid))["expired"] >= 1

    autopilot.save_settings(pid, {"weekly_goal": 4, "days": [0, 1, 2, 3, 4, 5, 6]})
    with db.tx() as c:
        c.execute("DELETE FROM ready_queue")
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at, applied_at) VALUES (?,?,?,?,?)",
                  (pid, ids["Oldco"], "applied", db.now(), db.now()))
    w = autopilot.week_progress(pid)
    size = autopilot.run_size(pid, autopilot.settings(pid))
    import math
    checks["weekly goal paces how many it prepares"] = w["applied"] == 1 and size == math.ceil(3 / w["days_left"])
    autopilot.save_settings(pid, {"weekly_goal": 1})
    checks["...and prepares nothing new once the goal is met"] = autopilot.run_size(pid, autopilot.settings(pid)) == 0
    autopilot.save_settings(pid, {"weekly_goal": 0})

    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    cfg = autopilot.save_settings(pid, {"on": True, "paused_until": tomorrow})
    checks["pause until a date"] = autopilot.paused(cfg) and \
        datetime.fromtimestamp(autopilot.next_run(cfg)).date() > (datetime.now() + timedelta(days=1)).date()
    cfg = autopilot.save_settings(pid, {"paused_until": "", "days": [(datetime.now().weekday() + 2) % 7]})
    checks["only runs on the chosen days"] = datetime.fromtimestamp(autopilot.next_run(cfg)).weekday() == cfg["days"][0]
    autopilot.save_settings(pid, {"days": [0, 1, 2, 3, 4, 5, 6], "on": False})

    with db.tx() as c:
        c.execute("UPDATE applications SET applied_at=?, updated_at=? WHERE job_id=?", (db.now() - 30 * 86400,
                                                                                         db.now() - 30 * 86400, ids["Oldco"]))
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at, applied_at) VALUES (?,?,?,?,?)",
                  (pid, ids["Stripe"], "interviewing", db.now(), db.now() - 10 * 86400))
    extra = autopilot.housekeeping(pid, autopilot.settings(pid), "run-x")
    kinds = {r["kind"] for r in db.rows("SELECT kind FROM documents WHERE profile_id=?", (pid,))}
    checks["drafts a follow-up for a quiet application"] = extra["followups"] == ["Oldco"] and "followup" in kinds
    checks["drafts a thank-you after an interview"] = extra["thankyous"] == ["Stripe"] and "thankyou" in kinds
    checks["writes an interview prep sheet"] = extra["preps"] == ["Stripe"] and "prep" in kinds
    prep = db.row("SELECT content FROM documents WHERE kind='prep'")["content"]
    checks["...prep sheet has questions and a plan"] = "Likely questions" in prep and "Plan" in prep
    fu = db.row("SELECT content FROM documents WHERE kind='followup'")["content"]
    checks["...follow-up is a draft with a subject, never sent"] = fu.startswith("Subject:") and not SENT
    checks["saves hackathons near them (Waterloo counts as near Toronto)"] = (db.row(
        "SELECT mark FROM event_marks WHERE event_id='e1'") or {}).get("mark") == "interested" and any(
        a["tool"] == "save_event" and a["source"] == "autopilot" for a in agent.activity(pid))
    again = autopilot.housekeeping(pid, autopilot.settings(pid), "run-y")
    checks["...and doesn't redo any of it next run"] = not any(again.values())
    act = next(a for a in agent.activity(pid) if a["tool"] == "draft_followup" and a["summary"].startswith("drafted a follow-up"))
    agent.undo_action(pid, act["id"])
    checks["Undo discards a draft"] = not db.row("SELECT 1 FROM documents WHERE kind='followup'")

    cfg = autopilot.save_settings(pid, {"quiet_from": "00:00", "quiet_to": "23:59", "notify": True, "on": True})
    checks["quiet hours are understood"] = autopilot._in_quiet(cfg) and not autopilot._in_quiet({"quiet_from": "", "quiet_to": ""})
    with db.tx() as c:
        c.execute("DELETE FROM ready_queue")
        c.execute("DELETE FROM applications")
    store.invalidate_cache()
    autopilot.save_settings(pid, {"instant": True, "instant_score": 0, "min_score": 0})
    autopilot.on_sync({ids["Shopify"]})
    inst = db.row("SELECT trigger, queued, notified FROM autopilot_runs WHERE trigger='instant'")
    checks["⚡ instant prepares a just-posted match after a sync"] = inst and inst["queued"] == 1 and \
        db.row("SELECT 1 FROM ready_queue WHERE job_id=?", (ids["Shopify"],))
    checks["...and holds the alert during quiet hours"] = inst["notified"] == 0 and not SENT
    autopilot.save_settings(pid, {"quiet_from": "", "quiet_to": ""})
    autopilot.tick()
    checks["...then sends it once quiet hours end"] = any(s[0] == "send" for s in SENT) and \
        db.row("SELECT notified FROM autopilot_runs WHERE trigger='instant'")["notified"] == 1

    nxt = agent.call(pid, "apply_session_next", {})
    checks["apply session serves the next prepared application"] = nxt["next"]["company"] == "Shopify" and nxt["left"] == 1
    autopilot.save_settings(pid, {"mix": {"bigtech": 1}})
    with db.tx() as c:
        c.execute("UPDATE jobs SET tier='bigtech' WHERE id=?", (ids["Stripe"],))
    store.invalidate_cache()
    picks = autopilot.candidates(pid, limit=2)
    checks["company mix puts its quota first"] = picks and picks[0]["tier"] == "bigtech"
    lines = autopilot.weekly_recap(pid)
    checks["weekly recap sums up the week"] = lines[0].startswith("Applied:")
    today(pid, ids, checks)

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


def today(pid, ids, checks):
    """Today: Jarvis's plan read from plain words, a filter for the kind of company, and a run of any size."""
    t = autopilot.parse_tell("15 a day, only startups in Toronto and Remote, skip Amazon and Meta, at least 75, weekdays at 9am")
    checks["Tell Jarvis: plain words become the plan (how many, kind of company, places, never, score, days, time)"] = t == {
        "per_day": 15, "tiers": ["startup", "yc"], "locations": ["Toronto", "Remote"], "blocklist": ["Amazon", "Meta"],
        "min_score": 75, "days": [0, 1, 2, 3, 4], "time": "09:00"}
    checks["...the vibe words too (chill 3, steady 10, all in 25), and it can switch Autopilot on or off"] = [
        autopilot.parse_tell(x).get("per_day") for x in ("make it chill", "steady please", "go all in")] == [3, 10, 25] \
        and autopilot.parse_tell("turn it on")["on"] is True and autopilot.parse_tell("stop")["on"] is False
    out = autopilot.tell(pid, "prepare 30 a day for big tech")
    checks["...and saves only what it was told to change, up to 50 a run"] = out["changed"] == {
        "per_day": 30, "tiers": ["bigtech"]} and autopilot.tell(pid, "200 a day")["settings"]["per_day"] == 50
    checks["...and says so when nothing in the words is a change"] = "error" in autopilot.tell(pid, "hello jarvis")
    autopilot.save_settings(pid, {"blocklist": []})
    autopilot.tell(pid, "skip Amazon")
    checks["...\"skip\" adds to the companies already skipped, once each"] = autopilot.tell(pid, "skip Meta and Amazon")[
        "settings"]["blocklist"] == ["Amazon", "Meta"]
    autopilot.save_settings(pid, {"tiers": ["bigtech"], "mix": {}, "blocklist": [], "min_score": 0, "eligibility": "any",
                                  "locations": [], "categories": [], "terms": [], "posted_within": 0})
    # what earlier runs prepared depends on how ties sort (SQLite and Postgres differ): start from an empty queue
    queued = db.rows("SELECT * FROM ready_queue WHERE profile_id=?", (pid,))
    with db.tx() as c:
        c.execute("DELETE FROM ready_queue WHERE profile_id=?", (pid,))
    picks = autopilot.candidates(pid, limit=10)
    checks["a kind-of-company filter keeps only those companies"] = picks and {p["tier"] for p in picks} == {"bigtech"}
    with db.tx() as c:  # put the queue back for the checks after this one
        for r in queued:
            c.execute(f"INSERT INTO ready_queue ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", list(r.values()))
    autopilot.save_settings(pid, {"tiers": [], "per_day": 1, "prerender": False, "tailor": False, "letter": False})
    with db.tx() as c:
        c.execute("DELETE FROM ready_queue WHERE profile_id=?", (pid,))
    store.upsert_jobs([store.normalize(job(co, "Software Engineer Intern", "Toronto, ON", "Python and Go."))
                       for co in ("Ramp", "Figma", "Notion", "Linear")], db.now())
    store.invalidate_cache()
    for _ in range(120):  # an instant run from the checks above may still be finishing
        if not autopilot.state(pid).get("running"):
            break
        time.sleep(0.25)
    r = autopilot.run(pid, "manual", count=3)
    checks["'Prepare N now' prepares that many this once, whatever the plan says"] = r.get("queued") == 3 \
        and autopilot.settings(pid)["per_day"] == 1


if __name__ == "__main__":
    main()
