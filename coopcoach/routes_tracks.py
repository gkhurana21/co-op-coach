"""Jobs → tracks: the ＋ Tracks list, a track's page (header, companies, market), pins and follows.

A niche track's firm list is edited through the niche routes (/api/niches/{nid}/firms, /find, /reminders), so the
track page and the chat tools change the same list. A track's postings are /api/jobs?track=...
"""

from fastapi import APIRouter, Body, HTTPException

from . import market, tracks
from .routes_match import _ok, _pid

router = APIRouter()


def _track(pid: int, tid: str) -> dict:
    t = tracks.get(pid, tid)
    if not t:
        raise HTTPException(404, "no such track")
    return t


@router.get("/api/interns")
def hires_interns(profile_id: int, q: str = ""):
    """Does a company hire interns (Jobs → Tracks, and Jarvis's hires_interns)."""
    from . import interns
    _pid(profile_id)
    out = interns.lookup(q)
    if out.get("error"):  # nothing typed yet: a hint, not a failure
        return {"verdict": "ask", "answer": out["error"], "open_now": [], "seasons": [], "company": "", "key": ""}
    return out


@router.get("/api/tracks")
def browse(profile_id: int):
    return tracks.browse(_pid(profile_id))


@router.get("/api/tracks/bar")
def bar(profile_id: int):
    """The tracks pinned to this person's Jobs bar."""
    pid = _pid(profile_id)
    return {"pins": tracks.bar(pid), "max": tracks.MAX_PINS}


@router.put("/api/tracks/pins")
def set_pins(body: dict = Body(...)):
    pins = body.get("pins")
    if not isinstance(pins, list):
        raise HTTPException(400, "pins must be a list of track ids")
    return _ok(tracks.set_pins(_pid(body.get("profile_id")), [str(x)[:120] for x in pins]))


@router.post("/api/tracks")
def create(body: dict = Body(...)):
    return _ok(tracks.create(_pid(body.get("profile_id")), body.get("name") or ""))


@router.get("/api/tracks/{tid}")
def header(tid: str, profile_id: int):
    pid = _pid(profile_id)
    _track(pid, tid)
    return tracks.header(pid, tid)


@router.delete("/api/tracks/{tid}")
def delete(tid: str, profile_id: int):
    return _ok(tracks.delete(_pid(profile_id), tid))


@router.put("/api/tracks/{tid}/pin")
def pin(tid: str, body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    _track(pid, tid)
    return _ok(tracks.pin(pid, tid, body.get("on", True) is not False))


@router.put("/api/tracks/{tid}/follow")
def follow(tid: str, body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    _track(pid, tid)
    return _ok(tracks.follow(pid, tid, body.get("on", True) is not False))


@router.get("/api/tracks/{tid}/companies")
def companies(tid: str, profile_id: int, segment: str = "", q: str = "", chip: str = "", offset: int = 0):
    pid = _pid(profile_id)
    _track(pid, tid)
    return tracks.companies(pid, tid, segment[:60], q[:80], chip if chip in tracks.CHIPS else "", max(0, offset))


@router.get("/api/tracks/{tid}/market")
def get_market(tid: str, profile_id: int):
    pid = _pid(profile_id)
    _track(pid, tid)
    return market.track_market(pid, tid)
