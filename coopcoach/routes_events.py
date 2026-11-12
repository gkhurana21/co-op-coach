"""Events routes: the list, saving, refreshing, sources, the calendar link (the cloud one is on Supabase) and
interview / assessment dates."""

import logging
import re
import threading

from fastapi import APIRouter, Body, HTTPException, Response

from . import db, events, store
from .sources import BlockedURL, check_public

log = logging.getLogger(__name__)
router = APIRouter()

@router.get("/api/events")
def get_events(profile_id: int, days: int = 90):
    from .server import update_prefs  # server includes this router
    feeds = db.rows("SELECT f.*, (SELECT COUNT(*) FROM events e WHERE e.feed_id=f.id AND e.start_ts >= ?) AS upcoming"
                    " FROM event_feeds f ORDER BY f.id", (db.this_hour(),))
    p = store.get_profile(profile_id) or {"prefs": {}}
    tok = p["prefs"].get("calendar_token")
    if tok and not p["prefs"].get("calendar_token_hash"):  # links made before the cloud feed existed
        import hashlib
        update_prefs(profile_id, {"calendar_token_hash": hashlib.sha256(tok.encode()).hexdigest()})
    return {"events": events.for_person(profile_id, days), "mine": _mine(profile_id, days), "feeds": feeds,
            "refreshing": _events_refreshing.is_set(),
            **(_calendar_urls(p) if p.get("id") else {"calendar_feed": "", "calendar_public": ""}),
            "filters": {k: p["prefs"].get(k) for k in ("event_regions", "event_kinds")}}


_events_refreshing = threading.Event()


def _mine(pid: int, days: int = 90) -> list[dict]:
    """Your own: interviews, assessments, coffee chats and info sessions found in email, and interview times and
    deadlines set on the tracker, for the Events list and month."""
    from . import meetings
    out = [{"id": f"item-{r['id']}", "item_id": r["id"], "kind": r["kind"], "label": r["label"], "title": r["title"],
            "start_ts": r["start_ts"] or r["due_ts"], "end_ts": r["end_ts"], "due": r["start_ts"] is None and bool(r["due_ts"]),
            "all_day": bool(r["all_day"]), "location": r["location"], "join_url": r["join_url"], "book_url": r["book_url"],
            "status": r["status"], "job_id": r["job_id"], "company": r["company"], "who": r["who"],
            "email_subject": r["email_subject"], "email_link": r["email_link"]} for r in meetings.upcoming(pid, days)]
    have = {(m["job_id"], "oa" if m["kind"] == "oa" else "interview") for m in out if m["job_id"]}
    now = db.now()
    for e in events.application_dates(pid):  # set on the tracker (or by an older version) without an item of their own
        if e["kind"] in ("interview", "oa") and not str(e["id"]).startswith("item-") and \
                (e["job_id"], e["kind"]) not in have and now - 6 * 3600 <= e["start_ts"] <= now + days * 86400:
            out.append({"id": e["id"], "item_id": None, "kind": e["kind"], "label": "Interview" if e["kind"] == "interview"
                        else "Online assessment", "title": e["title"], "start_ts": e["start_ts"], "end_ts": e["end_ts"],
                        "due": e["kind"] == "oa", "all_day": False, "location": "", "join_url": "", "book_url": "",
                        "status": "confirmed", "job_id": e["job_id"], "company": None, "who": None, "email_subject": None,
                        "email_link": e.get("url") or None})
    return out


@router.post("/api/calendar")
def add_calendar_block(body: dict = Body(...)):
    """Your own block on the calendar (Calendar → Yours: "OA prep", "Coffee with Sam"), with a reminder on the link."""
    from . import meetings
    pid = int(body.get("profile_id") or 0)
    if not store.get_profile(pid):
        raise HTTPException(404, "no such person")
    title = str(body.get("title") or "").strip()[:200]
    try:
        start, end = int(body["start_ts"]), int(body.get("end_ts") or 0) or int(body["start_ts"]) + 3600
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "a block needs a start time")
    if not title or end <= start or end - start > 16 * 3600:
        raise HTTPException(400, "give it a name and an end after its start (up to 16 hours)")
    ids = meetings.save(pid, [{"kind": "block", "title": title, "start_ts": start, "end_ts": end, "status": "confirmed"}],
                        source="you")
    return {"ok": True, "item_id": ids[0] if ids else None}


@router.delete("/api/calendar/{item_id}")
def delete_calendar_item(item_id: int, profile_id: int):
    """Take something found in email off the calendar."""
    from . import meetings
    if not meetings.remove(profile_id, item_id):
        raise HTTPException(404, "not on your calendar")
    return {"ok": True}


_refresh = {"done": 0, "total": 0, "new": 0, "at": 0}


@router.post("/api/events/refresh")
def refresh_events():
    """Events only, instead of waiting for the 3-hour job sync: the sources near you first, each saved as it arrives,
    and none read in the last 15 minutes. Ask GET /api/events/refresh how far along it is."""
    if _events_refreshing.is_set():
        return {"started": False, **_refresh}
    _events_refreshing.set()
    _refresh.update(done=0, total=0, new=0, at=db.now())

    def run():
        try:
            events.sync_feeds(min_age=events.RECENT, progress=lambda done, total, new: _refresh.update(done=done, total=total, new=new))
        except Exception as e:
            log.warning("refreshing events failed: %s", e)
        finally:
            _events_refreshing.clear()

    threading.Thread(target=run, daemon=True, name="events-refresh").start()
    return {"started": True, **_refresh}


@router.get("/api/events/refresh")
def refresh_status():
    """How far a refresh has got: cheap enough to ask every second or two."""
    return {"refreshing": _events_refreshing.is_set(), **_refresh}


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
    saved = [{**e, "alarm": 1440} for e in events.for_person(pid, 180) if e["mark"]]  # a reminder the day before
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
    try:
        check_public(url.replace("webcal://", "https://"))
    except BlockedURL as e:
        raise HTTPException(400, str(e))
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


# --- hackathon packages: apply to attend, and the Devpost write-up afterwards (hackathons.py) ------------------

def _hack(out: dict) -> dict:
    if out.get("error"):
        raise HTTPException(400 if "resume" in out["error"] or "repo" in out["error"] or "GitHub" in out["error"]
                            else 404, out["error"])
    return out


@router.get("/api/hackathons/{key}/apply")
def hackathon_apply(key: str, profile_id: int, redraft: bool = False):
    from . import hackathons
    return _hack(hackathons.app_kit(profile_id, key, redraft=redraft))


@router.put("/api/hackathons/{key}/apply")
def hackathon_apply_save(key: str, body: dict = Body(...)):
    from . import hackathons
    return _hack(hackathons.save_answers(body.get("profile_id"), key, body.get("answers") or {}, body.get("prefs")))


@router.get("/api/hackathons/{key}/apply/download")
def hackathon_apply_download(key: str, profile_id: int):
    from . import hackathons, render, routes_docs
    kit = _hack(hackathons.app_kit(profile_id, key))
    r = routes_docs.master(profile_id)
    tpl = store.get_profile(profile_id)["prefs"].get("resume_template", "jake")
    who = render.safe_name((r or {}).get("header", {}).get("name") or kit["basics"]["full_name"] or "Resume")
    name = render.safe_name(kit["hackathon"]["name"])
    files = {f"{who}_Resume.pdf": render.pdf(render.resume_html(r, tpl)), f"{name}_answers.md": hackathons.answers_markdown(kit)}
    return Response(render.package_zip(files), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{render.safe_name(who, name, "application")}.zip"'})


@router.post("/api/hackathons/{key}/apply/fill")
def hackathon_apply_fill(key: str, body: dict = Body(...)):
    """Open the hackathon's site in Chrome and fill the application once the student is on it. Never submits."""
    from . import apply, hackathons
    kit = _hack(hackathons.app_kit(body.get("profile_id"), key))
    if not kit["job"]["apply_url"]:
        raise HTTPException(400, "this hackathon has no site yet")
    apply.fill(body["profile_id"], kit["job"]["id"], kit=kit)
    return {"ok": True}


@router.post("/api/hackathons/{key}/track")
def hackathon_track(key: str, body: dict = Body(...)):
    from . import hackathons
    out = _hack(hackathons.track(body.get("profile_id"), key, body.get("status", "applied")))
    return {k: v for k, v in out.items() if not k.startswith("_")}


@router.get("/api/hackathons/{key}/submission")
def hackathon_submission(key: str, profile_id: int):
    """The write-up saved for this hackathon, if one was made."""
    from . import hackathons
    h = hackathons.find(profile_id, key)
    saved = hackathons._saved(profile_id, "devpost", h["key"]) if h else None
    return {"hackathon": h, "submission": saved}


@router.post("/api/hackathons/{key}/submission")
def hackathon_submission_make(key: str, body: dict = Body(...)):
    from . import hackathons
    args = {k: str(body.get(k) or "") for k in ("project", "built", "challenge", "learned", "mine", "prize", "video",
                                                "devpost_url")}
    return _hack(hackathons.submission(body.get("profile_id"), "" if key == "none" else key, body.get("repo") or "",
                                       **args))


@router.put("/api/devpost/{doc_id}")
def devpost_save(doc_id: int, body: dict = Body(...)):
    from . import hackathons
    return _hack(hackathons.save_submission(body.get("profile_id"), doc_id, body.get("sections") or {},
                                            body.get("tagline"), body.get("project")))


@router.get("/api/devpost/{doc_id}/download")
def devpost_download(doc_id: int, profile_id: int):
    from . import render
    d = db.row("SELECT content FROM documents WHERE id=? AND profile_id=? AND kind='devpost'", (doc_id, profile_id))
    if not d:
        raise HTTPException(404, "that write-up is gone")
    s = db.loads(d["content"], {})
    text = "\n".join([f"# {s['project']}", "", f"> {s.get('tagline', '')}", "", s.get("markdown", ""), "",
                      "## Built with", ", ".join(s.get("built_with") or []), "", "## Try it out",
                      *[f"- {u}" for u in s.get("links") or []], "", "## Demo video script", "```",
                      s.get("video_script", ""), "```"])
    return Response(text, media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="{render.safe_name(s["project"], "Devpost")}.md"'})


@router.post("/api/devpost/{doc_id}/resume")
def devpost_to_resume(doc_id: int, body: dict = Body(...)):
    from . import hackathons
    out = _hack(hackathons.add_to_resume(body.get("profile_id"), doc_id, body.get("entry")))
    return {k: v for k, v in out.items() if not k.startswith("_")}


@router.post("/api/devpost/restore")
def devpost_restore(body: dict = Body(...)):
    """Undo "Add to my resume": the resume as it was just before."""
    from .tools.profile import restore_resume
    out = restore_resume(body.get("profile_id"), int(body.get("backup_id") or 0))
    if out.get("error"):
        raise HTTPException(404, out["error"])
    return out


@router.post("/api/devpost/{doc_id}/fill")
def devpost_fill(doc_id: int, body: dict = Body(...)):
    """Open Devpost (the student signs in themselves) and fill the submission's pages as they go. Never submits."""
    from . import apply, hackathons
    pid = body.get("profile_id")
    d = db.row("SELECT content FROM documents WHERE id=? AND profile_id=? AND kind='devpost'", (doc_id, pid))
    if not d:
        raise HTTPException(404, "that write-up is gone")
    s = db.loads(d["content"], {})
    url = (body.get("url") or s.get("devpost_url") or ((s.get("hackathon") or {}).get("url") or "")).strip()
    if not url.startswith("https://") or "devpost.com" not in url.split("/")[2]:
        url = "https://devpost.com/software/new"
    kit = {"job": {"id": f"devpost:{doc_id}", "company": "Devpost", "title": s["project"], "url": url,
                   "apply_url": url, "ats": "other"}, "basics": {}, "questions": [], "files": {}, "letter_id": None}
    apply.fill(pid, kit["job"]["id"], kit=kit, answers=hackathons.devpost_answers(s))
    return {"ok": True, "url": url}
