"""Job sources. Each module exposes fetch(...) -> list[dict] of raw postings with keys:

source, company, title, url, locations (list[str]), terms (list[str]), category (optional),
description (optional), posted_at (unix seconds, optional), sponsorship (optional), tier_hint (optional)
"""

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"},
    )
