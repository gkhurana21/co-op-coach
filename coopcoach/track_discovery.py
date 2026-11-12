"""New kinds of role, found in the feed: Forward Deployed Engineer, GTM, and whatever comes next.

Once a day after a pull (tracks.after_pull), every posting title is cut into its distinctive words: the company's own
name, places, terms and years, seniority and internship words come out ("Year at Palantir - Forward Deployed Software
Engineer, Internship - US Government" leaves "forward deployed software engineer" and "government"). Phrases of one to
three words that aren't only generic words (engineer, data, strategy...) are candidates. One that:
  - is posted by at least MIN_COMPANIES companies, with at least MIN_OPEN open postings counting at most PER_COMPANY
    per company (Palantir's 32 FDE postings are one company's program, not a kind of role),
  - isn't a shipped track already (a shipped role track names it and holds 70% of its postings),
  - isn't on tracks.yaml's ignore list,
becomes a track. Phrases that are the same role merge: one inside another with the same postings ("deployed" and
"forward deployed"), and an acronym with its words ("GTM" and "go to market").

Each is labelled by how fast it's spreading: "new" when no company posted it before the last 30 days, "rising" when
at least twice as many companies posted it in the last 30 days as in a usual month of the 90 before, otherwise
"established" (shown under "More roles in the feed"). Status: active (passes today), steady (passed within 30 days),
faded (older; still opens if pinned). One a shipped track covers later is folded into it: its pins open that track.
No AI, nothing sent anywhere, the same feed always gives the same tracks.
"""

import re
from collections import defaultdict
from functools import lru_cache

from . import db

MIN_COMPANIES = 5
MIN_OPEN = 8
PER_COMPANY = 3
COVERED = 0.7       # a shipped role track holding this share of a phrase's postings (and naming it) covers it
SAME = 0.8          # a phrase inside another, sharing this share of both's postings, is the same role
MAX_ESTABLISHED = 24
RECENT, WINDOW = 30 * 86400, 120 * 86400
STEADY_FOR = 30 * 86400
REVISION = 0        # bumped on every run: tracks.py caches which postings are in each track

# words that never make a phrase on their own, or start or end one
_STOP = set("""intern interns internship internships co op coop student students summer fall winter spring autumn
term terms new grad grads graduate graduates junior jr senior sr i ii iii iv v early career careers program programme
programs placement placements month months year years week weeks the a an of for in at and with on to by from via or
m f d w x h remote hybrid onsite on site full time part off cycle entry level trainee apprentice apprenticeship
fellow fellowship fellows position role roles opportunity opportunities temporary temp contract paid based e g etc
university college campus undergraduate undergrad masters master phd mba bachelor bachelors s
us usa uk eu emea apac latam anz canada canadian usg nyc sf u.s u.k
al ak az ar ca co ct de fl ga hi id il ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc
sd tn tx ut vt va wa wv wi wy dc bc ab sk mb qc ns nb nl pe yt nt nu
jan january feb february mar march apr april may jun june jul july aug august sep sept september oct october nov
november dec december q1 q2 q3 q4 h1 h2
janvier février fevrier mars avril mai juin juillet août aout septembre octobre novembre décembre decembre hiver été
ete automne printemps stage stages stagiaire stagiaires alternance alternant alternante werkstudent praktikum
praktikant praktikantin becario becaria prácticas practicas tirocinio estágio estagio
ceo cfo cto coo cpo cmo first next multiple various several teams openings vacancies vacancy""".split())
# role nouns and broad function words: a phrase made only of these is a shipped track's job, not a new kind of role
GENERIC = set("""engineer engineers engineering developer developers development analyst analysts analytics associate
associates specialist specialists assistant assistants scientist scientists science researcher researchers research
designer designers design manager managers management consultant consultants consulting architect architects
technician technicians operator operators representative representatives coordinator coordinators officer officers
administrator administrators advisor advisors lead leader leaders staff member members worker workers trader traders
writer writers strategist strategists software hardware data business technology technologies technical tech
operations ops strategy marketing finance financial sales product products project projects program services service
solutions solution systems system digital global team teams general support it hr ai ml information platform
platforms applied application applications quality test testing web internal corporate commercial enterprise
client customer customers people office field process manufacturing mechanical electrical civil chemical computer
industrial intelligence machine learning deep cloud security network networks infrastructure planning communications
content growth brand partnerships account accounts risk audit tax legal investment investments banking bank
capital markets equity wealth insurance accounting supply chain logistics procurement human resources talent
acquisition recruiting recruitment health healthcare clinical medical pharmacy nursing care energy environmental
construction maintenance safety production plant store retail innovation transformation excellence portfolio
""".split())
_ROLE_NOUNS = ["engineer", "developer", "analyst", "scientist", "researcher", "designer", "strategist", "specialist",
               "consultant", "architect", "manager", "associate"]
_KEEP_INSIDE = {"to", "of", "and"}  # "go to market", "head of", "research and development"
_DISPLAY = {"gtm": "GTM", "ai": "AI", "ml": "ML", "fde": "FDE", "ui": "UI", "ux": "UX", "qa": "QA", "it": "IT",
            "sre": "SRE", "llm": "LLM", "llms": "LLMs", "hr": "HR", "ios": "iOS", "go to market": "Go-to-Market",
            "devrel": "DevRel", "fpa": "FP&A", "ehs": "EHS", "hse": "HSE", "rf": "RF", "asic": "ASIC", "fpga": "FPGA", "devops": "DevOps", "mlops": "MLOps", "saas": "SaaS", "api": "API", "gpu": "GPU"}
_SPLIT = re.compile(r"\s[-–—|:•]\s|[,()\[\]/|;:]|\s[–—]|[–—]\s")
_TOK = re.compile(r"[^\W_][\w+#.']*")


@lru_cache(maxsize=1)
def _ignore() -> frozenset:
    from . import tracks
    return frozenset(x.lower() for x in tracks._catalog().get("ignore") or [])


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]+", (s or "").lower()) if len(w) >= 3}


_PLACES: frozenset = frozenset()  # while find() runs: every place word in the feed's locations ("london", "austin")


@lru_cache(maxsize=65536)
def _grams(title: str, company: str, places: str) -> frozenset:
    """The phrases in a title: 1-3 words, never starting or ending on a stop word, never only generic words."""
    t = _norm(title)
    drop = _words(company) | _words(places)
    out = set()
    for part in _SPLIT.split(t):
        toks = [w.strip(".'") for w in _TOK.findall(part)]
        stop = [len(w) < 2 or w in _STOP or w in drop or w in _PLACES or any(c.isdigit() for c in w) for w in toks]
        for i in range(len(toks)):
            for n in (1, 2, 3):
                if i + n > len(toks):
                    break
                g = toks[i:i + n]
                if stop[i] or stop[i + n - 1] or any(stop[k] and toks[k] not in _KEEP_INSIDE for k in range(i, i + n)):
                    continue
                if all(w in GENERIC or w in _KEEP_INSIDE for w in g):
                    continue
                out.add(" ".join(g))
    return frozenset(out)


def _norm(title: str) -> str:
    t = re.sub(r"\bfp\s*&\s*a\b", "fpa", title.lower()).replace("&", " and ")
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)  # go-to-market, field-deployed
    return re.sub(r"\b(full|front|back) (stack|end)\b", r"\1\2", t)  # one word, as the shipped tracks read it


def grams(j: dict) -> frozenset:
    return _grams(j["title"], j.get("company") or "", " ".join(j.get("locations") or []))


def matcher(phrases: list[str]):
    """Is this posting in the found track with these phrases?"""
    want = frozenset(phrases)
    return lambda j: bool(want & grams(j))


def _initials(phrase: str) -> str:
    """go to market -> gtm, forward deployed engineer -> fde (research and development -> rd)."""
    return "".join(w[0] for w in phrase.split() if w not in ("and", "of"))


def _acronym(word: str, titles: list[str]) -> bool:
    """Written in capitals in most titles that have it: GTM, FDE, not Java."""
    if not 2 <= len(word) <= 5:
        return False
    rx = re.compile(r"\b" + re.escape(word) + r"\b", re.I)
    seen = [m.group(0) for t in titles for m in [rx.search(t)] if m]
    return bool(seen) and sum(1 for w in seen if w.isupper()) >= 0.6 * len(seen)


def display(phrase: str) -> str:
    if phrase in _DISPLAY:
        return _DISPLAY[phrase]
    return " ".join(_DISPLAY.get(w, w.capitalize()) for w in phrase.split())


def _noun(titles: list[str], core: str) -> str:
    """The role noun that follows the core in most of its titles ("forward deployed" -> engineer), or ""."""
    counts: dict[str, int] = defaultdict(int)
    words = core.split()
    for t in titles:
        toks = _TOK.findall(_norm(t))
        for i in range(len(toks) - len(words) + 1):
            if toks[i:i + len(words)] != words:
                continue
            for w in toks[i + len(words):i + len(words) + 3]:
                n = "engineer" if w.startswith("engineer") else next((r for r in _ROLE_NOUNS if w in (r, r + "s")), "")
                if n:
                    counts[n] += 1
                    break
            break
    if not counts:
        return ""
    noun, k = max(sorted(counts.items()), key=lambda kv: kv[1])
    return noun if k >= 0.4 * len(titles) else ""


def label(core: str, noun: str, also: list[str]) -> str:
    base = display(core)
    if noun and noun not in core.split():
        base = f"{base} {noun.capitalize()}"
    long = next((a for a in also if " " in a and _initials(a) == core.replace(" ", "")), "")
    return f"{base} ({display(long)})" if long and len(core) <= 4 and " " not in core else base


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def _rows(now: int) -> list[dict]:
    cols = "id, company, company_key, title, locations, category, tier, flags, first_seen, posted_at, active"
    rows = db.rows(f"SELECT {cols} FROM jobs WHERE active=1 OR first_seen >= ?", (now - WINDOW,))
    for r in rows:
        r["locations"] = db.loads(r["locations"], []) or []
        r["flags"] = db.loads(r["flags"], []) or []
    return rows


def _shipped_roles() -> list[tuple[str, re.Pattern | None, object]]:
    from . import tracks
    return [(tid, t["titles"], (lambda t: lambda j: tracks._role_has(t, j))(t))
            for tid, t in tracks._shipped().items() if t["kind"] == "role"]


def find(rows: list[dict], now: int, baseline: int = 0, roles=None) -> list[dict]:
    """Every kind of role the feed shows that nothing shipped covers, most companies first. Deterministic."""
    from .textutil import senior_title
    global _PLACES
    roles = _shipped_roles() if roles is None else roles
    rows = [r for r in rows if "not_intern" not in (r.get("flags") or []) and not senior_title(r["title"])]
    # a city in one posting's title is often only in another's locations: place words in the feed come out, when
    # they're written in locations at least as often as in titles ("london" yes, "market" of Market Street no)
    in_loc: dict[str, int] = defaultdict(int)
    in_title: dict[str, int] = defaultdict(int)
    for r in rows:
        for w in set().union(*(_words(loc) for loc in r.get("locations") or [])):
            in_loc[w] += 1
        for w in _words(r["title"]):
            in_title[w] += 1
    places = frozenset(w for w, k in in_loc.items() if k >= in_title.get(w, 0)) - _KEEP_INSIDE
    by_gram: dict[str, list[int]] = defaultdict(list)
    _PLACES = places
    _grams.cache_clear()
    try:
        for i, r in enumerate(rows):
            for g in grams(r):
                by_gram[g].append(i)
    finally:
        _PLACES = frozenset()
        _grams.cache_clear()
    # how far back the feed knows when postings opened: with less than a month before the last 30 days, nothing
    # can be called new or rising yet
    start = max(baseline, min((r["posted_at"] or r["first_seen"] for r in rows), default=now))
    prior_days = max(0, min(WINDOW, now - start) - RECENT) / 86400
    ignore = _ignore()

    def stats(idx: list[int]) -> dict:
        open_by: dict[str, int] = defaultdict(int)
        recent, prior, before = set(), set(), set()
        for i in idx:
            r = rows[i]
            if r["active"]:
                open_by[r["company_key"]] += 1
            when = r["posted_at"] or (r["first_seen"] if r["first_seen"] > baseline + 86400 else 0)
            if when >= now - RECENT:
                recent.add(r["company_key"])
            elif when >= now - WINDOW:
                prior.add(r["company_key"])
            else:
                before.add(r["company_key"])
        return {"open": sum(open_by.values()), "open_capped": sum(min(v, PER_COMPANY) for v in open_by.values()),
                "companies": len(open_by), "recent": len(recent), "prior": len(prior | before),
                "before": len(before)}

    # candidates: enough companies, at the per-company cap
    cands = {}
    for g, idx in by_gram.items():
        if g in ignore or len({rows[i]["company_key"] for i in idx}) < MIN_COMPANIES:
            continue
        s = stats(idx)
        if s["companies"] >= MIN_COMPANIES and s["open_capped"] >= MIN_OPEN:
            cands[g] = set(idx)
    # an acronym and its words go together even when one of them is rare: "fde" with forward deployed engineer,
    # "go to market" with gtm
    multis = [g for g in cands if " " in g]
    for g in sorted(by_gram):
        if g in cands or len(by_gram[g]) < 2:
            continue
        if " " in g:
            if _initials(g) in cands and len(_initials(g)) >= 3:
                cands[g] = set(by_gram[g])
        elif 3 <= len(g) <= 4 and g.isalpha() and g not in GENERIC and any(_initials(m) == g for m in multis):
            cands[g] = set(by_gram[g])

    # the same role written several ways: union-find over the candidates
    parent = {g: g for g in cands}

    def root(g):
        while parent[g] != g:
            parent[g] = parent[parent[g]]
            g = parent[g]
        return g

    def join(a, b):
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    names = sorted(cands)
    for a in names:
        if " " not in a and 3 <= len(a) <= 4:
            for b in names:
                if " " in b and _initials(b) == a:
                    join(a, b)
    for a in names:
        wa = set(a.split())
        for b in names:
            if a < b and (wa < set(b.split()) or set(b.split()) < wa):
                if len(cands[a] & cands[b]) >= SAME * max(len(cands[a]), len(cands[b])):
                    join(a, b)
    groups: dict[str, list[str]] = defaultdict(list)
    for g in names:
        groups[root(g)].append(g)

    out = []
    for members in groups.values():
        biggest = max(len(cands[g]) for g in members)
        core = sorted([g for g in members if len(cands[g]) >= SAME * biggest],
                      key=lambda g: (-len(g.split()), -len(cands[g]), g))[0]
        noun = _noun([rows[i]["title"] for i in cands[core]], core)
        same = [g for g in members if g != core and (_initials(g) == core or _initials(core) == g or
                                                     (noun and _initials(f"{core} {noun}") == g))]
        # its own acronym, written in at least two titles and not a role of its own: "FDE" for forward deployed
        # engineer
        for a in {_initials(core), _initials(f"{core} {noun}") if noun else ""}:
            if 3 <= len(a) <= 4 and a.isalpha() and a not in GENERIC and len(by_gram.get(a, ())) >= 2 and \
                    (a not in cands or a in members):
                same.append(a)
        phrases = sorted({core, *same})
        idx = sorted(set().union(*(set(by_gram[p]) for p in phrases)))
        s = stats(idx)
        if s["companies"] < MIN_COMPANIES or s["open_capped"] < MIN_OPEN:
            continue
        titles = [rows[i]["title"] for i in idx]
        noun = _noun(titles, core)
        # a kind of role has a role word ("Geotechnical Engineer"), several words ("forward deployed") or is written
        # as an acronym ("GTM"); one plain word ("thesis", "cash", "layout") is a topic, not a role
        if not noun and " " not in core and not _acronym(core, titles):
            continue
        name = label(core, noun, phrases)
        if name.lower() in ignore:
            continue
        active = [rows[i] for i in idx if rows[i]["active"]]
        covered = ""  # the track naming its distinctive words first ("ios" -> Mobile), then one naming the role noun
        for text in (" ".join(w for w in core.split() if w not in GENERIC) or core, name):
            covered = next((tid for tid, rx, has in roles if rx and rx.search(text) and
                            sum(1 for r in active if has(r)) >= COVERED * max(1, len(active))), "")
            if covered:
                break
        rate = s["prior"] / (prior_days / 30) if prior_days >= 30 else 0
        if prior_days < 30:
            trend = "established"
        elif s["recent"] >= MIN_COMPANIES and s["prior"] == 0:
            trend = "new"
        elif s["recent"] >= MIN_COMPANIES and s["recent"] >= 2 * max(rate, 1):
            trend = "rising"
        else:
            trend = "established"
        out.append({"id": _slug(core), "label": name, "phrases": phrases, "trend": trend, "covered": covered,
                    "stats": {**s, "pace": round(s["recent"] / max(rate, 1), 1) if rate else 0},
                    "_core": set(core.split()), "_idx": set(idx)})
    # a narrower way of writing a role found as well ("forward deployed software" inside "forward deployed") isn't a
    # track of its own
    narrower = {d["id"] for d in out for o in out if o is not d and o["_core"] < d["_core"]
                and len(d["_idx"] & o["_idx"]) >= SAME * len(d["_idx"])}
    out = [{k: v for k, v in d.items() if not k.startswith("_")} for d in out if d["id"] not in narrower]
    out.sort(key=lambda d: (-d["stats"]["companies"], -d["stats"]["open"], d["id"]))
    established = [d for d in out if d["trend"] == "established" and not d["covered"]][:MAX_ESTABLISHED]
    keep = {d["id"] for d in established} | {d["id"] for d in out if d["trend"] != "established" or d["covered"]}
    return [d for d in out if d["id"] in keep]


def run(now: int | None = None, rows: list[dict] | None = None) -> dict:
    """Find today's kinds of role and update the stored ones (active, steady, faded, folded)."""
    global REVISION
    now = now or db.now()
    baseline = int(db.meta_get("baseline_ts", 0) or 0)
    found = find(_rows(now) if rows is None else rows, now, baseline)
    stored = {r["id"]: r for r in db.rows("SELECT * FROM tracks_discovered")}
    seen = set()
    with db.tx() as c:
        for d in found:
            seen.add(d["id"])
            old = stored.get(d["id"])
            folded = d["covered"] or None
            status = "faded" if folded else "active"
            if old:
                c.execute("UPDATE tracks_discovered SET label=?, phrases=?, status=?, trend=?, stats=?, folded_into=?,"
                          " last_seen=? WHERE id=?", (d["label"], db.dumps(d["phrases"]), status, d["trend"],
                                                      db.dumps(d["stats"]), folded, now if not folded else
                                                      old["last_seen"], d["id"]))
            elif not folded:  # a phrase a shipped track already covers never becomes a track
                c.execute("INSERT INTO tracks_discovered (id, label, phrases, status, trend, stats, folded_into,"
                          " first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                          (d["id"], d["label"], db.dumps(d["phrases"]), status, d["trend"], db.dumps(d["stats"]),
                           None, now, now))
        for tid, old in stored.items():
            if tid in seen or old["folded_into"]:
                continue
            status = "steady" if old["last_seen"] >= now - STEADY_FOR else "faded"
            if status != old["status"]:
                c.execute("UPDATE tracks_discovered SET status=? WHERE id=?", (status, tid))
    db.meta_set("track_discovery_at", str(now))
    REVISION += 1
    top = [{"id": d["id"], "label": d["label"], "trend": d["trend"], "covered": d["covered"],
            "companies": d["stats"]["companies"], "open": d["stats"]["open"]} for d in found[:15]]
    db.meta_set("track_discovery_report", db.dumps(top))  # the sync report lists these for a look
    return {"found": len([d for d in found if not d["covered"]]), "top": top}


def run_if_due(now: int | None = None) -> dict:
    now = now or db.now()
    if int(db.meta_get("track_discovery_at", 0) or 0) > now - 20 * 3600:
        return {}
    return run(now)


def _decode(r: dict) -> dict:
    return {**r, "phrases": db.loads(r["phrases"], []) or [], "stats": db.loads(r["stats"], {}) or {}}


def listed() -> list[dict]:
    rows = db.rows("SELECT * FROM tracks_discovered ORDER BY id")
    out = [_decode(r) for r in rows]
    out.sort(key=lambda d: (-(d["stats"].get("companies") or 0), d["id"]))
    return out


def get(tid: str) -> dict | None:
    r = db.row("SELECT * FROM tracks_discovered WHERE id=?", (tid,))
    return _decode(r) if r else None


def card(d: dict) -> str:
    """"New · Forward Deployed Engineer · 61 open at 11 companies · 3× this month"."""
    s = d["stats"]
    head = {"new": "New", "rising": "Rising"}.get(d["trend"], "In the feed")
    pace = f" · {s['pace']:g}× this month" if d["trend"] == "rising" and s.get("pace", 0) >= 2 else ""
    return f"{head} · {d['label']} · {s.get('open', 0)} open at {s.get('companies', 0)} companies{pace}"
