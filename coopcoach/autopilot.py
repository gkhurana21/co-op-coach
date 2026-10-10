"""Autopilot: on the days and at the time you pick, Co-op Coach does the job-hunt busywork and leaves you a short list.

Each pass:
1. Picks your best untouched matches using your Autopilot settings: score, roles/places/terms, who it's open to,
   company mix, blocklist, freshness, favourites first, and what your "Not for me" reasons taught it.
2. Prepares each one into "Ready to apply": form answers, tailored resume, cover letter, PDFs rendered ahead.
3. Housekeeping: drops prepared ones whose posting closed or that sat unopened too long, drafts follow-ups for quiet
   applications and thank-yous after interviews, writes interview prep sheets, saves events from companies you follow.
4. Sends you the brief (held until your quiet hours end).

Also: "⚡ Instant" prepares a just-posted great match right after a sync, a weekly goal paces how many it prepares,
and a Sunday recap sums up the week. With your Claude Code connected it does the choosing and writes the brief
itself (read-only tools plus queue_application). You review each prepared application and press Submit yourself.
"""

import logging
import math
import threading
import time
import uuid
from datetime import datetime, timedelta

from . import agent, ai, db, notify, store
from .textutil import company_key

log = logging.getLogger(__name__)
DEFAULTS = {
    "on": False, "time": "08:00", "days": [0, 1, 2, 3, 4, 5, 6], "per_day": 3, "min_score": 70, "engine": "auto",
    "notify": True, "quiet_from": "", "quiet_to": "", "instant": False, "instant_score": 88, "paused_until": "",
    "categories": [], "locations": [], "terms": [], "mix": {}, "eligibility": "any", "posted_within": 0,
    "favourites_first": True, "blocklist": [],
    "letter": True, "letter_style": "", "tailor": True, "ai_answers": True, "prerender": True, "fill_with": "chrome",
    "weekly_goal": 0, "followups": True, "followup_days": 21, "thankyou": True, "prep": True, "auto_events": True,
    "expire_days": 10,
}
FILL_WITH = ("chrome", "claude_code", "codex", "atlas", "manual")
ELIGIBILITY = {"fit": {"fit"}, "open": {"fit", "ok"}, "any": {"fit", "ok", "check"}}
SKIP_REASONS = {"location": "Wrong place", "company": "Not that company", "role": "Wrong kind of role",
                "senior": "Too senior", "term": "Wrong term", "other": "Something else"}
_SENIOR = ("senior", "staff", "lead", "principal", "ii", "iii", "master", "phd", "graduate")
_state: dict[int, dict] = {}
_lock = threading.Lock()

SYSTEM = """You are Co-op Coach's Autopilot, running unattended on a student's computer. You prepare internship/co-op
applications for the student to review and submit later. Tools: daily_brief, search_jobs, job_details,
queue_application, ready_queue, my_applications, find_events, my_resume, preview_picks.
Rules:
- Prepare at most the number of applications you're given, only for postings where eligibility isn't "no", that
  they haven't applied to or queued, and that genuinely fit their resume and goals. One per company. Fewer is fine
  if nothing is good enough: never pad the list. Respect the student's Autopilot settings shown below.
- Call job_details before queue_application, and give queue_application a one-line `why` grounded in the posting.
- Never invent facts, experience or numbers. You can't and must not submit anything.
- Finish with a brief for the student in plain text, under 170 words, with three short sections:
  "Ready to apply" (each prepared one: company · role, why), "Needs you" (follow-ups, interviews to prep, anything
  odd) and "This week" (1-3 events worth going to, if any)."""


# --- settings ------------------------------------------------------------------------------------------------

def settings(pid: int) -> dict:
    p = store.get_profile(pid)
    return {**DEFAULTS, **((p or {}).get("prefs", {}).get("autopilot") or {})}


def _hhmm(v) -> str:
    v = str(v or "").strip()
    try:
        h, m = (int(x) for x in v.split(":")[:2])
        return f"{h:02d}:{m:02d}" if 0 <= h < 24 and 0 <= m < 60 else ""
    except ValueError:
        return ""


def save_settings(pid: int, change: dict) -> dict:
    """Autopilot settings live in prefs but don't change scores, so the score cache is left alone."""
    cur = settings(pid)
    clamp = lambda v, lo, hi: max(lo, min(int(v), hi))  # noqa: E731
    words = lambda v: [str(x).strip()[:60] for x in (v or []) if str(x).strip()][:30]  # noqa: E731
    for k, v in (change or {}).items():
        try:
            if k in ("on", "notify", "instant", "favourites_first", "letter", "tailor", "ai_answers", "prerender",
                     "followups", "thankyou", "prep", "auto_events"):
                cur[k] = bool(v)
            elif k == "time" and _hhmm(v):
                cur[k] = _hhmm(v)
            elif k in ("quiet_from", "quiet_to"):
                cur[k] = _hhmm(v)
            elif k == "days":
                cur[k] = sorted({int(d) for d in v if 0 <= int(d) <= 6}) or [0, 1, 2, 3, 4]
            elif k == "per_day":
                cur[k] = clamp(v, 1, 10)
            elif k in ("min_score", "instant_score"):
                cur[k] = clamp(v, 0, 100)
            elif k == "posted_within":
                cur[k] = clamp(v, 0, 60)
            elif k == "weekly_goal":
                cur[k] = clamp(v, 0, 50)
            elif k == "followup_days":
                cur[k] = clamp(v, 7, 60)
            elif k == "expire_days":
                cur[k] = clamp(v, 3, 30)
            elif k == "engine" and v in ("auto", "claude_code", "codex", "rules"):
                cur[k] = v
            elif k == "eligibility" and v in ELIGIBILITY:
                cur[k] = v
            elif k == "fill_with" and v in FILL_WITH:
                cur[k] = v
            elif k in ("categories", "locations", "terms", "blocklist"):
                cur[k] = words(v)
            elif k == "mix" and isinstance(v, dict):
                cur[k] = {t: clamp(n, 0, 10) for t, n in v.items() if str(t).isidentifier() and int(n) > 0}
            elif k == "letter_style":
                cur[k] = str(v or "")[:40]
            elif k == "paused_until":
                cur[k] = str(v)[:10] if not v or datetime.strptime(str(v)[:10], "%Y-%m-%d") else ""
        except (TypeError, ValueError):
            continue
    raw = db.loads(db.row("SELECT prefs_json FROM profiles WHERE id=?", (pid,))["prefs_json"], {}) or {}
    raw["autopilot"] = cur
    with db.tx() as c:
        c.execute("UPDATE profiles SET prefs_json=? WHERE id=?", (db.dumps(raw), pid))
    return cur


def state(pid: int) -> dict:
    return _state.get(pid) or {"running": False}


def last_run(pid: int) -> dict | None:
    return db.row("SELECT * FROM autopilot_runs WHERE profile_id=? AND trigger != 'instant' ORDER BY id DESC LIMIT 1",
                  (pid,))


def history(pid: int, n: int = 14) -> list[dict]:
    return db.rows("SELECT id, started_at, finished_at, trigger, engine, queued, brief, error FROM autopilot_runs WHERE"
                   " profile_id=? ORDER BY id DESC LIMIT ?", (pid, n))


def paused(cfg: dict, today=None) -> bool:
    today = today or datetime.now().date()
    try:
        return bool(cfg.get("paused_until")) and datetime.strptime(cfg["paused_until"], "%Y-%m-%d").date() >= today
    except ValueError:
        return False


def next_run(cfg: dict, now: datetime | None = None) -> float | None:
    if not cfg.get("on"):
        return None
    now = now or datetime.now()
    hh, mm = (int(x) for x in (_hhmm(cfg.get("time")) or "08:00").split(":"))
    for add in range(0, 15):
        d = now + timedelta(days=add)
        at = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if d.weekday() in cfg.get("days", DEFAULTS["days"]) and at > now and not paused(cfg, d.date()):
            return at.timestamp()
    return None


# --- what it learned from "Not for me" ------------------------------------------------------------------------

def _city(loc: str) -> str:
    return (loc or "").split(",")[0].strip().lower()


def learned(pid: int) -> dict:
    """Penalties from the last 90 days of skip reasons: 4 points per skip, up to 16, for that place/role/term."""
    rows = db.rows("SELECT q.skip_reason, j.locations, j.category, j.terms FROM ready_queue q JOIN jobs j ON j.id=q.job_id"
                   " WHERE q.profile_id=? AND q.status='skipped' AND q.skip_reason IS NOT NULL AND q.updated_at > ?",
                   (pid, db.now() - 90 * 86400))
    out = {"places": {}, "categories": {}, "terms": {}, "senior": 0}
    for r in rows:
        if r["skip_reason"] == "location":
            for loc in db.loads(r["locations"], [])[:2]:
                c = _city(loc)
                out["places"][c] = min(16, out["places"].get(c, 0) + 4)
        elif r["skip_reason"] == "role" and r["category"]:
            out["categories"][r["category"]] = min(16, out["categories"].get(r["category"], 0) + 4)
        elif r["skip_reason"] == "term":
            for t in db.loads(r["terms"], [])[:1]:
                out["terms"][t] = min(16, out["terms"].get(t, 0) + 4)
        elif r["skip_reason"] == "senior":
            out["senior"] = min(16, out["senior"] + 4)
    return out


def skip(pid: int, job_id: str, reason: str = "") -> dict:
    """'Not for me'. A company reason adds it to the blocklist; the others tune future picks."""
    reason = reason if reason in SKIP_REASONS else None
    with db.tx() as c:
        c.execute("UPDATE ready_queue SET status='skipped', skip_reason=?, updated_at=? WHERE profile_id=? AND job_id=?",
                  (reason, db.now(), pid, job_id))
    if reason == "company":
        j = db.row("SELECT company FROM jobs WHERE id=?", (job_id,))
        if j:
            cfg = settings(pid)
            if j["company"] not in cfg["blocklist"]:
                save_settings(pid, {"blocklist": cfg["blocklist"] + [j["company"]]})
    return {"ok": True, "reason": reason}


# --- picking -------------------------------------------------------------------------------------------------

def _fresh(j: dict) -> int:
    return j.get("posted_at") or (j["first_seen"] if j.get("fresh") else 0)


def candidates(pid: int, cfg: dict | None = None, limit: int = 12, only: set[str] | None = None,
               min_score: int | None = None) -> list[dict]:
    """Ranked postings worth preparing, one per company, honouring the settings and the company mix. Each has
    `rank` and `pick_reasons` (shown by Preview picks)."""
    cfg = {**DEFAULTS, **(cfg or settings(pid))}
    floor = cfg["min_score"] if min_score is None else min_score
    queued = {r["job_id"] for r in db.rows("SELECT job_id FROM ready_queue WHERE profile_id=?", (pid,))}
    block = {company_key(n) for n in cfg["blocklist"]}
    cats, terms = set(cfg["categories"]), set(cfg["terms"])
    places = [x.lower() for x in cfg["locations"]]
    allowed = ELIGIBILITY.get(cfg["eligibility"], ELIGIBILITY["any"])
    now = time.time()
    since = now - cfg["posted_within"] * 86400 if cfg["posted_within"] else 0
    pen = learned(pid)
    out = []
    for j in store.scored_jobs(pid):
        if (only is not None and j["id"] not in only) or j["eligibility"] not in allowed or j["app_status"] \
                or j["id"] in queued or j.get("twin") or j["score"] < floor or j["company_key"] in block:
            continue
        if cats and j["category"] not in cats or terms and not terms & set(j["terms"]):
            continue
        if places and not any(p in f"{' '.join(j['locations'])} {' '.join(j['regions'])}".lower() for p in places):
            continue
        if since and _fresh(j) < since:
            continue
        rank, why = j["score"], [f"{j['score']}% match"]
        if j["eligibility"] == "fit":
            rank += 4
            why.append("open to you")
        if _fresh(j) > now - 3 * 86400:
            rank += 3
            why.append("posted in the last 3 days")
        if j["favourite"]:
            rank += 8 if cfg["favourites_first"] else 3
            why.append("a company you follow" if j["fav_company"] else "you starred it")
        p = max([pen["places"].get(_city(loc), 0) for loc in j["locations"][:3]] or [0])
        p += pen["categories"].get(j["category"], 0) + max([pen["terms"].get(t, 0) for t in j["terms"]] or [0])
        if pen["senior"] and any(w in j["title"].lower().split() for w in _SENIOR):
            p += pen["senior"]
        if p:
            rank -= p
            why.append(f"−{p} from your “Not for me” picks")
        out.append({**j, "rank": rank, "pick_reasons": why})
    out.sort(key=lambda j: (-j["rank"], -_fresh(j)))
    seen, ranked = set(), []
    for j in out:
        if j["company_key"] not in seen:
            seen.add(j["company_key"])
            ranked.append(j)
    return _apply_mix(ranked, cfg["mix"], limit)


def _apply_mix(ranked: list[dict], mix: dict, limit: int) -> list[dict]:
    """Quotas like {"bigtech": 1, "startup": 1}: the best of each tier first, then the rest by rank."""
    if not mix:
        return ranked[:limit]
    picked = []
    for tier, n in mix.items():
        picked += [j for j in ranked if j["tier"] == tier][:n]
    rest = [j for j in ranked if j not in picked]
    picked.sort(key=lambda j: -j["rank"])
    return (picked + rest)[:limit]


def week_progress(pid: int, cfg: dict | None = None) -> dict:
    cfg = cfg or settings(pid)
    now = datetime.now()
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    applied = db.row("SELECT COUNT(*) AS n FROM applications WHERE profile_id=? AND status != 'saved' AND"
                     " COALESCE(applied_at, updated_at) >= ?", (pid, monday))["n"]
    ready = db.row("SELECT COUNT(*) AS n FROM ready_queue WHERE profile_id=? AND status IN ('ready', 'opened')", (pid,))["n"]
    days_left = sum(1 for d in range(now.weekday(), 7) if d in cfg.get("days", DEFAULTS["days"])) or 1
    return {"goal": cfg.get("weekly_goal") or 0, "applied": applied, "ready": ready, "days_left": days_left,
            "since": monday}


def run_size(pid: int, cfg: dict) -> int:
    """How many to prepare now: the per-run number, or what's needed to stay on pace for the weekly goal."""
    if not cfg.get("weekly_goal"):
        return max(1, min(int(cfg["per_day"]), 10))
    w = week_progress(pid, cfg)
    need = max(0, w["goal"] - w["applied"] - w["ready"])
    return min(10, math.ceil(need / w["days_left"]))


def preview(pid: int, n: int | None = None) -> dict:
    cfg = settings(pid)
    size = run_size(pid, cfg) if n is None else n
    picks = candidates(pid, cfg, limit=max(size, 1) + 4)
    return {"would_prepare": size, "picks": [{"job_id": j["id"], "company": j["company"], "title": j["title"],
                                              "score": j["score"], "where": ", ".join(j["locations"][:2]),
                                              "tier": j["tier"], "why": j["pick_reasons"], "chosen": i < size}
                                             for i, j in enumerate(picks)],
            "week": week_progress(pid, cfg)}


# --- a pass --------------------------------------------------------------------------------------------------

def _engine(cfg: dict) -> str:
    from . import claude_code
    want = cfg.get("engine")
    if want == "rules":
        return "rules"
    if (want == "claude_code" or (want == "auto" and ai.provider() == "claude_code")) and claude_code.usable():
        return "claude_code"
    from . import codex_cli
    if (want == "codex" or (want == "auto" and ai.provider() == "codex")) and codex_cli.usable():
        return "codex"
    return "rules"


def run(pid: int, trigger: str = "manual") -> dict:
    """One Autopilot pass for one person. Safe to call from a thread; one pass at a time per person."""
    with _lock:
        if state(pid).get("running"):
            return {"error": "already running"}
        _state[pid] = {"running": True, "step": "Looking at what's new…", "started": db.now()}
    cfg = settings(pid)
    engine = _engine(cfg)
    with db.tx() as c:
        rid = c.execute("INSERT INTO autopilot_runs (profile_id, started_at, trigger, engine) VALUES (?,?,?,?)",
                        (pid, db.now(), trigger, engine)).lastrowid
    brief, run_id, err = "", uuid.uuid4().hex[:12], None
    try:
        _step(pid, "Tidying Ready to apply…")
        tidy = cleanup(pid, cfg)
        size = run_size(pid, cfg)
        if size and engine in ("claude_code", "codex"):
            try:
                brief, run_id = _run_agent(pid, cfg, size, engine)
            except Exception as e:
                log.warning("Autopilot on %s failed, using the built-in rules: %s", engine, e)
                err = f"{'Claude Code' if engine == 'claude_code' else 'ChatGPT'}: {e}"
                engine = "rules"
        if engine == "rules" or not size:
            brief = _run_rules(pid, cfg, size, run_id)
            err = None if not err else err + " (used the built-in rules instead)"
        extra = housekeeping(pid, cfg, run_id)
        brief = _with_extras(brief, tidy, extra, cfg, pid)
        queued = db.rows("SELECT q.job_id, q.why, j.company, j.title, j.url FROM ready_queue q LEFT JOIN jobs j ON"
                         " j.id=q.job_id WHERE q.profile_id=? AND q.run_id=?", (pid, run_id))
        held = _in_quiet(cfg)
        with db.tx() as c:
            c.execute("UPDATE autopilot_runs SET finished_at=?, engine=?, run_id=?, queued=?, brief=?, error=?, notified=?"
                      " WHERE id=?", (db.now(), engine, run_id, len(queued), brief, err, int(not held), rid))
        if not held:
            _notify(pid, queued, cfg)
        return {"ok": True, "queued": len(queued), "brief": brief, "engine": engine, "error": err}
    except Exception as e:
        log.exception("Autopilot failed")
        with db.tx() as c:
            c.execute("UPDATE autopilot_runs SET finished_at=?, error=? WHERE id=?", (db.now(), str(e)[:300], rid))
        return {"error": str(e)[:300]}
    finally:
        _state[pid] = {"running": False}


def _step(pid: int, text: str) -> None:
    """Progress text for the tab, only while a pass is actually running."""
    if _state.get(pid, {}).get("running"):
        _state[pid] = {**_state[pid], "step": text}


def prepare(pid: int, job_id: str, why: str, run_id: str, cfg: dict, source: str = "autopilot") -> dict:
    """queue_application, then the Autopilot's 'what to prepare' extras (tailored resume, PDFs rendered ahead)."""
    out = agent.call(pid, "queue_application", {"job_id": job_id, "why": why}, source=source, run_id=run_id)
    if "error" not in out and not out.get("already_queued"):
        _extras_only(pid, job_id, cfg)
    return out


def _settings_note(cfg: dict) -> str:
    bits = [f"min score {cfg['min_score']}", f"open to them: {cfg['eligibility']}"]
    for k in ("categories", "locations", "terms", "blocklist"):
        if cfg[k]:
            bits.append(f"{k}: {', '.join(cfg[k])}" if k != "blocklist" else f"never: {', '.join(cfg[k])}")
    if cfg["mix"]:
        bits.append("mix: " + ", ".join(f"{n} {t}" for t, n in cfg["mix"].items()))
    if cfg["posted_within"]:
        bits.append(f"posted within {cfg['posted_within']} days")
    return "; ".join(bits)


def _run_agent(pid: int, cfg: dict, size: int, engine: str) -> tuple[str, str]:
    from . import claude_code
    cands = candidates(pid, cfg, limit=max(8, size * 3))
    lines = [f"- {j['id']} · {j['company']} · {j['title']} · score {j['score']} · {', '.join(j['locations'][:2])}"
             f" · eligibility {j['eligibility']} · {'; '.join(j['pick_reasons'][1:])}" for j in cands]
    prompt = (f"Today is {datetime.now():%A %B %d, %Y}. Prepare up to {size} application(s).\n"
              f"Their Autopilot settings: {_settings_note(cfg)}.\n"
              f"Strongest untouched matches that fit those settings (job_id · company · role · score · where · "
              f"eligibility · notes):\n" + ("\n".join(lines) or "(none: say so in the brief)")
              + "\n\nStart with daily_brief, check the best candidates with job_details, queue the ones worth it, "
                "then write the brief.")
    names = {"job_details": "Reading a posting…", "queue_application": "Preparing an application…",
             "daily_brief": "Checking what's new…", "search_jobs": "Searching matches…", "find_events": "Looking at events…"}
    who = "Claude Code" if engine == "claude_code" else "ChatGPT"
    on_step = lambda s: _step(pid, f"{who}: " + names.get(s["tool"], s["tool"] + "…"))  # noqa: E731
    if engine == "codex":
        from . import codex_cli
        out = codex_cli.run_agent(pid, prompt, SYSTEM, source="autopilot", tools=agent.AUTOPILOT_TOOLS, timeout=900,
                                  on_step=on_step)
    else:
        out = claude_code.run_agent(pid, prompt, SYSTEM, source="autopilot", tools=agent.AUTOPILOT_TOOLS, timeout=900,
                                    on_step=on_step)
    for r in db.rows("SELECT job_id FROM ready_queue WHERE profile_id=? AND run_id=?", (pid, out["run_id"])):
        _extras_only(pid, r["job_id"], cfg)
    return out["reply"], out["run_id"]


def _extras_only(pid: int, job_id: str, cfg: dict) -> None:
    from . import apply, routes_docs
    try:
        if cfg.get("tailor") and ai.available():
            routes_docs.tailor({"profile_id": pid, "job_id": job_id})
        if cfg.get("prerender"):
            kit = agent.cached_kit(pid, job_id)
            if kit:
                kit["local_files"] = apply._write_files(pid, kit)
                with db.tx() as c:
                    c.execute("UPDATE ready_queue SET kit=? WHERE profile_id=? AND job_id=?", (db.dumps(kit), pid, job_id))
    except Exception as e:
        log.warning("Autopilot extras for %s failed: %s", job_id, e)


def _run_rules(pid: int, cfg: dict, size: int, run_id: str) -> str:
    done = []
    for j in candidates(pid, cfg, limit=size * 2) if size else []:
        if len(done) >= size:
            break
        _step(pid, f"Preparing {j['company']}…")
        why = "; ".join(j["pick_reasons"][:3])
        out = prepare(pid, j["id"], why, run_id, cfg)
        if "error" not in out:
            done.append((j, why))
    if not size:
        return "Ready to apply:\n• You're on pace for this week's goal, so nothing new today."
    return "Ready to apply:\n" + ("\n".join(f"• {j['company']} · {j['title']} ({j['score']}%): {why}" for j, why in done)
                                  or "• Nothing new that fits your Autopilot settings today.")


def _with_extras(brief: str, tidy: dict, extra: dict, cfg: dict, pid: int) -> str:
    """Add what housekeeping did and the goal line to whichever brief was written."""
    lines = [brief.strip()]
    needs = []
    if extra.get("followups"):
        needs.append(f"• Follow-up drafts ready: {', '.join(extra['followups'])}")
    if extra.get("thankyous"):
        needs.append(f"• Thank-you notes ready: {', '.join(extra['thankyous'])}")
    if extra.get("preps"):
        needs.append(f"• Interview prep sheets: {', '.join(extra['preps'])}")
    if tidy.get("closed"):
        needs.append(f"• Removed {tidy['closed']} prepared application{'s' if tidy['closed'] > 1 else ''} whose posting closed")
    if needs and "Needs you" not in brief:
        lines += ["", "Needs you:"] + needs
    elif needs:
        lines += needs
    if extra.get("events"):
        lines += ["", "Saved for you: " + "; ".join(extra["events"])]
    if cfg.get("weekly_goal"):
        w = week_progress(pid, cfg)
        lines += ["", f"This week: {w['applied']}/{w['goal']} applied, {w['ready']} ready."]
    return "\n".join(lines).strip()


# --- housekeeping --------------------------------------------------------------------------------------------

def cleanup(pid: int, cfg: dict) -> dict:
    """Prepared applications whose posting closed, or that sat unopened too long, leave Ready to apply."""
    closed = db.rows("SELECT q.job_id FROM ready_queue q JOIN jobs j ON j.id=q.job_id WHERE q.profile_id=? AND"
                     " q.status IN ('ready', 'opened') AND j.active=0", (pid,))
    stale = db.rows("SELECT job_id FROM ready_queue WHERE profile_id=? AND status='ready' AND created_at < ?",
                    (pid, db.now() - cfg.get("expire_days", 10) * 86400))
    with db.tx() as c:
        for r in closed:
            c.execute("UPDATE ready_queue SET status='skipped', note='posting closed', updated_at=? WHERE profile_id=? AND"
                      " job_id=?", (db.now(), pid, r["job_id"]))
        for r in stale:
            c.execute("UPDATE ready_queue SET status='skipped', note='expired unopened', updated_at=? WHERE profile_id=?"
                      " AND job_id=?", (db.now(), pid, r["job_id"]))
    return {"closed": len(closed), "expired": len(stale)}


def housekeeping(pid: int, cfg: dict, run_id: str) -> dict:
    out = {"followups": [], "thankyous": [], "preps": [], "events": []}
    src = {"source": "autopilot", "run_id": run_id}
    if cfg.get("followups"):
        for r in db.rows("SELECT a.job_id, COALESCE(j.company, a.company) AS company FROM applications a LEFT JOIN jobs j"
                         " ON j.id=a.job_id WHERE a.profile_id=? AND a.status='applied' AND COALESCE(a.applied_at,"
                         " a.updated_at) < ? AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.profile_id=a.profile_id"
                         " AND d.job_id=a.job_id AND d.kind='followup') ORDER BY a.updated_at LIMIT 3",
                         (pid, db.now() - cfg.get("followup_days", 21) * 86400)):
            _step(pid, f"Drafting a follow-up for {r['company']}…")
            if "error" not in agent.call(pid, "draft_followup", {"job_id": r["job_id"]}, **src):
                out["followups"].append(r["company"] or "?")
    if cfg.get("thankyou"):
        for r in db.rows("SELECT a.job_id, COALESCE(j.company, a.company) AS company FROM applications a LEFT JOIN jobs j"
                         " ON j.id=a.job_id WHERE a.profile_id=? AND a.status='interviewing' AND a.updated_at > ? AND NOT"
                         " EXISTS (SELECT 1 FROM documents d WHERE d.profile_id=a.profile_id AND d.job_id=a.job_id AND"
                         " d.kind='thankyou') AND (a.interview_at IS NULL OR a.interview_at < ?) LIMIT 2",
                         (pid, db.now() - 10 * 86400, db.now())):  # after the interview, once its time is known
            if "error" not in agent.call(pid, "draft_followup", {"job_id": r["job_id"], "kind": "thankyou"}, **src):
                out["thankyous"].append(r["company"] or "?")
    if cfg.get("prep"):
        for r in db.rows("SELECT a.job_id, COALESCE(j.company, a.company) AS company FROM applications a LEFT JOIN jobs j"
                         " ON j.id=a.job_id WHERE a.profile_id=? AND a.status IN ('oa', 'interviewing') AND NOT EXISTS"
                         " (SELECT 1 FROM documents d WHERE d.profile_id=a.profile_id AND d.job_id=a.job_id AND"
                         " d.kind='prep') LIMIT 2", (pid,)):
            _step(pid, f"Writing an interview prep sheet for {r['company']}…")
            if "error" not in agent.call(pid, "prep_interview", {"job_id": r["job_id"]}, **src):
                out["preps"].append(r["company"] or "?")
    if cfg.get("auto_events"):
        from .events import for_person
        regions = agent._event_regions(pid)
        horizon = db.now() + 14 * 86400
        for e in [e for e in for_person(pid, 14) if not e["mark"] and e["start_ts"] < horizon and (
                e.get("company") or (e["kind"] == "hackathon" and e.get("region") in regions))][:5]:
            if "error" not in agent.call(pid, "save_event", {"event_id": e["id"], "mark": "interested"}, **src):
                out["events"].append(e["title"][:60])
    return out


# --- alerts --------------------------------------------------------------------------------------------------

def _in_quiet(cfg: dict, now: datetime | None = None) -> bool:
    a, b = _hhmm(cfg.get("quiet_from")), _hhmm(cfg.get("quiet_to"))
    if not a or not b or a == b:
        return False
    t = (now or datetime.now()).strftime("%H:%M")
    return a <= t < b if a < b else t >= a or t < b


def _notify(pid: int, queued: list[dict], cfg: dict) -> None:
    p = store.get_profile(pid)
    prefs = (p or {}).get("prefs", {})
    if not queued or not cfg.get("notify", True):
        return
    scores = {j["id"]: j["score"] for j in store.scored_jobs(pid)}
    jobs = [{"company": q["company"] or "", "title": f"{q['title'] or ''} · ready to apply", "url": q["url"] or "",
             "score": scores.get(q["job_id"], 0), "why": q["why"] or "", "locations": []} for q in queued]
    try:
        n = len(queued)
        notify.send(p["name"], prefs.get("notify") or {}, jobs, [],
                    title=f"🤖 Autopilot · {p['name']}: {n} application{'s' if n != 1 else ''} ready to review")
    except Exception as e:
        log.warning("Autopilot notification failed: %s", e)


def on_sync(new_ids: set[str]) -> None:
    """⚡ Instant: right after a sync, prepare a just-posted great match for anyone who turned it on."""
    if not new_ids:
        return
    for p in store.list_profiles():
        cfg = {**DEFAULTS, **((p.get("prefs") or {}).get("autopilot") or {})}
        if not (cfg.get("on") and cfg.get("instant")) or paused(cfg) or state(p["id"]).get("running"):
            continue
        picks = candidates(p["id"], cfg, limit=2, only=new_ids, min_score=cfg.get("instant_score", 88))
        if not picks:
            continue
        run_id = uuid.uuid4().hex[:12]
        done = [j for j in picks if "error" not in prepare(p["id"], j["id"], "⚡ just posted; " + "; ".join(j["pick_reasons"][:2]),
                                                          run_id, cfg)]
        if not done:
            continue
        with db.tx() as c:
            c.execute("INSERT INTO autopilot_runs (profile_id, started_at, finished_at, trigger, engine, run_id, queued,"
                      " brief, notified) VALUES (?,?,?,?,?,?,?,?,?)",
                      (p["id"], db.now(), db.now(), "instant", "rules", run_id, len(done),
                       "⚡ Just posted:\n" + "\n".join(f"• {j['company']} · {j['title']} ({j['score']}%)" for j in done),
                       int(not _in_quiet(cfg))))
        if not _in_quiet(cfg):
            _notify(p["id"], [{"job_id": j["id"], "company": j["company"], "title": j["title"], "url": j["url"],
                               "why": "just posted"} for j in done], cfg)


def weekly_recap(pid: int) -> list[str]:
    w = week_progress(pid)
    since = w["since"]
    replies = db.rows("SELECT status, COUNT(*) AS n FROM email_events WHERE profile_id=? AND received_at >= ? AND"
                      " status IN ('oa', 'interviewing', 'offer', 'rejected') AND ignored=0 GROUP BY status", (pid, since))
    by = {r["status"]: r["n"] for r in replies}
    prepared = db.row("SELECT COUNT(*) AS n FROM ready_queue WHERE profile_id=? AND created_at >= ?", (pid, since))["n"]
    lines = [f"Applied: {w['applied']}" + (f" of your goal of {w['goal']}" if w["goal"] else ""),
             f"Prepared by Autopilot: {prepared} ({w['ready']} still ready)"]
    if by:
        lines.append("Replies: " + ", ".join(f"{n} {'OA' if s == 'oa' else s}" for s, n in by.items()))
    return lines


def tick() -> None:
    """Every few minutes: today's scheduled pass, alerts held for quiet hours, and the Sunday recap."""
    now = datetime.now()
    try:
        agent.fire_reminders()  # reminders set in the chat ring whether or not Autopilot is on
    except Exception as e:
        log.warning("reminders failed: %s", e)
    for p in store.list_profiles():
        pid = p["id"]
        cfg = {**DEFAULTS, **((p.get("prefs") or {}).get("autopilot") or {})}
        if cfg.get("prep", True) and ai.available():
            try:
                agent.prep_pending(pid)  # an interview / OA that just came in gets its prep sheet, Autopilot on or off
            except Exception as e:
                log.warning("prep sheets failed: %s", e)
        if not cfg.get("on"):
            continue
        if not _in_quiet(cfg, now):
            for r in db.rows("SELECT id, run_id FROM autopilot_runs WHERE profile_id=? AND notified=0 AND finished_at > ?",
                             (pid, db.now() - 86400)):
                queued = db.rows("SELECT q.job_id, q.why, j.company, j.title, j.url FROM ready_queue q LEFT JOIN jobs j ON"
                                 " j.id=q.job_id WHERE q.profile_id=? AND q.run_id=? AND q.status IN ('ready', 'opened')",
                                 (pid, r["run_id"]))
                with db.tx() as c:
                    c.execute("UPDATE autopilot_runs SET notified=1 WHERE id=?", (r["id"],))
                _notify(pid, queued, cfg)
            week = now.strftime("%G-%V")
            if now.weekday() == 6 and now.hour >= 18 and db.meta_get(f"recap:{pid}") != week:
                db.meta_set(f"recap:{pid}", week)
                try:
                    notify.send_text((p.get("prefs") or {}).get("notify") or {}, f"📊 Your week · {p['name']}",
                                     weekly_recap(pid))
                except Exception as e:
                    log.warning("weekly recap failed: %s", e)
        if state(pid).get("running") or paused(cfg) or now.weekday() not in cfg.get("days", DEFAULTS["days"]):
            continue
        hh, mm = (int(x) for x in (_hhmm(cfg.get("time")) or "08:00").split(":"))
        if (now.hour, now.minute) < (hh, mm):
            continue
        last = db.row("SELECT started_at FROM autopilot_runs WHERE profile_id=? AND trigger='schedule' ORDER BY id DESC"
                      " LIMIT 1", (pid,))
        if last and datetime.fromtimestamp(last["started_at"]).date() == now.date():
            continue
        threading.Thread(target=run, args=(pid, "schedule"), daemon=True, name=f"autopilot-{pid}").start()
