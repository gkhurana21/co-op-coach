import atexit
import gc
import json
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import (
    Body,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import (
    ai,
    auth,
    autopilot,
    central,
    claude_code,
    codex_cli,
    companies,
    config,
    db,
    discover,
    events,
    features,
    guard,
    mail,
    matching,
    notify,
    polish,
    resume,
    routes_auto,
    routes_contacts,
    routes_docs,
    routes_email,
    routes_events,
    routes_extension,
    routes_match,
    routes_tracks,
    store,
    sync,
    workday,
)
from .config import PKG, UPLOAD_DIR
from .routes_email import (  # noqa: F401 (other modules call these)
    add_imap,
    delete_email_account,
    email_accounts,
    email_events,
    email_provider,
    email_sync,
    fix_email_event,
    outlook_start,
    outlook_status,
    set_ms_client_id,
    toggle_email_account,
)
from .routes_events import (  # noqa: F401 (other modules call these)
    add_feed,
    application_dates,
    calendar_feed,
    delete_feed,
    event_ics,
    get_events,
    hackathon_season,
    make_calendar_feed,
    mark_event,
    refresh_events,
    update_feed,
    update_feed_group,
)
from .sources import BlockedURL, ats, indeed
from .textutil import CATEGORIES, detect_skills

log = logging.getLogger("coopcoach")
scheduler = BackgroundScheduler(daemon=True)


def _sync_in_background():
    threading.Thread(target=sync.run, name="sync", daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    db.start_refreshing()
    if db.is_postgres():  # many round trips over the internet: the page shouldn't wait for them
        threading.Thread(target=lambda: (_seed(), _upkeep()), daemon=True, name="upkeep").start()
    else:
        _seed()
        threading.Thread(target=_upkeep, daemon=True, name="upkeep").start()
    threading.Thread(target=reparse_outdated, daemon=True).start()
    _note_address()
    threading.Thread(target=_warm_caches, daemon=True, name="warm").start()  # first page load shouldn't wait
    if not os.environ.get("COOPCOACH_DEMO"):
        threading.Thread(target=_refresh_plugin, daemon=True, name="plugin-refresh").start()
    threading.Thread(target=polish.resume, daemon=True, name="polish-resume").start()  # whatever a restart interrupted
    if os.environ.get("COOPCOACH_DEMO"):  # the demo is a snapshot: no syncs, inbox checks or Autopilot runs
        yield
        return
    hours = float(config.get("sync.interval_hours", 3))
    scheduler.add_job(sync.run, "interval", hours=hours, id="sync", max_instances=1, coalesce=True)
    # new Greenhouse, Ashby and Lever boards from the public web, about once a month (in the background)
    # (on the shared feed, its crawler does this for everyone)
    scheduler.add_job(lambda: not central.enabled() and discover.due() and discover.start(), "interval", hours=12,
                      id="discover", max_instances=1, coalesce=True)
    quick = float(config.get("sync.quick_minutes", 20))
    if central.enabled():  # on the shared feed, a check is a quick pull of what changed: look more often
        quick = min(quick or 15, float(config.get("sync.feed_minutes", 15)))
    if quick > 0:  # the boards that matter most (followed niches, starred and applied-to companies) between syncs
        scheduler.add_job(sync.run_quick, "interval", minutes=quick, id="quick", max_instances=1, coalesce=True)
    scheduler.add_job(autopilot.tick, "interval", minutes=5, id="autopilot", max_instances=1, coalesce=True)
    scheduler.start()
    last = sync.last_run(full_only=True)
    if config.get("sync.run_on_startup", True) and (not last or time.time() - last["finished_at"] > hours * 3600):
        _sync_in_background()
    yield
    scheduler.shutdown(wait=False)


def _note_address() -> None:
    """Say where this app is, so the Claude Code plugin's server can ask it (only a port on this computer)."""
    port = os.environ.get("COOPCOACH_PORT")
    if not port:
        return
    f = config.DATA_DIR / "app.json"
    f.write_text(json.dumps({"port": int(port), "pid": os.getpid()}), encoding="utf-8")
    atexit.register(lambda: f.unlink(missing_ok=True))


def _refresh_plugin() -> None:
    try:
        from . import claude_setup
        claude_setup.refresh_if_stale()
    except Exception as e:  # never in the way of the app
        log.info("couldn't refresh the Claude Code plugin: %s", e)


def _seed() -> None:
    mail.migrate_secrets()
    store.seed_companies()
    store.seed_event_feeds()


def _upkeep() -> None:
    """After an update the app re-reads every posting's text (about a minute on 20,000 of them): behind the page, never in
    front of it. What it changes shows when it's done."""
    try:
        before = (db.meta_get("flags_version"), db.meta_get("skills_version"), db.meta_get("requirements_version"))
        store.refresh_flags()
        store.refresh_skills()
        store.backfill_req_ids()
        store.refresh_requirements()
        if before != (db.meta_get("flags_version"), db.meta_get("skills_version"), db.meta_get("requirements_version")):
            store.invalidate_cache()
    except Exception as e:
        log.warning("upkeep: %s", e)


def _warm_caches() -> None:
    """Load the postings and score them for each person in the background at startup (from Supabase this takes a few
    seconds), so the first page doesn't wait for it."""
    try:
        store.collapsed_jobs()
        from . import market, path, tracks
        for p in store.list_profiles():
            store.scored_jobs(p["id"])
            path.summary(p["id"])  # (what's read from the database stays in memory: the first click is instant)
            tracks.browse(p["id"])
            for tid in tracks.pins(p["id"]):
                tracks.header(p["id"], tid)
                market.track_market(p["id"], tid)
        # what's loaded stays loaded: garbage collection needn't walk 20,000 postings and pause a page while it does (74 ms
        # measured down to 1 ms here, and worse on older Pythons)
        gc.collect()
        gc.freeze()
    except Exception as e:  # just a head start: the first request does it otherwise
        log.warning("warming caches failed: %s", e)


app = FastAPI(title="Co-op Coach", lifespan=lifespan)


def _malformed(request, exc):
    """A request the app's own pages never send (a field missing, or text where a list goes), or a bug. Either way, a
    sentence instead of "Internal Server Error", and the details in the log."""
    log.error("%s %s failed: %r", request.method, request.url.path, exc, exc_info=exc)
    if request.method in ("POST", "PUT", "PATCH"):
        what = f"Missing: {exc.args[0]}" if isinstance(exc, KeyError) and exc.args else "That wasn't in the form expected"
        return JSONResponse({"detail": what}, status_code=400)
    return JSONResponse({"detail": "Something went wrong on our side: try again, and if it keeps happening see the log."},
                        status_code=500)


for _bad in (KeyError, TypeError, AttributeError):
    app.add_exception_handler(_bad, _malformed)


def _offline(_request, exc):
    """The database is in the cloud and the internet dropped: say that, instead of a server error."""
    log.warning("database unreachable: %s", str(exc)[:160])
    return JSONResponse({"detail": "Can't reach your database. Check your internet: this page keeps trying.",
                         "offline": True}, status_code=503)


def _wrong_value(_request, exc):
    """Postgres is strict where SQLite shrugs: a number where text belongs, or one too big for its column."""
    log.warning("a request's value didn't fit: %s", str(exc)[:160])
    return JSONResponse({"detail": "Something in that request wasn't the right kind of value."}, status_code=400)


try:
    import psycopg
    import psycopg_pool
    app.add_exception_handler(psycopg.OperationalError, _offline)
    app.add_exception_handler(psycopg_pool.PoolTimeout, _offline)
    app.add_exception_handler(psycopg.DataError, _wrong_value)
    app.add_exception_handler(psycopg.errors.UndefinedFunction, _wrong_value)
except ImportError:  # SQLite only
    pass
app.include_router(routes_docs.router)
app.include_router(routes_auto.router)
app.include_router(routes_email.router)
app.include_router(routes_events.router)
app.include_router(routes_match.router)
app.include_router(routes_tracks.router)
app.include_router(routes_contacts.router)
app.include_router(routes_extension.router)
app.add_middleware(guard.ProfileLock)
app.add_middleware(guard.SameOrigin)  # changes sent by other websites are refused before anything reads them
# Only answer to this machine's own names. Stops malicious web pages from reaching the app through DNS
# rebinding. `serve --host 0.0.0.0` (sharing on your Wi-Fi) relaxes this on purpose.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"] if os.environ.get("COOPCOACH_ANY_HOST") else
                   config.get("security.allowed_hosts", ["localhost", "127.0.0.1", "[::1]", "::1"]))
app.mount("/static", StaticFiles(directory=PKG / "static"), name="static")


@app.get("/")
def index():
    """index.html with ?v=<file hash> on the scripts and stylesheet, so an updated app never runs stale code."""
    import hashlib
    page = (PKG / "static" / "index.html").read_text(encoding="utf-8")
    for name in sorted(set(re.findall(r'/static/([\w.-]+\.(?:js|css))"', page))):  # every file the page loads
        f = PKG / "static" / name
        if f.is_file():
            v = hashlib.sha1(f.read_bytes()).hexdigest()[:10]
            page = page.replace(f'/static/{name}"', f'/static/{name}?v={v}"')
    return Response(page, media_type="text/html", headers={"Cache-Control": "no-cache"})


def _profile_or_404(pid: int) -> dict:
    p = store.get_profile(pid)
    if not p:
        raise HTTPException(404, "profile not found")
    return p


# --- app state ---------------------------------------------------------------------

def _visible_profile(p: dict, token: str | None) -> dict:
    if p.get("pass_hash") and not auth.is_unlocked(token, p["id"], touch=False):
        return {"id": p["id"], "name": p["name"], "locked": True, "has_passcode": True}
    out = _public_profile(p)
    out["locked"], out["has_passcode"] = False, bool(p.get("pass_hash"))
    out["ready_count"] = db.row("SELECT COUNT(*) AS n FROM ready_queue WHERE profile_id=? AND status IN ('ready', 'opened')",
                                (p["id"],))["n"]
    return out


@app.put("/api/features")
def put_features(body: dict = Body(...)):
    """Lean (what a new person sees) or everything: see features.py."""
    try:
        return {"mode": features.set_mode(str(body.get("mode") or "")), "hidden": sorted(features.hidden())}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/state")
def get_state(request: Request):
    token = request.cookies.get(auth.COOKIE)
    last, latest = sync.last_run(full_only=True), sync.last_run()  # latest: a quick check counts as "synced"
    job = scheduler.get_job("sync")
    baseline = int(db.meta_get("baseline_ts", 0) or 0)
    day_ago = db.now() - 86400
    counts = _job_counts(day_ago, baseline)
    return {
        "profiles": [_visible_profile(p, token) for p in store.list_profiles()],
        "sync": {
            "running": sync.state["running"],
            "progress": sync.state["progress"],
            "finishing": sync.state["finishing"],  # done, with AI scoring and alerts still going in the background
            "last_finished": latest["finished_at"] if latest else None,
            "last_report": last["report"] if last else None,
            "next_run": job.next_run_time.timestamp() if job and job.next_run_time else None,
        },
        "counts": {"active": counts["active"] or 0, "new_24h": counts["new24"] or 0,
                   "events": events.count_upcoming(60)},
        "features": {"mode": features.mode(), "hidden": sorted(features.hidden())},
        # never waits on the `claude` / `codex` commands: their last check (Settings refreshes it when it matters)
        "ai_enabled": ai.available(wait=False),
        "ai_label": ai.label(wait=False),
        "ai_provider": ai.provider(wait=False),
        "ai_engine": ai.engine_choice(),
        "demo": bool(os.environ.get("COOPCOACH_DEMO")),
        "claude_code": {k: v for k, v in claude_code.status_cached().items() if k in ("installed", "logged_in", "version")},
        "codex": {k: v for k, v in codex_cli.status_cached().items() if k in ("installed", "logged_in", "version")},
        "outlook_configured": bool(mail.ms_client_id()),
        "extension_dir": str(config.ROOT / "extension"),
        "app_dir": str(config.ROOT),
        "auto_lock_minutes": config.get("security.auto_lock_minutes", 30),
        "work_auth": matching.WORK_AUTH,
        "email_use_ai": bool(config.get("email.use_ai", False)),
        "indeed_enabled": indeed.available(),
        "categories": CATEGORIES,
        "tiers": companies.TIERS,
        "statuses": store.APP_STATUSES,
        "terms": _term_options(),
    }


_counts_cache: dict = {"at": 0, "v": None, "sync": None}


def _job_counts(day_ago: int, baseline: int) -> dict:
    """Postings counts change only with a sync: reuse them for a few minutes (the status is polled)."""
    last = (sync.last_run() or {}).get("finished_at")
    if _counts_cache["v"] is None or _counts_cache["sync"] != last or time.time() - _counts_cache["at"] > 300:
        _counts_cache["v"] = db.row(
            "SELECT COUNT(*) AS active, SUM(CASE WHEN posted_at >= ? OR (posted_at IS NULL AND first_seen >= ?"
            " AND first_seen > ?) THEN 1 ELSE 0 END) AS new24 FROM jobs WHERE active=1", (day_ago, day_ago, baseline))
        _counts_cache.update(at=time.time(), sync=last)
    return _counts_cache["v"]


_terms_cache: list = [None, []]


def _term_options() -> list[str]:
    if not store.jobs_ready():  # the page opens now; the terms come with the next state once postings are in
        return []
    stamp = (store._cache["version"], time.strftime("%Y-%m-%d"))  # asked every minute; only a sync or a new day changes it
    if _terms_cache[0] == stamp:
        return _terms_cache[1]
    seen: dict[str, int] = {}
    for j in store.collapsed_jobs():
        for t in j["terms"]:
            if re.fullmatch(r"(Winter|Spring|Summer|Fall) 20\d\d", t):
                seen[t] = seen.get(t, 0) + 1
    order = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}
    now = time.time()
    out = sorted([t for t, n in seen.items() if n >= 3 and not matching._term_started(t, now)],
                 key=lambda t: (int(t[-4:]), order[t.split()[0]]))
    _terms_cache[:] = [stamp, out]
    return out


def _public_profile(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k not in ("resume_text", "pass_hash", "pass_salt")}
    if out.get("assessment"):  # links added later in Resume Studio count too
        doc = db.row("SELECT content FROM documents WHERE profile_id=? AND kind='resume_master'", (p["id"],))
        head = (db.loads(doc["content"], {}) or {}).get("header", {}) if doc else {}
        known = list((out.get("profile") or {}).get("links") or []) + list(head.get("links") or [])
        out["assessment"] = resume.drop_stale_link_tips(dict(out["assessment"]), {"links": known})
    out["app_stats"] = mail.stats(p["id"])
    out["email_accounts"] = db.row("SELECT COUNT(*) AS n FROM email_accounts WHERE profile_id=?", (p["id"],))["n"]
    return out


@app.get("/api/discover")
def discover_status():
    return discover.status()


@app.post("/api/discover")
def discover_now():
    return {"started": discover.start(), **discover.status()}


@app.post("/api/sync")
def trigger_sync():
    if sync.busy():
        return {"started": False, "reason": "already running"}
    _sync_in_background()
    return {"started": True}


# --- profiles ----------------------------------------------------------------------

def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict", path="/")


@app.post("/api/profiles")
def create_profile(request: Request, response: Response, body: dict = Body(...)):
    name = (body.get("name") or "").strip()[:40]
    passcode = body.get("passcode") or ""
    if not name:
        raise HTTPException(400, "name required")
    if passcode and len(passcode) < auth.MIN_LENGTH:
        raise HTTPException(400, f"passcode must be at least {auth.MIN_LENGTH} characters")
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json) VALUES (?,?,?,?)",
                        (name, db.now(), db.now(), db.dumps({}))).lastrowid
    if passcode:
        auth.set_passcode(pid, passcode)
        _set_session_cookie(response, auth.unlock(request.cookies.get(auth.COOKIE), pid))
    return _public_profile(store.get_profile(pid))


@app.post("/api/auth/unlock")
def unlock(request: Request, response: Response, body: dict = Body(...)):
    pid = int(body.get("profile_id") or 0)
    if not db.row("SELECT 1 FROM profiles WHERE id=?", (pid,)):
        raise HTTPException(404, "profile not found")
    wait = auth.wait_seconds(pid)
    if wait:
        raise HTTPException(429, f"Too many wrong tries. Wait {wait}s.")
    if not auth.verify(pid, body.get("passcode") or ""):
        wait = auth.wait_seconds(pid)
        raise HTTPException(403, "Wrong passcode" + (f". Locked for {wait}s." if wait else "."))
    _set_session_cookie(response, auth.unlock(request.cookies.get(auth.COOKIE), pid))
    return {"ok": True}


@app.post("/api/auth/lock")
def lock(request: Request):
    auth.lock(request.cookies.get(auth.COOKIE))
    return {"ok": True}


@app.post("/api/profiles/{pid}/passcode")
def change_passcode(pid: int, request: Request, response: Response, body: dict = Body(...)):
    """Set, change (needs the current passcode) or remove (remove: true + current) a passcode."""
    if auth.has_passcode(pid):
        wait = auth.wait_seconds(pid)
        if wait:
            raise HTTPException(429, f"Too many wrong tries. Wait {wait}s.")
        if not auth.verify(pid, body.get("current") or ""):
            raise HTTPException(403, "Current passcode is wrong")
    if body.get("remove"):
        auth.clear_passcode(pid)
        return {"ok": True, "has_passcode": False}
    try:
        auth.set_passcode(pid, body.get("new") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    _set_session_cookie(response, auth.unlock(request.cookies.get(auth.COOKIE), pid))
    return {"ok": True, "has_passcode": True}


# every table that holds a person's data, read from the schema so a new table is never left behind
_PROFILE_TABLES = [t for t, cols in db.COLUMNS.items() if "profile_id" in cols]


@app.delete("/api/profiles/{pid}")
def delete_profile(pid: int):
    workday.forget_legacy_accounts(pid)
    for a in db.rows("SELECT id FROM email_accounts WHERE profile_id=?", (pid,)):
        delete_email_account(a["id"])
    with db.tx() as c:
        for t in _PROFILE_TABLES:
            c.execute(f"DELETE FROM {t} WHERE profile_id=?", (pid,))
        c.execute("DELETE FROM profiles WHERE id=?", (pid,))
        c.execute("DELETE FROM meta WHERE key LIKE ?", (f"%:{pid}",))  # their settings kept by name: leetcode_user:{pid}...
    for f in UPLOAD_DIR.glob(f"{pid}_*"):
        f.unlink(missing_ok=True)
    return {"ok": True}


@app.put("/api/profiles/{pid}/prefs")
def update_prefs(pid: int, prefs: dict = Body(...)):
    p = _profile_or_404(pid)
    merged = matching.merged_prefs({**p["prefs"], **prefs})
    if "name" in prefs and prefs["name"].strip():
        name = prefs["name"].strip()[:40]
    else:
        name = p["name"]
    merged.pop("name", None)
    with db.tx() as c:
        c.execute("UPDATE profiles SET prefs_json=?, name=?, updated_at=? WHERE id=?",
                  (db.dumps(merged), name, max(db.now(), p["updated_at"] + 1), pid))
    return _public_profile(store.get_profile(pid))


@app.post("/api/profiles/{pid}/resume")
async def upload_resume(pid: int, file: UploadFile = File(...)):
    _profile_or_404(pid)
    data = await file.read()
    if len(data) > 15_000_000:
        raise HTTPException(413, "resume too large (15 MB max)")
    fname = re.sub(r"[^\w.-]+", "_", file.filename or "resume.pdf")
    if not fname.lower().endswith((".pdf", ".docx", ".txt", ".md")):
        raise HTTPException(400, "upload a PDF, DOCX or TXT resume")
    try:
        text = resume.extract_text(fname, data)
    except Exception as e:
        raise HTTPException(400, f"couldn't read that file: {e}")
    if len(text) < 80:
        raise HTTPException(400, "couldn't find text in that file (is it a scanned image?)")
    (UPLOAD_DIR / f"{pid}_{fname}").write_bytes(data)
    return _ingest_resume(pid, fname, data)


@app.post("/api/profiles/{pid}/linkedin")
async def upload_linkedin(pid: int, file: UploadFile = File(...)):
    """Optional: a LinkedIn "Save to PDF" export fills gaps the resume leaves (never overrides it)."""
    p = _profile_or_404(pid)
    data = await file.read()
    if len(data) > 10_000_000 or not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Upload the PDF from LinkedIn: Profile → More → Save to PDF")
    facts = resume.linkedin_facts(resume.extract_text("linkedin.pdf", data))
    prof = p["profile"]
    filled = []
    if facts["coop_program"] and not prof.get("coop_program"):
        prof["coop_program"], _ = True, filled.append("co-op program")
    if facts["grad_date"] and not prof.get("grad_date"):
        prof["grad_date"], _ = facts["grad_date"], filled.append("graduation date")
    if facts["linkedin"] and not any("linkedin.com" in (l or "") for l in prof.get("links") or []):
        prof["links"] = (prof.get("links") or []) + [facts["linkedin"]]
        filled.append("LinkedIn link")
    prof["linkedin_headline"] = facts["headline"] or prof.get("linkedin_headline", "")
    with db.tx() as c:
        c.execute("UPDATE profiles SET profile_json=?, updated_at=? WHERE id=?",
                  (db.dumps(prof), max(db.now(), p["updated_at"] + 1), pid))
    return {"filled": filled, "facts": facts}


def reread_resume(pid: int, background_ai: bool = True, rebuild_master: bool = True) -> bool:
    """Run the stored upload through the current parser again (Resume Studio's Re-import, after parser fixes)."""
    p = store.get_profile(pid)
    f = UPLOAD_DIR / f"{pid}_{p['resume_filename']}" if p and p.get("resume_filename") else None
    if not f or not f.exists():
        return False
    _ingest_resume(pid, p["resume_filename"], f.read_bytes(), background_ai=background_ai, rebuild_master=rebuild_master)
    return True


def repair_cut_names() -> None:
    """Names an AI read with part of them hidden ("Alex Chen" saved as "Chen"): put the file's name back. Only that exact
    damage is touched, so a name someone chose to change stays."""
    for p in db.rows("SELECT id, name, resume_text, profile_json FROM profiles WHERE resume_text IS NOT NULL"):
        whole = resume.name_line(p["resume_text"])
        prof = db.loads(p["profile_json"], {}) or {}
        with db.tx() as c:
            if resume.cut_by_hiding(prof.get("name"), whole, p["name"]):
                prof["name"] = whole
                c.execute("UPDATE profiles SET profile_json=? WHERE id=?", (db.dumps(prof), p["id"]))
                log.info("put the full name back on profile %s", p["id"])
            for d in c.execute("SELECT id, content FROM documents WHERE profile_id=? AND kind IN ('resume_master', 'resume_source', 'resume')",
                               (p["id"],)).fetchall():
                body = db.loads(d["content"], {}) or {}
                h = body.get("header") if isinstance(body, dict) else None
                if isinstance(h, dict) and resume.cut_by_hiding(h.get("name"), whole, p["name"]):
                    h["name"] = whole
                    c.execute("UPDATE documents SET content=? WHERE id=?", (db.dumps(body), d["id"]))


def reparse_outdated() -> None:
    """After a parser upgrade, refresh every Coach card from its stored file. Resume Studio copies someone edited
    by hand are left alone (they can Re-import when they want)."""
    try:
        repair_cut_names()
    except Exception as e:
        log.warning("name repair failed: %s", e)
    for r in db.rows("SELECT id FROM profiles WHERE resume_filename IS NOT NULL AND"
                     " COALESCE(parser_version, 0) < ?", (resume.PARSER_VERSION,)):
        doc = db.row("SELECT generated_by FROM documents WHERE profile_id=? AND kind='resume_master'", (r["id"],))
        try:
            reread_resume(r["id"], background_ai=False, rebuild_master=not (doc and doc["generated_by"] == "you"))
        except Exception as e:
            log.warning("re-parse after parser upgrade failed for profile %s: %s", r["id"], e)


def _ingest_resume(pid: int, fname: str, data: bytes, background_ai: bool = True, rebuild_master: bool = True) -> dict:
    """Parse a resume file into the profile (school, skills, links...) and Resume Studio's editable version."""
    p = _profile_or_404(pid)
    text = resume.extract_text(fname, data)
    links = resume.extract_links(fname, data)  # URLs hidden behind words like "GitHub"

    parsed_by, note = "rules", ""
    profile = assessment = None
    if ai.available():
        try:
            profile, assessment = ai.parse_resume(fname, data, text, resume.describe_links(links))
            parsed_by = ai.provider()
        except Exception as e:
            log.warning("AI resume parse failed, using rule-based parser: %s", e)
            note = f"{ai.label() or 'AI'} parsing failed ({type(e).__name__}); used the built-in parser instead."
    rules = resume.heuristic_parse(text)
    if profile is None:
        profile = resume.merge_file_links(rules, links)
        assessment = resume.heuristic_assessment(profile, text)
    else:
        # links come from the file itself (visible URLs + the ones behind words); an AI only ever guesses at them
        profile["links"] = list(rules.get("links") or [])
        profile["email"] = rules.get("email") or profile.get("email", "")
        profile["name"] = resume.whole_name(profile.get("name"), rules.get("name"))
        resume.merge_file_links(profile, links)
        # dates and program facts are deterministic: trust the parser's reading of the actual dates over a guess
        for k in ("year_of_study", "grad_year", "grad_date", "coop_program"):
            if rules.get(k):
                profile[k] = rules[k]
        profile["target_categories"] = list(dict.fromkeys(
            (profile.get("target_categories") or []) + rules["target_categories"]))[:4]
    resume.drop_stale_link_tips(assessment, profile)
    # canonical skill tags keep matching consistent no matter who parsed the resume
    canon = detect_skills(text)
    lower = {c.lower() for c in canon}
    extra = [s for s in profile.get("skills", []) if s.lower() not in lower and
             not set(detect_skills(s)) & set(canon)]  # "FastAPI" is already covered by "Django/Flask/FastAPI"
    profile["skills"] = list(dict.fromkeys(canon + extra))

    with db.tx() as c:
        c.execute("UPDATE profiles SET resume_filename=?, resume_text=?, profile_json=?, assessment_json=?,"
                  " parsed_by=?, updated_at=?, parser_version=? WHERE id=?",
                  (fname, text, db.dumps(profile), db.dumps(assessment), parsed_by,
                   max(db.now(), p["updated_at"] + 1), resume.PARSER_VERSION, pid))
    if rebuild_master:
        routes_docs.build_master(pid, use_ai=False)  # instant rule-based structure for Resume Studio
    if ai.available() and background_ai and rebuild_master:
        built = db.now()
        threading.Thread(target=lambda: routes_docs.build_master(pid, use_ai=True, if_unchanged_since=built),
                         daemon=True).start()
    if ai.available() and parsed_by != "rules":
        threading.Thread(target=lambda: _safe_ai_score(pid), daemon=True).start()
    out = _public_profile(store.get_profile(pid))
    out["note"] = note
    return out


def _safe_ai_score(pid: int):
    try:
        sync.score_with_ai(store.get_profile(pid), limit=40)
    except Exception as e:
        log.warning("background AI scoring failed: %s", e)


@app.post("/api/profiles/{pid}/test-notify")
def test_notify(pid: int):
    p = _profile_or_404(pid)
    jobs = sorted(store.scored_jobs(pid), key=lambda j: -j["score"])[:3]
    res = notify.send(p["name"], p["prefs"]["notify"], jobs, events.upcoming(14)[:2])
    if not res:
        raise HTTPException(400, "no notification channels configured")
    return {"results": res}


# --- jobs ----------------------------------------------------------------------------

@app.get("/api/jobs")
def get_jobs(profile_id: int, q: str = "", tiers: str = "", categories: str = "", region: str = "",
             term: str = "", since_days: int = 0, min_score: int = 0, sort: str = "match",
             hide_applied: bool = False, limit: int = Query(60, le=200), offset: int = 0,
             show_ineligible: bool = False, starred: bool = False, niche: str = "", track: str = "",
             facet: str = "", population: str = ""):
    _profile_or_404(profile_id)
    return store.list_jobs(profile_id, q=q, tiers=[t for t in tiers.split(",") if t],
                           categories=[c for c in categories.split(",") if c], region=region, term=term,
                           since_days=since_days, min_score=min_score, sort=sort, hide_applied=hide_applied,
                           limit=limit, offset=offset, show_ineligible=show_ineligible, starred=starred, niche=niche,
                           track=track[:120], facet=facet[:200], population="market" if population == "market" else "")


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = db.row("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not j:
        raise HTTPException(404)
    return store._decode(j)


@app.post("/api/favourites")
def add_favourite(body: dict = Body(...)):
    kind, key, pid = body.get("kind"), body.get("key"), body.get("profile_id")
    if kind not in ("company", "job") or not key or not pid:
        raise HTTPException(400, "profile_id, kind (company|job) and key required")
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO favourites (profile_id, kind, key, label, created_at) VALUES (?,?,?,?,?)",
                  (pid, kind, key, body.get("label"), db.now()))
    if kind == "job":
        routes_docs.auto_letter(pid, key)
    return {"ok": True}


@app.delete("/api/favourites")
def remove_favourite(profile_id: int, kind: str, key: str):
    with db.tx() as c:
        c.execute("DELETE FROM favourites WHERE profile_id=? AND kind=? AND key=?", (profile_id, kind, key))
    return {"ok": True}


@app.get("/api/favourites")
def list_favourites(profile_id: int):
    favs = db.rows("SELECT * FROM favourites WHERE profile_id=? ORDER BY created_at DESC", (profile_id,))
    jobs = store.collapsed_jobs()
    by_company: dict[str, int] = {}
    names: dict[str, str] = {}
    for j in jobs:
        by_company[j["company_key"]] = by_company.get(j["company_key"], 0) + 1
        names.setdefault(j["company_key"], j["company"])
    for f in favs:
        if f["kind"] == "company":
            f["open_roles"] = by_company.get(f["key"], 0)
            f["name"] = f["label"] or names.get(f["key"], f["key"])
    return favs


@app.put("/api/applications")
def upsert_application(body: dict = Body(...)):
    pid, jid, status = body.get("profile_id"), body.get("job_id"), body.get("status")
    if not pid or not jid:
        raise HTTPException(400, "profile_id and job_id required")
    with db.tx() as c:
        if not status:
            c.execute("DELETE FROM applications WHERE profile_id=? AND job_id=?", (pid, jid))
        else:
            if status not in store.APP_STATUSES:
                raise HTTPException(400, f"status must be one of {store.APP_STATUSES}")
            now = db.now()
            # keep the posting's requisition number on the application so it survives the posting closing
            c.execute("INSERT INTO applications (profile_id, job_id, status, notes, deadline, source, applied_at,"
                      " updated_at, req_id) VALUES (?,?,?,?,?,'board',?,?,"
                      " COALESCE(?, (SELECT req_id FROM jobs WHERE id=?))) ON CONFLICT(profile_id, job_id) DO UPDATE SET"
                      " status=excluded.status, notes=COALESCE(excluded.notes, applications.notes),"
                      " deadline=COALESCE(excluded.deadline, applications.deadline),"
                      " applied_at=COALESCE(applications.applied_at, excluded.applied_at),"
                      " updated_at=excluded.updated_at, req_id=COALESCE(applications.req_id, excluded.req_id)",
                      (pid, jid, status, body.get("notes"), body.get("deadline"),
                       now if status != "saved" else None, now, (body.get("req_id") or "").strip() or None, jid))
    if status in ("saved", "applied") and not jid.startswith("hack:"):  # hackathons don't take cover letters
        routes_docs.auto_letter(pid, jid)
    return {"ok": True}


@app.put("/api/applications/req")
def set_application_req(body: dict = Body(...)):
    """Type in (or fix) the requisition number for an application."""
    pid, jid = body.get("profile_id"), body.get("job_id")
    rid = re.sub(r"\s+", "", body.get("req_id") or "")[:40] or None
    with db.tx() as c:
        n = c.execute("UPDATE applications SET req_id=? WHERE profile_id=? AND job_id=?", (rid, pid, jid)).rowcount
    if not n:
        raise HTTPException(404, "application not found")
    return {"ok": True, "req_id": rid}


@app.post("/api/applications/manual")
def add_manual_application(body: dict = Body(...)):
    pid = body.get("profile_id")
    company, title = (body.get("company") or "").strip(), (body.get("title") or "").strip()
    status = body.get("status") or "applied"
    if not pid or not company or status not in store.APP_STATUSES:
        raise HTTPException(400, "profile_id, company and a valid status are required")
    import hashlib

    from .textutil import company_key, title_key
    jid = "ext:" + hashlib.sha1(f"{company_key(company)}|{title_key(title)}".encode()).hexdigest()[:12]
    url = (body.get("url") or "").strip()
    from .textutil import extract_req_id
    typed = re.sub(r"\s+", "", body.get("req_id") or "")[:40]
    rid = typed or (extract_req_id(url) if url.startswith("http") else None) or None
    with db.tx() as c:
        c.execute("INSERT INTO applications (profile_id, job_id, status, company, title, url, source, applied_at,"
                  " updated_at, req_id) VALUES (?,?,?,?,?,?,'manual',?,?,?) ON CONFLICT(profile_id, job_id) DO UPDATE SET"
                  " status=excluded.status, url=COALESCE(excluded.url, applications.url),"
                  " updated_at=excluded.updated_at, req_id=COALESCE(excluded.req_id, applications.req_id)",
                  (pid, jid, status, company, title or "Role", url if url.startswith("http") else None,
                   db.now() if status != "saved" else None, db.now(), rid))
    return {"ok": True, "job_id": jid}


@app.get("/api/applications")
def list_applications(profile_id: int):
    apps = db.rows(
        "SELECT a.profile_id, a.job_id, a.status, a.notes, a.deadline, a.source, a.applied_at, a.updated_at,"
        " a.last_email_at, a.last_email_subject, COALESCE(a.req_id, j.req_id) AS req_id, a.interview_at, a.oa_due_at,"
        " COALESCE(j.company, a.company) AS company, COALESCE(j.title, a.title) AS title,"
        " COALESCE(j.url, a.url) AS url, j.locations, COALESCE(j.active, 1) AS active, j.tier, j.terms,"
        " (SELECT COUNT(*) FROM email_events e WHERE e.profile_id=a.profile_id AND e.job_id=a.job_id"
        "  AND e.ignored=0) AS email_count"
        " FROM applications a LEFT JOIN jobs j ON j.id = a.job_id WHERE a.profile_id=?"
        " ORDER BY COALESCE(a.last_email_at, a.updated_at) DESC", (profile_id,))
    for a in apps:
        a["locations"] = db.loads(a["locations"], [])
        a["terms"] = db.loads(a["terms"], [])
    return {"applications": apps, "stats": mail.stats(profile_id)}


# --- sources -----------------------------------------------------------------------------

@app.get("/api/companies")
def list_companies():
    return db.rows("SELECT * FROM companies WHERE removed=0 ORDER BY (last_count > 0) DESC, name COLLATE NOCASE")


@app.post("/api/companies")
def add_company(body: dict = Body(...)):
    url, name = (body.get("url") or "").strip(), (body.get("name") or "").strip()
    try:
        found = ats.detect(url) or ats.probe_custom_site(url)
    except BlockedURL as e:
        raise HTTPException(400, str(e))
    if not found:
        raise HTTPException(400, "Couldn't read that careers site. Supported: Greenhouse, Lever, Ashby, Workday, "
                                 "SuccessFactors and Jibe sites, plus ycombinator.com company pages. Tip: open one job "
                                 "on the company's site and paste that link.")
    kind, slug = found
    name = name or slug.split("/")[-1].replace("-", " ").replace("_", " ").title()
    try:
        if kind == "workday":
            jobs = ats.workday(slug, name, body.get("tier"), set(), 40)
        else:
            jobs = ats.FETCHERS[kind](slug, name, body.get("tier"), set())
    except Exception as e:
        raise HTTPException(400, f"Found a {kind} board but couldn't read it: {e}")
    with db.tx() as c:
        c.execute("INSERT INTO companies (ats, slug, name, tier, last_status, last_count, last_checked, origin)"
                  " VALUES (?,?,?,?,?,?,?,'you') ON CONFLICT(ats, slug) DO UPDATE SET removed=0, enabled=1,"
                  " name=excluded.name", (kind, slug, name, body.get("tier") or None, "ok", len(jobs), db.now()))
    normed = [n for n in (store.normalize({**j, "source": f"{kind}:{slug}"}) for j in jobs) if n]
    store.upsert_jobs(normed, db.now())
    return {"ok": True, "ats": kind, "slug": slug, "internships": len(normed)}


@app.patch("/api/companies/{cid}")
def toggle_company(cid: int, body: dict = Body(...)):
    with db.tx() as c:
        if "enabled" in body:
            c.execute("UPDATE companies SET enabled=? WHERE id=?", (1 if body["enabled"] else 0, cid))
        if "tier" in body:
            c.execute("UPDATE companies SET tier=? WHERE id=?", (body["tier"], cid))
    return {"ok": True}


@app.delete("/api/companies/{cid}")
def delete_company(cid: int):
    with db.tx() as c:  # soft delete so shipped lists never add it back
        c.execute("UPDATE companies SET removed=1, enabled=0 WHERE id=?", (cid,))
    return {"ok": True}
