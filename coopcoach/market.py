"""MATCH, part M and A: what your market asks for, and how your resume answers it.

M (match the market): `summary()` reads the postings that fit you best (your top 30 or so that you can actually apply
to, each with a full description) and counts what they ask for. Gaps that show up across 30 postings are the ones
worth closing, rather than one posting's wish list. `pattern()` looks at what you've actually applied to and starred,
which after 20-30 picks says more about what you want than the targets you typed in.

A (assemble the resume): `fit()` checks each skill the market asks for against your resume: already in Skills, shown
in your bullets but missing from Skills (surface it), or not there at all (confirm you have it, or plan a project
that builds it). Nothing is added without you: `add_skill()` only runs when you press the button.
"""

import re
import statistics
import threading
import time
from collections import Counter

from . import companies, db, routes_docs, store
from . import resume_studio as rs
from .textutil import CATEGORIES, SKILLS, classify_category, detect_skills, senior_title

SAMPLE = 30           # postings in the market sample
MIN_DESC = 300        # characters: shorter descriptions are only a title and a link
MIN_SHARE = 0.15      # a skill asked by fewer than this share of the sample isn't a market gap
LANGUAGES = {"Python", "Java", "C++", "C", "C#", "JavaScript", "TypeScript", "Go", "Rust", "Kotlin", "Swift", "R", "SQL",
             "MATLAB", "Scala", "Ruby", "PHP", "Bash", "Verilog/VHDL", "Assembly", "OCaml/Haskell", "Dart/Flutter",
             "Solidity", "HTML/CSS"}
STATUS_LABEL = {"listed": "In your Skills", "surface": "In your bullets, not in Skills", "known": "You've said you know it",
                "missing": "Not on your resume"}


def _targets(prof: dict, category: str) -> list[str]:
    if category and category in CATEGORIES:
        return [category]
    return prof["prefs"].get("categories") or prof["profile"].get("target_categories") or []


def _descriptions(ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in range(0, len(ids), 500):
        chunk = ids[k:k + 500]
        for r in db.rows(f"SELECT id, description FROM jobs WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            out[r["id"]] = r["description"] or ""
    return out


def _written(skill: str, texts: list[str]) -> list[tuple[str, bool]]:
    """How postings actually write a skill ('Kubernetes' for Docker/Kubernetes), most common first, and whether they
    write it with a capital letter, as a name is ('Kubernetes', 'PostgreSQL'), rather than as a word ('containers')."""
    rx = SKILLS[skill][0] if skill in SKILLS else None
    if not rx:
        return []
    seen: Counter = Counter()
    casing: dict[str, Counter] = {}
    for t in texts:
        for m in rx.finditer(t):
            w = re.sub(r"\s+", " ", m.group(0).strip())
            if len(w) < 1 or len(w) > 30:
                continue
            seen[w.lower()] += 1
            casing.setdefault(w.lower(), Counter())[w] += 1
    out = []
    for low, _ in seen.most_common(4):
        best = casing[low].most_common(1)[0][0]
        named = not best.islower()
        if best.islower() and len(best) > 3:  # 'kubernetes' in running text: show it the way it's usually written
            best = best[0].upper() + best[1:]
        out.append((best, named))
    return out[:3]


# words the skill patterns match that aren't skills on their own: never one-click-added to a resume
_GENERIC = {"stakeholder", "stakeholders", "portfolio", "portfolios", "container", "containers", "equity", "equities",
            "spreadsheet", "spreadsheets", "lambda", "analog", "valuation", "derivatives", "fixed income", "regression",
            "probability", "transformer", "transformers", "sketch", "claude"}


def _names(skill: str, written: list[tuple[str, bool]]) -> list[str]:
    """The ways postings write a skill that are safe to write on a resume as its name: the skill's own name or one of
    its parts ('Docker'), or a tool's name written like one ('Kubernetes', 'PostgreSQL', 'Tableau')."""
    own = {skill.lower(), *(p.strip().lower() for p in skill.split("/"))}
    return [w for w, named in written if w.lower() in own or (named and w.lower() not in _GENERIC)]


def sample(pid: int, category: str = "", n: int = SAMPLE) -> tuple[list[dict], dict]:
    """The postings that stand for this person's market: best matches first, ones they can apply to, internships
    only, each with a real description. Returns (postings, info)."""
    prof = store.get_profile(pid)
    if not prof:
        return [], {}
    targets = _targets(prof, category)
    pool = [j for j in store.scored_jobs(pid) if j["eligibility"] != "no" and "not_intern" not in j["flags"]
            and not senior_title(j["title"]) and (not targets or j["category"] in targets)]
    pool.sort(key=lambda j: -j["score"])
    head = pool[:n * 3]
    desc = _descriptions([j["id"] for j in head])
    described = [j for j in head if len(desc.get(j["id"], "")) >= MIN_DESC]
    picked = described[:n]
    if len(picked) < min(10, n):  # few descriptions read yet: titles still say something
        picked = head[:n]
    picked = [{**j, "_desc": desc.get(j["id"], "")} for j in picked]  # (the list is shared: add to copies)
    full = sum(1 for j in picked if len(j["_desc"]) >= MIN_DESC)
    return picked, {"targets": targets, "pool": len(pool), "described": full,
                    "category": category if category in CATEGORIES else ""}


def summary(pid: int, category: str = "", n: int = SAMPLE) -> dict:
    prof = store.get_profile(pid)
    if not prof:
        return {"error": "no such person"}
    jobs, info = sample(pid, category, n)
    have = set(prof["profile"].get("skills") or [])
    counts: Counter = Counter()
    for j in jobs:
        counts.update(set(j["skills"]))
    texts = [f"{j['title']}\n{j['_desc']}" for j in jobs]
    size = len(jobs) or 1
    skills = []
    for s, c in counts.most_common(30):
        if c >= 2 or len(jobs) < 8:
            w = _written(s, texts)
            skills.append({"skill": s, "count": c, "share": round(c / size, 2), "have": s in have,
                           "forms": [x for x, _ in w], "names": _names(s, w),
                           "categories": SKILLS[s][1] if s in SKILLS else []})
    gaps = [s for s in skills if not s["have"] and s["share"] >= MIN_SHARE][:8]
    scores = [j["score"] for j in jobs]
    label = " + ".join(info.get("targets") or []) or "all roles"
    tiers = Counter(j["tier"] or "other" for j in jobs)
    return {
        "label": label,
        "category": info.get("category", ""),
        "targets": info.get("targets", []),
        "size": len(jobs),
        "described": info.get("described", 0),
        "pool": info.get("pool", 0),
        "scores": {"best": max(scores), "median": round(statistics.median(scores)), "lowest": min(scores)} if scores else {},
        "skills": skills,
        "gaps": gaps,
        "have_share": round(sum(s["share"] for s in skills if s["have"]) / (sum(s["share"] for s in skills) or 1), 2),
        "titles": [{"title": t, "count": c} for t, c in Counter(_title_family(j["title"]) for j in jobs).most_common(6)],
        "companies": [{"company": c, "count": k} for c, k in Counter(j["company"] for j in jobs).most_common(10)],
        "tiers": [{"tier": t, "label": companies.TIERS.get(t, t), "count": c} for t, c in tiers.most_common()],
        "regions": dict(Counter(r for j in jobs for r in j["regions"]).most_common(5)),
        "jobs": [{"id": j["id"], "company": j["company"], "title": j["title"], "score": j["score"]} for j in jobs[:n]],
        "pattern": pattern(pid),
    }


_FAMILY = [("Quant trader", r"\btrad(?:er|ing)\b"), ("Quant researcher", r"quant\w* research|\bresearch(?:er)?\b.*quant"),
           ("Quant developer", r"quant\w* (?:dev|engineer|software)"), ("Machine learning", r"machine learning|\bml\b|\bai\b"),
           ("Data science", r"data scien|analytics|\bdata analyst"), ("Data engineering", r"data engineer"),
           ("Frontend / full stack", r"front[- ]?end|full[- ]?stack|web"), ("Backend / infrastructure",
                                                                         r"back[- ]?end|infra|platform|devops|site reliability|\bsre\b|cloud"),
           ("Mobile", r"\bios\b|android|mobile"), ("Hardware / embedded", r"hardware|embedded|firmware|fpga|asic|electrical"),
           ("Security", r"security"), ("Product / design", r"product|design|\bux\b"), ("Software engineering",
                                                                                     r"software|developer|engineer|swe|sde")]


def _title_family(title: str) -> str:
    t = title.lower()
    return next((name for name, rx in _FAMILY if re.search(rx, t)), "Other")


def pattern(pid: int) -> dict:
    """What someone actually goes for (applications and stars), next to what they said they want."""
    prof = store.get_profile(pid)
    apps = db.rows("SELECT job_id, status, company, title FROM applications WHERE profile_id=?", (pid,))
    stars = [r["key"] for r in db.rows("SELECT key FROM favourites WHERE profile_id=? AND kind='job'", (pid,))]
    ids = list({a["job_id"] for a in apps} | set(stars))
    rows = {}
    for k in range(0, len(ids), 500):
        chunk = ids[k:k + 500]
        for r in db.rows(f"SELECT id, company, title, category, tier, skills FROM jobs WHERE id IN "
                         f"({','.join('?' * len(chunk))})", chunk):
            rows[r["id"]] = r
    picks = []
    for i in ids:
        r = rows.get(i)
        a = next((x for x in apps if x["job_id"] == i), None)
        if r:
            picks.append({"category": r["category"] or "Other", "tier": r["tier"] or "other",
                          "skills": db.loads(r["skills"], []), "applied": bool(a and a["status"] != "saved")})
        elif a and a.get("title"):  # tracked by hand: no posting row
            picks.append({"category": classify_category(a["title"]), "tier": companies.classify(a["company"] or "")[0],
                          "skills": detect_skills(a["title"]), "applied": a["status"] != "saved"})
    out = {"count": len(picks), "applied": sum(1 for p in picks if p["applied"]), "insights": [], "categories": [],
           "tiers": []}
    if len(picks) < 5:
        out["note"] = (f"Once you've applied to, saved or starred 5 postings, this shows what you actually go for. "
                       f"So far: {len(picks)}.")
        return out
    cats = Counter(p["category"] for p in picks)
    tiers = Counter(p["tier"] for p in picks)
    out["categories"] = [{"category": c, "share": round(k / len(picks), 2)} for c, k in cats.most_common(4)]
    out["tiers"] = [{"tier": t, "label": companies.TIERS.get(t, t), "share": round(k / len(picks), 2)}
                    for t, k in tiers.most_common(4)]
    targets = prof["prefs"].get("categories") or prof["profile"].get("target_categories") or []
    top_cat, top_n = cats.most_common(1)[0]
    if targets and top_cat not in targets and top_n / len(picks) >= 0.35:
        out["insights"].append({"kind": "category", "category": top_cat,
                                "text": f"You target {', '.join(targets)}, but {top_n} of your {len(picks)} picks are "
                                        f"{top_cat} roles. See what that market asks for."})
    top_tier, tier_n = tiers.most_common(1)[0]
    if tier_n / len(picks) >= 0.5 and top_tier != "other":
        out["insights"].append({"kind": "tier", "tier": top_tier,
                                "text": f"{round(100 * tier_n / len(picks))}% of what you go for is "
                                        f"{companies.TIERS.get(top_tier, top_tier)}. Its track (Jobs → ＋ Tracks) lists "
                                        f"firms like them you may not know."})
    have = set(prof["profile"].get("skills") or [])
    asked = Counter(s for p in picks for s in set(p["skills"]))
    missing = [s for s, k in asked.most_common(12) if s not in have and k >= max(2, len(picks) // 5)][:4]
    if missing:
        out["insights"].append({"kind": "skills", "skills": missing,
                                "text": f"The roles you pick keep asking for {', '.join(missing)}, and your resume "
                                        f"doesn't show them yet."})
    return out


# --- A: the resume against the market ---------------------------------------------------------

def _skills_text(r: dict) -> str:
    return "\n".join(f"{s.get('label', '')}: {s.get('items', '')}" for s in r.get("skills") or [])


# "Go", "C", "R" or "Finance" written as an item in a skills line: the free-text patterns need more context than that
_BY_NAME = {n.lower(): s for s in SKILLS for n in {s, *s.split("/")}}


def _listed(r: dict) -> set[str]:
    out = set(detect_skills(_skills_text(r)))
    for line in r.get("skills") or []:
        for tok in re.split(r"[,;|•·]|\band\b", line.get("items") or ""):
            s = _BY_NAME.get(tok.strip().lower())
            if s:
                out.add(s)
    return out


def _entries(r: dict) -> list[tuple[str, str]]:
    """(where, text) for each experience, project, education entry and extra."""
    out = []
    for e in r.get("experience") or []:
        where = " at ".join(x for x in (e.get("title", ""), e.get("company", "")) if x)
        out.append((where, " ".join([e.get("title", ""), *e.get("bullets", [])])))
    for p in r.get("projects") or []:
        out.append((f"project {p.get('name', '')}".strip(), " ".join([p.get("name", ""), p.get("tech", ""),
                                                                       *p.get("bullets", [])])))
    for e in r.get("education") or []:
        out.append((e.get("school", "education"), " ".join([e.get("degree", ""), *e.get("details", [])])))
    for x in r.get("extras") or []:
        out.append((x.get("heading", "extras"), " ".join(x.get("items", []))))
    return out


def fit(pid: int, resume: dict | None = None, category: str = "", n: int = SAMPLE, track: str = "") -> dict:
    """Each skill the market asks for, checked against the resume, with the one thing to do about it. With a track,
    the market is that track's (the same numbers its Market page shows): one engine for Resume Studio and tracks."""
    prof = store.get_profile(pid)
    if not prof:
        return {"error": "no such person"}
    r = rs.normalize(resume) if resume else routes_docs.master(pid)
    if track:
        tm = track_skills(pid, track)
        if tm is None:
            return {"error": "no such track"}
        m = {"label": tm["name"], "category": "", "targets": [], "size": tm["described"], "described": tm["described"],
             "companies": [], "scores": {}, "track": tm["id"], "skills": tm["skills"]}
    else:
        m = summary(pid, category, n)
    if not r:
        return {"market": m, "skills": [], "coverage": None}
    return {"market": {k: m[k] for k in ("label", "category", "targets", "size", "described", "companies", "scores")
                       if k in m} | ({"track": m["track"]} if m.get("track") else {}),
            **fit_rows(prof, r, m["skills"])}


def fit_rows(prof: dict, r: dict, skills: list[dict]) -> dict:
    """The resume against a list of asked-for skills ({skill, share, count, names?}), most asked first."""
    listed = _listed(r)
    entries = [(w, set(detect_skills(t))) for w, t in _entries(r)]
    shown = set().union(*[s for _, s in entries]) if entries else set()
    known = set(prof["profile"].get("skills") or [])
    rows = []
    for s in skills:
        if s["share"] < 0.1 and len(rows) >= 8:
            continue
        name = s["skill"]
        if name in listed:
            status = "listed"
        elif name in shown:
            status = "surface"
        elif name in known:
            status = "known"
        else:
            status = "missing"
        where = next((w for w, sk in entries if name in sk), "") if status == "surface" else ""
        # the one-click "Add to Skills" writes this: a name for the skill, never a word like 'Stakeholders'
        rows.append({**s, "status": status, "label": STATUS_LABEL[status], "where": where,
                     "add_as": (s.get("names") or [_short(name)])[0]})
    rows = rows[:16]
    total = sum(x["share"] for x in rows) or 1
    covered = sum(x["share"] for x in rows if x["status"] in ("listed", "surface"))
    listed_share = sum(x["share"] for x in rows if x["status"] == "listed")
    return {
        "skills": rows,
        "coverage": round(100 * covered / total) if rows else None,  # nothing asked yet: no fit, not 0%
        "listed_coverage": round(100 * listed_share / total) if rows else None,
        "counts": dict(Counter(x["status"] for x in rows)),
    }


def _short(skill: str) -> str:
    """A name to write on a resume for a skill family ('Docker/Kubernetes' -> 'Docker')."""
    if skill in ("CI/CD", "HTML/CSS"):
        return skill
    return skill.split("/")[0]


def add_skill(resume: dict, text: str, skill: str = "") -> tuple[dict, str]:
    """Add one skill to the Skills section: languages to the languages line, everything else to the tools/frameworks
    line. Returns (resume, label of the line it went on). Already there: unchanged."""
    r = rs.normalize(resume)
    text = re.sub(r"\s+", " ", (text or "").strip(" ,;"))
    if not text:
        return r, ""
    lines = r["skills"]
    for ln in lines:
        items = [x.strip().lower() for x in re.split(r"[,;|]", ln.get("items", ""))]
        if text.lower() in items:
            return r, ln.get("label", "")
    is_lang = skill in LANGUAGES or text in LANGUAGES
    want = re.compile(r"language", re.I) if is_lang else \
        re.compile(r"framework|librar|tool|technolog|platform|software|developer|cloud|devops|data|other|skill", re.I)
    target = next((ln for ln in lines if want.search(ln.get("label", ""))), None)
    if target is None and not is_lang:
        target = next((ln for ln in lines if not re.search(r"language|interest|hobb|certif", ln.get("label", ""), re.I)),
                      None)
    if target is None:
        target = {"label": "Languages" if is_lang else "Tools", "items": ""}
        lines.append(target)
    cur = (target.get("items") or "").strip().rstrip(",;").strip()
    target["items"] = f"{cur}, {text}" if cur else text
    return r, target["label"]


# --- a project that builds a missing skill -----------------------------------------------------

_PLANS = {
    "C++": ("Low-latency order book in C++",
            ["Build a limit order book (add, cancel, match) with price-time priority",
             "Replay a public market-data sample through it and measure throughput and latency",
             "Profile it, then cut allocations (object pools, flat arrays) and compare",
             "Write it up: design, benchmarks, what you'd change"]),
    "Python": ("A data tool in Python that someone uses",
               ["Pick a small daily annoyance (a club's sign-ups, a price you track) and script it",
                "Add tests (pytest) and a command-line interface", "Package it and put it on GitHub with a README",
                "Get one other person to use it and fix what they hit"]),
    "Statistics": ("A statistics notebook on real data",
                   ["Pick a public dataset you care about (sports, transit, markets)",
                    "Ask one question and test it properly: hypothesis, test, confidence interval",
                    "Fit a regression, check its assumptions, and say what it can't tell you",
                    "Publish it as a short write-up with the code"]),
    "Pandas/NumPy": ("Backtest a simple strategy with pandas",
                     ["Download daily prices for a few ETFs",
                      "Implement a moving-average or momentum rule, vectorized with pandas/NumPy",
                      "Account for transaction costs and compare to buy-and-hold",
                      "Plot results and write up why it would or wouldn't work live"]),
    "Finance": ("Price options three ways",
                ["Implement Black-Scholes for European options", "Price the same options with a binomial tree and Monte Carlo",
                 "Compare the three and show where they disagree", "Write up the Greeks and what they mean for a trader"]),
    "OCaml/Haskell": ("A small interpreter in OCaml",
                      ["Work through the first chapters of Real World OCaml",
                       "Write a parser and evaluator for a tiny calculator language",
                       "Add variables and functions, with tests", "Write up what functional style changed in how you code"]),
    "SQL": ("A database-backed app with real queries",
            ["Model a small domain (courses, clubs, games) in PostgreSQL",
             "Write the queries an app needs: joins, aggregates, a window function",
             "Add indexes and show a query plan before and after", "Put a tiny API in front of it"]),
    "Distributed Systems": ("A replicated key-value store",
                            ["Work through MIT 6.5840's labs (MapReduce, Raft)",
                             "Build a key-value store on your Raft implementation",
                             "Test it under failures (kill nodes, drop messages)", "Write up the bugs you found"]),
    "PyTorch": ("Train and ship a small model in PyTorch",
                ["Pick a problem with a public dataset", "Train a baseline, then improve it and track experiments",
                 "Serve it behind a small API", "Write up the results honestly, including what didn't work"]),
    "LLMs/GenAI": ("An LLM app that answers from your own documents",
                   ["Collect a document set (course notes, a club's handbook)",
                    "Build retrieval (embeddings + a vector index) and an answer step with citations",
                    "Make a small evaluation set and measure answer quality",
                    "Deploy it and write up what improved the scores"]),
    "Docker/Kubernetes": ("Deploy a multi-service app with Docker and Kubernetes",
                          ["Containerize an app you already have (API + database)",
                           "Run it with docker compose, then on a local Kubernetes cluster (kind or minikube)",
                           "Add health checks, config and a rolling update", "Write up the setup in the README"]),
    "AWS": ("Put a project on AWS properly",
            ["Deploy an app you have with Lambda or ECS, plus S3 and a managed database",
             "Define it as code (CDK or Terraform)", "Add logging and an alarm",
             "Write up the architecture and its monthly cost"]),
    "React": ("A React app with real users",
              ["Build something small people around you need (a club tool, a schedule planner)",
               "Use React with TypeScript, and fetch from an API", "Add tests and deploy it",
               "Get feedback from a few users and ship one change because of it"]),
    "TypeScript": ("Convert a JavaScript project to TypeScript",
                   ["Take a JS project of yours and turn on strict TypeScript", "Type the API boundaries and fix what it finds",
                    "Add tests", "Write up the bugs the types caught"]),
    "Go": ("A concurrent service in Go",
           ["Build a small HTTP service (a URL shortener, a rate limiter)", "Use goroutines and channels for background work",
            "Load-test it and fix the bottleneck", "Write up the benchmarks"]),
    "Rust": ("A command-line tool in Rust",
             ["Rewrite a small tool you use (grep-like search, a file deduper)", "Make it fast and safe: no unwrap in the main path",
              "Benchmark it against the original", "Publish it on crates.io or GitHub"]),
    "Linux": ("Your own Linux toolbox",
              ["Set up a home server or VM and do everything over SSH", "Write shell scripts for backups and monitoring",
               "Learn the debugging tools (strace, top, journalctl) on a real problem", "Document the setup"]),
}


def project_plan(pid: int, skill: str, category: str = "", use_ai: bool = True) -> dict:
    """A short project that would let someone honestly list a skill. Saved to Documents."""
    from . import ai
    prof = store.get_profile(pid)
    if not prof:
        return {"error": "no such person"}
    m = summary(pid, category, SAMPLE)
    row = next((s for s in m["skills"] if s["skill"] == skill), {"forms": [], "count": 0})
    plan, by = None, "rules"
    if use_ai and ai.available():
        try:
            plan = ai.project_plan(skill, row.get("forms") or [], m["label"], [c["company"] for c in m["companies"][:6]],
                                   prof.get("resume_text") or "")
            by = ai.provider()
        except Exception as e:  # the built-in plans below still work
            import logging
            logging.getLogger(__name__).warning("AI project plan failed: %s", e)
    if not plan:
        title, steps = _PLANS.get(skill) or ("", [])
        if not title:
            word = (row.get("forms") or [_short(skill)])[0]
            title, steps = (f"A small project that uses {word}",
                            [f"Pick a problem you or a friend actually have that {word} fits",
                             f"Build the smallest version that works, using {word} for the core of it",
                             "Add tests, a README and a short demo (screenshot or video)",
                             "Write up what you built, the hard part, and what you'd do next"])
        plan = {"title": title, "why": f"{row.get('count', 0)} of your top {m['size']} {m['label']} postings ask for "
                                       f"{skill}." if row.get("count") else f"Builds {skill} for {m['label']} roles.",
                "steps": [{"when": f"Week {i + 1}", "do": s} for i, s in enumerate(steps)],
                "shows": [skill], "resume_line": f"Built {title[0].lower() + title[1:]} ([what it does, for whom]); "
                                                 f"[the result you measured]"}
    content = f"{plan['title']}\n\n{plan['why']}\n\n" + "\n".join(f"{s['when']}: {s['do']}" for s in plan["steps"]) + \
        f"\n\nWhen it's done, a resume line could read:\n{plan['resume_line']}"
    doc_id = routes_docs._save_doc(pid, "plan", content, f"Project plan: {skill}", job_id=f"plan:{skill}", by=by)
    return {"ok": True, "skill": skill, "plan": plan, "by": by, "doc_id": doc_id}


# --- market v2: a track's market, from what each posting's full text says ------------------------
#
# Which postings count (`population`): the track's open postings this person can apply to, internships, opened in the
# last POP_DAYS days (cross-source copies are already one posting). Skill shares are over the ones with a full
# description only, and that number is always shown. Each posting's text is read once (`facts_for`, kept in
# job_facts): the skills it asks for, which of them sit under its own "Requirements" or "Nice to have" headings, and
# the pay it states. Nothing is converted between currencies and nothing is guessed: no stated pay, no pay.

FACTS_VERSION = "1"
POP_DAYS = 120
TREND_MIN = 8        # postings needed in both the last 30 days and the 90 before for a skill's trend
TREND_STEP = 0.1     # a share that moved this much (10 points) is up or down
READ_MORE = 30       # postings read in the background when under half a market has its full text...
READ_PER_DAY = 120   # ...and at most this many a day, across every market
_REQ_HEAD = re.compile(r"^(?:basic |minimum |required |key |core |must[- ]have )?(?:requirements?|qualifications?|"
                       r"skills(?: (?:and|&) (?:experience|qualifications))?|what (?:you'll|you will|you) (?:need|bring)|"
                       r"who you are|must[- ]haves?|you (?:have|bring|should have|might be a fit)|about you|"
                       r"what we(?:'re| are) looking for|we(?:'re| are) looking for|your (?:background|profile|skills)|"
                       r"required skills|profil(?:e)? recherch[ée]|exigences)\b", re.I)
_NICE_HEAD = re.compile(r"^(?:preferred(?: qualifications?| skills| experience)?|nice[- ]to[- ]haves?|bonus(?: points)?|"
                        r"(?:it's |it is )?a plus|pluses|assets?|additional (?:qualifications|skills)|desired(?: "
                        r"qualifications| skills)?|good to have|extra credit|atouts?|ideally)\b", re.I)
_OTHER_HEAD = re.compile(r"^(?:responsibilities|what you(?:'ll| will) (?:do|work on)|about (?:us|the (?:role|team|"
                         r"company|job|position))|the (?:role|team|opportunity)|benefits|perks|compensation|pay|salary|"
                         r"why (?:join|work)|our (?:team|mission|values)|location|how to apply|equal opportunity|"
                         r"who we are|what we offer|day[- ]to[- ]day|your (?:role|impact|responsibilities)|mission)\b",
                         re.I)
_NICE_INLINE = re.compile(r"\b(?:a plus|is a bonus|are a bonus|nice to have|preferred|bonus points|an asset|"
                          r"is an advantage|ideally)\b", re.I)


def _heading(line: str) -> tuple[str, str] | None:
    """('req' | 'nice' | 'other', the text after it on the same line) when a line is a section heading."""
    head, _, rest = line.partition(":")
    head = head.strip(" \t•*#-–—").strip()
    if not head or len(head) > 60 or (not rest and len(line) > 60):
        return None
    for kind, rx in (("nice", _NICE_HEAD), ("req", _REQ_HEAD), ("other", _OTHER_HEAD)):
        if rx.match(head):
            return kind, rest.strip()
    return None


def sections(text: str) -> dict[str, str]:
    """A description cut by its own headings: {'intro', 'req', 'nice', 'other'} -> text. A line inside Requirements
    that says "a plus" counts as nice to have."""
    out = {"intro": [], "req": [], "nice": [], "other": []}
    kind = "intro"
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        h = _heading(line)
        if h:
            kind, rest = h
            if rest:
                out[kind].append(rest)
            continue
        out["nice" if kind in ("req", "intro", "other") and _NICE_INLINE.search(line) else kind].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


_CUR = r"(?:CA\$|C\$|US\$|CAD\s?\$?|USD\s?\$?|\$)"
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s?([kK])?(?![\d,])"
_PAY_RX = re.compile(r"(?<![\w$])(" + _CUR + r")\s?" + _NUM + r"(?:\s?(?:-|–|—|to|and)\s?(?:" + _CUR + r")?\s?" +
                     _NUM + r")?")
_BIG = re.compile(r"^\s?(?:m|mm|b|bn|million|billion|trillion)\b|^\+", re.I)
_LUMP = re.compile(r"^\s?(?:/|per|for the|a|each)\s?(?:summer|term|season|semester|internship|program|session)\b|"
                   r"^[^.\n]{0,15}\b(?:total|lump sum|one[- ]time)\b", re.I)  # a stipend for the whole term isn't a rate
_UNITS = [("hour", re.compile(r"(?:\b(?:per|an?)\s?|/\s?)(?:hour|hr|h)\b|\bhourly\b|\bph\b|\bp/h\b", re.I)),
          ("year", re.compile(r"(?:\b(?:per|a)\s?|/\s?)(?:year|yr|annum)\b|\bannual(?:ly)?\b|\bsalary\b", re.I)),
          ("month", re.compile(r"(?:\b(?:per|a)\s?|/\s?)(?:month|mo)\b|\bmonthly\b", re.I)),
          ("week", re.compile(r"(?:\b(?:per|a)\s?|/\s?)(?:week|wk)\b|\bweekly\b", re.I))]
_PAY_WORDS = re.compile(r"\b(?:pay|salary|compensation|wage|rate|range|hourly|stipend|earn|remuneration|base)\b", re.I)
_NOT_PAY = re.compile(r"\b(?:raised|funding|funded|series [a-e]|valuation|revenue|backed|assets under|aum|grant|prize|"
                      r"budget|investors?|seed|market cap|in sales|customers|transactions|processed|relocation|"
                      r"reimburs\w*|tuition|donat\w*|fundrais\w*|sponsorship|scholarship|award)\b", re.I)
_RANGE = {"hour": (12, 150), "year": (25000, 300000), "month": (1500, 20000), "week": (400, 6000)}
_HOURS = {"hour": 1, "year": 1 / 2080, "month": 12 / 2080, "week": 1 / 40}


def _amount(num: str, k: str | None) -> float:
    v = float(num.replace(",", ""))
    return v * 1000 if k else v


def pay_of(text: str, regions: list[str] | None = None) -> dict | None:
    """The pay a posting states, as written and as an hourly equivalent (a year is 2,080 hours), or None. A bare "$"
    is the posting's own country's dollar; "$10M raised" and other money that isn't pay don't count."""
    regions = regions or []
    best = None
    for m in _PAY_RX.finditer(text or ""):
        after = text[m.end():m.end() + 40]
        if _BIG.match(after) or _LUMP.match(after):
            continue
        before = text[max(0, m.start() - 80):m.start()]
        near = before[-60:] + " " + after[:30]
        if _NOT_PAY.search(near):
            continue
        a, ak, b, bk = m.group(2), m.group(3), m.group(4), m.group(5)
        lo = _amount(a, ak or (bk if b else None))
        hi = _amount(b, bk) if b else lo
        if hi < lo:
            lo, hi = hi, lo
        unit = next((u for u, rx in _UNITS if rx.search(after[:30])), None) or \
            next((u for u, rx in _UNITS if rx.search(before[-50:])), None)
        explicit = unit is not None
        if unit is None:  # no unit written: only a clear hourly or yearly figure, next to a word about pay
            if not _PAY_WORDS.search(before[-60:] + " " + after[:20]):
                continue
            unit = "hour" if 12 <= hi <= 150 else "year" if 25000 <= lo <= 300000 else None
            if unit is None:
                continue
        low_ok, high_ok = _RANGE[unit]
        if not (low_ok <= lo <= high_ok and low_ok <= hi <= high_ok * 1.5):
            continue
        cur = m.group(1).upper().replace(" ", "")
        tail = after[:20].upper()
        currency = ("CAD" if cur.startswith(("CA", "C$")) else "USD" if cur.startswith("US") else
                    "CAD" if "CAD" in tail else "USD" if "USD" in tail else
                    "CAD" if "Canada" in regions and "US" not in regions else
                    "USD" if "US" in regions and "Canada" not in regions else "$")
        start = max(0, m.start() - 40)
        quote = re.sub(r"\s+", " ", text[start:m.end() + 25]).strip()
        got = {"low": round(lo, 2), "high": round(hi, 2), "unit": unit, "currency": currency,
               "hourly_low": round(lo * _HOURS[unit], 2), "hourly_high": round(hi * _HOURS[unit], 2),
               "quote": ("…" if start else "") + quote[:140] + "…"}
        if explicit:
            return got
        best = best or got
    return best


def facts_of(title: str, desc: str, regions: list[str] | None = None, skills: list[str] | None = None) -> dict:
    """What one posting's full text says. Not read yet (no description): {'described': False}. `skills`: the ones
    already detected in its title and text when it was stored (detect_skills is most of the cost)."""
    if len(desc or "") < MIN_DESC:
        return {"described": False}
    sec = sections(desc)
    asked = sorted(set(skills if skills is not None else detect_skills(f"{title}\n{desc}")))
    # which of them its own headings put under Requirements or Nice to have (each skill's own pattern, once)
    req = {x for x in asked if x in SKILLS and SKILLS[x][0].search(sec["req"])}
    nice = {x for x in asked if x in SKILLS and SKILLS[x][0].search(sec["nice"])} - req
    return {"described": True, "skills": asked, "req": sorted(req), "nice": sorted(nice),
            "headed": bool(sec["req"] or sec["nice"]), "pay": pay_of(desc, regions)}


def facts_for(jobs: list[dict]) -> dict[str, dict]:
    """job id -> facts for these postings: read from job_facts, worked out for any that are new or whose description
    changed since (each kept with the length of the text it was read from)."""
    out: dict[str, dict] = {}
    ids = [j["id"] for j in jobs]
    lengths: dict[str, int] = {}
    kept: dict[str, tuple[str, str]] = {}
    for k in range(0, len(ids), 500):
        chunk = ids[k:k + 500]
        marks = ",".join("?" * len(chunk))
        for r in db.rows(f"SELECT id, LENGTH(COALESCE(description, '')) AS n FROM jobs WHERE id IN ({marks})", chunk):
            lengths[r["id"]] = int(r["n"] or 0)
        for r in db.rows(f"SELECT job_id, version, facts FROM job_facts WHERE job_id IN ({marks})", chunk):
            kept[r["job_id"]] = (r["version"], r["facts"])
    todo = []
    for j in jobs:
        want = f"{FACTS_VERSION}:{lengths.get(j['id'], 0)}"
        k = kept.get(j["id"])
        if k and k[0] == want:
            out[j["id"]] = db.loads(k[1], {}) or {"described": False}
        elif lengths.get(j["id"], 0) < MIN_DESC:
            out[j["id"]] = {"described": False}  # nothing to read: no need to keep a row
        else:
            todo.append(j)
    if todo:
        desc = _descriptions([j["id"] for j in todo])
        rows = []
        for j in todo:
            f = facts_of(j["title"], desc.get(j["id"], ""), j.get("regions"), j.get("skills"))
            out[j["id"]] = f
            rows.append((j["id"], f"{FACTS_VERSION}:{lengths.get(j['id'], 0)}", db.dumps(f), db.now()))
        with db.tx() as c:
            for r in rows:
                c.execute("INSERT OR REPLACE INTO job_facts (job_id, version, facts, computed_at) VALUES (?,?,?,?)", r)
    return out


def _opened(j: dict) -> int:
    return j.get("opened_at") or j.get("posted_at") or j["first_seen"]


def population(pid: int, jobs: list[dict]) -> list[dict]:
    """The postings a market is about: ones this person can apply to, internships, opened in the last POP_DAYS days."""
    since = db.now() - POP_DAYS * 86400
    return [j for j in jobs if j["eligibility"] != "no" and "not_intern" not in j["flags"]
            and not senior_title(j["title"]) and _opened(j) >= since]


def city_of(loc: str) -> str:
    return (loc or "").split(",")[0].strip()


def remote(j: dict) -> bool:
    return "Remote" in (j.get("regions") or []) or any(re.search(r"\bremote\b", x, re.I) for x in j["locations"])


def _month(ts: int) -> str:
    return time.strftime("%Y-%m", time.localtime(ts))


def facet_filter(facet: str, jobs: list[dict], pid: int = 0, track: str = ""):
    """The test for one market number's postings ("skill:Python" opens the described postings that ask for it)."""
    kind, _, val = facet.partition(":")
    if kind == "firm":  # one firm of a niche track, under every name it posts as ("Citadel Securities", "...LLC")
        from . import tracks
        m = tracks.firms_of(pid, track)
        return (lambda j: ((m.firm(j) or {}).get("key") == val)) if m else (lambda j: False)
    if kind in ("skill", "req", "nice", "pay"):
        facts = facts_for(jobs)
        key = {"skill": "skills"}.get(kind, kind)
        if kind == "pay":
            return lambda j: bool(facts.get(j["id"], {}).get("pay"))
        return lambda j: val in (facts.get(j["id"], {}).get(key) or [])
    if kind == "city":
        return lambda j: any(city_of(x) == val for x in j["locations"])
    if kind == "term":
        return lambda j: val in j["terms"]
    if kind == "remote":
        return remote
    if kind == "company":
        return lambda j: j["company_key"] == val
    if kind == "month":
        return lambda j: _month(_opened(j)) == val
    if kind == "elig":
        return lambda j: j["eligibility"] == val
    return lambda j: False


def track_skills(pid: int, tid: str) -> dict | None:
    """The skills a track's postings ask for, kept until those postings or the student change (Resume Studio's market fit asks
    this every time it opens, and Market asks it too). Shared: read it, don't change it."""
    key, now_ = (pid, tid), _market_stamp(pid)
    hit = _skills_cache.get(key)
    if hit and hit[0] == now_:
        return hit[1]
    out = _track_skills(pid, tid)
    if len(_skills_cache) > 64:
        _skills_cache.clear()
    _skills_cache[key] = (now_, out)
    return out


def _track_skills(pid: int, tid: str) -> dict | None:
    """The skills a track's postings ask for, over the described ones in its population (Resume Studio's market fit
    for a track reads exactly this)."""
    from . import tracks
    t = tracks.get(pid, tid)
    if not t:
        return None
    pop = population(pid, tracks.members(pid, t["id"]))
    facts = facts_for(pop)
    described = [j for j in pop if facts.get(j["id"], {}).get("described")]
    n = len(described) or 1
    asked, req, nice = Counter(), Counter(), Counter()
    for j in described:
        f = facts[j["id"]]
        asked.update(f["skills"])
        req.update(f["req"])
        nice.update(f["nice"])
    now = db.now()
    recent = [j for j in described if _opened(j) >= now - 30 * 86400]
    prior = [j for j in described if _opened(j) < now - 30 * 86400]
    skills = []
    for s, c in asked.most_common(25):
        if c < 2 and len(described) >= 8:
            continue
        trend = None
        if len(recent) >= TREND_MIN and len(prior) >= TREND_MIN:
            a = sum(1 for j in recent if s in facts[j["id"]]["skills"]) / len(recent)
            b = sum(1 for j in prior if s in facts[j["id"]]["skills"]) / len(prior)
            trend = "up" if a - b >= TREND_STEP else "down" if b - a >= TREND_STEP else "flat"
        skills.append({"skill": s, "count": c, "share": round(c / n, 2), "req": req[s], "nice": nice[s],
                       "trend": trend, "names": [], "categories": SKILLS[s][1] if s in SKILLS else []})
    return {"id": t["id"], "name": t["name"], "track": tracks.public(t), "population": pop, "facts": facts,
            "described": len(described), "size": len(pop), "skills": skills,
            "trend_ready": len(recent) >= TREND_MIN and len(prior) >= TREND_MIN,
            "headed": sum(1 for j in described if facts[j["id"]].get("headed"))}


_history_cache: dict = {}


def _all_rows() -> list[dict]:
    """Every posting seen in the last year, open or closed (what 'posted per month' counts), read at most every 6 h."""
    hit = _history_cache.get("rows")
    if hit and hit[0] > time.time() - 6 * 3600:
        return hit[1]
    since = db.now() - 365 * 86400
    rows = db.rows("SELECT id, company, company_key, title, category, tier, flags, locations, first_seen, posted_at,"
                   " opened_at FROM jobs WHERE first_seen >= ? OR active=1", (since,))
    for r in rows:
        r["flags"] = db.loads(r["flags"], []) or []
        r["locations"] = db.loads(r["locations"], []) or []
    _history_cache["rows"] = (time.time(), rows)
    return rows


def history(pid: int, tid: str, months: int = 12) -> list[dict]:
    """Postings per month for the last 12 months, open and closed, by when each went up."""
    from . import tracks
    t = tracks.get(pid, tid)
    if not t:
        return []
    lens = tracks._Lenses(pid, [t])
    now = time.localtime()
    keys = []
    y, mo = now.tm_year, now.tm_mon
    for _ in range(months):
        keys.append(f"{y:04d}-{mo:02d}")
        y, mo = (y - 1, 12) if mo == 1 else (y, mo - 1)
    counts = dict.fromkeys(keys, 0)
    seen: set = set()
    for r in _all_rows():
        key = (r["company_key"], r["title"].lower())
        if key in seen:
            continue
        m = _month(_opened(r))
        if m in counts and lens.tracks_of(r):
            seen.add(key)
            counts[m] += 1
    return [{"month": k, "n": counts[k]} for k in reversed(keys)]


def _quartiles(xs: list[float]) -> tuple[float, float, float]:
    if len(xs) == 1:
        return xs[0], xs[0], xs[0]
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], statistics.median(xs), q[2]


def pay_summary(pop: list[dict], facts: dict[str, dict]) -> dict:
    """Stated pay only, as hourly equivalents, in the currency most of these postings state it in."""
    stated = [(j, facts[j["id"]]["pay"]) for j in pop if (facts.get(j["id"]) or {}).get("pay")]
    described = sum(1 for j in pop if (facts.get(j["id"]) or {}).get("described"))
    if not stated:
        return {"n": 0, "described": described}
    cur = Counter(p["currency"] for _, p in stated).most_common(1)[0][0]
    mine = [(j, p) for j, p in stated if p["currency"] == cur]
    mids = sorted((p["hourly_low"] + p["hourly_high"]) / 2 for _, p in mine)
    lo, med, hi = _quartiles(mids)
    return {"n": len(mine), "stated": len(stated), "described": described, "currency": cur,
            "median": round(med, 2), "p25": round(lo, 2), "p75": round(hi, 2),
            "other_currencies": {c: k for c, k in Counter(p["currency"] for _, p in stated).items() if c != cur},
            "quotes": [{"company": j["company"], "title": j["title"], "id": j["id"], "quote": p["quote"],
                        "unit": p["unit"]} for j, p in sorted(mine, key=lambda x: -x[0]["score"])[:3]]}


_reading: set = set()


_unreadable: dict[str, float] = {}  # postings whose page wouldn't give up its text (a 403, say): not asked for again today


def read_more(pop: list[dict], facts: dict[str, dict]) -> int:
    """Under half of a market read in full: fetch the full text of up to READ_MORE more (best matches first) in the
    background, within READ_PER_DAY a day. Returns how many it started reading."""
    import threading

    from . import jobdetail
    described = sum(1 for j in pop if (facts.get(j["id"]) or {}).get("described"))
    if not pop or described * 2 >= len(pop):
        return 0
    key = f"facts_reads:{time.strftime('%Y-%m-%d')}"
    left = READ_PER_DAY - int(db.meta_get(key, 0) or 0)
    recently = time.time() - 86400
    todo = [j["id"] for j in sorted(pop, key=lambda j: -j["score"])
            if not (facts.get(j["id"]) or {}).get("described") and j["id"] not in _reading
            and _unreadable.get(j["id"], 0) < recently][:max(0, min(READ_MORE, left))]
    if not todo:
        return 0
    db.meta_set(key, str(int(db.meta_get(key, 0) or 0) + len(todo)))
    _reading.update(todo)

    def run():
        for i in todo:
            try:
                if not jobdetail.ensure_description(i):
                    _unreadable[i] = time.time()
            except Exception:  # a page that won't load stays unread; the next market look tries others, not this one again
                _unreadable[i] = time.time()
            finally:
                _reading.discard(i)

    threading.Thread(target=run, name="market-read-more", daemon=True).start()
    return len(todo)


_market_cache: dict = {}
_market_locks: dict = {}
READING_REFRESH = 5  # seconds
_skills_cache: dict = {}


def _market_stamp(pid: int) -> tuple:
    prof = store.get_profile(pid) or {}
    return (prof.get("updated_at"), store._cache["version"],
            db.table_version("jobs", "job_facts", "documents", "profiles", "niche_firms"))


def track_market(pid: int, tid: str) -> dict | None:
    """A track's Market, kept until the postings, the student or what was read from the postings change. It takes a while to work
    out, so one request does it and the others wait and share; while more postings are being read, it's worked out again every few
    seconds at most, however many times it's asked for."""
    key = (pid, tid)

    def kept():
        hit = _market_cache.get(key)
        if hit:
            if hit[1] and hit[1].get("reading"):
                # Even a failed fetch must stop showing "reading" when the worker finishes, without a DB write.
                return hit if time.time() - hit[2] < READING_REFRESH else None
            if hit[0] == _market_stamp(pid):
                return hit
        return None
    hit = kept()
    if hit:
        return hit[1]
    with _market_locks.setdefault(key, threading.Lock()):
        hit = kept()
        if hit:
            return hit[1]
        stamp = _market_stamp(pid)
        out = _track_market(pid, tid)
        if len(_market_cache) > 64:
            _market_cache.clear()
        if out is not None:
            _market_cache[key] = (stamp, out, time.time())
        return out


def _track_market(pid: int, tid: str) -> dict | None:
    """At a glance, skills, where & when, pay, and your fit (the same engine as Resume Studio)."""
    from . import niches, tracks
    tm = track_skills(pid, tid)
    if tm is None:
        return None
    pop, facts = tm["population"], tm["facts"]
    prof = store.get_profile(pid)
    r = routes_docs.master(pid)
    fit = fit_rows(prof, r, tm["skills"]) if r else {"skills": [], "coverage": None}
    gaps = [x for x in fit["skills"] if x["status"] == "missing" and x["share"] >= MIN_SHARE][:3]
    every = tracks.members(pid, tm["id"])
    hist = history(pid, tm["id"])
    busy = [h for h in sorted(hist, key=lambda h: -h["n"]) if h["n"]][:2]
    t = tracks.get(pid, tm["id"])
    openings = None
    if t.get("niche"):
        n = niches.get(pid, t["niche"])
        firms = niches._firms(n, include_hidden=False) if n else []
        if firms:
            niches._openings(firms, n["season"])
            openings = niches.windows(firms, season=n["season"])
    cities = Counter(c for j in pop for c in {city_of(x) for x in j["locations"]} if c and c.lower() != "remote")
    terms = Counter(t for j in pop for t in j["terms"])
    import os
    if not os.environ.get("COOPCOACH_DEMO"):  # the demo is a snapshot: no fetching
        read_more(pop, facts)
    return {
        "track": tm["track"],
        "size": tm["size"],
        "described": tm["described"],
        "reading": sum(1 for j in pop if j["id"] in _reading),
        "headed": tm["headed"],
        "days": POP_DAYS,
        "glance": {"open_to_you": tm["size"], "fit": fit.get("coverage"), "gaps": [g["skill"] for g in gaps],
                   "pay": pay_summary(pop, facts), "peak": [h["month"] for h in busy]},
        "skills": [{**s, "status": next((x["status"] for x in fit["skills"] if x["skill"] == s["skill"]), None)}
                   for s in tm["skills"]],
        "trend_ready": tm["trend_ready"],
        "fit": fit,
        "where": {"terms": [{"term": k, "n": v} for k, v in terms.most_common(6)],
                  "cities": [{"city": k, "n": v} for k, v in cities.most_common(8)],
                  "remote": sum(1 for j in pop if remote(j)),
                  "who": dict(Counter(j["eligibility"] for j in every))},
        "history": hist,
        "openings": openings,
    }
