"""API for Autopilot, the Ready to apply queue, the Activity log, and your Claude Code (engine, sign-in, connect)."""

import threading

from fastapi import APIRouter, Body, HTTPException

from . import agent, ai, auth, autopilot, claude_code, codex_cli, companies, db, store

router = APIRouter()


# --- your Claude Code ---------------------------------------------------------------------------------------

@router.get("/api/claude/status")
def claude_status(profile_id: int, refresh: bool = False):
    s = claude_code.status(refresh=refresh)
    out = {**s, "engine": ai.engine_choice(), "provider": ai.provider(), "label": ai.label(),
           "connections": auth.connections(profile_id)}
    if refresh and s["installed"]:
        from . import claude_setup
        try:
            out["plugin_installed"] = claude_setup.installed()
        except Exception:
            out["plugin_installed"] = None
    return out


@router.put("/api/claude/engine")
def set_engine(body: dict = Body(...)):
    want = body.get("engine")
    if want not in ("auto", "claude_code", "codex"):
        raise HTTPException(400, "engine is 'auto', 'claude_code' or 'codex'")
    if want == "claude_code" and not claude_code.status(refresh=True)["installed"]:
        raise HTTPException(400, "Claude Code isn't installed on this computer")
    if want == "codex" and not codex_cli.status(refresh=True)["installed"]:
        raise HTTPException(400, "ChatGPT's Codex isn't installed on this computer")
    db.meta_set("ai_engine", want)
    return claude_status(body["profile_id"])


@router.post("/api/claude/signin")
def claude_signin(body: dict = Body(...)):
    try:
        claude_code.sign_in_in_terminal()
    except claude_code.Unavailable as e:
        raise HTTPException(400, str(e))
    return {"opened": True}


@router.post("/api/claude/connect")
def claude_connect(body: dict = Body(...)):
    from . import claude_setup
    if not claude_code.binary():
        raise HTTPException(400, "Claude Code isn't installed")
    return claude_setup.connect(body["profile_id"])


@router.get("/api/codex/status")
def codex_status(profile_id: int, refresh: bool = False):
    out = {**codex_cli.status(refresh=refresh), "engine": ai.engine_choice(), "provider": ai.provider()}
    if refresh and out["installed"]:
        from . import claude_setup
        try:
            out["plugin_installed"] = claude_setup.codex_installed()
        except Exception:
            out["plugin_installed"] = None
    return out


@router.post("/api/codex/connect")
def codex_connect(body: dict = Body(...)):
    from . import claude_setup
    if not codex_cli.binary():
        raise HTTPException(400, "ChatGPT's Codex isn't installed")
    return claude_setup.connect_codex(body["profile_id"])


@router.post("/api/codex/disconnect")
def codex_disconnect(body: dict = Body(...)):
    from . import claude_setup
    return claude_setup.disconnect_codex(body["profile_id"])


@router.post("/api/codex/signin")
def codex_signin(body: dict = Body(...)):
    try:
        codex_cli.sign_in_in_terminal()
    except claude_code.Unavailable as e:
        raise HTTPException(400, str(e))
    return {"opened": True}


@router.post("/api/claude/disconnect")
def claude_disconnect(body: dict = Body(...)):
    from . import claude_setup
    return claude_setup.disconnect(body["profile_id"], uninstall=bool(body.get("uninstall")))


# --- Supabase ---------------------------------------------------------------------------------------------

@router.get("/api/impact")
def impact(profile_id: int):
    """Real counts for "Your numbers" (Settings → Account & data) and a resume line built only from them."""
    from . import mail
    st = mail.stats(profile_id)
    one = lambda sql, *a: db.row(sql, a)["n"]  # noqa: E731
    out = {
        "postings": one("SELECT COUNT(*) AS n FROM jobs WHERE active=1"),
        "career_sites": one("SELECT COUNT(*) AS n FROM companies WHERE removed=0"),
        "event_sources": one("SELECT COUNT(*) AS n FROM event_feeds WHERE enabled=1"),
        "prepared": one("SELECT COUNT(*) AS n FROM ready_queue WHERE profile_id=? AND status<>'skipped'", profile_id),
        "letters": one("SELECT COUNT(*) AS n FROM documents WHERE profile_id=? AND kind='cover_letter'", profile_id),
        "emails": one("SELECT COUNT(*) AS n FROM email_events WHERE profile_id=? AND ignored=0", profile_id),
        "ai_actions": one("SELECT COUNT(*) AS n FROM agent_actions WHERE profile_id=? AND writes=1 AND ok=1", profile_id),
        "applied": st["applied_total"], "response_rate": st["response_rate"],
        "interviews": st["interviewing"] + st["offer"], "offers": st["offer"],
    }
    parts = [f"indexes {out['postings']:,} live internship postings from {out['career_sites']:,} company career sites"]
    if out["prepared"]:
        parts.append(f"prepared {out['prepared']} applications")
    if out["applied"]:
        rate = f" ({out['response_rate']}% response rate)" if out["response_rate"] else ""
        parts.append(f"tracked {out['applied']} applications{rate}")
    out["resume_line"] = ("Built Co-op Coach, an AI job-search agent (Python, FastAPI, Postgres/Supabase, MCP) that "
                          + ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + ".")
    return out


@router.get("/api/database")
def database_status(profile_id: int):
    from . import supa
    s = supa.settings()
    out = {"backend": "supabase" if supa.enabled() else "local", "ref": s.get("ref"), "region": s.get("region"),
           "moved_at": s.get("moved_at"), "state": supa.state()}
    if supa.enabled():
        try:
            out["counts"] = {t: db.row(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("jobs", "profiles", "applications",
                                                                                        "documents", "events")}
            out["ok"] = True
        except Exception as e:
            out["ok"], out["error"] = False, str(e)[:200]
    return out


# --- Autopilot ----------------------------------------------------------------------------------------------

def _queue(pid: int) -> list[dict]:
    rows = db.rows("SELECT job_id, status, why, source, created_at, updated_at FROM ready_queue WHERE profile_id=?"
                   " AND (status IN ('ready', 'opened') OR updated_at > ?) ORDER BY status IN ('ready', 'opened') DESC,"
                   " created_at DESC LIMIT 40", (pid, db.now() - 7 * 86400))
    by = {}
    for j in store.scored_jobs(pid):
        for i in j["alt_ids"]:
            by[i] = j
    out = []
    for r in rows:
        j = by.get(r["job_id"])
        if not j:
            continue
        out.append({**r, "company": j["company"], "title": j["title"], "score": j["score"], "url": j["url"],
                    "where": (j["locations"] or [""])[0], "eligibility": j["eligibility"], "app_status": j["app_status"]})
    return out


def _drafts(pid: int) -> list[dict]:
    return db.rows("SELECT id, kind, job_id, title, content, generated_by, updated_at FROM documents WHERE profile_id=? AND"
                   " kind IN ('followup', 'thankyou', 'prep', 'research', 'message') ORDER BY updated_at DESC LIMIT 20", (pid,))


@router.get("/api/autopilot")
def get_autopilot(profile_id: int):
    from . import events
    cfg = autopilot.settings(profile_id)
    last = autopilot.last_run(profile_id)
    soon = [e for e in events.for_person(profile_id, 7) if e["mark"]][:6]
    return {"settings": cfg, "state": autopilot.state(profile_id), "next_run": autopilot.next_run(cfg),
            "paused": autopilot.paused(cfg), "week": autopilot.week_progress(profile_id, cfg),
            "last_run": {k: last[k] for k in ("started_at", "finished_at", "trigger", "engine", "queued", "brief", "error")}
            if last else None,
            "history": autopilot.history(profile_id), "queue": _queue(profile_id), "drafts": _drafts(profile_id),
            "events": [{k: e.get(k) for k in ("id", "title", "start_ts", "all_day", "region", "url", "mark", "company")}
                       for e in soon],
            "activity": agent.activity(profile_id, 40), "skip_reasons": autopilot.SKIP_REASONS,
            "tiers": companies.TIERS}


@router.get("/api/autopilot/preview")
def autopilot_preview(profile_id: int):
    return autopilot.preview(profile_id)


@router.get("/api/drafts/{doc_id}")
def get_draft(doc_id: int, profile_id: int):
    d = db.row("SELECT id, kind, job_id, title, content, generated_by, updated_at FROM documents WHERE profile_id=? AND"
               " id=? AND kind IN ('followup', 'thankyou', 'prep', 'research', 'message')", (profile_id, doc_id))
    if not d:
        raise HTTPException(404)
    return d


@router.delete("/api/drafts/{doc_id}")
def delete_draft(doc_id: int, profile_id: int):
    return agent.discard_draft(profile_id, doc_id)


@router.put("/api/autopilot")
def put_autopilot(body: dict = Body(...)):
    return {"settings": autopilot.save_settings(body["profile_id"], {k: v for k, v in body.items() if k != "profile_id"})}


@router.post("/api/autopilot/run")
def run_autopilot(body: dict = Body(...)):
    pid = body["profile_id"]
    if autopilot.state(pid).get("running"):
        return {"started": False, "running": True}
    threading.Thread(target=autopilot.run, args=(pid, "manual"), daemon=True, name=f"autopilot-{pid}").start()
    return {"started": True}


@router.post("/api/queue/{job_id}/status")
def queue_status(job_id: str, body: dict = Body(...)):
    pid, status = body["profile_id"], body.get("status")
    if status == "applied":
        out = agent.call(pid, "track", {"job_id": job_id, "status": "applied"}, source="app")
        if out.get("error"):
            raise HTTPException(400, out["error"])
    elif status == "skipped":
        autopilot.skip(pid, job_id, body.get("reason") or "")
    elif status == "ready":
        with db.tx() as c:
            c.execute("UPDATE ready_queue SET status='ready', skip_reason=NULL, note=NULL, updated_at=? WHERE"
                      " profile_id=? AND job_id=?", (db.now(), pid, job_id))
    else:
        raise HTTPException(400, "status is applied, skipped or ready")
    return {"ok": True}


# --- Activity -----------------------------------------------------------------------------------------------

@router.get("/api/activity")
def get_activity(profile_id: int, all: bool = False):
    return {"activity": agent.activity(profile_id, 80, writes_only=not all)}


@router.post("/api/activity/{aid}/undo")
def undo(aid: int, body: dict = Body(...)):
    out = agent.undo_action(body["profile_id"], aid)
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return {"ok": True}


# --- apply with your Claude Code (Terminal, you watching) ---------------------------------------------------

APPLY_RULES = """You are helping a student apply to an internship through Co-op Coach (tools: mcp__coopcoach__*).
Hard rules: never press the final Submit / Send application button: the student does. Never solve CAPTCHAs, sign in,
create accounts or type passwords: if a sign-in wall appears, stop and ask the student. Leave voluntary demographic
questions (gender, race/ethnicity, veteran, disability, orientation) blank. Use only Co-op Coach's answers or what the
student tells you; ask instead of guessing. When the form is filled, say what's left and let them review and submit."""


@router.post("/api/apply/{job_id}/open")
def apply_open(job_id: str, body: dict = Body(...)):
    """Open a prepared application with the tool they chose: our Chrome filler, Claude Code, ChatGPT, Atlas or by hand."""
    from . import apply, workday
    pid = body["profile_id"]
    via = body.get("via") or autopilot.settings(pid)["fill_with"]
    j = agent._find_job(pid, job_id)
    if not j:
        raise HTTPException(404, "no such posting")
    kit = agent.cached_kit(pid, j["id"]) or apply.build_kit(pid, j["id"])
    if kit.get("error"):
        raise HTTPException(404, kit["error"])
    agent._mark_opened(pid, j["id"])
    if via == "chrome":
        if not workday.available():
            raise HTTPException(400, "Filling forms needs the playwright package")
        apply.fill(pid, j["id"])
        return {"via": via, "message": "Opening the form in Chrome and filling it. Check it, then press Submit."}
    if via == "claude_code":
        return {"via": via, **apply_with_claude(j["id"], {"profile_id": pid}),
                "message": "Claude Code opened in Terminal. Watch it fill the form, then press Submit."}
    if via in ("codex", "atlas"):
        try:
            from . import codex_cli
        except ImportError:
            raise HTTPException(400, "ChatGPT isn't set up yet")
        out = codex_cli.open_in_terminal(pid, j["id"]) if via == "codex" else codex_cli.open_in_atlas(pid, j["id"], kit)
        return {"via": via, **out}
    return {"via": "manual", "url": kit["job"]["apply_url"], "message": "Opened the application. Your kit has every answer."}


@router.post("/api/apply/{job_id}/claude")
def apply_with_claude(job_id: str, body: dict = Body(...)):
    pid = body["profile_id"]
    j = agent._find_job(pid, job_id)
    if not j:
        raise HTTPException(404, "no such posting")
    prompt = (f"Apply to {j['company']}: {j['title']} (Co-op Coach job_id {j['id']}).\n"
              f"1. Call application_files with job_id {j['id']} to get every answer, my details and my tailored resume "
              f"and cover letter as local files.\n"
              f"2. If you have Claude in Chrome: open the apply_url, fill every field, upload the resume (and the letter "
              f"if a cover letter is asked for), and move through the pages without pressing the final Submit. "
              f"Otherwise call fill_application to open and fill it in Chrome for me.\n"
              f"3. Tell me what's left (needs_you answers, anything you couldn't match) so I can review and submit.\n"
              f"4. When I say I've submitted, call track with status applied.")
    try:
        out = claude_code.open_in_terminal(pid, prompt, APPLY_RULES, chrome=bool(body.get("chrome", True)),
                                           label="Terminal apply")
    except claude_code.Unavailable as e:
        raise HTTPException(400, str(e))
    agent._mark_opened(pid, j["id"])
    return out
