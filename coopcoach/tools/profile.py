"""Tools about the student and Autopilot: preferences, resume review and fixes, the daily brief, Autopilot settings."""

import threading
import time

from .. import db, events, store
from .calendar import _event_regions
from .common import _job_brief, _new_doc

_STR, _INT, _BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
_AP_KEYS = {"on": _BOOL, "per_day": _INT, "min_score": _INT, "weekly_goal": _INT,
            "paused_until": {"type": "string", "description": "YYYY-MM-DD, or '' to resume"},
            "time": {"type": "string", "description": "HH:MM"}, "instant": _BOOL, "letter": _BOOL, "tailor": _BOOL,
            "followups": _BOOL, "prep": _BOOL, "auto_events": _BOOL,
            "fill_with": {"type": "string", "description": "chrome, claude_code, codex, atlas, manual"},
            "block_company": {"type": "string", "description": "add a company to never prepare"},
            "unblock_company": _STR, "blocklist": {"type": "array", "items": _STR}}


def update_preferences(pid: int, locations: list[str] | None = None, roles: list[str] | None = None,
                       terms: list[str] | None = None, aim: str = "") -> dict:
    from .. import server
    p = store.get_profile(pid)
    before = {k: p["prefs"].get(k) for k in ("locations", "categories", "terms", "aim")}
    change = {}
    if locations is not None:
        change["locations"] = locations
    if roles is not None:
        valid = {c.lower(): c for c in ("Software", "AI/ML/Data", "Hardware", "Quant", "Product/Design", "Business/Finance", "Other")}
        change["categories"] = [valid[r.lower()] for r in roles if r.lower() in valid]
    if terms is not None:
        change["terms"] = terms
    if aim:
        change["aim"] = {"yc": "yc", "startups": "startup", "startup": "startup", "big corps": "bigcorp",
                         "big tech": "bigcorp", "balanced": "balanced"}.get(aim.lower(), "balanced")
    server.update_prefs(pid, change)
    return {"ok": True, "changed": change, "_undo": {"tool": "update_preferences", "args": {
        "locations": before["locations"], "roles": before["categories"], "terms": before["terms"],
        "aim": before["aim"] or "balanced"}}}


def review_resume(pid: int) -> dict:
    from .. import routes_docs
    out = routes_docs.review({"profile_id": pid})
    v = out["review"]
    return {"verdict": v["verdict"], "first_impression": v["first_impression"],
            "top_changes": [c["title"] for c in v["changes"]][:5], "questions": v["questions"][:3],
            "_card": {"type": "review"}}


def daily_brief(pid: int, days: int = 1) -> dict:
    """What changed and what needs doing: new matches, the ready-to-apply queue, inbox updates, quiet
    applications, interviews to prep for, and this week's events."""
    from .. import mail
    since = db.now() - max(1, min(days, 14)) * 86400
    jobs = store.scored_jobs(pid)
    queued = {r["job_id"] for r in db.rows("SELECT job_id FROM ready_queue WHERE profile_id=?", (pid,))}
    new = sorted([j for j in jobs if (j.get("posted_at") or (j["first_seen"] if j.get("fresh") else 0)) >= since
                  and j["eligibility"] != "no" and not j["app_status"] and j["id"] not in queued and not j.get("twin")
                  and j["score"] >= 60],
                 key=lambda j: -j["score"])
    ready = db.rows("SELECT q.job_id, q.why, COALESCE(j.company, '') AS company, COALESCE(j.title, '') AS title FROM"
                    " ready_queue q LEFT JOIN jobs j ON j.id=q.job_id WHERE q.profile_id=? AND q.status IN ('ready',"
                    " 'opened') ORDER BY q.created_at DESC LIMIT 10", (pid,))
    updates = db.rows("SELECT company, role, status, received_at FROM email_events WHERE profile_id=? AND status IS NOT"
                      " NULL AND ignored=0 AND received_at >= ? ORDER BY received_at DESC LIMIT 8", (pid, since))
    quiet = db.rows("SELECT a.job_id, COALESCE(j.company, a.company) AS company, COALESCE(j.title, a.title) AS title,"
                    " COALESCE(a.applied_at, a.updated_at) AS since FROM applications a LEFT JOIN jobs j ON j.id=a.job_id"
                    " WHERE a.profile_id=? AND a.status='applied' AND COALESCE(a.applied_at, a.updated_at) < ?"
                    " ORDER BY since LIMIT 6", (pid, db.now() - 21 * 86400))
    prep = db.rows("SELECT a.job_id, a.status, COALESCE(j.company, a.company) AS company, COALESCE(j.title, a.title) AS"
                   " title FROM applications a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.profile_id=? AND a.status IN"
                   " ('oa', 'interviewing') ORDER BY a.updated_at DESC LIMIT 6", (pid,))
    regions = _event_regions(pid)
    evs = [e for e in events.for_person(pid, 7) if e["mark"] or e.get("region") in regions]
    evs.sort(key=lambda e: (not e["mark"], not e.get("company"), e["start_ts"]))
    day = lambda ts: time.strftime("%a %b %d", time.localtime(ts))  # noqa: E731
    return {
        "new_matches": {"count": len(new), "top": [_job_brief(j) for j in new[:8]]},
        "ready_to_apply": [{k: r[k] for k in ("job_id", "company", "title", "why")} for r in ready],
        "inbox_updates": [{"company": u["company"], "role": u["role"], "status": u["status"], "when": day(u["received_at"])}
                          for u in updates],
        "quiet_3_weeks": [{"job_id": q["job_id"], "company": q["company"], "title": q["title"], "applied": day(q["since"])}
                          for q in quiet],
        "prep_for": [{k: r[k] for k in ("job_id", "company", "title", "status")} for r in prep],
        "events_this_week": [{"event_id": e["id"], "title": e["title"], "when": day(e["start_ts"]), "where": e.get("region"),
                              "kind": e["kind"], "saved": e["mark"], "company": e.get("company") or None} for e in evs[:6]],
        "stats": mail.stats(pid),
        "_card": {"type": "jobs", "jobs": new[:5]},
    }


def my_resume(pid: int) -> dict:
    """Their resume as plain text (contact line left out): for tailoring advice and interview prep."""
    from .. import resume_studio as rs
    from .. import routes_docs
    r = routes_docs._resume_for(pid, None)
    if not r:
        return {"error": "no resume yet: add one in Settings → Profile"}
    r = rs.normalize(r)
    text = rs.plain_text({**r, "header": {}})
    return {"resume": text[:6000]}


def fix_resume(pid: int) -> dict:
    """Apply every safe small fix to their Resume Studio copy (typos in titles, capitals, split lines, stray spaces,
    dashes) and say what still needs them."""
    import copy

    from .. import resume_studio as rs
    from .. import routes_docs
    r = routes_docs.master(pid)
    if not r:
        return {"error": "add a resume first (Settings → Profile)"}
    before = rs.lint(r)
    fixed, n = rs.apply_fixes(copy.deepcopy(r), "auto")
    if not n:
        return {"ok": True, "fixed": 0, "score": before["score"],
                "still_needs_you": [i["message"] for i in before["issues"]][:5]}
    backup = _new_doc(pid, "resume_backup", db.dumps(r), "Resume before the chat's fixes", None, "you")
    routes_docs._save_doc(pid, "resume_master", db.dumps(rs.normalize(fixed)), "Master resume", by="you")
    after = rs.lint(fixed)
    done = [i["message"] for i in before["issues"] if i.get("auto")][:12]
    return {"ok": True, "fixed": n, "score_before": before["score"], "score": after["score"], "changes": done,
            "still_needs_you": [i["message"] for i in after["issues"]][:5], "_card": {"type": "review"},
            "_undo": {"tool": "restore_resume", "args": {"backup_id": backup}}}


def restore_resume(pid: int, backup_id: int) -> dict:
    from .. import routes_docs
    d = db.row("SELECT content FROM documents WHERE profile_id=? AND id=? AND kind='resume_backup'", (pid, backup_id))
    if not d:
        return {"error": "that backup is gone"}
    routes_docs._save_doc(pid, "resume_master", d["content"], "Master resume", by="you")
    return {"ok": True}


def autopilot_settings(pid: int) -> dict:
    from .. import autopilot
    cfg = autopilot.settings(pid)
    return {k: cfg[k] for k in ("on", "time", "days", "per_day", "min_score", "weekly_goal", "categories", "locations",
                                "terms", "mix", "eligibility", "blocklist", "fill_with", "instant", "paused_until")}


def run_autopilot(pid: int) -> dict:
    """Start an Autopilot pass now (prepares the best new matches into Ready to apply)."""
    from .. import autopilot
    if autopilot.state(pid).get("running"):
        return {"ok": True, "already_running": True}
    threading.Thread(target=autopilot.run, args=(pid, "chat"), daemon=True, name=f"autopilot-{pid}").start()
    return {"ok": True, "started": True, "note": "it prepares the best new matches into Ready to apply (Autopilot tab)"}


def change_autopilot(pid: int, **change) -> dict:
    """Change Autopilot: on/off, how many per run, minimum score, weekly goal, pause until a date, time, what it
    prepares, which tool fills forms, companies to skip."""
    from .. import autopilot
    before = autopilot.settings(pid)
    change = {k: v for k, v in change.items() if k in _AP_KEYS}
    bl = list(before.get("blocklist") or [])
    if change.get("block_company"):
        bl = bl + [change.pop("block_company").strip()]
        change["blocklist"] = bl
    if change.get("unblock_company"):
        drop = change.pop("unblock_company").strip().lower()
        change["blocklist"] = [b for b in bl if b.lower() != drop]
    if not change:
        return {"error": "nothing to change"}
    after = autopilot.save_settings(pid, change)
    diff = {k: after[k] for k in change if after.get(k) != before.get(k)}
    return {"ok": True, "changed": diff,
            "_undo": {"tool": "change_autopilot", "args": {k: before.get(k) for k in diff}} if diff else None}
