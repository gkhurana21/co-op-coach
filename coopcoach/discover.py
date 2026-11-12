"""Find company job boards nobody added by hand: every Greenhouse, Ashby and Lever board the public web links to.

Where the names come from: Common Crawl (a free, public crawl of the web; its index lists every page it saw on
jobs.ashbyhq.com, boards.greenhouse.io, job-boards.greenhouse.io and jobs.lever.co), and the application links in
SimplifyJobs' lists. Each new board is checked live (it answers and lists roles) before the sync reads it, and its
company name comes from the board itself. Boards without internships today are read about once a day (spread over
the day's syncs), so thousands more cost a few seconds a sync.

Runs from the app about once a month (Settings → Job sources shows what it added) and from tools/discover_boards.py.
"""

import concurrent.futures as cf
import html
import json
import logging
import re
import threading
import time

import httpx

from . import companies, db
from .sources import UA
from .textutil import company_key, is_internship

log = logging.getLogger(__name__)

CC_INDEXES = "https://index.commoncrawl.org/collinfo.json"
HOSTS = {  # host -> (ats, how a board's slug appears in its URLs)
    "jobs.ashbyhq.com": ("ashby", r"https?://jobs\.ashbyhq\.com/([^/?#]+)"),
    "boards.greenhouse.io": ("greenhouse", r"https?://boards\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?#&]+)"),
    "job-boards.greenhouse.io": ("greenhouse", r"https?://job-boards\.greenhouse\.io/([^/?#]+)"),
    "jobs.lever.co": ("lever", r"https?://jobs\.lever\.co/([^/?#]+)"),
}
_NOT_SLUG = {"embed", "api", "v1", "favicon.ico", "robots.txt", "sitemap.xml", "static", "assets", "jobs", "careers",
             "privacy", "terms", "login", "search", "_next"}
SIMPLIFY = ["SimplifyJobs/Summer2027-Internships", "SimplifyJobs/New-Grad-Positions"]


def _client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": UA})


def crawl_slugs(indexes: int = 3, progress=None) -> set[tuple[str, str]]:
    """(ats, slug) for every board the last few Common Crawl indexes saw. Each index is a month or two of the web;
    several together catch boards one crawl missed."""
    found: set[tuple[str, str]] = set()
    with _client(120) as h:
        apis = [c["cdx-api"] for c in h.get(CC_INDEXES).json()[:indexes]]
        for api in apis:
            for host, (ats, rx) in HOSTS.items():
                pat = re.compile(rx, re.I)
                try:
                    pages = h.get(api, params={"url": f"{host}/*", "output": "json", "showNumPages": "true",
                                               "pageSize": 1}).json().get("pages", 0)
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("Common Crawl %s %s: %s", api.rsplit("/", 1)[-1], host, e)
                    continue
                for page in range(pages):
                    for attempt in range(3):  # the index server is busy at times: wait and ask again
                        try:
                            r = h.get(api, params={"url": f"{host}/*", "output": "json", "fl": "url", "pageSize": 1,
                                                   "page": page})
                            if r.status_code in (502, 503, 504):
                                raise httpx.HTTPStatusError("busy", request=r.request, response=r)
                            for line in r.text.splitlines():
                                m = pat.match(json.loads(line).get("url", "")) if line.startswith("{") else None
                                slug = m.group(1).strip().lower() if m else ""
                                if slug and slug not in _NOT_SLUG and re.fullmatch(r"[\w.%-]{2,80}", slug):
                                    found.add((ats, slug))
                            break
                        except (httpx.HTTPError, ValueError):
                            time.sleep(5 * (attempt + 1))
                    if progress:
                        progress(f"reading Common Crawl: {len(found)} boards so far")
    return found


def simplify_slugs() -> dict[tuple[str, str], str]:
    """(ats, slug) -> company name, from the application links in SimplifyJobs' internship and new-grad lists."""
    from .sources.ats import detect
    out: dict[tuple[str, str], str] = {}
    with _client(120) as h:
        for repo in SIMPLIFY:
            try:
                rows = h.get(f"https://raw.githubusercontent.com/{repo}/dev/.github/scripts/listings.json").json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("SimplifyJobs %s: %s", repo, e)
                continue
            for x in rows:
                k = detect(x.get("url") or "")
                if k and k[0] in ("greenhouse", "lever", "ashby"):
                    out.setdefault((k[0], k[1].lower()), x.get("company_name") or "")
    return out


def check(ats: str, slug: str, name: str = "") -> dict | None:
    """Is this a live board with open roles? {ats, slug, name, roles, interns}, or None."""
    try:
        with _client(25) as h:
            if ats == "greenhouse":
                r = h.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
                if r.status_code != 200:
                    return None
                titles = [j.get("title") or "" for j in r.json().get("jobs", [])]
                if titles and not name:
                    name = h.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}").json().get("name") or ""
            elif ats == "ashby":
                r = h.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
                if r.status_code != 200:
                    return None
                titles = [j.get("title") or "" for j in r.json().get("jobs", [])]
                if titles and not name:
                    m = re.search(r"<title>(.*?)\s+Jobs?</title>", h.get(f"https://jobs.ashbyhq.com/{slug}").text, re.S)
                    name = html.unescape(m.group(1)).strip() if m else ""
            elif ats == "lever":
                r = h.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
                if r.status_code != 200 or not isinstance(r.json(), list):
                    return None
                titles = [j.get("text") or "" for j in r.json()]
                if titles and not name:
                    m = re.search(r"<title>(.*?)</title>", h.get(f"https://jobs.lever.co/{slug}").text, re.S)
                    name = html.unescape(m.group(1)).strip() if m else ""
            else:
                return None
    except (httpx.HTTPError, ValueError):
        return None
    if not titles:
        return None
    name = re.sub(r"\s+(jobs|careers)$", "", name or "", flags=re.I).strip() or slug.replace("-", " ").title()
    return {"ats": ats, "slug": slug, "name": name[:120], "roles": len(titles),
            "interns": sum(1 for t in titles if is_internship(t))}


def _tier(name: str) -> str:
    yc = companies.yc_lookup(name)
    if yc and company_key(yc["name"]) == company_key(name):
        return "bigtech" if (yc.get("team_size") or 0) >= 2000 or yc.get("status") == "Public" else "yc"
    return companies.static_tier(name) or "startup"


def run(indexes: int = 3, limit: int | None = None, progress=None) -> dict:
    """Find boards this computer doesn't read yet, check each live, and add the real ones (the sync then reads them).
    Boards someone removed stay removed."""
    started = time.time()
    have = {(r["ats"], r["slug"].lower()) for r in db.rows("SELECT ats, slug FROM companies")}
    names = simplify_slugs()
    cands = {k: names.get(k, "") for k in crawl_slugs(indexes, progress) | set(names) if k not in have}
    todo = sorted(cands.items())[:limit] if limit else sorted(cands.items())
    if progress:
        progress(f"checking {len(todo)} new boards live")
    with cf.ThreadPoolExecutor(24) as ex:
        live = [b for b in ex.map(lambda kv: check(kv[0][0], kv[0][1], kv[1]), todo) if b]
    now = db.now()
    with db.tx() as c:  # internships open: read next sync; none: spread over the day like other quiet boards
        c.executemany("INSERT OR IGNORE INTO companies (ats, slug, name, tier, origin, last_status, last_count,"
                      " last_checked) VALUES (?,?,?,?, 'discovered', ?,?,?)",
                      [(b["ats"], b["slug"], b["name"], _tier(b["name"]), f"found {time.strftime('%Y-%m-%d')}: "
                        f"{b['roles']} open roles", b["interns"] or None,
                        None if b["interns"] else now - (i * 977) % 86400) for i, b in enumerate(live)])
    out = {"candidates": len(cands), "checked": len(todo), "added": len(live),
           "with_internships": sum(1 for b in live if b["interns"]),
           "by_ats": {a: sum(1 for b in live if b["ats"] == a) for a in ("greenhouse", "ashby", "lever")},
           "seconds": round(time.time() - started)}
    db.meta_set("discover_last", db.dumps({**out, "at": now}))
    log.info("discovered boards: %s", out)
    return out


def track_hiring_yc() -> int:
    """Every YC company that says it's hiring gets its ycombinator.com jobs page read (Work at a Startup lists the
    same roles, but only to people signed in). Boards someone removed stay removed."""
    have = {(r["ats"], r["slug"].lower()) for r in db.rows("SELECT ats, slug FROM companies")}
    rows = [("yc", c["slug"], c["name"]) for c in companies.yc_all()
            if c.get("is_hiring") and c.get("slug") and c.get("status") in ("Active", None)
            and (c.get("team_size") or 0) < 2000 and ("yc", c["slug"].lower()) not in have]
    if rows:
        with db.tx() as c:
            c.executemany("INSERT OR IGNORE INTO companies (ats, slug, name, tier, origin) VALUES (?,?,?, 'yc',"
                          " 'discovered')", rows)
    return len(rows)


def due(days: int = 30) -> bool:
    last = db.loads(db.meta_get("discover_last"), {}) or {}
    return time.time() - (last.get("at") or 0) > days * 86400


state = {"running": False, "progress": ""}
_lock = threading.Lock()


def start(indexes: int = 2) -> bool:
    """Find new boards in the background (Settings → Job sources, and about once a month by itself)."""
    if not _lock.acquire(blocking=False):
        return False

    def go():
        try:
            state.update(running=True, progress="starting")
            run(indexes, progress=lambda m: state.update(progress=m))
        except Exception as e:  # a failed search for new boards must not take anything else down
            log.warning("finding new boards failed: %s", e)
            db.meta_set("discover_last", db.dumps({"error": str(e)[:200], "at": db.now()}))
        finally:
            state.update(running=False, progress="")
            _lock.release()
    threading.Thread(target=go, name="discover", daemon=True).start()
    return True


def status() -> dict:
    return {**state, "last": db.loads(db.meta_get("discover_last"), None)}
