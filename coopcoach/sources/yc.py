"""Y Combinator company job pages (ycombinator.com/companies/<slug>/jobs).

Every hiring YC startup lists its roles there with structured data: type (Internship / Full-time), location,
skills, school year, and a visa field ("Will sponsor", "US citizen/visa only", ...). We read one company page per
request, a few at a time (in a lane of the sync of their own), and the sync revisits quiet companies about once a
day.
"""

import html
import json
import re
import threading
import time

from ..textutil import extract_terms, is_internship, parse_iso
from . import pooled

_gate = threading.BoundedSemaphore(6)  # be gentle with ycombinator.com: a few pages at a time


def _visa_note(visa: str) -> str:
    v = (visa or "").lower()
    if "will sponsor" in v or v.startswith("any"):
        return "Visa sponsorship is available for this role."
    if "citizen" in v and "visa" in v:
        return "Candidates must be authorized to work in the US without sponsorship."
    if "citizen" in v:
        return "Must be a U.S. citizen."
    return ""


def fetch(slug: str, name: str, tier: str | None, known: set[str], info: dict | None = None) -> list[dict]:
    with _gate, pooled() as c:
        r = c.get(f"https://www.ycombinator.com/companies/{slug}/jobs", headers={"Accept": "text/html"}, timeout=25)
        r.raise_for_status()
        time.sleep(0.25)
    m = re.search(r'data-page="([^"]+)"', r.text)
    if not m:
        raise ValueError("YC page format changed (no data-page)")
    jobs = json.loads(html.unescape(m.group(1))).get("props", {}).get("jobPostings") or []
    if info is not None:
        info["roles"] = len(jobs)  # every open role, for the startup feed's hiring speed
    out = []
    for j in jobs:
        title = j.get("title") or ""
        if not ((j.get("type") or "").lower().startswith("intern") or is_internship(title)):
            continue
        url = j.get("url") or ""
        url = f"https://www.ycombinator.com{url}" if url.startswith("/") else url
        skills = ", ".join(s.get("name", s) if isinstance(s, dict) else str(s) for s in (j.get("skills") or []))
        desc = "\n".join(x for x in [
            f"{j.get('companyOneLiner') or ''}",
            f"Role: {j.get('prettyRole') or j.get('role') or ''}",
            f"Skills: {skills}" if skills else "",
            f"Minimum school year: {j.get('minSchoolYear')}" if j.get("minSchoolYear") else "",
            _visa_note(j.get("visa")),  # normalized, so "US citizen/visa only" isn't misread as citizens-only
            f"Salary: {j.get('salaryRange')}" if j.get("salaryRange") else "",
        ] if x)
        locs = [p.strip() for p in re.split(r"\s*/\s*", j.get("location") or "") if p.strip()]
        out.append({
            "source": "yc", "company": j.get("companyName") or name, "title": title, "url": url,
            "locations": locs, "terms": extract_terms(title), "description": desc,
            "posted_at": parse_iso(j.get("createdAt")), "tier_hint": tier or "yc",
        })
    return out
