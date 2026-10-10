"""Heuristic job-to-student match score (0-100) with human-readable reasons.

Fast enough to run over every posting on each request, so changing preferences re-ranks instantly.
When Claude has scored a posting, the final score blends both (see blend()).
"""

import re
import time

from .textutil import skill_categories

SUPPORT_RE = re.compile(r"help ?desk|service desk|desktop support|it support|technical support|technician|"
                        r"data entry|administrative|receptionist", re.I)
_SEASON_START = {"Winter": 1, "Spring": 5, "Summer": 5, "Fall": 9}


def _term_started(term: str, now: float) -> bool:
    m = re.fullmatch(r"(Winter|Spring|Summer|Fall) (20\d\d)", term)
    if not m:
        return False
    t = time.localtime(now)
    return (int(m.group(2)), _SEASON_START[m.group(1)]) <= (t.tm_year, t.tm_mon)

DEFAULT_PREFS = {
    "categories": [],          # empty -> use the resume's inferred target categories
    "locations": ["Canada", "Remote"],
    "terms": [],               # e.g. ["Winter 2027", "Summer 2027"]
    "aim": "balanced",         # balanced | yc | startup | bigcorp
    "citizenship": "Canadian",  # work authorization: US | Canadian | F1 | Intl-CA | Other (see WORK_AUTH)
    "us_open": True,           # non-US students: count US roles (J-1/TN/F-1 CPT-OPT or remote) as reachable
    "exclude_keywords": [],
    "notify": {"discord_webhook": "", "ntfy_topic": "", "email": "", "min_score": None},
    "track_email": True,  # read connected inboxes for application updates
}

AIM_BOOST = {
    "yc": {"yc": 12, "startup": 6},
    "startup": {"yc": 10, "startup": 10},
    "bigcorp": {"bigtech": 12, "quant": 10, "finance": 10},
    "balanced": {},
}
REGIONS = {"Canada", "US", "Remote", "Other"}
WORK_AUTH = {
    "US": "US citizen / permanent resident",
    "Canadian": "Canadian citizen / PR",
    "F1": "International student in the US (F-1: CPT/OPT)",
    "Intl-CA": "International student in Canada",
    "Other": "Other / will need sponsorship",
}
_HOME = {"US": "US", "F1": "US", "Canadian": "Canada", "Intl-CA": "Canada"}


def merged_prefs(prefs: dict | None) -> dict:
    p = {**DEFAULT_PREFS, **(prefs or {})}
    p["notify"] = {**DEFAULT_PREFS["notify"], **((prefs or {}).get("notify") or {})}
    return p


def score(job: dict, profile: dict, prefs: dict, now: float | None = None) -> tuple[int, list[dict]]:
    """Return (score, reasons). Each reason is {"t": text, "k": "good"|"bad"|"info"}."""
    now = now or time.time()
    reasons: list[dict] = []
    p_skills = set(profile.get("skills") or [])
    cats = prefs.get("categories") or profile.get("target_categories") or []
    flags = set(job.get("flags") or [])

    # role fit (35)
    cat = job.get("category") or "Other"
    if not cats:
        role = 22
    elif cat in cats:
        role = 35
        reasons.append({"t": f"{cat} role", "k": "good"})
    elif cat == "Other":
        role = 14
    else:
        aff = sum(1 for s in p_skills if cat in skill_categories(s))
        role = 18 if aff >= 3 else 8
        reasons.append({"t": f"{cat} (outside your targets)", "k": "info"})

    # skills (20)
    j_skills = set(job.get("skills") or [])
    if j_skills and p_skills:
        hit = j_skills & p_skills
        sk = round(20 * min(1.0, 1.3 * len(hit) / len(j_skills)))
        if hit:
            reasons.append({"t": "Skills: " + ", ".join(sorted(hit)[:4]), "k": "good"})
        missing = sorted(j_skills - p_skills)
        if missing and len(missing) >= len(hit):
            reasons.append({"t": "Asks for " + ", ".join(missing[:3]), "k": "info"})
    elif p_skills:
        aff = sum(1 for s in p_skills if cat in skill_categories(s))
        sk = 14 if aff >= 3 else 10 if aff else 6
    else:
        sk = 10

    # location (15)
    regions = set(job.get("regions") or [])
    # "Remote, US" usually means US work authorization; don't let it satisfy a Canadian's "Remote"
    # preference (and vice versa)
    auth = prefs.get("citizenship") or "Canadian"
    if auth.upper() in ("US", "AMERICAN", "US CITIZEN"):
        auth = "US"
    us_person = auth == "US"
    us_student = auth == "F1"  # already in the US; internships run on CPT/OPT without employer sponsorship
    us_open = (prefs.get("us_open", True) or us_student) and not us_person
    home = _HOME.get(auth)
    if home and "Remote" in regions and len(regions) == 2 and ({"US", "Canada"} & regions) and home not in regions:
        regions = regions - {"Remote"}  # "Remote, US" usually means remote within the US
    want = [l for l in prefs.get("locations") or [] if l]
    locs_text = " ".join(job.get("locations") or []).lower()
    if not want or "Anywhere" in want:
        loc = 11
    else:
        want_regions = {w for w in want if w in REGIONS}
        want_cities = [w.lower() for w in want if w not in REGIONS]
        if any(c in locs_text for c in want_cities):
            loc = 15
            reasons.append({"t": next(w for w in want if w.lower() in locs_text), "k": "good"})
        elif regions <= {"Unknown"}:
            loc = 9
        elif regions & want_regions:
            loc = 13 if want_cities else 15
            r = sorted(regions & want_regions)[0]
            reasons.append({"t": r if r != "Remote" else "Remote OK", "k": "good"})
        elif us_open and "US" in regions:
            # the US market is open to international students: F-1 CPT/OPT, J-1/TN for co-ops, or remote
            loc = 15 if us_student else 13
            reasons.append({"t": "🇺🇸 US role" + (" (CPT/OPT)" if us_student else ""), "k": "info"})
        else:
            loc = 0
            reasons.append({"t": "Outside your locations", "k": "bad"})

    # term (15)
    j_terms = [t for t in job.get("terms") or [] if not t.endswith("month")]
    want_terms = prefs.get("terms") or []
    if not want_terms:
        term = 10
    elif not j_terms:
        term = 8
    elif set(j_terms) & set(want_terms):
        term = 15
        reasons.append({"t": sorted(set(j_terms) & set(want_terms))[0], "k": "good"})
    else:
        term = 2
        reasons.append({"t": f"{j_terms[0]} term", "k": "bad"})

    # freshness (10)
    posted = job.get("posted_at") or (job.get("first_seen") if job.get("fresh") else None)
    age_days = (now - posted) / 86400 if posted else None
    if age_days is None:
        fresh = 4
    elif age_days <= 2:
        fresh = 10
        reasons.append({"t": "Just posted", "k": "good"})
    elif age_days <= 7:
        fresh = 7
    elif age_days <= 21:
        fresh = 4
    else:
        fresh = 1

    total = role + sk + loc + term + fresh
    base = total

    # postings whose only listed terms have already started are usually stale
    if j_terms and all(_term_started(t, now) for t in j_terms):
        total -= 15
        reasons.append({"t": "Term already started", "k": "bad"})

    # help-desk / IT-support style roles rarely move the needle for experienced students
    if SUPPORT_RE.search(job.get("title") or "") and \
            ((profile.get("coop_terms_completed") or 0) >= 1 or (profile.get("year_of_study") or 0) >= 3):
        total -= 12
        reasons.append({"t": "Support/helpdesk role", "k": "info"})

    # what they're aiming for
    boost = AIM_BOOST.get(prefs.get("aim") or "balanced", {}).get(job.get("tier") or "other", 0)
    total += boost

    # who the posting is open to (where you live, citizenship, degree), read from the full text and the form
    from . import eligibility
    reqs = job.get("requirements") or []
    elig = eligibility.evaluate(reqs, flags, profile, prefs)
    restricted = any(r["kind"] == "residency" for r in reqs)  # posting-specific rules beat company-level hunches

    # visa reality for international students looking at US roles
    if "US" in regions and not us_person and (us_open or "US" in want) and not (home == "Canada" and "Canada" in regions) \
            and elig["verdict"] != "no":
        if "us_citizen" in flags:
            pass  # handled below as a hard blocker
        elif "sponsor_yes" in flags:
            total += 5
            reasons.append({"t": "Sponsors visas / welcomes international students", "k": "good"})
        elif "no_sponsor" in flags:
            if us_student:  # "no sponsorship" is about H-1B; CPT/OPT internships usually don't need it
                total -= 3
                reasons.append({"t": "Says no sponsorship (CPT/OPT internships usually still OK)", "k": "info"})
            else:
                total -= 12
                reasons.append({"t": "Says no visa sponsorship (J-1 may still work: ask)", "k": "bad"})
        elif job.get("intl_friendly") and not restricted:
            total += 4
            reasons.append({"t": "Often hires international interns", "k": "good"})
        elif job.get("tier") in ("yc", "startup") and not us_student:
            total += 2
            reasons.append({"t": "US startup: ask about visa or remote", "k": "info"})

    # hard blockers / soft adjustments
    degree = profile.get("degree_level") or "Bachelor's"
    year = profile.get("year_of_study") or 0
    if "phd" in flags and degree != "PhD":
        total -= 35
    elif "masters" in flags and degree not in ("Master's", "PhD"):
        total -= 20
    citizenship_block = "us_citizen" in flags and not us_person
    if citizenship_block:
        total -= 40
    if elig["verdict"] == "fit":
        total += 4
    elif elig["verdict"] == "check":
        total -= 5
    if "not_intern" in flags:
        total -= 30
        reasons.append({"t": "Full-time role", "k": "bad"})
    if "early_program" in flags:
        if year and year <= 2:
            total += 6
            reasons.append({"t": "Early-year program", "k": "good"})
        elif year >= 3:
            total -= 15
            reasons.append({"t": "For 1st/2nd years", "k": "bad"})
    if "coop" in flags and "Canada" in regions:
        total += 3
    excl = [k.lower() for k in prefs.get("exclude_keywords") or [] if k]
    if excl and any(k in (job.get("title") or "").lower() for k in excl):
        total -= 50
        reasons.append({"t": "Matches an excluded keyword", "k": "bad"})

    if citizenship_block:  # citizenship / clearance / ITAR: not a stretch, simply not possible
        total = min(total, 15)
    if elig["verdict"] == "no":  # e.g. "US based applicants only" for someone in Canada
        total = min(total, 20)
    reasons = elig["reasons"] + reasons  # eligibility first: it's what decides whether to apply at all
    final = max(0, min(100, round(total)))
    reasons.append({"t": "parts", "k": "parts", "v": {"Role": role, "Skills": sk, "Location": loc, "Term": term,
                                                       "Freshness": fresh, "Adjustments": final - base}})
    reasons.append({"t": elig["verdict"], "k": "elig", "v": elig["verdict"]})
    reasons += [{"t": n, "k": "note"} for n in elig["notes"]]
    return max(0, min(100, round(total))), reasons


def blend(heuristic: int, ai_fit: int | None) -> int:
    if ai_fit is None:
        return heuristic
    return max(0, min(100, round(0.35 * heuristic + 0.65 * ai_fit)))
