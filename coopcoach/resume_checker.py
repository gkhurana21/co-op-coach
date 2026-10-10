"""Resume Studio's checker: proofreading (spelling, capitals, split lines, spacing, dashes), the lint score and its
issues, exact fixes (one or all), and the rule-based review."""

import copy
import hashlib
import json
import math
import re
import time
from collections import Counter

from .textutil import detect_skills

STRONG_VERBS = set("""accelerated achieved analyzed analysed architected authored automated benchmarked boosted built
captured championed coded collaborated compiled configured constructed containerized converted coordinated created
cut debugged decreased defined delivered deployed designed developed devised diagnosed directed doubled drove earned
eliminated enabled engineered enhanced established evaluated executed expanded extended fine-tuned forecasted founded
generated grew halved identified implemented improved increased initiated instrumented integrated introduced invented
investigated launched led leveraged maintained managed mentored migrated modeled modelled modernized monitored
optimized optimised orchestrated organized organised overhauled owned parallelized partnered piloted pioneered
presented profiled programmed prototyped published queried raised ranked rebuilt reduced refactored redesigned
reengineered released replaced researched resolved restructured revamped rewrote saved scaled scripted secured shipped
simplified slashed solved spearheaded standardized streamlined strengthened supervised taught tested trained
transformed tripled troubleshot tuned unified upgraded validated visualized won wrote""".split())
PRESENT_FORMS = {v[:-1] if v.endswith("ed") and not v.endswith("eed") else v for v in STRONG_VERBS} | {
    "build", "lead", "write", "drive", "grow", "cut", "own", "run", "teach", "make"}
IRREGULAR_PAST = {"built", "led", "wrote", "drove", "grew", "cut", "won", "taught", "ran", "made", "rebuilt", "rewrote",
                  "overhauled", "troubleshot", "sold", "brought", "began", "held", "set", "spun", "shot", "kept",
                  "found", "met", "sent", "spent", "understood", "took", "gave", "chose", "oversaw", "undertook"}
WEAK_OPENERS = re.compile(r"^(worked|helped|assisted|responsible|participated|involved|tasked|handled|did|made|used|"
                          r"utilized|utilised|was|were|got|tried|learned|learnt|familiarized|exposed|attended|shadowed|"
                          r"duties|in charge|took part|contributed to)\b", re.I)
FILLER = [(re.compile(r"\bin order to\b", re.I), "to"), (re.compile(r"\butili[sz](e|ed|es|ing)\b", re.I), "use"),
          (re.compile(r"\bsuccessfully\s+", re.I), ""), (re.compile(r"\b(various|several|a variety of|numerous)\b", re.I), ""),
          (re.compile(r"\betc\.?", re.I), ""), (re.compile(r"\bresponsible for\b", re.I), "")]
BUZZWORDS = re.compile(r"\b(hard-?working|team player|detail[- ]oriented|passionate|motivated|self-?starter|go-?getter|"
                       r"synergy|results-?driven|dynamic|think outside the box|fast learner|quick learner|guru|ninja|"
                       r"rockstar|world-?class|best[- ]in[- ]class)\b", re.I)
PRONOUNS = re.compile(r"(?<![\w-])(I|me|my|myself|we|our|us)(?![\w-])")
METRIC = re.compile(r"\d|%|\$|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|dozens?|hundreds?|thousands?|"
                    r"millions?|double[ds]?|triple[ds]?|half|halved)\b", re.I)
HOW = re.compile(r"\b(using|with|by|via|through|leveraging|in|on)\b", re.I)
SOFT_SKILLS = re.compile(r"\b(communication|teamwork|leadership|problem[- ]solving|time management|adaptability|"
                         r"critical thinking|interpersonal|microsoft word|ms word|powerpoint)\b", re.I)
PROFICIENCY = re.compile(r"\b(basic|beginner|familiar|intermediate|novice|some knowledge)\b", re.I)
DATE_FORMATS = [("Mon YYYY", re.compile(r"\b[A-Z][a-z]{2,8}\.? \d{4}\b")), ("MM/YYYY", re.compile(r"\b\d{1,2}/\d{4}\b")),
                ("Season YYYY", re.compile(r"\b(Winter|Summer|Fall|Spring) \d{4}\b")), ("YYYY", re.compile(r"^\d{4}\b"))]


def _first_word(b: str) -> str:
    m = re.match(r"\s*([A-Za-z-]+)", b)
    return m.group(1).lower() if m else ""


def _is_past(word: str) -> bool:
    return word in IRREGULAR_PAST or (word.endswith("ed") and len(word) > 3)


# --- proofreading ---------------------------------------------------------------------------

TITLE_WORDS = set("""intern internship interns engineer engineering developer development analyst analytics software
quantitative quant trading trader research researcher data scientist science systems system technical technology
operations operation product manager management designer design associate assistant consultant specialist coordinator
administrator architect lead junior senior student machine learning backend frontend full stack mobile web cloud
security infrastructure platform applications application business finance financial risk strategy marketing co-op coop
teaching laboratory programmer programming hardware firmware embedded electrical mechanical robotics devops site
reliability quality assurance test testing automation information support help desk president founder co-founder vice
captain member volunteer tutor ambassador head chief officer executive director partner principal staff fellow scholar
apprentice trainee summer winter fall spring team campus club society chair editor writer mentor organizer host
incoming part-time full-time contract freelance ai ml sre qa it ui ux""".split())
DANGLING = {"the", "a", "an", "of", "to", "with", "and", "or", "versus", "vs", "for", "in", "on", "by", "at", "from",
            "into", "than", "as", "while", "which", "that", "across", "over", "via", "its", "their", "our", "my"}
MONTHS = {m.lower(): m for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov",
                                 "Dec", "January", "February", "March", "April", "June", "July", "August", "September",
                                 "October", "November", "December", "Present", "Current"]}
_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*[A-Za-z]")
_dict_cache: dict = {}


def _dictionary() -> tuple[set[str], dict]:
    """English words (the system word list) plus tech terms, and words grouped by first letter for suggestions."""
    if "words" not in _dict_cache:
        words: set[str] = set()
        for f in ("/usr/share/dict/words", "/usr/share/dict/web2"):
            try:
                words |= {w.strip().lower() for w in open(f, encoding="utf-8", errors="ignore") if w.strip()}
                break
            except OSError:
                continue
        from .textutil import SKILLS
        for name in SKILLS:
            words |= {x.lower() for x in re.findall(r"[A-Za-z]+", name)}
        words |= set("""api apis backend frontend fullstack dataset datasets workflow workflows microservice microservices
            pipeline pipelines deploy deployed deploying scalable latency throughput repo repos codebase refactor refactored
            onboarding onboarded webhook webhooks dashboard dashboards analytics llm llms rag embeddings chatbot chatbots
            async realtime runtime config configs schema schemas frameworks middleware serverless kubernetes devops
            internship internships coop email emails online offline login logins signup timestamp benchmarking
            benchmarked containerized dockerized automations orchestration orchestrated geo fintech saas ui ux crud
            json csv sql nosql etl kpi kpis sla slas incident incidents greeks var""".split())
        common = Counter()
        from .config import CACHE_DIR
        cached = CACHE_DIR / "vocabulary.json"
        try:
            if cached.exists() and time.time() - cached.stat().st_mtime < 7 * 86400:
                common = Counter(json.loads(cached.read_text()))
        except (OSError, ValueError):
            pass
        if not common:
            try:  # today's English, from the job postings already in the database (the system list is from 1934)
                from . import db
                for row in db.rows("SELECT substr(description, 1, 4000) AS d FROM jobs WHERE description IS NOT NULL"
                                   " AND description != '' LIMIT 3000"):
                    common.update(w.lower() for w in re.findall(r"[A-Za-z]{3,}", row["d"] or ""))
                common = Counter({w: n for w, n in common.items() if n >= 2})
                if common:  # kept on this computer for a week, so it isn't read again from the database each start
                    cached.write_text(json.dumps(common))
            except Exception:
                pass
        words |= {w for w, n in common.items() if n >= 2}
        sure = {w for w, n in common.items() if n >= 8} | {w for w in words if not common}  # suggestions come from here
        by_first: dict[str, list[str]] = {}
        for w in sure:
            if w.isalpha():
                by_first.setdefault(w[0], []).append(w)
        _dict_cache.update(words=words, by_first=by_first, modern=bool(common))
    return _dict_cache["words"], _dict_cache["by_first"]


def _known(w: str, words: set[str], extra: set[str]) -> bool:
    lw = w.lower().replace("’", "'")
    if lw in words or lw in extra or lw.split("'")[0] in words:
        return True
    for suf, add in (("s", ""), ("es", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"), ("ly", ""), ("er", ""),
                     ("ers", ""), ("ies", "y"), ("ied", "y"), ("ment", ""), ("ments", ""), ("ation", "e"), ("ations", "e")):
        if lw.endswith(suf):
            stem = lw[: -len(suf)]
            if stem + add in words or (len(stem) > 2 and stem[-1] == stem[-2] and stem[:-1] in words):  # controlling
                return True
    return False


def _suggest(w: str, by_first: dict, cutoff: float = 0.86) -> str | None:
    import difflib
    lw = w.lower()
    pool = [x for x in by_first.get(lw[0], []) if abs(len(x) - len(lw)) <= 2]
    hit = difflib.get_close_matches(lw, pool, n=1, cutoff=cutoff)
    if not hit:
        return None
    return hit[0].capitalize() if w[0].isupper() else hit[0]


def _resume_names(r: dict) -> set[str]:
    """Words that are names in this resume (people, companies, schools, projects, places, skills): never typos."""
    bits = [r["header"].get("name", ""), r["header"].get("location", "")]
    for sec in ("education", "experience", "projects"):
        for e in r.get(sec, []):
            bits += [e.get(k, "") for k in ("school", "company", "name", "location", "tech")]
    bits += [s["items"] for s in r.get("skills", [])]
    return {w.lower() for b in bits for w in re.findall(r"[A-Za-z]+", b or "")}


def _replace_word(text: str, old: str, new: str) -> str:
    return re.sub(rf"(?<![A-Za-z]){re.escape(old)}(?![A-Za-z])", new, text, count=1)


def proofread(r: dict) -> list[dict]:
    """Real mistakes, each with the exact fix: typos, wrong capitals, unfinished sentences, lines split when the file
    was read, stray spaces and punctuation, mixed date dashes. `auto` marks the safe mechanical ones."""
    words, by_first = _dictionary()
    names = _resume_names(r)
    out: list[dict] = []

    def add(path, msg, op=None, sev="high", auto=False, cat="style"):
        out.append({"category": cat, "severity": sev, "message": msg, "path": path, "op": op, "auto": auto,
                    "kind": "mistake"})

    def swap(old: str, new: str) -> dict:
        return {"type": "replace", "find": old, "replace": new}

    def check_words(text: str, path: list, title: bool = False) -> None:
        sentence_start = True
        for m in _WORD.finditer(text):
            w = m.group(0)
            prev = text[:m.start()].rstrip()
            sentence_start = not prev or prev[-1] in ".:;!?•-–—("
            if re.fullmatch(r"[A-Z]{2}[a-z]+", w) and (w.lower() in MONTHS or w.lower() in words) and not w.endswith("s"):
                good = MONTHS.get(w.lower(), w.capitalize())
                add(path, f"Capitals: “{w}” → “{good}”", swap(w, good), auto=True)
                return
            if len(w) < 4 or any(c.isupper() for c in w[1:]) or w.lower() in names:
                continue
            if text[m.end():m.end() + 1] == "-" or text[m.start() - 1:m.start()] == "-":
                continue  # part of a compound like multi-strategy or co-op
            if title:
                lw = w.lower()
                if lw in TITLE_WORDS or lw.rstrip("s") in TITLE_WORDS or lw + "s" in TITLE_WORDS:
                    continue
                import difflib
                hits = difflib.get_close_matches(lw, TITLE_WORDS, n=4, cutoff=0.8)
                # "Inter" is as close to "Winter" as to "Intern": a word cut short is the likelier typo
                hit = [h for h in hits if h.startswith(lw)] or [h for h in hits if len(lw) >= 6]
                if hit:
                    good = hit[0].capitalize() if w[0].isupper() else hit[0]
                    add(path, f"Typo: “{w}” → “{good}”", swap(w, good), auto=True)
                    return
                continue
            if w[0].isupper() and not sentence_start:
                continue  # a proper noun in the middle of a sentence
            if _known(w, words, names) or not _dict_cache.get("modern"):
                continue  # without today's vocabulary (no postings yet) an old dictionary would flag modern words
            good = _suggest(w, by_first)
            if good and good.lower() != w.lower():
                add(path, f"Spelling: “{w}” → “{good}”?", swap(w, good))
                return

    def tidy(text: str) -> str:
        t = re.sub(r"[ \t]{2,}", " ", text).strip()
        t = re.sub(r"\s+,", ",", t)
        return t

    h = r["header"]
    for k in ("name", "location"):
        if h.get(k) and tidy(h[k]) != h[k]:
            add(["header", k], f"Stray spaces in “{h[k].strip()}”", {"type": "tidy"}, sev="low", auto=True)
    for sec, fields in (("education", ("school", "degree", "location", "dates")),
                        ("experience", ("title", "company", "location", "dates")),
                        ("projects", ("name", "tech", "dates"))):
        for i, e in enumerate(r.get(sec, [])):
            for f in fields:
                v = e.get(f) or ""
                if v and tidy(v) != v:
                    add([sec, i, f], f"Stray spaces in “{v.strip()}”", {"type": "tidy"}, sev="low", auto=True)
                    v = tidy(v)
                if f in ("title", "degree"):
                    check_words(v, [sec, i, f], title=True)
                elif f == "dates":
                    check_words(v, [sec, i, f])
            for j, d in enumerate(e.get("details", [])):
                if d.strip().endswith(":") and j + 1 < len(e["details"]):
                    add([sec, i, "details", j], f"“{d.strip()}” is a heading split from its line: join them",
                        {"type": "merge_next"}, sev="medium", auto=True)
    # dates: one dash style
    dated = [(sec, i, e["dates"]) for sec in ("education", "experience", "projects") for i, e in enumerate(r.get(sec, []))
             if e.get("dates")]
    en = sum("–" in d for _, _, d in dated)
    for sec, i, d in dated:
        if en and "–" not in d and re.search(r"\s-\s|\w-\w", d) and not re.search(r"\d{4}-\d{2}", d):
            add([sec, i, "dates"], f"Dash: “{d}” uses “-” where the others use “–”", {"type": "dash"}, sev="low", auto=True)
    # skills: stray punctuation at the end of a list
    for i, sk in enumerate(r.get("skills", [])):
        items = sk.get("items") or ""
        if re.search(r"[,;|]\s*$", items) or re.search(r"\s+,", items) or "  " in items:
            add(["skills", i, "items"], f"{sk.get('label') or 'Skills'}: stray comma or spaces at the end",
                {"type": "trim_punct"}, sev="low", auto=True)
    # bullets
    for sec in ("experience", "projects"):
        for i, e in enumerate(r.get(sec, [])):
            bl = e.get("bullets", [])
            for j, b in enumerate(bl):
                if not b.strip():
                    continue
                path = [sec, i, "bullets", j]
                first = b.strip()[0]
                if first.islower() and j > 0 and not bl[j - 1].rstrip().endswith((".", "!", "?")):
                    add(path, "This is the end of the line above (split when your file was read): join them",
                        {"type": "merge_prev"}, sev="high", auto=True)
                    continue
                last = re.sub(r"[^\w’']+$", "", b.strip()).split()[-1:] or [""]
                if last[0].lower() in DANGLING:
                    add(path, f"Stops mid-sentence (“…{' '.join(b.strip().split()[-3:])}”). Finish it or cut the end",
                        sev="high", cat="completeness")
                if tidy(b) != b.strip() or b != b.strip():
                    add(path, "Stray spaces", {"type": "tidy"}, sev="low", auto=True)
                check_words(b, path)
    return out


def unglue_project(name: str) -> tuple[str, str] | None:
    """“PriceCore: …Pricing EngineC++, Python (pybind11), FastAPI” -> the name, and the tech list stuck to it."""
    for m in re.finditer(r"(?<=[a-z0-9)])(?=[A-Z])", name):
        head, tail = name[:m.start()].strip(), name[m.start():].strip()
        parts = [x.strip() for x in tail.split(",") if x.strip()]
        # the tech list starts right at the seam: its first item is short and is itself a skill
        if len(parts) >= 2 and len(parts[0]) <= 25 and detect_skills(parts[0]) and len(head) >= 4:
            return head, tail
    return None


def _get(r: dict, path: list):
    node = r
    for k in path:
        node = node[k]
    return node


def _set(r: dict, path: list, value) -> None:
    _get(r, path[:-1])[path[-1]] = value


def _tidy(text: str) -> str:
    return re.sub(r"\s+,", ",", re.sub(r"[ \t]{2,}", " ", text or "")).strip()


def apply_fix(r: dict, issue: dict) -> bool:
    """Apply one checker suggestion to the resume in place. Returns whether anything changed."""
    op, path = issue.get("op"), issue.get("path")
    if not op or not path:
        return False
    try:
        t = op["type"]
        if t == "set_fields":
            _get(r, path).update(op["fields"])
            return True
        cur = _get(r, path)
        if t == "set_value":
            new = op["value"]
        elif t == "replace":
            new = _replace_word(cur, op["find"], op["replace"]) if op["find"] in cur else cur
        elif t == "tidy":
            new = _tidy(cur)
        elif t == "dash":
            new = re.sub(r"\s*-\s*", " – ", cur, count=1)
        elif t == "trim_punct":
            new = re.sub(r"[,;|\s]+$", "", _tidy(cur))
        elif t == "merge_prev":
            lst, j = _get(r, path[:-1]), path[-1]
            if j < 1 or j >= len(lst):
                return False
            lst[j - 1] = f"{lst[j - 1].rstrip()} {lst[j].strip()}"
            del lst[j]
            return True
        elif t == "merge_next":  # a "Heading:" line takes the list that follows it ("a; b; c")
            lst, j = _get(r, path[:-1]), path[-1]
            if j + 1 >= len(lst):
                return False
            lst[j] = f"{lst[j].strip()} {lst[j + 1].strip()}"
            del lst[j + 1]
            while j + 1 < len(lst) and lst[j].rstrip().endswith(";"):
                lst[j] = f"{lst[j].strip()} {lst[j + 1].strip()}"
                del lst[j + 1]
            return True
        else:
            return False
        if new == cur:
            return False
        _set(r, path, new)
        return True
    except (KeyError, IndexError, TypeError):
        return False


def apply_fixes(r: dict, ids: list[str] | str = "auto") -> tuple[dict, int]:
    """Apply checker fixes by id, or every safe one ("auto"). Edits that change list positions (joining split lines)
    go last, from the bottom up, so the other paths stay valid."""
    from .resume_studio import normalize  # resume_studio imports this module
    r = normalize(r)
    issues = [i for i in lint(r)["issues"] if i.get("op") and (i["auto"] if ids == "auto" else i["id"] in ids)]
    moves = [i for i in issues if i["op"]["type"] in ("merge_prev", "merge_next")]
    edits = [i for i in issues if i not in moves]
    n = sum(apply_fix(r, i) for i in edits)
    for i in sorted(moves, key=lambda i: [str(x) if isinstance(x, str) else f"{x:05d}" for x in i["path"]], reverse=True):
        n += apply_fix(r, i)
    return r, n


def _bullets(r: dict):
    """Yield (section, entry_index, bullet_index, bullet, is_current_role)."""
    for sec in ("experience", "projects"):
        for i, e in enumerate(r.get(sec, [])):
            current = bool(re.search(r"present|current|now", e.get("dates", ""), re.I))
            for j, b in enumerate(e.get("bullets", [])):
                if b.strip():
                    yield sec, i, j, b.strip(), current


def lint(r: dict) -> dict:
    from .resume_studio import normalize  # resume_studio imports this module
    r = normalize(r)
    issues: list[dict] = []

    def add(cat, sev, msg, path=None, fix=None):
        issues.append({"category": cat, "severity": sev, "message": msg, "path": path, "fix": fix})

    bullets = list(_bullets(r))
    n = len(bullets)
    strong = metric = specific = 0
    verbs = Counter()
    endings = Counter()
    for sec, i, j, b, current in bullets:
        path = [sec, i, "bullets", j]
        w = _first_word(b)
        verbs[w] += 1
        endings["period" if b.endswith(".") else "none"] += 1
        has_metric = bool(METRIC.search(b))
        fragment = b[:1].islower()  # half of a split line: proofread() offers to join it
        if WEAK_OPENERS.match(b):
            rewrites = suggest_rewrites(b)
            add("impact", "medium", f"“{b.split()[0]}” reads like a duty. Lead with what you did: Built, Shipped, Reduced, Led…",
                path, rewrites[0] if rewrites else None)
        elif w in STRONG_VERBS or w in PRESENT_FORMS or w in IRREGULAR_PAST or _is_past(w):
            strong += 1
        elif not fragment and not re.match(r"^[A-Z][a-z]+(ed|t)$", b.split()[0] if b.split() else ""):
            add("impact", "low", "Lead with a verb that says what you did (Built, Designed, Automated…)", path)
        metric += int(has_metric)
        if (HOW.search(b) or detect_skills(b)) and len(b) >= 60:
            specific += 1  # says what was built and with what
        if len(b) < 45 and not fragment:
            add("brevity", "low", "Short. Say what you built, how, and what changed", path)
        elif len(b) > CHARS_PER_LINE * 2:
            add("brevity", "medium", f"About {math.ceil(len(b) / CHARS_PER_LINE)} lines long. Trim it to 2", path)
        if PRONOUNS.search(b):
            add("style", "medium", "Drop first-person words (I, my, we)", path)
        if BUZZWORDS.search(b):
            add("style", "low", f"“{BUZZWORDS.search(b).group(0)}” is a cliché. Show it with evidence instead", path)
        for rx, rep in FILLER:
            m = rx.search(b)
            if m:
                add("style", "low", f"Filler “{m.group(0).strip()}”" + (f": use “{rep}”" if rep else ": cut it"), path,
                    rx.sub(rep, b, count=1).replace("  ", " ").strip())
                break
        if sec == "experience" and not current and w in PRESENT_FORMS and not _is_past(w):
            add("style", "medium", "Past role: use past tense (Build → Built)", path)

    for w, c in verbs.items():
        if w and c >= 3:
            add("style", "low", f"“{w.title()}” starts {c} bullets. Vary your verbs")
    if endings["period"] and endings["none"]:
        add("style", "low", "Some bullets end with a period and some don't. Pick one style")

    # entries
    for i, e in enumerate(r["experience"]):
        k = len([b for b in e["bullets"] if b.strip()])
        label = f"{(e['title'] or 'Role').strip()} @ {(e['company'] or '?').strip()}"
        current = bool(re.search(r"present|current|now|incoming", f"{e.get('dates', '')} {e.get('title', '')}", re.I))
        if k == 0 and current:
            add("completeness", "low", f"{label}: new role? Add 1–2 bullets once you have something to show", ["experience", i])
        elif k < 2:
            add("completeness", "medium", f"{label}: add 2–4 bullets", ["experience", i])
        elif k > 5:
            add("brevity", "low", f"{label}: {k} bullets. Keep the 3–5 strongest", ["experience", i])
        if k and not detect_skills(" ".join(e["bullets"]) + " " + e["title"]):
            add("completeness", "low", f"{label}: name the tools/languages you used", ["experience", i])
        if not e.get("dates"):
            add("completeness", "medium", f"{label}: missing dates", ["experience", i])
    for i, p in enumerate(r["projects"]):
        glued = unglue_project(p.get("name") or "") if not p.get("tech") else None
        if glued:
            issues.append({"category": "style", "severity": "high", "kind": "mistake", "auto": True, "path": ["projects", i],
                           "message": f"“{glued[0]}” and its tech ({glued[1]}) got stuck together: split them",
                           "op": {"type": "set_fields", "fields": {"name": glued[0], "tech": glued[1]}}})
        elif not p.get("tech"):
            add("completeness", "low", f"Project “{p['name'] or i + 1}”: list its tech stack", ["projects", i])
        if not p.get("bullets"):
            add("completeness", "medium", f"Project “{p['name'] or i + 1}”: add 1–3 bullets on what it does and its impact",
                ["projects", i])

    # sections + header
    h = r["header"]
    if not h.get("email"):
        add("completeness", "high", "Add your email to the header")
    elif ".." in h["email"] or not re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", h["email"].strip()):
        add("completeness", "high", f"Your email looks mistyped ({h['email']}). Recruiters can't reach you")
    if not any("linkedin" in l.lower() for l in h.get("links", [])):
        add("completeness", "low", "Add your LinkedIn URL")
    if not any(("github" in l.lower() or "gitlab" in l.lower()) for l in h.get("links", [])) and \
            len(h.get("links", [])) < 2:
        add("completeness", "medium", "Add a GitHub or portfolio link; reviewers look for code")
    if not r["education"]:
        add("completeness", "high", "Add an Education section")
    if not r["experience"] and len(r["projects"]) < 2:
        add("completeness", "high", "Add experience or at least 2–3 projects")
    if not r["skills"]:
        add("completeness", "medium", "Add a Skills section (Languages / Frameworks / Tools)")
    skill_text = " ".join(f"{s['label']} {s['items']}" for s in r["skills"])
    if SOFT_SKILLS.search(skill_text):
        add("style", "low", f"Soft skills like “{SOFT_SKILLS.search(skill_text).group(0)}” don't belong in Skills; "
                            "show them in bullets")
    if PROFICIENCY.search(skill_text):
        add("style", "low", "Skip proficiency words (basic, familiar). List what you can actually use")
    n_items = sum(len([x for x in re.split(r",|;", s["items"]) if x.strip()]) for s in r["skills"])
    if n_items > 35:
        add("brevity", "low", f"{n_items} skills listed. Trim to the ~20 you'd want to be asked about")

    # dates consistency
    used = set()
    for sec in ("education", "experience", "projects"):
        for e in r[sec]:
            d = e.get("dates", "")
            for name, rx in DATE_FORMATS:
                if d and rx.search(d):
                    used.add(name)
                    break
    if len(used - {"YYYY"}) > 1:
        add("style", "low", f"Mixed date formats ({', '.join(sorted(used))}). Use one, e.g. “May 2025 – Aug 2025”")

    issues.extend(proofread(r))

    # length estimate (exact page count comes from the PDF render)
    est = estimate_lines(r)
    if r["style"].get("one_page") and est > 62:
        if est > 78:  # beyond what fitting can squeeze without tiny text
            add("brevity", "high", f"Too long for one page even with fitting (~{est} lines). Cut your weakest bullets")
        elif est > 70:
            add("brevity", "low", f"Long (~{est} lines): it fits on one page with smaller text. Trimming a line or "
                                  "two would give it more room")
    elif est > 62:
        add("brevity", "high", f"Probably runs over one page (~{est} lines). Co-op resumes should be one page")
    elif est < 38 and n < 8:
        add("brevity", "low", "Lots of empty space. Add another project or more detail")

    # scoring
    ratio = lambda x: x / n if n else 0  # noqa: E731
    # numbers help when they're real, but they're a small part: a crisp technical bullet doesn't need one
    if n >= 4 and ratio(metric) < 0.2:
        add("impact", "low", "Optional: where you know a real number (users, data size, speed), a couple of bullets "
                             "could show scale. Not every bullet needs one.")
    impact = 40 * (0.50 * ratio(strong) + 0.35 * ratio(specific) + 0.15 * min(1.0, ratio(metric) / 0.4)) if n else 0
    sev_w = {"high": 6, "medium": 3, "low": 1.5}

    def cat_score(cat, full):
        lost = 0.0
        for i in issues:
            if i["category"] == cat:
                per_bullet = bool(i["path"]) and len(i["path"]) == 4
                lost += sev_w[i["severity"]] * (0.5 if per_bullet else 1.0)
        return max(0.0, full - lost)

    scores = {"impact": round(impact), "brevity": round(cat_score("brevity", 20)),
              "style": round(cat_score("style", 20)), "completeness": round(cat_score("completeness", 20))}
    total = sum(scores.values())
    for i in issues:
        if i.get("fix") and not i.get("op"):
            i["op"] = {"type": "set_value", "value": i["fix"]}
        i.setdefault("op", None)
        i.setdefault("fix", None)
        i.setdefault("auto", False)
        i.setdefault("kind", "clarity" if i["severity"] != "low" else "polish")
        i["id"] = hashlib.sha1(json.dumps([i["path"], i["message"]], ensure_ascii=False).encode()).hexdigest()[:10]
    issues.sort(key=lambda i: ({"mistake": 0, "clarity": 1, "polish": 2}[i["kind"]], {"high": 0, "medium": 1, "low": 2}[i["severity"]]))
    return {
        "score": total,
        "grade": "Excellent" if total >= 85 else "Strong" if total >= 72 else "Needs work" if total >= 55 else "Rough draft",
        "scores": scores,
        "issues": issues,
        "stats": {"bullets": n, "with_metrics": metric, "strong_verbs": strong, "specific": specific, "est_lines": est},
    }


CHARS_PER_LINE = 125  # a bullet line in the one-page templates (measured on a real Jake-style resume)


def estimate_lines(r: dict) -> int:
    lines = 4  # name + contact
    for sec in r["section_order"]:
        items = r.get(sec) or []
        if not items:
            continue
        lines += 2
        if sec == "skills":
            lines += sum(max(1, math.ceil(len(f"{s['label']}: {s['items']}") / 105)) for s in items)
            continue
        for e in items:
            lines += 2 if sec != "projects" else 1
            for b in e.get("bullets", []) + e.get("details", []) + e.get("items", []):
                lines += max(1, math.ceil(len(b) / CHARS_PER_LINE))
    return lines


# --- rewrite suggestions (rule-based) -------------------------------------------------------

_VERB_FOR = [
    (re.compile(r"\b(test|qa|bug)", re.I), "Tested"),
    (re.compile(r"\b(automat|script)", re.I), "Automated"),
    (re.compile(r"\b(pipeline|app|website|api|service|tool|feature|platform|model|system|bot|backend|frontend|"
                r"dashboard|database|integration|extension|library|cli|page|component)", re.I), "Built"),
    (re.compile(r"\b(design|ui|ux|figma|wireframe|prototype)", re.I), "Designed"),
    (re.compile(r"\b(analy|report|insight|data|research|metric)", re.I), "Analyzed"),
    (re.compile(r"\b(team|mentor|lead|club|event)", re.I), "Led"),
]
_WEAK_PREFIX = re.compile(r"^(?:(?:was|were) )?(worked on|worked with|worked to|helped (?:to )?|assisted (?:with|in) |"
                          r"responsible for |participated in |involved in |tasked with |in charge of |contributed to |"
                          r"took part in )", re.I)
_USED_TO = re.compile(r"^(?:used|utili[sz]ed|leveraged)\s+(?P<tool>.+?)\s+(?:in order\s+)?to\s+(?P<verb>[a-z]+)\s+(?P<rest>.+)$",
                      re.I)


def _past(verb: str) -> str:
    v = verb.lower()
    irregular = {"build": "built", "write": "wrote", "lead": "led", "run": "ran", "make": "made", "drive": "drove",
                 "grow": "grew", "cut": "cut", "teach": "taught", "set": "set", "find": "found", "send": "sent"}
    if v in irregular:
        return irregular[v].capitalize()
    if v.endswith("e"):
        return (v + "d").capitalize()
    if re.search(r"[^aeiou]y$", v):
        return (v[:-1] + "ied").capitalize()
    return (v + "ed").capitalize()


def suggest_rewrites(b: str) -> list[str]:
    """Concrete rewrites that never invent facts (and don't push numbers into every bullet)."""
    out = []
    original = b.strip().rstrip(".")
    text = re.sub(r"\s{2,}", " ", PRONOUNS.sub("", original)).strip(" ,")
    m = _USED_TO.match(text)
    if m:  # "Used React to build pages" -> "Built pages with React"
        text = f"{_past(m.group('verb'))} {m.group('rest')} with {m.group('tool')}"
    else:
        m = _WEAK_PREFIX.match(text)
        if m:
            rest = text[m.end():]
            first = rest.split()[0] if rest.split() else ""
            if re.match(r"^[a-z]+ing$", first):  # "responsible for testing x" -> "Tested x"
                stem = first[:-3]
                verb = _past(stem + ("e" if stem + "e" + "d" in STRONG_VERBS else ""))
                rest = rest[len(first):].strip()
            elif first and (first.lower() + "d" in STRONG_VERBS or first.lower() + "ed" in STRONG_VERBS):
                verb = _past(first)  # "helped automate x" -> "Automated x"
                rest = rest[len(first):].strip()
            else:
                verb = next((v for rx, v in _VERB_FOR if rx.search(rest)), "Developed")
            text = f"{verb} {rest}"
    for rx, rep in FILLER:
        text = rx.sub(rep, text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    text = text[0].upper() + text[1:] if text else text
    if text and text != original:
        out.append(text)
    if not HOW.search(text) and not detect_skills(text) and len(text) < 90:
        out.append(f"{text} using [tech/approach]")
    return out[:3]


def _fix_preview(r: dict, issue: dict) -> tuple[str, str]:
    """(before, after) text for a suggested fix, for showing it as a diff."""
    path, op = issue["path"], issue["op"]
    after_r = copy.deepcopy(r)
    if not apply_fix(after_r, issue):
        return "", ""
    if op["type"] == "merge_prev":
        lst, j = _get(r, path[:-1]), path[-1]
        return f"{lst[j - 1]} ⏎ {lst[j]}", _get(after_r, path[:-1])[j - 1]
    if op["type"] == "merge_next":
        lst, j = _get(r, path[:-1]), path[-1]
        return " ⏎ ".join(lst[j:j + 4]), _get(after_r, path[:-1])[j]
    if op["type"] == "set_fields":
        e, f = _get(r, path), _get(after_r, path)
        return e.get("name", ""), f"{f.get('name', '')} | {f.get('tech', '')}"
    return str(_get(r, path)), str(_get(after_r, path))


def rule_review(r: dict) -> dict:
    """The Resume Coach without AI: mistakes first, then the clarity fixes, each as an exact before/after."""
    from .resume_studio import normalize  # resume_studio imports this module
    r = normalize(r)
    L = lint(r)
    verdict = "Ready to send" if L["score"] >= 85 else "Almost there" if L["score"] >= 70 else "Needs work"
    strengths = []
    st = L["stats"]
    if st["bullets"] and st["strong_verbs"] / st["bullets"] >= 0.8:
        strengths.append("Bullets lead with strong action verbs")
    if st["bullets"] and st["specific"] / st["bullets"] >= 0.6:
        strengths.append("Most bullets say what you built and with which tools")
    if not any(i["category"] == "brevity" and i["severity"] == "high" for i in L["issues"]):
        strengths.append("Fits on one page")
    changes, seen = [], set()
    mistakes = [i for i in L["issues"] if i["kind"] == "mistake"]
    for i in L["issues"]:
        key = json.dumps(i["path"])
        if len(changes) >= 6 or key in seen or i["kind"] == "polish" or not i.get("op"):
            continue
        before, after = _fix_preview(r, i)
        if not after or before == after:
            continue
        seen.add(key)
        changes.append({"title": i["message"].split(":")[0] if i["kind"] == "mistake" else i["message"].split(".")[0],
                        "why": i["message"], "path": "", "before": before, "after": after, "fix_id": i["id"]})
    unfinished = [i for i in mistakes if not i.get("op")]
    for i in unfinished[:2]:
        changes.append({"title": "Needs you", "why": i["message"], "path": "", "before": "", "after": ""})
    first = (f"{len(mistakes)} small mistake{'s' if len(mistakes) != 1 else ''} to fix" if mistakes else "No mistakes found") + \
            f"; overall {L['grade'].lower()} ({L['score']}/100)."
    return {"verdict": verdict, "first_impression": first, "strengths": strengths, "changes": changes, "questions": []}
