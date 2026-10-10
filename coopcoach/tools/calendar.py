"""Tools about events and time: finding and saving events, event sources, reminders."""

import logging
import re
import time

from .. import db, events, store
from .common import _epoch

log = logging.getLogger(__name__)


def find_events(pid: int, query: str = "", region: str = "", kind: str = "", days: int = 30, saved_only: bool = False,
                limit: int = 6) -> dict:
    evs = events.for_person(pid, max(1, min(days, 90)))
    q, reg = query.lower(), region.lower()
    kinds = [k for k in re.split(r"[,\s/|]+|\bor\b|\band\b", kind.lower()) if k]  # "hackathon,info_session"
    evs = [e for e in evs if (not q or q in f"{e['title']} {e.get('location') or ''} {e.get('description') or ''}".lower())
           and (not reg or reg in (e.get("region") or "").lower() or reg in (e.get("location") or "").lower())
           and (not kinds or any(k.rstrip("s") in e["kind"] for k in kinds)) and (not saved_only or e["mark"])]
    picked = evs[:max(1, min(limit, 8))]
    brief = [{"event_id": e["id"], "title": e["title"], "when": time.strftime("%a %b %d %H:%M", time.localtime(e["start_ts"])),
              "where": e.get("region"), "kind": e["kind"], "saved": e["mark"],
              **({"major_hackathon": e["major"]} if e.get("major") else {})} for e in picked]
    return {"total": len(evs), "events": brief, "_card": {"type": "events", "events": picked}}


def save_event(pid: int, event_id: str, mark: str = "interested") -> dict:
    from .. import server
    mark = mark if mark in ("interested", "going") else None
    before = (db.row("SELECT mark FROM event_marks WHERE profile_id=? AND event_id=?", (pid, event_id)) or {}).get("mark")
    server.mark_event(event_id, {"profile_id": pid, "mark": mark})
    return {"ok": True, "event_id": event_id, "mark": mark or "removed",
            "_undo": {"tool": "save_event", "args": {"event_id": event_id, "mark": before or "none"}}}


def _event_regions(pid: int) -> set[str]:
    """Where they'd go to events: the regions picked on the Events tab, else their cities, school and Online."""
    p = store.get_profile(pid)
    picked = p["prefs"].get("event_regions") or []
    if picked:
        return set(picked)
    places = [*(p["prefs"].get("locations") or []), p["profile"].get("location") or "", p["profile"].get("university") or ""]
    mine = {events.event_region(x) for x in places if x} - {"Unknown", "Elsewhere"}
    return mine.union(*[events.NEARBY.get(r, set()) for r in mine]) | {"Online"}


def add_event_source(pid: int, url: str) -> dict:
    """Add an events calendar to the Events tab: any .ics / webcal link, a Luma city or calendar, a Meetup search,
    an Eventbrite browse page, or a university's Localist / LiveWhale calendar."""
    from fastapi import HTTPException

    from .. import server
    try:
        out = server.add_feed({"url": url})
    except HTTPException as e:
        return {"error": e.detail}
    f = db.row("SELECT id, label FROM event_feeds WHERE url=?", (url.strip(),))
    return {"ok": True, "found": out.get("found"), "source": (f or {}).get("label") or url,
            "_undo": {"tool": "remove_event_source", "args": {"feed_id": f["id"]}} if f else None}


def remove_event_source(pid: int, feed_id: int) -> dict:
    from .. import server
    server.delete_feed(feed_id)
    return {"ok": True}


def remind_me(pid: int, title: str, when: str, job_id: str = "") -> dict:
    """A reminder at a time: it goes on their calendar link and pings them (notification / their alert channels)."""
    due = _epoch(when)
    if not title or not due:
        return {"error": "need what to remind about and when (local ISO time, e.g. 2026-10-16T09:00)"}
    if due < db.now() - 60:
        return {"error": "that time has passed"}
    with db.tx() as c:
        rid = c.execute("INSERT INTO reminders (profile_id, title, due_at, job_id, created_at) VALUES (?,?,?,?,?)",
                        (pid, title.strip()[:200], due, job_id or None, db.now())).lastrowid
    return {"ok": True, "id": rid, "title": title, "at": time.strftime("%a %b %d, %-I:%M %p", time.localtime(due)),
            "_undo": {"tool": "cancel_reminder", "args": {"reminder_id": rid}}}


def reminders(pid: int) -> dict:
    """Their upcoming reminders."""
    rows = db.rows("SELECT id, title, due_at, job_id FROM reminders WHERE profile_id=? AND done=0 AND due_at > ?"
                   " ORDER BY due_at LIMIT 20", (pid, db.now() - 86400))
    return {"reminders": [{**r, "at": time.strftime("%a %b %d, %-I:%M %p", time.localtime(r["due_at"]))} for r in rows]}


def cancel_reminder(pid: int, reminder_id: int) -> dict:
    with db.tx() as c:
        n = c.execute("DELETE FROM reminders WHERE profile_id=? AND id=?", (pid, reminder_id)).rowcount
    return {"ok": True} if n else {"error": "no such reminder"}


def fire_reminders() -> int:
    """Called every few minutes: ping reminders whose time has come (a Mac notification plus their alert channels)."""
    import subprocess
    import sys

    from .. import notify
    due = db.rows("SELECT r.*, p.prefs_json FROM reminders r JOIN profiles p ON p.id=r.profile_id WHERE r.notified=0"
                  " AND r.done=0 AND r.due_at <= ? AND r.due_at > ?", (db.now(), db.now() - 2 * 86400))
    for r in due:
        with db.tx() as c:
            c.execute("UPDATE reminders SET notified=1 WHERE id=?", (r["id"],))
        if sys.platform == "darwin":
            msg = r["title"].replace("\\", "").replace('"', "'")
            subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Co-op Coach reminder"'],
                           check=False, timeout=10)
        try:
            notify.send_text((db.loads(r["prefs_json"], {}) or {}).get("notify") or {}, "⏰ Co-op Coach reminder",
                             [r["title"]])
        except Exception as e:
            log.warning("reminder alert failed: %s", e)
    return len(due)
