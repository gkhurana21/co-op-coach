"""Cover letters: built-in formats, your own formats, and generation (rule-based fill or Claude).

Rule-based letters only use facts from your resume and the posting. Anything the app can't know (like why
you care about this particular company) is left as a [bracketed prompt] for you to fill in.
"""

import re
import time
from datetime import date

from . import db
from .textutil import detect_skills, long_date

BUILTIN_FORMATS = [
    {
        "id": "classic", "name": "Classic", "style": "template",
        "description": "Three short paragraphs: why this role, your best evidence, why this company. Works almost anywhere.",
        "body": """{date}

Dear {hiring_manager},

I'm a {year_phrase}{program} student at {school}, and I'm excited to apply for the {role} position{term_phrase} at {company}. {why_company}

{experience_paragraph}

{project_paragraph}

I'd love to bring {skills_phrase} to the team at {company}. Thank you for your time and consideration. I'd welcome the chance to talk about how I can contribute.

Sincerely,
{name}
{contact_line}""",
    },
    {
        "id": "startup", "name": "Startup: short & direct", "style": "template",
        "description": "Around 150 words. Gets to the point, shows you ship. Best for YC and early-stage startups.",
        "body": """Hi {company} team,

I'm {name}, a {year_phrase}{program} student at {school}, applying for the {role} role{term_phrase}.

What I've shipped:
{evidence_bullets}

{why_company}

I can start contributing with {skills_phrase} from day one. My work: {links}

Thanks,
{name}""",
    },
    {
        "id": "corporate", "name": "Bank / corporate (formal)", "style": "template",
        "description": "Formal tone with competencies and professionalism up front. Good for banks, insurers and large corporates.",
        "body": """{date}

{company}
Re: {role}{term_phrase}

Dear {hiring_manager},

Please accept this letter and my resume as my application for the {role} position{term_phrase}. I am currently a {year_phrase}{program} student at {school}{coop_phrase}.

{experience_paragraph}

{project_paragraph}

{why_company} I am confident that my experience with {skills_phrase}, together with my attention to detail and ability to learn quickly, would allow me to contribute meaningfully to your team.

Thank you for considering my application. I look forward to the opportunity to discuss it with you.

Yours sincerely,
{name}
{contact_line}""",
    },
    {
        "id": "match", "name": "Requirements match", "style": "template",
        "description": "A short intro, then 'what you're looking for / what I bring' bullets. Easy for recruiters to skim.",
        "body": """{date}

Dear {hiring_manager},

I'm applying for the {role} position{term_phrase} at {company}. I'm a {year_phrase}{program} student at {school}, and my experience lines up closely with what you're looking for:

{requirements_match}

{why_company}

I'd be glad to walk you through any of this. Thank you for your consideration.

Best regards,
{name}
{contact_line}""",
    },
    {
        "id": "story", "name": "Story-led", "style": "template",
        "description": "Opens with the project or moment that best shows who you are, then connects it to the role.",
        "body": """{date}

Dear {hiring_manager},

{story_opening}

That's the kind of work I want to do as a {role}{term_phrase} at {company}. {why_company}

{experience_paragraph}

I'd love to bring {skills_phrase} to {company}. Thank you for reading. I hope we get the chance to talk.

Warmly,
{name}
{contact_line}""",
    },
    {
        "id": "email", "name": "Recruiter email / LinkedIn note", "style": "template",
        "description": "4–5 sentences for cold emails, referrals or LinkedIn messages. Pair it with your resume.",
        "body": """Hi {hiring_manager_first},

I'm {name}, a {year_phrase}{program} student at {school}. I just applied for the {role} role{term_phrase} at {company} and wanted to reach out directly. {short_evidence} {why_company}

Would you be open to a quick 15-minute chat? My resume is attached.

Thanks so much,
{name}
{links}""",
    },
]
BUILTIN_IDS = {f["id"] for f in BUILTIN_FORMATS}

PLACEHOLDERS = {
    "date": "today's date", "name": "your name", "contact_line": "email · phone · links", "links": "your links",
    "school": "university", "program": "program", "year_phrase": "e.g. 'third-year '", "coop_phrase": "co-op experience",
    "company": "company", "role": "job title", "term_phrase": "e.g. ' for Winter 2027'",
    "hiring_manager": "Hiring Manager / recruiter name", "hiring_manager_first": "recruiter first name",
    "skills_phrase": "skills that match the posting", "why_company": "why this company (you fill it in)",
    "experience_paragraph": "your most relevant role", "project_paragraph": "your most relevant project",
    "evidence_bullets": "3 bullets of proof", "requirements_match": "requirement → evidence bullets",
    "story_opening": "a project story", "short_evidence": "one-line proof",
}


def list_formats(profile_id: int) -> list[dict]:
    custom = db.rows("SELECT id, name, description, body, style FROM letter_formats WHERE profile_id=? ORDER BY created_at",
                     (profile_id,))
    return [{**f, "builtin": True} for f in BUILTIN_FORMATS] + [{**f, "builtin": False} for f in custom]


def get_format(profile_id: int, format_id: str) -> dict:
    for f in list_formats(profile_id):
        if f["id"] == format_id:
            return f
    return BUILTIN_FORMATS[0]


# --- building the facts ----------------------------------------------------------------

_ORD = {1: "first-year ", 2: "second-year ", 3: "third-year ", 4: "fourth-year ", 5: "fifth-year "}


def _clean_role(title: str) -> str:
    t = re.sub(r"\s*[\(\[][^)\]]*(?:20\d\d|winter|summer|fall|spring|month|usa|canada|remote)[^)\]]*[\)\]]", "", title, flags=re.I)
    t = re.sub(r"\s*[-–|,]\s*(?:winter|summer|fall|spring|20\d\d).*$", "", t, flags=re.I)
    return t.strip(" -–|,") or title


def _lower_first(s: str) -> str:
    """Lowercase a leading verb ("Built x" -> "built x") but never a name ("Monte Carlo", "React")."""
    from .resume_studio import IRREGULAR_PAST, PRESENT_FORMS, STRONG_VERBS
    w = (s.split() or [""])[0].lower().strip(",.:;")
    if w in STRONG_VERBS or w in IRREGULAR_PAST or w in PRESENT_FORMS or (w.endswith("ed") and len(w) > 4):
        return s[0].lower() + s[1:]
    return s


def _sentence(s: str) -> str:
    s = re.sub(r";\s+", ", and ", s.strip().rstrip(".;,"))
    return s + "."


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def facts(profile: dict, resume: dict | None, job: dict, description: str = "", yc: dict | None = None) -> dict:
    """Everything a template can reference, computed from the resume + posting only."""
    resume = resume or {}
    h = resume.get("header") or {}
    jd = f"{job.get('title', '')}\n{description}"
    job_kw = detect_skills(jd)
    mine = set(profile.get("skills") or [])
    matched = [k for k in job_kw if k in mine] or list(profile.get("skills") or [])[:4]

    def relevance(text: str) -> int:
        return len(set(detect_skills(text)) & set(job_kw)) if job_kw else len(detect_skills(text))

    exps = resume.get("experience") or [{"title": e.get("title"), "company": e.get("company"),
                                          "bullets": e.get("highlights", [])} for e in profile.get("experiences", [])]
    projs = resume.get("projects") or [{"name": p.get("name"), "tech": ", ".join(p.get("tech", [])),
                                        "bullets": [p.get("summary", "")]} for p in profile.get("projects", [])]
    best_exp = max(exps, key=lambda e: relevance(" ".join(e.get("bullets", [])) + " " + (e.get("title") or "")),
                   default=None)
    best_proj = max(projs, key=lambda p: relevance(f"{p.get('tech', '')} {' '.join(p.get('bullets', []))}"), default=None)

    if best_exp and best_exp.get("bullets"):
        bl = sorted(best_exp["bullets"], key=lambda b: -relevance(b))
        exp_par = (f"Most recently, as a {best_exp.get('title') or 'member of the team'} at {best_exp.get('company') or 'my last co-op'}, "
                   f"I {_lower_first(_sentence(bl[0]))}")
        if len(bl) > 1:
            exp_par += f" I also {_lower_first(_sentence(bl[1]))}"
    else:
        exp_par = "[Describe your most relevant experience: what you built, how, and the result.]"
    if best_proj and best_proj.get("bullets"):
        b0 = best_proj["bullets"][0]
        starts_verb = bool(re.match(r"^[A-Z][a-z]+ed\b|^(Built|Wrote|Led|Ran|Made)\b", b0))
        proj_par = (f"Outside of work, I built {best_proj['name']}" + (f" ({best_proj['tech']})" if best_proj.get("tech") else "")
                    + (f", where I {_lower_first(_sentence(b0))}" if starts_verb else f": {_lower_first(_sentence(b0))}"))
    else:
        proj_par = ""

    evidence = []
    for e in exps[:3]:
        if e.get("bullets"):
            b = sorted(e["bullets"], key=lambda x: -relevance(x))[0]
            evidence.append(f"• {_sentence(b)[:-1]} ({e.get('company') or e.get('title')})")
    for p in projs[:2]:
        if p.get("bullets") and len(evidence) < 3:
            evidence.append(f"• {p['name']}: {_lower_first(_sentence(p['bullets'][0])[:-1])}")

    req_lines = []
    pool = [(b, e.get("company") or "") for e in exps for b in e.get("bullets", [])] + \
           [(b, p.get("name") or "") for p in projs for b in p.get("bullets", [])]
    for kw in job_kw[:6]:
        hit = next(((b, src) for b, src in pool if kw in detect_skills(b)), None)
        if hit:
            req_lines.append(f"• {kw}: {_sentence(hit[0])[:-1]} ({hit[1]})")
        if len(req_lines) == 3:
            break
    if not req_lines:
        req_lines = evidence[:3] or ["• [Requirement from the posting]: [your evidence]"]

    if yc and yc.get("one_liner"):
        why = (f"{job.get('company')}'s work on {_lower_first(yc['one_liner'].rstrip('.'))} is exactly the kind of "
               f"problem I want to spend my next term on. [Add one specific detail: a feature you use, a launch, or a talk.]")
    else:
        why = f"[One or two sentences on why {job.get('company')} specifically: a product you use, its mission, or a recent launch.]"

    year = profile.get("year_of_study") or 0
    terms = [t for t in job.get("terms") or [] if re.match(r"(Winter|Spring|Summer|Fall) 20\d\d", t)]
    coop_n = profile.get("coop_terms_completed") or 0
    links = h.get("links") or profile.get("links") or []
    contact = " · ".join(x for x in [h.get("email") or profile.get("email"), h.get("phone"), *links[:2]] if x)
    story = (f"{proj_par.replace('Outside of work, I built', 'Last year I built', 1)}"
             if proj_par else "[Open with a project or moment that shows what you care about building.]")
    return {
        "date": long_date(date.today()),
        "name": h.get("name") or profile.get("name") or "[Your name]",
        "contact_line": contact, "links": " · ".join(links) or "[GitHub / portfolio]",
        "school": profile.get("university") or "[University]",
        "program": profile.get("program") or "[program]",
        "year_phrase": _ORD.get(year, ""),
        "coop_phrase": f", with {coop_n} co-op term{'s' if coop_n != 1 else ''} of experience" if coop_n else "",
        "company": job.get("company") or "[Company]",
        "role": _clean_role(job.get("title") or "[Role]"),
        "term_phrase": f" for {terms[0]}" if terms else "",
        "hiring_manager": "Hiring Manager", "hiring_manager_first": "there",
        "skills_phrase": _join(matched[:3]) or "my technical skills",
        "why_company": why,
        "experience_paragraph": exp_par,
        "project_paragraph": proj_par,
        "evidence_bullets": "\n".join(evidence[:3]) or "• [Your strongest result]",
        "requirements_match": "\n".join(req_lines),
        "story_opening": story,
        "short_evidence": (f"Most recently I {_lower_first(_sentence(best_exp['bullets'][0]))}"
                           if best_exp and best_exp.get("bullets") else ""),
    }


class _Safe(dict):
    def __missing__(self, key):
        return f"[{PLACEHOLDERS.get(key, key)}]"


def fill(template: str, f: dict) -> str:
    try:
        text = template.format_map(_Safe(f))
    except (ValueError, IndexError):  # stray braces in a user template
        text = template
        for k, v in f.items():
            text = text.replace("{" + k + "}", str(v))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def placeholders_left(text: str) -> list[str]:
    return re.findall(r"\[[^\]]{3,160}\]", text)


def save_letter(profile_id: int, job_id: str, title: str, format_id: str, content: str, by: str) -> int:
    now = int(time.time())
    existing = db.row("SELECT id FROM documents WHERE profile_id=? AND kind='cover_letter' AND job_id=? AND format_id=?",
                      (profile_id, job_id, format_id))
    with db.tx() as c:
        if existing:
            c.execute("UPDATE documents SET content=?, generated_by=?, title=?, updated_at=? WHERE id=?",
                      (content, by, title, now, existing["id"]))
            return existing["id"]
        return c.execute("INSERT INTO documents (profile_id, kind, job_id, title, format_id, content, generated_by,"
                         " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (profile_id, "cover_letter", job_id, title, format_id, content, by, now, now)).lastrowid
