"""Jarvis tracks your LeetCode by itself, if you link a public LeetCode username: accepted solves on the NeetCode 150 are
logged as they happen, and a Path test can confirm what was solved inside its time.

This reads what anyone can see on a public profile: LeetCode's own site asks the same question. No password, no sign-in, and
only the last 20 accepted solves, so a check every so often is enough. LeetCode can hide these (a private profile, or an
empty list): then logging stays by hand, and nothing breaks.
"""

import logging
import re
import threading

import httpx

from . import db

log = logging.getLogger(__name__)
API = "https://leetcode.com/graphql"
QUERY = ("query r($username:String!,$limit:Int!){recentAcSubmissionList(username:$username,limit:$limit){titleSlug timestamp} "
         "matchedUser(username:$username){username}}")
CHECK_EVERY = 600  # seconds
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
_running: set = set()
_running_lock = threading.Lock()


class UnknownUser(ValueError):
    pass


def _ask(username: str) -> dict:
    r = httpx.post(API, json={"query": QUERY, "variables": {"username": username, "limit": 20}}, timeout=20, headers={
        "Referer": "https://leetcode.com", "Content-Type": "application/json", "User-Agent": "Co-op Coach"})
    r.raise_for_status()
    return r.json()


def recent_solves(username: str) -> list[dict]:
    """[{slug, at}] for this public profile's latest accepted solves, newest first."""
    d = _ask(username).get("data") or {}
    if not d.get("matchedUser"):
        raise UnknownUser(f"LeetCode has no user called {username}")
    return [{"slug": s["titleSlug"], "at": int(s["timestamp"])} for s in d.get("recentAcSubmissionList") or []]


def username(pid: int) -> str:
    return db.meta_get(f"leetcode_user:{pid}") or ""


def link(pid: int, name: str) -> dict:
    """Link a public LeetCode username, checking it exists, and log what it has solved lately."""
    name = (name or "").strip().lstrip("@").rstrip("/").rsplit("/", 1)[-1]
    if not name:
        db.meta_set(f"leetcode_user:{pid}", "")
        return {"ok": True, "linked": ""}
    if not _NAME.match(name):
        return {"error": "That doesn't look like a LeetCode username"}
    try:
        recent_solves(name)
    except UnknownUser as e:
        return {"error": str(e)}
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"Couldn't reach LeetCode just now: {str(e)[:100]}"}
    db.meta_set(f"leetcode_user:{pid}", name)
    return {"ok": True, "linked": name, **sync(pid)}


def sync(pid: int) -> dict:
    """Log any accepted solve of a NeetCode 150 problem that isn't logged yet. A solve already logged by hand around
    then isn't logged twice."""
    from . import path
    name = username(pid)
    if not name:
        return {"new": 0}
    # A failed request is still a check: don't retry it every time the open Path polls.
    db.meta_set(f"leetcode_checked:{pid}", str(db.now()))
    try:
        recent = recent_solves(name)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.info("LeetCode check failed: %s", e)
        return {"new": 0, "error": str(e)[:120]}
    known = {p["slug"]: p for t in path.coding() for p in t["problems"]}
    new = []
    for s in sorted(recent, key=lambda x: x["at"]):
        if s["slug"] not in known:
            continue
        with db.tx() as c:
            logged = c.execute("SELECT 1 AS x FROM coding_attempts WHERE profile_id=? AND slug=? AND result='solved' AND created_at"
                               " BETWEEN ? AND ?", (pid, s["slug"], s["at"] - 3600, s["at"] + 3600)).fetchone()
            if not logged:
                c.execute("INSERT INTO coding_attempts (profile_id, slug, result, seconds, notes, created_at) VALUES"
                          " (?,?, 'solved', NULL, 'From LeetCode', ?)", (pid, s["slug"], s["at"]))
            c.execute("INSERT OR IGNORE INTO coding_solved (profile_id, slug, solved_at) VALUES (?,?,?)", (pid, s["slug"], s["at"]))
            # A later manual entry may exist already. Imported evidence still moves the first solve back,
            # even when its nearby manual attempt was deliberately deduplicated above.
            c.execute("UPDATE coding_solved SET solved_at=? WHERE profile_id=? AND slug=? AND solved_at>?",
                      (s["at"], pid, s["slug"], s["at"]))
        if not logged:
            new.append(known[s["slug"]]["title"])
    return {"new": len(new), "problems": new, "seen": len(recent)}


def check_soon(pid: int) -> None:
    """The Path was opened: look for new solves behind it, at most every so often."""
    if not username(pid) or int(db.meta_get(f"leetcode_checked:{pid}", 0) or 0) > db.now() - CHECK_EVERY:
        return
    with _running_lock:
        if pid in _running:
            return
        _running.add(pid)

    def run():
        try:
            sync(pid)
        finally:
            with _running_lock:
                _running.discard(pid)
    threading.Thread(target=run, daemon=True, name="leetcode-check").start()


def checking(pid: int) -> bool:
    """Whether the open Path should check back soon for the import's result."""
    with _running_lock:
        return pid in _running


def solved_between(pid: int, slugs: list[str], start: int, end: int) -> set[str]:
    """Which of these LeetCode itself shows as accepted in this window (empty without a linked profile)."""
    name = username(pid)
    if not name:
        return set()
    try:
        return {s["slug"] for s in recent_solves(name) if s["slug"] in slugs and start <= s["at"] <= end}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return set()
