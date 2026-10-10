"""Resume Studio: an editable, structured resume plus a line-by-line checker, rewrite suggestions and
per-job tailoring. Everything here is deterministic (no AI needed); ai.py adds optional Claude rewrites.

Resume shape:
{
  "header": {"name", "email", "phone", "location", "links": [..]},
  "education": [{"school", "degree", "location", "dates", "details": [..]}],
  "experience": [{"title", "company", "location", "dates", "bullets": [..]}],
  "projects": [{"name", "tech", "dates", "link", "bullets": [..]}],
  "skills": [{"label", "items"}],
  "extras": [{"heading", "items": [..]}],
  "section_order": ["education", "experience", "projects", "skills", "extras"]
}
"""

import copy
import re

from . import resume as rp
from .resume_checker import (  # noqa: F401 (moved; still reachable here)
    _USED_TO,
    _VERB_FOR,
    _WEAK_PREFIX,
    _WORD,
    BUZZWORDS,
    CHARS_PER_LINE,
    DANGLING,
    DATE_FORMATS,
    FILLER,
    HOW,
    IRREGULAR_PAST,
    METRIC,
    MONTHS,
    PRESENT_FORMS,
    PROFICIENCY,
    PRONOUNS,
    SOFT_SKILLS,
    STRONG_VERBS,
    TITLE_WORDS,
    WEAK_OPENERS,
    _bullets,
    _dict_cache,
    _dictionary,
    _first_word,
    _fix_preview,
    _get,
    _is_past,
    _known,
    _past,
    _replace_word,
    _resume_names,
    _set,
    _suggest,
    _tidy,
    apply_fix,
    apply_fixes,
    estimate_lines,
    lint,
    proofread,
    rule_review,
    suggest_rewrites,
    unglue_project,
)
from .resume_compare import (  # noqa: F401 (moved; still reachable here)
    _ENTRY_FIELDS,
    _SECTION_LABEL,
    _entry_key,
    _lines_overlap,
    _match,
    _sim,
    apply_compare,
    compare,
)
from .textutil import detect_skills

SECTIONS = ["education", "experience", "projects", "skills", "extras"]
# How the resume looks. Colours come from a palette (or any hex the person picks); contact items show as icon or
# emoji + a clickable label ("GitHub") instead of raw URLs, like most polished student resumes.
STYLE_DEFAULTS = {"accent": "#000000", "link_color": "#000000", "contact": "icons", "link_icons": True,
                  "company_first": False, "one_page": True}
PALETTE = [("Classic black", "#000000"), ("Navy", "#1f3a68"), ("Ocean", "#0b5c8a"), ("Teal", "#0f6e73"),
           ("Forest", "#1f6f50"), ("Waterloo gold", "#8a6a00"), ("Burgundy", "#7a1f2b"), ("Crimson", "#b4232f"),
           ("Royal purple", "#4338ca"), ("Plum", "#6b2d6b"), ("Slate", "#334155"), ("Charcoal", "#2b2b2b")]
LINK_LABELS = [("linkedin.com", "LinkedIn"), ("github.com", "GitHub"), ("gitlab.com", "GitLab"), ("devpost.com", "Devpost"),
               ("leetcode.com", "LeetCode"), ("kaggle.com", "Kaggle"), ("x.com/", "X"), ("twitter.com", "Twitter"),
               ("medium.com", "Medium"), ("behance.net", "Behance"), ("dribbble.com", "Dribbble"),
               ("scholar.google", "Scholar"), ("youtube.com", "YouTube")]


def link_label(url: str) -> tuple[str, str]:
    """(label, kind) for a header link: ("GitHub", "github"), ("LinkedIn", "linkedin"), else ("Portfolio", "web")."""
    u = rp.short_url(url).lower()
    for host, label in LINK_LABELS:
        if u.startswith(host) or ("." + host) in u:
            return label, label.lower()
    return "Portfolio", "web"


TEMPLATES = {
    "jake": "Jake's (classic CS)",
    "modern": "Modern (sans-serif)",
    "classic": "Traditional (banks / corporate)",
}

# --- building a structured resume ---------------------------------------------------------

_PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_LOCATION_RE = re.compile(r"\b([A-Z][a-zA-Z.' -]+,\s*(?:ON|BC|QC|AB|MB|SK|NS|NB|NL|PE|[A-Z]{2}))\b")
_LINK_TOKEN = re.compile(r"(?:https?://)?(?:www\.)?((?:linkedin\.com/in|github\.com|gitlab\.com)/[\w./-]+|"
                         r"[\w-]+\.(?:dev|io|me|ca|com|app|xyz)(?:/[\w./-]*)?)", re.I)


# a link label glued onto the end of a heading ("Next.jsLive Demo") or after a separator ("... | GitHub")
_LINK_LABEL = re.compile(r"(?:(?<=[^\s|])|\s*[|·]\s*)(?:Live Demo|Demo|GitHub|Source Code|Code|Link|Website|Devpost|"
                         r"Video|Paper|App Store|Play Store)\s*$")
_TECH_TAIL = ["C++", "C#", "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust", "Swift", "Kotlin", "React",
              "Next.js", "Node.js", "SQL", "PyTorch", "TensorFlow", "Flask", "FastAPI", "Django", "Ruby", "Scala", "R"]


def split_project_head(head: str) -> tuple[str, str]:
    """"PriceCore: Pricing Engine | C++, Python" -> (name, tech). Also handles PDFs that glue the two together
    ("Pricing EngineC++, Python, FastAPI")."""
    head = _LINK_LABEL.sub("", head.strip())
    if "|" in head:
        name, _, tech = head.partition("|")
        return name.strip(" -–|"), _LINK_LABEL.sub("", tech.strip(" -–|"))
    parts = head.split(",")
    if len(parts) >= 3:
        first = parts[0]
        for t in sorted(_TECH_TAIL, key=len, reverse=True):
            i = len(first) - len(t)
            if first.endswith(t) and i > 3 and (first[i - 1].islower() or first[i - 1] in ")"):
                return first[:i].strip(" -–:"), (t + "," + ",".join(parts[1:])).strip()
    return head, ""


parse_exp_head = rp.parse_exp_head
_PLACE = rp._PLACE


def _upload_order(secs: dict) -> list[str]:
    """Keep the section order of the uploaded resume (it's theirs; Resume Studio can reorder it)."""
    order = []
    for k in secs:
        sec = k if k in SECTIONS else "extras" if k not in ("header", "summary", "profile", "objective") else None
        if sec and sec not in order:
            order.append(sec)
    return order if {"experience", "education"} <= set(order) else []


def _merge_label_lines(lines: list[str]) -> list[str]:
    """"Honors & Awards:" + "Scholarship ($50,000);" + "Euclid Distinction;" + "BMC Certification" -> one line."""
    out: list[str] = []
    joining = False
    for ln in lines:
        if out and (out[-1].endswith(":") or (joining and out[-1].endswith(";"))):
            out[-1] = f"{out[-1]} {ln}"
            joining = True
        else:
            out.append(ln)
            joining = False
    return out


def _split_skill_line(ln: str) -> dict | None:
    if ":" in ln:
        label, items = ln.split(":", 1)
        if len(label) <= 30:
            return {"label": label.strip(), "items": items.strip()}
    return None


def build_from_text(text: str, profile: dict | None = None) -> dict:
    """Best-effort structure from raw resume text (the user fixes the rest in the editor)."""
    profile = profile or {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    secs = rp._sections(lines)
    header_text = " ".join(secs.get("header", [])[:6])
    no_email = rp._EMAIL_RE.sub(" ", header_text)
    email = rp._EMAIL_RE.search(header_text) or rp._EMAIL_RE.search(text[:600])
    phone = _PHONE_RE.search(header_text)
    loc = _LOCATION_RE.search(no_email)

    education = []
    edu_lines = secs.get("education", [])
    if edu_lines:
        school = profile.get("university") or edu_lines[0]
        dates = ""
        for ln in edu_lines[:3]:
            m = rp._DATE_RANGE_RE.search(ln)
            if m:
                dates = m.group(0)
                break
        degree = next((ln for ln in edu_lines if rp._DEGREE_RE.search(ln) or
                       (profile.get("program") and profile["program"].lower() in ln.lower())), "")
        degree = rp._DATE_RANGE_RE.sub("", degree).strip(" |–—-,")
        if school and degree.lower().startswith(school.lower()):
            degree = degree[len(school):].strip(" |–—-,")
        details = [ln for ln in edu_lines if ln not in (edu_lines[0], degree) and not ln.startswith(school or "\0")]
        details = [rp._DATE_RANGE_RE.sub("", d).strip(" |–—-,") for d in details]
        place = next((seg.strip() for ln in edu_lines[:2] for seg in re.split(r"\s+\|\s+", ln)
                      if _PLACE.match(seg.strip())), "")
        if school and " | " in school:  # "University of Waterloo | Sept 2024 – Aug 2029 | Waterloo, ON"
            school = school.split(" | ")[0].strip()
        details = _merge_label_lines([d for d in details if d and d != degree and d != place])
        education.append({"school": school, "degree": degree or profile.get("program", ""), "location": place,
                          "dates": dates, "details": details[:5]})

    experience = []
    company_first = 0
    for e in rp._entries(secs.get("experience", []), dated=True):
        h = parse_exp_head(e["head"])
        company_first += 1 if h.pop("company_first", False) else -1
        experience.append({**h, "link": "", "bullets": e["bullets"][:8]})

    projects = []
    for e in rp._entries(secs.get("projects", []), dated=False):
        head = " | ".join(e["head"])
        dr = rp._DATE_RANGE_RE.search(head)
        name, tech = split_project_head(rp._DATE_RANGE_RE.sub("", head).strip(" -–|"))
        projects.append({"name": name, "tech": tech, "dates": dr.group(0) if dr else "", "link": "", "demo": "",
                         "bullets": e["bullets"][:6]})

    skills = []
    for ln in secs.get("skills", []):
        s = _split_skill_line(ln)
        if s:
            skills.append(s)
        elif skills and ln.strip():  # a skills line wrapped onto the next line ("..., GraphQL," + "Tailwind CSS")
            skills[-1]["items"] = (skills[-1]["items"].rstrip() + " " + rp._BULLET_RE.sub("", ln).strip()).strip()
    if not skills and profile.get("skills"):
        skills = [{"label": "Technical", "items": ", ".join(profile["skills"])}]

    extras = []
    for key in ("leadership", "extracurriculars", "extracurricular", "activities", "awards", "honours", "honors",
                "certifications", "volunteer", "volunteering"):
        if secs.get(key):
            extras.append({"heading": key.title(), "items": [rp._BULLET_RE.sub("", x) for x in secs[key]][:6]})

    return normalize({
        "header": {
            "name": profile.get("name") or (lines[0] if lines else ""),
            "email": email.group(0) if email else profile.get("email", ""),
            "phone": phone.group(0) if phone else "",
            "location": loc.group(1) if loc else "",
            "links": sorted({m.group(1) for m in _LINK_TOKEN.finditer(no_email)})[:4],
        },
        "education": education,
        "experience": experience,
        "projects": projects,
        "skills": skills,
        "extras": extras,
        "style": {**STYLE_DEFAULTS, "company_first": company_first > 0},
        "section_order": _upload_order(secs) or (["education", "experience", "projects", "skills", "extras"]
        if (profile.get("coop_terms_completed") or 0) < 2 else ["experience", "projects", "education", "skills", "extras"]),
    })


_HOSTS = {"github", "gitlab", "devpost", "youtube", "linkedin", "medium", "docs", "drive", "play", "apps", "demo",
          "software", "watch", "tree", "main", "blob", "master"}


def _slug(x: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (x or "").lower())


def _same_line(a: dict, b: dict) -> bool:
    return a.get("page") is not None and a.get("page") == b.get("page") and abs((a.get("y") or 0) - (b.get("y") or 0)) < 5


def _names_match(anchor: str, url: str, name: str) -> bool:
    """Does a link belong to the entry called `name`? By the words under it, or the repo/site name in its URL."""
    n = _slug(name)
    if len(n) < 3:
        return False
    a = _slug(anchor)
    if len(a) >= 4 and (n in a or (len(a) >= 8 and a in n)):
        return True
    path = [p for p in re.split(r"[/?#]", url) if p]
    cands = {_slug(path[-1]) if len(path) > 1 else "", _slug(path[0].split(".")[0]) if path else ""} - _HOSTS
    return any(len(c) >= 4 and (c == n or c in n or (len(c) >= 6 and n in c)) for c in cands)


def _is_repo(url: str) -> bool:
    return bool(re.match(r"(?:github\.com|gitlab\.com|bitbucket\.org)/[^/]+/[^/]+", url, re.I))


def attach_file_links(r: dict, profile: dict) -> dict:
    """Put back links that only exist as clickable text in the uploaded file. Never overwrites a link already set.
    - profile links (GitHub, LinkedIn, portfolio) join the header;
    - a company name's link becomes that job's company website;
    - a project's links become its repo and demo, including a "Live Demo" sitting on the same line."""
    h = r.setdefault("header", {})
    links = [x for x in h.get("links") or [] if x]
    have = {rp.short_url(x).lower() for x in links}
    for x in profile.get("links") or []:
        if rp.short_url(x).lower() not in have:
            have.add(rp.short_url(x).lower())
            links.append(rp.short_url(x))
    h["links"] = links[:6]
    pending = [l for l in profile.get("file_links") or [] if not l.get("profile")]
    targets = [("experience", e, e.get("company") or "") for e in r.get("experience") or []] + \
              [("projects", p, p.get("name") or "") for p in r.get("projects") or []]
    owner: dict[int, tuple] = {}
    for i, l in enumerate(pending):
        for kind, entry, name in targets:
            if _names_match(l.get("text", ""), l["url"], name):
                owner[i] = (kind, entry)
                break
    for i, l in enumerate(pending):  # "Live Demo" etc.: belongs to whatever else is linked on its line
        if i not in owner:
            j = next((j for j in owner if _same_line(pending[j], l)), None)
            if j is not None:
                owner[i] = owner[j]
    for i, l in sorted(owner.items()):
        kind, entry = l
        url = pending[i]["url"]
        if kind == "experience":
            entry.setdefault("link", "")
            if not entry["link"]:
                entry["link"] = url
        else:
            entry.setdefault("link", "")
            entry.setdefault("demo", "")
            if url in (entry["link"], entry["demo"]):
                continue
            if not entry["link"] and (_is_repo(url) or not entry["demo"]):
                entry["link"] = url
            elif not entry["demo"]:
                entry["demo"] = url
    for p in r.get("projects") or []:  # repo first, demo second
        if p.get("demo") and _is_repo(p["demo"]) and not _is_repo(p.get("link") or ""):
            p["link"], p["demo"] = p["demo"], p["link"]
    return r


def normalize(r: dict) -> dict:
    """Fill missing keys so the editor and renderers can rely on the shape."""
    r = copy.deepcopy(r or {})
    h = r.setdefault("header", {})
    for k in ("name", "email", "phone", "location"):
        h.setdefault(k, "")
    h.setdefault("links", [])
    for sec, keys in (("education", ("school", "degree", "location", "dates")),
                      ("experience", ("title", "company", "location", "dates", "link")),
                      ("projects", ("name", "tech", "dates", "link", "demo"))):
        items = r.setdefault(sec, [])
        for it in items:
            for k in keys:
                it.setdefault(k, "")
            it.setdefault("details" if sec == "education" else "bullets", [])
    r.setdefault("skills", [])
    r.setdefault("extras", [])
    st = r.get("style") if isinstance(r.get("style"), dict) else {}
    r["style"] = {**STYLE_DEFAULTS, **{k: v for k, v in st.items() if k in STYLE_DEFAULTS}}
    for k in ("accent", "link_color"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(r["style"][k])):
            r["style"][k] = STYLE_DEFAULTS[k]
    if r["style"]["contact"] not in ("icons", "emoji", "plain"):
        r["style"]["contact"] = "icons"
    order = [s for s in r.get("section_order") or [] if s in SECTIONS]
    r["section_order"] = order + [s for s in SECTIONS if s not in order]
    return r


def plain_text(r: dict) -> str:
    out = [r["header"].get("name", "")]
    for e in r.get("education", []):
        out += [e["school"], e["degree"], *e.get("details", [])]
    for e in r.get("experience", []):
        out += [f"{e['title']} {e['company']}", *e.get("bullets", [])]
    for p in r.get("projects", []):
        out += [f"{p['name']} {p['tech']}", *p.get("bullets", [])]
    out += [f"{s['label']}: {s['items']}" for s in r.get("skills", [])]
    for x in r.get("extras", []):
        out += x.get("items", [])
    return "\n".join(x for x in out if x)


# --- the checker ------------------------------------------------------------------------------


# --- tailoring ---------------------------------------------------------------------------------

def keyword_report(r: dict, jd: str, title: str = "") -> dict:
    job_kw = detect_skills(f"{title}\n{jd}")
    text = plain_text(r)
    have = set(detect_skills(text))
    matched = [k for k in job_kw if k in have]
    missing = [k for k in job_kw if k not in have]
    coverage = round(100 * len(matched) / len(job_kw)) if job_kw else None
    return {"job_keywords": job_kw, "matched": matched, "missing": missing, "coverage": coverage}


def _relevance(text: str, kws: set[str]) -> int:
    return len(set(detect_skills(text)) & kws)


def tailor(r: dict, jd: str, title: str = "") -> tuple[dict, dict]:
    """Reorder for this posting without changing any facts: most relevant projects, bullets and skills first."""
    r = normalize(r)
    report = keyword_report(r, jd, title)
    kws = set(report["job_keywords"])
    t = copy.deepcopy(r)
    changes = []
    if kws:
        for e in t["experience"]:
            before = list(e["bullets"])
            e["bullets"].sort(key=lambda b: -_relevance(b, kws))
            if e["bullets"] != before:
                changes.append(f"Moved the most relevant bullets to the top for {e['company'] or e['title']}")
        before = [p["name"] for p in t["projects"]]
        t["projects"].sort(key=lambda p: -_relevance(f"{p['tech']} {' '.join(p['bullets'])}", kws))
        if [p["name"] for p in t["projects"]] != before:
            changes.append("Reordered projects: " + " → ".join(p["name"] for p in t["projects"][:3]))
        for s in t["skills"]:
            items = [x.strip() for x in re.split(r",", s["items"]) if x.strip()]
            ranked = sorted(items, key=lambda x: -(1 if set(detect_skills(x)) & kws else 0))
            if ranked != items:
                s["items"] = ", ".join(ranked)
                changes.append(f"Front-loaded matching {s['label'].lower()}")
    report["changes"] = changes
    report["tips"] = []
    if report["missing"]:
        report["tips"].append("The posting mentions " + ", ".join(report["missing"][:6]) +
                              ". If you've genuinely used any of these, add them to a bullet or your skills.")
    if report["coverage"] is not None and report["coverage"] < 50:
        report["tips"].append("Under half of the posting's keywords appear on your resume; ATS filters often rank on this.")
    return t, report


# --- check against the uploaded file ----------------------------------------------------------
# Your Studio copy keeps your edits, so parser fixes never overwrite it. compare() lists every difference between it
# and a fresh read of the file, and apply_compare() takes the ones you pick.

