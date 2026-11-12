"""Indeed via the optional python-jobspy package (Indeed blocks plain HTTP scraping)."""

import importlib.util
import logging
import math

from ..textutil import extract_terms, is_internship, parse_iso

log = logging.getLogger(__name__)


def available() -> bool:
    return importlib.util.find_spec("jobspy") is not None  # (importing it takes half a second: pandas and all)


def _val(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def fetch(queries: list[dict], past_days: int = 7, results_per_query: int = 40) -> list[dict]:
    from jobspy import scrape_jobs

    seen: dict[str, dict] = {}
    for q in queries:
        try:
            df = scrape_jobs(
                site_name=["indeed"],
                search_term=q["keywords"],
                location=q.get("location", ""),
                country_indeed=q.get("country", "Canada"),
                results_wanted=int(results_per_query),
                hours_old=int(past_days) * 24,
                description_format="markdown",
                verbose=0,
            )
        except Exception as e:  # jobspy raises a variety of transport/parse errors
            log.warning("indeed query %r failed: %s", q, e)
            continue
        for rec in df.to_dict("records"):
            title = _val(rec.get("title")) or ""
            url = _val(rec.get("job_url")) or ""
            desc = _val(rec.get("description")) or ""
            if not url or url in seen or not is_internship(title, ""):
                continue
            posted = _val(rec.get("date_posted"))
            seen[url] = {
                "source": "indeed",
                "company": _val(rec.get("company")) or "Unknown",
                "title": title,
                "url": _val(rec.get("job_url_direct")) or url,
                "locations": [_val(rec.get("location")) or ""],
                "terms": extract_terms(title + " " + desc[:600]),
                "description": desc,
                "posted_at": parse_iso(str(posted)) if posted else None,
            }
    return list(seen.values())
