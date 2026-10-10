"""SimplifyJobs community-maintained internship lists (github.com/SimplifyJobs)."""

from ..textutil import SIMPLIFY_CATEGORY, extract_terms, normalize_terms
from . import client

RAW = "https://raw.githubusercontent.com/{repo}/dev/.github/scripts/listings.json"


def fetch(repos: list[str]) -> list[dict]:
    out: list[dict] = []
    with client(timeout=90) as c:
        for repo in repos:
            r = c.get(RAW.format(repo=repo))
            r.raise_for_status()
            for x in r.json():
                if not x.get("active") or not x.get("is_visible", True):
                    continue
                title = x.get("title") or ""
                terms = normalize_terms(x.get("terms") or []) or extract_terms(title)
                out.append({
                    "source": "simplify",
                    "company": x.get("company_name") or "",
                    "title": title,
                    "url": x.get("url") or "",
                    "locations": x.get("locations") or [],
                    "terms": terms,
                    "category": SIMPLIFY_CATEGORY.get(x.get("category") or ""),
                    "posted_at": x.get("date_posted"),
                    "sponsorship": x.get("sponsorship") or "",
                    "degrees": x.get("degrees") or [],
                })
    return out
