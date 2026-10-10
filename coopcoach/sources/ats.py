"""Public job-board APIs for Greenhouse, Lever, Ashby and Workday company career sites."""

import logging
import re
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
from . import client

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

def greenhouse(slug: str, name: str, tier: str | None, known: set[str]) -> list[dict]:
    with client() as c:
        r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        r.raise_for_status()
        jobs = [j for j in r.json().get("jobs", []) if is_internship(j.get("title", ""))]

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


def greenhouse_job(url: str, source: str) -> dict | None:
    """One Greenhouse posting from its board API, with the full application form."""
    slug = source.split(":", 1)[1] if ":" in source else ""
    m = re.search(r"gh_jid=(\d+)|/jobs/(\d+)", url or "")
    if not slug or not m:
        return None
    gid = m.group(1) or m.group(2)
    with client(timeout=20) as c:
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

def lever(slug: str, name: str, tier: str | None, known: set[str]) -> list[dict]:
    with client() as c:
        r = c.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        r.raise_for_status()
        out = []
        for j in r.json():
            cats = j.get("categories") or {}
            title = j.get("text") or ""
            if not is_internship(title, cats.get("commitment") or ""):
                continue
            desc = j.get("descriptionPlain") or strip_html(j.get("description") or "")
            for lst in j.get("lists") or []:
                desc += "\n" + strip_html(lst.get("content") or "")
            locs = cats.get("allLocations") or [cats.get("location") or ""]
            out.append({
                "source": "lever",
                "company": name,
                "title": title,
                "url": j.get("hostedUrl") or "",
                "locations": [l for l in locs if l],
                "terms": extract_terms(title + " " + desc[:600]),
                "description": desc.strip(),
                "posted_at": parse_iso(j.get("createdAt")),
                "tier_hint": tier,
            })
        return out


# --- Ashby --------------------------------------------------------------------

def ashby(slug: str, name: str, tier: str | None, known: set[str]) -> list[dict]:
    with client() as c:
        r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        r.raise_for_status()
        out = []
        for j in r.json().get("jobs", []):
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


def workday(site_url: str, name: str, tier: str | None, known: set[str], max_results: int = 120) -> list[dict]:
    m = _WD_RE.match(site_url)
    if not m:
        raise ValueError(f"not a Workday careers URL: {site_url}")
    tenant, wd, site = m.groups()
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    seen: dict[str, dict] = {}
    with client() as c:
        for query in ("intern", "co-op", "internship"):
            offset = 0
            while offset < max_results:
                r = c.post(api, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": query})
                r.raise_for_status()
                data = r.json()
                posts = data.get("jobPostings") or []
                for p in posts:
                    title = p.get("title") or ""
                    path = p.get("externalPath") or ""
                    if not path or path in seen or not is_internship(title):
                        continue
                    seen[path] = {
                        "source": "workday",
                        "company": name,
                        "title": title,
                        "url": f"{base}/{site}{path}",
                        "locations": [p.get("locationsText") or ""],
                        "terms": extract_terms(title),
                        "posted_at": parse_relative_posted(p.get("postedOn") or ""),
                        "tier_hint": tier,
                        "req_id": next((b for b in p.get("bulletFields") or [] if isinstance(b, str)
                                        and re.fullmatch(r"[A-Za-z]{0,6}[-_]?\d[\w-]{2,24}", b)), ""),
                    }
                offset += 20
                # Workday search is fuzzy: once a page has no internship titles, the rest won't either
                if len(posts) < 20 or not any(is_internship(p.get("title") or "") for p in posts):
                    break
    return list(seen.values())


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
    with client(timeout=15) as c:
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


def _yc(slug, name, tier, known):
    from . import yc
    return yc.fetch(slug, name, tier, known)


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday,
            "successfactors": successfactors, "jibe": jibe, "yc": _yc}
