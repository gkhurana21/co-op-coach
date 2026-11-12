"""Does a company hire interns? Answered from every posting Co-op Coach has seen from it (open or closed), the
dates it saw internships open, and the curated company lists, in one plain sentence plus the details."""

import re
from collections import Counter
from datetime import date, datetime

from . import companies, db, niches
from .textutil import company_key, title_key

INTERNY = re.compile(r"\b(interns?|internships?|co-?ops?|students?|stagiaires?|stages?|placements?|apprentices?|"
                     r"new grad)\b", re.I)
_SEASON = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def _catalog(ck: str) -> tuple[dict, str] | None:
    """The firm on one of the curated lists (trading firms, AI labs...), and that list's name."""
    for nid in niches.shipped_ids():
        n = niches._shipped(nid)
        for s in n["segments"]:
            for f in s["firms"]:
                if ck in {company_key(x) for x in [f["name"], *(f.get("aliases") or [])]}:
                    return f, n["name"]
    return None


def _role(title: str) -> str:
    """A role's name without its season or year: "ML Intern/Co-op (Winter 2027)" and "ML Intern/Co-op" are one role."""
    t = re.sub(r"\([^)]*\)|\b(winter|spring|summer|fall|autumn)\b|\b20\d\d\b", " ", title or "", flags=re.I)
    return title_key(t)


def _season_key(t: str) -> tuple:
    m = re.fullmatch(r"(Winter|Spring|Summer|Fall) (20\d\d)", t or "")
    return (int(m[2]), _SEASON[m[1]]) if m else (0, 0)


def _when(ts) -> date | None:
    if not ts:
        return None
    if isinstance(ts, date):
        return ts
    if isinstance(ts, str):
        try:
            return date.fromisoformat(ts[:10])
        except ValueError:
            return None
    return datetime.fromtimestamp(int(ts)).date()


def lookup(q: str) -> dict:
    ck = company_key(q or "")
    if len(ck) < 2:
        return {"error": "Type a company name, like Notion or Jane Street."}
    rows = db.rows("SELECT id, company, title, url, terms, posted_at, first_seen, active, source FROM jobs"
                   " WHERE company_key=? OR company_key LIKE ?", (ck, ck + " %"))
    name = Counter(r["company"] for r in rows).most_common(1)[0][0] if rows else q.strip()
    interns = [r for r in rows if INTERNY.search(r["title"] or "")]
    open_now, titles = [], set()  # one per role: the same internship on its own board and on LinkedIn counts once
    for r in sorted((r for r in interns if r["active"]), key=lambda r: (":" not in (r["source"] or ""),
                                                                       -(r["posted_at"] or r["first_seen"] or 0))):
        if _role(r["title"]) not in titles:
            titles.add(_role(r["title"]))
            open_now.append(r)
    seen_roles = len({_role(r["title"]) for r in interns})
    seasons = sorted({t for r in interns for t in (db.loads(r["terms"], []) or []) if _season_key(t)[0]},
                     key=_season_key)
    cat = _catalog(ck)
    opened = [_when(r["opened_at"]) for r in db.rows("SELECT opened_at FROM firm_openings WHERE company_key=?", (ck,))]
    if cat and cat[0].get("opened"):
        opened.append(_when(cat[0]["opened"]))
    opened += [_when(r["posted_at"] or r["first_seen"]) for r in interns]
    opened = [d for d in opened if d]
    usual = Counter(d.month for d in opened).most_common(1)[0][0] if opened else None
    first_seen = min(opened) if opened else None
    tier = companies.classify(name)[0]
    reads_board = any(":" in (r["source"] or "") for r in rows)
    n = len(open_now)
    if n:
        verdict, answer = "yes", f"Yes. {name} has {n} internship{'s' if n != 1 else ''} open right now."
    elif interns:
        when = f" ({', '.join(seasons[-3:])})" if seasons else ""
        verdict, answer = "yes", f"Yes, {name} hires interns: we've seen {seen_roles} internship role" \
                                 f"{'s' if seen_roles != 1 else ''}{when}. None open right now."
    elif cat:
        verdict, answer = "yes", f"Yes, {name} is on our \"{cat[1]}\" list of companies that take interns. " \
                                 "None open right now."
    elif rows:
        verdict, answer = "unclear", f"We've seen {len(rows)} job{'s' if len(rows) != 1 else ''} from {name}, " \
                                     "but no internships yet."
    else:
        verdict, answer = "unknown", f"We haven't seen any jobs from {name} yet."
    if not n and usual:
        answer += f" Their internships usually open around {_MONTHS[usual - 1]}."
    return {"company": name, "key": ck, "verdict": verdict, "answer": answer, "tier": companies.TIERS.get(tier, ""),
            "open_now": [{"id": r["id"], "title": r["title"], "url": r["url"]} for r in open_now[:6]],
            "open_count": n, "seen": seen_roles, "seasons": seasons, "usual_month": _MONTHS[usual - 1] if usual else "",
            "first_seen": first_seen.isoformat() if first_seen else "", "watched": reads_board or bool(cat),
            "list": cat[1] if cat else ""}
