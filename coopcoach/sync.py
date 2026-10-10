"""One full sync: pull every source, store, (optionally) AI-score, then notify each profile."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import (
    ai,
    companies,
    config,
    db,
    eligibility,
    events,
    jobdetail,
    mail,
    notify,
    store,
    workday,
)
from .sources import ats, indeed, linkedin, simplify

log = logging.getLogger(__name__)

_lock = threading.Lock()
state = {"running": False, "started_at": None, "progress": "", "last_report": None}


def _company_task(co: dict, known: set[str]):
    fn = ats.FETCHERS[co["ats"]]
    if co["ats"] == "workday":
        return fn(co["slug"], co["name"], co["tier"], known, int(config.get("sources.workday.max_per_company", 120)))
    return fn(co["slug"], co["name"], co["tier"], known)


def run(notify_users: bool = True) -> dict:
    if not _lock.acquire(blocking=False):
        return {"skipped": "a sync is already running"}
    try:
        state.update(running=True, started_at=time.time(), progress="starting")
        return _run(notify_users)
    finally:
        state["running"] = False
        state["progress"] = ""
        _lock.release()


def _run(notify_users: bool) -> dict:
    started = db.now()
    first_sync_ever = db.row("SELECT 1 FROM sync_runs WHERE finished_at IS NOT NULL LIMIT 1") is None
    run_id = None
    with db.tx() as c:
        run_id = c.execute("INSERT INTO sync_runs (started_at) VALUES (?)", (started,)).lastrowid

    store.refresh_flags()
    store.refresh_requirements()
    state["progress"] = "refreshing YC directory"
    companies.refresh_yc()

    known = {r["url"] for r in db.rows("SELECT url FROM jobs WHERE description IS NOT NULL AND description != ''")}
    tasks: dict[str, callable] = {}
    if config.get("sources.simplify.enabled", True):
        repos = config.get("sources.simplify.repos", ["SimplifyJobs/Summer2027-Internships"])
        tasks["simplify"] = lambda: simplify.fetch(repos)
    quiet_hours = float(config.get("sync.quiet_board_hours", 24))
    due_before = started - quiet_hours * 3600
    skipped_quiet = 0
    for co in db.rows("SELECT * FROM companies WHERE enabled=1 AND removed=0"):
        if not config.get(f"sources.{co['ats']}.enabled", True):
            continue
        # boards with internships open are checked every sync; quiet ones about once a day
        if not co["last_count"] and co["last_checked"] and co["last_checked"] > due_before:
            skipped_quiet += 1
            continue
        tasks[f"{co['ats']}:{co['slug']}"] = (lambda co=co: _company_task(co, known))
    li = config.get("sources.linkedin", {}) or {}
    if li.get("enabled"):
        tasks["linkedin"] = lambda: linkedin.fetch(li.get("queries", []), li.get("past_days", 7),
                                                   li.get("pages_per_query", 2))
    ind = config.get("sources.indeed", {}) or {}
    if ind.get("enabled") and indeed.available():
        tasks["indeed"] = lambda: indeed.fetch(ind.get("queries", []), ind.get("past_days", 7),
                                               ind.get("results_per_query", 40))

    report: dict[str, dict] = {}
    all_jobs: list[dict] = []
    ok_sources: list[str] = []
    done = 0
    state["progress"] = f"fetching 0/{len(tasks)} sources"
    with ThreadPoolExecutor(max_workers=int(config.get("sync.workers", 16))) as ex:
        futs = {ex.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(futs):
            key = futs[fut]
            done += 1
            state["progress"] = f"fetching {done}/{len(tasks)} sources"
            try:
                raw = fut.result()
            except Exception as e:  # any one source failing must not sink the sync
                log.warning("source %s failed: %s", key, e)
                report[key] = {"ok": False, "error": str(e)[:200]}
                continue
            normed = []
            for r in raw:
                r["source"] = key if ":" in key else r.get("source", key)
                j = store.normalize(r)
                if j:
                    normed.append(j)
            all_jobs.extend(normed)
            ok_sources.append(key)
            report[key] = {"ok": True, "count": len(normed)}

    # company-board rows remember their status for Settings → Job sources
    with db.tx() as c:
        for key, rep in report.items():
            if ":" in key:
                ats_name, slug = key.split(":", 1)
                c.execute("UPDATE companies SET last_status=?, last_count=?, last_checked=? WHERE ats=? AND slug=?",
                          ("ok" if rep["ok"] else rep["error"], rep.get("count"), started, ats_name, slug))

    state["progress"] = f"saving {len(all_jobs)} postings"
    # dedupe within this run (same URL from two sources): keep the higher-ranked source
    by_id: dict[str, dict] = {}
    for j in all_jobs:
        prev = by_id.get(j["id"])
        if prev is None or store._rank(j["source"]) > store._rank(prev["source"]):
            by_id[j["id"]] = j
    new_ids = store.upsert_jobs(list(by_id.values()), started)
    deact_prefixes = [k for k in ok_sources if k not in ("linkedin", "indeed")]  # only boards checked this run
    closed = store.deactivate_missing(deact_prefixes, set(by_id), started)

    state["progress"] = "checking event feeds"
    try:
        new_events, ev_status = events.sync_feeds()
    except Exception as e:
        log.warning("event sync failed: %s", e)
        new_events, ev_status = 0, {"error": str(e)}

    email_report = {}
    if db.row("SELECT 1 FROM email_accounts LIMIT 1"):
        state["progress"] = "checking inboxes for application updates"
        try:
            email_report = mail.sync_all()
        except Exception as e:
            log.warning("email sync failed: %s", e)
            email_report = {"error": str(e)}

    portal_report = {}
    if workday.available() and db.row("SELECT 1 FROM portal_accounts WHERE needs_manual=0 LIMIT 1"):
        state["progress"] = "checking Workday application statuses"
        try:
            portal_report = workday.check_all()
        except Exception as e:
            log.warning("Workday checks failed: %s", e)
            portal_report = {"error": str(e)}

    state["progress"] = "checking who your top matches are open to"
    for prof in store.list_profiles():
        if prof["profile"]:
            try:
                enrich_top(prof)
            except Exception as e:
                log.warning("eligibility check failed for %s: %s", prof["name"], e)

    ai_report = {}
    if ai.available():
        state["progress"] = "AI-scoring promising postings"
        for prof in store.list_profiles():
            if prof["profile"]:
                try:
                    ai_report[prof["name"]] = score_with_ai(prof)
                except Exception as e:
                    log.warning("AI scoring failed for %s: %s", prof["name"], e)
                    ai_report[prof["name"]] = f"failed: {e}"

    notified = {}
    if notify_users and not first_sync_ever:
        state["progress"] = "sending notifications"
        notified = notify_profiles(set(new_ids), started)
        try:  # ⚡ Instant Autopilot: prepare a just-posted great match right away
            from . import autopilot
            state["progress"] = "Autopilot: preparing just-posted matches"
            autopilot.on_sync(set(new_ids))
        except Exception as e:
            log.warning("instant Autopilot failed: %s", e)

    summary = {
        "sources": report,
        "quiet_boards_skipped": skipped_quiet,
        "new_jobs": len(new_ids),
        "closed_jobs": closed,
        "total_active": db.row("SELECT COUNT(*) AS n FROM jobs WHERE active=1")["n"],
        "new_events": new_events,
        "event_feeds": ev_status,
        "ai": ai_report,
        "email": email_report,
        "workday": portal_report,
        "notifications": notified,
        "first_sync": first_sync_ever,
        "seconds": round(time.time() - started, 1),
    }
    with db.tx() as c:
        c.execute("UPDATE sync_runs SET finished_at=?, new_jobs=?, new_events=?, report=? WHERE id=?",
                  (db.now(), len(new_ids), new_events, db.dumps(summary), run_id))
    if first_sync_ever:
        # everything found by the very first sync was already open; don't treat it as "new"
        db.meta_set("baseline_ts", db.now())
    state["last_report"] = summary
    log.info("sync done: %s new, %s closed, %s active, %ss", len(new_ids), closed, summary["total_active"],
             summary["seconds"])
    return summary


def enrich_top(prof: dict, n: int = 60) -> int:
    """Read the full posting (and the Greenhouse application form) for someone's best matches, so who a role is
    open to is known before it's ranked or AI-scored. Each posting is only fetched once."""
    top = sorted(store.scored_jobs(prof["id"]), key=lambda j: -j["score"])[:n]
    ids = [i for j in top for i in j["alt_ids"]]
    rows = db.rows(f"SELECT id, url, source, title, description, form_checked FROM jobs WHERE id IN "
                   f"({','.join('?' * len(ids))}) AND form_checked=0", ids) if ids else []

    def one(r):
        desc, questions = r["description"] or "", None
        if r["source"].startswith("greenhouse"):
            got = ats.greenhouse_form(r["url"], r["source"])
            if got:
                desc, questions = got[0] or desc, got[1]
        if len(desc) < 200:
            desc = jobdetail.ensure_description(r["id"]) or desc
        return r, desc, questions

    with ThreadPoolExecutor(6) as ex:
        done = list(ex.map(one, rows))
    with db.tx() as c:
        for r, desc, questions in done:
            req = eligibility.extract(r["title"], desc, questions)
            c.execute("UPDATE jobs SET requirements=?, form_checked=1, description=CASE WHEN length(?) >"
                      " length(COALESCE(description,'')) THEN ? ELSE description END WHERE id=?",
                      (db.dumps(req), desc, desc, r["id"]))
            store.patch_job(r["id"], requirements=req)
    return len(done)


def score_with_ai(prof: dict, limit: int | None = None) -> str:
    """Send the most promising not-yet-AI-scored postings to the AI provider in small batches."""
    limit = limit or ai.max_scored_per_sync()
    jobs = store.scored_jobs(prof["id"])
    version = prof["updated_at"]
    have = {r["job_id"] for r in db.rows("SELECT job_id FROM ai_scores WHERE profile_id=? AND profile_version=?",
                                         (prof["id"], version))}
    cands = [j for j in jobs if j["id"] not in have and j["heuristic"] >= 45]
    cands.sort(key=lambda j: (-(j["favourite"]), -j["heuristic"], -(j["first_seen"])))
    cands = cands[:limit]
    if not cands:
        return "nothing new to score"
    full = {r["id"]: r for r in db.rows(
        f"SELECT id, description FROM jobs WHERE id IN ({','.join('?' * len(cands))})", [j["id"] for j in cands])}
    for j in cands:
        j["description"] = (full.get(j["id"]) or {}).get("description") or ""
    scored = 0
    step = ai.batch_size()
    for i in range(0, len(cands), step):
        batch = cands[i:i + step]
        results = ai.score_jobs(prof["profile"], prof["prefs"], batch)
        with db.tx() as c:
            for r in results:
                c.execute("INSERT OR REPLACE INTO ai_scores (profile_id, job_id, fit, why, tip, profile_version,"
                          " computed_at) VALUES (?,?,?,?,?,?,?)",
                          (prof["id"], r["job_id"], max(0, min(100, int(r["fit"]))), r["why"], r["tip"], version,
                           db.now()))
        scored += len(results)
    store.refresh_scores()
    return f"scored {scored}"


def notify_profiles(new_ids: set[str], run_started: int) -> dict:
    out = {}
    min_default = int(config.get("notifications.min_score", 60))
    max_items = int(config.get("notifications.max_items", 10))
    upcoming = events.upcoming(days=30)
    for prof in store.list_profiles():
        n = prof["prefs"]["notify"]
        min_score = int(n.get("min_score") or min_default)
        already = {r["item_id"] for r in db.rows("SELECT item_id FROM notified WHERE profile_id=?", (prof["id"],))}
        picks = []
        for j in store.scored_jobs(prof["id"]):
            if not (set(j["alt_ids"]) & new_ids) or j["id"] in already:
                continue
            if j["first_seen"] < prof["created_at"]:
                continue
            if j["favourite"] or j["score"] >= min_score:
                picks.append(j)
        picks.sort(key=lambda j: (-j["favourite"], -j["score"]))
        picks = picks[:max_items]
        regions, kinds = prof["prefs"].get("event_regions") or [], prof["prefs"].get("event_kinds") or []
        new_evs = [e for e in upcoming if e["first_seen"] >= run_started and f"evt:{e['id']}" not in already
                   and e["kind"] != "other" and (not regions or e.get("region") in regions)
                   and (not kinds or e["kind"] in kinds) and not events.JUNK_RE.search(e["title"])][:6]
        # saved (⭐/✅) events starting within a day and a half get a reminder
        saved = {r["event_id"] for r in db.rows("SELECT event_id FROM event_marks WHERE profile_id=?", (prof["id"],))}
        soon = time.time() + 36 * 3600
        reminders = [{**e, "title": f"⏰ Coming up: {e['title']}"} for e in upcoming if e["id"] in saved
                     and time.time() < e["start_ts"] < soon and f"rem:{e['id']}" not in already]
        new_evs = reminders + new_evs
        updates = [u for u in db.rows(
            "SELECT id, status, company, role, subject FROM email_events WHERE profile_id=? AND changed_status=1"
            " AND ignored=0 AND created_at >= ? AND status != 'applied'", (prof["id"], run_started))
            if f"mail:{u['id']}" not in already]
        if not picks and not new_evs and not updates:
            continue
        res = notify.send(prof["name"], n, picks, new_evs, updates)
        with db.tx() as c:
            for j in picks:
                c.execute("INSERT OR IGNORE INTO notified VALUES (?,?,?)", (prof["id"], j["id"], db.now()))
            for e in new_evs:
                key = f"rem:{e['id']}" if e["title"].startswith("⏰") else f"evt:{e['id']}"
                c.execute("INSERT OR IGNORE INTO notified VALUES (?,?,?)", (prof["id"], key, db.now()))
            for u in updates:
                c.execute("INSERT OR IGNORE INTO notified VALUES (?,?,?)", (prof["id"], f"mail:{u['id']}", db.now()))
        out[prof["name"]] = {"jobs": len(picks), "events": len(new_evs), "updates": len(updates), "channels": res}
    return out


def last_run() -> dict | None:
    r = db.row("SELECT * FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
    if r:
        r["report"] = db.loads(r["report"], {})
    return r
