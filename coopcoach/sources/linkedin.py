"""LinkedIn public (logged-out) job search. Small and slow on purpose: a handful of queries,
internship experience level only, a pause between requests. LinkedIn's terms restrict automated
access, so keep volumes low or disable this source in config.yaml."""

import logging
import random
import time
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from ..textutil import extract_terms, is_internship, parse_iso
from . import client

log = logging.getLogger(__name__)

SEARCH = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
          "?keywords={kw}&location={loc}&f_E=1&f_TPR=r{secs}&start={start}")


def _parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("div.base-card"):
        a = card.select_one("a.base-card__full-link")
        title_el = card.select_one(".base-search-card__title")
        comp_el = card.select_one(".base-search-card__subtitle")
        loc_el = card.select_one(".job-search-card__location")
        time_el = card.select_one("time")
        if not (a and title_el and comp_el):
            continue
        urn = card.get("data-entity-urn", "")
        job_id = urn.rsplit(":", 1)[-1] if urn else ""
        url = f"https://www.linkedin.com/jobs/view/{job_id}" if job_id else a["href"].split("?")[0]
        out.append({
            "title": title_el.get_text(" ", strip=True),
            "company": comp_el.get_text(" ", strip=True),
            "location": loc_el.get_text(" ", strip=True) if loc_el else "",
            "url": url,
            "posted_at": parse_iso(time_el.get("datetime")) if time_el else None,
        })
    return out


def fetch(queries: list[dict], past_days: int = 7, pages_per_query: int = 2) -> list[dict]:
    secs = int(past_days) * 86400
    seen: dict[str, dict] = {}
    with client() as c:
        for q in queries:
            for page in range(int(pages_per_query)):
                url = SEARCH.format(kw=quote_plus(q["keywords"]), loc=quote_plus(q.get("location", "")),
                                    secs=secs, start=page * 25)
                try:
                    r = c.get(url, headers={"Accept": "text/html"})
                except httpx.HTTPError as e:
                    log.warning("linkedin request failed: %s", e)
                    break
                if r.status_code == 429:
                    log.warning("linkedin rate-limited; stopping early")
                    return list(seen.values())
                if r.status_code != 200 or not r.text.strip():
                    break
                cards = _parse(r.text)
                for j in cards:
                    if j["url"] in seen or not is_internship(j["title"]):
                        continue
                    seen[j["url"]] = {
                        "source": "linkedin",
                        "company": j["company"],
                        "title": j["title"],
                        "url": j["url"],
                        "locations": [j["location"]] if j["location"] else [],
                        "terms": extract_terms(j["title"]),
                        "posted_at": j["posted_at"],
                    }
                if len(cards) < 10:
                    break
                time.sleep(1.5 + random.random() * 1.5)
    return list(seen.values())
