"""Tools for hackathons: applying to attend one, putting it on the tracker, and the Devpost write-up afterwards."""

from .. import db, events, hackathons


def _key(pid: int, hackathon: str) -> str | None:
    """A hackathon by its name ("Hack the North", "uofthacks"), its package key, or an event's title."""
    want = (hackathon or "").strip()
    if not want:
        return None
    if want.startswith("hack:"):
        return want
    s = hackathons.slug(want)
    for h in events.hackathon_season(pid):
        if hackathons.slug(h["name"]) == s or s in hackathons.slug(h["name"]):
            return f"hack:{hackathons.slug(h['name'])}"
    e = db.row("SELECT id FROM events WHERE kind='hackathon' AND title LIKE ? ORDER BY start_ts LIMIT 1", (f"%{want}%",))
    return f"hack:ev-{e['id']}" if e else None


def hackathon_application(pid: int, hackathon: str, redraft: bool = False) -> dict:
    """Draft (never submit) everything a hackathon application asks for."""
    key = _key(pid, hackathon)
    if not key:
        return {"error": f"no hackathon called {hackathon!r}: try its name as the Events tab shows it"}
    kit = hackathons.app_kit(pid, key, redraft=redraft)
    if kit.get("error"):
        return kit
    return {"ok": True, "hackathon": kit["hackathon"]["name"], "key": key, "apply_at": kit["job"]["apply_url"],
            "answers": {q["label"]: q["answer"] for q in kit["questions"] if q.get("group") == "answers"},
            "about_you": {q["label"]: q["answer"] or "(missing)" for q in kit["questions"] if q.get("group") == "you"
                          and q["kind"] != "input_file"},
            "still_needs_you": [q["label"] for q in kit["questions"] if q["needs_you"] and not q["answer"]]
            + kit["left_for_you"], "status": kit.get("status"),
            "rules": "Fill the [brackets] with the student. They review it, tick the MLH boxes and submit it "
                     "themselves (Events → Hackathons → Apply, or the tracker)."}


def track_hackathon(pid: int, hackathon: str, status: str = "applied") -> dict:
    """Put a hackathon application on the tracker: applied, offer (accepted), rejected, or '' to remove it."""
    key = _key(pid, hackathon)
    if not key:
        return {"error": f"no hackathon called {hackathon!r}"}
    status = {"accepted": "offer", "in": "offer", "remove": "", "none": ""}.get((status or "").lower(), status)
    return hackathons.track(pid, key, status)


def devpost_submission(pid: int, repo_url: str, hackathon: str = "", project: str = "", built: str = "",
                       challenge: str = "", learned: str = "", mine: str = "", prize: str = "") -> dict:
    """Write the Devpost submission for a project from its public GitHub repo and the team's own lines."""
    key = _key(pid, hackathon) if hackathon else ""
    out = hackathons.submission(pid, key or "", repo_url, project=project, built=built, challenge=challenge,
                                learned=learned, mine=mine, prize=prize)
    if out.get("error"):
        return out
    return {"ok": True, "doc_id": out["doc_id"], "project": out["project"], "elevator_pitch": out["tagline"],
            "about_the_project": out["markdown"], "built_with": out["built_with"], "try_it_out": out["links"],
            "brackets_to_fill": out["brackets"], "resume_entry": out["resume_entry"], "by": out["by"],
            "next": "The student fills the [brackets], records the demo (video_script) and submits on Devpost "
                    "themselves. add_project_to_resume puts it on their resume."}


def add_project_to_resume(pid: int, doc_id: int) -> dict:
    """Put a Devpost write-up's project on the student's master resume (undo restores the resume)."""
    return hackathons.add_to_resume(pid, doc_id)
