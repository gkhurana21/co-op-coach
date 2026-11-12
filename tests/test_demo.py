"""The one-command demo builds from scratch offline (a fictional student, the bundled postings and events), and the app
serves it: matches ranked, applications at every stage, Autopilot's picks, events and hackathons, the numbers card.

Run: .venv/bin/python -m tests.test_demo
"""

import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="coopcoach-demo-"))
os.environ.update(COOPCOACH_DATA=str(TMP), COOPCOACH_DEMO="1", GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import db, demo, supa  # noqa: E402


def main():
    if os.environ.get("COOPCOACH_PG_SCHEMA") or os.environ.get("COOPCOACH_DATABASE_URL"):
        print("SKIP the demo is SQLite-only by design (its own data-demo/ folder)")
        return
    checks = {}
    checks["the demo never uses Supabase"] = not supa.enabled()
    demo.build(TMP)
    from fastapi.testclient import TestClient

    from coopcoach import server
    with TestClient(server.app, base_url="http://localhost") as cl:
        st = cl.get("/api/state").json()
        p = st["profiles"][0]
        checks["a fictional student, no passcode, flagged as a demo"] = p["name"] == "Alex" and not p.get("locked") and st["demo"]
        checks["the resume was read without AI"] = "Python" in (p.get("profile") or {}).get("skills", [])
        jobs = cl.get(f"/api/jobs?profile_id={p['id']}&limit=50").json()
        checks["250 bundled postings, ranked"] = st["counts"]["active"] == 250 and jobs["jobs"][0]["score"] >= jobs["jobs"][-1]["score"]
        apps = cl.get(f"/api/applications?profile_id={p['id']}").json()["applications"]
        checks["applications at every stage"] = {"saved", "applied", "oa", "interviewing", "rejected"} <= {a["status"] for a in apps}
        checks["the interview has a booking link and a time"] = any(a["status"] == "interviewing" and a["interview_at"]
                                                                     and "Book a time" in (a["notes"] or "") for a in apps)
        ap = cl.get(f"/api/autopilot?profile_id={p['id']}").json()
        checks["Autopilot prepared 3 strong picks and wrote a brief"] = len(ap["queue"]) == 3 and \
            min(q["score"] for q in ap["queue"]) >= 60 and ap["last_run"]["brief"]
        evs = cl.get(f"/api/events?profile_id={p['id']}&days=120").json()
        checks["events with saved ones, dated from today"] = len(evs["events"]) >= 40 and any(e["mark"] for e in evs["events"])
        hk = cl.get(f"/api/events/hackathons?profile_id={p['id']}").json()["hackathons"]
        checks["the hackathon season list has dated majors"] = len(hk) == 40 and any(h["event"] for h in hk)
        n = cl.get(f"/api/impact?profile_id={p['id']}").json()
        checks["the numbers card counts real rows"] = n["postings"] == 250 and n["applied"] >= 5 and "250" in n["resume_line"]
        lint = cl.get(f"/api/resume?profile_id={p['id']}").json()["lint"]
        checks["the resume checker finds the planted mistakes"] = any("Intern" in i["message"] for i in lint["issues"])
    checks["nothing was written to the real data folder"] = db.DB_PATH.parent == TMP

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
