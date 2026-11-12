"""The shipped niche data (coopcoach/niches.yaml) holds together, and tools/refresh_niches.py dates firms the way its
rule says: Simplify's term mis-tags, evergreen listings, off-role postings at banks, and UTC days. No network.

Run: .venv/bin/python -m tests.test_niche_data
"""

import datetime
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="coopcoach-nichedatatest-")
os.environ["COOPCOACH_DATA"] = TMP

import yaml  # noqa: E402

from coopcoach import niches  # noqa: E402
from coopcoach.sources import ats  # noqa: E402
from coopcoach.textutil import company_key  # noqa: E402
from tools import refresh_niches as rn  # noqa: E402

CANADIAN_CITIES = {"Toronto", "Montreal", "Calgary", "Vancouver", "Edmonton", "Victoria", "Ottawa", "Waterloo",
                   "Kitchener", "Halifax", "Winnipeg", "Quebec City", "St. John's", "Regina", "Saskatoon"}
UTC = datetime.timezone.utc

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def ts(y, mo, d, h=12) -> float:
    return datetime.datetime(y, mo, d, h, tzinfo=UTC).timestamp()


def firm(name: str, segment: str) -> dict:
    return {"key": niches._norm_key(name), "name": name, "aliases": [], "segment": segment}


def posting(company: str, title: str, terms: list[str], when: float, category: str = "Quant") -> dict:
    return {"company_name": company, "title": title, "terms": terms, "date_posted": when, "category": category}


def structure() -> None:
    raw = yaml.safe_load(niches.CATALOG.read_text(encoding="utf-8"))["quant"]
    n = niches._shipped("quant")
    firms = [f for s in n["segments"] for f in s["firms"]]
    season = raw.get("season") or ""
    check("the quant niche says which season its dates are for",
          re.fullmatch(r"(Winter|Spring|Summer|Fall) 20\d\d", season))
    year = int(season[-4:]) if season else 0
    check("...and every opening date falls in the year before it",
          all(datetime.date.fromisoformat(f["opened"]).year == year - 1 for f in firms if f["opened"]))

    owner: dict[str, set] = {}
    for f in firms:
        for nm in [f["name"], *f["aliases"]]:
            owner.setdefault(niches._norm_key(nm), set()).add(f["key"])
    check("no two firms share a name or alias", len({f["key"] for f in firms}) == len(firms) and
          all(len(v) == 1 for v in owner.values()))
    boards = [f["board"] for f in firms if f["board"]]
    check("every board is ats:slug with an ATS Co-op Coach can read",
          all(":" in b and b.split(":", 1)[0] in ats.FETCHERS and b.split(":", 1)[1] for b in boards))
    check("canada flags name Canadian cities",
          all(c in CANADIAN_CITIES for f in firms if f["canada"] for c in f["canada"].split(", ")))
    check("notes are short and name the season instead of saying 'last season'",
          all(len(f["note"]) <= 70 and "last season" not in f["note"].lower() for f in firms))
    check("careers links are web links",
          all(f["careers"].startswith(("https://", "http://")) for f in firms if f["careers"]))

    by = {f["name"]: f for f in firms}
    m = niches.Matcher(firms)
    check("the bare name 'Fanatics' (the merchandise company) isn't the betting firm",
          m.match(company_key("Fanatics")) is None and m.match(company_key("Fanatics Betting & Gaming")))
    check("Canadian offices the postings show are flagged (Tower: Montreal, BP: Calgary, TMX counts as Canadian)",
          by["Tower Research Capital"]["canada"] == "Montreal" and by["BP"]["canada"] == "Calgary" and
          (by["TMX Group"]["canada"] or by["TMX Group"]["segment"] == "canada"))
    check("the Northwestern list's GSA Capital and Mako Trading are on the list",
          "GSA Capital" in by and "Mako Trading" in by)
    name = lambda k: (m.match(k) or {}).get("name")  # noqa: E731
    check("'IMCO' is its own firm, not PIMCO or IMC",
          name("imco") == "IMCO" and name("pimco") == "PIMCO" and name("imc") == "IMC Trading")

    found = yaml.safe_load((niches.CATALOG.parent / "boards_discovered.yaml").read_text(encoding="utf-8"))["boards"]
    known = {(m.match(company_key(b["name"])) or {}).get("key") for b in found}
    check("every firm boards_discovered.yaml has a board for links somewhere (board or careers)",
          all(f["board"] or f["careers"] for f in firms if f["key"] in known))


def rule() -> None:
    n = {"id": "quant", "category": "Quant", "keywords": ["quant", "quantitative", "trading", "trader"],
         "segments": [{"id": "market-makers", "firms": [firm("Jane Street", "market-makers")]},
                      {"id": "prop-shops", "firms": [firm("Old Mission", "prop-shops")]},
                      {"id": "systematic", "firms": [firm("Evergreen Partners", "systematic"),
                                                     firm("Fellows Fund", "systematic")]},
                      {"id": "bank-desks", "firms": [firm("Big Bank", "bank-desks")]}]}
    starts = ts(2026, 2, 16)
    listings = [
        posting("Jane Street", "Quantitative Researcher Intern", ["Summer 2026"], ts(2026, 7, 6)),   # a July mis-tag
        posting("Jane Street", "Sales and Trading Intern", ["Summer 2027"], ts(2026, 8, 3)),
        posting("Jane Street", "Software Engineer Intern", ["Summer 2026"], ts(2026, 5, 20)),       # last summer's
        posting("Jane Street", "Trading Intern - Summer 2026", ["Summer 2026"], ts(2026, 6, 20)),   # says so
        posting("Jane Street", "Quant Co-op - Winter 2027", ["N/A"], ts(2026, 6, 25)),             # another season
        posting("Old Mission", "Software Engineer Intern", ["Summer 2027"], ts(2026, 7, 15), "Software"),
        posting("Old Mission", "Trader Intern", ["Summer 2027"], ts(2026, 2, 20)),       # the list's first two weeks
        posting("Evergreen Partners", "Research Intern",
                ["Summer 2027", "Fall 2027", "Winter 2027", "Spring 2027", "Summer 2028"], ts(2026, 3, 10)),
        posting("Evergreen Partners", "Quant Research Intern", ["Summer 2027"], ts(2026, 9, 1)),
        posting("Fellows Fund", "Execution Research Fellow", ["N/A"], ts(2026, 6, 16)),  # not an internship
        posting("Big Bank", "Wealth Management Technology Intern", ["Summer 2027"], ts(2026, 4, 8), "Software"),
        posting("Big Bank", "Relationship Manager Commercial Markets Intern", ["Summer 2026"], ts(2026, 7, 2)),
        posting("Big Bank", "Global Markets Quantitative Trading Summer Analyst", ["Summer 2027"], ts(2026, 8, 7)),
    ]
    got = rn.openings(listings, n, "Summer 2027", starts)
    check("a 'Summer 2026' posting from July counts for Summer 2027",
          got.get("jane street") == datetime.date(2026, 7, 6))
    check("...but not one from before June, one whose title names 2026, or one for another season",
          rn.for_term(listings[2], "Summer 2027") == "" and rn.for_term(listings[3], "Summer 2027") == "" and
          rn.for_term(listings[4], "Summer 2027") == "")
    check("an evergreen listing tagged with five terms is skipped",
          got.get("evergreen partners") == datetime.date(2026, 9, 1))
    check("an untagged program that isn't an internship doesn't date a firm", "fellows fund" not in got)
    check("at a trading firm a software internship counts (and the list's first two weeks don't)",
          got.get("old mission") == datetime.date(2026, 7, 15))
    check("at a bank only quant and trading roles count, and Simplify's category can't vouch for a wrong tag",
          got.get("big bank") == datetime.date(2026, 8, 7))

    late = [posting("Jane Street", "Quantitative Trader Intern", ["Summer 2027"], ts(2026, 8, 2, 2))]
    tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/Toronto"  # 2 a.m. UTC on August 2 is still August 1 in Toronto
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        utc_day = rn.openings(late, n, "Summer 2027", starts).get("jane street")
    finally:
        if tz is None:
            os.environ.pop("TZ")
        else:
            os.environ["TZ"] = tz
        if hasattr(time, "tzset"):
            time.tzset()
    check("days are UTC, whatever the computer's time zone", utc_day == datetime.date(2026, 8, 2))


def writing() -> None:
    n = niches._shipped("quant")
    by = {f["name"]: f for s in n["segments"] for f in s["firms"]}
    dates = {f["key"]: datetime.date.fromisoformat(f["opened"]) for f in by.values() if f["opened"]}
    dates[by["Jane Street"]["key"]] = datetime.date(2027, 7, 7)
    dates.pop(by["Two Sigma"]["key"])
    dates[by["Group One Trading"]["key"]] = datetime.date(2027, 7, 20)
    dates[by["Connor, Clark & Lunn"]["key"]] = datetime.date(2027, 9, 2)
    copy = Path(TMP) / "niches.yaml"
    shutil.copy(niches.CATALOG, copy)
    real, rn.CATALOG = rn.CATALOG, copy
    try:
        before = copy.read_text(encoding="utf-8")
        try:
            changed = rn.write(dates, n, "Summer 2028")
        except SystemExit as e:  # it refused: the checks below say what's missing
            changed = str(e)
        after = copy.read_text(encoding="utf-8")
        got = yaml.safe_load(after)["quant"]
        opened = {f["name"]: f.get("opened") for s in got["segments"] for f in s["firms"]}
        diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        check("--write sets the season (keeping its comment) and each firm's date, and takes off dates not found",
              changed == 4 and got["season"] == "Summer 2028" and "season: Summer 2028      #" in after and
              opened["Jane Street"] == datetime.date(2027, 7, 7) and opened["Two Sigma"] is None and
              opened["Group One Trading"] == datetime.date(2027, 7, 20) and
              opened['Connor, Clark & Lunn'] == datetime.date(2027, 9, 2))
        check("...changing only those lines, in the file's own layout",
              len(diff) == 5 and len(before.splitlines()) == len(after.splitlines()) and
              "{name: Group One Trading, where: Chicago, opened: 2027-07-20, small: true}" in after)
        lost = {**dates, "nowhere capital": datetime.date(2027, 7, 1)}
        ghost = {**n, "segments": [*n["segments"], {"id": "x", "name": "x", "blurb": "",
                                                     "firms": [{"key": "nowhere capital", "name": "Nowhere Capital"}]}]}
        try:
            rn.write(lost, ghost, "Summer 2028")
            refused = False
        except SystemExit:
            refused = True
        check("...and a date it can't place leaves the file untouched",
              refused and copy.read_text(encoding="utf-8") == after)
    finally:
        rn.CATALOG = real


def fields() -> None:
    """The other shipped fields (AI labs, robotics, chips, fintech, dev tools, Waterloo & Toronto startups)."""
    others = [nid for nid in niches.shipped_ids() if nid != "quant"]
    check("six more fields ship firm by firm", len(others) >= 6)
    bad, dupes = [], []
    for nid in others:
        n = niches._shipped(nid)
        fs = [f for s in n["segments"] for f in s["firms"]]
        bad += [f"{nid}: {f['name']}" for f in fs if not (f["careers"] or f["board"])]
        keys = [f["key"] for f in fs]
        dupes += [f"{nid}: {k}" for k in set(keys) if keys.count(k) > 1]
        segs = [s["id"] for s in n["segments"]]
        dupes += [f"{nid} segment {x}" for x in set(segs) if segs.count(x) > 1]
    check(f"every firm in them has a careers page or job board {bad[:3] or ''}", not bad)
    check(f"no firm or segment is listed twice in one field {dupes[:3] or ''}", not dupes)
    check("each says every firm in it is in the field (all_roles)", all(niches._shipped(x)["all_roles"] for x in others))

    def job(company: str, title: str, category: str) -> dict:
        from coopcoach.textutil import company_key
        return {"company_key": company_key(company), "title": title, "category": category, "tier": "other", "flags": []}

    fin = niches.Members([niches._shipped("fintech")])
    check("in an all-roles field any tech internship at a listed firm counts",
          fin.firm(job("Wealthsimple", "Software Developer Intern", "Software")) is not None)
    check("...but not its sales and business internships",
          fin.firm(job("Wealthsimple", "Sales Operations Intern", "Business/Finance")) is None)
    hw = niches.Members([niches._shipped("hardware")])
    check("at a big company with one team in the field (Nokia) only the field's roles count",
          hw.firm(job("Nokia", "Firmware Engineering Co-op", "Hardware")) is not None
          and hw.firm(job("Nokia", "Software Developer Co-op", "Software")) is None)


def main():
    structure()
    rule()
    writing()
    fields()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
