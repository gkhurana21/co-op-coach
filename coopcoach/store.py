"""Read/write helpers for jobs, profiles, favourites and applications, plus ranked job listing."""

import hashlib
import threading
import time

from . import companies, config, db, eligibility, matching
from .textutil import (
    NONTECH_RE,
    classify_category,
    classify_regions,
    clean_url,
    company_key,
    detect_flags,
    detect_skills,
    extract_req_id,
    normalize_terms,
    title_key,
)

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
    import yaml
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
        for e in (yaml.safe_load(found.read_text()) or {}).get("boards", []):
            rows.append((e["ats"], e["slug"], e["name"], e.get("tier"), "discovered", e.get("interns_seen", 0)))
    now = db.now()
    with db.tx() as c:
        for i, (ats, slug, name, tier, origin, interns) in enumerate(rows):
            # boards with internships open get checked on the next sync; quiet ones are spread across the day
            spread = now - (i * 977) % 86400 if origin == "discovered" and not interns else None
            c.execute("INSERT OR IGNORE INTO companies (ats, slug, name, tier, origin, last_checked, last_count)"
                      " VALUES (?,?,?,?,?,?,?)", (ats, slug, name, tier, origin, spread, interns or None))
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
    with db.tx() as c:
        for url, label, grp, region in default_event_feeds():
            if url not in seeded:
                c.execute("INSERT OR IGNORE INTO event_feeds (url, label, career_only, created_at) VALUES (?,?,1,?)",
                          (url, label, db.now()))
                seeded.add(url)
            c.execute("UPDATE event_feeds SET grp=?, region=COALESCE(NULLIF(?, ''), region) WHERE url=? AND"
                      " (grp IS NULL OR grp<>?)", (grp, region, url, grp))
    db.meta_set("event_feeds_seeded", db.dumps(sorted(seeded)))


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


def upsert_jobs(jobs: list[dict], now: int) -> list[str]:
    """Insert/update postings; returns ids that are brand new."""
    if not jobs:
        return []
    existing = {}
    ids = [j["id"] for j in jobs]
    for i in range(0, len(ids), 800):
        chunk = ids[i:i + 800]
        for r in db.rows(f"SELECT id, source FROM jobs WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            existing[r["id"]] = r["source"]
    new_ids, params = [], []
    for j in jobs:
        prev_source = existing.get(j["id"])
        if prev_source is None:
            new_ids.append(j["id"])
            existing[j["id"]] = j["source"]
        source = j["source"] if prev_source is None or _rank(j["source"]) >= _rank(prev_source) else prev_source
        params.append((j["id"], j["source"], j["company"], j["company_key"], j["title"], j["url"], db.dumps(j["locations"]),
                       db.dumps(j["regions"]), db.dumps(j["terms"]), j["category"], j["tier"], j["yc_batch"],
                       db.dumps(j["skills"]), db.dumps(j["flags"]), j["description"], j["posted_at"], now, now,
                       j.get("req_id"), db.dumps(j.get("requirements") or []), j.get("form_checked", 0), source))
    with db.tx() as c:  # one batch: a single round trip per chunk on Supabase instead of one per posting
        c.executemany(
            """INSERT INTO jobs (id, source, company, company_key, title, url, locations, regions, terms, category,
                       tier, yc_batch, skills, flags, description, posted_at, first_seen, last_seen, active, req_id,
                       requirements, form_checked)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)
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
                       form_checked=MAX(jobs.form_checked, excluded.form_checked)""",
            params)
    invalidate_cache()
    return new_ids


def deactivate_missing(source_prefixes: list[str], seen_ids: set[str], run_started: int) -> int:
    """Mark postings from sources that synced successfully but no longer list them as closed."""
    closed = 0
    with db.tx() as c:
        for prefix in source_prefixes:
            stale = [r["id"] for r in c.execute(
                "SELECT id FROM jobs WHERE active=1 AND (source=? OR source LIKE ?) AND last_seen < ?",
                (prefix, prefix + ":%", run_started)).fetchall() if r["id"] not in seen_ids]
            for i in range(0, len(stale), 800):
                chunk = stale[i:i + 800]
                c.execute(f"UPDATE jobs SET active=0 WHERE id IN ({','.join('?' * len(chunk))})", chunk)
            closed += len(stale)
        # search-based sources don't tell us when something closes; expire them after 3 weeks unseen
        c.execute("UPDATE jobs SET active=0 WHERE active=1 AND (source LIKE 'linkedin%' OR source LIKE 'indeed%')"
                  " AND last_seen < ?", (run_started - 21 * 86400,))
    invalidate_cache()
    return closed


# --- collapsed + cached job list ---------------------------------------------------

_cache_lock = threading.Lock()
_cache: dict = {"version": 0, "jobs": None}
_score_cache: dict = {}


def invalidate_cache() -> None:
    """Postings changed (a sync): reload them next time."""
    with _cache_lock:
        _cache["version"] += 1
        _cache["jobs"] = None
        _score_cache.clear()


def refresh_scores() -> None:
    """Something about a person changed (tracker, AI scores): re-score from the postings already in memory."""
    with _cache_lock:
        _score_cache.clear()


def patch_job(job_id: str, **fields) -> None:
    """One posting changed (its description was fetched): update it in memory instead of reloading every posting."""
    with _cache_lock:
        for j in _cache["jobs"] or []:
            if job_id in j["alt_ids"]:
                j.update({k: v for k, v in fields.items() if k in j})
        _score_cache.clear()


def _decode(r: dict) -> dict:
    for k in ("locations", "regions", "terms", "skills", "flags", "requirements"):
        if k in r:
            r[k] = db.loads(r[k], []) or []
    return r


# no descriptions here: they're fetched by id when needed, which keeps this load small
_COLS = ("id, source, company, company_key, title, url, locations, regions, terms, category, tier, yc_batch, skills,"
         " flags, '' AS description, posted_at, first_seen, requirements")
_DIGEST = ("md5(concat_ws('|', source, company, title, url, locations, regions, terms, category, tier, yc_batch, skills,"
           " flags, posted_at, requirements))")
_mirror: dict = {"rows": None, "digest": None}  # Supabase only: what was downloaded last time, and its fingerprints


def _active_rows() -> list[dict]:
    """The list fields of every active posting. From Supabase, only postings that changed since the last load are
    downloaded: a sync otherwise means re-sending ~14 MB, and the free plan includes 5 GB of transfer a month."""
    if not db.is_postgres():
        return db.rows(f"SELECT {_COLS} FROM jobs WHERE active=1")
    digests = {r["id"]: r["d"] for r in db.rows(f"SELECT id, {_DIGEST} AS d FROM jobs WHERE active=1")}
    if _mirror["rows"] is None:  # first load: one query is quicker than many small ones (fingerprints taken first,
        # so anything that changes in between is simply fetched again next time)
        rows = {r["id"]: dict(r) for r in db.rows(f"SELECT {_COLS} FROM jobs WHERE active=1")}
        _mirror.update(rows=rows, digest=digests, fetched=len(rows))
        return [dict(r) for r in rows.values()]
    rows, before = dict(_mirror["rows"]), _mirror["digest"] or {}
    stale = [i for i, d in digests.items() if before.get(i) != d or i not in rows]
    for k in range(0, len(stale), 1000):
        chunk = stale[k:k + 1000]
        for r in db.rows(f"SELECT {_COLS} FROM jobs WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            rows[r["id"]] = dict(r)
    rows = {i: rows[i] for i in digests if i in rows}  # closed postings drop out
    _mirror.update(rows=rows, digest=digests, fetched=len(stale))
    return [dict(r) for r in rows.values()]  # copies: grouping below changes them


def collapsed_jobs() -> list[dict]:
    """Active postings with cross-source duplicates merged (same company + same title)."""
    with _cache_lock:
        if _cache["jobs"] is not None:
            return _cache["jobs"]
        version = _cache["version"]
    rows = _active_rows()
    groups: dict[tuple, dict] = {}
    for r in rows:
        r = _decode(r)
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
    fav_companies, fav_jobs = favourites(pid)
    apps = applications(pid)
    ai = ai_scores(pid)
    cache_key = (pid, prof["updated_at"], _cache["version"])  # base scores depend only on profile + jobs
    if cache_key in _score_cache:
        base = _score_cache[cache_key]
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
        _score_cache[cache_key] = base
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
              starred: bool = False) -> dict:
    jobs = scored_jobs(pid)
    now = time.time()
    ql = q.lower().strip()

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


FLAGS_VERSION = "4"  # bump when detect_flags learns something new


def refresh_flags() -> int:
    """Re-run flag detection over stored descriptions after detect_flags changes."""
    if db.meta_get("flags_version") == FLAGS_VERSION:
        return 0
    rows = db.rows("SELECT id, title, description, flags FROM jobs")
    with db.tx() as c:
        for r in rows:
            old = set(db.loads(r["flags"], []))
            new = set(detect_flags(r["title"], r["description"] or ""))
            keep = old & {"us_citizen"}  # came from SimplifyJobs metadata we don't store
            c.execute("UPDATE jobs SET flags=? WHERE id=?", (db.dumps(sorted(new | keep)), r["id"]))
    db.meta_set("flags_version", FLAGS_VERSION)
    invalidate_cache()
    return len(rows)


REQUIREMENTS_VERSION = "2"


def refresh_requirements() -> int:
    """(Re)read who each stored posting is open to from its full description (once per rules version)."""
    if db.meta_get("requirements_version") == REQUIREMENTS_VERSION:
        return 0
    rows = db.rows("SELECT id, title, description FROM jobs WHERE form_checked=0 AND COALESCE(description,'') != ''")
    with db.tx() as c:
        c.executemany("UPDATE jobs SET requirements=? WHERE id=?",
                      [(db.dumps(eligibility.extract(r["title"], r["description"])), r["id"]) for r in rows])
        # role categories: business roles that mention "developer" etc. were filed as Software
        fixed = [(classify_category(r["title"]), r["id"]) for r in db.rows("SELECT id, title FROM jobs")
                 if NONTECH_RE.search(r["title"])]
        c.executemany("UPDATE jobs SET category=? WHERE id=?", fixed)
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
            found.append((rid, r["id"]))
    with db.tx() as c:
        c.executemany("UPDATE jobs SET req_id=? WHERE id=?", found)
        # applications keep their own copy so the number survives the posting closing
        c.execute("UPDATE applications SET req_id=(SELECT j.req_id FROM jobs j WHERE j.id=applications.job_id)"
                  " WHERE req_id IS NULL")
    db.meta_set("req_ids_v1", "1")
    return len(found)
