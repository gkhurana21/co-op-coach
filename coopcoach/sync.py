"""One full sync: pull every source, store, (optionally) AI-score, then notify each profile."""

import logging
import multiprocessing
import os
import queue
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import (
    ai,
    central,
    companies,
    config,
    db,
    eligibility,
    events,
    jobdetail,
    niches,
    notify,
    store,
    tracks,
)
from .sources import ats, indeed, linkedin, simplify
from .textutil import company_key

log = logging.getLogger(__name__)

_lock = threading.Lock()
state = {"running": False, "started_at": None, "progress": "", "finishing": "", "last_report": None}


def _company_task(co: dict, known: set[str], peek: bool = False):
    try:
        return _read_board(co, known, peek)
    except (httpx.TransportError, OSError) as e:  # a timeout or dropped connection: once more before it counts
        log.info("retrying %s:%s after %s", co["ats"], co["slug"], e)
        time.sleep(1)
        return _read_board(co, known, peek)


def _read_board(co: dict, known: set[str], peek: bool = False):
    """One board's internships. `peek` (the quick check): just its newest postings on the boards where reading all
    takes long (Workday, big Lever boards); co["_partial"] then says the read didn't see the whole board."""
    fn, info = ats.FETCHERS[co["ats"]], {}
    if co["ats"] == "workday":  # how this board is best read (its internship filter), kept between syncs
        co["_plan"] = db.loads(co.get("plan"), {}) or {}
        # the first read through the board's internship filter finds roles that were open all along, just missed
        co["_first_filter_read"] = not peek and not co["_plan"].get("full_by")
        out = fn(co["slug"], co["name"], co["tier"], known, int(config.get("sources.workday.max_per_company", 120)),
                 plan=co["_plan"], peek=peek, info=info)
    elif co["ats"] == "lever":
        out = fn(co["slug"], co["name"], co["tier"], known, peek=peek, info=info)
    elif co["ats"] in ("greenhouse", "ashby", "yc"):
        out = fn(co["slug"], co["name"], co["tier"], known, info=info)
    else:
        out = fn(co["slug"], co["name"], co["tier"], known)
    co["_partial"] = bool(info.get("partial"))
    co["_roles"] = info.get("roles")  # every open role on the board, when the read saw them all
    return out


# boards are read in lanes, each with its own threads: YC pages come from one site and are read a few at a time, and
# that lane never holds up the hundreds of Greenhouse, Lever, Ashby and Workday boards read alongside it (in worker
# processes for a full sync, here for a short list)
LANES = {"yc": 6, "workday": 32}


def _lane(key: str) -> str:
    kind = key.split(":", 1)[0] if ":" in key else "other"
    return kind if kind in LANES else "other"


WORKER_LANES = {"workday": 20, "other": 10}  # threads in each sync worker process
_WORKERS_DONE = "__workers_done__"


def _processes(boards: int) -> int:
    """How many worker processes read the company boards: a few for a full sync, none (threads here) for a short
    list like the quick check."""
    n = int(os.environ.get("COOPCOACH_SYNC_PROCESSES") or config.get("sync.processes", 4))
    return max(1, min(n, (os.cpu_count() or 2) - 1)) if boards >= int(config.get("sync.processes_from", 150)) else 1


def _result(key: str, co: dict, ok: bool, payload, secs: float) -> dict:
    """One board's read, as it travels back from a worker process (or a thread here)."""
    return {"key": key, "ok": ok, "payload": payload, "secs": secs, "plan": co.get("_plan") if ok else None,
            "first_read": bool(ok and co.get("_first_filter_read")), "partial": bool(ok and co.get("_partial")),
            "roles": co.get("_roles") if ok else None}


def _start_workers(cos: list[dict], known: set[str], n: int, results: queue.Queue, peek: bool = False) -> list:
    """Start n worker processes, each with a fair share of every kind of board; their results land in `results`
    (then a marker once they've all stopped)."""
    ctx = multiprocessing.get_context("spawn")  # the same on macOS, Windows and Linux
    out = ctx.Queue()
    ordered = sorted(cos, key=lambda c: (c["ats"], -(c.get("last_count") or 0)))
    shares = [ordered[i::n] for i in range(n)]
    procs = [ctx.Process(target=_worker, args=(share, known, WORKER_LANES, out, peek), name=f"sync-worker-{i}",
                         daemon=True) for i, share in enumerate(shares) if share]
    for p in procs:
        p.start()

    def forward():
        finished = 0
        while finished < len(procs):
            try:
                item = out.get(timeout=2)
            except queue.Empty:
                if not any(p.is_alive() for p in procs):
                    break
                continue
            if item is None:
                finished += 1
            else:
                results.put(item)
        results.put({"key": _WORKERS_DONE})
    threading.Thread(target=forward, name="sync-workers", daemon=True).start()
    return procs


def _worker(cos: list[dict], known: set[str], lanes: dict[str, int], out, peek: bool = False) -> None:
    """A sync worker process: reads its share of the boards, several at a time, sending back each as it's read."""
    pools = {name: ThreadPoolExecutor(n, thread_name_prefix=f"sync-{name}") for name, n in lanes.items()}

    def one(co):
        key = f"{co['ats']}:{co['slug']}"
        t = time.time()
        try:
            raw = _company_task(co, known, peek)
        except Exception as e:
            out.put(_result(key, co, False, str(e)[:200], time.time() - t))
        else:
            out.put(_result(key, co, True, raw, time.time() - t))

    futs = [pools["workday" if co["ats"] == "workday" else "other"].submit(one, co) for co in cos]
    for f in futs:
        f.result()
    out.put(None)  # this worker is done


def _quiet_due(co: dict, started: int, quiet_hours: float) -> bool:
    """A board with no internships open is read again after about quiet_hours: each board gets its own interval
    between half and one and a half times that (fixed per board), so quiet boards spread across the day's syncs
    instead of all coming due in one long sync."""
    spread = 0.5 + zlib.crc32(f"{co['ats']}:{co['slug']}".encode()) % 1000 / 1000
    return co["last_checked"] <= started - quiet_hours * 3600 * spread


def busy() -> bool:
    """A sync (full or quick) is under way, its last steps (AI scoring, alerts) included."""
    return _lock.locked()


def run(notify_users: bool = True) -> dict:
    """The full sync (every few hours): every source."""
    return _guarded(lambda: _run(notify_users))


def run_quick() -> dict:
    """The quick check between full syncs (every 20 minutes or so): only the boards that matter most to someone (see
    priority_boards), so a new internship there is found and alerted within minutes, not hours."""
    if db.row("SELECT 1 FROM sync_runs WHERE finished_at IS NOT NULL LIMIT 1") is None:
        return {"skipped": "waiting for the first full sync"}
    return _guarded(lambda: _run(True, quick=True))


def _guarded(fn) -> dict:
    if not _lock.acquire(blocking=False):
        return {"skipped": "a sync is already running"}
    try:
        state.update(running=True, started_at=time.time(), progress="starting", finishing="")
        return fn()
    finally:
        state.update(running=False, progress="", finishing="")
        _lock.release()


def priority_boards(limit: int = 250) -> set[str]:
    """The boards the quick check reads, most useful first: ones someone added themselves, in niches they follow, at
    companies they starred, saved or applied to; then the boards behind their best current matches (where roles like
    theirs keep appearing); then boards that posted in the last three days."""
    cos = db.rows("SELECT ats, slug, name, origin FROM companies WHERE enabled=1 AND removed=0")
    keys = {f"{c['ats']}:{c['slug']}" for c in cos}
    by_lower = {k.lower(): k for k in keys}
    by_company: dict[str, list[str]] = {}
    for c in cos:
        by_company.setdefault(company_key(c["name"]), []).append(f"{c['ats']}:{c['slug']}")
    picked: list[str] = [f"{c['ats']}:{c['slug']}" for c in cos if c["origin"] == "you"]
    matched: list[str] = []
    for prof in store.list_profiles():
        pid = prof["id"]
        picked += [by_lower[b.lower()] for b in niches.followed_boards(pid) if b.lower() in by_lower]
        for r in db.rows("SELECT key FROM favourites WHERE profile_id=? AND kind='company'", (pid,)):
            picked += by_company.get(r["key"], [])
        for r in db.rows("SELECT DISTINCT j.source, j.company_key FROM jobs j JOIN (SELECT job_id AS id FROM"
                         " applications WHERE profile_id=? UNION SELECT key AS id FROM favourites WHERE profile_id=?"
                         " AND kind='job') m ON m.id = j.id", (pid, pid)):
            picked += [r["source"]] if r["source"] in keys else by_company.get(r["company_key"], [])
        if prof["profile"]:
            top = sorted(store.scored_jobs(pid), key=lambda j: -j["score"])[:120]
            matched += [j["source"] for j in top if j["source"] in keys]
    since = db.now() - 3 * 86400  # by the posting's own date: a board's filter can turn up roles open for months
    recent = [r["source"] for r in db.rows(
        "SELECT source, MAX(COALESCE(posted_at, first_seen)) AS t FROM jobs WHERE active=1 AND first_seen >= ? AND"
        " COALESCE(posted_at, first_seen) >= ? AND source LIKE '%:%' GROUP BY source ORDER BY t DESC", (since, since))]
    return set(list(dict.fromkeys(picked + matched + [k for k in recent if k in keys]))[:limit])


def _run(notify_users: bool, quick: bool = False) -> dict:
    started = db.now()
    timings: dict[str, float] = {}
    mark = [time.time()]

    def lap(phase: str) -> None:  # how long each part of the sync took, kept in its report
        now = time.time()
        timings[phase] = round(now - mark[0], 1)
        mark[0] = now

    first_sync_ever = db.row("SELECT 1 FROM sync_runs WHERE finished_at IS NOT NULL LIMIT 1") is None
    with db.tx() as c:
        run_id = c.execute("INSERT INTO sync_runs (started_at, kind) VALUES (?,?)",
                           (started, "quick" if quick else "full")).lastrowid

    feed = central.mode()  # this app pulls from the shared feed: it reads only the boards the feed doesn't
    own = not feed or not central.fresh()  # ...unless its crawler hasn't run for half a day: then every board here
    if feed and own:
        log.warning("the shared feed hasn't crawled for %s hours: reading every board here meanwhile",
                    central.STALE_HOURS)
    if not quick:
        store.refresh_flags()
        store.refresh_skills()
        store.refresh_requirements()
        state["progress"] = "refreshing YC directory"
        companies.refresh_yc()
        try:  # every YC company that says it's hiring: its jobs page is read too (what Work at a Startup lists)
            from . import discover
            if own:
                discover.track_hiring_yc()
        except Exception as e:
            log.warning("tracking hiring YC companies failed: %s", e)

    known = {r["url"] for r in db.rows("SELECT url FROM jobs WHERE description IS NOT NULL AND description != ''")}
    fingerprints = store.stored_fingerprints()
    tasks: dict[str, callable] = {}
    boards: dict[str, dict] = {}
    if config.get("sources.simplify.enabled", True) and not quick and own:
        repos = config.get("sources.simplify.repos", ["SimplifyJobs/Summer2027-Internships"])
        tasks["simplify"] = lambda: simplify.fetch(repos)
    quiet_hours = float(config.get("sync.quiet_board_hours", 24))
    # the crawler has nobody's matches to go by: its quick check reads more of the boards that just posted. On the
    # feed, the quick check still reads the boards that matter to this person here, between the feed's crawls
    wanted = priority_boards(500 if central.crawler() else 250) if quick else None
    if wanted is not None and not own:
        wanted |= {f"{c['ats']}:{c['slug']}" for c in central.local_boards()}
    skipped_quiet = 0
    new_boards: set[str] = set()  # boards never read before (following a niche adds some): what they list isn't new
    every_board = own or quick  # a quick check picks from all of them; a full read on the feed only reads its own
    for co in db.rows("SELECT * FROM companies WHERE enabled=1 AND removed=0") if every_board else central.local_boards():
        key = f"{co['ats']}:{co['slug']}"
        if not config.get(f"sources.{co['ats']}.enabled", True) or (wanted is not None and key not in wanted):
            continue
        if co["last_checked"] is None:
            new_boards.add(key)
        # boards with internships open are checked every sync; quiet ones about once a day
        if not quick and not co["last_count"] and co["last_checked"] and not _quiet_due(co, started, quiet_hours):
            skipped_quiet += 1
            continue
        boards[key] = co
        tasks[key] = (lambda co=co: _company_task(co, known, True)) if quick else (lambda co=co: _company_task(co, known))
    li = config.get("sources.linkedin", {}) or {}
    if li.get("enabled") and not quick and own:
        tasks["linkedin"] = lambda: linkedin.fetch(li.get("queries", []), li.get("past_days", 7),
                                                   li.get("pages_per_query", 2))
    ind = config.get("sources.indeed", {}) or {}
    if ind.get("enabled") and indeed.available() and not quick and own:
        tasks["indeed"] = lambda: indeed.fetch(ind.get("queries", []), ind.get("past_days", 7),
                                               ind.get("results_per_query", 40))

    lap("preparing")
    side = ThreadPoolExecutor(1, thread_name_prefix="sync-events")
    # campus and event calendars don't depend on the boards: read alongside them (the crawler only reads postings)
    from . import features
    events_fut = side.submit(events.sync_feeds) if not quick and not central.crawler() and features.shown("events") else None
    report: dict[str, dict] = {}
    seen_from: dict[str, set[str]] = {}  # posting id -> the sources that listed it this run
    new_ids: list[str] = []
    opened: set[str] = set()
    closed = 0
    pulled: dict = {}
    if feed:  # everything the feed found since the last look, in a second or two
        state["progress"] = "getting new postings from the shared feed"
        try:
            pulled = central.pull()
        except Exception as e:
            log.warning("pulling from the shared feed failed: %s", e)
            pulled = {"error": str(e)[:200]}
        new_ids.extend(pulled.get("new") or [])
        opened.update(pulled.get("opened") or [])
        closed += pulled.get("closed") or 0
        lap("shared feed")
    label = "quick check" if quick else "fetching"
    today = time.strftime("%Y-%m-%d")
    state["progress"] = f"{label} 0/{len(tasks)} sources"

    def save(results: list[tuple[str, list[dict]]]) -> None:
        """Store the boards read since the last save: their new postings show while the rest are still being read."""
        nonlocal closed
        if not results:
            return
        with db.tx() as c:  # one batch: thousands of boards in a few round trips on Supabase, not one each
            c.executemany("UPDATE companies SET last_status=?, last_count=?, last_checked=?, plan=COALESCE(?, plan)"
                          " WHERE ats=? AND slug=?",
                          [("ok" if report[key]["ok"] else report[key]["error"], report[key].get("count"), started,
                            db.dumps(boards[key]["_plan"]) if boards.get(key, {}).get("_plan") is not None else None,
                            *key.split(":", 1)) for key, _ in results if ":" in key and not report[key].get("partial")])
            # every role each board lists today (full reads only): how fast it's hiring, for the startup feed
            c.executemany("INSERT INTO board_roles (board, day, roles, interns) VALUES (?,?,?,?) ON CONFLICT(board,"
                          " day) DO UPDATE SET roles=excluded.roles, interns=excluded.interns",
                          [(key, today, boards[key]["_roles"], report[key].get("count")) for key, _ in results
                           if report[key]["ok"] and not report[key].get("partial")
                           and boards.get(key, {}).get("_roles") is not None])
        # dedupe within the batch (same URL from two sources): keep the higher-ranked source
        by_id: dict[str, dict] = {}
        for _, jobs in results:
            for j in jobs:
                prev = by_id.get(j["id"])
                if prev is None or store._rank(j["source"]) > store._rank(prev["source"]):
                    by_id[j["id"]] = j
        fresh = store.upsert_jobs(list(by_id.values()), started)
        new_ids.extend(fresh)
        # postings first seen only because their board was just added were already open: they don't alert, don't
        # wake Instant Autopilot and say nothing about when the firm opened
        batch = {i for i in fresh if not seen_from.get(i, set()) <= new_boards}
        opened.update(batch)
        if batch and not first_sync_ever:  # apps pulling from the shared feed alert on these too
            store.mark_opened(batch)
        checked = [k for k, _ in results if report[k]["ok"] and not report[k].get("partial")
                   and k not in ("linkedin", "indeed")]
        closed += store.deactivate_missing(checked, set(by_id), started)

    # company boards go to worker processes (each has its own interpreter: hundreds of boards read in one process
    # spend more time waiting on each other than on the network); YC pages and the lists are read here
    procs = _processes(len(boards))
    remote = {k: co for k, co in boards.items() if co["ats"] != "yc"} if procs > 1 else {}
    results: queue.Queue = queue.Queue()
    lanes = {name: ThreadPoolExecutor(max_workers=n, thread_name_prefix=f"sync-{name}") for name, n in
             {**LANES, "other": int(config.get("sync.workers", 48))}.items()}

    def local(key: str, fn) -> None:
        t = time.time()
        try:
            raw = fn()
        except Exception as e:  # any one source failing must not sink the sync
            results.put(_result(key, boards.get(key, {}), False, str(e)[:200], time.time() - t))
        else:
            results.put(_result(key, boards.get(key, {}), True, raw, time.time() - t))

    for key, fn in tasks.items():
        if key not in remote:
            lanes[_lane(key)].submit(local, key, fn)
    workers = _start_workers(list(remote.values()), known, procs, results, quick) if remote else []
    pending: list[tuple[str, list[dict]]] = []
    every = float(config.get("sync.save_every_seconds", 15))
    last_save = time.time()
    waiting, workers_done = set(tasks), not workers
    try:
        while waiting and not (workers_done and waiting <= remote.keys()):
            try:
                item = results.get(timeout=5)
            except queue.Empty:
                continue
            if item.get("key") == _WORKERS_DONE:
                workers_done = True
                continue
            key, ok, payload, secs = item["key"], item["ok"], item["payload"], item["secs"]
            first_read, partial = item["first_read"], item["partial"]
            waiting.discard(key)
            state["progress"] = f"{label} {len(tasks) - len(waiting)}/{len(tasks)} sources"
            if not ok:
                log.warning("source %s failed: %s", key, payload)
                report[key] = {"ok": False, "error": payload}
                pending.append((key, []))
            else:
                if key in boards:
                    boards[key].update(_plan=item["plan"], _first_filter_read=first_read, _roles=item["roles"])
                normed = []
                for r in payload:
                    r["source"] = key if ":" in key else r.get("source", key)
                    j = store.prepare(r, fingerprints)
                    if j:
                        normed.append(j)
                        seen_from.setdefault(j["id"], set()).add(key)
                report[key] = {"ok": True, "count": len(normed), "secs": round(secs, 1)}
                if partial:  # saw only the newest postings: nothing closes, and the board's numbers stay
                    report[key]["partial"] = True
                pending.append((key, normed))
                if first_read:
                    new_boards.add(key)  # what it finds isn't new: no alerts or opening dates from it
            if time.time() - last_save >= every:  # what's in so far shows now, not when the slowest board is done
                save(pending)
                pending, last_save = [], time.time()
    finally:
        for ex in lanes.values():
            ex.shutdown(wait=False, cancel_futures=True)
        for w in workers:
            w.join(timeout=2)
            if w.is_alive():
                w.terminate()
    for key in waiting:  # a worker process that stopped early: its unread boards count as failed this time
        report[key] = {"ok": False, "error": "not read: the sync worker reading it stopped"}
        pending.append((key, []))
    lap("fetching")
    state["progress"] = "saving"
    save(pending)
    if not quick:  # a few months of hiring history is plenty
        with db.tx() as c:
            c.execute("DELETE FROM board_roles WHERE day < ?", (time.strftime("%Y-%m-%d", time.localtime(
                started - 120 * 86400)),))
    if not first_sync_ever:  # the first sync finds what was already open: no opening dates to learn from it
        try:
            niches.record_openings(opened)
        except Exception as e:
            log.warning("recording openings failed: %s", e)
    lap("saving")

    new_events, ev_status = 0, {}
    if events_fut:
        state["progress"] = "checking event feeds"
        try:
            new_events, ev_status = events_fut.result()
        except Exception as e:
            log.warning("event sync failed: %s", e)
            ev_status = {"error": str(e)}
    side.shutdown(wait=False)
    lap("events")

    email_report = {}
    if db.row("SELECT 1 FROM email_accounts LIMIT 1"):  # on their own threads: a slow inbox never holds up postings
        from .routes_email import _start_email_sync
        checking = [p["id"] for p in db.rows("SELECT DISTINCT profile_id AS id FROM email_accounts WHERE enabled=1")
                    if _start_email_sync(p["id"])]
        email_report = {"started": len(checking)}

    lap("inboxes")

    if not quick:
        state["progress"] = "checking who your top matches are open to"
        for prof in store.list_profiles():
            if prof["profile"]:
                try:
                    enrich_top(prof)
                except Exception as e:
                    log.warning("eligibility check failed for %s: %s", prof["name"], e)
        lap("reading top matches")

    summary = {
        "kind": "quick" if quick else "full",
        "sources": report,
        "quiet_boards_skipped": skipped_quiet,
        "new_jobs": len(new_ids),
        "closed_jobs": closed,
        "total_active": db.row("SELECT COUNT(*) AS n FROM jobs WHERE active=1")["n"],
        "new_events": new_events,
        "event_feeds": ev_status,
        "email": email_report,
        "first_sync": first_sync_ever,
        **({"shared_feed": {k: (len(v) if isinstance(v, list) else v) for k, v in pulled.items()}} if feed else {}),
    }

    def finish() -> None:
        summary.update(seconds=round(time.time() - started, 1), timings=timings,
                       slowest=sorted(((v["secs"], k) for k, v in report.items() if "secs" in v), reverse=True)[:8])
        with db.tx() as c:
            c.execute("UPDATE sync_runs SET finished_at=?, new_jobs=?, new_events=?, report=? WHERE id=?",
                      (db.now(), len(new_ids), new_events, db.dumps(summary), run_id))
        state["last_report"] = summary

    # the postings are in: the sync shows as done while AI scoring and alerts finish in the background
    finish()
    if first_sync_ever:
        # everything found by the very first sync was already open; don't treat it as "new"
        db.meta_set("baseline_ts", db.now())
    state.update(running=False, progress="")

    ai_report = {}
    if ai.available() and not quick:
        state["finishing"] = "AI-scoring promising postings"
        for prof in store.list_profiles():
            if prof["profile"]:
                try:
                    ai_report[prof["name"]] = score_with_ai(prof)
                except Exception as e:
                    log.warning("AI scoring failed for %s: %s", prof["name"], e)
                    ai_report[prof["name"]] = f"failed: {e}"
        lap("AI scoring")

    try:  # new kinds of role (once a day) and each person's tracks, so track pages and their alerts are ready
        state["finishing"] = "updating your tracks"
        found = tracks.after_pull()
        if found:
            summary["tracks_found"] = found
    except Exception as e:
        log.warning("updating tracks failed: %s", e)
    lap("tracks")

    notified = {}
    if notify_users and not first_sync_ever:
        state["finishing"] = "sending alerts"
        notified = notify_profiles(opened, started)
        try:  # ⚡ Instant Autopilot: prepare a just-posted great match right away
            from . import autopilot
            state["finishing"] = "Autopilot: preparing just-posted matches"
            autopilot.on_sync(opened)
        except Exception as e:
            log.warning("instant Autopilot failed: %s", e)
        lap("notifications")
    summary.update(ai=ai_report, notifications=notified)
    if not quick and own:  # boards that list only titles (Workday): read their full postings a batch at a time
        state["finishing"] = "reading full postings"
        try:
            summary["descriptions_read"] = backfill_descriptions()
        except Exception as e:
            log.warning("reading full postings failed: %s", e)
        lap("reading full postings")
    finish()
    log.info("%s sync done: %s new, %s closed, %s active, %ss (%s)", summary["kind"], len(new_ids), closed,
             summary["total_active"], summary["seconds"], ", ".join(f"{k} {v}s" for k, v in timings.items()))
    return summary


def backfill_descriptions(limit: int = 1000, seconds: float = 90) -> int:
    """Workday lists only a title, place and date: its postings' full text (and so their skills, flags and who can
    apply) is read here, up to `limit` a sync and within `seconds`, best matches first, then the newest. A few
    syncs cover every posting; after that it keeps up with the new ones."""
    from .textutil import detect_flags, detect_skills
    need = {r["id"]: r for r in db.rows(
        "SELECT id, url, title, COALESCE(posted_at, first_seen) AS t FROM jobs WHERE active=1 AND url LIKE"
        " '%myworkdayjobs.com%' AND (description IS NULL OR length(description) < 200)")}
    if not need:
        return 0
    order: list[str] = []
    for prof in store.list_profiles():
        if prof["profile"]:
            top = sorted(store.scored_jobs(prof["id"]), key=lambda j: -j["score"])[:limit]
            order += [i for j in top for i in j["alt_ids"] if i in need]
    rest = sorted((i for i in need if i not in set(order)), key=lambda i: -(need[i]["t"] or 0))
    ids = list(dict.fromkeys(order + rest))[:limit]
    deadline = time.time() + seconds

    def one(job_id):
        if time.time() > deadline:
            return None
        with ats._WD_GATE:  # the same cap as the board reads: Workday says "too many requests" past it
            return job_id, jobdetail.fetch_description(need[job_id]["url"])

    got = []
    with ThreadPoolExecutor(12) as ex:
        for res in ex.map(one, ids):
            if res and res[1]:
                got.append(res)
    if not got:
        return 0
    rows = []
    for job_id, desc in got:
        title = need[job_id]["title"]
        rows.append((desc, db.dumps(detect_skills(f"{title}\n{desc}")), db.dumps(detect_flags(title, desc)),
                     db.dumps(eligibility.extract(title, desc, None)), db.now(), job_id))
    with db.tx() as c:
        c.executemany("UPDATE jobs SET description=?, skills=?, flags=?, requirements=?, changed_at=? WHERE id=?", rows)
    store.invalidate_cache()
    log.info("read %s full Workday postings", len(rows))
    return len(rows)


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
    reqs = [(r, desc, eligibility.extract(r["title"], desc, questions)) for r, desc, questions in done]
    with db.tx() as c:
        c.executemany("UPDATE jobs SET requirements=?, form_checked=1, description=CASE WHEN length(?) >"
                      " length(COALESCE(description,'')) THEN ? ELSE description END, changed_at=? WHERE id=?",
                      [(db.dumps(req), desc, desc, db.now(), r["id"]) for r, desc, req in reqs])
    for r, _, req in reqs:
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
    cands = [dict(j) for j in cands[:limit]]  # (the list is shared: add to copies)
    if not cands:
        return "nothing new to score"
    full = {r["id"]: r for r in db.rows(
        f"SELECT id, description FROM jobs WHERE id IN ({','.join('?' * len(cands))})", [j["id"] for j in cands])}
    for j in cands:
        j["description"] = (full.get(j["id"]) or {}).get("description") or ""
    scored = 0
    step = ai.batch_size()
    batches = [cands[i:i + step] for i in range(0, len(cands), step)]
    with ThreadPoolExecutor(ai.score_workers()) as ex:  # a few batches at once: each is a separate AI request
        answers = ex.map(lambda batch: ai.score_jobs(prof["profile"], prof["prefs"], batch), batches)
        for results in answers:
            with db.tx() as c:
                c.executemany("INSERT OR REPLACE INTO ai_scores (profile_id, job_id, fit, why, tip, profile_version,"
                              " computed_at) VALUES (?,?,?,?,?,?,?)",
                              [(prof["id"], r["job_id"], max(0, min(100, int(r["fit"]))), r["why"], r["tip"], version,
                                db.now()) for r in results])
            scored += len(results)
    store.refresh_scores()
    return f"scored {scored}"


def notify_profiles(new_ids: set[str], run_started: int) -> dict:
    out = {}
    min_default = int(config.get("notifications.min_score", 60))
    max_items = int(config.get("notifications.max_items", 10))
    from . import features
    upcoming = events.upcoming(days=30) if features.mode() == "all" else []  # (lean's alerts stay about jobs)
    for prof in store.list_profiles():
        n = prof["prefs"]["notify"]
        min_score = int(n.get("min_score") or min_default)
        already = {r["item_id"] for r in db.rows("SELECT item_id FROM notified WHERE profile_id=?", (prof["id"],))}
        followed = tracks.follow_matcher(prof["id"])  # roles in tracks they follow always alert, like favourites
        picks = []
        for j in store.scored_jobs(prof["id"]):
            if not (set(j["alt_ids"]) & new_ids) or j["id"] in already:
                continue
            if j["first_seen"] < prof["created_at"]:
                continue
            niche_hit = bool(followed and followed.firm(j)) and j["eligibility"] != "no"
            if j["favourite"] or niche_hit or j["score"] >= min_score:
                picks.append({**j, "niche_hit": niche_hit})
        # favourites first; niche roles take their place by score, so they never push out a better match
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


def last_run(full_only: bool = False) -> dict | None:
    """The last finished sync; full_only skips the quick checks in between."""
    r = db.row("SELECT * FROM sync_runs WHERE finished_at IS NOT NULL" + (" AND (kind IS NULL OR kind='full')" if
               full_only else "") + " ORDER BY id DESC LIMIT 1")
    if r:
        r["report"] = db.loads(r["report"], {})
    return r
