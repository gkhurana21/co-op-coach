"""MATCH routes: your market, the resume against it, niches (the firm lists behind employer tracks), the Path."""

from fastapi import APIRouter, Body, HTTPException

from . import agent, market, niches, path, startups, store
from .sources import BlockedURL

router = APIRouter()


def _pid(pid) -> int:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise HTTPException(400, "profile_id is required")
    if not store.get_profile(pid):
        raise HTTPException(404, "no such person")
    return pid


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise HTTPException(400, "that should be a number")


def _ok(out: dict) -> dict:
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


# --- M + A ---------------------------------------------------------------------------------------

@router.get("/api/market")
def get_market(profile_id: int, category: str = "", n: int = market.SAMPLE):
    return _ok(market.summary(_pid(profile_id), category, max(10, min(n, 60))))


@router.post("/api/market/fit")
def market_fit(body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    try:
        n = int(body.get("n") or market.SAMPLE)
    except (TypeError, ValueError):
        raise HTTPException(400, "n is how many postings to read: a whole number")
    return _ok(market.fit(pid, body.get("resume"), body.get("category") or "", max(10, min(n, 60)),
                          str(body.get("track") or "")[:120]))


@router.post("/api/market/add-skill")
def market_add_skill(body: dict = Body(...)):
    _pid(body.get("profile_id"))
    if not isinstance(body.get("resume"), dict):
        raise HTTPException(400, "resume is required")
    r, line = market.add_skill(body["resume"], body.get("text") or "", body.get("skill") or "")
    return {"resume": r, "line": line}


@router.post("/api/market/plan")
def market_plan(body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    if not body.get("skill"):
        raise HTTPException(400, "which skill?")
    return _ok(market.project_plan(pid, body["skill"], body.get("category") or "", body.get("ai", True) is not False))


# --- T: startups in Waterloo, Toronto and Canada -------------------------------------------------

@router.get("/api/startups")
def get_startups(profile_id: int, area: str = "waterloo", yc: bool = False, known: bool = False, hiring: bool = False,
                 q: str = "", min_funding: int = 0):
    if area not in startups.AREAS:
        raise HTTPException(400, f"area is one of {', '.join(startups.AREAS)}")
    return startups.feed(_pid(profile_id), area, yc, known, hiring, q[:80], max(0, min_funding))


@router.post("/api/startups/watch")
def watch_startup(body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    if not (body.get("name") or "").strip():
        raise HTTPException(400, "which startup?")
    return _ok(agent.call(pid, "watch_startup", {"name": body["name"]}, source="app"))


# --- T: niches -----------------------------------------------------------------------------------

@router.get("/api/niches")
def list_niches(profile_id: int):
    pid = _pid(profile_id)
    return {"niches": niches.summary_list(pid), "starters": niches.starters(), "following": niches.followed(pid)}


@router.post("/api/niches")
def create_niche(body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    return _ok(niches.create(pid, body.get("name") or "", body.get("keywords") or None, body.get("category") or "",
                             body.get("blurb") or ""))


@router.get("/api/niches/{nid}")
def get_niche(nid: str, profile_id: int):
    d = niches.detail(_pid(profile_id), nid)
    if not d:
        raise HTTPException(404, "no such niche")
    return d


@router.delete("/api/niches/{nid}")
def delete_niche(nid: str, profile_id: int):
    return _ok(niches.delete(_pid(profile_id), nid))


@router.put("/api/niches/{nid}/follow")
def follow_niche(nid: str, body: dict = Body(...)):
    return _ok(niches.follow(_pid(body.get("profile_id")), nid, body.get("on", True) is not False))


@router.post("/api/niches/{nid}/find")
def find_firms(nid: str, body: dict = Body(...)):
    return _ok(niches.find_more(_pid(body.get("profile_id")), nid, body.get("ai", True) is not False))


@router.post("/api/niches/{nid}/firms")
def add_firm(nid: str, body: dict = Body(...)):
    try:
        return _ok(niches.add_firm(_pid(body.get("profile_id")), nid, body.get("name") or "", body.get("url") or "",
                                   body.get("segment") or ""))
    except BlockedURL as e:
        raise HTTPException(400, str(e))


@router.put("/api/niches/{nid}/firms/{key}")
def set_firm(nid: str, key: str, body: dict = Body(...)):
    return _ok(niches.set_firm(_pid(body.get("profile_id")), nid, key, body.get("status") or ""))


@router.post("/api/niches/{nid}/reminders")
def niche_reminders(nid: str, body: dict = Body(...)):
    months = [int(m) for m in body.get("months") or [] if str(m).isdigit()]
    return _ok(niches.window_reminders(_pid(body.get("profile_id")), nid, months or None))


# --- the Path: XP, streaks, levels, the LeetCode ladder ------------------------------------------

@router.get("/api/path")
def get_path(profile_id: int):
    pid = _pid(profile_id)
    path.first_visit(pid)
    return path.summary(pid)


@router.post("/api/path/goal")
def path_goal(body: dict = Body(...)):
    try:
        return _ok(path.set_goal(_pid(body.get("profile_id")), int(body.get("goal") or 0)))
    except (TypeError, ValueError):
        raise HTTPException(400, "goal is XP a day: a whole number")


@router.post("/api/path/seen")
def path_seen(body: dict = Body(...)):
    path.seen(_pid(body.get("profile_id")), [str(k) for k in body.get("keys") or []][:50])
    return {"ok": True}


@router.get("/api/path/ladder")
def path_ladder(profile_id: int):
    """The LeetCode ladder: Easy, then Medium, then Hard, with each problem's attempts, reviews and stars."""
    return path.ladder(_pid(profile_id))


@router.post("/api/path/attempt")
def path_attempt(body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    try:
        seconds = int(body.get("seconds") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "seconds is a whole number")
    return _ok(agent.call(pid, "record_problem", {"problem": str(body.get("slug") or ""), "result": str(
        body.get("result") or ""), "minutes": round(seconds / 60, 2), "notes": str(body.get("notes") or "")},
        source="app"))


@router.post("/api/path/star")
def path_star(body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    return _ok(path.star(pid, str(body.get("slug") or ""), str(body.get("kind") or ""), str(body.get("tag") or ""),
                         str(body.get("label") or ""), body.get("on", True) is not False))


@router.get("/api/path/test")
def path_test_now(profile_id: int, check: bool = False):
    return {"test": path.current_test(_pid(profile_id), check)}


@router.post("/api/path/test/start")
def path_test_start(body: dict = Body(...)):
    return _ok(path.start_test(_pid(body.get("profile_id")), str(body.get("kind") or ""), str(body.get("scope") or "")))


@router.post("/api/path/test/finish")
def path_test_finish(body: dict = Body(...)):
    return _ok(path.finish_test(_pid(body.get("profile_id")), _int(body.get("test_id")), body.get("results") or {}))


@router.post("/api/path/test/give-up")
def path_test_give_up(body: dict = Body(...)):
    return _ok(path.give_up_test(_pid(body.get("profile_id")), _int(body.get("test_id"))))


@router.post("/api/path/leetcode")
def path_leetcode(body: dict = Body(...)):
    from . import leetcode
    return _ok(leetcode.link(_pid(body.get("profile_id")), str(body.get("username") or "")))


@router.post("/api/path/solved")
def path_solved(body: dict = Body(...)):
    if isinstance(body.get("slugs"), list):
        return _ok(path.mark_solved_many(_pid(body.get("profile_id")), body["slugs"]))
    return _ok(path.mark_solved(_pid(body.get("profile_id")), str(body.get("slug") or ""), body.get("solved", True)
                                is not False))
