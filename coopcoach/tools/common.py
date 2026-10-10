"""Helpers the tools share: postings and applications by id, times, documents."""

import logging

from .. import db, store

log = logging.getLogger(__name__)


STATUSES = ["saved", "applied", "oa", "interviewing", "offer", "rejected"]


def _job_brief(j: dict) -> dict:
    return {"job_id": j["id"], "company": j["company"], "title": j["title"], "score": j["score"],
            "where": ", ".join(j["locations"][:2]), "eligibility": j.get("eligibility", "ok"),
            "terms": j.get("terms", [])[:2], "status": j.get("app_status")}


def _find_job(pid: int, job_id: str) -> dict | None:
    return next((j for j in store.scored_jobs(pid) if job_id in j["alt_ids"] or j["id"] == job_id), None)


def _app_job(pid: int, job_id: str) -> dict | None:
    """An application plus its posting (manual applications have no posting row)."""
    j = _find_job(pid, job_id)
    a = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id IN (?, ?)", (pid, job_id, (j or {}).get("app_id") or job_id))
    if not j and not a:
        return None
    return {"job_id": (a or {}).get("job_id") or j["id"], "company": (j or a)["company"], "title": (j or a)["title"],
            "status": (a or {}).get("status"), "since": (a or {}).get("applied_at") or (a or {}).get("updated_at"),
            "description_id": j["id"] if j else None}


def _resume_text(pid: int) -> str:
    from .profile import my_resume  # profile builds on these helpers
    out = my_resume(pid)
    return out.get("resume", "")


def _epoch(v) -> int | None:
    """'2026-10-14T14:00' (local time), an ISO time with an offset, or epoch seconds -> epoch seconds."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        return int(v)
    from datetime import datetime
    try:
        d = datetime.fromisoformat(str(v).strip().replace(" ", "T", 1))
    except ValueError:
        return None
    return int(d.timestamp())


def _app_row(pid: int, job_id: str) -> dict | None:
    return db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, job_id))


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception as e:
        log.warning("%s failed: %s", fn.__name__, e)


def _new_doc(pid: int, kind: str, content: str, title: str, job_id: str | None, by: str) -> int:
    with db.tx() as c:
        return c.execute("INSERT INTO documents (profile_id, kind, job_id, title, content, generated_by, created_at,"
                         " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                         (pid, kind, job_id, title, content, by, db.now(), db.now())).lastrowid


def _target(pid: int, job_id: str = "", company: str = "") -> dict:
    """A posting / application by id, or just a company name: {company, title, job_id, description_id}."""
    a = _app_job(pid, job_id) if job_id else None
    if a:
        return a
    return {"job_id": None, "company": (company or "").strip(), "title": "", "status": None, "since": None,
            "description_id": None}
