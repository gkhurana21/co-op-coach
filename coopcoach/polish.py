"""Finishing prepared applications in the background. An application goes on Ready to apply at once with what needs no AI; this
then writes the AI letter and answers, tailors the resume and renders the files. The work is recorded on the Ready row itself,
so a restart picks up where it stopped, and a failure leaves the application Ready with what it had.
"""

import logging
import queue
import threading
import time

from . import ai, db

log = logging.getLogger(__name__)
WORKERS = 3  # at once: each is mostly waiting on an AI that can take a while
MAX_TRIES = 3
RETRY_WAIT = 20  # seconds, times the number of tries so far
_todo: queue.Queue = queue.Queue()
_started = False
_start_lock = threading.Lock()
_busy = 0
_busy_lock = threading.Lock()


def enqueue(pid: int, job_id: str) -> None:
    global _started
    with _start_lock:
        if not _started:
            _started = True
            for i in range(WORKERS):
                threading.Thread(target=_worker, daemon=True, name=f"polish-{i}").start()
    _todo.put((pid, job_id))


def resume() -> int:
    """After a start: anything a restart interrupted goes back in the queue."""
    rows = db.rows("SELECT profile_id, job_id FROM ready_queue WHERE polish IN ('pending', 'running') AND status IN ('ready', 'opened')")
    for r in rows:
        enqueue(r["profile_id"], r["job_id"])
    return len(rows)


def idle() -> bool:
    return _todo.empty() and _busy == 0


def wait(timeout: float = 60) -> bool:
    """Until nothing's left (tests, and anything that needs the finished version)."""
    end = time.time() + timeout
    while time.time() < end:
        if idle():
            return True
        time.sleep(0.05)
    return idle()


def _worker() -> None:
    global _busy
    while True:
        pid, job_id = _todo.get()
        with _busy_lock:
            _busy += 1
        try:
            _one(pid, job_id)
        except Exception as e:  # (a failure in the bookkeeping itself mustn't take the worker with it)
            log.warning("polishing %s failed: %s", job_id, e)
        finally:
            with _busy_lock:
                _busy -= 1


def _live(pid: int, job_id: str) -> dict | None:
    """The row, while it's still worth finishing: it may have been applied to, skipped or undone meanwhile."""
    r = db.row("SELECT status, source, kit, polish, polish_tries FROM ready_queue WHERE profile_id=? AND job_id=?", (pid, job_id))
    return r if r and r["status"] in ("ready", "opened") else None


def _mark(pid: int, job_id: str, state: str, tries: int | None = None) -> None:
    with db.tx() as c:
        if tries is None:
            c.execute("UPDATE ready_queue SET polish=? WHERE profile_id=? AND job_id=?", (state, pid, job_id))
        else:
            c.execute("UPDATE ready_queue SET polish=?, polish_tries=? WHERE profile_id=? AND job_id=?", (state, tries, pid, job_id))


def _one(pid: int, job_id: str) -> None:
    r = _live(pid, job_id)
    if not r or r["polish"] not in ("pending", "running"):
        return
    _mark(pid, job_id, "running")
    try:
        _finish(pid, job_id, r)
        _mark(pid, job_id, "done")
    except Exception as e:
        tries = (r["polish_tries"] or 0) + 1
        log.warning("polishing %s, try %d: %s", job_id, tries, e)
        if tries >= MAX_TRIES:
            _mark(pid, job_id, "failed", tries)  # it stays Ready, with what it had
            return
        _mark(pid, job_id, "pending", tries)
        t = threading.Timer(RETRY_WAIT * tries, enqueue, args=(pid, job_id))
        t.daemon = True
        t.start()


def _upgrade_letter(pid: int, job_id: str, fmt: str | None) -> None:
    """The AI letter replaces the quick one, and only that: a letter you've edited, or an AI one, is yours to keep."""
    from . import companies, coverletter, jobdetail, routes_docs, store
    if not ai.available():
        return
    doc = db.row("SELECT id, content, generated_by, format_id FROM documents WHERE profile_id=? AND"
                 " kind='cover_letter' AND job_id=? ORDER BY updated_at DESC LIMIT 1", (pid, job_id))
    if doc and doc["generated_by"] != "rules":
        return
    p = store.get_profile(pid)
    job = routes_docs._job(job_id, pid)
    fmt_row = coverletter.get_format(pid, (doc or {}).get("format_id") or fmt or p["prefs"].get("letter_format", "classic"))
    yc = companies.yc_lookup(job["company"])
    text = ai.write_cover_letter(fmt_row, routes_docs.master(pid) or {}, p["profile"], job, jobdetail.ensure_description(job_id),
                                 f"YC {yc['batch']}: {yc.get('one_liner')}" if yc else "")
    now = db.row("SELECT content FROM documents WHERE id=?", (doc["id"],)) if doc else None
    if doc and (not now or now["content"] != doc["content"]):  # edited while the AI was writing
        return
    coverletter.save_letter(pid, job_id, f"{job['company']} · {job['title']}", fmt_row["id"], text, ai.provider())


def _finish(pid: int, job_id: str, row: dict) -> None:
    from . import apply, autopilot, routes_docs
    cfg = autopilot.settings(pid) if row["source"] == "autopilot" else {}
    letter = cfg.get("letter", True)
    fmt = cfg.get("letter_style") or None
    if letter:
        _upgrade_letter(pid, job_id, fmt)
    if not _live(pid, job_id):
        return
    if cfg.get("tailor") and ai.available():
        routes_docs.tailor({"profile_id": pid, "job_id": job_id})
    kit = apply.build_kit(pid, job_id, letter=letter, letter_fmt=fmt, ai_answers=cfg.get("ai_answers", True))
    if kit.get("error"):
        raise RuntimeError(kit["error"])
    if cfg.get("prerender"):
        kit["local_files"] = apply._write_files(pid, kit)
    with db.tx() as c:
        c.execute("UPDATE ready_queue SET kit=? WHERE profile_id=? AND job_id=? AND status IN ('ready', 'opened')",
                  (db.dumps(kit), pid, job_id))
