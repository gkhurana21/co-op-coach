"""Fetch the full description of one posting on demand (for tailoring and cover letters).

Most SimplifyJobs/LinkedIn rows only carry a title, so we go to the source: Greenhouse, Lever, Ashby and
Workday expose JSON; anything else falls back to the page's visible text. Results are cached in jobs.description.
"""

import logging
import re

import httpx
from bs4 import BeautifulSoup

from . import db
from .sources import client
from .textutil import detect_flags, detect_skills, strip_html

log = logging.getLogger(__name__)


def _greenhouse(c: httpx.Client, url: str) -> str:
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url) or \
        re.search(r"greenhouse\.io/([\w-]+).*?[?&]gh_jid=(\d+)", url)
    if not m:
        return ""
    r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
    return strip_html(r.json().get("content", "")) if r.status_code == 200 else ""


def _lever(c: httpx.Client, url: str) -> str:
    m = re.search(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)/([0-9a-f-]{36})", url)
    if not m:
        return ""
    r = c.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
    if r.status_code != 200:
        return ""
    j = r.json()
    text = j.get("descriptionPlain") or strip_html(j.get("description", ""))
    for lst in j.get("lists") or []:
        text += f"\n{lst.get('text', '')}\n" + strip_html(lst.get("content", ""))
    return text


def _ashby(c: httpx.Client, url: str) -> str:
    m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
    if not m:
        return ""
    r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
    if r.status_code != 200:
        return ""
    for j in r.json().get("jobs", []):
        if m.group(2) in (j.get("jobUrl") or "") or j.get("id") == m.group(2):
            return j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", ""))
    return ""


def _workday(c: httpx.Client, url: str) -> str:
    m = re.match(r"(https://([\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)(/job/.+)", url)
    if not m:
        return ""
    base, tenant, site, path = m.groups()
    r = c.get(f"{base}/wday/cxs/{tenant}/{site}{path}", headers={"Accept": "application/json"})
    if r.status_code != 200:
        return ""
    return strip_html((r.json().get("jobPostingInfo") or {}).get("jobDescription", ""))


def _generic(c: httpx.Client, url: str) -> str:
    r = c.get(url, headers={"Accept": "text/html"})
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("article") or soup.body
    text = re.sub(r"\n\s*\n+", "\n", main.get_text("\n", strip=True)) if main else ""
    return text if len(text) > 400 else ""


def fetch_description(url: str) -> str:
    with client(timeout=25) as c:
        for fn in (_greenhouse, _lever, _ashby, _workday, _generic):
            try:
                text = fn(c, url)
            except (httpx.HTTPError, ValueError) as e:
                log.info("description fetch via %s failed: %s", fn.__name__, e)
                text = ""
            if text and len(text) > 200:
                return text[:20000]
    return ""


CLOSED_RE = re.compile(r"no longer accepting applications|this (?:job|position|posting|role|requisition) is no longer (?:available|open|active)|"
                       r"this job (?:posting )?has expired|position has been filled|not accepting applications at this time", re.I)


def ensure_description(job_id: str) -> str:
    """Return the posting's description, fetching and caching it if we don't have one yet."""
    j = db.row("SELECT id, url, title, description FROM jobs WHERE id=?", (job_id,))
    if not j:
        return ""
    if j["description"] and len(j["description"]) > 200:
        return j["description"]
    from . import central
    desc = central.description(job_id) or fetch_description(j["url"])  # the shared feed may have read it already
    if desc and CLOSED_RE.search(desc[:1500]):  # closed since the last crawl: the page says so, so it leaves the list now
        with db.tx() as c:
            c.execute("UPDATE jobs SET active=0, changed_at=? WHERE id=?", (db.now(), job_id))
        from . import store
        store.invalidate_cache()
        return ""
    if desc:
        skills, flags = detect_skills(f"{j['title']}\n{desc}"), detect_flags(j["title"], desc)
        with db.tx() as c:
            c.execute("UPDATE jobs SET description=?, skills=?, flags=?, changed_at=? WHERE id=?",
                      (desc, db.dumps(skills), db.dumps(flags), db.now(), job_id))
        from . import store
        store.patch_job(job_id, skills=skills, flags=flags)
    return desc
