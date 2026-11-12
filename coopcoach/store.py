"""Read/write helpers for jobs, profiles, favourites and applications, plus ranked job listing."""

import gzip
import hashlib
import json
import logging
import re
import threading
import time

from . import companies, config, db, eligibility, matching
from .config import DATA_DIR
from .textutil import (
    DEGREE_FLAGS,
    NARROWED_SKILLS,
    NONTECH_RE,
    classify_category,
    classify_regions,
    clean_url,
    company_key,
    degree_flags,
    detect_flags,
    detect_skills,
    extract_req_id,
    normalize_terms,
    refine_category,
    title_key,
)

log = logging.getLogger(__name__)
SNAPSHOT_VERSION = 2  # older snapshots may have missed delayed feed imports
SOURCE_RANK = {"greenhouse": 3, "lever": 3, "ashby": 3, "workday": 3, "successfactors": 3, "jibe": 3, "yc": 2,
               "simplify": 2, "linkedin": 1, "indeed": 1}
SOURCE_LABEL = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
                "successfactors": "SuccessFactors", "jibe": "company site", "yc": "Y Combinator",
                "simplify": "SimplifyJobs", "linkedin": "LinkedIn", "indeed": "Indeed"}
APP_STATUSES = ["saved", "applied", "oa", "interviewing", "offer", "rejected"]


def job_id(url: str) -> str:
    return hashlib.sha1(clean_url(url).encode()).hexdigest()[:16]


def _rank(source: str) -> int:
    return SOURCE_RANK.get((source or "").split(":")[0], 0)


# --- companies watchlist ------------------------------------------------------------

def seed_companies() -> None:
    """Add any boards from the shipped lists that this database doesn't have yet. Boards a person removed stay
    removed (they're soft-deleted, so INSERT OR IGNORE skips them)."""
    version = config.seed_version()
    if db.meta_get("seed_version") == version and db.row("SELECT 1 FROM companies LIMIT 1"):
        return
    seed = config.seed_companies()
    rows = []
    for ats in ("greenhouse", "lever", "ashby", "successfactors", "jibe", "yc"):
        rows += [(ats, e["slug"], e["name"], e.get("tier"), "curated") for e in seed.get(ats) or []]
    rows += [("workday", e["url"], e["name"], e.get("tier"), "curated") for e in seed.get("workday") or []]
    rows = [(*r, 0) for r in rows]
    found = config.PKG / "boards_discovered.yaml"
    if found.exists():
        for e in (config.read_yaml(found.read_text(encoding="utf-8")) or {}).get("boards", []):
            rows.append((e["ats"], e["slug"], e["name"], e.get("tier"), "discovered", e.get("interns_seen", 0)))
    now = db.now()
    params = []
    for i, (ats, slug, name, tier, origin, interns) in enumerate(rows):
        # boards with internships open get checked on the next sync; quiet ones are spread across the day
        spread = now - (i * 977) % 86400 if origin == "discovered" and not interns else None
        params.append((ats, slug, name, tier, origin, spread, interns or None))
    # a batch at a time: this runs behind the page, and thousands of boards in one go would make every save wait for them
    for k in range(0, len(params), 500):
        with db.tx() as c:
            c.executemany("INSERT OR IGNORE INTO companies (ats, slug, name, tier, origin, last_checked, last_count)"
                          " VALUES (?,?,?,?,?,?,?)", params[k:k + 500])
    db.meta_set("seed_version", version)


def default_event_feeds() -> list[tuple[str, str, str, str]]:
    """(url, label, group, region) for every source everyone gets."""
    import datetime

    from . import events
    today = datetime.date.today()
    season = today.year + 1 if today.month >= 7 else today.year  # MLH seasons run Aug-Jul ("2027" = 2026-27)
    meetup = "https://www.meetup.com/find/?location=ca--on--{}&source=EVENTS&categoryId=546"  # 546 = technology
    feeds = [
        ("https://lu.ma/toronto", "Luma · Toronto", "Cities", "Toronto / GTA"),
        ("https://lu.ma/sf", "Luma · San Francisco", "Cities", "SF Bay Area"),
        ("https://lu.ma/nyc", "Luma · New York", "Cities", "New York"),
        (meetup.format("Waterloo"), "Meetup · Waterloo tech", "Cities", "Waterloo Region"),
        (meetup.format("Toronto"), "Meetup · Toronto tech", "Cities", "Toronto / GTA"),
        ("https://www.eventbrite.ca/d/canada--waterloo/science-and-tech--events/", "Eventbrite · Waterloo tech", "Cities", "Waterloo Region"),
        ("https://www.eventbrite.ca/d/canada--toronto/science-and-tech--events/", "Eventbrite · Toronto tech", "Cities", "Toronto / GTA"),
        ("https://devpost.com/hackathons", "Devpost hackathons", "Hackathons", ""),
        (f"https://mlh.io/seasons/{season}/events", "MLH hackathons", "Hackathons", ""),
        ("https://uwaterloo.ca/events/events/ical", "UWaterloo events", "Canadian universities", "Waterloo Region"),
    ]
    return feeds + [(s["url"], s["name"], s["group"], s.get("region") or "") for s in events.campus_sources()]


def seed_event_feeds() -> None:
    """Add each default feed once (removing one keeps it removed; new defaults still arrive), and keep the group and
    region of the defaults current."""
    seeded = set(db.loads(db.meta_get("event_feeds_seeded"), []) or [])
    if not seeded and db.row("SELECT 1 FROM event_feeds LIMIT 1"):
        seeded = {"https://lu.ma/toronto"}  # installs from before this list existed already have it (or removed it)
    feeds = default_event_feeds()
    new = [(url, label, db.now()) for url, label, _, _ in feeds if url not in seeded]
    with db.tx() as c:
        if new:
            c.executemany("INSERT OR IGNORE INTO event_feeds (url, label, career_only, created_at) VALUES (?,?,1,?)", new)
        c.executemany("UPDATE event_feeds SET grp=?, region=COALESCE(NULLIF(?, ''), region) WHERE url=? AND"
                      " (grp IS NULL OR grp<>?)", [(grp, region, url, grp) for url, _, grp, region in feeds])
    db.meta_set("event_feeds_seeded", db.dumps(sorted(seeded | {u for u, _, _, _ in feeds})))


# --- jobs ------------------------------------------------------------------------------

def normalize(raw: dict) -> dict | None:
    url = (raw.get("url") or "").strip()
    title = (raw.get("title") or "").strip()
    company = (raw.get("company") or "").strip()
    if not url.startswith("http") or not title or not company:
        return None
    desc = raw.get("description") or ""
    tier, yc_batch = companies.classify(company, raw.get("tier_hint"))
    locations = [l.strip() for l in raw.get("locations") or [] if l and l.strip()]
    return {
        "id": job_id(url),
        "source": raw["source"],
        "company": company,
        "company_key": company_key(company),
        "title": title,
        "url": clean_url(url),
        "locations": locations,
        "regions": classify_regions(locations),
        "terms": normalize_terms(raw.get("terms") or []),
        "category": raw.get("category") or classify_category(title, desc),
        "tier": tier,
        "yc_batch": yc_batch,
        "skills": detect_skills(f"{title}\n{desc}"),
        "flags": detect_flags(title, desc, raw.get("sponsorship") or ""),
        "description": desc[:20000],
        "posted_at": raw.get("posted_at"),
        "req_id": extract_req_id(url, desc, raw.get("req_id") or ""),
        "requirements": eligibility.extract(title, desc, raw.get("questions")),
        "form_checked": int(raw.get("questions") is not None),
    }


# bump when normalize() reads postings differently (regions, terms, categories...): the next sync re-reads them all
NORMALIZE_VERSION = "1"


def fingerprint(raw: dict) -> str:
    """What a board sent for one posting, plus how it's read and the company's tier: when this matches what's stored,
    the posting hasn't changed and needn't be read (skills, flags, who it's open to: the slow part of a sync) or
    written again. New skill, flag or eligibility patterns don't change it: refresh_skills, refresh_flags and
    refresh_requirements re-read the stored postings once instead."""
    tier = companies.classify((raw.get("company") or "").strip(), raw.get("tier_hint"))
    keys = ("source", "company", "title", "url", "locations", "terms", "category", "description", "posted_at",
            "req_id", "sponsorship", "questions", "tier_hint")
    # Workday only says "Posted 3 Days Ago": the date worked out from it moves with the clock, so its own words are
    # compared instead (otherwise every Workday posting looks changed on every read)
    vals = [raw.get("posted_text") if k == "posted_at" and raw.get("posted_text") else raw.get(k) for k in keys]
    return hashlib.sha1(db.dumps([NORMALIZE_VERSION, tier, vals]).encode()).hexdigest()[:20]


def stored_fingerprints() -> dict[str, str]:
    """Each open posting's fingerprint from the last sync that wrote it."""
    return {r["id"]: r["fp"] for r in db.rows("SELECT id, fp FROM jobs WHERE active=1 AND fp IS NOT NULL")}


def prepare(raw: dict, stored: dict[str, str]) -> dict | None:
    """normalize(), skipped for a posting the board sends exactly as last time: then only its id, source and
    fingerprint come back (marked unchanged), and saving it just records that it's still open."""
    url = (raw.get("url") or "").strip()
    if not url.startswith("http") or not (raw.get("title") or "").strip() or not (raw.get("company") or "").strip():
        return None
    fp = fingerprint(raw)
    jid = job_id(url)
    if stored.get(jid) == fp:
        return {"id": jid, "source": raw["source"], "fp": fp, "unchanged": True}
    j = normalize(raw)
    j["fp"] = fp
    return j


def upsert_jobs(jobs: list[dict], now: int) -> list[str]:
    """Insert/update postings; returns ids that are brand new."""
    if not jobs:
        return []
    existing, fps = {}, {}
    ids = [j["id"] for j in jobs]
    for i in range(0, len(ids), 800):
        chunk = ids[i:i + 800]
        for r in db.rows(f"SELECT id, source, fp FROM jobs WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            existing[r["id"]], fps[r["id"]] = r["source"], r["fp"]
    new_ids, params, unchanged = [], [], []
    t = db.now()  # changed_at: the moment it's written (a sync's own start time would hide rows saved mid-sync)
    for j in jobs:
        prev_source = existing.get(j["id"])
        # unchanged since last time, or stored from a better source (the company's own board) than this one (an
        # aggregate list saved earlier in the sync): only "still open" changes
        if prev_source is not None and (j.get("unchanged") or _rank(j["source"]) < _rank(prev_source)):
            unchanged.append(j["id"])
            continue
        if prev_source is None:
            new_ids.append(j["id"])
            existing[j["id"]] = j["source"]
        source = j["source"] if prev_source is None or _rank(j["source"]) >= _rank(prev_source) else prev_source
        row = (j["id"], j["source"], j["company"], j["company_key"], j["title"], j["url"], db.dumps(j["locations"]),
               db.dumps(j["regions"]), db.dumps(j["terms"]), j["category"], j["tier"], j["yc_batch"],
               db.dumps(j["skills"]), db.dumps(j["flags"]), j["description"], j["posted_at"], j.get("req_id"),
               db.dumps(j.get("requirements") or []), j.get("form_checked", 0), source)
        fp = j.get("fp") or hashlib.sha1(db.dumps(row).encode()).hexdigest()[:20]
        if prev_source is not None and fps.get(j["id"]) == fp:
            unchanged.append(j["id"])  # the board sent exactly what it sent last time: only "still open" changes
            continue
        params.append((*row[:16], now, now, *row[16:19], fp, t, source))
    with db.tx() as c:  # one batch: a single round trip per chunk on Supabase instead of one per posting
        for i in range(0, len(unchanged), 800):  # most postings, most syncs: no need to send their text again
            chunk = unchanged[i:i + 800]
            c.execute(f"UPDATE jobs SET last_seen=?, changed_at=CASE WHEN active=0 THEN ? ELSE changed_at END,"
                      f" active=1 WHERE id IN ({','.join('?' * len(chunk))})", [now, t, *chunk])
        if params:
            c.executemany(
                """INSERT INTO jobs (id, source, company, company_key, title, url, locations, regions, terms, category,
                           tier, yc_batch, skills, flags, description, posted_at, first_seen, last_seen, active, req_id,
                           requirements, form_checked, fp, changed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                           source=?, title=excluded.title, locations=excluded.locations, regions=excluded.regions,
                           terms=CASE WHEN excluded.terms='[]' THEN jobs.terms ELSE excluded.terms END,
                           category=COALESCE(excluded.category, jobs.category), tier=excluded.tier,
                           yc_batch=excluded.yc_batch,
                           skills=CASE WHEN excluded.description='' THEN jobs.skills ELSE excluded.skills END,
                           flags=CASE WHEN excluded.description='' AND jobs.description != '' THEN jobs.flags
                                      ELSE excluded.flags END,
                           description=CASE WHEN excluded.description='' THEN jobs.description
                                            ELSE excluded.description END,
                           posted_at=COALESCE(MIN(jobs.posted_at, excluded.posted_at), jobs.posted_at,
                                              excluded.posted_at),
                           last_seen=excluded.last_seen, active=1, req_id=COALESCE(excluded.req_id, jobs.req_id),
                           requirements=CASE WHEN jobs.form_checked=1 AND excluded.form_checked=0 THEN jobs.requirements
                                             WHEN excluded.description='' THEN jobs.requirements
                                             ELSE excluded.requirements END,
                           form_checked=MAX(jobs.form_checked, excluded.form_checked), fp=excluded.fp,
                           changed_at=excluded.changed_at""",
                params)
    invalidate_cache()
    return new_ids


def deactivate_missing(source_prefixes: list[str], seen_ids: set[str], run_started: int) -> int:
    """Mark postings from sources that synced successfully but no longer list them as closed."""
    prefixes = set(source_prefixes)

    def checked(source: str) -> bool:  # "greenhouse:stripe" belongs to that board; "simplify" to its feed
        parts = source.split(":")
        return any(":".join(parts[:k]) in prefixes for k in range(1, len(parts) + 1))

    t = db.now()
    with db.tx() as c:  # one query for every board, instead of one per board (thousands of round trips on Supabase)
        stale = [r["id"] for r in c.execute("SELECT id, source FROM jobs WHERE active=1 AND last_seen < ?",
                                            (run_started,)).fetchall()
                 if checked(r["source"]) and r["id"] not in seen_ids] if prefixes else []
        for i in range(0, len(stale), 800):
            chunk = stale[i:i + 800]
            c.execute(f"UPDATE jobs SET active=0, changed_at=? WHERE id IN ({','.join('?' * len(chunk))})", [t, *chunk])
        closed = len(stale)
        # search-based sources don't tell us when something closes; expire them after 3 weeks unseen
        c.execute("UPDATE jobs SET active=0, changed_at=? WHERE active=1 AND (source LIKE 'linkedin%' OR source LIKE"
                  " 'indeed%') AND last_seen < ?", (t, run_started - 21 * 86400))
    invalidate_cache()
    if prefixes:
        closed += len(close_orphan_copies(run_started))
    return closed


_BOARD_POST = (  # an employer's own board, and the posting on it, read from a link
    (re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)/([0-9a-f-]{36})", re.I), "ashby"),
    (re.compile(r"jobs\.lever\.co/([\w.-]+)/([0-9a-f-]{36})", re.I), "lever"),
    (re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w.-]+)/jobs/(\d+)", re.I), "greenhouse"),
)


def _board_post(url: str) -> tuple[str, str, str] | None:
    for rx, ats in _BOARD_POST:
        m = rx.search(url or "")
        if m:
            return ats, m.group(1).lower(), m.group(2).lower()
    return None


def close_orphan_copies(run_started: int, dry_run: bool = False) -> list[dict]:
    """Copies of a posting from lists and search sites (LinkedIn, Indeed, Simplify) close when the employer's own
    board no longer lists it: matched by the board link the copy carries (the board was read after the copy was last
    seen, without it), or else by company and title (closed on its board in the last two weeks, no open twin there).
    Those sites keep listing roles long after the employer closes them (Cohere's Winter 2027 intern, still "open" on
    LinkedIn after Ashby took it down)."""
    boards = db.rows("SELECT company_key, title, url, active, last_seen FROM jobs WHERE source LIKE '%:%'")
    read_at: dict[tuple, int] = {}
    live_posts, live_titles, dead_titles = set(), set(), set()
    recent = min(run_started, db.now()) - 14 * 86400
    for b in boards:
        bp = _board_post(b["url"])
        if bp:
            read_at[bp[:2]] = max(read_at.get(bp[:2], 0), b["last_seen"] or 0)
        if b["active"]:
            if bp:
                live_posts.add(bp)
            live_titles.add((b["company_key"], title_key(b["title"])))
        elif (b["last_seen"] or 0) >= recent:
            dead_titles.add((b["company_key"], title_key(b["title"])))
    out = []
    for j in db.rows("SELECT id, company, company_key, title, url, source, last_seen FROM jobs WHERE active=1"
                     " AND source NOT LIKE '%:%'"):
        bp, key = _board_post(j["url"]), (j["company_key"], title_key(j["title"]))
        if bp and read_at.get(bp[:2], 0) > (j["last_seen"] or 0) and bp not in live_posts:
            out.append({**j, "why": "its board no longer lists it"})
        elif not bp and key in dead_titles and key not in live_titles:
            out.append({**j, "why": "closed on the employer's board"})
    if out and not dry_run:
        t = db.now()
        ids = [j["id"] for j in out]
        with db.tx() as c:
            for i in range(0, len(ids), 800):
                chunk = ids[i:i + 800]
                c.execute(f"UPDATE jobs SET active=0, changed_at=? WHERE id IN ({','.join('?' * len(chunk))})", [t, *chunk])
        invalidate_cache()
    return out


def mark_opened(ids: set[str] | list[str]) -> None:
    """These postings are real new openings (not what a board already listed the first time it was read): apps that
    pull from the shared feed alert on them, like this sync does."""
    ids, t = list(ids), db.now()
    with db.tx() as c:
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            c.execute(f"UPDATE jobs SET opened_at=COALESCE(opened_at, ?), changed_at=? WHERE id IN"
                      f" ({','.join('?' * len(chunk))})", [t, t, *chunk])


# --- collapsed + cached job list ---------------------------------------------------

_cache_lock = threading.Lock()
_load_lock = threading.Lock()
_cache: dict = {"version": 0, "jobs": None}
_score_cache: dict = {}
_merged: dict = {}  # person -> their finished list (treat it as read-only: copy a posting to add to it)


def invalidate_cache() -> None:
    """Postings changed (a sync): reload them next time."""
    with _cache_lock:
        _cache["version"] += 1
        _cache["jobs"] = None
        _score_cache.clear()
        _merged.clear()


def refresh_scores() -> None:
    """Something about a person changed (tracker, AI scores): re-score from the postings already in memory."""
    with _cache_lock:
        _score_cache.clear()
        _merged.clear()


def patch_job(job_id: str, **fields) -> None:
    """One posting changed (its description was fetched): update it in memory instead of reloading every posting."""
    with _cache_lock:
        _cache["version"] += 1
        for j in _cache["jobs"] or []:
            if job_id in j["alt_ids"]:
                j.update({k: v for k, v in fields.items() if k in j})
        _score_cache.clear()
        _merged.clear()


def _decode(r: dict) -> dict:
    for k in ("locations", "regions", "terms", "skills", "flags", "requirements"):
        if k in r:
            r[k] = [] if r[k] in ("[]", None, "") else db.loads(r[k], []) or []  # (most are empty, and there are 20,000 of them)
    return r


# no descriptions here: they're fetched by id when needed, which keeps this load small
_COLS = ("id, source, company, company_key, title, url, locations, regions, terms, category, tier, yc_batch, skills,"
         " flags, '' AS description, posted_at, first_seen, requirements, opened_at")
_mirror: dict = {"rows": None, "at": 0}  # Supabase only: the active postings as downloaded, and as of when
MIRROR_OVERLAP = 600  # seconds asked for again each time: a write can land just after the moment it's stamped with


def _snapshot_file():
    return DATA_DIR / "cache" / "postings.json.gz"


def _read_snapshot() -> None:
    """The postings from the last run, off this computer's disk: no reason to download 14 MB on every start."""
    try:
        with gzip.open(_snapshot_file(), "rt", encoding="utf-8") as f:
            snap = json.load(f)
        if snap.get("version") == SNAPSHOT_VERSION:
            _mirror.update(rows=snap["rows"], at=snap["at"])
    except (OSError, ValueError, KeyError, EOFError):
        pass


def _write_snapshot() -> None:
    rows, at = _mirror["rows"], _mirror["at"]

    def run():
        try:
            f = _snapshot_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=3) as out:
                json.dump({"version": SNAPSHOT_VERSION, "at": at, "rows": rows}, out)
            tmp.replace(f)
        except OSError as e:
            log.info("couldn't save the postings snapshot: %s", e)
    threading.Thread(target=run, daemon=True, name="postings-snapshot").start()


def _active_rows() -> list[dict]:
    """The list fields of every active posting. From Supabase only what changed since last time is asked for (each
    posting's changed_at moves when anything about it does), and a run starts from the copy on disk. Feed rows keep
    the crawler's changed_at, so their local imported_at must also count: a delayed edit can be hours old."""
    if not db.is_postgres():
        return db.rows(f"SELECT {_COLS} FROM jobs WHERE active=1")
    if _mirror["rows"] is None:
        _read_snapshot()
    started = db.now()
    if _mirror["rows"] is None:
        _mirror.update(rows={r["id"]: dict(r) for r in db.rows(f"SELECT {_COLS} FROM jobs WHERE active=1")}, at=started)
        _write_snapshot()
    else:
        since = _mirror["at"] - MIRROR_OVERLAP
        changed = db.rows(f"SELECT {_COLS}, active FROM jobs WHERE changed_at >= ? OR imported_at >= ?", (since, since))
        rows = dict(_mirror["rows"])
        for r in changed:
            r = dict(r)
            if r.pop("active"):
                rows[r["id"]] = r
            else:
                rows.pop(r["id"], None)
        count = db.rows("SELECT COUNT(*) AS n FROM jobs WHERE active=1")[0]["n"]
        if count != len(rows):  # something moved without saying so: read them all once
            log.info("postings copy has %s, the database %s: reading them all", len(rows), count)
            rows = {r["id"]: dict(r) for r in db.rows(f"SELECT {_COLS} FROM jobs WHERE active=1")}
        had_changes = bool(changed) or count != len(_mirror["rows"])
        _mirror.update(rows=rows, at=started)
        if had_changes:
            _write_snapshot()
    return [dict(r) for r in _mirror["rows"].values()]  # copies: grouping below changes them


def collapsed_jobs() -> list[dict]:
    """Active postings with cross-source duplicates merged (same company + same title)."""
    with _cache_lock:
        if _cache["jobs"] is not None:
            return _cache["jobs"]
    with _load_lock:  # one load at a time: the page's requests at startup wait and share it (it's ~14 MB from Supabase)
        with _cache_lock:
            if _cache["jobs"] is not None:
                return _cache["jobs"]
            version = _cache["version"]
        return _load_jobs(version)


def jobs_ready() -> bool:
    """Are the postings in memory? If not, start loading them and don't wait (on a slow connection Supabase's ~14 MB
    can take minutes, and the page and its passcode box shouldn't)."""
    with _cache_lock:
        if _cache["jobs"] is not None:
            return True
    if not db.is_postgres():  # a local file: quick enough to just read
        return bool(collapsed_jobs()) or True
    if not _load_lock.locked():
        threading.Thread(target=collapsed_jobs, name="load-postings", daemon=True).start()
    return False


def _load_jobs(version: int) -> list[dict]:
    rows = _active_rows()
    groups: dict[tuple, dict] = {}
    for r in rows:
        r = _decode(r)
        # the title says these, whatever a source or an older version stored
        r["category"] = refine_category(r["title"], r["category"])
        r["flags"] = [f for f in r["flags"] if f not in DEGREE_FLAGS] + degree_flags(r["title"])
        key = (r["company_key"], title_key(r["title"]))
        g = groups.get(key)
        src = r["source"].split(":")[0]
        if g is None:
            r["sources"] = [src]
            r["alt_ids"] = [r["id"]]
            groups[key] = r
            continue
        g["alt_ids"].append(r["id"])
        if src not in g["sources"]:
            g["sources"].append(src)
        for loc in r["locations"]:
            if loc not in g["locations"]:
                g["locations"].append(loc)
        g["regions"] = sorted(set(g["regions"]) | set(r["regions"]))
        g["terms"] = g["terms"] or r["terms"]
        g["skills"] = sorted(set(g["skills"]) | set(r["skills"]))
        g["requirements"] = g["requirements"] + [x for x in r["requirements"] if x not in g["requirements"]]
        g["first_seen"] = min(g["first_seen"], r["first_seen"])
        if r["posted_at"] and (not g["posted_at"] or r["posted_at"] < g["posted_at"]):
            g["posted_at"] = r["posted_at"]
        if r.get("opened_at") and (not g.get("opened_at") or r["opened_at"] < g["opened_at"]):
            g["opened_at"] = r["opened_at"]
        if _rank(r["source"]) > _rank(g["source"]):  # prefer the company's own ATS link
            for k in ("id", "source", "url", "description", "category"):
                g[k] = r[k] or g[k]
    jobs = list(groups.values())
    baseline = int(db.meta_get("baseline_ts", 0) or 0)
    for j in jobs:
        j["fresh"] = j["first_seen"] > baseline  # appeared after the first sync, so we know when it opened
        j["intl_friendly"] = "US" in j["regions"] and companies.hires_international_interns(j["company"])
        j["core"] = eligibility.core_title(j["title"])
        for vague in ("Unknown", "Other"):
            if len(j["regions"]) > 1 and vague in j["regions"]:
                j["regions"].remove(vague)
    with _cache_lock:
        if _cache["version"] == version:
            _cache["jobs"] = jobs
    return jobs


# --- profiles ----------------------------------------------------------------------

def get_profile(pid: int) -> dict | None:
    p = db.row("SELECT * FROM profiles WHERE id=?", (pid,))
    if not p:
        return None
    p["profile"] = db.loads(p.pop("profile_json"), {})
    p["assessment"] = db.loads(p.pop("assessment_json"), {})
    p["prefs"] = matching.merged_prefs(db.loads(p.pop("prefs_json"), {}))
    # facts the person corrected in Settings → Profile win over whatever the parser read
    for k, v in (p["prefs"].get("profile_overrides") or {}).items():
        if k in PROFILE_OVERRIDABLE and v not in (None, ""):
            p["profile"][k] = v
    return p


PROFILE_OVERRIDABLE = ("year_of_study", "grad_date", "grad_year", "coop_program", "target_categories", "university",
                       "program")


def list_profiles() -> list[dict]:
    return [get_profile(r["id"]) for r in db.rows("SELECT id FROM profiles ORDER BY id")]


def favourites(pid: int) -> tuple[set[str], set[str]]:
    rows = db.rows("SELECT kind, key FROM favourites WHERE profile_id=?", (pid,))
    return ({r["key"] for r in rows if r["kind"] == "company"}, {r["key"] for r in rows if r["kind"] == "job"})


def applications(pid: int) -> dict[str, dict]:
    return {r["job_id"]: r for r in db.rows("SELECT * FROM applications WHERE profile_id=?", (pid,))}


def ai_scores(pid: int) -> dict[str, dict]:
    return {r["job_id"]: r for r in db.rows("SELECT * FROM ai_scores WHERE profile_id=?", (pid,))}


def scored_jobs(pid: int) -> list[dict]:
    """Every active (collapsed) posting with this profile's score, reasons, favourite + application state."""
    prof = get_profile(pid)
    if not prof:
        return []
    jobs = collapsed_jobs()
    # every page asks: the finished list is kept until the postings, their profile or what's merged in below change
    done_key = (prof["updated_at"], _cache["version"], db.table_version("favourites", "applications", "ai_scores"))
    hit = _merged.get(pid)
    if hit and hit[0] == done_key and hit[1] is jobs:
        return hit[2]
    fav_companies, fav_jobs = favourites(pid)
    apps = applications(pid)
    ai = ai_scores(pid)
    cache_key = (pid, prof["updated_at"], _cache["version"])  # base scores depend only on profile + jobs
    cached = _score_cache.get(cache_key)
    # the scores must be for this very list: a sync (another thread) can swap the postings between the two reads above
    if cached and cached[0] is jobs:
        base = cached[1]
    else:
        now = time.time()
        base = {}
        for j in jobs:
            s, reasons = matching.score(j, prof["profile"], prof["prefs"], now)
            elig = next((r for r in reasons if r["k"] == "elig"), {"v": "ok"})
            notes = [r["t"] for r in reasons if r["k"] == "note"]
            parts = next((r["v"] for r in reasons if r["k"] == "parts"), {})
            base[j["id"]] = (s, [r for r in reasons if r["k"] not in ("elig", "note", "parts")], elig["v"], notes, parts)
        _score_cache.clear()
        _score_cache[cache_key] = (jobs, base)
    out = []
    for j in jobs:
        h, reasons, verdict, notes, parts = base[j["id"]]
        a = next((ai[i] for i in j["alt_ids"] if i in ai), None)
        app = next((apps[i] for i in j["alt_ids"] if i in apps), None)
        blended = matching.blend(h, a["fit"] if a else None)
        out.append({
            **j,
            "heuristic": h,
            # a posting they can't apply to stays low however good the AI thinks the fit is
            "score": min(blended, h) if verdict == "no" else blended,
            "eligibility": verdict,
            "notes": notes,
            "parts": {**parts, **({"AI fit": a["fit"]} if a else {})},
            "reasons": reasons,
            "why": a["why"] if a else "",
            "tip": a["tip"] if a else "",
            "ai_scored": bool(a),
            "fav_company": j["company_key"] in fav_companies,
            "fav_job": any(i in fav_jobs for i in j["alt_ids"]),
            "favourite": j["company_key"] in fav_companies or any(i in fav_jobs for i in j["alt_ids"]),
            "app_status": app["status"] if app else None,
            "app_id": app["job_id"] if app else None,
        })
    _link_twins(out)
    _merged[pid] = (done_key, jobs, out)
    return out


def _link_twins(jobs: list[dict]) -> None:
    """Same internship posted for several places (Pinterest SWE Intern USA / Toronto / Dublin): point the weaker
    copies at the one that fits this person best."""
    groups: dict[tuple, list[dict]] = {}
    for j in jobs:
        if j.get("core"):
            groups.setdefault((j["company_key"], j["core"]), []).append(j)
    for g in groups.values():
        if len(g) < 2:
            continue
        best = max(g, key=lambda j: (j["eligibility"] != "no", j["score"]))
        if best["eligibility"] == "no":
            continue
        for j in g:
            if j is not best and j["score"] < best["score"]:
                j["twin"] = {"id": best["id"], "title": best["title"], "score": best["score"],
                             "where": ", ".join(best["locations"][:2]), "url": best["url"]}


def list_jobs(pid: int, q: str = "", tiers: list[str] | None = None, categories: list[str] | None = None,
              region: str = "", term: str = "", since_days: int = 0, min_score: int = 0, sort: str = "match",
              hide_applied: bool = False, limit: int = 60, offset: int = 0, show_ineligible: bool = False,
              starred: bool = False, niche: str = "", track: str = "", facet: str = "", population: str = "") -> dict:
    """The postings for Matches or a track page. `track` keeps one track's postings; `population="market"` keeps the
    ones its Market counts (see market.population); `facet` opens the postings behind one of its numbers
    ("skill:Python", "req:SQL", "city:Toronto", "term:Summer 2027", "remote", "pay", "company:<key>",
    "month:2026-07")."""
    jobs = scored_jobs(pid)
    now = time.time()
    ql = q.lower().strip()
    in_track = None
    if track:
        from . import tracks
        in_track = tracks.ids(pid, track)
        if in_track is None:  # deleted, or another person's: say so rather than show everything
            return {"total": 0, "ineligible": 0, "jobs": [], "pinned": [], "facets": {"tiers": {}, "categories": {}},
                    "new_24h": 0, "track_unknown": True}
    in_facet = None
    if facet or population:
        from . import market
        if population == "market":
            jobs = market.population(pid, [j for j in jobs if in_track is None or j["id"] in in_track])
        if facet:
            in_facet = market.facet_filter(facet, jobs, pid, track)
    in_niche = None
    if niche:  # an employer track's firm list (the chat's niche tools): only its roles at firms on the list
        from . import niches
        in_niche = niches.matcher(pid, niche)
        if in_niche is None:  # deleted, or another person's: say so rather than show everything
            return {"total": 0, "ineligible": 0, "jobs": [], "pinned": [], "facets": {"tiers": {}, "categories": {}},
                    "new_24h": 0, "niche_unknown": True}

    def newest(j):
        return j["posted_at"] or (j["first_seen"] if j["fresh"] else 0)

    def keep(j):
        if ql and ql not in j["title"].lower() and ql not in j["company"].lower() and \
                ql not in " ".join(j["locations"]).lower():
            return False
        if tiers and j["tier"] not in tiers:
            return False
        if categories and j["category"] not in categories:
            return False
        if region == "US-ok":  # US roles an international student can realistically get
            if "US" not in j["regions"] or {"us_citizen", "no_sponsor"} & set(j["flags"]) and \
                    "sponsor_yes" not in j["flags"]:
                return False
        elif region and region not in j["regions"]:
            return False
        if term and term not in j["terms"]:
            return False
        if since_days and newest(j) < now - since_days * 86400:
            return False
        if hide_applied and j["app_status"] in ("applied", "oa", "interviewing", "offer", "rejected"):
            return False
        if starred and not j["favourite"]:
            return False
        if in_niche is not None and not in_niche.firm(j):
            return False
        if in_track is not None and j["id"] not in in_track:
            return False
        if in_facet is not None and not in_facet(j):
            return False
        return True

    filtered = [j for j in jobs if keep(j)]
    ineligible = sum(1 for j in filtered if j["eligibility"] == "no" and not j["favourite"])
    if not show_ineligible:  # roles they can't apply to (US-residents only, PhD only...) stay out of the way
        filtered = [j for j in filtered if j["eligibility"] != "no" or j["favourite"]]
    facet_tiers: dict[str, int] = {}
    facet_cats: dict[str, int] = {}
    for j in filtered:
        facet_tiers[j["tier"]] = facet_tiers.get(j["tier"], 0) + 1
        facet_cats[j["category"]] = facet_cats.get(j["category"], 0) + 1
    above = [j for j in filtered if j["score"] >= min_score]

    if sort == "newest":
        above.sort(key=lambda j: (-newest(j), -j["score"]))
    elif sort == "company":
        above.sort(key=lambda j: (j["company"].lower(), -j["score"]))
    else:
        above.sort(key=lambda j: (-j["score"], -newest(j)))
    pinned = sorted([j for j in above if j["favourite"]], key=lambda j: (-j["score"], -newest(j)))[:24]
    return {
        "total": len(above),
        "ineligible": ineligible,
        "jobs": [_slim(j) for j in above[offset:offset + limit]],
        "pinned": [_slim(j) for j in pinned],
        "facets": {"tiers": facet_tiers, "categories": facet_cats},
        "new_24h": sum(1 for j in filtered if j["fresh"] and j["first_seen"] >= now - 86400),
    }


def _slim(j: dict) -> dict:
    out = {k: v for k, v in j.items() if k not in ("description", "core")}
    out["source_labels"] = [SOURCE_LABEL.get(s, s) for s in j["sources"]]
    out["locations"] = j["locations"][:6]
    out["more_locations"] = max(0, len(j["locations"]) - 6)
    return out


FLAGS_VERSION = "6"  # clause-scoped citizenship/clearance exceptions; recheck older flags


def refresh_flags() -> int:
    """Re-run flag detection over stored descriptions after detect_flags changes."""
    if db.meta_get("flags_version") == FLAGS_VERSION:
        return 0
    rows = db.rows("SELECT id, source, title, description, flags FROM jobs")
    changed = []
    for i, r in enumerate(rows):  # worked out before anything's locked, leaving room for whoever's using the app
        old = set(db.loads(r["flags"], []))
        new = set(detect_flags(r["title"], r["description"] or ""))
        # SimplifyJobs says this in metadata we don't store: kept for its rows, and where there's no text to read it from again
        keep = old & {"us_citizen"} if r["source"].startswith("simplify") or not r["description"] else set()
        if sorted(new | keep) != sorted(old):
            changed.append((db.dumps(sorted(new | keep)), db.now(), r["id"]))
        if i % 200 == 0:
            time.sleep(0.002)
    for k in range(0, len(changed), 500):
        with db.tx() as c:
            c.executemany("UPDATE jobs SET flags=?, changed_at=? WHERE id=?", changed[k:k + 500])
    db.meta_set("flags_version", FLAGS_VERSION)
    invalidate_cache()
    return len(rows)


SKILLS_VERSION = "3"  # bump when detect_skills' patterns change (3: algorithms, OS, networking, trading, mental math...)


def refresh_skills() -> int:
    """Check stored skill tags again after detect_skills changes. A posting with its description stored is simply read
    again. Without one (most SimplifyJobs rows, and the demo's snapshot), only tags whose patterns were narrowed are
    re-checked against the title: the rest may have come from a description that's no longer here. People's resume
    skills get the same check against their resume text."""
    if db.meta_get("skills_version") == SKILLS_VERSION:
        return 0
    rows = db.rows("SELECT id, title, description, skills FROM jobs")
    updates = []
    for i, r in enumerate(rows):
        if i % 200 == 0:  # leave room for whoever's using the app
            time.sleep(0.002)
        old = db.loads(r["skills"], []) or []
        if r["description"]:
            new = detect_skills(f"{r['title']}\n{r['description']}")
        else:
            seen = set(detect_skills(r["title"]))
            new = [s for s in old if s not in NARROWED_SKILLS or s in seen] + \
                [s for s in sorted(seen) if s not in old]
        if new != old:
            updates.append((db.dumps(new), db.now(), r["id"]))
    for k in range(0, len(updates), 500):
        with db.tx() as c:
            c.executemany("UPDATE jobs SET skills=?, changed_at=? WHERE id=?", updates[k:k + 500])
    with db.tx() as c:
        for p in db.rows("SELECT id, resume_text, profile_json FROM profiles WHERE COALESCE(resume_text, '') != ''"):
            prof = db.loads(p["profile_json"], {}) or {}
            seen = set(detect_skills(p["resume_text"]))
            skills = [s for s in prof.get("skills") or [] if s not in NARROWED_SKILLS or s in seen]
            if skills != (prof.get("skills") or []):
                prof["skills"] = skills
                c.execute("UPDATE profiles SET profile_json=? WHERE id=?", (db.dumps(prof), p["id"]))
    db.meta_set("skills_version", SKILLS_VERSION)
    invalidate_cache()
    return len(updates)


REQUIREMENTS_VERSION = "2"


def refresh_requirements() -> int:
    """(Re)read who each stored posting is open to from its full description (once per rules version)."""
    if db.meta_get("requirements_version") == REQUIREMENTS_VERSION:
        return 0
    rows = db.rows("SELECT id, title, description FROM jobs WHERE form_checked=0 AND COALESCE(description,'') != ''")
    reqs = []
    for i, r in enumerate(rows):
        if i % 50 == 0:  # leave room for whoever's using the app
            time.sleep(0.002)
        reqs.append((db.dumps(eligibility.extract(r["title"], r["description"])), db.now(), r["id"]))
    # role categories: business roles that mention "developer" etc. were filed as Software
    fixed = [(classify_category(r["title"]), db.now(), r["id"]) for r in db.rows("SELECT id, title FROM jobs")
             if NONTECH_RE.search(r["title"])]
    for k in range(0, len(reqs), 500):
        with db.tx() as c:
            c.executemany("UPDATE jobs SET requirements=?, changed_at=? WHERE id=?", reqs[k:k + 500])
    with db.tx() as c:
        c.executemany("UPDATE jobs SET category=?, changed_at=? WHERE id=?", fixed)
    db.meta_set("requirements_version", REQUIREMENTS_VERSION)
    invalidate_cache()
    return len(rows)


def backfill_req_ids() -> int:
    """Fill in requisition numbers for postings stored before they were tracked (once)."""
    if db.meta_get("req_ids_v1"):
        return 0
    found = []
    for r in db.rows("SELECT id, url, description FROM jobs WHERE req_id IS NULL"):
        rid = extract_req_id(r["url"], r["description"] or "")
        if rid:
            found.append((rid, db.now(), r["id"]))
    with db.tx() as c:
        c.executemany("UPDATE jobs SET req_id=?, changed_at=? WHERE id=?", found)
        # applications keep their own copy so the number survives the posting closing
        c.execute("UPDATE applications SET req_id=(SELECT j.req_id FROM jobs j WHERE j.id=applications.job_id)"
                  " WHERE req_id IS NULL")
    db.meta_set("req_ids_v1", "1")
    return len(found)
