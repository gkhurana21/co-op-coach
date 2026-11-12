"""Public job-board APIs for Greenhouse, Lever, Ashby and Workday company career sites."""

import html
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..textutil import (
    clean_url,
    extract_terms,
    is_internship,
    parse_iso,
    parse_relative_posted,
    strip_html,
)
from . import client, pooled

log = logging.getLogger(__name__)


# --- URL detection (for "paste a careers link" in the UI) ---------------------

def detect(url: str) -> tuple[str, str] | None:
    """Map a careers URL to (ats, slug). Workday slugs are the normalized careers-site URL."""
    u = url.strip()
    m = re.search(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", u)
    if m:
        return "greenhouse", m.group(1).lower()
    m = re.search(r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)", u)
    if m:
        return "greenhouse", m.group(1).lower()
    m = re.search(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)", u)
    if m:
        return "lever", m.group(1).lower()
    m = re.search(r"jobs\.ashbyhq\.com/([\w.%-]+)", u)
    if m:
        return "ashby", m.group(1)
    m = re.search(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", u)
    if m:
        return "workday", f"https://{m.group(1)}.{m.group(2)}.myworkdayjobs.com/{m.group(3)}"
    m = re.search(r"ycombinator\.com/companies/([\w-]+)", u)
    if m:
        return "yc", m.group(1).lower()
    return None


# --- Greenhouse ---------------------------------------------------------------

def _count(info: dict | None, roles: int) -> None:
    """Every open role on the board (not just internships): how fast a company is hiring, for the startup feed."""
    if info is not None:
        info["roles"] = roles


def greenhouse(slug: str, name: str, tier: str | None, known: set[str], info: dict | None = None) -> list[dict]:
    with pooled() as c:
        r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        r.raise_for_status()
        listed = r.json().get("jobs", [])
        _count(info, len(listed))
        jobs = [j for j in listed if is_internship(j.get("title", ""))]

        def detail(j):
            url = j.get("absolute_url") or ""
            desc, questions = "", None
            if clean_url(url) not in known:  # only fetch descriptions for postings we haven't stored yet
                try:  # the application form tells us who they'll accept ("What U.S. State do you reside in?")
                    d = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j['id']}",
                              params={"questions": "true"})
                    if d.status_code == 200:
                        data = d.json()
                        desc = strip_html(data.get("content", ""))
                        questions = [q.get("label") or "" for q in data.get("questions") or []]
                except httpx.HTTPError:
                    pass
            loc = (j.get("location") or {}).get("name") or ""
            return {
                "source": "greenhouse",
                "company": j.get("company_name") or name,
                "title": j["title"],
                "url": url,
                "locations": [p.strip() for p in re.split(r";|\s\|\s", loc) if p.strip()] or [loc],
                "terms": extract_terms(j["title"] + " " + desc[:600]),
                "description": desc,
                "posted_at": parse_iso(j.get("first_published") or j.get("updated_at")),
                "tier_hint": tier,
                "req_id": str(j.get("requisition_id") or ""),
                "questions": questions,
            }

        with ThreadPoolExecutor(6) as ex:
            return list(ex.map(detail, jobs))


def greenhouse_job(url: str, source: str, timeout: float = 20) -> dict | None:
    """One Greenhouse posting from its board API, with the full application form."""
    slug = source.split(":", 1)[1] if ":" in source else ""
    m = re.search(r"gh_jid=(\d+)|/jobs/(\d+)", url or "")
    if not slug or not m:
        return None
    gid = m.group(1) or m.group(2)
    with client(timeout=timeout) as c:
        r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{gid}", params={"questions": "true"})
        if r.status_code != 200:
            return None
        data = r.json()
    data["slug"], data["gh_id"] = slug, gid
    return data


def greenhouse_form(url: str, source: str) -> tuple[str, list[str]] | None:
    """(description, application questions) for one Greenhouse posting, from its board API."""
    data = greenhouse_job(url, source)
    if not data:
        return None
    return strip_html(data.get("content", "")), [q.get("label") or "" for q in data.get("questions") or []]


# --- Lever --------------------------------------------------------------------

def _lever_post(j: dict, name: str, tier: str | None) -> dict | None:
    cats = j.get("categories") or {}
    title = j.get("text") or ""
    if not is_internship(title, cats.get("commitment") or ""):
        return None
    desc = j.get("descriptionPlain") or strip_html(j.get("description") or "")
    for lst in j.get("lists") or []:
        desc += "\n" + strip_html(lst.get("content") or "")
    locs = cats.get("allLocations") or [cats.get("location") or ""]
    return {
        "source": "lever",
        "company": name,
        "title": title,
        "url": j.get("hostedUrl") or "",
        "locations": [l for l in locs if l],
        "terms": extract_terms(title + " " + desc[:600]),
        "description": desc.strip(),
        "posted_at": parse_iso(j.get("createdAt")),
        "tier_hint": tier,
    }


_LEVER_ROW = re.compile(r'data-qa-posting-id="([\w-]+)".*?data-qa="posting-name">([^<]*)</h5>(.*?)</a>', re.S)
_LEVER_COMMITMENT = re.compile(r'class="[^"]*\bcommitment\b[^"]*">([^<]*)<')
_LEVER_BIG = 60  # postings on the board page above which only the internships are read in full


def lever(slug: str, name: str, tier: str | None, known: set[str], peek: bool = False,
          info: dict | None = None) -> list[dict]:
    """Lever's API sends every posting with its full description in one answer: tens of megabytes (and a minute or
    two) for a big employer. Its public board page lists the same postings with just title and commitment, so on a
    big board the internships are picked from that and only theirs are read in full. Small boards, and boards whose
    page can't be read (a custom careers site), use the API's full list: one quick answer. `peek` (the quick check)
    reads in full only the internships not stored yet, and says so in info["partial"]."""
    with pooled() as c:
        page = c.get(f"https://jobs.lever.co/{slug}", headers={"Accept": "text/html"})
        rows = _LEVER_ROW.findall(page.text) if page.status_code == 200 else []
        if len(rows) <= _LEVER_BIG:
            r = c.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
            r.raise_for_status()
            _count(info, len(r.json()))
            return [p for p in (_lever_post(j, name, tier) for j in r.json()) if p]
        _count(info, len(rows))
        ids = [pid for pid, title, rest in rows if is_internship(html.unescape(title), html.unescape(
            next(iter(_LEVER_COMMITMENT.findall(rest)), "")))]
        if peek:
            ids = [pid for pid in ids if f"https://jobs.lever.co/{slug}/{pid}" not in known]
            if info is not None:
                info["partial"] = True

        def one(pid):
            for attempt in (1, 2):  # Lever's API sometimes answers 502: one more try before the board counts as failed
                try:
                    r = c.get(f"https://api.lever.co/v0/postings/{slug}/{pid}", params={"mode": "json"})
                    if r.status_code == 404:  # closed since the page was read
                        return None
                    r.raise_for_status()
                    return _lever_post(r.json(), name, tier)
                except (httpx.TransportError, httpx.HTTPStatusError) as e:
                    if attempt == 2 or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500):
                        raise

        with ThreadPoolExecutor(12) as ex:  # Lever's API takes a second or more per posting: read many at once
            return [p for p in ex.map(one, dict.fromkeys(ids)) if p]


# --- Ashby --------------------------------------------------------------------

def ashby(slug: str, name: str, tier: str | None, known: set[str], info: dict | None = None) -> list[dict]:
    with pooled() as c:
        r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        r.raise_for_status()
        out = []
        listed = r.json().get("jobs", [])
        _count(info, len(listed))
        for j in listed:
            title = j.get("title") or ""
            if not (is_internship(title) or (j.get("employmentType") or "").lower() == "intern"):
                continue
            locs = [j.get("location") or ""]
            for sec in j.get("secondaryLocations") or []:
                locs.append(sec.get("location") or "")
            if j.get("isRemote") and not any("remote" in l.lower() for l in locs):
                locs.append("Remote")
            desc = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml") or "")
            out.append({
                "source": "ashby",
                "company": name,
                "title": title,
                "url": j.get("jobUrl") or j.get("applyUrl") or "",
                "locations": [l for l in locs if l],
                "terms": extract_terms(title + " " + desc[:600]),
                "description": desc,
                "posted_at": parse_iso(j.get("publishedAt")),
                "tier_hint": tier,
            })
        return out


# --- Workday ------------------------------------------------------------------

_WD_RE = re.compile(r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/([\w-]+)")


# a board's own job-type filter values for internships: "Intern (Fixed Term) (Trainee)", "Co-op", "Students & Interns"
_WD_INTERN_FACET = re.compile(r"\bintern(s|ship|ships)?\b|\bco-?ops?\b|\bstudents?\b|\btrainees?\b|\bwork terms?\b|"
                              r"\bsummer (analyst|associate)s?\b", re.I)
_WD_FACET_CAP = 400  # postings read through the filter: past this the board is read by searching instead
_WD_QUERIES = ("intern", "co-op", "internship")
_WD_GATE = threading.BoundedSemaphore(24)  # Workday requests in flight at once (per process: a sync uses a few)


def _wd_intern_facets(data: dict) -> dict[str, list[str]]:
    """{filter parameter: [value ids]} for the internship values in a Workday board's filters (at most two filters,
    the ones listing the most postings)."""
    found: dict[str, list[tuple[str, int]]] = {}

    def walk(facets, param=None):
        for f in facets or []:
            p = f.get("facetParameter") or param
            for v in f.get("values") or []:
                if "values" in v:
                    walk([v], p)
                elif v.get("id") and _WD_INTERN_FACET.search(v.get("descriptor") or ""):
                    found.setdefault(p, []).append((v["id"], v.get("count") or 0))
    walk(data.get("facets"))
    best = sorted(found.items(), key=lambda kv: -sum(n for _, n in kv[1]))[:2]
    return {p: [i for i, _ in vals] for p, vals in best if p}


def workday(site_url: str, name: str, tier: str | None, known: set[str], max_results: int = 120,
            plan: dict | None = None, peek: bool = False, info: dict | None = None) -> list[dict]:
    """A Workday board's internships. Workday's keyword search is fuzzy and stops short, so a full read runs the
    three searches and also lists the board's own internship filter (most boards have one, and it finds many
    internships the searches miss). It also notes which searches found anything the filter (and the other searches)
    didn't: until the next full read, one to three days later, a sync reads only the filter and those searches. On
    most boards that's one or two requests instead of six or more. `plan` carries this between syncs:
    {"facets": {...}, "queries": [...], "trusted": bool (filter alone is enough), "full_by": unix time}.
    `peek` (the quick check) reads one page: Workday lists the newest first, so that's where new internships are. It
    sees only part of the board, which it says in info["partial"]."""
    m = _WD_RE.match(site_url)
    if not m:
        raise ValueError(f"not a Workday careers URL: {site_url}")
    plan = plan if plan is not None else {}
    tenant, wd, site = m.groups()
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"

    def page(query: str, offset: int, facets: dict | None = None, limit: int = 20) -> dict:
        # a busy Workday site answers 429 or 5xx now and then: wait a moment and ask again before giving up
        for wait in (2, 5, 12, None):
            try:
                with _WD_GATE:  # Workday answers "too many requests" past a few dozen at once from one computer
                    r = c.post(api, json={"appliedFacets": facets or {}, "limit": limit, "offset": offset,
                                          "searchText": query})
            except httpx.TransportError:  # a timeout or dropped connection: same as a busy answer
                if wait is None:
                    raise
                time.sleep(wait)
                continue
            if r.status_code != 429 and r.status_code < 500 or wait is None:
                r.raise_for_status()
                return r.json()
            after = r.headers.get("retry-after", "")
            time.sleep(min(float(after), 20) if after.isdigit() else wait + random.random() * wait)

    def search(query: str) -> list[dict]:
        found, offset = [], 0
        while offset < max_results:
            posts = page(query, offset).get("jobPostings") or []
            found += posts
            offset += 20
            # Workday search is fuzzy: once a page has no internship titles, the rest won't either
            if len(posts) < 20 or not any(is_internship(p.get("title") or "") for p in posts):
                break
        return found

    def listing(facets: dict) -> tuple[list[dict], bool]:
        """Every posting under the internship filter: (postings, whether that was all of them). The first page says
        how many there are; the rest are read a few at a time."""
        found, complete = [], True
        for param, ids in facets.items():
            first = page("", 0, {param: ids})
            found += first.get("jobPostings") or []
            total = first.get("total") or 0
            complete = complete and total <= _WD_FACET_CAP
            rest = range(20, min(total, _WD_FACET_CAP), 20)
            if rest:
                with ThreadPoolExecutor(4) as pages:
                    for data in pages.map(lambda off: page("", off, {param: ids}), rest):
                        found += data.get("jobPostings") or []
        return found, complete

    def by_filter() -> tuple[dict, list[dict], bool]:
        everything = page("", 0, limit=1)
        _count(info, everything.get("total") or 0)
        facets = _wd_intern_facets(everything)
        return (facets, *listing(facets)) if facets else ({}, [], False)

    def interns(posts: list[dict]) -> dict[str, dict]:
        return {p["externalPath"]: p for p in posts if p.get("externalPath") and is_internship(p.get("title") or "")}

    if peek:
        with pooled() as c:
            posts = page("", 0, plan.get("facets") or {}).get("jobPostings") or []
        if info is not None:
            info["partial"] = True
        return _wd_records(interns(posts), base, site, name, tier)
    with pooled() as c, ThreadPoolExecutor(4) as ex:
        if "queries" in plan and time.time() < (plan.get("full_by") or 0):  # between full reads: what's needed
            filtered = ex.submit(listing, plan["facets"]) if plan.get("facets") else None
            searched = interns([p for posts in ex.map(search, plan["queries"]) for p in posts])
            fposts = filtered.result()[0] if filtered else []
            return _wd_records({**searched, **interns(fposts)}, base, site, name, tier)
        # a full read: each search finds roles the others miss, so run them (and the filter) at once
        filtered = ex.submit(by_filter)
        results = {q: interns(posts) for q, posts in zip(_WD_QUERIES, ex.map(search, _WD_QUERIES))}
        facets, fposts, complete = filtered.result()
    found = interns(fposts)
    covered, needed = set(found), []
    for q in sorted(_WD_QUERIES, key=lambda q: -len(results[q])):  # the fewest searches that find the rest
        if results[q].keys() - covered:
            needed.append(q)
            covered |= results[q].keys()
    # the next full read comes one to three days later, a different time for each board so they spread across syncs
    plan.update(facets=facets, queries=needed, trusted=bool(facets) and complete and not needed,
                full_by=int(time.time() + 86400 * (1 + 2 * random.random())))
    return _wd_records({k: v for r in results.values() for k, v in r.items()} | found, base, site, name, tier)


def _wd_records(posts: dict[str, dict], base: str, site: str, name: str, tier: str | None) -> list[dict]:
    return [{
        "source": "workday",
        "company": name,
        "title": p.get("title") or "",
        "url": f"{base}/{site}{path}",
        "locations": [p.get("locationsText") or ""],
        "terms": extract_terms(p.get("title") or ""),
        "posted_at": parse_relative_posted(p.get("postedOn") or ""),
        "posted_text": p.get("postedOn") or "",  # "Posted 3 Days Ago": what the fingerprint compares (see store)
        "tier_hint": tier,
        "req_id": next((b for b in p.get("bulletFields") or [] if isinstance(b, str)
                        and re.fullmatch(r"[A-Za-z]{0,6}[-_]?\d[\w-]{2,24}", b)), ""),
    } for path, p in posts.items()]


# --- SAP SuccessFactors career sites (e.g. EY, Deloitte Canada): public RSS search feed --------------

_NOT_A_JOB = re.compile(r"talent (?:community|network|pool)|future opportunit|general (?:application|interest)|"
                        r"expression of interest", re.I)


def _rss_items(xml: str) -> list[dict]:
    items = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(name):
            m = re.search(rf"<{name}>(.*?)</{name}>", it, re.S)
            return re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1)).strip() if m else ""
        items.append({"title": tag("title"), "link": tag("link"), "date": tag("pubDate"), "description": tag("description")})
    return items


def successfactors(host: str, name: str, tier: str | None, known: set[str]) -> list[dict]:
    from email.utils import parsedate_to_datetime
    seen: dict[str, dict] = {}
    with client() as c:
        for q in ("intern", "internship", "co-op", "student", "summer analyst"):
            r = c.get(f"https://{host}/services/rss/job/", params={"locale": "en_US", "keywords": q},
                      headers={"Accept": "application/rss+xml, text/xml"})
            r.raise_for_status()
            for it in _rss_items(r.text):
                raw = it["title"]
                m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", raw)  # "Title (City, PROV, CA, Postal)"
                title, loc = (m.group(1), m.group(2)) if m else (raw, "")
                desc = strip_html(it["description"])
                if not it["link"] or it["link"] in seen or _NOT_A_JOB.search(title) or \
                        not is_internship(title, desc[:400] if re.search(r"co-?op/intern", desc[:400], re.I) else ""):
                    continue
                try:
                    posted = int(parsedate_to_datetime(it["date"]).timestamp()) if it["date"] else None
                except (TypeError, ValueError):
                    posted = None
                loc = re.sub(r",\s*[A-Z]\d[A-Z]\s?\d[A-Z]\d\s*$|,\s*\d{5}(-\d{4})?\s*$", "", loc)  # drop postal codes
                seen[it["link"]] = {"source": "successfactors", "company": name, "title": title, "url": it["link"],
                                    "locations": [loc] if loc else [], "terms": extract_terms(f"{title} {desc[:600]}"),
                                    "description": desc, "posted_at": posted, "tier_hint": tier}
    return list(seen.values())


# --- Jibe (iCIMS) career sites (e.g. KPMG Canada): public JSON search API ------------------------------

def jibe(host: str, name: str, tier: str | None, known: set[str]) -> list[dict]:
    seen: dict[str, dict] = {}
    with client() as c:
        for q in ("intern", "co-op", "student", "summer"):
            for page in range(1, 4):
                r = c.get(f"https://{host}/api/jobs", params={"keywords": q, "page": page, "limit": 50})
                r.raise_for_status()
                data = r.json()
                jobs = data.get("jobs") or []
                for j in jobs:
                    d = j.get("data") or {}
                    title = d.get("title") or ""
                    url = d.get("apply_url") or (f"https://{host}/jobs/{d.get('req_id') or d.get('slug')}" if d.get("slug") else "")
                    if not url or url in seen or _NOT_A_JOB.search(title) or not is_internship(title):
                        continue
                    locs = [d.get("full_location") or ", ".join(x for x in (d.get("city"), d.get("state"), d.get("country")) if x)]
                    locs += [a for a in (d.get("additional_locations") or []) if isinstance(a, str)]
                    desc = strip_html(d.get("description") or "")
                    seen[url] = {"source": "jibe", "company": name, "title": title, "url": url,
                                 "locations": [l for l in locs if l], "terms": extract_terms(f"{title} {desc[:600]}"),
                                 "description": desc, "posted_at": parse_iso(d.get("posted_date") or d.get("create_date")),
                                 "tier_hint": tier, "req_id": str(d.get("req_id") or "")}
                if len(jobs) < 50:
                    break
    return list(seen.values())


def probe_custom_site(url: str) -> tuple[str, str] | None:
    """For career sites on their own domain: is it SuccessFactors (RSS) or Jibe (JSON API)?"""
    m = re.match(r"https?://([^/]+)", url.strip())
    if not m:
        return None
    host = m.group(1).lower()
    with client(timeout=15, public_only=True) as c:  # a careers link someone pasted
        try:
            r = c.get(f"https://{host}/api/jobs", params={"keywords": "intern", "limit": 1})
            if r.status_code == 200 and "jobs" in r.json():
                return "jibe", host
        except (httpx.HTTPError, ValueError):
            pass
        try:
            r = c.get(f"https://{host}/services/rss/job/", params={"locale": "en_US", "keywords": "intern"})
            if r.status_code == 200 and "<rss" in r.text[:500]:
                return "successfactors", host
        except httpx.HTTPError:
            pass
    return None


def _yc(slug, name, tier, known, info=None):
    from . import yc
    return yc.fetch(slug, name, tier, known, info)


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday,
            "successfactors": successfactors, "jibe": jibe, "yc": _yc}
