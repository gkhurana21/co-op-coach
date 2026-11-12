"""MATCH, part T (target the ecosystem): niches.

A niche is every kind of employer behind one kind of role, grouped into segments (for quant: market makers, smaller
prop shops, systematic funds, pod shops, Canadian pension funds and bank desks, energy traders, exchanges, betting...).
The shipped ones live in niches.yaml; anyone can start their own. For each firm, a niche shows the roles open now
(from the postings Co-op Coach already reads), when the firm opens internships, and its careers page.

Following a niche tracks it: its firms' boards are read on every sync, its new roles always alert you, and
`window_reminders()` puts "these firms usually open now" on your calendar a couple of weeks ahead.

"Find more" (`find_more()`) is where the list grows: firms that keep posting this kind of role in your postings but
aren't on the list, and, with an AI connected, firms and whole segments it thinks you're missing. Nothing an AI names
reaches you before `verify()` has found it live (its postings, its job board, or its careers page answering).
"""

import datetime
import logging
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import httpx

from . import companies, db, store
from .config import PKG, read_yaml
from .sources import BlockedURL, ats, check_public, client
from .textutil import CATEGORIES, company_key, is_internship, senior_title

log = logging.getLogger(__name__)

CATALOG = PKG / "niches.yaml"
STATUSES = ("added", "suggested", "hidden", "dismissed")
REVISION = 0  # bumped whenever a niche or its firm list changes: tracks.py caches which postings are in each
# a one-word firm name only matches a longer company name when the next word is one of these ("Optiver US",
# "Akuna Capital University"), never "Cumberland Farms"
_CORP = {"capital", "trading", "securities", "investments", "investment", "partners", "management", "financial", "us",
         "usa", "americas", "global", "markets", "university", "campus", "careers", "group", "asset", "advisors",
         "fund", "funds", "research", "quantitative", "llc", "lp", "europe", "asia", "canada", "digital", "energy"}


# --- the catalog -------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _catalog() -> dict:
    return read_yaml(CATALOG.read_text(encoding="utf-8")) or {}


def starters() -> list[dict]:
    return list(_catalog().get("starters") or [])


def _norm_key(name: str) -> str:
    return re.sub(r"^the ", "", company_key(name or ""))


def _shipped(nid: str) -> dict | None:
    n = _catalog().get(nid)
    if not n or nid == "starters":
        return None
    segs = []
    for s in n.get("segments") or []:
        firms = []
        for f in s.get("firms") or []:
            opened = f.get("opened")
            firms.append({"key": _norm_key(f["name"]), "name": f["name"], "aliases": f.get("aliases") or [],
                          "board": f.get("board"), "where": f.get("where") or "", "canada": f.get("canada") or "",
                          "opened": opened.isoformat() if isinstance(opened, datetime.date) else (opened or ""),
                          "careers": f.get("careers") or "", "small": bool(f.get("small")), "note": f.get("note") or "",
                          "origin": "catalog", "segment": s["id"]})
        segs.append({"id": s["id"], "name": s["name"], "blurb": " ".join((s.get("blurb") or "").split()),
                     "role_only": bool(s.get("role_only")), "firms": firms})
    return {"id": nid, "name": n["name"], "blurb": " ".join((n.get("blurb") or "").split()),
            "category": n.get("category") or "", "tiers": n.get("tiers") or [], "keywords": n.get("keywords") or [],
            "season": str(n.get("season") or ""),  # the season the opening dates are for ("Summer 2027")
            "all_roles": bool(n.get("all_roles")),  # every firm is in the field: any internship there counts
            "custom": False, "segments": segs}


def shipped_ids() -> list[str]:
    return [k for k in _catalog() if k != "starters"]


def _custom(pid: int, nid: str) -> dict | None:
    r = db.row("SELECT * FROM niches WHERE id=? AND profile_id=?", (nid, pid))
    if not r:
        return None
    return {"id": r["id"], "name": r["name"], "blurb": r["blurb"] or "", "category": r["category"] or "", "tiers": [],
            "keywords": db.loads(r["keywords"], []), "season": "", "custom": True, "segments": []}


def get(pid: int, nid: str) -> dict | None:
    """The niche as this person sees it: the shipped firms and the ones they added, each marked hidden if they took
    it off their list."""
    n = _shipped(nid) or _custom(pid, nid)
    if not n:
        return None
    rows = db.rows("SELECT * FROM niche_firms WHERE profile_id=? AND niche_id=?", (pid, nid))
    hidden = {r["key"] for r in rows if r["status"] == "hidden"}
    for s in n["segments"]:
        for f in s["firms"]:
            f["hidden"] = f["key"] in hidden
    by_seg = {s["id"]: s for s in n["segments"]}
    for r in rows:
        if r["status"] not in ("added", "hidden") or r["origin"] == "catalog":
            continue
        info = db.loads(r["info"], {}) or {}
        seg_id = r["segment"] or "yours"
        if seg_id not in by_seg:
            by_seg[seg_id] = {"id": seg_id, "name": info.get("segment_name") or ("Your list" if seg_id == "yours" else seg_id),
                              "blurb": info.get("segment_why") or "", "firms": []}
            n["segments"].append(by_seg[seg_id])
        if any(f["key"] == r["key"] for f in by_seg[seg_id]["firms"]):
            continue
        by_seg[seg_id]["firms"].append({"key": r["key"], "name": r["name"], "aliases": info.get("aliases") or [],
                                        "board": info.get("board"), "where": info.get("where") or "",
                                        "canada": info.get("canada") or "", "opened": "",
                                        "careers": info.get("careers") or "", "small": False,
                                        "note": r["why"] or "", "origin": r["origin"] or "you", "segment": seg_id,
                                        "verified": r["verified"] or "", "hidden": r["status"] == "hidden"})
    return n


def _firms(n: dict, include_hidden: bool = False) -> list[dict]:
    return [f for s in n["segments"] for f in s["firms"] if include_hidden or not f.get("hidden")]


# --- which company is which firm ---------------------------------------------------------------

class Matcher:
    """company_key of a posting -> the firm it belongs to. 'Citadel Securities' is Citadel Securities, not
    Citadel; 'Susquehanna International Group (SIG)' is SIG; 'Akuna Capital University' is Akuna Capital."""

    def __init__(self, firms: list[dict]):
        self.exact: dict[str, dict] = {}
        self.prefix: list[tuple[str, dict]] = []
        for f in firms:
            for nm in [f["name"], *(f.get("aliases") or [])]:
                k = _norm_key(nm)
                if not k:
                    continue
                self.exact.setdefault(k, f)
                if len(k) >= 4:
                    self.prefix.append((k, f))
        self.prefix.sort(key=lambda kf: -len(kf[0]))
        self._memo: dict[str, dict | None] = {}

    def match(self, ckey: str) -> dict | None:
        if ckey in self._memo:
            return self._memo[ckey]
        k = re.sub(r"^the ", "", ckey or "")
        f = self.exact.get(k)
        if f is None:
            for p, cand in self.prefix:
                if k.startswith(p + " "):
                    rest = k[len(p) + 1:].split()
                    if " " in p or (rest and rest[0] in _CORP):
                        f = cand
                        break
        self._memo[ckey] = f
        return f


# segments whose firms are trading firms through and through; elsewhere (an energy company, a bank, an exchange) the
# niche is one team inside a bigger company: its board shouldn't label every posting there "Quant & Trading", and only
# the niche's own roles there count for it
_CORE_SEGMENTS = {"market-makers", "prop-shops", "systematic", "multi-manager", "macro", "crypto"}


def _role_segments(n: dict) -> set[str]:
    """The niche's shipped segments where only its kind of role counts. A niche someone started has none."""
    if n.get("custom"):
        return set()
    segs = (_catalog().get(n["id"]) or {}).get("segments") or []
    if n.get("all_roles"):  # every firm is in the field, except segments of big companies with one team in it
        return {s["id"] for s in segs if s.get("role_only")}
    return {s["id"] for s in segs} - _CORE_SEGMENTS


class Members:
    """Which postings count for a niche (or for any of several: the ones someone follows). Every internship at a
    trading firm does; at a bank, a pension fund, an energy company or an exchange only the niche's kind of role does,
    so TD's HR co-op isn't a quant role. A firm taken off the list still claims its own postings, so hiding Citadel
    Securities doesn't hand its roles to Citadel."""

    def __init__(self, niches: list[dict]):
        self._parts = [(n, Matcher(_firms(n, include_hidden=True)), _role_segments(n)) for n in niches]

    def match(self, ckey: str) -> dict | None:
        """The firm on the list this company is, whatever the role (None for a firm taken off the list)."""
        for _, m, _ in self._parts:
            f = m.match(ckey)
            if f and not f.get("hidden"):
                return f
        return None

    def firm(self, j: dict) -> dict | None:
        """The firm this posting counts for, or None."""
        for n, m, role_only in self._parts:
            f = m.match(j["company_key"])
            if f is None or f.get("hidden"):
                continue
            if "not_intern" in j["flags"] or senior_title(j["title"]):
                return None
            if n.get("all_roles") and j.get("category") == "Business/Finance" and n.get("category") != j["category"]:
                continue  # an AI lab's or a fintech's sales and HR internships aren't what the niche is about
            if f["segment"] not in role_only or _in_niche_role(n, j):
                return f
        return None


def matcher(pid: int, nid: str) -> Members | None:
    n = get(pid, nid)
    return Members([n]) if n else None


def followed(pid: int) -> list[str]:
    return [r["niche_id"] for r in db.rows("SELECT niche_id FROM niche_follows WHERE profile_id=? ORDER BY created_at",
                                           (pid,))]


def followed_boards(pid: int) -> list[str]:
    """The job boards ("greenhouse:optiver") of every firm in the niches someone follows."""
    return [f["board"] for nid in followed(pid) for n in [get(pid, nid)] if n for f in _firms(n)
            if f.get("board") and ":" in f["board"]]


def followed_matcher(pid: int) -> Members | None:
    ns = [n for n in (get(pid, nid) for nid in followed(pid)) if n]
    return Members(ns) if any(_firms(n) for n in ns) else None


# --- what a niche looks like right now ---------------------------------------------------------

def _in_niche_role(n: dict, j: dict) -> bool:
    """Is this posting the kind of role the niche is about (for postings at firms not on the list)? A niche someone
    started goes by its keywords only: "Hardware" as a whole is far wider than "Robotics"."""
    if not n.get("custom"):
        if n.get("category") and j.get("category") == n["category"]:
            return True
        if j.get("tier") in (n.get("tiers") or []):
            return True
    kws = n.get("keywords") or []
    return bool(kws) and bool(re.search(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")s?\b", j["title"], re.I))


def _month_name(iso: str) -> str:
    try:
        return datetime.date.fromisoformat(iso).strftime("%B")
    except ValueError:
        return ""


def _learned(keys: list[str]) -> dict[str, list[dict]]:
    """Openings Co-op Coach saw itself, per company_key: [{'term', 'opened_at'}], newest first."""
    out: dict[str, list[dict]] = {}
    if not keys:
        return out
    for k in range(0, len(keys), 500):
        chunk = keys[k:k + 500]
        for r in db.rows(f"SELECT company_key, term, opened_at FROM firm_openings WHERE company_key IN "
                         f"({','.join('?' * len(chunk))}) ORDER BY opened_at DESC", chunk):
            out.setdefault(r["company_key"], []).append({"term": r["term"], "opened_at": r["opened_at"]})
    return out


def _term(t: str) -> tuple[str, int] | None:
    """'Summer 2028' -> ('Summer', 2028)."""
    m = re.fullmatch(r"(Winter|Spring|Summer|Fall) (20\d\d)", (t or "").strip())
    return (m[1], int(m[2])) if m else None


def _later(term: str, season: str) -> bool:
    """Is this term the same season as the niche's, in a later year ('Summer 2028' after 'Summer 2027')?"""
    a, b = _term(term), _term(season)
    return bool(a and b and a[0] == b[0] and a[1] > b[1])


def _bump(term: str, years: int) -> str:
    """'Summer 2027' a year on is 'Summer 2028'."""
    t = _term(term)
    return f"{t[0]} {t[1] + years}" if t else ""


def _openings(firms: list[dict], season: str) -> None:
    """Openings Co-op Coach saw itself, per firm: under its name, its aliases, or the companies its postings come from
    (the dates stay after the postings close). Sets f['seen_opening'], the newest, and f['when'], the opening the
    niche's windows go by: one seen for a later season than the catalog's when there is one, else the catalog's."""
    m = Matcher(firms)
    keys = {f["key"]: {k for nm in [f["name"], *(f.get("aliases") or [])] for k in (company_key(nm), _norm_key(nm)) if k}
            for f in firms}
    for j in store.collapsed_jobs():
        f = m.match(j["company_key"])
        if f:
            keys[f["key"]].add(j["company_key"])
    learned = _learned(sorted({k for ks in keys.values() for k in ks}))
    for f in firms:
        seen = sorted((x for k in keys[f["key"]] for x in learned.get(k, [])), key=lambda x: -x["opened_at"])
        f["seen_opening"] = seen[0] if seen else None
        later = [x for x in seen if _later(x["term"], season)]
        if later:  # the newest season, from its first posting
            x = min(later, key=lambda x: (-_term(x["term"])[1], x["opened_at"]))
            f["when"] = {"date": datetime.date.fromtimestamp(x["opened_at"]).isoformat(), "term": x["term"], "seen": True}
        elif f.get("opened"):
            f["when"] = {"date": f["opened"], "term": season, "seen": False}
        else:
            f["when"] = None


def detail(pid: int, nid: str) -> dict | None:
    n = get(pid, nid)
    if not n:
        return None
    firms = _firms(n, include_hidden=True)
    members = Members([n])
    now = db.now()
    roles: dict[str, list[dict]] = {}
    for j in store.scored_jobs(pid):
        f = members.firm(j)
        if f:
            roles.setdefault(f["key"], []).append(j)
    _openings(firms, n["season"])
    from . import contacts
    know: dict[str, int] = {}  # people you know at each firm (recruiters, LinkedIn connections, people you added)
    for ck, k in contacts.at_companies(pid).items():
        f = members.match(ck)
        if f:
            know[f["key"]] = know.get(f["key"], 0) + k
    hiring = 0
    for f in firms:
        f["people"] = know.get(f["key"], 0)
        js = sorted(roles.get(f["key"], []), key=lambda j: (j["eligibility"] == "no", -j["score"]))
        ok = [j for j in js if j["eligibility"] != "no"]
        f["open_roles"] = len(ok)
        f["closed_to_you"] = len(js) - len(ok)
        f["new_week"] = sum(1 for j in ok if j["fresh"] and j["first_seen"] >= now - 7 * 86400)
        f["best"] = {"id": ok[0]["id"], "title": ok[0]["title"], "score": ok[0]["score"], "url": ok[0]["url"],
                     "app_status": ok[0]["app_status"]} if ok else None
        f["opens"] = _month_name(f["opened"]) if f.get("opened") else ""
        f["link"] = f["careers"] or board_url(f.get("board")) or ""
        if ok and not f.get("hidden"):
            hiring += 1
    visible = [f for f in firms if not f.get("hidden")]
    sugg = [{**r, "info": db.loads(r["info"], {}) or {}} for r in db.rows(
        "SELECT * FROM niche_firms WHERE profile_id=? AND niche_id=? AND status='suggested' ORDER BY created_at DESC",
        (pid, nid))]
    return {
        **n,
        "following": nid in followed(pid),
        "stats": {"firms": len(visible), "hiring": hiring, "roles": sum(f["open_roles"] for f in visible),
                  "segments": sum(1 for s in n["segments"] if any(not f.get("hidden") for f in s["firms"])),
                  "canada": sum(1 for f in visible if f.get("canada") or f["segment"] == "canada"),
                  "small": sum(1 for f in visible if f.get("small")),
                  "people": sum(f["people"] for f in visible)},
        "windows": windows(visible, season=n["season"]),
        "suggestions": sugg,
    }


def summary_list(pid: int) -> list[dict]:
    """Every niche this person can open, with a few numbers each."""
    out = []
    fol = set(followed(pid))
    for nid in shipped_ids():
        n = get(pid, nid)
        out.append({"id": nid, "name": n["name"], "blurb": n["blurb"], "custom": False, "following": nid in fol,
                    "firms": len(_firms(n)), "segments": len(n["segments"])})
    for r in db.rows("SELECT id FROM niches WHERE profile_id=? ORDER BY created_at", (pid,)):
        n = get(pid, r["id"])
        out.append({"id": n["id"], "name": n["name"], "blurb": n["blurb"], "custom": True, "following": n["id"] in fol,
                    "firms": len(_firms(n)), "segments": len(n["segments"]),
                    "suggested": db.row("SELECT COUNT(*) AS n FROM niche_firms WHERE profile_id=? AND niche_id=? AND"
                                        " status='suggested'", (pid, n["id"]))["n"]})
    m_all = {o["id"]: matcher(pid, o["id"]) for o in out}
    counts = {o["id"]: set() for o in out}
    for j in store.scored_jobs(pid):
        if j["eligibility"] == "no":
            continue
        for nid, mm in m_all.items():
            if mm and mm.firm(j):
                counts[nid].add(j["id"])
    for o in out:
        o["roles"] = len(counts[o["id"]])
    return out


def windows(firms: list[dict], today: datetime.date | None = None, season: str = "") -> dict:
    """When this niche's firms open internships, by month, and which months are coming up. Each firm goes by
    f['when'] (see _openings), or by its catalog date for the niche's season. Each month says which season its next
    opening hires for."""
    today = today or datetime.date.today()
    by_month: dict[int, list[dict]] = {}
    for f in firms:
        w = f["when"] if "when" in f else \
            {"date": f["opened"], "term": season, "seen": False} if f.get("opened") else None
        if w:
            d = datetime.date.fromisoformat(w["date"])
            by_month.setdefault(d.month, []).append({"name": f["name"], "day": d.day, "date": w["date"],
                                                     "term": w["term"], "seen": w["seen"]})
    months = []
    for mo in by_month:
        fs = sorted(by_month[mo], key=lambda x: x["day"])
        nxt = _next_date(mo, fs[0]["day"], today)
        months.append({"month": mo, "name": datetime.date(2000, mo, 1).strftime("%B"), "firms": fs, "next": nxt.isoformat(),
                       "season": _bump(fs[0]["term"], nxt.year - int(fs[0]["date"][:4]))})
    months.sort(key=lambda m: m["next"])  # the next one coming up first
    busiest = max(by_month, key=lambda m: len(by_month[m]), default=None)
    return {"months": months, "busiest": datetime.date(2000, busiest, 1).strftime("%B") if busiest else "",
            "dated": sum(len(v) for v in by_month.values()), "season": season,
            "seen": sum(1 for v in by_month.values() for x in v if x["seen"])}


def _next_date(month: int, day: int, today: datetime.date) -> datetime.date:
    d = datetime.date(today.year, month, min(day, 28))
    return d if d > today else datetime.date(today.year + 1, month, min(day, 28))


def board_url(board: str | None) -> str:
    if not board or ":" not in board:
        return ""
    kind, slug = board.split(":", 1)
    return {"greenhouse": f"https://job-boards.greenhouse.io/{slug}", "lever": f"https://jobs.lever.co/{slug}",
            "ashby": f"https://jobs.ashbyhq.com/{slug}", "workday": slug, "successfactors": f"https://{slug}/",
            "jibe": f"https://{slug}/", "yc": f"https://www.ycombinator.com/companies/{slug}"}.get(kind, "")


# --- following -----------------------------------------------------------------------------------

def follow(pid: int, nid: str, on: bool = True) -> dict:
    n = get(pid, nid)
    if not n:
        return {"error": "no such niche"}
    with db.tx() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO niche_follows (profile_id, niche_id, created_at) VALUES (?,?,?)",
                      (pid, nid, db.now()))
        else:
            c.execute("DELETE FROM niche_follows WHERE profile_id=? AND niche_id=?", (pid, nid))
    added = add_boards(n) if on else 0
    return {"ok": True, "following": on, "niche": n["name"], "boards_added": added}


def add_boards(n: dict) -> int:
    """Make sure the sync reads every firm board in the niche. Boards someone removed stay removed."""
    tier = (n.get("tiers") or [None])[0]
    rows = []
    for f in _firms(n):
        b = f.get("board")
        if b and ":" in b:
            kind, slug = b.split(":", 1)
            if kind in ats.FETCHERS:
                hint = tier if f.get("segment") in _CORE_SEGMENTS else None
                rows.append((kind, slug, f["name"], companies.static_tier(f["name"]) or hint))
    added = 0
    with db.tx() as c:
        for kind, slug, name, t in rows:
            exists = c.execute("SELECT 1 FROM companies WHERE ats=? AND lower(slug)=lower(?)", (kind, slug)).fetchone()
            if not exists:
                c.execute("INSERT OR IGNORE INTO companies (ats, slug, name, tier, origin) VALUES (?,?,?,?,'niche')",
                          (kind, slug, name, t))
                added += 1
    return added


# --- editing a niche ---------------------------------------------------------------------------

def create(pid: int, name: str, keywords: list[str] | None = None, category: str = "", blurb: str = "") -> dict:
    name = re.sub(r"\s+", " ", (name or "").strip())[:60]
    if not name:
        return {"error": "give the niche a name"}
    starter = next((s for s in starters() if s["name"].lower() == name.lower()), None)
    if starter:
        keywords = keywords or starter.get("keywords")
        category = category or starter.get("category") or ""
    if not keywords:
        keywords = [w for w in re.split(r"[^\w+#]+", name.lower()) if len(w) > 2 and w not in ("and", "the", "for")]
    nid = "custom-" + secrets.token_hex(3)
    with db.tx() as c:
        c.execute("INSERT INTO niches (id, profile_id, name, blurb, keywords, category, created_at) VALUES (?,?,?,?,?,?,?)",
                  (nid, pid, name, blurb, db.dumps(keywords[:12]), category if category in CATEGORIES else "", db.now()))
    _changed()
    return {"ok": True, "id": nid, "name": name}


def delete(pid: int, nid: str) -> dict:
    """Delete a niche someone started, with their firms and follow. The shipped ones stay."""
    if nid in shipped_ids():
        return {"error": "This niche comes with Co-op Coach, so it can't be deleted. You can unfollow it, or take "
                         "firms off its list."}
    with db.tx() as c:
        n = c.execute("DELETE FROM niches WHERE id=? AND profile_id=?", (nid, pid)).rowcount
        if n:
            c.execute("DELETE FROM niche_firms WHERE profile_id=? AND niche_id=?", (pid, nid))
            c.execute("DELETE FROM niche_follows WHERE profile_id=? AND niche_id=?", (pid, nid))
    _changed()
    return {"ok": bool(n)}


def _changed() -> None:
    global REVISION
    REVISION += 1


def _save_firm(pid: int, nid: str, key: str, name: str, status: str, origin: str, segment: str = "",
               info: dict | None = None, why: str = "", verified: str = "") -> None:
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO niche_firms (profile_id, niche_id, key, name, segment, status, origin, info,"
                  " why, verified, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (pid, nid, key, name, segment or None, status, origin, db.dumps(info or {}), why, verified, db.now()))
    _changed()


def set_firm(pid: int, nid: str, key: str, status: str) -> dict:
    """added (accept a suggestion), hidden (take a firm off your list), dismissed (not interested in a suggestion),
    or 'shown' to put a hidden firm back."""
    n = get(pid, nid)
    if not n:
        return {"error": "no such niche"}
    row = db.row("SELECT * FROM niche_firms WHERE profile_id=? AND niche_id=? AND key=?", (pid, nid, key))
    catalog = next((f for f in _firms(n, include_hidden=True) if f["key"] == key and f["origin"] == "catalog"), None)
    if status == "shown":
        if row and row["status"] == "hidden":
            with db.tx() as c:
                if row["origin"] == "catalog":  # a shipped firm: hiding it was the only row
                    c.execute("DELETE FROM niche_firms WHERE profile_id=? AND niche_id=? AND key=?", (pid, nid, key))
                else:  # one they added, or a find they accepted
                    c.execute("UPDATE niche_firms SET status='added' WHERE profile_id=? AND niche_id=? AND key=?",
                              (pid, nid, key))
            _changed()
        return {"ok": True}
    if status not in STATUSES:
        return {"error": f"status is one of {', '.join(STATUSES)}"}
    if row:
        with db.tx() as c:
            c.execute("UPDATE niche_firms SET status=? WHERE profile_id=? AND niche_id=? AND key=?", (status, pid, nid, key))
        _changed()
    elif catalog and status == "hidden":
        _save_firm(pid, nid, key, catalog["name"], "hidden", "catalog", catalog["segment"])
    else:
        return {"error": "no such firm in this niche"}
    if status == "added" and nid in followed(pid):
        add_boards(get(pid, nid))
    return {"ok": True}


def add_firm(pid: int, nid: str, name: str, url: str = "", segment: str = "") -> dict:
    """Someone adds a firm they know about. It's checked the same way, but kept even if nothing answers."""
    n = get(pid, nid)
    if not n:
        return {"error": "no such niche"}
    name = re.sub(r"\s+", " ", (name or "").strip())[:80]
    if not name:
        return {"error": "which firm?"}
    key = _norm_key(name)
    if not key:
        return {"error": f"{name} on its own doesn't say which firm. Type the firm's full name."}
    have = Matcher(_firms(n, include_hidden=True)).match(key)
    if have and have.get("hidden"):  # taken off the list before: put it back
        set_firm(pid, nid, have["key"], "shown")
        return {"ok": True, "name": have["name"], "restored": True, "verified": "back on your list", "found": True}
    if have:
        seg = next((s["name"] for s in n["segments"] if s["id"] == have["segment"]), "")
        return {"error": f"{have['name']} is already on the list" + (f", under {seg}." if seg else ".")}
    try:
        v = verify(name, url)
    except BlockedURL as e:
        return {"error": str(e)}
    seg = segment if any(s["id"] == segment for s in n["segments"]) else "yours"
    info = {"board": v.get("board"), "careers": url if url.startswith("http") else v.get("careers", ""),
            "segment_name": next((s["name"] for s in n["segments"] if s["id"] == seg), "Your list")}
    _save_firm(pid, nid, key, name, "added", "you", seg, info, verified=v["how"])
    if nid in followed(pid):
        add_boards(get(pid, nid))
    return {"ok": True, "name": name, "verified": v["how"], "found": v["ok"]}


# --- finding firms you're missing --------------------------------------------------------------

def verify(name: str, url: str = "", timeout: float = 12) -> dict:
    """Is this a real employer with a way in? In order: its postings are already in Co-op Coach; the job board or
    careers page given answers; a Greenhouse board under its name answers with the same company name.
    Returns {ok, how, board, careers}. Raises BlockedURL for links into a private network."""
    key = _norm_key(name)
    if not key:
        return {"ok": False, "how": "no name"}
    m = Matcher([{"name": name, "key": key}])
    hits = [j for j in store.collapsed_jobs() if m.match(j["company_key"])]
    if hits:
        interns = sum(1 for j in hits if "not_intern" not in j["flags"])
        return {"ok": True, "how": f"{len(hits)} posting{'s' if len(hits) != 1 else ''} in Co-op Coach now"
                                   + (f" ({interns} internships)" if interns != len(hits) else ""), "board": None,
                "careers": hits[0]["url"]}
    url = (url or "").strip()
    if url:
        check_public(url)
        found = ats.detect(url)
        if found:
            kind, slug = found
            try:
                jobs = ats.FETCHERS[kind](slug, name, None, set()) if kind != "workday" else \
                    ats.workday(slug, name, None, set(), 20)
                return {"ok": True, "how": f"its {kind.title()} board answers ({len(jobs)} internships open)",
                        "board": f"{kind}:{slug}", "careers": url}
            except Exception as e:
                log.info("board check for %s failed: %s", name, e)
        try:
            with client(timeout=timeout, public_only=True) as c:
                r = c.get(url)
            if r.status_code < 400:
                return {"ok": True, "how": "its careers page answers", "board": None, "careers": str(r.url)}
        except (httpx.HTTPError, BlockedURL) as e:
            log.info("careers page check for %s failed: %s", name, e)
    slug = re.sub(r"[^a-z0-9]", "", key)
    if len(slug) >= 3:
        try:
            with client(timeout=timeout) as c:
                r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            if r.status_code == 200 and _norm_key(r.json().get("name") or "") == key:
                return {"ok": True, "how": "a Greenhouse board under its name answers", "board": f"greenhouse:{slug}",
                        "careers": f"https://job-boards.greenhouse.io/{slug}"}
        except (httpx.HTTPError, ValueError) as e:
            log.info("greenhouse guess for %s failed: %s", name, e)
    return {"ok": False, "how": "nothing answered"}


def _from_postings(pid: int, n: dict, known: Matcher, skip: set[str]) -> list[dict]:
    """Companies with roles of this kind in the postings, that aren't on the list yet."""
    found: dict[str, dict] = {}
    for j in store.scored_jobs(pid):
        if "not_intern" in j["flags"] or senior_title(j["title"]) or j["company_key"] in skip or \
                known.match(j["company_key"]):
            continue
        if not _in_niche_role(n, j):
            continue
        f = found.setdefault(j["company_key"], {"name": j["company"], "key": _norm_key(j["company"]), "jobs": [],
                                                 "open_to_you": 0})
        f["jobs"].append(j["title"])
        f["open_to_you"] += j["eligibility"] != "no"
    ranked = sorted(found.values(), key=lambda f: (-f["open_to_you"], -len(f["jobs"])))
    return [f for f in ranked if f["key"] not in skip][:15]


def find_more(pid: int, nid: str, use_ai: bool = True) -> dict:
    """Firms (and segments) missing from a niche, each one checked before it's shown. Saved as suggestions."""
    from . import ai
    n = get(pid, nid)
    if not n:
        return {"error": "no such niche"}
    prof = store.get_profile(pid) or {}
    have = Matcher(_firms(n, include_hidden=True))
    old = {r["key"] for r in db.rows("SELECT key FROM niche_firms WHERE profile_id=? AND niche_id=?", (pid, nid))}
    out = {"from_postings": 0, "from_ai": 0, "dropped": [], "new_segments": [], "ai": None, "web": False}
    for f in _from_postings(pid, n, have, old):
        sample = ", ".join(sorted(set(f["jobs"]))[:2])
        _save_firm(pid, nid, f["key"], f["name"], "suggested", "postings", "found",
                   {"segment_name": "Found in your postings", "careers": ""},
                   why=f"Posting roles like this now: {sample}", verified=f"{len(f['jobs'])} open in Co-op Coach now")
        old.add(f["key"])
        out["from_postings"] += 1
    if use_ai and ai.available():
        p = prof.get("profile") or {}
        student = {"school": p.get("university"), "program": p.get("program"), "year": p.get("year_of_study"),
                   "work_authorization": (prof.get("prefs") or {}).get("citizenship"),
                   "locations": (prof.get("prefs") or {}).get("locations"), "skills": (p.get("skills") or [])[:15]}
        known = [f["name"] for f in _firms(n, include_hidden=True)]
        known += [r["name"] for r in db.rows("SELECT name FROM niche_firms WHERE profile_id=? AND niche_id=?", (pid, nid))]
        try:
            answer, web = ai.find_firms(n, known, student)
        except Exception as e:
            log.warning("finding firms with AI failed: %s", e)
            out["ai_error"] = str(e)[:200]
            answer, web = {"segments": []}, False
        out["ai"], out["web"] = ai.label(), web
        cands = []
        seg_ids = {s["name"].lower(): s["id"] for s in n["segments"]}
        for s in answer.get("segments") or []:
            seg = seg_ids.get((s.get("name") or "").lower()) or "ai-" + re.sub(r"[^a-z0-9]+", "-", (s.get("name") or "more").lower()).strip("-")[:40]
            if s.get("new") and seg.startswith("ai-"):
                out["new_segments"].append({"name": s.get("name"), "why": s.get("why") or ""})
            for f in s.get("firms") or []:
                k = _norm_key(f.get("name") or "")
                if not k or k in old or have.match(k):
                    continue
                old.add(k)
                cands.append((seg, s, f, k))

        def check(item):
            seg, s, f, k = item
            try:
                return item, verify(f["name"], f.get("careers_url") or "")
            except BlockedURL:
                return item, {"ok": False, "how": "link points into a private network"}
            except Exception as e:
                return item, {"ok": False, "how": f"check failed: {str(e)[:80]}"}

        with ThreadPoolExecutor(6) as ex:
            for (seg, s, f, k), v in ex.map(check, cands[:20]):
                if not v["ok"]:
                    out["dropped"].append({"name": f["name"], "why": v["how"]})
                    continue
                _save_firm(pid, nid, k, f["name"], "suggested", "ai", seg,
                           {"segment_name": s.get("name") or "More", "segment_why": s.get("why") or "",
                            "board": v.get("board"), "careers": v.get("careers") or "", "where": f.get("where") or ""},
                           why=f.get("why") or "", verified=v["how"])
                out["from_ai"] += 1
    out["suggestions"] = detail(pid, nid)["suggestions"]
    return out


# --- seasons -------------------------------------------------------------------------------------

def window_reminders(pid: int, nid: str, months: list[int] | None = None, today: datetime.date | None = None) -> dict:
    """One reminder per month that firms in the niche usually open internships, two weeks before the first of them,
    on the calendar link and as a notification. `months` picks which (all by default). Already there: skipped."""
    n = get(pid, nid)
    if not n:
        return {"error": "no such niche"}
    firms = _firms(n, include_hidden=True)
    _openings(firms, n["season"])
    w = windows([f for f in firms if not f.get("hidden")], today, n["season"])
    made = []
    with db.tx() as c:
        for mo in w["months"]:
            if months and mo["month"] not in months:
                continue
            names = [f["name"] for f in mo["firms"]]
            first = datetime.date.fromisoformat(mo["next"])
            due = datetime.datetime.combine(first - datetime.timedelta(days=14), datetime.time(9, 0))
            if due.date() <= (today or datetime.date.today()):
                due = datetime.datetime.combine((today or datetime.date.today()) + datetime.timedelta(days=1),
                                                datetime.time(9, 0))
            shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
            title = (f"{n['name']}: {shown} usually open internships in {mo['name']}"
                     + (f" ({mo['season']} hiring)" if mo["season"] else ""))[:200]
            # one per niche and month, whatever the firm list looks like now
            ref = f"niche:{nid}:{mo['next'][:7]}"
            if c.execute("SELECT 1 FROM reminders WHERE profile_id=? AND ref=? AND done=0", (pid, ref)).fetchone():
                continue
            rid = c.execute("INSERT INTO reminders (profile_id, title, due_at, created_at, ref) VALUES (?,?,?,?,?)",
                            (pid, title, int(due.timestamp()), db.now(), ref)).lastrowid
            made.append({"id": rid, "title": title, "due_at": int(due.timestamp())})
    return {"ok": True, "created": made, "months": len(w["months"])}


def record_openings(new_ids: set[str]) -> int:
    """After a sync: the first internship Co-op Coach sees from a company for a term is when that company opened.
    Kept per company and term, so next season's 'usually opens' is what actually happened."""
    if not new_ids:
        return 0
    ids = list(new_ids)
    found = []
    for k in range(0, len(ids), 500):
        chunk = ids[k:k + 500]
        for r in db.rows(f"SELECT company_key, title, terms, posted_at, first_seen FROM jobs WHERE id IN "
                         f"({','.join('?' * len(chunk))})", chunk):
            if not is_internship(r["title"]):
                continue
            when = r["posted_at"] or r["first_seen"]  # the board's own date when it gives one
            for term in db.loads(r["terms"], []) or []:
                if re.fullmatch(r"(Winter|Spring|Summer|Fall) 20\d\d", term):
                    found.append((r["company_key"], term, when))
    n = 0
    with db.tx() as c:
        for row in found:  # the earliest posting for the term is the opening, whichever sync saw it first
            n += c.execute("INSERT INTO firm_openings (company_key, term, opened_at) VALUES (?,?,?) ON CONFLICT"
                           "(company_key, term) DO UPDATE SET opened_at=MIN(firm_openings.opened_at,"
                           " excluded.opened_at) WHERE excluded.opened_at < firm_openings.opened_at",
                           row).rowcount or 0
    return n
