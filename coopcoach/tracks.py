"""Tracks: the lenses Jobs is browsed through (Jobs → Matches | your pinned tracks | ＋ Tracks).

A track is a kind of role (Software, Quant roles, Data...) or a kind of employer (Startups in Canada, Trading firms &
funds, Banks & financial firms...), and every one opens the same page: its postings, its companies and its market.
They replace the Startups, Niches and Your market pages, and the company and role chips in Matches.

Track ids:
  role:<id>             a kind of role, shipped in tracks.yaml
  co:<id>               a kind of employer, shipped in tracks.yaml; the niche ones (co:quant-firms...) are the niches
  co:custom-<hex>       a niche someone started (niches.py)
  auto:<id>             a kind of role the feed keeps posting that nothing shipped covers (track_discovery.py)

Which postings are in which track is worked out once per set of postings (`index()`), so a page or the ＋ Tracks
list never re-reads 27,000 postings per track. Pins and follows are per person. Following a niche track still goes
through niches.follow (its firms' boards join the sync); other tracks just alert.
"""

import logging
import re
import threading
import time
from functools import lru_cache

from . import db, niches, startups, store, track_discovery
from .config import PKG, read_yaml
from .textutil import CATEGORIES, senior_title

log = logging.getLogger(__name__)

CATALOG = PKG / "tracks.yaml"
MAX_PINS = 6
MAX_DEFAULTS = 4
DAY = 86400
# the role tracks a role category stands for: targets typed in Settings, or inferred from the resume
CATEGORY_TRACKS = {"Software": "role:software", "AI/ML/Data": "role:ml", "Hardware": "role:hardware",
                   "Quant": "role:quant", "Product/Design": "role:product", "Business/Finance": "role:business"}
# Settings → "what you're aiming for" as employer tracks
AIM_TRACKS = {"yc": "co:yc", "startup": "co:startups", "bigcorp": "co:bigtech"}
STARTUP_NICHE = startups.NICHE  # the Waterloo & Toronto niche is the Startups track's own list
KINDS = {"role": "Role", "co": "Employers", "auto": "Role"}


# --- the catalog -----------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _catalog() -> dict:
    return read_yaml(CATALOG.read_text(encoding="utf-8")) or {}


def _rx(words: list[str] | None) -> re.Pattern | None:
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.I) if words else None


@lru_cache(maxsize=1)
def _shipped() -> dict[str, dict]:
    """Every shipped track by id, with its patterns compiled."""
    cat = _catalog()
    out: dict[str, dict] = {}
    for key, t in (cat.get("roles") or {}).items():
        out[f"role:{key}"] = {"id": f"role:{key}", "kind": "role", "name": t["name"], "icon": t.get("icon") or "",
                              "blurb": t.get("blurb") or "", "source": "shipped",
                              "categories": t.get("categories") or [], "titles": _rx(t.get("titles")),
                              "not": _rx(t.get("not")), "business": "Business/Finance" in (t.get("categories") or [])}
    emp = cat.get("employers") or {}
    for key, t in emp.items():
        names = t.get("names")
        if t.get("not_names"):  # "Other large employers": the tier without the companies another track names
            names = None
        n = niches._shipped(t["niche"]) if t.get("niche") else None
        out[f"co:{key}"] = {"id": f"co:{key}", "kind": "co", "name": t["name"], "icon": t.get("icon") or "",
                            "blurb": t.get("blurb") or (n or {}).get("blurb") or "", "source": "shipped",
                            "tiers": t.get("tiers") or [], "names": _rx(names),
                            "not_names": _rx((emp.get(t["not_names"]) or {}).get("names")) if t.get("not_names") else None,
                            "niche": t.get("niche") or (STARTUP_NICHE if t.get("source") == "startups" else None),
                            "startups": t.get("source") == "startups", "ai": bool(t.get("ai"))}
    return out


def niche_track(nid: str) -> str:
    """The track a niche is: co:quant-firms for quant, co:startups for Waterloo & Toronto, co:custom-1a2b3c."""
    for tid, t in _shipped().items():
        if t.get("niche") == nid:
            return tid
    return f"co:{nid}"


def _custom(pid: int) -> list[dict]:
    return [{"id": f"co:{r['id']}", "kind": "co", "name": r["name"], "icon": "🎯", "blurb": r["blurb"] or "",
             "source": "yours", "niche": r["id"]}
            for r in db.rows("SELECT id, name, blurb FROM niches WHERE profile_id=? ORDER BY created_at", (pid,))]


def _found() -> list[dict]:
    out = []
    for d in track_discovery.listed():
        out.append({"id": f"auto:{d['id']}", "kind": "auto", "name": d["label"], "icon": "✨",
                    "blurb": track_discovery.card(d), "source": "found", "trend": d["trend"], "status": d["status"],
                    "phrases": d["phrases"], "stats": d["stats"], "folded_into": d.get("folded_into")})
    return out


def all_tracks(pid: int) -> list[dict]:
    """Every track this person can open: shipped, their own niches, and the kinds of role found in the feed."""
    return [*_shipped().values(), *_custom(pid), *[t for t in _found() if t["status"] != "faded"
                                                    and not t.get("folded_into")]]


def get(pid: int, tid: str) -> dict | None:
    """One track. A found one that a shipped track now covers opens that one; a faded one still opens. "niche:quant"
    (an older link, a chat card) opens the niche's track."""
    if tid.startswith("niche:"):
        return get(pid, niche_track(tid[6:]))
    if tid in _shipped():
        return _shipped()[tid]
    if tid.startswith("co:"):
        return next((t for t in _custom(pid) if t["id"] == tid), None)
    if tid.startswith("auto:"):
        d = track_discovery.get(tid[5:])
        if not d:
            return None
        if d.get("folded_into") and d["folded_into"] in _shipped():
            return _shipped()[d["folded_into"]]
        return next((t for t in _found() if t["id"] == tid), None)
    return None


def public(t: dict) -> dict:
    """A track as the page sees it (no compiled patterns)."""
    return {k: t.get(k) for k in ("id", "kind", "name", "icon", "blurb", "source", "niche", "trend", "status")
            if t.get(k) is not None} | {"kind_label": KINDS[t["kind"]]}


# --- which postings are in a track ---------------------------------------------------------------------

def _internship(j: dict) -> bool:
    return "not_intern" not in (j.get("flags") or []) and not senior_title(j["title"])


def _role_has(t: dict, j: dict) -> bool:
    title = j["title"]
    if t["not"] and t["not"].search(title):
        return False
    if j.get("category") in t["categories"]:
        return True
    if t["titles"] and t["titles"].search(title):
        # a business role with a tech word in it ("AI Sales Intern", "Developer Marketing") isn't a tech track's
        return t["business"] or j.get("category") != "Business/Finance"
    return False


def _co_has(t: dict, j: dict) -> bool:
    if j.get("tier") not in t["tiers"]:
        return False
    if t["names"] and not t["names"].search(j["company"]):
        return False
    return not (t["not_names"] and t["not_names"].search(j["company"]))


_AI_NAME = re.compile(r"(\bai\b|\.ai\b|\bml\b|\bllm|\bagents?\b|\bneural|\bintelligence\b|\bcognitive|\bgenerative|\bautonom)", re.I)


class _Lenses:
    """Is this posting in each track? Built once for a person, used for every posting."""

    def __init__(self, pid: int, tracks: list[dict]):
        self.tracks = tracks
        self.members: dict[str, niches.Members] = {}
        for t in tracks:
            if t.get("niche") and not t.get("startups"):
                n = niches.get(pid, t["niche"])
                if n:
                    self.members[t["id"]] = niches.Members([n])
        self.startup_keys = _startup_keys(pid) if any(t.get("startups") for t in tracks) else set()
        self.by_phrase: dict[str, list[str]] = {}  # phrase -> found tracks: one look per posting, not one per track
        for t in tracks:
            if t["kind"] == "auto":
                for ph in t["phrases"]:
                    self.by_phrase.setdefault(ph, []).append(t["id"])
        ai = niches.get(pid, "ai") if any(t.get("ai") for t in tracks) else None
        self.ai_firms = niches.Members([ai]) if ai else None

    def tracks_of(self, j: dict) -> list[str]:
        if not _internship(j):
            return []
        out = []
        found = None
        for t in self.tracks:
            tid = t["id"]
            if t["kind"] == "role":
                hit = _role_has(t, j)
            elif t["kind"] == "auto":
                if found is None:
                    found = {x for g in track_discovery.grams(j) for x in self.by_phrase.get(g, ())}
                hit = tid in found
            elif t.get("ai"):  # young AI companies: the applied-AI and smaller AI firms on the list, YC companies
                # hiring for AI, and startups whose name says AI (not the big labs, not Databricks)
                firm = self.ai_firms.firm(j) if self.ai_firms else None
                tier = j.get("tier")
                hit = bool(firm and firm.get("segment") != "frontier" and (firm.get("segment") == "applied" or firm.get("small"))) \
                    or (tier == "yc" and j.get("category") == "AI/ML/Data") \
                    or (tier in t["tiers"] and bool(_AI_NAME.search(j["company"])))
            elif t.get("startups"):
                hit = j["company_key"] in self.startup_keys
            elif tid in self.members:
                hit = self.members[tid].firm(j) is not None
            elif t.get("tiers"):
                hit = _co_has(t, j)
            else:
                hit = False
            if hit:
                out.append(tid)
        return out


_startups_cache: dict = {}


def _startup_keys(pid: int) -> set[str]:
    """The companies in the Startups track: the ones the Startups feed lists in Canada (cached for 10 minutes: the
    feed reads the YC directory and People)."""
    stamp = store._cache["version"]
    hit = _startups_cache.get(pid)
    if hit and hit[0] == stamp and hit[1] > time.time() - 600:
        return hit[2]
    keys = {r["key"] for r in startups.feed(pid, "canada", limit=100000)["startups"]}
    _startups_cache[pid] = (stamp, time.time(), keys)
    return keys


_sizes_cache: list = [None, {}]


def _feed_sizes() -> dict[str, int]:
    """Postings per company across the whole feed, counted once per change of the feed (not on every page)."""
    stamp = store._cache["version"]
    if _sizes_cache[0] != stamp:
        sizes: dict[str, int] = {}
        for j in store.collapsed_jobs():
            sizes[j["company_key"]] = sizes.get(j["company_key"], 0) + 1
        _sizes_cache[:] = [stamp, sizes]
    return _sizes_cache[1]


_index_lock = threading.Lock()
_index_cache: dict = {}
_index_secs: dict[int, float] = {}
_index_building: set[int] = set()


def _stamp(pid: int) -> tuple:
    return (store._cache["version"], niches.REVISION, track_discovery.REVISION, len(_custom(pid)))


def _build_index(pid: int, stamp: tuple) -> dict[str, set[str]]:
    t0 = time.time()
    tracks = [*all_tracks(pid), *[t for t in _found() if t["status"] == "faded" and not t.get("folded_into")]]
    lenses = _Lenses(pid, tracks)
    out: dict[str, set[str]] = {t["id"]: set() for t in tracks}
    for j in store.collapsed_jobs():
        for tid in lenses.tracks_of(j):
            out[tid].add(j["id"])
    with _index_lock:
        _index_cache[pid] = (stamp, out)
        _index_secs[pid] = time.time() - t0
    return out


def _rebuild_behind(pid: int, stamp: tuple) -> None:
    try:
        _build_index(pid, stamp)
    except Exception as e:
        log.warning("refreshing tracks failed: %s", e)
    finally:
        with _index_lock:
            _index_building.discard(pid)


def index(pid: int) -> dict[str, set[str]]:
    """track id -> the ids of its postings (each collapsed posting by its own id). A big feed takes seconds to sort and
    a sync changes it every 15 minutes: when only the postings changed, the last answer is served at once and the new
    one is worked out behind it. A track someone just made or edited is always worked out first."""
    stamp = _stamp(pid)
    with _index_lock:
        hit = _index_cache.get(pid)
        if hit and hit[0] == stamp:
            return hit[1]
        if hit and hit[0][1:] == stamp[1:] and _index_secs.get(pid, 0) > 1:
            if pid not in _index_building:
                _index_building.add(pid)
                threading.Thread(target=_rebuild_behind, args=(pid, stamp), daemon=True, name="tracks-refresh").start()
            return hit[1]
    return _build_index(pid, stamp)


def ids(pid: int, tid: str) -> set[str] | None:
    """The postings in a track, or None for a track that doesn't exist (for this person)."""
    t = get(pid, tid)
    if not t:
        return None
    return index(pid).get(t["id"], set())


def members(pid: int, tid: str) -> list[dict]:
    """The scored postings in a track (every one, including the ones this person can't apply to)."""
    got = ids(pid, tid) or set()
    return [j for j in store.scored_jobs(pid) if j["id"] in got]


def counts(pid: int, tid: str) -> dict:
    """What the header says, counted by the very query each count opens, so the numbers always match the lists."""
    return {"open": store.list_jobs(pid, track=tid, show_ineligible=True, limit=0)["total"],
            "open_to_you": store.list_jobs(pid, track=tid, limit=0)["total"],
            "new_week": store.list_jobs(pid, track=tid, since_days=7, limit=0)["total"]}


# --- pins ----------------------------------------------------------------------------------------------

def _edited(pid: int) -> bool:
    return db.meta_get(f"track_pins_set:{pid}") == "1"


def defaults(pid: int) -> list[str]:
    """2-4 tracks from what someone told us and does: their target roles, the niches they follow, what they aim for,
    and Startups in Canada."""
    prof = store.get_profile(pid)
    if not prof:
        return []
    out: list[str] = []
    targets = prof["prefs"].get("categories") or prof["profile"].get("target_categories") or []
    for c in targets:
        tid = CATEGORY_TRACKS.get(c)
        if tid and tid not in out and len([x for x in out if x.startswith("role:")]) < 2:
            out.append(tid)
    for nid in niches.followed(pid):
        tid = niche_track(nid)
        if tid not in out and get(pid, tid):
            out.append(tid)
    aim = AIM_TRACKS.get(prof["prefs"].get("aim") or "")
    if aim and aim not in out:
        out.append(aim)
    if "Quant" in targets and "co:quant-firms" not in out:
        out.append("co:quant-firms")
    for tid in ("co:startups", "role:software"):
        if len(out) < 2 and tid not in out:
            out.append(tid)
    if "co:startups" not in out and len(out) < MAX_DEFAULTS:
        out.append("co:startups")
    return out[:MAX_DEFAULTS]


def pins(pid: int) -> list[str]:
    """The tracks in this person's Jobs bar, in order. Until they change them, they follow `defaults()`."""
    if not _edited(pid):
        return defaults(pid)
    out = []
    for r in db.rows("SELECT track_id FROM track_pins WHERE profile_id=? ORDER BY position", (pid,)):
        t = get(pid, r["track_id"])
        if t and t["id"] not in out:  # a found track folded into a shipped one shows as that one
            out.append(t["id"])
    return out


def set_pins(pid: int, tids: list[str]) -> dict:
    clean: list[str] = []
    for tid in tids:
        t = get(pid, str(tid))
        if not t:
            return {"error": f"no such track: {tid}"}
        if t["id"] not in clean:
            clean.append(t["id"])
    if len(clean) > MAX_PINS:
        return {"error": f"Pin up to {MAX_PINS} tracks. Unpin one first."}
    with db.tx() as c:
        c.execute("DELETE FROM track_pins WHERE profile_id=?", (pid,))
        for i, tid in enumerate(clean):
            c.execute("INSERT INTO track_pins (profile_id, track_id, position, created_at) VALUES (?,?,?,?)",
                      (pid, tid, i, db.now()))
    db.meta_set(f"track_pins_set:{pid}", "1")
    return {"ok": True, "pins": clean}


def pin(pid: int, tid: str, on: bool = True) -> dict:
    cur = pins(pid)
    t = get(pid, tid)
    if not t:
        return {"error": f"no such track: {tid}"}
    if on and t["id"] not in cur:
        cur.append(t["id"])
    elif not on:
        cur = [x for x in cur if x != t["id"]]
    return set_pins(pid, cur) | {"track": t["name"], "pinned": on}


# --- follows -------------------------------------------------------------------------------------------

def follows(pid: int) -> set[str]:
    out = {r["track_id"] for r in db.rows("SELECT track_id FROM track_follows WHERE profile_id=?", (pid,))}
    return out | {niche_track(nid) for nid in niches.followed(pid)}


def follow(pid: int, tid: str, on: bool = True) -> dict:
    """Follow a track: its new roles always alert. A niche track also adds its firms' boards to the sync."""
    t = get(pid, tid)
    if not t:
        return {"error": f"no such track: {tid}"}
    if t.get("niche"):
        r = niches.follow(pid, t["niche"], on)
        return {**r, "track": t["name"], "following": on}
    with db.tx() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO track_follows (profile_id, track_id, created_at) VALUES (?,?,?)",
                      (pid, t["id"], db.now()))
        else:
            c.execute("DELETE FROM track_follows WHERE profile_id=? AND track_id=?", (pid, t["id"]))
    return {"ok": True, "track": t["name"], "following": on, "boards_added": 0}


class Followed:
    """The postings in any track someone follows (sync alerts on their new ones, like starred companies)."""

    def __init__(self, pid: int, tids: set[str]):
        self.pid = pid
        self.idx = index(pid)
        self.tids = [t for t in tids if t in self.idx]
        self.names = {t: (get(pid, t) or {}).get("name", t) for t in self.tids}

    def firm(self, j: dict) -> dict | None:
        """{'track': name} for the first followed track this posting is in, or None."""
        for t in self.tids:
            if j["id"] in self.idx[t]:
                return {"track": self.names[t], "name": self.names[t]}
        return None


def follow_matcher(pid: int) -> Followed | None:
    tids = follows(pid)
    return Followed(pid, tids) if tids else None


# --- the page ----------------------------------------------------------------------------------------

def header(pid: int, tid: str) -> dict | None:
    t = get(pid, tid)
    if not t:
        return None
    n = niches.get(pid, t["niche"]) if t.get("niche") else None
    return {
        **public(t),
        "id": t["id"],
        "asked": tid,  # a found track folded into a shipped one opens that one
        **counts(pid, t["id"]),
        "pinned": t["id"] in pins(pid),
        "following": t["id"] in follows(pid),
        "can": {"find_more": bool(n), "add_firm": bool(n), "reminders": bool(n),
                "delete": t.get("source") == "yours"},
        "segments": [{"id": s["id"], "name": s["name"]} for s in (n or {}).get("segments", [])],
    }


def firms_of(pid: int, tid: str) -> niches.Members | None:
    """A niche track's firms (which posting belongs to which firm), or None."""
    t = get(pid, tid)
    n = niches.get(pid, t["niche"]) if t and t.get("niche") and not t.get("startups") else None
    return niches.Members([n]) if n else None


COMPANY_PAGE = 25
CHIPS = ("hiring", "open", "small", "canada")


def _company_rows(pid: int, t: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """(rows, segments, to review, taken off the list) for a track's Companies section."""
    from . import companies, contacts
    if t.get("startups"):
        d = startups.feed(pid, "canada", limit=100000)
        rows = [{"key": r["key"], "name": r["name"], "note": r["one_liner"], "where": r["where"],
                 "tags": [x for x in (f"YC {r['yc']}" if r["yc"] else "", "Hiring fast" if r["pace"] == "fast" else "")
                          if x],
                 "open": r["open"], "open_to_you": r["open_to_you"], "new": r["new30"], "new_label": "in 30 days",
                 "people": r["people"], "link": r["website"] or r["yc_url"], "best": r["best"], "small": True,
                 "canada": True, "segment": r["area"], "facet": f"company:{r['key']}", "opens": ""}
                for r in d["startups"]]
        segs = [{"id": k, "name": v} for k, v in startups.AREAS.items() if k in ("waterloo", "toronto", "canada")]
        return rows, segs, [], []
    n = niches.get(pid, t["niche"]) if t.get("niche") else None
    if n:
        d = niches.detail(pid, t["niche"])
        firms = [f for s in d["segments"] for f in s["firms"]]
        rows = [{"key": f["key"], "name": f["name"], "note": f.get("note") or "", "where": f.get("where") or "",
                 "tags": [{"ai": "found by AI", "postings": "from your postings", "you": "added by you"}.get(
                     f.get("origin"), "")] if f.get("origin") not in (None, "catalog") else [],
                 "open": f["open_roles"] + f["closed_to_you"], "open_to_you": f["open_roles"], "new": f["new_week"],
                 "new_label": "this week", "people": f["people"], "link": f["link"], "best": f["best"],
                 "small": bool(f.get("small")), "canada": bool(f.get("canada")) or f["segment"] == "canada",
                 "segment": f["segment"], "facet": f"firm:{f['key']}", "opens": f.get("opens") or "",
                 "hidden": bool(f.get("hidden"))} for f in firms]
        segs = [{"id": s["id"], "name": s["name"], "blurb": s.get("blurb") or ""} for s in d["segments"]]
        review = [{"key": r["key"], "name": r["name"], "why": r["why"] or "", "verified": r["verified"] or "",
                   "segment": (r["info"] or {}).get("segment_name") or "", "link": (r["info"] or {}).get("careers") or ""}
                  for r in d["suggestions"]]
        return [r for r in rows if not r["hidden"]], segs, review, [r for r in rows if r["hidden"]]
    # any other track: the companies behind its postings
    by: dict[str, list[dict]] = {}
    for j in members(pid, t["id"]):
        by.setdefault(j["company_key"], []).append(j)
    feed_size = _feed_sizes()
    people = contacts.at_companies(pid)
    week = time.time() - 7 * DAY
    rows = []
    for ck, js in by.items():
        ok = sorted([j for j in js if j["eligibility"] != "no"], key=lambda j: -j["score"])
        tier = js[0]["tier"] or "other"
        rows.append({"key": ck, "name": js[0]["company"], "note": "", "where": "", "tags": [],
                     "open": len(js), "open_to_you": len(ok),
                     "new": sum(1 for j in ok if j["fresh"] and j["first_seen"] >= week), "new_label": "this week",
                     "people": people.get(ck, 0), "link": "",
                     "best": {"id": ok[0]["id"], "title": ok[0]["title"], "score": ok[0]["score"], "url": ok[0]["url"]}
                     if ok else None,
                     # lesser-known: few postings anywhere in the feed and not a household-name kind of company
                     "small": feed_size.get(ck, 0) <= 3 and tier not in ("bigtech", "finance"),
                     "canada": any("Canada" in (j.get("regions") or []) for j in js),
                     "segment": tier, "facet": f"company:{ck}", "opens": ""})
    segs = [{"id": k, "name": v} for k, v in companies.TIERS.items() if any(r["segment"] == k for r in rows)]
    return rows, segs, [], []


def companies(pid: int, tid: str, segment: str = "", q: str = "", chip: str = "", offset: int = 0,
              limit: int = COMPANY_PAGE) -> dict | None:
    """A track's Companies: hiring now first, 25 to a page, with a segment picker, search and chips."""
    t = get(pid, tid)
    if not t:
        return None
    rows, segs, review, hidden = _company_rows(pid, t)
    total = len(rows)
    ql = (q or "").strip().lower()
    keep = [r for r in rows if (not segment or r["segment"] == segment)
            and (not ql or ql in f"{r['name']} {r['where']} {r['note']}".lower())
            and (chip != "hiring" or r["open"] > 0) and (chip != "open" or r["open_to_you"] > 0)
            and (chip != "small" or r["small"]) and (chip != "canada" or r["canada"])]
    keep.sort(key=lambda r: (-(r["open_to_you"] > 0), -(r["open"] > 0), -r["open_to_you"], -r["new"],
                             r["name"].lower()))
    seg_n = {s["id"]: sum(1 for r in rows if r["segment"] == s["id"]) for s in segs}
    return {"track": t["id"], "total": total, "shown": len(keep), "rows": keep[offset:offset + limit],
            "offset": offset, "limit": limit, "segments": [{**s, "n": seg_n[s["id"]]} for s in segs if seg_n[s["id"]]],
            "hiring": sum(1 for r in rows if r["open"] > 0), "to_review": review, "hidden": hidden,
            "chips": {c: sum(1 for r in rows if (c == "hiring" and r["open"] > 0) or (c == "open" and r["open_to_you"] > 0)
                             or (c == "small" and r["small"]) or (c == "canada" and r["canada"])) for c in CHIPS}}


def browse(pid: int) -> dict:
    """＋ Tracks: every track, grouped, with how many postings are open to this person in each."""
    idx = index(pid)
    ok = {j["id"] for j in store.scored_jobs(pid) if j["eligibility"] != "no"}
    week = {j["id"] for j in store.scored_jobs(pid) if j["eligibility"] != "no" and j["fresh"]
            and j["first_seen"] >= time.time() - 7 * DAY}
    mine, fol = pins(pid), follows(pid)

    def card(t: dict) -> dict:
        got = idx.get(t["id"], set())
        return {**public(t), "open_to_you": len(got & ok), "new_week": len(got & week), "pinned": t["id"] in mine,
                "following": t["id"] in fol, **({"stats": t["stats"]} if t.get("stats") else {})}

    tracks = all_tracks(pid)
    by_id = {t["id"]: t for t in tracks}
    found = [t for t in tracks if t["kind"] == "auto"]
    suggested = [tid for tid in defaults(pid) if tid not in mine and tid in by_id]
    return {
        "pins": [card(by_id[tid]) for tid in mine if tid in by_id],
        "suggested": [card(by_id[tid]) for tid in suggested],
        "rising": [card(t) for t in found if t.get("trend") in ("new", "rising")],
        "roles": [card(t) for t in tracks if t["kind"] == "role"],
        "employers": [card(t) for t in tracks if t["kind"] == "co" and t["source"] == "shipped"],
        "yours": [card(t) for t in tracks if t["source"] == "yours"],
        "more_roles": [card(t) for t in found if t.get("trend") not in ("new", "rising")],
        "starters": niches.starters(),
        "max_pins": MAX_PINS,
    }


def bar(pid: int) -> list[dict]:
    """The pinned tracks for the Jobs bar: id, name and icon."""
    out = []
    for tid in pins(pid):
        t = get(pid, tid)
        if t:
            out.append({"id": t["id"], "name": t["name"], "icon": t.get("icon") or ""})
    return out


def create(pid: int, name: str) -> dict:
    """Start your own employer track (a niche): it's pinned straight away."""
    r = niches.create(pid, name)
    if r.get("error"):
        return r
    tid = f"co:{r['id']}"
    cur = pins(pid)
    if len(cur) < MAX_PINS:
        set_pins(pid, [*cur, tid])
    return {"ok": True, "id": tid, "name": r["name"], "niche": r["id"]}


def delete(pid: int, tid: str) -> dict:
    t = get(pid, tid)
    if not t or t.get("source") != "yours":
        return {"error": "Only a track you started can be deleted. You can unpin this one."}
    r = niches.delete(pid, t["niche"])
    if r.get("ok") and _edited(pid):
        set_pins(pid, [x for x in pins(pid) if x != tid])
    return r


def after_pull() -> dict:
    """After the postings change: find new kinds of role once a day, then warm each person's track index."""
    out = {}
    try:
        out = track_discovery.run_if_due()
    except Exception:
        log.exception("track discovery failed")
    for p in store.list_profiles():
        try:
            index(p["id"])
        except Exception:
            log.exception("track index failed for %s", p["id"])
    return out


def categories_of(tid: str) -> list[str]:
    """The role categories a role track stands for (Resume Studio's market picker reads these)."""
    t = _shipped().get(tid) or {}
    return [c for c in t.get("categories") or [] if c in CATEGORIES]
