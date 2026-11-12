"""Tools for MATCH: what a market asks for (your targets, or any track), and niches (the firm lists behind employer
tracks)."""

import re

from .. import db, market, niches, tracks

_ROLES = {"software": "Software", "ai": "AI/ML/Data", "ml": "AI/ML/Data", "machine learning": "AI/ML/Data",
          "data": "AI/ML/Data", "quant": "Quant", "quantitative": "Quant", "hardware": "Hardware",
          "product": "Product/Design", "design": "Product/Design", "designer": "Product/Design",
          "business": "Business/Finance", "finance": "Business/Finance"}


def _role(role: str) -> str:
    """The role category a few words name, by whole words: 'retail analytics' isn't AI."""
    t = (role or "").lower()
    return next((v for k, v in _ROLES.items() if re.search(rf"\b{re.escape(k)}s?\b", t)), "")


def _niche_id(pid: int, niche: str) -> str | None:
    want = (niche or "quant").lower().strip()
    for n in niches.summary_list(pid):
        if want in (n["id"].lower(), n["name"].lower()) or want in n["name"].lower():
            return n["id"]
    return None


def _track(pid: int, name: str) -> dict | None:
    """A track by its id, its name or part of it ("FDE", "forward deployed", "trading firms")."""
    want = (name or "").lower().strip()
    if not want:
        return None
    every = tracks.all_tracks(pid)
    exact = next((t for t in every if want in (t["id"].lower(), t["name"].lower())), None)
    return exact or next((t for t in every if want in t["name"].lower() or want in " ".join(t.get("phrases") or [])),
                         None)


def my_market(pid: int, role: str = "", n: int = 30, track: str = "") -> dict:
    if track:
        t = _track(pid, track)
        if not t:
            return {"error": f"no track called {track}", "tracks": [x["name"] for x in tracks.all_tracks(pid)][:40]}
        m = market.track_market(pid, t["id"])
        pay = m["glance"]["pay"]
        return {"market": t["name"], "track": t["id"], "open_to_you": m["size"], "with_descriptions": m["described"],
                "top_skills": [{"skill": s["skill"], "share": s["share"], "required_in": s["req"],
                                "nice_to_have_in": s["nice"], "trend": s["trend"], "on_your_resume": s["status"]}
                               for s in m["skills"][:12]],
                "gaps": m["glance"]["gaps"], "coverage_percent": m["fit"].get("coverage"),
                "pay": {k: pay[k] for k in ("n", "currency", "median", "p25", "p75") if k in pay} if pay["n"] else
                "no stated pay yet", "terms": [x["term"] for x in m["where"]["terms"]],
                "cities": [x["city"] for x in m["where"]["cities"][:5]], "peak_months": m["glance"]["peak"]}
    m = market.summary(pid, _role(role), max(10, min(n or 30, 60)))
    if m.get("error"):
        return m
    f = market.fit(pid, None, _role(role), max(10, min(n or 30, 60)))
    return {"market": m["label"], "postings_read": m["size"], "with_descriptions": m["described"],
            "top_skills": [{"skill": s["skill"], "in": f"{s['count']}/{m['size']}", "on_your_resume": s["have"]}
                           for s in m["skills"][:12]],
            "gaps": [g["skill"] for g in m["gaps"]],
            "resume": [{"skill": s["skill"], "status": s["label"], "where": s["where"]} for s in f.get("skills", [])[:12]],
            "coverage_percent": f.get("coverage"),
            "what_you_go_for": [i["text"] for i in m["pattern"].get("insights", [])] or m["pattern"].get("note", ""),
            "companies": [c["company"] for c in m["companies"][:8]]}


TOP_FIRMS = 6  # of each segment, unless they ask for that segment: a niche can have hundreds


def explore_niche(pid: int, niche: str = "quant", segment: str = "") -> dict:
    nid = _niche_id(pid, niche)
    if not nid:
        return {"error": f"no niche called {niche}; pick one below, or start one in Jobs → ＋ Tracks",
                "niches": [n["name"] for n in niches.summary_list(pid)]}
    d = niches.detail(pid, nid)
    segs = []
    for s in d["segments"]:
        if segment and segment.lower() not in s["name"].lower():
            continue
        fs = sorted((f for f in s["firms"] if not f.get("hidden")), key=lambda f: -f["open_roles"])
        shown = fs if segment else fs[:TOP_FIRMS]
        segs.append({"segment": s["name"], "firms_total": len(fs), "firms": [
            {"name": f["name"], "open_roles": f["open_roles"], "usually_opens": f["opens"] or None,
             "canada": f.get("canada") or None, "smaller": f["small"] or None,
             "best_role": f["best"]["title"] if f["best"] else None} for f in shown]})
    return {"niche": d["name"], "following": d["following"], "stats": d["stats"],
            "opening_dates_season": d["season"] or None, "busiest_month": d["windows"]["busiest"], "segments": segs,
            "_card": {"type": "niche", "id": nid, "name": d["name"]}}


def follow_niche(pid: int, niche: str = "quant", on: bool = True) -> dict:
    nid = _niche_id(pid, niche)
    if not nid:
        return {"error": f"no niche called {niche}"}
    out = niches.follow(pid, nid, on)
    if out.get("ok"):
        out["_undo"] = {"tool": "follow_niche", "args": {"niche": nid, "on": not on}}
    return out


def my_path(pid: int) -> dict:
    from .. import path
    s = path.summary(pid)
    return {"xp_today": s["today"], "daily_goal": s["goal"], "streak_days": s["streak"], "best_streak": s["best_streak"],
            "level": f"{s['level']['level']} ({s['level']['name']})", "xp": s["level"]["xp"],
            "next_steps": s["next_steps"], "job_hunt_stage": next(({"stage": x["name"], "goals_done": f"{x['have']}/{x['total']}",
                                                                    "next_goal": next(g["title"] for g in x["goals"] if not g["done"])}
                                                                   for x in s["hunt"] if not x["done"]), None), "quests_today": [{"quest": q["title"], "done": q["done"]} for q in s["quests"]],
            "shields": s["shields"], "days_at_goal_this_week": f"{s['week_met']}/{s['week_target']}", "leetcode_linked": s["leetcode_user"] or None,
            "tests_passed": s["counts"]["tests_passed"], "crowns": s["coding"]["crowns"], "topics_finished": s["coding"]["topics_done"], "milestones": [m["title"] for m in s["milestones"]][-5:],
            "neetcode_150": {"solved": f"{s['coding']['solved']}/{s['coding']['total']}",
                             **{lv: f"{v['solved']}/{v['total']}" for lv, v in s["coding"]["by_level"].items()}}}


def coding_progress(pid: int) -> dict:
    """Their NeetCode 150: solved overall, by level and by topic, what's next, misses due for review, recent attempts,
    and the lists they starred problems for."""
    from .. import path
    lad = path.ladder(pid)
    recent = sorted((p for p in lad["problems"] if p["last"]), key=lambda p: -p["last"]["created_at"])[:8]
    return {"neetcode_150": f"{lad['solved']}/{lad['total']}",
            "solved": {lv: f"{v['solved']}/{v['total']}" for lv, v in lad["by_level"].items()},
            "topics": {t["name"]: f"{t['solved']}/{t['total']}" for t in lad["by_topic"]},
            "next_up": {"title": lad["next"]["title"], "level": lad["next"]["level"], "topic": lad["next"]["topic"],
                        "link": lad["next"]["url"]}
            if lad["next"] else None,
            "due_for_review": [p["title"] for p in lad["due"]][:8],
            "ready_for_a_crown": [p["title"] for p in lad["sharpen"]][:8],
            "test_running": path.current_test(pid),
            "tests_ready": [{"kind": n["test_kind"], "scope": n["scope"], "title": n["title"], "problems": n["size"], "about_minutes": n["about_minutes"]}
                            for x in path.summary(pid)["sections"] for n in x["nodes"]
                            if n["kind"] == "test" and n["ready"] and not n["passed"] and not n["retake_at"]][:5],
            "recent": [{"problem": p["title"], "level": p["level"], "result": path.RESULTS[p["last"]["result"]],
                        "minutes": round((p["last"]["seconds"] or 0) / 60) or None, "notes": p["last"]["notes"]}
                       for p in recent],
            "starred_lists": [{"for": x["label"], "problems": len(x["slugs"])} for x in lad["lists"]]}


def record_problem(pid: int, problem: str, result: str, minutes: float = 0, notes: str = "") -> dict:
    """Log an attempt at a NeetCode 150 problem ("I solved Two Sum in 12 minutes"): solved, hints or unsolved."""
    from .. import path
    return path.record(pid, problem, result, round(float(minutes or 0) * 60), notes)


def undo_problem_attempt(pid: int, attempt_id: int) -> dict:
    from .. import path
    return path.undo_attempt(pid, attempt_id)


def star_problem(pid: int, problem: str, kind: str, tag: str, on: bool = True) -> dict:
    """Keep a problem on a list for a job (job_id), a kind of role or industry, or a company."""
    from .. import path
    return path.star(pid, problem, kind, tag, on=on)


def startup_feed(pid: int, area: str = "waterloo", yc_only: bool = False, people_you_know: bool = False,
                 query: str = "") -> dict:
    from .. import startups
    area = area if area in startups.AREAS else "waterloo"
    d = startups.feed(pid, area, yc_only=yc_only, known=people_you_know, hiring=True, q=query, limit=15)
    return {"area": startups.AREAS[area], "hiring_now": d["total"], "startups": [
        {"name": r["name"], "where": r["where"], "pace": r["pace"], "new_internships_30_days": r["new30"],
         "open_internships": r["open"], "open_to_you": r["open_to_you"], "roles_listed": r["roles"],
         "roles_change_30_days": r["growth"], "yc": r["yc"] or None, "team": r["team"], "what": r["one_liner"],
         "best_match": r["best"], "people_you_know": r["people"], "founders_you_know": r["founders"],
         "find_alumni": r["alumni"], "watched": r["watched"]} for r in d["startups"]]}


def watch_startup(pid: int, name: str) -> dict:
    from .. import startups
    out = startups.watch(pid, name)
    return out


def _card_of(c: dict) -> dict:
    return {"id": c["id"], "name": c["name"], "open_to_you": c["open_to_you"], "new_this_week": c["new_week"],
            "pinned": c["pinned"], "following": c["following"], **({"trend": c["trend"]} if c.get("trend") else {})}


def list_tracks(pid: int) -> dict:
    """The kinds of role and of employer they can browse: pinned, suggested, new and rising, all of them."""
    b = tracks.browse(pid)
    return {k: [_card_of(c) for c in b[k]] for k in ("pins", "suggested", "rising", "roles", "employers", "yours",
                                                     "more_roles")}


def pin_track(pid: int, track: str, on: bool = True) -> dict:
    t = _track(pid, track)
    if not t:
        return {"error": f"no track called {track}"}
    out = tracks.pin(pid, t["id"], on)
    if out.get("ok"):
        out["_undo"] = {"tool": "pin_track", "args": {"track": t["id"], "on": not on}}
    return out


def follow_track(pid: int, track: str, on: bool = True) -> dict:
    t = _track(pid, track)
    if not t:
        return {"error": f"no track called {track}"}
    out = tracks.follow(pid, t["id"], on)
    if out.get("ok"):
        out["_undo"] = {"tool": "follow_track", "args": {"track": t["id"], "on": not on}}
    return out


def my_calendar(pid: int, days: int = 14) -> dict:
    """What's coming: interviews, assessments and coffee chats found in their email, still to book, and reminders."""
    from .. import meetings
    from .calendar import reminders, when_text
    items = meetings.upcoming(pid, max(1, min(int(days or 14), 120)))
    return {"items": [{"id": r["id"], "kind": r["label"], "title": r["title"], "company": r["company"], "who": r["who"],
                       "when": when_text(r["start_ts"] or r["due_ts"]) if (r["start_ts"] or r["due_ts"]) else None,
                       "is_deadline": r["start_ts"] is None and r["due_ts"] is not None, "to_book": r["status"] == "to_book",
                       "join": r["join_url"], "book": r["book_url"], "email": r["email_link"]} for r in items],
            "reminders": reminders(pid)["reminders"]}


def coop_status(pid: int) -> dict:
    """What's connected and working, for when something looks off. No secrets in it."""
    import time

    from .. import ai, central, leetcode, path, sync
    from ..mcp_server import app_address
    last = sync.last_run() or {}
    ago = round((time.time() - last["finished_at"]) / 60) if last.get("finished_at") else None
    inboxes = [{"address": r["address"], "kind": r["kind"], "on": bool(r["enabled"]), "last_status": r["last_status"],
                "needs_sign_in": "sign in again" in (r["last_status"] or "").lower()}
               for r in db.rows("SELECT address, kind, enabled, last_status FROM email_accounts WHERE profile_id=?", (pid,))]
    return {"app_open": bool(app_address()), "database": "Supabase" if db.is_postgres() else "this computer",
            "postings_open": db.row("SELECT COUNT(*) AS n FROM jobs WHERE active=1")["n"], "last_sync_minutes_ago": ago,
            "shared_feed": central.enabled(), "ai_engine": ai.label() if ai.available() else None, "inboxes": inboxes,
            "ready_to_apply": db.row("SELECT COUNT(*) AS n FROM ready_queue WHERE profile_id=? AND status IN ('ready', "
                                     "'opened')", (pid,))["n"], "pinned_tracks": tracks.pins(pid), "leetcode_linked": leetcode.username(pid) or None,
            "test_running": bool(path.current_test(pid))}


def start_path_test(pid: int, kind: str, scope: str) -> dict:
    """Start a Path test: it picks real problems and starts the clock."""
    from .. import path
    return path.start_test(pid, kind, scope)


def finish_path_test(pid: int, test_id: int, results: dict) -> dict:
    from .. import path
    return path.finish_test(pid, test_id, results)


def link_leetcode(pid: int, username: str) -> dict:
    from .. import leetcode
    return leetcode.link(pid, username)


def hires_interns(pid: int, company: str) -> dict:
    """Does this company hire interns: what's open now, the seasons seen before, when its internships usually open."""
    from .. import interns
    return interns.lookup(company)
