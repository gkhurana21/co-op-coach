"""Jobs → Startups in Canada (a track): startups in Waterloo Region, Toronto and the rest of Canada, ranked by how fast
they're hiring.

Which companies: YC companies located in Canada (the YC directory), the Waterloo & Toronto niche, the companies of
founders you know (People), and startups among your postings. How fast they hire: internships in Canada that opened
in the last 30 days (each once, however many boards list it), and how many roles of every kind their job boards list
now against a month ago (each sync notes it in board_roles). Everything here is computed from data already on this
computer: no AI, nothing sent anywhere.
"""

import re
import time
from urllib.parse import quote

from . import companies, contacts, db, niches, store
from .textutil import company_key

AREAS = {"waterloo": "Waterloo Region", "toronto": "Toronto", "canada": "Canada", "all": "Everywhere"}
NICHE = "waterloo-toronto"
_WATERLOO = re.compile(r"\b(waterloo|kitchener|cambridge, on|guelph)\b", re.I)
_TORONTO = re.compile(r"\b(toronto|mississauga|markham|vaughan|richmond hill|oakville|brampton|north york|"
                      r"scarborough|etobicoke|gta)\b", re.I)
_CANADA = re.compile(r"\bcanada\b|,\s*(on|bc|qc|ab|mb|sk|ns|nb|nl|pe)\b|\b(montr[eé]al|vancouver|ottawa|calgary|"
                     r"edmonton|victoria|halifax|winnipeg|qu[eé]bec)\b", re.I)
DAY = 86400
BIG = 1000  # people: past this it's a big company, not a startup (Stripe, Airbnb...)


def area_of(where: str) -> str:
    """waterloo | toronto | canada | elsewhere, for a place like "Kitchener, ON, Canada; Remote"."""
    if _WATERLOO.search(where or ""):
        return "waterloo"
    if _TORONTO.search(where or ""):
        return "toronto"
    return "canada" if _CANADA.search(where or "") else "elsewhere"


def _canadian(locations: str) -> str:
    return "; ".join(x.strip() for x in (locations or "").split(";") if _CANADA.search(x)) or ""


def _entries(pid: int) -> dict[str, dict]:
    """company_key -> {name, where, area, sources, yc, boards}: every startup the feed knows about."""
    out: dict[str, dict] = {}

    def add(name: str, source: str, where: str = "", board: str = "", yc: dict | None = None, segment: str = ""):
        ck = company_key(name)
        if not ck:
            return
        e = out.setdefault(ck, {"key": ck, "name": name, "where": "", "sources": [], "yc": None, "boards": [],
                                "segment": ""})
        if source not in e["sources"]:
            e["sources"].append(source)
        e["where"] = e["where"] or where
        e["segment"] = e["segment"] or segment
        e["yc"] = e["yc"] or yc
        if board and board not in e["boards"]:
            e["boards"].append(board)

    for c in companies.yc_all():  # YC companies located in Canada, still going
        here = _canadian(c.get("locations") or "")
        if here and c.get("status") in ("Active", None) and c.get("name"):
            add(c["name"], "yc", here, f"yc:{c['slug']}" if c.get("slug") else "", c)
    n = niches.get(pid, NICHE)
    for s in (n or {}).get("segments", []):
        for f in s["firms"]:
            if not f.get("hidden"):
                where = ", ".join(x for x in (f.get("canada"), f.get("where")) if x)
                add(f["name"], "niche", where, f.get("board") or "", segment=s["id"])
    for r in db.rows("SELECT company, title FROM contacts WHERE profile_id=? AND hidden=0 AND kind='person' AND"
                     " company IS NOT NULL AND company != ''", (pid,)):
        if contacts.role_of(r["title"] or "") == "founder":
            add(r["company"], "founder")
    for e in out.values():  # YC facts for niche firms and founders' companies too
        if not e["yc"]:
            yc = companies.yc_lookup(e["name"])
            e["yc"] = yc if yc and company_key(yc["name"]) == e["key"] else None
        if e["yc"] and e["yc"].get("slug") and f"yc:{e['yc']['slug']}" not in e["boards"]:
            e["boards"].append(f"yc:{e['yc']['slug']}")
    return out


def _roles(boards: set[str], now: int) -> dict[str, tuple[int, int | None, int]]:
    """board -> (roles listed now, roles about a month ago or None, days of history)."""
    if not boards:
        return {}
    month_ago = time.strftime("%Y-%m-%d", time.localtime(now - 30 * DAY))
    rows = db.rows("SELECT board, day, roles FROM board_roles WHERE day >= ? ORDER BY day", (
        time.strftime("%Y-%m-%d", time.localtime(now - 40 * DAY)),))
    hist: dict[str, list[tuple[str, int]]] = {}
    for r in rows:
        if r["board"] in boards:
            hist.setdefault(r["board"], []).append((r["day"], r["roles"]))
    out = {}
    for b, h in hist.items():
        then = next((roles for day, roles in h if day >= month_ago), None) if len(h) > 1 else None
        days = (time.mktime(time.strptime(h[-1][0], "%Y-%m-%d")) - time.mktime(time.strptime(h[0][0], "%Y-%m-%d")))
        out[b] = (h[-1][1], then, int(days // DAY))
    return out


def feed(pid: int, area: str = "waterloo", yc_only: bool = False, known: bool = False, hiring: bool = False,
         q: str = "", min_funding: int = 0, limit: int = 150) -> dict:
    """The startups, fastest-hiring first, with what's open to you, who you know there and what they've raised."""
    now = db.now()
    entries = _entries(pid)
    for e in entries.values():
        e["area"] = area_of(e["where"]) if e["where"] else ("canada" if e["segment"] == "canada" else
                                                            e["segment"] if e["segment"] in AREAS else "elsewhere")
    # your postings: what's open at each company now, and YC startups among them hiring in Canada ("startup" on a
    # posting alone is too loose: listed companies and job aggregators carry it too)
    jobs: dict[str, list[dict]] = {}
    for j in store.scored_jobs(pid):
        jobs.setdefault(j["company_key"], []).append(j)
        if j["company_key"] not in entries and "Canada" in (j.get("regions") or []):
            yc = companies.yc_lookup(j["company"])
            if yc and company_key(yc["name"]) == j["company_key"]:  # the same name, not a near one ("Yotta Labs")
                where = "; ".join(loc for loc in j.get("locations") or [] if _CANADA.search(loc))
                entries[j["company_key"]] = {"key": j["company_key"], "name": j["company"], "where": where,
                                             "area": area_of(where), "sources": ["postings"], "yc": yc,
                                             "boards": [f"yc:{yc['slug']}"] if yc.get("slug") else [], "segment": ""}
    since = now - 30 * DAY
    new30: dict[str, int] = {}
    for ck, js in jobs.items():  # each internship once (the same one on two boards is one), opened this month, in Canada
        k = sum(1 for j in js if "Canada" in (j.get("regions") or []) and
                (j.get("opened_at") or j.get("posted_at") or (j["first_seen"] if j["fresh"] else 0)) >= since)
        if k:
            new30[ck] = k
    roles = _roles({b for e in entries.values() for b in e["boards"]}, now)
    watched = {f"{r['ats']}:{r['slug']}".lower() for r in db.rows(
        "SELECT ats, slug FROM companies WHERE enabled=1 AND removed=0")}
    people = contacts.at_companies(pid)
    founders: dict[str, list[str]] = {}
    for r in db.rows("SELECT name, title, company_key FROM contacts WHERE profile_id=? AND hidden=0 AND kind='person'"
                     " AND company_key IS NOT NULL", (pid,)):
        if contacts.role_of(r["title"] or "") == "founder":
            founders.setdefault(r["company_key"], []).append(r["name"])
    fund = contacts.funding(set(entries))
    alumni = contacts.alumni_search(pid, "{company}").replace(quote("{company}"), "{company}")  # their school, once
    q = (q or "").strip().lower()
    counts = {a: 0 for a in AREAS}
    out = []
    for ck, e in entries.items():
        yc = e["yc"] or {}
        f = fund.get(ck)
        if yc.get("status") in ("Public", "Acquired", "Inactive") or ((f or {}).get("last_round") or "").lower() in (
                "public", "acquired") or (yc.get("team_size") or 0) >= BIG:
            continue
        open_jobs = [j for j in jobs.get(ck, [])]
        mine = [j for j in open_jobs if j["eligibility"] != "no"]
        best = max(mine or open_jobs, key=lambda j: j["score"], default=None)
        now_roles = then_roles = None
        history = 0
        for b in e["boards"]:
            if b in roles:
                r_now, r_then, days = roles[b]
                now_roles = (now_roles or 0) + r_now
                if r_then is not None:
                    then_roles = (then_roles or 0) + r_then
                history = max(history, days)
        growth = now_roles - then_roles if now_roles is not None and then_roles is not None and history >= 7 else None
        n30 = new30.get(ck, 0)
        speed = n30 * 3 + max(growth or 0, 0) + min(len(open_jobs), 10) + (2 if yc.get("is_hiring") else 0) + \
            (2 if mine else 0)
        row = {
            "key": ck, "name": e["name"], "where": e["where"], "area": e["area"], "sources": e["sources"],
            "yc": yc.get("batch") or "", "team": yc.get("team_size"), "one_liner": yc.get("one_liner") or "",
            "industry": yc.get("industry") or "", "logo": yc.get("logo") or "", "website": yc.get("website") or "",
            "yc_hiring": bool(yc.get("is_hiring")), "yc_url": f"https://www.ycombinator.com/companies/{yc['slug']}/jobs"
            if yc.get("slug") else "",
            "open": len(open_jobs), "open_to_you": len(mine), "new30": n30,
            "best": {"id": best["id"], "title": best["title"], "score": best["score"], "url": best["url"]} if best else None,
            "roles": now_roles, "growth": growth, "history_days": history,
            "people": people.get(ck, 0), "founders": founders.get(ck, [])[:3],
            "funding": {k: f[k] for k in ("found", "total_usd", "last_round", "last_date", "sources")} if f else None,
            "watched": any(b.lower() in watched for b in e["boards"]), "can_watch": bool(e["boards"]),
            "alumni": alumni.replace("{company}", quote(e["name"])),
            "speed": speed,
            "pace": "fast" if n30 >= 3 or (growth or 0) >= 5 else "hiring" if open_jobs or yc.get("is_hiring") or
            (now_roles or 0) > 0 else "quiet",
        }
        if q and q not in f"{row['name']} {row['one_liner']} {row['industry']} {row['where']}".lower():
            continue
        if yc_only and not row["yc"]:
            continue
        if known and not (row["people"] or row["founders"]):
            continue
        if hiring and row["pace"] == "quiet":
            continue
        if min_funding and not (f and f["found"] and (f["total_usd"] or 0) >= min_funding):
            continue
        counts["all"] += 1
        if row["area"] in ("waterloo", "toronto"):
            counts[row["area"]] += 1
        if row["area"] != "elsewhere":
            counts["canada"] += 1
        if area == "all" or row["area"] == area or (area == "canada" and row["area"] != "elsewhere"):
            out.append(row)
    out.sort(key=lambda r: (-r["speed"], -r["open_to_you"], -(r["roles"] or 0), r["name"].lower()))
    return {"area": area, "areas": AREAS, "counts": counts, "startups": out[:limit], "total": len(out),
            "history_days": max((r["history_days"] for r in out), default=0)}


def watch(pid: int, name: str) -> dict:
    """Have the sync read a startup's job boards (its YC jobs page, or the board the niche knows)."""
    ck = company_key(name)
    e = _entries(pid).get(ck)
    if not e or not e["boards"]:
        return {"error": f"No job board known for {name}: open their careers page instead."}
    added = 0
    with db.tx() as c:
        for b in e["boards"]:
            ats, slug = b.split(":", 1)
            row = c.execute("SELECT removed, enabled FROM companies WHERE ats=? AND lower(slug)=lower(?)",
                            (ats, slug)).fetchone()
            if row:
                if row["removed"] or not row["enabled"]:
                    c.execute("UPDATE companies SET removed=0, enabled=1 WHERE ats=? AND lower(slug)=lower(?)", (ats, slug))
                    added += 1
                continue
            c.execute("INSERT INTO companies (ats, slug, name, tier, origin) VALUES (?,?,?,?, 'startups')",
                      (ats, slug, e["name"], "yc" if ats == "yc" else "startup"))
            added += 1
    return {"ok": True, "name": e["name"], "boards": e["boards"], "added": added}
