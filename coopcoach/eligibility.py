"""Who a posting is actually open to, and whether a given student can apply.

Requirements come from the whole description (eligibility lines usually sit at the very end, after the pitch) and,
for Greenhouse boards, the application form itself ("What U.S. State do you currently reside in?"). The verdict:
  "no"    - the posting rules this student out (US-based applicants only, citizens only, PhD only...)
  "fit"   - the posting explicitly wants someone like them ("Canada based applicants only" for a student in Canada)
  "check" - something worth confirming before applying
  "ok"    - nothing either way
A graduation window ("graduate by June 2029") is only ever a note: dates shift and plenty of students finish early.
"""

import re

_US = r"(?:US|U\.S\.?|USA|U\.S\.A\.|United States|America)"
_CA = r"(?:Canada|Canadian)"
_BASED = r"(?:based|located|residing|living)"

RESIDENCY = [
    ("US", re.compile(
        rf"\b{_US}[- ]{_BASED} (?:applicants|candidates|students|residents)(?: only)?|"
        rf"(?:applicants|candidates) (?:must|need to|should) (?:currently )?(?:be |reside |live )(?:{_BASED} )?in the {_US}|"
        rf"must (?:currently )?(?:reside|live|be located|be based) (?:with)?in the {_US}|"
        rf"(?:open|available|limited) (?:only )?to (?:candidates|applicants|students) {_BASED} in the {_US}|"
        rf"(?:only|exclusively) (?:considering|accepting) (?:candidates|applicants) (?:{_BASED} )?in the {_US}|"
        rf"(?:fully )?remote (?:work )?(?:within|in) (?:approved |eligible )?(?:{_US}|U\.S\.) (?:locations|states)", re.I)),
    ("Canada", re.compile(
        rf"\b{_CA}[- ]{_BASED} (?:applicants|candidates|students|residents)(?: only)?|"
        rf"(?:applicants|candidates) (?:must|need to) (?:currently )?(?:be |reside |live )(?:{_BASED} )?in {_CA}|"
        rf"must (?:currently )?(?:reside|live|be located|be based) in {_CA}|"
        rf"(?:open|available|limited) (?:only )?to (?:candidates|applicants|students) {_BASED} in {_CA}", re.I)),
]
_FORM_RESIDENCY = [
    ("US", re.compile(r"what (?:u\.?s\.?|united states) state do you (?:currently )?(?:reside|live)|"
                      r"which (?:u\.?s\.?|us) state (?:do you|are you)", re.I)),
    ("Canada", re.compile(r"which (?:canadian )?province (?:or territory )?(?:do you|are you)|"
                          r"province of residence", re.I)),
]
CA_CITIZEN = re.compile(rf"(?:must be|only) (?:a )?{_CA} citizens?(?: or permanent residents?)?|"
                        rf"{_CA} citizens? (?:or|and) permanent residents? only|"
                        rf"(?:open|available) only to {_CA} citizens", re.I)
_SPONSOR_Q = re.compile(r"require (?:employer )?sponsorship|sponsorship (?:or other|for) (?:employer|work)", re.I)
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
GRAD_BY = re.compile(rf"(?:graduat\w*|obtain\w*|complet\w*|earn\w*)[^.\n]{{0,60}}?(?:by|before|no later than)\s+"
                     rf"((?:{_MONTHS})[a-z]*\.?\s+\d{{4}}|\d{{4}})", re.I)
GRAD_BETWEEN = re.compile(rf"graduat\w*[^.\n]{{0,30}}?between\s+((?:{_MONTHS})[a-z]*\.?\s+\d{{4}})\s+(?:and|-|–)\s+"
                          rf"((?:{_MONTHS})[a-z]*\.?\s+\d{{4}})", re.I)


def _sentence(text: str, m: re.Match) -> str:
    start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start()), text.rfind("•", 0, m.start())) + 1
    ends = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
    return re.sub(r"\s+", " ", text[start:min(ends) if ends else m.end() + 80]).strip(" •-")[:200]


def extract(title: str, description: str, questions: list[str] | None = None) -> list[dict]:
    """Requirements stated in the posting (and its application form)."""
    text = f"{title}\n{description or ''}"
    out: list[dict] = []
    seen = set()

    def add(kind, value, quote, source="posting"):
        if (kind, value) not in seen:
            seen.add((kind, value))
            out.append({"kind": kind, "value": value, "text": quote, "source": source})

    for country, rx in RESIDENCY:
        m = rx.search(text)
        if m:
            add("residency", country, _sentence(text, m))
    for q in questions or []:
        for country, rx in _FORM_RESIDENCY:
            if rx.search(q):
                add("residency", country, f"Application asks: “{q.strip()[:140]}”", "form")
        if _SPONSOR_Q.search(q):
            add("sponsorship_question", "", f"Application asks: “{q.strip()[:140]}”", "form")
    m = CA_CITIZEN.search(text)
    if m:
        add("ca_citizen", "", _sentence(text, m))
    m = GRAD_BETWEEN.search(text)
    if m:
        add("grad_window", f"{m.group(1)} – {m.group(2)}", _sentence(text, m))
    else:
        m = GRAD_BY.search(text)
        if m:
            add("grad_window", f"by {m.group(1)}", _sentence(text, m))
    return out


_MON = {m: i for i, m in enumerate(_MONTHS.split("|"), 1)}


def _when(s: str) -> tuple[int, int] | None:
    m = re.search(rf"(?:({_MONTHS})[a-z]*\.?\s+)?(\d{{4}})", s or "", re.I)
    return (int(m.group(2)), _MON[m.group(1).lower()[:3]] if m.group(1) else 12) if m else None


def evaluate(reqs: list[dict], flags: set, profile: dict, prefs: dict) -> dict:
    """{"verdict": "no" | "fit" | "check" | "ok", "reasons": [{"t", "k"}], "notes": [...]}"""
    from .matching import _HOME
    auth = (prefs.get("citizenship") or "Canadian")
    auth = "US" if auth.upper() in ("US", "AMERICAN", "US CITIZEN") else auth
    home = _HOME.get(auth) or ("US" if auth == "US" else None)
    reasons, notes, verdict = [], [], "ok"

    def bump(v):
        nonlocal verdict
        order = {"ok": 0, "fit": 1, "check": 2, "no": 3}
        if order[v] > order[verdict]:
            verdict = v

    residency = [r for r in reqs if r["kind"] == "residency"]
    countries = {r["value"] for r in residency}
    for r in residency:
        if home and r["value"] == home:
            reasons.append({"t": f"Open to {r['value']}-based applicants: that's you", "k": "good"})
            bump("fit")
        elif home and home not in countries:
            where = "the US" if r["value"] == "US" else r["value"]
            reasons.append({"t": f"Not eligible: only for applicants based in {where}"
                            + (" (asked on the application)" if r["source"] == "form" else ""), "k": "bad"})
            bump("no")
        elif not home:
            reasons.append({"t": f"Only for applicants based in {r['value']}: check you qualify", "k": "info"})
            bump("check")
    if "us_citizen" in flags and auth != "US":
        reasons.append({"t": "Not eligible: US citizens / clearance only", "k": "bad"})
        bump("no")
    if any(r["kind"] == "ca_citizen" for r in reqs) and auth not in ("Canadian", "US"):
        reasons.append({"t": "Canadian citizens or permanent residents only", "k": "bad"})
        bump("no")
    degree = profile.get("degree_level") or "Bachelor's"
    if "phd" in flags and degree != "PhD":
        reasons.append({"t": "Not eligible: PhD students only", "k": "bad"})
        bump("no")
    elif "masters" in flags and degree not in ("Master's", "PhD"):
        reasons.append({"t": "Aimed at Master's students", "k": "bad"})
        bump("check")
    if any(r["kind"] == "sponsorship_question" for r in reqs) and home == "Canada" and "US" in countries | {""} and \
            verdict != "no" and auth != "US":
        notes.append("The application asks whether you'll need visa sponsorship")
    for r in reqs:  # graduation windows are a note only: dates move and students finish early
        if r["kind"] == "grad_window":
            mine = _when(profile.get("grad_date") or str(profile.get("grad_year") or ""))
            limit = _when(r["value"].split("–")[-1])
            if mine and limit and mine > limit:
                notes.append(f"Wants graduation {r['value']} (your resume says {profile.get('grad_date') or profile.get('grad_year')})")
            else:
                notes.append(f"Graduation {r['value']}")
    return {"verdict": verdict, "reasons": reasons, "notes": notes}


_LOC_WORDS = re.compile(r"\((?:[^)]*)\)|\b(?:usa|us|u\.s\.|canada|toronto|waterloo|vancouver|montreal|ottawa|new york|nyc|"
                        r"san francisco|sf|seattle|remote|hybrid|dublin|london|zurich|bay area|ny|ca|on)\b|"
                        r"\b20\d\d\b|[-–|,/]", re.I)


def core_title(title: str) -> str:
    """'Software Engineer Intern 2027 (USA)' and 'Software Engineering Intern 2027 (Toronto)' -> same core."""
    t = _LOC_WORDS.sub(" ", (title or "").lower())
    t = re.sub(r"\bengineering\b", "engineer", t)
    t = re.sub(r"\binternship\b", "intern", t)
    return " ".join(sorted(set(re.findall(r"[a-z+#]+", t)) - {"the", "and", "of", "for", "summer", "fall", "winter"}))
