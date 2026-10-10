"""Events routes: the list, saving, refreshing, sources, the calendar link (the cloud one is on Supabase) and
interview / assessment dates."""

import re
import threading

from fastapi import APIRouter, Body, HTTPException, Response

from . import db, events, store

router = APIRouter()

@router.get("/api/events")
def get_events(profile_id: int, days: int = 90):
    from .server import update_prefs  # server includes this router
    feeds = db.rows("SELECT f.*, (SELECT COUNT(*) FROM events e WHERE e.feed_id=f.id AND e.start_ts >= ?) AS upcoming"
                    " FROM event_feeds f ORDER BY f.id", (db.now(),))
    p = store.get_profile(profile_id) or {"prefs": {}}
    tok = p["prefs"].get("calendar_token")
    if tok and not p["prefs"].get("calendar_token_hash"):  # links made before the cloud feed existed
        import hashlib
        update_prefs(profile_id, {"calendar_token_hash": hashlib.sha256(tok.encode()).hexdigest()})
    return {"events": events.for_person(profile_id, days), "feeds": feeds, "refreshing": _events_refreshing.is_set(),
            **(_calendar_urls(p) if p.get("id") else {"calendar_feed": "", "calendar_public": ""}),
            "filters": {k: p["prefs"].get(k) for k in ("event_regions", "event_kinds")}}


_events_refreshing = threading.Event()


@router.post("/api/events/refresh")
def refresh_events():
    """Events only (a few seconds), instead of waiting for the 3-hour job sync."""
    if _events_refreshing.is_set():
        return {"started": False}
    _events_refreshing.set()

    def run():
        try:
            events.sync_feeds()
        finally:
            _events_refreshing.clear()

    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


@router.put("/api/events/{eid}/mark")
def mark_event(eid: str, body: dict = Body(...)):
    pid, mark = body["profile_id"], body.get("mark")
    with db.tx() as c:
        if mark in ("interested", "going"):
            c.execute("INSERT INTO event_marks (profile_id, event_id, mark, created_at) VALUES (?,?,?,?)"
                      " ON CONFLICT(profile_id, event_id) DO UPDATE SET mark=excluded.mark", (pid, eid, mark, db.now()))
        else:
            c.execute("DELETE FROM event_marks WHERE profile_id=? AND event_id=?", (pid, eid))
    return {"ok": True}


@router.get("/api/events/{eid}/ics")
def event_ics(eid: str):
    e = db.row("SELECT * FROM events WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404)
    name = re.sub(r"[^\w-]+", "-", e["title"])[:60].strip("-") or "event"
    return Response(events.ics_for([e], e["title"]), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{name}.ics"'})


def _calendar_urls(p: dict) -> dict:
    """The feed on this Mac, and (with Supabase) the one any calendar app or phone can subscribe to anytime."""
    from . import supa
    token = p["prefs"].get("calendar_token") or ""
    if not token:
        return {"calendar_feed": "", "calendar_public": ""}
    ref = supa.settings().get("ref") if supa.enabled() else None
    return {"calendar_feed": f"/api/events/feed/{token}.ics",
            "calendar_public": f"https://{ref}.supabase.co/functions/v1/calendar/{token}.ics" if ref else ""}


@router.post("/api/events/calendar-feed")
def make_calendar_feed(body: dict = Body(...)):
    """A private, unguessable address calendar apps can subscribe to (they can't unlock passcodes). `new` makes a
    fresh one, so the old link stops working. Only the token's hash is what the cloud feed looks up."""
    import hashlib
    import secrets

    from .server import _profile_or_404, update_prefs  # server includes this router
    pid = body["profile_id"]
    p = _profile_or_404(pid)
    token = p["prefs"].get("calendar_token")
    if not token or body.get("new"):
        token = secrets.token_urlsafe(24)
    update_prefs(pid, {"calendar_token": token, "calendar_token_hash": hashlib.sha256(token.encode()).hexdigest()})
    return {"url": f"/api/events/feed/{token}.ics", **_calendar_urls(store.get_profile(pid))}


@router.get("/api/events/feed/{token}.ics")
def calendar_feed(token: str):
    pid = next((p["id"] for p in store.list_profiles() if token and p["prefs"].get("calendar_token") == token), None)
    if not pid:
        raise HTTPException(404)
    saved = [e for e in events.for_person(pid, 180) if e["mark"]]
    return Response(events.ics_for(saved + events.application_dates(pid), "Co-op Coach"), media_type="text/calendar")


@router.put("/api/applications/dates")
def application_dates(body: dict = Body(...)):
    """Interview time / online-assessment deadline (epoch seconds, or null to clear): they go on the calendar."""
    vals = {k: (int(body[k]) if body.get(k) else None) for k in ("interview_at", "oa_due_at") if k in body}
    if not vals:
        raise HTTPException(400, "send interview_at and/or oa_due_at")
    with db.tx() as c:
        for k, v in vals.items():
            c.execute(f"UPDATE applications SET {k}=? WHERE profile_id=? AND job_id=?", (v, body["profile_id"], body["job_id"]))
    return {"ok": True}


@router.put("/api/event-feeds/group")
def update_feed_group(body: dict = Body(...)):
    """Turn a whole group of sources (e.g. US universities) on or off."""
    with db.tx() as c:
        c.execute("UPDATE event_feeds SET enabled=? WHERE grp=?", (int(bool(body.get("enabled"))), body.get("grp") or ""))
    return {"ok": True}


@router.put("/api/event-feeds/{fid}")
def update_feed(fid: int, body: dict = Body(...)):
    with db.tx() as c:
        if "enabled" in body:
            c.execute("UPDATE event_feeds SET enabled=? WHERE id=?", (int(bool(body["enabled"])), fid))
        if "career_only" in body:
            c.execute("UPDATE event_feeds SET career_only=? WHERE id=?", (int(bool(body["career_only"])), fid))
    return {"ok": True}


@router.post("/api/event-feeds")
def add_feed(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://", "webcal://")):
        raise HTTPException(400, "paste a calendar (.ics / webcal) link, a lu.ma page, a Meetup search, an Eventbrite browse page, devpost.com/hackathons or an MLH season page")
    url = events.resolve_feed(url)  # a university's calendar page -> its Localist / LiveWhale / .ics feed
    try:
        evs, label = events.fetch_feed(url)
    except Exception as e:
        raise HTTPException(400, f"couldn't load that feed: {e}")
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO event_feeds (url, label, career_only, created_at, grp) VALUES (?,?,?,?,?)",
                  (url, body.get("label") or label, 1 if body.get("career_only", True) else 0, db.now(), "Added by you"))
    events.sync_feeds()
    return {"ok": True, "found": len(evs)}


@router.get("/api/events/hackathons")
def hackathon_season(profile_id: int):
    """Every major hackathon in Canada and the US: dates if announced, otherwise when it usually happens."""
    return {"hackathons": events.hackathon_season(profile_id)}


@router.delete("/api/event-feeds/{fid}")
def delete_feed(fid: int):
    with db.tx() as c:
        c.execute("DELETE FROM events WHERE feed_id=?", (fid,))
        c.execute("DELETE FROM event_feeds WHERE id=?", (fid,))
    return {"ok": True}
