"""Keep coopcoach/niches.yaml current: when each firm opened internships this season, and firms the list is missing.

    .venv/bin/python -m tools.refresh_niches                     # report for Summer <next year>, nothing written
    .venv/bin/python -m tools.refresh_niches --term "Summer 2028" --write
    .venv/bin/python -m tools.refresh_niches --file listings.json # a copy of SimplifyJobs' listings.json

Reads SimplifyJobs' public internship list (it keeps the date each posting went up), and for every firm in a niche
finds the first posting for the term: that's when the firm opened. At a trading firm any internship counts; at a bank,
pension fund, energy company, exchange, asset manager or betting company only its quant and trading roles do. Simplify
often tags next summer's internships with this summer's term, or with none, so those count too when they went up from
June 1 on. Listings tagged with more than two terms are open all year and are left out. Days are UTC. --write puts the
term in the niche's `season:` and the dates in the `opened:` fields. It also lists companies with several postings in
the niche's role category that aren't on the list yet, for a person to look at and add by hand (with a segment). Run it
once the season is underway (September or so).
"""

import argparse
import collections
import datetime
import json
import re
import sys
from pathlib import Path

import httpx
import yaml

from coopcoach import niches
from coopcoach.sources.simplify import RAW
from coopcoach.textutil import SIMPLIFY_CATEGORY, company_key, is_internship

REPO = "SimplifyJobs/Summer2027-Internships"
CATALOG = niches.CATALOG
_YEAR = re.compile(r"\b(20\d\d)\b")
_SEASON = re.compile(r"\b(winter|spring|summer|fall|autumn)\b", re.I)


def load(path: str | None, repo: str) -> list[dict]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    r = httpx.get(RAW.format(repo=repo), timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def day(ts: float) -> datetime.date:
    """The UTC day of a timestamp, so the dates come out the same on every computer."""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date()


def for_term(x: dict, term: str) -> str:
    """Is this posting for `term` ("Summer 2027")? "tagged" when its tags say so. "untagged" when the tag is likely
    wrong, as it often is: it's tagged only "N/A" and/or the same season a year earlier ("Summer 2026"), yet went up
    from June 1 of that year on, when that summer's hiring is over, and its title says it's an internship without
    naming another season or year. "" otherwise, and for listings tagged with more than two terms (open all year)."""
    terms = x.get("terms") or []
    if len(terms) > 2:
        return ""
    if term in terms:
        return "tagged"
    season, year = term.rsplit(" ", 1)
    title = x.get("title") or ""
    if not set(terms) <= {"N/A", f"{season} {int(year) - 1}"} or not is_internship(title):
        return ""
    june = datetime.datetime(int(year) - 1, 6, 1, tzinfo=datetime.timezone.utc).timestamp()
    other = set(_YEAR.findall(title)) - {year} or {w.lower() for w in _SEASON.findall(title)} - {season.lower()}
    return "untagged" if x["date_posted"] >= june and not other else ""


def in_role(x: dict, segment: str, n: dict, words: re.Pattern | None, tagged: bool) -> bool:
    """At a trading firm any internship counts. Elsewhere (a bank, an energy company, an exchange...) the niche is one
    team inside a bigger company, so only its quant and trading roles count: a title with one of the niche's words
    or, when the posting is tagged for the term, Simplify's role category. (Simplify files some bank roles, such as
    relationship managers, under Quant, so the category alone can't vouch for a wrong tag.)"""
    every = n.get("all_roles") and not any(s["id"] == segment and s.get("role_only") for s in n["segments"])
    if segment in niches._CORE_SEGMENTS or every or (words and words.search(x.get("title") or "")):
        return True
    return tagged and SIMPLIFY_CATEGORY.get(x.get("category") or "") == n.get("category")


def openings(listings: list[dict], n: dict, term: str, data_starts: float) -> dict[str, datetime.date]:
    """firm key -> the day its first posting for `term` went up. Postings in the list's first two weeks are left out:
    those are where the list's history starts, not openings."""
    firms = [f for s in n["segments"] for f in s["firms"]]
    segment = {f["key"]: s["id"] for s in n["segments"] for f in s["firms"]}
    kws = n.get("keywords") or []
    words = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")s?\b", re.I) if kws else None
    m = niches.Matcher(firms)
    first: dict[str, float] = {}
    for x in listings:
        how = for_term(x, term) if x.get("date_posted") and x["date_posted"] >= data_starts + 14 * 86400 else ""
        if not how:
            continue
        f = m.match(company_key(x.get("company_name") or ""))
        if f and in_role(x, segment[f["key"]], n, words, how == "tagged"):
            first[f["key"]] = min(first.get(f["key"], x["date_posted"]), x["date_posted"])
    return {k: day(v) for k, v in first.items()}


def missing(listings: list[dict], n: dict, firms: list[dict], at_least: int = 2) -> list[tuple[str, int]]:
    m = niches.Matcher(firms)
    seen: collections.Counter = collections.Counter()
    for x in listings:
        if SIMPLIFY_CATEGORY.get(x.get("category") or "") != n["category"]:
            continue
        name = x.get("company_name") or ""
        if name and not m.match(company_key(name)):
            seen[name] += 1
    return [(c, k) for c, k in seen.most_common() if k >= at_least]


def _set_opened(line: str, when: datetime.date | None) -> str:
    """One firm's line with its `opened:` set to `when` (or taken off), where the file keeps it: before careers,
    small and note."""
    line = re.sub(r", opened: [\d-]+", "", line)
    if when is None:
        return line
    at = re.search(r", (?:careers|small|note):|\}[ \t]*$", line)
    return line[:at.start()] + f", opened: {when.isoformat()}" + line[at.start():]


def write(dates: dict[str, datetime.date], n: dict, term: str) -> int:
    """Put `term` in the niche's `season:` and each firm's date in its `opened:` (one firm per line), taking the date
    off firms with none this term and keeping everything else as it is. The new file is read back before it's saved:
    if any line didn't come out right, nothing is written."""
    text = CATALOG.read_text(encoding="utf-8")
    start = re.search(r"^" + re.escape(n["id"]) + r":\s*$", text, re.M)
    end = re.search(r"^\S", text[start.end():], re.M)
    stop = start.end() + end.start() if end else len(text)
    block = re.sub(r"^(  season: ).*?([ \t]+#.*)?$", lambda mm: mm.group(1) + term + (mm.group(2) or ""),
                   text[start.end():stop], count=1, flags=re.M)
    changed = 0
    for f in (f for s in n["segments"] for f in s["firms"]):
        rx = re.compile(r"^[ \t]*- \{name: \"?" + re.escape(f["name"]) + r"\"?, .*\}[ \t]*$", re.M)
        line = rx.search(block)
        if line:
            new = _set_opened(line.group(0), dates.get(f["key"]))
            if new != line.group(0):
                block, changed = block[:line.start()] + new + block[line.end():], changed + 1
    text = text[:start.end()] + block + text[stop:]
    got = yaml.safe_load(text)[n["id"]]
    have = {niches._norm_key(f["name"]): f["opened"] for s in got["segments"] for f in s["firms"] if f.get("opened")}
    wrong = sorted(k for k in have.keys() | dates.keys() if have.get(k) != dates.get(k))
    if got.get("season") != term or wrong:
        sys.exit(f"not written: {CATALOG.name} didn't come out right (season: {got.get('season')!r}; "
                 f"dates: {', '.join(wrong[:5]) or 'fine'})")
    CATALOG.write_text(text, encoding="utf-8")
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--term", default=f"Summer {datetime.date.today().year + 1}")
    ap.add_argument("--niche", default="quant")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--file", help="a local copy of listings.json")
    ap.add_argument("--write", action="store_true", help="put the dates in niches.yaml")
    a = ap.parse_args()
    n = niches._shipped(a.niche)
    if not n:
        sys.exit(f"no niche {a.niche} in {CATALOG.name}")
    firms = [f for s in n["segments"] for f in s["firms"]]
    listings = load(a.file, a.repo)
    starts = min((x["date_posted"] for x in listings if x.get("date_posted")), default=0)
    dates = openings(listings, n, a.term, starts)
    print(f"{a.term}: {len(dates)} of {len(firms)} firms in {n['name']} have opened "
          f"(the list starts {day(starts) if starts else '?'})")
    for f in sorted(firms, key=lambda f: dates.get(f["key"], datetime.date.max)):
        if f["key"] in dates:
            was = f" (was {f['opened']})" if f["opened"] and f["opened"] != dates[f["key"]].isoformat() else ""
            print(f"  {dates[f['key']].isoformat()}  {f['name']}{was}")
    gone = [f for f in firms if f["opened"] and f["key"] not in dates]
    if gone:
        print("\nNo posting for the term found (--write takes their date off):")
        for f in gone:
            print(f"  {f['name']} (was {f['opened']})")
    late = missing(listings, n, firms)
    if late:
        print(f"\nPosting {n['category']} roles but not on the list (add by hand, with a segment):")
        for c, k in late[:40]:
            print(f"  {k:3}  {c}")
    if a.write:
        print(f"\nwrote {write(dates, n, a.term)} dates to {CATALOG.relative_to(CATALOG.parent.parent)}")


if __name__ == "__main__":
    main()
