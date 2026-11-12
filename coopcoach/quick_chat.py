"""Answers that need no AI: the requests people make most, read exactly and answered from their own data in milliseconds. Anything
that doesn't match exactly goes to the assistant, so a wrong guess can't happen here. Same tools, Activity log and Undo as the chat."""

import re
import uuid

_NEETCODE = re.compile(r"(how'?s|how is|show( me)?|check)?\s*(my )?(neet ?code|leet ?code|coding)( progress| going| doing)?\s*\??", re.I)
_PATH = re.compile(r"(how am i doing|what'?s my streak|my streak|my path|show( me)? my path|how'?s my (streak|path))\s*\??", re.I)
_NEXT = re.compile(r"(what'?s next|what should i (solve|do) next|next (neet ?code )?problem|what'?s my next problem)\s*\??", re.I)
_READY = re.compile(r"(what'?s ready|ready to apply|what'?s ready to apply|show( me)? (my )?ready( list)?)\s*\??", re.I)
_CAL = re.compile(r"(what'?s coming up|what do i have coming up|my calendar|any interviews( coming up)?|upcoming interviews)\s*\??", re.I)
_LOG = re.compile(r"i (?:just )?(solved|finished|did|needed hints on|needed a hint on|couldn'?t do|could not do|failed|got stuck on) "
                  r"(?P<name>.+?)(?: in (?P<mins>\d{1,3}) ?(?:min|mins|minutes|m))?(?:[,.;:]+\s*(?P<notes>the trick was .+|trick was .+))?[.!]*", re.I)


def _problem_line(n: dict) -> str:
    return f"[{n['title']}]({n['link']}) ({n['level']}, {n['topic']})"


def answer(pid: int, text: str) -> dict | None:
    from . import agent, path
    t = (text or "").strip()
    if not t or len(t) > 300 or "\n" in t:
        return None
    run_id = uuid.uuid4().hex[:12]
    use = lambda name, args=None: agent.call(pid, name, args or {}, source="chat", run_id=run_id)  # noqa: E731
    reply, tool = None, ""
    m = _LOG.fullmatch(t)
    if m and path._find(pid, m["name"]):
        word = m.group(1).lower()
        result = "solved" if word in ("solved", "finished", "did") else "hints" if word.startswith("needed") else "unsolved"
        args = {"problem": m["name"], "result": result, **({"minutes": int(m["mins"])} if m["mins"] else {}), **({"notes": m["notes"]} if m["notes"] else {})}
        tool = "record_problem"
        r = use(tool, args)
        if r.get("error"):
            return None
        when = f" in {m['mins']} minutes" if m["mins"] else ""
        if result == "solved":
            reply = f"Logged {r['problem']} ({r['level']}), solved{when}: +{r['xp']} XP." + (" That's a crown 👑." if r.get("crowned") else "")
        else:
            reply = f"Logged {r['problem']}: {r['result'].lower()}{when}. +{r['xp']} XP, and it comes back for review."
        nx = use("coding_progress").get("next_up")
        if nx:
            reply += f"\n\nNext up: {_problem_line(nx)}."
    elif _NEETCODE.fullmatch(t):
        tool = "coding_progress"
        r = use(tool)
        by = ", ".join(f"{k} {v}" for k, v in r["solved"].items())
        reply = f"You're at **{r['neetcode_150']}** on the NeetCode 150 ({by})."
        if r["due_for_review"]:
            reply += f"\n\nDue for review: {', '.join(r['due_for_review'][:5])}."
        if r["next_up"]:
            reply += f"\n\nNext up: {_problem_line(r['next_up'])}."
        if r["test_running"]:
            reply += "\n\nYou have a test running."
    elif _NEXT.fullmatch(t):
        tool = "coding_progress"
        r = use(tool)
        reply = f"Next up: {_problem_line(r['next_up'])}." if r["next_up"] else "You've solved the whole NeetCode 150. 🎉"
    elif _PATH.fullmatch(t):
        tool = "my_path"
        r = use(tool)
        left = [q["quest"] for q in r["quests_today"] if not q["done"]]
        reply = (f"**{r['streak_days']}-day streak** (best {r['best_streak']}), level {r['level']}. {r['xp_today']}/{r['daily_goal']} XP today, "
                 f"{r['neetcode_150']['solved']} on the NeetCode 150." + (f"\n\nQuests left today: {', '.join(left)}." if left else ""))
    elif _READY.fullmatch(t):
        tool = "ready_queue"
        r = use(tool)
        apps = r["applications"]
        reply = (f"**{r['count']} ready to apply:**\n" + "\n".join(f"- {a['company']}: {a['title']}" for a in apps[:8])) if apps else "Nothing's ready to apply right now."
    elif _CAL.fullmatch(t):
        tool = "my_calendar"
        r = use(tool, {"days": 14})
        items = r["items"]
        reply = ("**Next 2 weeks:**\n" + "\n".join(f"- {i['title']} ({i['when'] or 'still to book'})" for i in items[:8])) if items else "Nothing on your calendar in the next 2 weeks."
    if reply is None:
        return None
    acts = agent.run_actions(pid, run_id)
    return {"reply": reply, "by": "instant", "steps": [{"tool": a["tool"], "args": {}, "ok": bool(a["ok"]), "summary": a["summary"]} for a in acts if a["tool"] == tool],
            "cards": [], "undo": [u for u in (agent.db.loads(a["undo"]) for a in acts if a["undo"]) if u]}
