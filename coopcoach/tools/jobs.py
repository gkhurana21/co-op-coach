"""Tools about postings: search, details, stars, letters, application kits, Ready to apply, filling forms."""

import re
import time

from .. import db, store
from .common import _app_row, _find_job, _job_brief


def search_jobs(pid: int, query: str = "", location: str = "", role: str = "", company_type: str = "",
                posted_within_days: int = 0, include_ineligible: bool = False, track: str = "", limit: int = 6, offset: int = 0) -> dict:
    tiers = {"yc": ["yc"], "startup": ["startup", "yc"], "big tech": ["bigtech"], "quant": ["quant"],
             "bank": ["finance"], "corporate": ["finance"]}.get(company_type.lower().strip(), [])
    role_map = {"software": "Software", "ai": "AI/ML/Data", "ml": "AI/ML/Data", "data": "AI/ML/Data",
                "hardware": "Hardware", "quant": "Quant", "product": "Product/Design", "design": "Product/Design",
                "business": "Business/Finance", "finance": "Business/Finance"}
    categories = [v for k, v in role_map.items() if k in role.lower()][:1]
    tid = ""
    if track:
        from .explore import _track
        found = _track(pid, track)
        if not found:
            return {"error": f"no track called {track}: list_tracks shows them"}
        tid, tiers, categories = found["id"], [], []
    res = store.list_jobs(pid, q=query, tiers=tiers, categories=categories, since_days=posted_within_days,
                          show_ineligible=include_ineligible, track=tid, limit=5000)
    jobs = res["jobs"]
    if location:  # match the place in locations, not just anywhere in the text
        loc = location.lower()
        jobs = [j for j in jobs if loc in " ".join(j["locations"]).lower() or (loc == "remote" and "Remote" in j["regions"])]
    start = max(0, offset)
    picked = jobs[start:start + max(1, min(limit, 25))]
    out = {"total": len(jobs), "jobs": [_job_brief(j) for j in picked], "_card": {"type": "jobs", "jobs": picked}}
    if start + len(picked) < len(jobs):
        out["next_offset"] = start + len(picked)
    return out


def job_details(pid: int, job_id: str) -> dict:
    j = _find_job(pid, job_id)
    if not j:
        return {"error": "no such posting"}
    from .. import jobdetail
    desc = jobdetail.ensure_description(j["id"])
    return {"job_id": j["id"], "company": j["company"], "title": j["title"], "url": j["url"], "score": j["score"],
            "where": j["locations"][:4], "terms": j["terms"], "eligibility": j.get("eligibility"),
            "who_can_apply": [r["text"] for r in j.get("requirements") or []][:5], "notes": j.get("notes", []),
            "reasons": [r["t"] for r in j["reasons"]][:6], "better_version": (j.get("twin") or {}).get("title"),
            "status": j.get("app_status"), "ai_why": j.get("why"), "ai_tip": j.get("tip"),
            "description_excerpt": (desc or "")[:900], "_card": {"type": "jobs", "jobs": [j]}}


def star(pid: int, job_id: str = "", company: str = "", on: bool = True) -> dict:
    from .. import server
    if job_id:
        j = _find_job(pid, job_id)
        if not j:
            return {"error": "no such posting"}
        kind, key, label = "job", j["id"], f"{j['company']} · {j['title']}"
    elif company:
        j = next((x for x in store.collapsed_jobs() if company.lower() in x["company"].lower()), None)
        kind, key, label = "company", (j["company_key"] if j else company.lower()), (j["company"] if j else company)
    else:
        return {"error": "say which job or company"}
    if on:
        server.add_favourite({"profile_id": pid, "kind": kind, "key": key, "label": label})
    else:
        server.remove_favourite(pid, kind, key)
    return {"ok": True, "starred" if on else "unstarred": label,
            "_undo": {"tool": "star", "args": {("job_id" if kind == "job" else "company"): key if kind == "job" else label,
                                               "on": not on}}}


def write_cover_letter(pid: int, job_id: str, style: str = "") -> dict:
    from .. import routes_docs
    j = _find_job(pid, job_id)
    if not j:
        return {"error": "no such posting"}
    fmt = {"startup": "startup", "corporate": "corporate", "bank": "corporate", "story": "story", "email": "email",
           "short": "email", "classic": "classic"}.get((style or "").lower().strip())
    letter = routes_docs.generate_letter(pid, j["id"], fmt)
    blanks = re.findall(r"\[[^\]]{2,60}\]", letter["content"])
    return {"ok": True, "letter_id": letter.get("id"), "by": letter.get("generated_by"),
            "preview": letter["content"][:350], "blanks_to_fill": blanks[:6],
            "_card": {"type": "letter", "job_id": j["id"], "title": f"{j['company']} · {j['title']}"}}


def application_kit(pid: int, job_id: str) -> dict:
    from .. import apply
    kit = apply.build_kit(pid, job_id)
    if kit.get("error"):
        return kit
    return {"ok": True, "job": kit["job"]["title"], "company": kit["job"]["company"],
            "questions_answered": sum(1 for q in kit["questions"] if q.get("answer")),
            "needs_you": [q["label"] for q in kit["questions"] if q.get("needs_you")][:6],
            "_card": {"type": "kit", "job_id": kit["job"]["id"], "title": f"{kit['job']['company']} · {kit['job']['title']}"}}


def queue_application(pid: int, job_id: str, why: str = "", remove: bool = False) -> dict:
    """Prepare an application now (form answers, tailored resume, cover letter) and put it in Ready to apply."""
    from .. import apply
    j = _find_job(pid, job_id)
    if not j:
        return {"error": "no such posting"}
    if remove:
        with db.tx() as c:
            c.execute("DELETE FROM ready_queue WHERE profile_id=? AND job_id=? AND status IN ('ready', 'opened', 'skipped')",
                      (pid, j["id"]))
        return {"ok": True, "removed": f"{j['company']} · {j['title']}"}
    if j["eligibility"] == "no":
        why = [r["text"] for r in j.get("requirements") or []] or [r["t"] for r in j["reasons"] if r.get("k") == "bad"]
        return {"error": "this posting says you can't apply: " + "; ".join(why)[:200]}
    if j.get("app_status") in ("applied", "oa", "interviewing", "offer", "rejected"):
        return {"error": f"already {j['app_status']}"}
    have = db.row("SELECT status FROM ready_queue WHERE profile_id=? AND job_id=?", (pid, j["id"]))
    if have and have["status"] in ("ready", "opened"):
        return {"ok": True, "already_queued": True, "job": f"{j['company']} · {j['title']}"}
    from ..agent import _ctx  # who is asking (chat, Autopilot, MCP): agent sets it around each call
    ctx = _ctx.get()
    want = {}
    if ctx.get("source") == "autopilot":  # Autopilot's "what to prepare" settings: a letter or not, its style, drafts
        from .. import autopilot
        cfg = autopilot.settings(pid)
        want = {"letter": cfg["letter"], "letter_fmt": cfg["letter_style"] or None, "ai_answers": cfg["ai_answers"]}
    from .. import agent, polish
    later = not agent.SHORT_LIVED  # (a helper process that ends with the chat can't finish anything afterwards)
    kit = apply.build_kit(pid, j["id"], **want, quick=later)
    if kit.get("error"):
        return kit
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO ready_queue (profile_id, job_id, status, why, kit, source, run_id, created_at,"
                  " updated_at, polish, polish_tries) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                  (pid, j["id"], "ready", why.strip()[:400], db.dumps(kit), ctx.get("source", "app"), ctx.get("run_id"),
                   db.now(), db.now(), "pending" if later else "done"))
    if later:
        polish.enqueue(pid, j["id"])
    return {"ok": True, "job": f"{j['company']} · {j['title']}", "score": j["score"],
            **({"polishing": "Ready now; the AI cover letter and written answers finish in the background."} if later else {}),
            "questions_answered": sum(1 for q in kit["questions"] if q.get("answer")),
            "needs_you": [q["label"] for q in kit["questions"] if q.get("needs_you")][:6],
            "_card": {"type": "kit", "job_id": j["id"], "title": f"{j['company']} · {j['title']}"},
            "_undo": {"tool": "queue_application", "args": {"job_id": j["id"], "remove": True}}}


def ready_queue(pid: int, status: str = "ready") -> dict:
    want = [x.strip() for x in (status or "ready").split(",") if x.strip()]
    if "ready" in want:
        want.append("opened")
    rows = db.rows("SELECT q.job_id, q.status, q.why, q.source, q.created_at, COALESCE(j.company, '') AS company,"
                   " COALESCE(j.title, '') AS title FROM ready_queue q LEFT JOIN jobs j ON j.id=q.job_id WHERE"
                   f" q.profile_id=? AND q.status IN ({','.join('?' * len(want))}) ORDER BY q.created_at DESC LIMIT 25",
                   (pid, *want))
    return {"count": len(rows), "applications": [{**{k: r[k] for k in ("job_id", "company", "title", "status", "why")},
                                                  "prepared": time.strftime("%b %d", time.localtime(r["created_at"]))}
                                                 for r in rows]}


def cached_kit(pid: int, job_id: str, max_age_days: int = 3) -> dict | None:
    r = db.row("SELECT kit, updated_at FROM ready_queue WHERE profile_id=? AND job_id=?", (pid, job_id))
    if r and r["kit"] and r["updated_at"] > db.now() - max_age_days * 86400:
        return db.loads(r["kit"])
    return None


def _mark_opened(pid: int, job_id: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE ready_queue SET status='opened', updated_at=? WHERE profile_id=? AND job_id=? AND status='ready'",
                  (db.now(), pid, job_id))


def application_files(pid: int, job_id: str) -> dict:
    """Everything a browser agent needs to fill the form itself: every answer, the person's contact details and
    the tailored resume + cover letter as files on this computer (for upload fields)."""
    from .. import apply
    j = _find_job(pid, job_id)
    if not j:
        return {"error": "no such posting"}
    kit = cached_kit(pid, j["id"]) or apply.build_kit(pid, j["id"])
    if kit.get("error"):
        return kit
    files = apply._write_files(pid, kit)
    _mark_opened(pid, j["id"])
    return {"company": j["company"], "title": j["title"], "apply_url": kit["job"]["apply_url"], "form": kit["job"]["ats"],
            "eligibility": kit["job"]["eligibility"], "who_can_apply": kit["job"]["who_can_apply"],
            "you": {k: v for k, v in kit["basics"].items() if v},
            "answers": [{"question": q["label"], "answer": q["answer"], "required": q.get("required", False),
                         "needs_you": q.get("needs_you", False), "note": q.get("note", ""),
                         "options": q.get("options", [])[:8]} for q in kit["questions"]],
            "files": files,
            "rules": "Fill only. Never press Submit, never solve CAPTCHAs, never sign in or create accounts for them, "
                     "leave voluntary demographic questions blank, and ask the student about anything marked "
                     "needs_you or not covered here instead of guessing."}


def fill_application(pid: int, job_id: str) -> dict:
    """Open the application in a Chrome window on their screen and fill in what's known. Never submits."""
    from .. import apply, workday
    j = _find_job(pid, job_id)
    if not j:
        return {"error": "no such posting"}
    if not workday.available():
        return {"error": "form filling needs playwright installed"}
    apply.fill(pid, j["id"])
    _mark_opened(pid, j["id"])
    end = time.time() + 45
    st = apply.fill_state(pid)
    while st.get("state") == "running" and time.time() < end:
        time.sleep(1.5)
        st = apply.fill_state(pid)
    return {"ok": st.get("state") != "error", "state": st.get("state"), "message": st.get("message"),
            "filled": st.get("filled", [])[:30], "job": f"{j['company']} · {j['title']}"}


def preview_picks(pid: int, n: int = 0) -> dict:
    """What Autopilot would prepare right now (and why), without preparing anything."""
    from .. import autopilot
    return autopilot.preview(pid, n or None)


def apply_session_next(pid: int) -> dict:
    """The next prepared application to submit (best first), and how they like to fill forms."""
    from .. import autopilot
    rows = ready_queue(pid)["applications"]
    if not rows:
        return {"done": True, "message": "Nothing waiting in Ready to apply."}
    scores = {j["id"]: j["score"] for j in store.scored_jobs(pid)}
    rows.sort(key=lambda r: -scores.get(r["job_id"], 0))
    nxt = rows[0]
    kit = cached_kit(pid, nxt["job_id"]) or {}
    return {"next": {**nxt, "score": scores.get(nxt["job_id"]), "apply_url": (kit.get("job") or {}).get("apply_url")},
            "left": len(rows), "fill_with": autopilot.settings(pid)["fill_with"]}


def not_for_me(pid: int, job_id: str, reason: str = "") -> dict:
    """Hide a posting from Autopilot's picks and learn why: location, company (never that company), role, senior,
    term."""
    from .. import autopilot
    j = _find_job(pid, job_id)
    if not j:
        return {"error": "no such posting"}
    prev = db.row("SELECT status, skip_reason FROM ready_queue WHERE profile_id=? AND job_id=?", (pid, j["id"]))
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO ready_queue (profile_id, job_id, status, source, created_at, updated_at)"
                  " VALUES (?,?,?,?,?,?)", (pid, j["id"], "skipped", "chat", db.now(), db.now()))
    out = autopilot.skip(pid, j["id"], reason)
    return {"ok": True, "job": f"{j['company']} · {j['title']}", "reason": out.get("reason"),
            "_undo": {"tool": "queue_application", "args": {"job_id": j["id"], "remove": True}} if not prev else None}


def refresh_jobs(pid: int) -> dict:
    """Check every job source for new postings now (takes a few minutes; new matches show up as it goes)."""
    from .. import server, sync
    if sync.state.get("running"):
        return {"ok": True, "already_running": True, "progress": sync.state.get("progress")}
    return {"ok": True, **server.trigger_sync()}


def _known_link(pid: int, url: str) -> bool:
    like = "%" + url.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return bool(db.row("SELECT 1 AS k FROM jobs WHERE url=?", (url,))
                or db.row("SELECT 1 AS k FROM applications WHERE profile_id=? AND (url=? OR notes LIKE ? ESCAPE '\\')",
                          (pid, url, like))
                or db.row("SELECT 1 AS k FROM events WHERE url=?", (url,)))


def open_link(pid: int, url: str = "", job_id: str = "") -> dict:
    """Open a posting, application form, booking or assessment link in their browser on this Mac."""
    import subprocess
    import sys
    if job_id:
        j = _find_job(pid, job_id)
        a = _app_row(pid, job_id)
        url = (j or {}).get("apply_url") or (j or {}).get("url") or (a or {}).get("url") or ""
    if not re.match(r"https?://[^\s]+$", url or ""):
        return {"error": "need an http(s) link or a job_id with one"}
    if not job_id and not _known_link(pid, url):  # a link planted in a web page or posting can't be opened this way
        return {"error": "I only open links Co-op Coach already has (a posting, an application's booking or "
                         "assessment link, an event). Paste the link here if you want it tracked."}
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False, timeout=15)
    else:
        import webbrowser
        webbrowser.open(url)
    return {"ok": True, "opened": url}
