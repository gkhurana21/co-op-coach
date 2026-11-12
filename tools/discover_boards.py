"""Rebuild coopcoach/boards_discovered.yaml: find real company career boards and check each one live.

    .venv/bin/python -m tools.discover_boards          # ~10 minutes, polite concurrency

1. Mine SimplifyJobs' internship + new-grad listings for application links that point at Greenhouse, Lever,
   Ashby or Workday boards (real boards companies have actually used), and Common Crawl's index for every other
   Greenhouse, Ashby and Lever board the web links to (coopcoach.discover).
2. Check every board live: keep it only if it responds and has open roles.
3. For hiring Y Combinator companies not covered yet, try their likely Ashby/Greenhouse/Lever board names and keep a
   match only if it's confirmed: job titles match the company's YC job page, or the company's website/careers page
   links to that board. Everything else is covered by reading the company's ycombinator.com job page.
4. Tag tiers (Workday -> large employer, Greenhouse/Lever/Ashby -> startup, YC -> yc) and write the YAML.
"""

import collections
import concurrent.futures as cf
import datetime
import html
import json
import re

import httpx
import yaml

from coopcoach import companies, config
from coopcoach.sources.ats import detect
from coopcoach.textutil import company_key, is_internship

REPOS = ["SimplifyJobs/Summer2027-Internships", "SimplifyJobs/New-Grad-Positions"]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/128", "Accept": "application/json"}
PUBLIC = re.compile(r"universit|college|school|hospital|health ?(system|care|services)|medical cent|clinic|city of|"
                    r"county|state of|government|ministry|public|district|library|foundation|church|museum|commission|"
                    r"authority", re.I)


def titles(h: httpx.Client, ats: str, slug: str) -> list[str] | None:
    if ats == "greenhouse":
        r = h.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        return [j["title"] for j in r.json().get("jobs", [])] if r.status_code == 200 else None
    if ats == "lever":
        r = h.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        return [j["text"] for j in r.json()] if r.status_code == 200 and isinstance(r.json(), list) else None
    if ats == "ashby":
        r = h.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        return [j["title"] for j in r.json().get("jobs", [])] if r.status_code == 200 else None
    base, site = slug.rsplit("/", 1)
    tenant = base.split("//")[1].split(".")[0]
    r = h.post(f"{base}/wday/cxs/{tenant}/{site}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": 0,
                                                              "searchText": "intern"})
    return [j["title"] for j in r.json().get("jobPostings", [])] if r.status_code == 200 else None


def check(c: dict) -> dict:
    try:
        with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as h:
            t = titles(h, c["ats"], c["slug"])
        c["open"], c["interns"] = (len(t), sum(map(is_internship, t))) if t is not None else (0, 0)
        c["alive"] = t is not None
    except (httpx.HTTPError, ValueError):
        c["alive"], c["open"], c["interns"] = False, 0, 0
    return c


def yc_titles(h: httpx.Client, slug: str) -> set[str]:
    r = h.get(f"https://www.ycombinator.com/companies/{slug}/jobs", headers={**UA, "Accept": "text/html"})
    m = re.search(r'data-page="([^"]+)"', r.text)
    jobs = json.loads(html.unescape(m.group(1)))["props"].get("jobPostings") or [] if m else []
    return {norm(j.get("title")) for j in jobs}


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def confirm_yc(c: dict) -> dict:
    hosts = {"ashby": "jobs.ashbyhq.com/", "greenhouse": "greenhouse.io/", "lever": "jobs.lever.co/"}
    with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as h:
        for slug in c["slugs"]:
            for ats in ("ashby", "greenhouse", "lever"):
                try:
                    t = titles(h, ats, slug)
                except (httpx.HTTPError, ValueError):
                    continue
                if t is None:
                    continue
                try:
                    if {norm(x) for x in t} & yc_titles(h, c["yc_slug"]):
                        return {**c, "ats": ats, "slug": slug, "open": len(t), "why": "titles match YC job page"}
                    site = (c.get("website") or "").rstrip("/")
                    for url in (site, site + "/careers", site + "/jobs") if site else ():
                        if f"{hosts[ats]}{slug.lower()}" in h.get(url, headers={**UA, "Accept": "text/html"}).text.lower():
                            return {**c, "ats": ats, "slug": slug, "open": len(t), "why": "company site links to board"}
                except (httpx.HTTPError, ValueError):
                    pass
                return c  # a board with this name exists but isn't provably theirs: don't guess
    return c


def main() -> None:
    today = datetime.date.today().isoformat()
    found: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    with httpx.Client(timeout=120, headers=UA) as h:
        for repo in REPOS:
            for x in h.get(f"https://raw.githubusercontent.com/{repo}/dev/.github/scripts/listings.json").json():
                k = detect(x.get("url") or "")
                if k and k[0] in ("greenhouse", "lever", "ashby", "workday"):
                    found[k][x.get("company_name") or "?"] += 1
    cands = [{"ats": a, "slug": s, "name": n.most_common(1)[0][0]} for (a, s), n in found.items()]
    print(f"{len(cands)} candidate boards from SimplifyJobs; checking each live…")
    with cf.ThreadPoolExecutor(16) as ex:
        live = [c for c in ex.map(check, cands) if c["alive"] and c["open"] > 0]
    print(f"{len(live)} alive with open roles ({sum(c['interns'] > 0 for c in live)} with internships now)")

    # every other Greenhouse, Ashby and Lever board the public web links to (Common Crawl's index), checked the same way
    from coopcoach import discover
    known = {(c["ats"], c["slug"].lower()) for c in cands}
    extra = sorted(k for k in discover.crawl_slugs(2, progress=print) if k not in known)
    print(f"{len(extra)} more boards from Common Crawl; checking each live…")
    with cf.ThreadPoolExecutor(24) as ex:
        live += [{"ats": b["ats"], "slug": b["slug"], "name": b["name"], "open": b["roles"], "interns": b["interns"],
                  "alive": True} for b in ex.map(lambda k: discover.check(*k), extra) if b]
    print(f"{len(live)} live boards in all")

    seed = config.seed_companies()
    covered = {company_key(c["name"]) for c in live} | {company_key(e["name"]) for a in seed for e in seed.get(a) or []}
    companies.refresh_yc()
    yc_all = json.loads(companies.YC_CACHE.read_text(encoding="utf-8"))
    hiring = [y for y in yc_all if y.get("is_hiring") and y.get("status") not in ("Inactive", "Acquired")]
    todo = []
    for y in hiring:
        if company_key(y["name"]) in covered:
            continue
        slugs = []
        for s in (y.get("slug") or "", re.sub(r"[^a-z0-9]", "", y["name"].lower()),
                  re.sub(r"^https?://(www\.)?", "", y.get("website") or "").split(".")[0].lower()):
            if s and s not in slugs:
                slugs.append(s)
        todo.append({"name": y["name"], "yc_slug": y["slug"], "batch": y.get("batch"), "website": y.get("website"),
                     "slugs": slugs})
    print(f"{len(hiring)} hiring YC companies, {len(todo)} not covered yet; confirming their boards…")
    with cf.ThreadPoolExecutor(6) as ex:
        yc_done = list(ex.map(confirm_yc, todo))

    curated = {(a, e["slug"]) for a in ("greenhouse", "lever", "ashby", "successfactors", "jibe")
               for e in seed.get(a) or []} | {("workday", e["url"]) for e in seed.get("workday") or []}
    boards, seen = [], set()

    def add(b):
        k = (b["ats"], b["slug"])
        if k not in curated and k not in seen:
            seen.add(k)
            boards.append(b)

    for c in live:
        tier = ("other" if PUBLIC.search(c["name"]) else "finance") if c["ats"] == "workday" else "startup"
        b = {"ats": c["ats"], "slug": c["slug"], "name": c["name"], "tier": tier}
        if c["interns"]:
            b["interns_seen"] = c["interns"]
        add({**b, "verified": f"live, {c['open']} open roles"})
    for c in yc_done:
        if c.get("why"):
            add({"ats": c["ats"], "slug": c["slug"], "name": c["name"], "tier": "yc",
                 "verified": f"YC {c['batch']}: {c['why']}"})
        else:
            add({"ats": "yc", "slug": c["yc_slug"], "name": c["name"], "tier": "yc",
                 "verified": f"YC {c['batch']}: hiring on ycombinator.com"})
    header = (f"# Career boards found by mining SimplifyJobs' application links and Y Combinator's directory, each checked\n"
              f"# live on {today} (dead or mismatched boards dropped). interns_seen = internships open at that check.\n"
              f"# Regenerate with `python -m tools.discover_boards`; hand-picked companies live in seed_companies.yaml.\n")
    (config.PKG / "boards_discovered.yaml").write_text(
        header + yaml.safe_dump({"boards": boards}, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")
    print(f"wrote {len(boards)} boards:", dict(collections.Counter(b["ats"] for b in boards)))


if __name__ == "__main__":
    main()
