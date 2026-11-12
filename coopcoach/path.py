"""The Path: Co-op Coach as a game you keep coming back to. Consistency is what lands co-ops.

XP comes from the real work, read from what's already recorded (so it's always right, and nothing has to remember
to award it): preparing and applying, hackathon applications and write-ups, and the NeetCode 150 tracked here. A
daily goal (50 XP by default: one application) and a streak of days it was met; levels with Jarvis names; milestones
Jarvis celebrates once; and the path itself: a long trail through NeetCode's 18 topics with every problem a stop on it,
real-work stops in between (applying, what comes back from companies, people, events), and a chest at the end of each
topic.

The game on top is read from the same records: a crown for solving a problem again a week later and another three weeks
after that, chests for finishing a topic, and three daily quests. Nothing here sets homework: every stop is real work,
any stop can be done in any order, and Jarvis stands at the next unsolved problem.

Tests are proof, not quizzes: a test picks real problems from the 150, and the student solves them on LeetCode against the
clock, with no hints. One per topic, a checkpoint every three topics, and a final. They never lock anything. A linked public
LeetCode profile (leetcode.py) logs solves by itself and confirms test results.

Coding is the NeetCode 150 and nothing else (coding.yaml): 150 LeetCode problems in 18 topics, in the order of
NeetCode's roadmap, which covers most of what coding interviews ask. Tracked, not taught: links only, never the
problem text; each attempt recorded (solved, needed hints, couldn't; how long; notes), the misses brought back for
review, and stars that keep a problem on a list for a job, a kind of role or industry, or a company.
"""

import random
import re
from datetime import date, datetime, timedelta
from functools import lru_cache

from . import db, store
from .config import PKG, read_yaml

XP = {"prepared": 5, "applied": 50, "hackathon": 30, "devpost": 40, "attempt": 5, "review": 10,
      "Easy": 20, "Medium": 30, "Hard": 50, "crown": 15, "chest": 50, "all_150": 500, "quest": 10,
      "test_topic": 100, "test_checkpoint": 200, "test_final": 1000}
TEST_MINUTES = {"Easy": 15, "Medium": 25, "Hard": 40}  # how long each problem gets, about what real assessments allow
TEST_MIX = {"topic": ("Easy", "Medium", "Hard"), "checkpoint": ("Easy", "Medium", "Medium", "Medium", "Hard"),
            "final": ("Easy", "Medium", "Medium", "Medium", "Medium", "Medium", "Medium", "Hard", "Hard", "Hard")}
TEST_SIZE = {k: len(v) for k, v in TEST_MIX.items()}
RETAKE_DAYS = 3
CHECKPOINT_EVERY = 3  # topics
TEST_GRACE = 300  # seconds, for logging the results after the clock ends
SHIELD_EVERY = 7  # days in a row earn a shield: a missed day that doesn't break the streak
MAX_SHIELDS = 2
WEEK_TARGET = 5  # days a week the goal is met
CROWN_DAYS = (7, 21)  # solve it again this long after the last crown (the first comes with the first solve)
LEVEL_ORDER = ("Easy", "Medium", "Hard")
RESULTS = {"solved": "Solved", "hints": "Needed hints", "unsolved": "Couldn't"}
REVIEW_DAYS = (2, 5, 12)  # a miss comes back after 2 days, then 5, then 12
STAR_KINDS = ("job", "track", "company")
_JOBS = " AND job_id NOT LIKE 'hack:%'"  # internships only (hackathon applications count on their own)
GOALS = {20: "Casual", 50: "Regular", 100: "Serious", 200: "Intense"}
DEFAULT_GOAL = 50
LEVELS = [(0, "Booting"), (100, "Hello World"), (300, "Script"), (600, "Process"), (1000, "Daemon"), (1600, "Root Access"),
          (2400, "Kernel"), (3500, "Compiler"), (5000, "Architect"), (7000, "Mainframe"),
          (9500, "Overclocked"), (12500, "Supercomputer"), (16000, "Neural Net"), (20000, "Quantum Core"),
          (25000, "J.A.R.V.I.S.")]  # Jarvis's terminal world
ROLE_OF = {"Software": ["software"], "AI/ML/Data": ["data", "software"], "Quant": ["quant", "software"],
           "Hardware": ["hardware", "software"], "Business/Finance": ["data"], "Product/Design": ["software"]}
NEETCODE_URL = "https://neetcode.io/practice"  # where the seven LeetCode Premium ones are free
_SLUG = re.compile(r"^(?:https?://(?:www\.)?leetcode\.(?:com|cn)/problems/)?([a-z0-9]+(?:-[a-z0-9]+)*)/?(?:[?#].*)?$")


@lru_cache(maxsize=1)
def coding() -> list[dict]:
    """The NeetCode 150 by topic, in roadmap order."""
    raw = read_yaml((PKG / "coding.yaml").read_text(encoding="utf-8"))["topics"]
    return [{**t, "problems": [{"title": p[0], "slug": p[1], "level": p[2],
                                "url": f"https://leetcode.com/problems/{p[1]}/", "premium": "premium" in p[3:],
                                **({"free_url": NEETCODE_URL} if "premium" in p[3:] else {})}
                               for p in t["problems"]]} for t in raw]


def total() -> int:
    return sum(len(t["problems"]) for t in coding())


def roles(pid: int) -> list[str]:
    """Which kinds of role they're going for (software, data, quant, hardware), from their targets."""
    p = store.get_profile(pid) or {"prefs": {}, "profile": {}}
    cats = p["prefs"].get("categories") or p["profile"].get("target_categories") or ["Software"]
    out = [r for c in cats for r in ROLE_OF.get(c, [])]
    return list(dict.fromkeys(out)) or ["software"]


def _day(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _levels(pid: int) -> dict[str, str]:
    """slug -> Easy/Medium/Hard for the NeetCode 150."""
    return {p["slug"]: p["level"] for t in coding() for p in t["problems"]}


def crown_times(first_solve: int | None, solves: list[int]) -> list[int]:
    """When each crown was earned: the first solve, then solving it again CROWN_DAYS later each time."""
    ts = sorted(solves)
    first = first_solve or (ts[0] if ts else None)
    if not first:
        return []
    out = [first]
    for days in CROWN_DAYS:
        nxt = next((t for t in ts if t >= out[-1] + days * 86400), None)
        if nxt is None:
            break
        out.append(nxt)
    return out


def _solve_records(pid: int) -> tuple[dict[str, int], dict[str, list[int]]]:
    """(slug -> first solve, slug -> every solved attempt)."""
    first = {r["slug"]: r["solved_at"] for r in db.rows("SELECT slug, solved_at FROM coding_solved WHERE profile_id=?",
                                                        (pid,))}
    solves: dict[str, list[int]] = {}
    for r in db.rows("SELECT slug, created_at FROM coding_attempts WHERE profile_id=? AND result='solved'", (pid,)):
        solves.setdefault(r["slug"], []).append(r["created_at"])
    return first, solves


def _game_events(pid: int, since: int) -> list[tuple[str, str, int]]:
    """Crowns won again and chests opened."""
    out = []
    first, solves = _solve_records(pid)
    level = _levels(pid)
    for slug in first:
        if slug in level:
            for t in crown_times(first[slug], solves.get(slug, []))[1:]:
                if t >= since:
                    out.append((_day(t), "crown", XP["crown"]))
    done_all = True
    latest = 0
    for t in coding():
        when = [first.get(x["slug"]) for x in t["problems"]]
        if all(when):
            latest = max(latest, *when)
            if max(when) >= since:
                out.append((_day(max(when)), "chest", XP["chest"]))
        else:
            done_all = False
    if done_all and latest >= since:
        out.append((_day(latest), "all_150", XP["all_150"]))
    for r in db.rows("SELECT kind, finished_at FROM path_tests WHERE profile_id=? AND passed=1 AND finished_at>=?", (pid, since)):
        out.append((_day(r["finished_at"]), f"test_{r['kind']}", XP[f"test_{r['kind']}"]))
    return out


# the daily quests: (id, title, the kinds of work that complete it)
QUESTS = [("try", "Log a NeetCode try", {"coding", "attempt", "review", "crown", "test_topic", "test_checkpoint", "test_final"}),
          ("solve", "Solve a new NeetCode problem", {"coding"}),
          ("apply", "Prepare or send an application", {"prepared", "applied", "hackathon"})]


def _quest_events(events: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """Each quest done on a day is worth XP that day."""
    kinds: dict[str, set[str]] = {}
    for day, kind, _ in events:
        kinds.setdefault(day, set()).add(kind)
    return [(day, "quest", XP["quest"]) for day, have in kinds.items() for _, _, want in QUESTS if have & want]


def quests(ev: list[tuple[str, str, int]], today: str) -> list[dict]:
    have = {kind for day, kind, _ in ev if day == today}
    return [{"id": q, "title": title, "done": bool(have & want), "xp": XP["quest"]} for q, title, want in QUESTS]


def _events(pid: int, since: int = 0) -> list[tuple[str, str, int]]:
    """(day, kind, xp) for every bit of work since `since`."""
    out = []
    for r in db.rows("SELECT created_at FROM ready_queue WHERE profile_id=? AND created_at>=?", (pid, since)):
        out.append((_day(r["created_at"]), "prepared", XP["prepared"]))
    for r in db.rows("SELECT job_id, COALESCE(applied_at, updated_at) AS t FROM applications WHERE profile_id=? AND"
                     " status IN ('applied', 'oa', 'interviewing', 'offer', 'rejected') AND"
                     " COALESCE(applied_at, updated_at)>=?", (pid, since)):
        kind = "hackathon" if r["job_id"].startswith("hack:") else "applied"
        out.append((_day(r["t"]), kind, XP[kind]))
    level = _levels(pid)
    for r in db.rows("SELECT slug, solved_at FROM coding_solved WHERE profile_id=? AND solved_at>=?", (pid, since)):
        out.append((_day(r["solved_at"]), "coding", XP[level.get(r["slug"], "Medium")]))
    misses: set[str] = set()  # a later solve of a problem they missed before is a review
    for r in db.rows("SELECT slug, result, created_at FROM coding_attempts WHERE profile_id=? ORDER BY created_at",
                     (pid,)):
        if r["created_at"] >= since:
            if r["result"] != "solved":
                out.append((_day(r["created_at"]), "attempt", XP["attempt"]))
            elif r["slug"] in misses:
                out.append((_day(r["created_at"]), "review", XP["review"]))
        if r["result"] != "solved":
            misses.add(r["slug"])
        else:
            misses.discard(r["slug"])
    for r in db.rows("SELECT created_at FROM documents WHERE profile_id=? AND kind='devpost' AND created_at>=?", (pid, since)):
        out.append((_day(r["created_at"]), "devpost", XP["devpost"]))
    out += _game_events(pid, since)
    return out + _quest_events(out)


def goal(pid: int) -> int:
    p = store.get_profile(pid) or {"prefs": {}}
    g = p["prefs"].get("path_goal")
    return int(g) if g in GOALS or (isinstance(g, int) and 10 <= g <= 500) else DEFAULT_GOAL


def set_goal(pid: int, xp: int) -> dict:
    xp = int(xp)
    if not 10 <= xp <= 500:
        return {"error": "a daily goal is 10 to 500 XP"}
    raw = db.loads(db.row("SELECT prefs_json FROM profiles WHERE id=?", (pid,))["prefs_json"], {}) or {}
    raw["path_goal"] = xp
    with db.tx() as c:
        c.execute("UPDATE profiles SET prefs_json=? WHERE id=?", (db.dumps(raw), pid))
    return {"ok": True, "goal": xp}


def level_of(xp: int) -> dict:
    i = max(k for k, (need, _) in enumerate(LEVELS) if xp >= need)
    nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return {"level": i + 1, "name": LEVELS[i][1], "xp": xp, "from": LEVELS[i][0], "to": nxt,
            "next_name": LEVELS[i + 1][1] if nxt else None}


def _crowns_won(pid: int) -> int:
    """Crowns beyond the one each solve comes with."""
    first, solves = _solve_records(pid)
    return sum(max(0, len(crown_times(t, solves.get(s, []))) - 1) for s, t in first.items())


_COUNTS = {
    "prepared": "ready_queue WHERE profile_id=?",
    "applied": "applications WHERE profile_id=? AND status IN ('applied', 'oa', 'interviewing', 'offer', 'rejected')" + _JOBS,
    "replies": "applications WHERE profile_id=? AND status IN ('oa', 'interviewing', 'offer', 'rejected')" + _JOBS,
    "interviews": "applications WHERE profile_id=? AND status IN ('oa', 'interviewing', 'offer')" + _JOBS,
    "offers": "applications WHERE profile_id=? AND status='offer'" + _JOBS,
    "hackathons": "applications WHERE profile_id=? AND job_id LIKE 'hack:%' AND status != 'saved'",
    "hackathons_in": "applications WHERE profile_id=? AND job_id LIKE 'hack:%' AND status='offer'",
    "devposts": "documents WHERE profile_id=? AND kind='devpost'",
    "tests_passed": "path_tests WHERE profile_id=? AND passed=1",
    "final_passed": "path_tests WHERE profile_id=? AND passed=1 AND kind='final'",
    "attempts": "coding_attempts WHERE profile_id=?",
    "resume": "profiles WHERE id=? AND COALESCE(resume_text, '') != ''",
    "follows_niche": "niche_follows WHERE profile_id=?",
    "follows_track": "track_follows WHERE profile_id=?",
    "inbox": "email_accounts WHERE profile_id=?",
    "events": "event_marks WHERE profile_id=?",
    "people": "contacts WHERE profile_id=? AND (source='you' OR starred=1)",
}
_COUNTS_SQL = "SELECT " + ", ".join(f"(SELECT COUNT(*) FROM {w}) AS {k}" for k, w in _COUNTS.items())  # one round trip


def counts(pid: int) -> dict:
    n = db.row(_COUNTS_SQL, (pid,) * len(_COUNTS))
    level = _levels(pid)
    solved = [r["slug"] for r in db.rows("SELECT slug FROM coding_solved WHERE profile_id=?", (pid,))]
    done = set(solved)
    return {
        **{k: n[k] or 0 for k in _COUNTS if not k.startswith("follows_")},
        "following": (n["follows_niche"] or 0) + (n["follows_track"] or 0),
        "coding": sum(1 for s in solved if s in level),  # of the NeetCode 150
        **{lv.lower(): sum(1 for s in solved if level.get(s) == lv) for lv in LEVEL_ORDER},
        **{f"nc_{t['id']}": sum(1 for x in t["problems"] if x["slug"] in done) for t in coding()},
        "topics_done": sum(1 for t in coding() if all(x["slug"] in done for x in t["problems"])),
        "crowns": _crowns_won(pid),
    }


MILESTONES = [  # (key, title, reached?) in the order they're usually met
    ("first_prepare", "First application prepared", lambda c, s, lv: c["prepared"] >= 1),
    ("applied_1", "First application sent", lambda c, s, lv: c["applied"] >= 1),
    ("applied_5", "5 applications sent", lambda c, s, lv: c["applied"] >= 5),
    ("applied_10", "10 applications sent", lambda c, s, lv: c["applied"] >= 10),
    ("applied_25", "25 applications sent", lambda c, s, lv: c["applied"] >= 25),
    ("applied_50", "50 applications sent", lambda c, s, lv: c["applied"] >= 50),
    ("applied_100", "100 applications sent", lambda c, s, lv: c["applied"] >= 100),
    ("first_reply", "First reply from a company", lambda c, s, lv: c["replies"] >= 1),
    ("first_interview", "First interview or assessment", lambda c, s, lv: c["interviews"] >= 1),
    ("first_offer", "First offer!", lambda c, s, lv: c["offers"] >= 1),
    ("streak_3", "3-day streak", lambda c, s, lv: s >= 3),
    ("streak_7", "7-day streak", lambda c, s, lv: s >= 7),
    ("streak_14", "Two-week streak", lambda c, s, lv: s >= 14),
    ("streak_30", "30-day streak", lambda c, s, lv: s >= 30),
    ("streak_100", "100-day streak", lambda c, s, lv: s >= 100),
    ("coding_1", "First NeetCode problem solved", lambda c, s, lv: c["coding"] >= 1),
    ("coding_10", "10 of the NeetCode 150", lambda c, s, lv: c["coding"] >= 10),
    ("topic_1", "First NeetCode topic finished", lambda c, s, lv: c["topics_done"] >= 1),
    ("coding_25", "25 of the NeetCode 150", lambda c, s, lv: c["coding"] >= 25),
    ("coding_50", "50 of the NeetCode 150", lambda c, s, lv: c["coding"] >= 50),
    ("coding_75", "Halfway through the NeetCode 150", lambda c, s, lv: c["coding"] >= 75),
    ("coding_100", "100 of the NeetCode 150", lambda c, s, lv: c["coding"] >= 100),
    ("coding_150", "The whole NeetCode 150!", lambda c, s, lv: c["coding"] >= 150),
    ("crown_1", "First crown: solved again a week later", lambda c, s, lv: c["crowns"] >= 1),
    ("crown_25", "25 crowns", lambda c, s, lv: c["crowns"] >= 25),
    ("test_1", "First test passed", lambda c, s, lv: c["tests_passed"] >= 1),
    ("test_10", "10 tests passed", lambda c, s, lv: c["tests_passed"] >= 10),
    ("test_final", "Passed the final: the whole NeetCode 150, under the clock", lambda c, s, lv: c["final_passed"] >= 1),
    ("first_medium", "First Medium solved", lambda c, s, lv: c["medium"] >= 1),
    ("first_hard", "First Hard solved", lambda c, s, lv: c["hard"] >= 1),
    ("first_hackathon", "First hackathon application", lambda c, s, lv: c["hackathons"] >= 1),
    ("hackathon_in", "Accepted to a hackathon", lambda c, s, lv: c["hackathons_in"] >= 1),
    ("first_devpost", "First Devpost write-up", lambda c, s, lv: c["devposts"] >= 1),
] + [(f"level_{n}", f"Level {n}: {name}", (lambda n: lambda c, s, lv: lv >= n)(n))
     for n, (_, name) in enumerate(LEVELS, 1) if n >= 2]

MILESTONES += [(f"topic_{t['id']}", f"{t['name']} finished: chest opened!",
                (lambda key, need: lambda c, s, lv: c.get(key, 0) >= need)(f"nc_{t['id']}", len(t["problems"])))
               for t in coding()]
_PREP = {"kind": "prepare"}
_TRACKER = {"kind": "go", "view": "tracker"}
_EVENTS = {"kind": "go", "view": "events"}
_PEOPLE = {"kind": "go", "view": "recruiters"}
# the real-work goals, smallest first (the job-hunt lane groups them into STAGES), growing along the lane.
# (id, title, icon, what counts, goal, what to press)
_WORK = [
    ("resume", "Add your resume", "📄", "resume", 1, {"kind": "go", "view": "documents"}),
    ("follow", "Follow a track", "🎯", "following", 1, {"kind": "go", "view": "tracks"}),
    ("inbox", "Connect your inbox", "📬", "inbox", 1, {"kind": "settings", "section": "tracking"}),
    ("prep1", "Prepare your first application", "✨", "prepared", 1, _PREP),
    ("apply1", "Send your first application", "📨", "applied", 1, _PREP),
    ("event1", "Save an event to go to", "📅", "events", 1, _EVENTS),
    ("apply3", "Send 3 applications", "📨", "applied", 3, _PREP),
    ("people1", "Add someone you know at a company", "👥", "people", 1, _PEOPLE),
    ("apply5", "Send 5 applications", "📨", "applied", 5, _PREP),
    ("reply1", "Hear back from a company", "📩", "replies", 1, _TRACKER),
    ("prep10", "Prepare 10 applications", "✨", "prepared", 10, _PREP),
    ("event3", "Save 3 events", "📅", "events", 3, _EVENTS),
    ("apply10", "Send 10 applications", "📨", "applied", 10, _PREP),
    ("hack1", "Apply to a hackathon", "🏁", "hackathons", 1, _EVENTS),
    ("people3", "Add 3 people at companies you want", "👥", "people", 3, _PEOPLE),
    ("apply15", "Send 15 applications", "📨", "applied", 15, _PREP),
    ("interview1", "Land an interview or assessment", "🎤", "interviews", 1, _TRACKER),
    ("apply20", "Send 20 applications", "📨", "applied", 20, _PREP),
    ("reply3", "Hear back from 3 companies", "📩", "replies", 3, _TRACKER),
    ("event5", "Save 5 events", "📅", "events", 5, _EVENTS),
    ("apply25", "Send 25 applications", "📨", "applied", 25, _PREP),
    ("people5", "Add 5 people", "👥", "people", 5, _PEOPLE),
    ("interview3", "Land 3 interviews or assessments", "🎤", "interviews", 3, _TRACKER),
    ("apply30", "Send 30 applications", "📨", "applied", 30, _PREP),
    ("prep25", "Prepare 25 applications", "✨", "prepared", 25, _PREP),
    ("apply40", "Send 40 applications", "📨", "applied", 40, _PREP),
    ("devpost1", "Write up a hackathon project", "🏆", "devposts", 1, _EVENTS),
    ("reply5", "Hear back from 5 companies", "📩", "replies", 5, _TRACKER),
    ("apply50", "Send 50 applications", "📨", "applied", 50, _PREP),
    ("people10", "Add 10 people", "👥", "people", 10, _PEOPLE),
    ("interview5", "Land 5 interviews or assessments", "🎤", "interviews", 5, _TRACKER),
    ("apply60", "Send 60 applications", "📨", "applied", 60, _PREP),
    ("hack3", "Apply to 3 hackathons", "🏁", "hackathons", 3, _EVENTS),
    ("apply75", "Send 75 applications", "📨", "applied", 75, _PREP),
    ("interview10", "Land 10 interviews or assessments", "🎤", "interviews", 10, _TRACKER),
    ("offer1", "Get an offer", "🎉", "offers", 1, _TRACKER),
    ("apply100", "Send 100 applications", "📨", "applied", 100, _PREP),
    ("people25", "Add 25 people", "👥", "people", 25, _PEOPLE),
    ("apply150", "Send 150 applications", "📨", "applied", 150, _PREP),
]
# the job hunt's lane: the real-work goals in stages. A stage is done when its goals are, in any order; the lane moves
# with the work itself, never waiting on coding (that's the NeetCode lane beside it)
STAGES = [
    ("setup", "Get set up", ("resume", "follow", "inbox", "prep1", "apply1")),
    ("momentum", "Momentum", ("event1", "apply3", "people1", "apply5", "reply1")),
    ("pipeline", "Build a pipeline", ("prep10", "event3", "apply10", "hack1", "people3", "apply15")),
    ("noticed", "Get noticed", ("interview1", "apply20", "reply3", "event5", "apply25", "people5")),
    ("demand", "In demand", ("interview3", "apply30", "prep25", "apply40", "devpost1", "reply5")),
    ("closing", "Closing in", ("apply50", "people10", "interview5", "apply60", "hack3", "apply75")),
    ("offers", "Offer season", ("interview10", "offer1", "apply100", "people25", "apply150")),
]


_MAIN_ROAD = ("resume", "inbox", "prep", "apply")  # the steps that are always the next thing to do


def hunt(c: dict) -> list[dict]:
    """The job-hunt lane: each stage's goals with how far along they are. Jarvis stands where the student really is:
    the first step of the main road not done (resume, inbox, preparing and sending applications). A side goal (following
    a track, people, events, hackathons) or one that's up to employers (replies, interviews, offers) only holds him if
    nothing after it is done yet; once they've moved past it, it stays open but he moves on. Goals for pages the app is
    hiding (features.py) are left out."""
    from . import features
    by_id = {w[0]: w for w in _WORK}
    out = []
    for n, (sid, name, ids) in enumerate(STAGES, 1):
        goals = []
        for gid in ids:
            if not features.goal_shown(gid):
                continue
            _, title, icon, key, need, action = by_id[gid]
            goals.append({"kind": "work", "id": gid, "title": title, "icon": icon, "have": min(c.get(key, 0), need),
                          "need": need, "done": c.get(key, 0) >= need, "action": action})
        out.append({"id": sid, "n": n, "name": name, "done": all(g["done"] for g in goals),
                    "have": sum(g["done"] for g in goals), "total": len(goals), "goals": goals})
    road = [g for st in out for g in st["goals"]]
    here = next((g for i, g in enumerate(road) if not g["done"] and (
        g["id"].startswith(_MAIN_ROAD) or not any(x["done"] for x in road[i + 1:]))), None)
    if here:
        here["state"] = "here"
    for st in out:
        for g in st["goals"]:
            g.setdefault("state", "done" if g["done"] else "ahead")
        st["has_here"] = any(g["state"] == "here" for g in st["goals"])
    return out


def journey(pid: int, c: dict, lad: dict) -> list[dict]:
    """The NeetCode lane: one section per topic, in roadmap order: its problems, its chest, its test. Jarvis stands at the
    first unsolved problem; every stop stays open, whatever order the work happens in."""
    here = next((p["slug"] for p in lad["problems"] if not p["solved_at"]), None)
    tests = _test_state(pid)
    by_topic: dict[str, list[dict]] = {}
    for p in lad["problems"]:
        by_topic.setdefault(p["topic_id"], []).append(p)
    out = []
    for n, t in enumerate(lad["by_topic"], 1):
        ps = by_topic[t["id"]]
        nodes = []
        for p in ps:
            nodes.append({"kind": "problem", "slug": p["slug"], "title": p["title"], "level": p["level"], "xp": p["xp"],
                          "url": p["url"], "premium": p["premium"], "free_url": p.get("free_url"), "topic": t["name"],
                          "topic_id": t["id"], "crowns": p["crowns"], "attempts": p["attempts"],
                          "state": "solved" if p["solved_at"] else "here" if p["slug"] == here else
                          "due" if p in lad["due"] else "ahead", "due": p in lad["due"]})
        nodes.append({"kind": "chest", "title": f"{t['name']} chest", "xp": XP["chest"],
                      "open": t["solved"] == t["total"], "left": t["total"] - t["solved"]})
        nodes.append(_test_stop("topic", t["id"], f"{t['name']} test", tests, t["solved"] * 2 >= t["total"]))
        if n % CHECKPOINT_EVERY == 0 and n < len(lad["by_topic"]):
            nodes.append(_test_stop("checkpoint", f"c{n}", f"Checkpoint: the last {CHECKPOINT_EVERY} topics", tests,
                                    sum(x["solved"] for x in lad["by_topic"][n - CHECKPOINT_EVERY:n]) * 2 >= sum(
                                        x["total"] for x in lad["by_topic"][n - CHECKPOINT_EVERY:n])))
        if n == len(lad["by_topic"]):
            nodes.append(_test_stop("final", "final", "The final: all 150", tests, lad["solved"] * 2 >= lad["total"]))
        out.append({"id": t["id"], "n": n, "name": t["name"], "solved": t["solved"], "total": t["total"],
                    "done": t["solved"] == t["total"], "has_here": any(x.get("state") == "here" for x in nodes),
                    "nodes": nodes})
    return out


def _test_state(pid: int) -> dict[str, dict]:
    """For each test: the latest try at it, whether one's been passed."""
    out: dict[str, dict] = {}
    _expire_tests(pid)
    for r in db.rows("SELECT id, scope, started_at, deadline, finished_at, passed FROM path_tests WHERE profile_id=? ORDER BY"
                     " started_at", (pid,)):
        s = out.setdefault(r["scope"], {"passed": False, "tries": 0, "last": None})
        s["tries"] += 1
        s["last"] = r
        s["passed"] = s["passed"] or bool(r["passed"])
    return out


def _test_stop(kind: str, scope: str, title: str, tests: dict, ready: bool) -> dict:
    s = tests.get(scope) or {"passed": False, "tries": 0, "last": None}
    last = s["last"]
    running = bool(last and not last["finished_at"])
    retake = last["finished_at"] + RETAKE_DAYS * 86400 if last and last["finished_at"] and not s["passed"] else None
    return {"kind": "test", "test_kind": kind, "scope": scope, "title": title, "xp": XP[f"test_{kind}"], "size": TEST_SIZE[kind],
            "about_minutes": sum(TEST_MINUTES[lv] for lv in TEST_MIX[kind]),
            "passed": s["passed"], "tries": s["tries"], "running": running, "test_id": last["id"] if running else None,
            "ready": ready, "retake_at": retake if retake and retake > db.now() else None}


def next_steps(stages: list[dict], lad: dict) -> list[str]:
    """What's next, in words: the next problem, where Jarvis stands on the job hunt, and the next goal still open."""
    nx = lad["next"]
    steps = [f"{'Review' if nx.get('review_at') else 'NeetCode'}: {nx['title']} ({nx['level']}, {nx['topic']})"] if nx else []
    goals = [g for s in stages for g in s["goals"] if not g["done"]]
    return steps + [g["title"] for g in sorted(goals, key=lambda g: g["state"] != "here")][:2]  # (stable: else in order)


def _streak(by_day: dict, g: int, today: date) -> tuple[int, int, int]:
    """(the streak, the best one, shields held). Every SHIELD_EVERY days in a row earn a shield, and a missed day spends
    one rather than ending the streak: it doesn't add to it. Today only counts once it's met: it isn't over yet."""
    streak = best = shields = 0
    for k in range(400, -1, -1):
        met = by_day.get((today - timedelta(days=k)).isoformat(), 0) >= g
        if met:
            streak += 1
            if streak % SHIELD_EVERY == 0:
                shields = min(MAX_SHIELDS, shields + 1)
        elif k == 0:
            pass
        elif shields and streak:
            shields -= 1
        else:
            streak = shields = 0
        best = max(best, streak)
    return streak, best, shields


def summary(pid: int) -> dict:
    """Everything the Path and Today show: today's XP against the goal, the streak, the level, the week, the path,
    and milestones not yet celebrated."""
    ev = _events(pid)
    by_day: dict[str, int] = {}
    for d, _, xp in ev:
        by_day[d] = by_day.get(d, 0) + xp
    g = goal(pid)
    today = date.today()
    streak, best, shields = _streak(by_day, g, today)
    total = sum(by_day.values())
    lv = level_of(total)
    c = counts(pid)
    seen = set(db.loads(db.meta_get(f"path_seen:{pid}"), []) or [])
    reached = [{"key": k, "title": t} for k, t, test in MILESTONES if test(c, max(streak, best), lv["level"])]
    week = [{"day": (today - timedelta(days=k)).isoformat(), "xp": by_day.get((today - timedelta(days=k)).isoformat(), 0)}
            for k in range(6, -1, -1)]
    monday = today - timedelta(days=today.weekday())
    lad = ladder(pid)
    sections = journey(pid, c, lad)
    stages = hunt(c)
    running = next((n["test_id"] for x in sections for n in x["nodes"] if n.get("running")), None)
    from . import leetcode
    leetcode.check_soon(pid)
    return {"leetcode_user": leetcode.username(pid), "leetcode_syncing": leetcode.checking(pid),
            "year": {k: v for k, v in by_day.items() if k >= (today - timedelta(days=371)).isoformat()},
            "test": _test_view(db.row("SELECT * FROM path_tests WHERE id=?", (running,))) if running else None,
            "today": by_day.get(today.isoformat(), 0), "goal": g, "goals": GOALS, "streak": streak, "best_streak": best,
            "shields": shields, "week_target": WEEK_TARGET,
            "week_met": sum(by_day.get((monday + timedelta(days=k)).isoformat(), 0) >= g for k in range(7)),
            "level": lv, "week": week, "counts": c, "sections": sections, "hunt": stages, "next_steps": next_steps(stages, lad),
            "stops": sum(len(x["nodes"]) for x in sections), "hunt_goals": sum(x["total"] for x in stages), "quests": quests(ev, today.isoformat()), "roles": roles(pid),
            "coding": {"next": lad["next"], "reviews_due": len(lad["due"]), "by_level": lad["by_level"],
                       "solved": lad["solved"], "total": lad["total"], "name": lad["name"],
                       "sharpen": len(lad["sharpen"]), "crowns": c["crowns"], "topics_done": c["topics_done"],
                       "topic": next((t for t in lad["by_topic"] if lad["next"] and t["id"] == lad["next"]["topic_id"]),
                                     None)},
            "milestones": reached, "new_milestones": [m for m in reached if m["key"] not in seen]}


def seen(pid: int, keys: list[str]) -> None:
    have = set(db.loads(db.meta_get(f"path_seen:{pid}"), []) or [])
    db.meta_set(f"path_seen:{pid}", db.dumps(sorted(have | {str(k)[:40] for k in keys})))


def first_visit(pid: int) -> None:
    """Milestones already reached before the Path existed are marked seen, so nobody gets twenty pop-ups at once."""
    if db.meta_get(f"path_seen:{pid}") is None:
        seen(pid, [m["key"] for m in summary(pid)["milestones"]])


# --- the LeetCode ladder: tracked, not taught ---------------------------------------------------------------

def _problems(pid: int) -> list[dict]:
    """The NeetCode 150 in roadmap order: topic by topic, each topic's problems in NeetCode's order."""
    return [{**p, "topic": t["name"], "topic_id": t["id"], "n": i}
            for i, (t, p) in enumerate((t, p) for t in coding() for p in t["problems"])]


def ladder(pid: int) -> dict:
    """The ladder with where they are on each problem: solved (when), the last attempt (result, time, notes), when a
    miss comes back for review, and what they starred it for."""
    attempts: dict[str, list[dict]] = {}
    for r in db.rows("SELECT slug, result, seconds, notes, created_at FROM coding_attempts WHERE profile_id=?"
                     " ORDER BY created_at", (pid,)):
        attempts.setdefault(r["slug"], []).append(dict(r))
    solved = {r["slug"]: r["solved_at"] for r in db.rows("SELECT slug, solved_at FROM coding_solved WHERE profile_id=?",
                                                         (pid,))}
    stars: dict[str, list[dict]] = {}
    for r in db.rows("SELECT slug, tag_kind, tag, label FROM coding_stars WHERE profile_id=? ORDER BY created_at", (pid,)):
        stars.setdefault(r["slug"], []).append({"kind": r["tag_kind"], "tag": r["tag"], "label": r["label"] or r["tag"]})
    now = db.now()
    probs, due, sharpen = [], [], []  # sharpen: solved a while ago, ready for another crown (never required)
    for p in _problems(pid):
        tries = attempts.get(p["slug"], [])
        last = tries[-1] if tries else None
        misses = 0
        for a in reversed(tries):  # misses since the last solve
            if a["result"] == "solved":
                break
            misses += 1
        review = None
        if last and last["result"] != "solved":
            review = last["created_at"] + REVIEW_DAYS[min(misses, len(REVIEW_DAYS)) - 1] * 86400
        crowns = crown_times(solved.get(p["slug"]), [a["created_at"] for a in tries if a["result"] == "solved"]) \
            if solved.get(p["slug"]) else []
        sharpen_at = crowns[-1] + CROWN_DAYS[len(crowns) - 1] * 86400 if 0 < len(crowns) <= len(CROWN_DAYS) else None
        row = {**p, "xp": XP[p["level"]], "solved_at": solved.get(p["slug"]), "attempts": len(tries),
               "last": last, "review_at": review, "stars": stars.get(p["slug"], []), "crowns": len(crowns),
               "sharpen_at": sharpen_at, "best_seconds": min((a["seconds"] for a in tries if a["result"] == "solved"
                                                              and a["seconds"]), default=None)}
        probs.append(row)
        if review and review <= now:
            due.append(row)
        elif sharpen_at and sharpen_at <= now:
            sharpen.append(row)
    # a due review, else the roadmap, else (all 150 done) another crown
    nxt = due[0] if due else next((p for p in probs if not p["solved_at"]), sharpen[0] if sharpen else None)
    by_level = {lv: {"solved": sum(1 for p in probs if p["level"] == lv and p["solved_at"]),
                     "total": sum(1 for p in probs if p["level"] == lv)} for lv in LEVEL_ORDER}
    by_topic = [{"id": t["id"], "name": t["name"], "total": len(t["problems"]),
                 "solved": sum(1 for p in probs if p["topic_id"] == t["id"] and p["solved_at"])} for t in coding()]
    lists: dict[str, dict] = {}
    for p in probs:
        for s in p["stars"]:
            k = f"{s['kind']}:{s['tag']}"
            lists.setdefault(k, {"kind": s["kind"], "tag": s["tag"], "label": s["label"], "slugs": []})["slugs"].append(
                p["slug"])
    return {"problems": probs, "next": nxt, "due": due, "sharpen": sharpen, "by_level": by_level, "by_topic": by_topic,
            "solved": sum(1 for p in probs if p["solved_at"]), "total": len(probs), "lists": list(lists.values()),
            "results": RESULTS, "name": "NeetCode 150"}


def _find(pid: int, problem: str) -> dict | None:
    """A problem on their ladder by slug, link or title."""
    want = (problem or "").strip()
    m = _SLUG.match(want.lower())
    slug = m.group(1) if m else ""
    norm = re.sub(r"[^a-z0-9]+", " ", want.lower()).strip()
    probs = _problems(pid)
    return next((p for p in probs if p["slug"] == slug), None) or next(
        (p for p in probs if re.sub(r"[^a-z0-9]+", " ", p["title"].lower()).strip() == norm), None)


def record(pid: int, problem: str, result: str, seconds: int | None = None, notes: str = "") -> dict:
    """One attempt at a problem: solved, needed hints, or couldn't; how long it took; notes. The first solve is what
    earns the problem's XP; a miss comes back for review."""
    p = _find(pid, problem)
    if not p:
        return {"error": "the Path tracks the NeetCode 150 only, and that isn't one of them"}
    result = {"solved": "solved", "hints": "hints", "needed hints": "hints", "unsolved": "unsolved",
              "couldn't": "unsolved", "couldnt": "unsolved", "failed": "unsolved"}.get((result or "").lower().strip())
    if not result:
        return {"error": "result is solved, hints or unsolved"}
    seconds = max(0, min(int(seconds or 0), 6 * 3600)) or None
    with db.tx() as c:
        return _record(c, pid, p, result, seconds, notes, db.now())


def _record(c, pid: int, p: dict, result: str, seconds: int | None, notes: str, now: int) -> dict:
    """An already validated attempt inside the caller's transaction (a test commits all its attempts together)."""
    already = c.execute("SELECT solved_at FROM coding_solved WHERE profile_id=? AND slug=?", (pid, p["slug"])).fetchone()
    before = [r["created_at"] for r in c.execute("SELECT created_at FROM coding_attempts WHERE profile_id=? AND slug=? AND"
                                                 " result='solved'", (pid, p["slug"])).fetchall()] if already else []
    aid = c.execute("INSERT INTO coding_attempts (profile_id, slug, result, seconds, notes, created_at) VALUES"
                    " (?,?,?,?,?,?)", (pid, p["slug"], result, seconds, (notes or "").strip()[:2000] or None, now)).lastrowid
    first = result == "solved" and c.execute(
        "INSERT OR IGNORE INTO coding_solved (profile_id, slug, solved_at) VALUES (?,?,?)", (pid, p["slug"], now)).rowcount == 1
    crowned = bool(already) and result == "solved" and len(crown_times(already["solved_at"], [*before, now])) > \
        len(crown_times(already["solved_at"], before))
    xp = XP[p["level"]] if first else XP["attempt"] if result != "solved" else XP["crown"] if crowned else 0
    return {"ok": True, "problem": p["title"], "level": p["level"], "result": RESULTS[result], "first_solve": first,
            "crowned": crowned, "xp": xp, "attempt_id": aid, "_undo": {"tool": "undo_problem_attempt", "args": {"attempt_id": aid}}}


def undo_attempt(pid: int, attempt_id: int) -> dict:
    a = db.row("SELECT slug FROM coding_attempts WHERE profile_id=? AND id=?", (pid, attempt_id))
    if not a:
        return {"error": "that attempt is gone"}
    with db.tx() as c:
        c.execute("DELETE FROM coding_attempts WHERE profile_id=? AND id=?", (pid, attempt_id))
        if not c.execute("SELECT 1 FROM coding_attempts WHERE profile_id=? AND slug=? AND result='solved'",
                         (pid, a["slug"])).fetchone():
            c.execute("DELETE FROM coding_solved WHERE profile_id=? AND slug=?", (pid, a["slug"]))
    return {"ok": True}


def mark_solved(pid: int, slug: str, solved: bool = True) -> dict:
    """The quick tick (solved, no details), or taking a solve back."""
    if not _find(pid, slug):
        return {"error": "not one of the NeetCode 150"}
    if solved:
        out = record(pid, slug, "solved")
        return {"ok": True, "slug": slug, "solved": True, "xp": out.get("xp", 0)}
    with db.tx() as c:
        c.execute("DELETE FROM coding_solved WHERE profile_id=? AND slug=?", (pid, slug))
        c.execute("DELETE FROM coding_attempts WHERE profile_id=? AND slug=? AND result='solved'", (pid, slug))
    return {"ok": True, "slug": slug, "solved": False}


def mark_solved_many(pid: int, slugs: list[str]) -> dict:
    """Ticking off several at once: ones solved before Co-op Coach was keeping track (LeetCode only shows anyone the
    last 20 solves)."""
    have = {r["slug"] for r in db.rows("SELECT slug FROM coding_solved WHERE profile_id=?", (pid,))}
    done, xp = [], 0
    for slug in dict.fromkeys(str(x) for x in slugs[:150]):
        if slug in have or not _find(pid, slug):
            continue
        xp += record(pid, slug, "solved").get("xp", 0)
        done.append(slug)
    return {"ok": True, "marked": done, "xp": xp}


def star(pid: int, problem: str, kind: str, tag: str, label: str = "", on: bool = True) -> dict:
    """Keep a problem on a list: for a job (an application or saved posting), a kind of role or industry ("Quant",
    "Big Tech"), or a company."""
    p = _find(pid, problem)
    if not p:
        return {"error": "not one of the NeetCode 150"}
    if kind not in STAR_KINDS:
        return {"error": f"star it for a {', a '.join(STAR_KINDS)}"}
    tag = (tag or "").strip()[:120]
    if not tag:
        return {"error": "for which one?"}
    if kind == "job" and not label:
        a = db.row("SELECT COALESCE(j.company, a.company) AS company, COALESCE(j.title, a.title) AS title FROM"
                   " applications a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.profile_id=? AND a.job_id=?", (pid, tag)) \
            or db.row("SELECT company, title FROM jobs WHERE id=?", (tag,))
        if not a:
            return {"error": "no such job"}
        label = f"{a['company']} · {a['title']}"
    with db.tx() as c:
        if on:
            c.execute("INSERT OR REPLACE INTO coding_stars (profile_id, slug, tag_kind, tag, label, created_at) VALUES"
                      " (?,?,?,?,?,?)", (pid, p["slug"], kind, tag, (label or tag)[:160], db.now()))
        else:
            c.execute("DELETE FROM coding_stars WHERE profile_id=? AND slug=? AND tag_kind=? AND tag=?",
                      (pid, p["slug"], kind, tag))
    return {"ok": True, "problem": p["title"], "for": label or tag, "on": on,
            "_undo": {"tool": "star_problem", "args": {"problem": p["slug"], "kind": kind, "tag": tag, "on": not on}}}


# --- tests: real problems, on LeetCode, against the clock -------------------------------------------------------------

def _test_pool(kind: str, scope: str) -> list[dict]:
    topics = coding()
    if kind == "topic":
        return [{**p, "topic": t["name"]} for t in topics if t["id"] == scope for p in t["problems"]]
    if kind == "checkpoint":
        m = re.fullmatch(r"c(\d+)", scope)
        n = int(m.group(1)) if m else 0
        if not (0 < n <= len(topics) and n % CHECKPOINT_EVERY == 0):
            return []
        return [{**p, "topic": t["name"]} for t in topics[n - CHECKPOINT_EVERY:n] for p in t["problems"]]
    return [{**p, "topic": t["name"]} for t in topics for p in t["problems"]] if kind == "final" and scope == "final" else []


def _pick(pid: int, kind: str, scope: str, pool: list[dict], tries: int) -> list[dict]:
    """A fair test: harder than the topic's average (no all-Easy test), not what they solved in the past week, and a
    different set on a retake where it can be. The same person and try always get the same one."""
    rng = random.Random(f"{pid}:{scope}:{tries}")
    recent = {r["slug"] for r in db.rows("SELECT slug FROM coding_attempts WHERE profile_id=? AND result='solved' AND created_at>?",
                                         (pid, db.now() - 7 * 86400))}
    fresh = [p for p in pool if p["slug"] not in recent] or pool
    size = min(TEST_SIZE[kind], len(pool))
    by = {lv: [p for p in fresh if p["level"] == lv] for lv in LEVEL_ORDER}
    for lv in by:
        rng.shuffle(by[lv])
    picked: list[dict] = []
    for lv in TEST_MIX[kind]:
        if by[lv]:
            picked.append(by[lv].pop())
    rest = [p for lv in ("Medium", "Hard", "Easy") for p in by[lv]] + [p for p in pool if p not in fresh]
    while len(picked) < size and rest:
        p = rest.pop(0)
        if p not in picked:
            picked.append(p)
    return sorted(picked[:size], key=lambda p: LEVEL_ORDER.index(p["level"]))


def _test_view(r: dict) -> dict:
    known = {p["slug"]: {**p, "topic": t["name"]} for t in coding() for p in t["problems"]}
    slugs = db.loads(r["slugs"], []) or []
    return {"id": r["id"], "kind": r["kind"], "scope": r["scope"], "started_at": r["started_at"], "deadline": r["deadline"],
            "seconds_left": max(0, r["deadline"] - db.now()), "finished": bool(r["finished_at"]), "passed": bool(r["passed"]),
            "problems": [{k: known[s].get(k) for k in ("slug", "title", "level", "url", "topic", "premium", "free_url")}
                         for s in slugs if s in known], "results": db.loads(r["results"], None)}


def _expire_tests(pid: int) -> None:
    """A test left running well past its clock counts as not passed."""
    late = db.this_hour() - 6 * 3600
    for r in db.rows("SELECT id FROM path_tests WHERE profile_id=? AND finished_at IS NULL AND deadline<?", (pid, late)):
        with db.tx() as c:
            c.execute("UPDATE path_tests SET finished_at=deadline, passed=0, results=? WHERE id=? AND finished_at IS NULL",
                      (db.dumps({"note": "The time ran out"}), r["id"]))


def start_test(pid: int, kind: str, scope: str) -> dict:
    """Start a test: it picks the problems and starts the clock. If one's already running, that one."""
    if kind not in TEST_SIZE:
        return {"error": "a test is a topic, a checkpoint or the final"}
    if kind == "topic":  # its id or its name
        scope = next((t["id"] for t in coding() if str(scope).strip().lower() in (t["id"], t["name"].lower())), scope)
    pool = _test_pool(kind, scope)
    if not pool:
        return {"error": "there's no such test"}
    _expire_tests(pid)
    running = db.row("SELECT * FROM path_tests WHERE profile_id=? AND finished_at IS NULL", (pid,))
    if running:
        return {"ok": True, "already_running": True, "test": _test_view(running)}
    prior = db.rows("SELECT finished_at, passed FROM path_tests WHERE profile_id=? AND scope=? ORDER BY started_at", (pid, scope))
    if prior and not any(r["passed"] for r in prior) and prior[-1]["finished_at"] + RETAKE_DAYS * 86400 > db.now():
        days = (prior[-1]["finished_at"] + RETAKE_DAYS * 86400 - db.now()) // 86400 + 1
        return {"error": f"You can retake this in {days} day{'s' if days != 1 else ''}: review what you missed first"}
    picked = _pick(pid, kind, scope, pool, len(prior))
    now = db.now()
    with db.tx() as c:
        tid = c.execute("INSERT INTO path_tests (profile_id, kind, scope, slugs, started_at, deadline) VALUES (?,?,?,?,?,?)",
                        (pid, kind, scope, db.dumps([p["slug"] for p in picked]), now,
                         now + sum(TEST_MINUTES[p["level"]] for p in picked) * 60)).lastrowid
    return {"ok": True, "test": _test_view(db.row("SELECT * FROM path_tests WHERE id=?", (tid,)))}


def current_test(pid: int, check_leetcode: bool = False) -> dict | None:
    """The running test. With check_leetcode: which of its problems a linked profile shows as accepted since it started."""
    _expire_tests(pid)
    r = db.row("SELECT * FROM path_tests WHERE profile_id=? AND finished_at IS NULL", (pid,))
    if not r:
        return None
    out = _test_view(r)
    if check_leetcode:
        from . import leetcode
        out["confirmed"] = sorted(leetcode.solved_between(pid, [p["slug"] for p in out["problems"]], r["started_at"], db.now()))
    return out


def finish_test(pid: int, test_id: int, results: dict) -> dict:
    """How each problem went: {slug: {result: solved|hints|unsolved, minutes, notes}}. Every one is logged like any other try.
    Passing means every problem solved, no hints, inside the time. A linked LeetCode profile confirms what it can."""
    from . import leetcode
    r = db.row("SELECT * FROM path_tests WHERE id=? AND profile_id=?", (test_id, pid))
    if not r:
        return {"error": "there's no such test"}
    if r["finished_at"]:
        return _finished_test(r)
    if not isinstance(results, dict):
        return {"error": "results says how each problem went"}
    slugs = db.loads(r["slugs"], []) or []
    now = db.now()
    in_time = now <= r["deadline"] + TEST_GRACE
    confirmed = leetcode.solved_between(pid, slugs, r["started_at"], now)
    rows, attempts = [], []
    known = {p["slug"]: p for t in coding() for p in t["problems"]}
    for s in slugs:
        one = results.get(s) if isinstance(results.get(s), dict) else {}
        said = str(one.get("result") or "unsolved")
        said = said if said in RESULTS else "unsolved"
        try:
            secs = max(0, min(round(float(one.get("minutes") or 0) * 60), 6 * 3600))
        except (TypeError, ValueError, OverflowError):
            secs = 0
        kind_note = {"topic": "In a topic test", "checkpoint": "In a checkpoint", "final": "In the final"}[r["kind"]]
        attempts.append((known[s], said, secs or None, f"{kind_note}. {str(one.get('notes') or '').strip()}".strip()[:500]))
        rows.append({"slug": s, "result": said, "minutes": round(secs / 60) or None, "confirmed": s in confirmed})
    missed = [x["slug"] for x in rows if x["result"] != "solved"]
    passed = in_time and not missed
    saved = {"problems": rows, "in_time": in_time}
    with db.tx() as c:
        # This conditional write claims the test across threads and processes. Its attempts and final state
        # commit together; a competing request (or a retry after a lost response) returns the saved result.
        claimed = c.execute("UPDATE path_tests SET finished_at=?, passed=?, results=? WHERE id=? AND profile_id=?"
                            " AND finished_at IS NULL", (now, int(passed), db.dumps(saved), test_id, pid)).rowcount
        if not claimed:
            finished = c.execute("SELECT * FROM path_tests WHERE id=? AND profile_id=?", (test_id, pid)).fetchone()
            return _finished_test(finished) if finished else {"error": "there's no such test"}
        for p, said, secs, note in attempts:
            _record(c, pid, p, said, secs, note, now)
    return _finished_test({**r, "passed": passed, "results": db.dumps(saved)}, retry=False)


def _finished_test(r, retry: bool = True) -> dict:
    saved = db.loads(r["results"], {}) or {}
    if "problems" not in saved:  # stopped early or expired, rather than completed
        return {"error": "that test is already finished"}
    rows, passed, in_time = saved["problems"], bool(r["passed"]), saved["in_time"]
    missed = [x["slug"] for x in rows if x["result"] != "solved"]
    return {"ok": True, "passed": passed, "in_time": in_time, "problems": rows, "missed": missed,
            "xp": XP[f"test_{r['kind']}"] if passed else 0, "already_finished": retry,
            "confirmed_by_leetcode": sorted(x["slug"] for x in rows if x.get("confirmed")),
            "retake_in_days": 0 if passed else RETAKE_DAYS}


def give_up_test(pid: int, test_id: int) -> dict:
    with db.tx() as c:
        n = c.execute("UPDATE path_tests SET finished_at=?, passed=0, results=? WHERE id=? AND profile_id=? AND finished_at IS NULL",
                      (db.now(), db.dumps({"note": "Stopped early"}), test_id, pid)).rowcount
    return {"ok": bool(n), "retake_in_days": RETAKE_DAYS} if n else {"error": "there's no such test running"}
