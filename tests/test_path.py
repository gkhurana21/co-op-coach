"""The Path (Today → Path): XP read from the real work (applications, hackathons, the NeetCode 150), the daily goal (50
XP unless they pick another), a streak of days it was met (still alive until today ends), levels, milestones celebrated
once (and the ones reached before the Path existed never pop up), the NeetCode 150 and nothing else (150 problems in 18
topics in roadmap order, links only), each try logged with reviews and stars, the long trail with its crowns, chests, quests and tests, a linked public LeetCode profile logging solves by itself, and the API and
chat tools around it.

Run: .venv/bin/python -m tests.test_path
"""

import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-path-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, auth, db, jobdetail, leetcode, path, routes_docs  # noqa: E402
from coopcoach.server import app  # noqa: E402

jobdetail.ensure_description = lambda *a, **k: "Build Java services and REST APIs with Python. Use Git every day."
results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def at(days_ago: int, hour: int = 12) -> int:
    """A timestamp `days_ago` days back, at midday local time (the Path counts days in local time)."""
    d = date.today() - timedelta(days=days_ago)
    return int(datetime(d.year, d.month, d.day, hour).timestamp())


def applied(pid: int, job_id: str, ts: int, status: str = "applied") -> None:
    with db.tx() as c:
        c.execute("INSERT INTO applications (profile_id, job_id, status, company, title, updated_at, applied_at) VALUES"
                  " (?,?,?,?,?,?,?)", (pid, job_id, status, "Acme", "Intern", ts, ts))


def new_profile(name: str, prefs: dict | None = None) -> int:
    now = db.now()
    with db.tx() as c:
        return c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES"
                         " (?,?,?,?,?)", (name, db.dumps({"skills": ["Python"]}), db.dumps(prefs or {}), now,
                                          now)).lastrowid


def data_files():
    topics, slugs = path.coding(), [p["slug"] for t in path.coding() for p in t["problems"]]
    check("coding problems: every one a LeetCode link, a known level, and no slug twice",
          len(topics) >= 10 and len(slugs) == len(set(slugs)) and all(
              re.fullmatch(r"https://leetcode\.com/problems/[a-z0-9-]+/", p["url"]) and p["level"] in path.XP
              for t in topics for p in t["problems"]))
    check("...only the name, link, level and topic are kept, never the problem itself",
          all(set(p) - {"free_url"} == {"title", "slug", "level", "url", "premium"} for t in topics for p in t["problems"]))
    check("the practice features are gone: no lessons, workbooks, mock interviews or drills",
          not hasattr(path, "lessons") and not hasattr(path, "workbook")
          and not ({"lesson", "mock", "drill", "answer", "workbook"} & set(path.XP))
          and not {"weak_spots", "practice_start", "practice_answer", "practice_progress", "prep_workbook"}
          & set(agent.TOOLS) and not ({"practice_sessions", "practice_answers", "drill_results", "lesson_done"}
                                      & set(db.COLUMNS)))


def levels():
    check("levels: 0 XP is Booting, 100 Hello World", path.level_of(0)["name"] == "Booting"
          and path.level_of(99)["level"] == 1 and path.level_of(100)["name"] == "Hello World")
    top = path.level_of(99999)
    check("...and the top level has nothing after it", top["name"] == "J.A.R.V.I.S." and top["to"] is None)
    mid = path.level_of(450)
    check("...each says how far to the next", mid["from"] == 300 and mid["to"] == 600 and mid["next_name"] == "Process")


def xp_and_streak(pid: int):
    s = path.summary(pid)
    check("a new student: 0 XP, the default 50 XP goal, no streak, an Egg, nothing to celebrate",
          s["today"] == 0 and s["goal"] == path.DEFAULT_GOAL == 50 and s["streak"] == 0
          and s["level"]["name"] == "Booting" and not s["milestones"])
    path.first_visit(pid)

    # three days before today each met the goal (an application is 50 XP)
    for k in (1, 2, 3):
        applied(pid, f"old{k}", at(k))
    s = path.summary(pid)
    check("today not done yet: the streak still counts yesterday back (3 days)", s["streak"] == 3 and s["today"] == 0)
    check("...and the 3-day streak is a new milestone", "streak_3" in {m["key"] for m in s["new_milestones"]})
    check("the week shows XP per day, oldest first, ending today",
          len(s["week"]) == 7 and s["week"][-1]["day"] == date.today().isoformat() and s["week"][-2]["xp"] == 60)  # 50 and a quest

    path.mark_solved(pid, "two-sum")  # Easy, 20 XP
    path.record(pid, "3sum", "unsolved", 1500)  # a logged try that didn't work out: 5 XP
    s = path.summary(pid)
    check("XP today comes from the work: a problem solved (20), a logged try (5) and two quests (10 each)", s["today"] == 45)
    check("...45 of 50 isn't the goal, so the streak is still 3", s["streak"] == 3)
    check("today's quests say which are done", {q["id"]: q["done"] for q in s["quests"]} == {
        "try": True, "solve": True, "apply": False})
    path.mark_solved(pid, "group-anagrams")  # Medium, 30 XP
    s = path.summary(pid)
    check("...one more problem reaches it: a 4-day streak", s["today"] == 75 and s["streak"] == 4
          and s["best_streak"] == 4)
    check("the level adds up every day's XP", s["level"]["xp"] == 3 * 60 + 75 and s["level"]["name"] == "Hello World")

    # a missed day breaks it
    other = new_profile("Gap")
    for k in (1, 3, 4, 5):
        applied(other, f"g{k}", at(k))
    s = path.summary(other)
    check("a day without the goal breaks the streak, but the best is remembered", s["streak"] == 1
          and s["best_streak"] == 3)
    path.record(other, "two-sum", "hints", 900)
    path.record(other, "two-sum", "solved", 600)
    check("solving a problem missed before is worth the problem and a review bonus (5 + 20 + 10), plus two quests",
          path.summary(other)["today"] == 55)


def goals(pid: int):
    check("a goal outside 10 to 500 XP is refused", path.set_goal(pid, 5).get("error")
          and path.set_goal(pid, 1000).get("error") and path.goal(pid) == 50)
    check("a new goal is kept", path.set_goal(pid, 100) == {"ok": True, "goal": 100} and path.goal(pid) == 100)
    s = path.summary(pid)
    check("...and the streak is measured against it (50 XP days don't make 100)", s["goal"] == 100 and s["streak"] == 0)
    path.set_goal(pid, 50)
    with db.tx() as c:
        raw = db.loads(db.row("SELECT prefs_json FROM profiles WHERE id=?", (pid,))["prefs_json"], {})
        c.execute("UPDATE profiles SET prefs_json=? WHERE id=?", (db.dumps({**raw, "path_goal": "lots"}), pid))
    check("a goal that isn't a number falls back to 50", path.goal(pid) == 50)
    path.set_goal(pid, 50)


def milestones(pid: int):
    fresh = new_profile("Fresh")
    applied(fresh, "f1", at(0))
    path.first_visit(fresh)
    check("milestones reached before their first visit never pop up", not path.summary(fresh)["new_milestones"])
    path.mark_solved(fresh, "two-sum")
    new = {m["key"] for m in path.summary(fresh)["new_milestones"]}
    check("...but the next ones do (the solve and, with its quests, 100 XP)", new == {"coding_1", "level_2"})
    path.seen(fresh, sorted(new))
    check("once seen it's never shown again", not path.summary(fresh)["new_milestones"])
    path.first_visit(fresh)
    check("...and a second visit doesn't reset anything", not path.summary(fresh)["new_milestones"]
          and "coding_1" in {m["key"] for m in path.summary(fresh)["milestones"]})


def ladder(pid: int):
    check("an unknown problem can't be ticked or logged", path.mark_solved(pid, "made-up-problem").get("error")
          and path.record(pid, "made-up-problem", "solved").get("error"))
    path.mark_solved(pid, "two-sum")
    check("ticking twice counts once", db.row("SELECT COUNT(*) AS n FROM coding_solved WHERE profile_id=? AND slug=?",
                                              (pid, "two-sum"))["n"] == 1)
    lad = path.ladder(pid)
    probs = lad["problems"]
    levels = [p["level"] for p in probs]
    check("the ladder is exactly the NeetCode 150: 150 different problems, 28 Easy, 101 Medium, 21 Hard",
          lad["total"] == len(probs) == len({p["slug"] for p in probs}) == 150 and lad["name"] == "NeetCode 150"
          and (levels.count("Easy"), levels.count("Medium"), levels.count("Hard")) == (28, 101, 21))
    check("...in the 18 topics of NeetCode's roadmap, in its order, with each topic's count",
          [(t["name"], t["total"]) for t in lad["by_topic"]] == [
              ("Arrays & Hashing", 9), ("Two Pointers", 5), ("Sliding Window", 6), ("Stack", 7), ("Binary Search", 7),
              ("Linked List", 11), ("Trees", 15), ("Tries", 3), ("Heap / Priority Queue", 7), ("Backtracking", 9),
              ("Graphs", 13), ("Advanced Graphs", 6), ("1-D Dynamic Programming", 12), ("2-D Dynamic Programming", 11),
              ("Greedy", 8), ("Intervals", 6), ("Math & Geometry", 8), ("Bit Manipulation", 7)])
    check("...the same order whoever it is (not reshuffled by role)", [p["slug"] for p in path.ladder(new_profile(
        "Quinn", {"categories": ["Quant"]}))["problems"]] == [p["slug"] for p in probs])
    check("...every link goes to the problem on LeetCode", all(
        p["url"] == f"https://leetcode.com/problems/{p['slug']}/" for p in probs))
    prem = [p for p in probs if p["premium"]]
    check("...the seven LeetCode Premium ones say where they're free", len(prem) == 7 and all(
        p["free_url"].startswith("https://neetcode.io/") for p in prem))
    check("next up is the first unsolved on the roadmap", lad["next"]["slug"] == "contains-duplicate")
    two = next(p for p in lad["problems"] if p["slug"] == "two-sum")
    check("a problem shows it's solved and what it's worth", two["solved_at"] and two["xp"] == 20)

    r = path.record(pid, "Valid Palindrome", "unsolved", 1200, "two pointers from both ends")
    check("a try is logged by name too: the result, the time and the notes", r["ok"] and r["xp"] == path.XP["attempt"]
          and not r["first_solve"] and r["result"] == "Couldn't")
    vp = next(p for p in path.ladder(pid)["problems"] if p["slug"] == "valid-palindrome")
    check("...and it comes back for review in 2 days", vp["review_at"] and abs(
        vp["review_at"] - vp["last"]["created_at"] - 2 * 86400) < 5
          and vp["last"]["notes"] == "two pointers from both ends" and vp["last"]["seconds"] == 1200)
    with db.tx() as c:  # three days on
        c.execute("UPDATE coding_attempts SET created_at=created_at-? WHERE profile_id=? AND slug=?",
                  (3 * 86400, pid, "valid-palindrome"))
    lad = path.ladder(pid)
    check("a miss that's due is first up, marked for review", lad["next"]["slug"] == "valid-palindrome"
          and any(p["slug"] == "valid-palindrome" for p in lad["due"]))
    path.record(pid, "valid-palindrome", "hints", 900)
    vp = next(p for p in path.ladder(pid)["problems"] if p["slug"] == "valid-palindrome")
    check("...a second miss waits longer (5 days)", abs(vp["review_at"] - vp["last"]["created_at"] - 5 * 86400) < 5)
    done = path.record(pid, "https://leetcode.com/problems/valid-palindrome/", "solved", 600)
    check("...solving it clears the review and counts as its first solve", done["first_solve"] and done["xp"] == 20
          and not next(p for p in path.ladder(pid)["problems"] if p["slug"] == "valid-palindrome")["review_at"])
    check("a result that isn't solved / hints / unsolved is refused", path.record(pid, "two-sum", "maybe").get("error"))
    u = path.record(pid, "contains-duplicate", "solved")
    path.undo_attempt(pid, u["attempt_id"])
    check("undoing a solve takes it back", not next(p for p in path.ladder(pid)["problems"]
                                                    if p["slug"] == "contains-duplicate")["solved_at"])

    applied(pid, "job-optiver", db.now(), "interviewing")
    with db.tx() as c:
        c.execute("UPDATE applications SET company='Optiver', title='Quant Trader Intern' WHERE job_id='job-optiver'")
    for kind, tag in (("job", "job-optiver"), ("track", "Quant"), ("company", "Jane Street")):
        path.star(pid, "two-sum", kind, tag)
    lists = {x["label"]: x["slugs"] for x in path.ladder(pid)["lists"]}
    check("stars keep a problem on a list for a job, a kind of role, or a company",
          lists.get("Optiver · Quant Trader Intern") == ["two-sum"] and lists.get("Quant") == ["two-sum"]
          and lists.get("Jane Street") == ["two-sum"])
    off = path.star(pid, "two-sum", "company", "Jane Street", on=False)
    check("...and come off again (with an undo that puts them back)", "Jane Street" not in {
        x["label"] for x in path.ladder(pid)["lists"]} and off["_undo"]["args"]["on"] is True)
    check("a star needs a real kind and a job that exists", path.star(pid, "two-sum", "vibes", "x").get("error")
          and path.star(pid, "two-sum", "job", "no-such-job").get("error"))

    check("a problem outside the 150 can't be logged or starred, even with a real LeetCode link", path.record(
        pid, "https://leetcode.com/problems/number-of-provinces/", "solved").get("error") and path.star(
        pid, "number-of-provinces", "track", "Quant").get("error") and not hasattr(path, "add_custom"))
    with db.tx() as c:  # solved under the old list: it doesn't count toward the 150
        c.execute("INSERT INTO coding_solved (profile_id, slug, solved_at) VALUES (?,?,?)",
                  (pid, "number-of-provinces", db.now()))
    check("...and an old solve from outside it doesn't count toward them", path.counts(pid)["coding"] == path.ladder(
        pid)["solved"])


def trail():
    from coopcoach import features
    features.set_mode("all")  # every goal first; lean (what a new person sees) is checked below
    sw = new_profile("Sam", {"categories": ["Software"]})
    d = path.summary(sw)
    secs = d["sections"]
    nodes = [n for x in secs for n in x["nodes"]]
    probs = [n for n in nodes if n["kind"] == "problem"]
    stages = d["hunt"]
    work = [g for x in stages for g in x["goals"]]
    check("the NeetCode lane has every problem as a stop, a chest per topic, and nothing else in between",
          len(secs) == 18 and len(probs) == 150 and len({n["slug"] for n in probs}) == 150 and d["stops"] == len(nodes)
          and sum(n["kind"] == "chest" for n in nodes) == 18 and {n["kind"] for n in nodes} == {"problem", "chest", "test"})
    check("...each topic's stops in order, its chest after its problems", [n["slug"] for n in probs][:2] == ["contains-duplicate",
          "valid-anagram"] and all([n["kind"] for n in x["nodes"]].index("chest") == len(x["nodes"]) - (
              3 if i + 1 in (3, 6, 9, 12, 15, 18) else 2) for i, x in enumerate(secs)))
    check("the job-hunt lane has every real-work goal once, in stages of five or six", [g["id"] for g in work] ==
          [w[0] for w in path._WORK] and all(5 <= x["total"] <= 6 for x in stages) and d["hunt_goals"] == len(path._WORK))
    check("...the goals grow along it", [g["need"] for g in work if g["id"].startswith("apply")] == sorted(
        g["need"] for g in work if g["id"].startswith("apply")) and work[0]["id"] == "resume" and all(g["have"] <= g["need"] for g in work))
    check("no stop asks for practice, lessons or mock interviews", not any(
        re.search(r"lesson|mock|practi|workbook", n["title"], re.I) for n in nodes + work))
    views = {g["action"].get("view") for g in work if g["action"]["kind"] == "go"}
    check("every job-hunt goal goes to a page that exists", views <= {"documents", "tracks", "events", "tracker", "recruiters"})
    check("Jarvis stands at the first problem in one lane and the first goal in the other", [n["slug"] for n in probs if n["state"] == "here"]
          == ["contains-duplicate"] and {n["state"] for n in probs} == {"here", "ahead"} and secs[0]["has_here"]
          and [g["id"] for g in work if g["state"] == "here"] == ["resume"] and stages[0]["has_here"])
    check("what's next says the problem and the first goals", d["next_steps"][0].startswith("NeetCode: Contains Duplicate")
          and "Add your resume" in d["next_steps"])

    # the job hunt moves with the work itself, never waiting on coding (it was stuck at problem one before)
    now = db.now()
    with db.tx() as tx:
        tx.execute("UPDATE profiles SET resume_text='Sam. Python.' WHERE id=?", (sw,))
        for i in range(6):
            tx.execute("INSERT INTO applications (profile_id, job_id, status, company, title, updated_at, applied_at) VALUES"
                       " (?,?, 'applied', ?, 'Intern', ?, ?)", (sw, f"j{i}", f"Co{i}", now, now))
    h = path.summary(sw)["hunt"]
    by = {g["id"]: g for x in h for g in x["goals"]}
    check("sending applications ticks the job-hunt goals with no coding done", by["apply1"]["done"] and by["apply5"]["done"]
          and by["apply3"]["done"] and by["apply10"]["have"] == 6 and not by["follow"]["done"]
          and [g["id"] for x in h for g in x["goals"] if g["state"] == "here"] == ["inbox"])
    check("...and a stage is done when its goals are, in any order", h[0]["have"] == 2 and not h[0]["done"] and h[1]["have"] == 2
          and path.summary(sw)["coding"]["solved"] == 0)

    # lean: goals for the pages it tucks away aren't on the lane, and the rest still move it
    features.set_mode("lean")
    lean = path.summary(sw)["hunt"]
    ids = [g["id"] for x in lean for g in x["goals"]]
    check("lean leaves out the goals for pages it hides (People, hackathons; Tracks and events stay)",
          not any(i.startswith(("people", "hack", "devpost")) for i in ids) and "follow" in ids and any(i.startswith("event") for i in ids) and "apply1" in ids
          and "interview1" in ids and all(x["total"] for x in lean) and path.summary(sw)["hunt_goals"] == len(ids))
    check("...and Jarvis still stands at the first goal not done", [g["id"] for x in lean for g in x["goals"] if g["state"] == "here"]
          == ["inbox"])
    features.set_mode("all")
    # the real case: 29 sent, an interview and an offer from the inbox, Follow a track and People never touched
    keyed = {w[0]: (w[3], w[4]) for w in path._WORK}
    def at(done):
        c = {}
        for g in done:
            k, need = keyed[g]
            c[k] = max(c.get(k, 0), need)
        return c
    far = at(["resume", "inbox", "prep10", "apply25", "reply5", "interview5", "offer1"])
    far[keyed["apply25"][0]] = 29
    spot = lambda c: [g["id"] for x in path.hunt(c) for g in x["goals"] if g["state"] == "here"]  # noqa: E731
    check("Jarvis stands at the real next step (30 applications), not a skipped Follow a track", spot(far) == ["apply30"])
    check("...with one spot only, and the skipped goals still open", len(spot(far)) == 1 and any(
        g["id"] == "follow" and g["state"] == "ahead" for x in path.hunt(far) for g in x["goals"]))
    check("a brand-new student starts at their resume", spot({}) == ["resume"])
    check("right after the resume, an untouched side goal is the next step", spot(at(["resume"])) == ["follow"])
    done_all = at([w[0] for w in path._WORK])
    check("everything done: no spot, and no crash", spot(done_all) == [])

    # ones solved before: ticked off together (LeetCode shows anyone only the last 20)
    m = new_profile("Mia")
    path.record(m, "two-sum", "solved")
    r = path.mark_solved_many(m, ["contains-duplicate", "valid-anagram", "two-sum", "not-a-real-problem", "valid-anagram"])
    check("several can be marked solved at once, skipping ones already solved or not on the list", r["marked"] == [
        "contains-duplicate", "valid-anagram"] and r["xp"] == 40 and path.ladder(m)["solved"] == 3)

    ids = [p["slug"] for p in path.coding()[0]["problems"]]
    for x in ids[:-1]:
        path.record(sw, x, "solved")
    d = path.summary(sw)
    chest_of = lambda x: next(n for n in x["nodes"] if n["kind"] == "chest")  # noqa: E731
    chest = chest_of(d["sections"][0])
    check("a chest stays shut with problems left", not chest["open"] and chest["left"] == 1 and d["counts"]["topics_done"] == 0)
    before = path.level_of(0) and d["level"]["xp"]
    path.record(sw, ids[-1], "solved")
    d = path.summary(sw)
    check("finishing a topic opens its chest for XP, and Jarvis moves to the next topic", chest_of(d["sections"][0])["open"]
          and d["sections"][0]["done"] and d["counts"]["topics_done"] == 1 and d["level"]["xp"] == before + 30 + path.XP["chest"]
          and d["sections"][1]["has_here"] and "topic_arrays-hashing" in {m["key"] for m in d["milestones"]})

    # crowns: solved again a week later, then three weeks after that
    c = new_profile("Cam")
    path.record(c, "two-sum", "solved")
    row = lambda: next(x for x in path.ladder(c)["problems"] if x["slug"] == "two-sum")  # noqa: E731
    check("a first solve is the first crown, and says when the next can be won", row()["crowns"] == 1
          and abs(row()["sharpen_at"] - db.now() - 7 * 86400) < 5)
    check("...solving it again the same day isn't a crown", path.record(c, "two-sum", "solved")["crowned"] is False
          and row()["crowns"] == 1 and not path.ladder(c)["sharpen"])
    with db.tx() as tx:  # eight days on
        tx.execute("UPDATE coding_attempts SET created_at=created_at-? WHERE profile_id=?", (8 * 86400, c))
        tx.execute("UPDATE coding_solved SET solved_at=solved_at-? WHERE profile_id=?", (8 * 86400, c))
    check("a week on it's ready for another crown (optional: it doesn't push Next up)", [x["slug"] for x in path.ladder(c)["sharpen"]]
          == ["two-sum"] and path.ladder(c)["next"]["slug"] != "two-sum")
    r = path.record(c, "two-sum", "solved")
    check("...solving it then wins the crown", r["crowned"] and r["xp"] == path.XP["crown"] and row()["crowns"] == 2
          and path.counts(c)["crowns"] == 1 and "crown_1" in {m["key"] for m in path.summary(c)["milestones"]})
    with db.tx() as tx:
        tx.execute("UPDATE coding_attempts SET created_at=created_at-? WHERE profile_id=?", (22 * 86400, c))
        tx.execute("UPDATE coding_solved SET solved_at=solved_at-? WHERE profile_id=?", (22 * 86400, c))
    path.record(c, "two-sum", "solved")
    check("...and the third, three weeks later, is the last", row()["crowns"] == 3 and row()["sharpen_at"] is None)
    allp = new_profile("Ace")
    for t in path.coding():
        for x in t["problems"]:
            path.record(allp, x["slug"], "solved")
    d = path.summary(allp)
    check("all 150: every chest open, a big bonus, and the top milestone", d["counts"]["coding"] == 150
          and d["counts"]["topics_done"] == 18 and "coding_150" in {m["key"] for m in d["milestones"]}
          and any(k == "all_150" and xp == path.XP["all_150"] for _, k, xp in path._events(allp))
          and not any(n.get("state") == "here" for x in d["sections"] for n in x["nodes"]))


def api_and_tools(pid: int):
    with TestClient(app, base_url="http://localhost") as cl:
        api(cl, pid)


def api(cl, pid: int):
    d = cl.get(f"/api/path?profile_id={pid}").json()
    check("GET /api/path: today, goal, streak, level, the trail, quests and the NeetCode summary",
          {"today", "goal", "goals", "streak", "level", "sections", "stops", "quests", "next_steps", "new_milestones", "coding"} <= set(d)
          and {"next", "reviews_due", "by_level", "solved", "total", "topic"} <= set(d["coding"]) and d["coding"]["total"] == 150)
    check("POST goal: a number is kept, junk is a 400", cl.post("/api/path/goal", json={"profile_id": pid, "goal": 20})
          .json()["goal"] == 20 and cl.post("/api/path/goal", json={"profile_id": pid, "goal": "x"}).status_code == 400
          and cl.post("/api/path/goal", json={"profile_id": pid, "goal": 3}).status_code == 400)
    cl.post("/api/path/goal", json={"profile_id": pid, "goal": 50})
    check("the old lesson, workbook and practice pages are gone", cl.get("/api/path/lesson/big-o").status_code == 404
          and cl.post("/api/path/workbook", json={"profile_id": pid, "job_id": "x"}).status_code in (404, 405)
          and cl.get(f"/api/practice?profile_id={pid}").status_code == 404)
    lad = cl.get(f"/api/path/ladder?profile_id={pid}").json()
    check("GET the ladder by the API", lad["problems"] and set(lad["by_level"]) == {"Easy", "Medium", "Hard"})
    r = cl.post("/api/path/attempt", json={"profile_id": pid, "slug": "valid-anagram", "result": "solved",
                                           "seconds": 420, "notes": "count letters"})
    check("logging a try by the API (logged in the Activity log, with Undo)", r.json().get("first_solve")
          and db.row("SELECT seconds, notes FROM coding_attempts WHERE profile_id=? AND slug='valid-anagram'",
                     (pid,)) == {"seconds": 420, "notes": "count letters"}
          and db.row("SELECT undo FROM agent_actions WHERE tool='record_problem' ORDER BY id DESC LIMIT 1")["undo"])
    check("...junk is a 400", cl.post("/api/path/attempt", json={"profile_id": pid, "slug": "valid-anagram",
                                                                 "result": "solved", "seconds": "x"}).status_code == 400
          and cl.post("/api/path/attempt", json={"profile_id": pid, "slug": "nope", "result": "solved"}).status_code
          == 400)
    check("starring by the API; adding other problems is gone", cl.post("/api/path/star", json={
        "profile_id": pid, "slug": "valid-anagram", "kind": "track", "tag": "Big Tech"}).json()["ok"]
          and cl.post("/api/path/custom", json={"profile_id": pid, "url": "https://leetcode.com/problems/design-hit-counter/",
                                                "level": "Medium"}).status_code in (404, 405))
    check("the quick tick still works, and untick", cl.post("/api/path/solved", json={
        "profile_id": pid, "slug": "climbing-stairs"}).json()["solved"] and cl.post("/api/path/solved", json={
            "profile_id": pid, "slug": "climbing-stairs", "solved": False}).json()["solved"] is False)
    keys = [m["key"] for m in cl.get(f"/api/path?profile_id={pid}").json()["new_milestones"]]
    cl.post("/api/path/seen", json={"profile_id": pid, "keys": keys})
    check("seen milestones stop coming back", not cl.get(f"/api/path?profile_id={pid}").json()["new_milestones"])

    me = agent.call(pid, "my_path", {}, source="test")
    check("chat: my_path says the streak, today's XP against the goal, what's next and the LeetCode count",
          {"streak_days", "xp_today", "daily_goal", "level", "next_steps", "neetcode_150"} <= set(me)
          and me["daily_goal"] == 50 and me["next_steps"])
    cp = agent.call(pid, "coding_progress", {}, source="test")
    check("chat: coding_progress gives solved by level, next up, reviews and lists",
          set(cp["solved"]) == {"Easy", "Medium", "Hard"} and cp["next_up"] and "starred_lists" in cp)
    rp = agent.call(pid, "record_problem", {"problem": "Climbing Stairs", "result": "solved", "minutes": 8},
                    source="chat")
    check("chat: \"I solved Climbing Stairs in 8 minutes\" is logged", rp.get("first_solve") and db.row(
        "SELECT seconds FROM coding_attempts WHERE profile_id=? AND slug='climbing-stairs' ORDER BY id DESC LIMIT 1",
        (pid,))["seconds"] == 480)
    st = agent.call(pid, "star_problem", {"problem": "climbing-stairs", "kind": "company", "tag": "Shopify"},
                    source="chat")
    check("chat: star a problem for a company", st.get("ok") and st["for"] == "Shopify")
    quick = routes_docs._rules_reply(pid, "How's my streak?")
    check("without an AI, the chat's quick answers know the Path too", "day streak" in quick
          and "/50 XP today" in quick and "Path" in quick)

    auth.set_passcode(pid, "maple-1234")
    try:
        check("a locked profile's Path stays locked", cl.get(f"/api/path?profile_id={pid}").status_code == 423
              and cl.post("/api/path/attempt", json={"profile_id": pid, "slug": "two-sum", "result": "solved"})
              .status_code == 423 and cl.get(f"/api/path/ladder?profile_id={pid}").status_code == 423)
    finally:
        auth.clear_passcode(pid)


def tests_on_the_trail():
    pid = new_profile("Tess")
    d = path.summary(pid)
    nodes = [n for x in d["sections"] for n in x["nodes"] if n["kind"] == "test"]
    kinds = [n["test_kind"] for n in nodes]
    check("the trail has a test for every topic, a checkpoint every three topics, and a final", kinds.count("topic") == 18
          and [n["scope"] for n in nodes if n["test_kind"] == "checkpoint"] == ["c3", "c6", "c9", "c12", "c15"]
          and kinds.count("final") == 1 and nodes[-1]["test_kind"] == "final" and d["stops"] == 192)
    check("a test isn't ready until about half of what it covers is solved", not nodes[0]["ready"])
    ids = [p["slug"] for p in path.coding()[0]["problems"]]
    for x in ids[:5]:
        path.record(pid, x, "solved")
    check("...and then it is", next(n for x in path.summary(pid)["sections"] for n in x["nodes"] if n["kind"] == "test")["ready"])

    r = path.start_test(pid, "topic", "arrays-hashing")
    t = r["test"]
    pool = {p["slug"]: p for p in path.coding()[0]["problems"]}
    check("a topic test picks 3 real problems from that topic", r["ok"] and len(t["problems"]) == 3 and all(
        p["slug"] in pool for p in t["problems"]))
    check("...not all Easy when the topic has harder ones", {p["level"] for p in t["problems"]} != {"Easy"})
    check("...and skips what they solved in the past week, while there are others", not {p["slug"] for p in t["problems"]} & set(ids[:5]))
    check("...and gives the time a real assessment would", t["deadline"] - t["started_at"] == sum(
        path.TEST_MINUTES[p["level"]] for p in t["problems"]) * 60)
    check("...links only: no problem text", all(set(p) <= {"slug", "title", "level", "url", "topic", "premium", "free_url"}
                                                for p in t["problems"]))
    check("only one test runs at a time: starting another gives the running one", path.start_test(pid, "final", "final")["test"]["id"] == t["id"])
    d = path.summary(pid)
    check("the Path shows the running test", d["test"]["id"] == t["id"] and path.current_test(pid)["id"] == t["id"])

    before = path.summary(pid)["level"]["xp"]
    res = {p["slug"]: {"result": "solved", "minutes": 9} for p in t["problems"]}
    done = path.finish_test(pid, t["id"], res)
    d = path.summary(pid)
    first_solves = sum(path.XP[p["level"]] for p in t["problems"])
    check("every problem solved in time passes", done["passed"] and done["in_time"] and done["xp"] == path.XP["test_topic"] and not done["missed"])
    check("...and earns the test's XP on top of the problems", d["level"]["xp"] - before >= first_solves + path.XP["test_topic"])
    check("...each problem is logged like any other try, with its time", all(
        db.row("SELECT seconds FROM coding_attempts WHERE profile_id=? AND slug=? AND result='solved'", (pid, s))["seconds"] == 540 for s in res))
    check("...the trail shows it passed, with a milestone", next(n for x in d["sections"] for n in x["nodes"] if n.get("scope") == "arrays-hashing")["passed"]
          and "test_1" in {m["key"] for m in d["milestones"]} and d["counts"]["tests_passed"] == 1 and d["test"] is None)
    check("a completed test retry returns its saved result", path.finish_test(pid, t["id"], res).get("already_finished"))

    concurrent = new_profile("Concurrent finisher")
    race = path.start_test(concurrent, "topic", "arrays-hashing")["test"]
    attempts = {p["slug"]: {"result": "solved", "minutes": 5} for p in race["problems"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: path.finish_test(concurrent, race["id"], attempts), range(2)))
    check("concurrent finishes record attempts and test XP only once", all(r.get("ok") for r in replies)
          and sum(bool(r.get("already_finished")) for r in replies) == 1
          and db.row("SELECT COUNT(*) AS n FROM coding_attempts WHERE profile_id=?", (concurrent,))["n"] == len(attempts))

    # a miss, a wait, a different test
    r = path.start_test(pid, "topic", "Two Pointers")["test"]
    check("a topic can be named by its name too", r["scope"] == "two-pointers")
    res = {p["slug"]: {"result": "solved"} for p in r["problems"]}
    res[r["problems"][0]["slug"]] = {"result": "hints"}
    done = path.finish_test(pid, r["id"], res)
    check("needing a hint fails it, and it says what to review", not done["passed"] and done["missed"] == [r["problems"][0]["slug"]]
          and done["xp"] == 0 and done["retake_in_days"] == path.RETAKE_DAYS)
    check("a retake waits three days", "error" in path.start_test(pid, "topic", "two-pointers")
          and next(n for x in path.summary(pid)["sections"] for n in x["nodes"] if n.get("scope") == "two-pointers")["retake_at"])
    with db.tx() as c:
        c.execute("UPDATE path_tests SET finished_at=? WHERE id=?", (db.now() - 4 * 86400, r["id"]))
    again = path.start_test(pid, "topic", "two-pointers")["test"]
    check("after the wait it can be taken again, with a fresh pick", again["id"] != r["id"] and len(again["problems"]) == 3)
    path.give_up_test(pid, again["id"])
    check("stopping early counts as not passed", not db.row("SELECT passed FROM path_tests WHERE id=?", (again["id"],))["passed"]
          and path.current_test(pid) is None and "error" in path.give_up_test(pid, again["id"]))

    # the clock
    late = path.start_test(pid, "topic", "stack")["test"]
    with db.tx() as c:
        c.execute("UPDATE path_tests SET deadline=? WHERE id=?", (db.now() - path.TEST_GRACE - 60, late["id"]))
    done = path.finish_test(pid, late["id"], {p["slug"]: {"result": "solved"} for p in late["problems"]})
    check("all solved, but after the clock, isn't a pass", not done["passed"] and not done["in_time"] and not done["missed"])
    left = path.start_test(pid, "topic", "binary-search")["test"]
    with db.tx() as c:
        c.execute("UPDATE path_tests SET deadline=? WHERE id=?", (db.now() - 7 * 3600, left["id"]))
    check("a test left running long past its clock ends by itself, not passed", path.current_test(pid) is None
          and not db.row("SELECT passed FROM path_tests WHERE id=?", (left["id"],))["passed"])

    # junk
    other = new_profile("Other")
    mine = path.start_test(other, "topic", "linked-list")["test"]
    check("someone else's test can't be finished or stopped", "error" in path.finish_test(pid, mine["id"], {})
          and "error" in path.give_up_test(pid, mine["id"]))
    check("nonsense is refused in words", all("error" in x for x in (path.finish_test(other, mine["id"], "soon"),
          path.start_test(pid, "quiz", "x"), path.start_test(pid, "topic", "nope"), path.start_test(pid, "checkpoint", "c4"),
          path.start_test(pid, "checkpoint", "c99"), path.start_test(pid, "checkpoint", "x"), path.start_test(pid, "final", "x"))))
    done = path.finish_test(other, mine["id"], {mine["problems"][0]["slug"]: {"result": "lucky", "minutes": "soon"}})
    check("a made-up result counts as not solved, and a bad time doesn't crash", done["ok"] and not done["passed"])
    check("a checkpoint and the final pick from what they cover", set(p["topic"] for p in path.start_test(
        new_profile("Cee"), "checkpoint", "c3")["test"]["problems"]) <= {t["name"] for t in path.coding()[:3]})
    fin = path.start_test(new_profile("Fin"), "final", "final")["test"]
    check("the final is 10 problems across the 150, about a real assessment's length each", len(fin["problems"]) == 10 and {
        p["level"] for p in fin["problems"]} == {"Easy", "Medium", "Hard"} and len({p["slug"] for p in fin["problems"]}) == 10)

    # through the API and chat
    c = TestClient(app, base_url="http://localhost")
    a = new_profile("Api")
    st = c.post("/api/path/test/start", json={"profile_id": a, "kind": "topic", "scope": "trees"}).json()
    now = c.get(f"/api/path/test?profile_id={a}").json()
    fin = c.post("/api/path/test/finish", json={"profile_id": a, "test_id": st["test"]["id"], "results": {
        p["slug"]: {"result": "solved"} for p in st["test"]["problems"]}}).json()
    check("the API starts, shows and finishes a test", now["test"]["id"] == st["test"]["id"] and fin["passed"])
    check("a bad test id is a sentence, not a crash", c.post("/api/path/test/finish", json={"profile_id": a, "test_id": "x"}).status_code == 400)
    prog = agent.call(a, "coding_progress", {}, source="test")
    check("Jarvis knows which tests are ready", "tests_ready" in prog and prog["test_running"] is None)
    check("Jarvis can start and finish one, only when asked: it isn't a read", {"start_path_test", "finish_path_test", "link_leetcode"} <= agent.WRITES)


def shields_and_year():
    today = date.today()
    days = lambda *ks: {(today - timedelta(days=k)).isoformat(): 50 for k in ks}  # noqa: E731
    check("the streak counts days in a row, ending yesterday while today isn't over", path._streak(days(1, 2, 3), 50, today)[0] == 3)
    check("...today adds once it's met", path._streak(days(0, 1, 2), 50, today)[0] == 3)
    check("...a missed day ends it when there's no shield", path._streak(days(1, 3, 4), 50, today)[0] == 1)
    check("seven in a row earn a shield", path._streak(days(*range(1, 8)), 50, today)[2] == 1)
    streak, best, shields = path._streak(days(1, 2, *range(4, 11)), 50, today)
    check("...and a shield saves the streak on a missed day, spent, adding nothing", streak == 9 and shields == 0 and best == 9)
    check("...but two misses with one shield end it", path._streak(days(1, *range(4, 11)), 50, today)[0] == 1)
    check("...shields stop at two", path._streak(days(*range(1, 40)), 50, today)[2] == path.MAX_SHIELDS)

    pid = new_profile("Yara")
    with db.tx() as c:
        c.execute("INSERT INTO coding_solved (profile_id, slug, solved_at) VALUES (?,?,?)", (pid, "two-sum", at(100)))
        c.execute("INSERT INTO coding_attempts (profile_id, slug, result, seconds, created_at) VALUES (?,?,?,?,?)", (pid, "two-sum", "solved", 900, at(100)))
    path.record(pid, "two-sum", "solved", 300)
    d = path.summary(pid)
    check("a year of activity is there, day by day", (today - timedelta(days=100)).isoformat() in d["year"] and d["week_target"] == path.WEEK_TARGET)
    check("...and each problem keeps its personal best", next(p for p in path.ladder(pid)["problems"] if p["slug"] == "two-sum")["best_seconds"] == 300)


def leetcode_link():
    pid = new_profile("Leah")
    calls = []
    feed = {"real_user": [{"titleSlug": "two-sum", "timestamp": str(at(3))}, {"titleSlug": "not-in-the-150-at-all", "timestamp": str(at(2))},
                          {"titleSlug": "valid-anagram", "timestamp": str(at(1))}], "hidden": []}

    def ask(username):
        calls.append(username)
        if username == "down":
            raise leetcode.httpx.ConnectError("no network")
        if username not in feed:
            return {"data": {"matchedUser": None, "recentAcSubmissionList": None}, "errors": [{"message": "That user does not exist."}]}
        return {"data": {"matchedUser": {"username": username}, "recentAcSubmissionList": feed[username]}}
    real_ask, leetcode._ask = leetcode._ask, ask
    try:
        check("nothing's linked until they link it, and nothing is asked", leetcode.username(pid) == "" and leetcode.sync(pid) == {"new": 0} and not calls)
        check("an unknown username is refused, not saved", "error" in leetcode.link(pid, "nobody_here") and leetcode.username(pid) == "")
        check("nonsense isn't even asked about", "error" in leetcode.link(pid, "a b c?d=1 e") and calls.count("a b c?d=1 e") == 0)
        check("no network says so, and isn't saved", "error" in leetcode.link(pid, "down") and leetcode.username(pid) == "")
        r = leetcode.link(pid, "https://leetcode.com/u/real_user/")
        check("linking takes a username or a profile link, and logs recent solves", r["linked"] == "real_user" and r["new"] == 2)
        lad = {p["slug"]: p for p in path.ladder(pid)["problems"]}
        check("only the NeetCode 150 is logged", lad["two-sum"]["solved_at"] and lad["valid-anagram"]["solved_at"] and not db.row(
            "SELECT 1 AS x FROM coding_attempts WHERE slug='not-in-the-150-at-all' AND profile_id=?", (pid,)))
        check("...on the day it was really solved", path.summary(pid)["year"].get((date.today() - timedelta(days=3)).isoformat(), 0) >= path.XP["Easy"]
              and lad["two-sum"]["solved_at"] == at(3))
        check("checking again logs nothing twice", leetcode.sync(pid)["new"] == 0 and db.row(
            "SELECT COUNT(*) AS n FROM coding_attempts WHERE profile_id=? AND slug='two-sum'", (pid,))["n"] == 1)

        hand = new_profile("Hand")
        path.record(hand, "two-sum", "solved")
        feed["hand"] = [{"titleSlug": "two-sum", "timestamp": str(db.now() - 60)}]
        leetcode.link(hand, "hand")
        check("a solve logged by hand around then isn't logged again", db.row(
            "SELECT COUNT(*) AS n FROM coding_attempts WHERE profile_id=? AND slug='two-sum'", (hand,))["n"] == 1)
        check("nearby imported solve moves the first-solved timestamp earlier", db.row(
            "SELECT solved_at FROM coding_solved WHERE profile_id=? AND slug='two-sum'", (hand,))["solved_at"]
              == int(feed["hand"][0]["timestamp"]))
        feed["hand"] = [{"titleSlug": "two-sum", "timestamp": str(at(10))}]
        leetcode.sync(hand)
        check("an older imported solve corrects first-solved history", db.row(
            "SELECT solved_at FROM coding_solved WHERE profile_id=? AND slug='two-sum'", (hand,))["solved_at"] == at(10))

        feed["real_user"].append({"titleSlug": "two-sum", "timestamp": str(at(0, 1))})
        check("solving it again later is logged as a new try", leetcode.sync(pid)["new"] == 1)

        priv = new_profile("Private")
        r = leetcode.link(priv, "hidden")
        check("a profile that hides its solves still links, and logging stays by hand", r["linked"] == "hidden" and r["new"] == 0)
        feed.pop("hidden")
        check("if it stops existing later, nothing breaks", "error" in leetcode.sync(priv) and leetcode.sync(priv)["new"] == 0)

        check("a failed check doesn't break the Path", path.summary(priv)["leetcode_user"] == "hidden")
        n = len(calls)
        db.meta_set(f"leetcode_checked:{pid}", str(db.now()))
        leetcode.check_soon(pid)
        check("opening the Path doesn't ask LeetCode again right away", len(calls) == n)
        check("unlinking stops it", leetcode.link(pid, "")["linked"] == "" and leetcode.username(pid) == "")

        tester = new_profile("Tim")
        t = path.start_test(tester, "topic", "stack")["test"]
        s0, s1 = t["problems"][0]["slug"], t["problems"][1]["slug"]
        with db.tx() as cx:
            cx.execute("UPDATE path_tests SET started_at=? WHERE id=?", (db.now() - 600, t["id"]))
        feed["tim"] = [{"titleSlug": s0, "timestamp": str(db.now() - 30)}, {"titleSlug": s1, "timestamp": str(db.now() - 900)}]
        leetcode.link(tester, "tim")
        check("a linked profile confirms what was solved during the test, not before it", path.current_test(tester, True)["confirmed"] == [s0])
        done = path.finish_test(tester, t["id"], {p["slug"]: {"result": "solved"} for p in t["problems"]})
        check("...and the result says so", done["confirmed_by_leetcode"] == [s0] and done["passed"])
        r = agent.call(tester, "link_leetcode", {"username": ""}, source="test")
        check("Jarvis can link and unlink it", r.get("ok") and leetcode.username(tester) == "")
        c = TestClient(app, base_url="http://localhost")
        check("the API links it too", c.post("/api/path/leetcode", json={"profile_id": tester, "username": "tim"}).json()["linked"] == "tim")
        db.meta_set("other_setting_kept", "yes")
        c.delete(f"/api/profiles/{tester}")
        check("deleting a person leaves nothing of theirs: not the username, not their tests", leetcode.username(tester) == "" and not db.row(
            "SELECT 1 AS x FROM path_tests WHERE profile_id=?", (tester,)) and not db.row("SELECT 1 AS x FROM meta WHERE key LIKE ?", (f"%:{tester}",))
              and db.meta_get("other_setting_kept") == "yes")
    finally:
        leetcode._ask = real_ask


def main():
    db.init()
    t0 = time.time()
    data_files()
    levels()
    pid = new_profile("Alex", {"categories": ["Software"]})
    xp_and_streak(pid)
    goals(pid)
    milestones(pid)
    ladder(pid)
    trail()
    tests_on_the_trail()
    shields_and_year()
    leetcode_link()
    api_and_tools(pid)
    print(f"\n{sum(results.values())}/{len(results)} passed in {time.time() - t0:.1f}s")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
