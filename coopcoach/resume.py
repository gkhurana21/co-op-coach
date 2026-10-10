"""Resume text extraction, a rule-based parser, and a rule-based assessment.

The Claude path (ai.py) produces the same profile/assessment shapes with much better judgment;
this module is the offline fallback and also supplies canonical skill tags for matching.
"""

import io
import re
from collections import Counter
from datetime import date

from .textutil import CATEGORIES, detect_skills, skill_categories

# Bump when parsing improves: profiles parsed by an older version are re-read from their stored file at startup.
PARSER_VERSION = 5

# --- text extraction ------------------------------------------------------------


def _pdf_reader(data: bytes):
    import logging

    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # emoji/icon fonts make pypdf log hundreds of warnings
    return PdfReader(io.BytesIO(data))


def _two_column(lines: list[str]) -> bool:
    """Layout extraction puts side-by-side columns on one line; spot that so we can fall back."""
    split = [ln for ln in lines if re.search(r"\S {8,}\S", ln)
             and all(len(part) > 28 for part in re.split(r" {8,}", ln.strip())[:2])]
    return len(lines) > 10 and len(split) > 0.25 * len(lines)


def _pdf_text(data: bytes) -> str:
    """Text in reading order. Layout mode follows the page position (so text added in a separate layer, like an
    edited contact line, lands where it's shown) and keeps gaps between columns, which become " | "."""
    reader = _pdf_reader(data)
    try:
        pages = [p.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or "" for p in reader.pages]
    except Exception:
        pages = []
    lines = [ln.rstrip() for pg in pages for ln in pg.splitlines()]
    if not "".join(lines).strip() or _two_column(lines):
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    out = []
    for ln in lines:
        # real column gaps (name ... dates) are 10+ spaces wide; bold or large text can show 3-4 between words
        ln = re.sub(r"(?<=\S) {6,}(?=\S)", " | ", ln.strip())
        out.append(re.sub(r"(?:\s*\|\s*){2,}", " | ", ln))
    return "\n".join(out)


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        text = _pdf_text(data)
    elif name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for r in t.rows:
                parts.append(" | ".join(c.text for c in r.cells))
        text = "\n".join(parts)
    else:
        text = data.decode("utf-8", errors="ignore")
    text = text.replace("\u00a0", " ").replace("\uf0b7", "•").replace("\u2022", "•")
    text = text.replace("\u2011", "-").replace("\u2010", "-").replace("\u00ad", "")  # non-breaking/soft hyphens
    text = re.sub(r"[\ue000-\uf8ff]", "", text)  # icon-font glyphs (FontAwesome phone, GitHub logo, ...)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +([,.;:])(?=\s|$)", r"\1", text)  # "Toronto , ON" -> "Toronto, ON"
    text = re.sub(r"(?<=[a-z])&(?= )", " &", text)  # layout extraction: "Languages& Frameworks"
    return re.sub(r"\n\s*\n+", "\n", text).strip()


# --- clickable links ------------------------------------------------------------------
# Most resumes show "GitHub" or "LinkedIn" as a word with the address hidden behind it, so text extraction never
# sees the URL. These read the link targets themselves.

_PROFILE_LINK = re.compile(r"^(?:linkedin\.com/in|github\.com|gitlab\.com|devpost\.com|leetcode\.com(?:/u)?|"
                           r"kaggle\.com|x\.com|twitter\.com|behance\.net|dribbble\.com|medium\.com)/[^/?#]+$", re.I)


def short_url(u: str) -> str:
    """'https://www.github.com/you/' -> 'github.com/you' (how links are shown and stored)."""
    return re.sub(r"^https?://(?:www\.)?", "", (u or "").strip(), flags=re.I).rstrip("/")


def extract_links(filename: str, data: bytes) -> list[dict]:
    """Every clickable link in the file, in reading order: {"url", "text", "header"}. `header` means it sits by the
    name (top of page 1 in a PDF, the page header or first lines of a DOCX). `text` is the anchor text (DOCX only)."""
    name, out = filename.lower(), []
    try:
        if name.endswith(".pdf"):
            for i, page in enumerate(_pdf_reader(data).pages):
                box = page.mediabox
                annots = []
                for a in page.get("/Annots") or []:
                    a = a.get_object()
                    act = a.get("/A")
                    act = act.get_object() if act is not None else {}
                    if a.get("/Subtype") == "/Link" and act.get("/S") == "/URI":
                        annots.append(([float(v) for v in a.get("/Rect") or (0, 0, 0, 0)], str(act.get("/URI") or "")))
                if not annots:
                    continue
                runs = []  # where each piece of text sits, to read the words under each link

                def visit(text, cm, tm, _font, _size, runs=runs):
                    if text and text.strip():
                        runs.append((tm[4] * cm[0] + tm[5] * cm[2] + cm[4], tm[4] * cm[1] + tm[5] * cm[3] + cm[5], text))
                try:
                    page.extract_text(visitor_text=visit)
                except Exception:
                    pass
                found = []
                for (x1, y1, x2, y2), uri in annots:
                    x1, x2, y1, y2 = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
                    words = [t for x, y, t in sorted(runs) if x1 - 2 <= x <= x2 + 1 and y1 - 3 <= y <= y2 + 1]
                    anchor = re.sub(r"\s+", " ", re.sub(r"[\ue000-\uf8ff]", "", " ".join(words))).strip()
                    from_top = (float(box.top) - y2) / (float(box.height) or 792)
                    found.append((round(-y2), x1, {"url": uri, "text": anchor, "header": i == 0 and from_top < 0.16,
                                                   "page": i, "y": round((y1 + y2) / 2, 1)}))
                out += [f for _, _, f in sorted(found, key=lambda t: (t[0], t[1]))]
        elif name.endswith(".docx"):
            import docx
            d = docx.Document(io.BytesIO(data))
            paras = [(p, True) for s in d.sections for p in s.header.paragraphs]
            body = [p for p in d.paragraphs if p.text.strip()]
            paras += [(p, n < 4) for n, p in enumerate(body)]
            paras += [(p, False) for t in d.tables for r in t.rows for c in r.cells for p in c.paragraphs]
            for p, head in paras:
                out += [{"url": h.url, "text": h.text.strip(), "header": head} for h in p.hyperlinks if h.url]
    except Exception:  # a malformed annotation shouldn't block the upload; the text still parsed
        pass
    seen, uniq = set(), []
    for l in out:
        if l["url"] and l["url"] not in seen:
            seen.add(l["url"])
            uniq.append(l)
    return uniq


def linkedin_facts(text: str) -> dict:
    """What a LinkedIn "Save to PDF" export adds: co-op status, study dates, the profile URL, the headline."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    edu = text[text.find("Education"):] if "Education" in text else ""
    dr = _DATE_RANGE_RE.search(edu)
    url = re.search(r"(?:www\.)?linkedin\.com/in/[\w-]+", text)
    return {
        "coop_program": bool(re.search(r"\bco-?op\b", text, re.I)),
        "grad_date": dr.group(2).strip() if dr and _year(dr.group(2)) else "",
        "start_date": dr.group(1).strip() if dr else "",
        "linkedin": url.group(0).replace("www.", "") if url else "",
        "headline": next((ln for ln in lines[1:6] if 15 < len(ln) < 160 and "@" not in ln and "linkedin" not in ln.lower()), ""),
    }


def merge_file_links(profile: dict, links: list[dict]) -> dict:
    """Fold a file's clickable links into a parsed profile: links by the name (and any LinkedIn/GitHub-style
    profile link) join profile["links"], a mailto: fills a missing email, and every web link is kept in
    profile["file_links"] so Resume Studio can put project links back too."""
    have, merged = set(), []
    for l in profile.get("links") or []:
        s = short_url(l)
        if s and s.lower() not in have:
            have.add(s.lower())
            merged.append(s)
    file_links = []
    for l in links:
        u = l["url"].strip()
        if u.lower().startswith("mailto:"):
            addr = u[7:].split("?")[0].strip()
            if not profile.get("email") and _EMAIL_RE.fullmatch(addr):
                profile["email"] = addr
            continue
        if not re.match(r"https?://", u, re.I):
            continue
        s = short_url(u)
        is_profile = bool(l.get("header") or _PROFILE_LINK.match(s))
        file_links.append({"url": s, "text": (l.get("text") or "")[:160], "profile": is_profile,
                           "page": l.get("page"), "y": l.get("y")})
        if is_profile and s.lower() not in have:
            have.add(s.lower())
            merged.append(s)
    profile["links"] = merged[:6]
    profile["file_links"] = file_links[:30]
    return profile


def describe_links(links: list[dict]) -> str:
    """What kinds of hidden links the file has, without the addresses (for the AI reviewer)."""
    kinds = Counter()
    for l in links:
        s = short_url(l["url"]).lower()
        if s.startswith("linkedin.com/in/"):
            kinds["LinkedIn profile"] += 1
        elif _PROFILE_LINK.match(s) and s.startswith(("github.com", "gitlab.com")):
            kinds["GitHub profile"] += 1
        elif l["url"].lower().startswith("mailto:"):
            kinds["email"] += 1
        elif l.get("header"):
            kinds["portfolio/website"] += 1
        elif l["url"].lower().startswith("http"):
            kinds["project or other link"] += 1
    return ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in kinds.items())


def drop_stale_link_tips(assessment: dict, profile: dict) -> dict:
    """Don't tell someone to add a GitHub/LinkedIn link their resume already has behind clickable text."""
    links = " ".join(profile.get("links") or []).lower()
    has_gh, has_li = "github.com" in links or "gitlab.com" in links, "linkedin.com" in links
    keep = []
    for t in assessment.get("resume_tips") or []:
        tl = t.lower()
        if re.search(r"\b(add|include|link|missing|put)\b", tl) and (
                (has_gh and re.search(r"github|portfolio", tl) and not re.search(r"readme|pin|repo", tl)) or
                (has_li and "linkedin" in tl and "github" not in tl)):
            continue
        keep.append(t)
    assessment["resume_tips"] = keep
    return assessment


# --- rule-based parsing ------------------------------------------------------------

_SECTION_RE = re.compile(
    r"^\s*(education|academic background|experience|work experience|professional experience|employment|"
    r"relevant experience|technical experience|projects?|personal projects|technical projects|skills|"
    r"technical skills|skills (?:and|&) interests|leadership|extracurriculars?|activities|awards|honou?rs|"
    r"certifications?|volunteer(?:ing)?|publications|interests|summary|profile|objective|coursework|"
    r"relevant coursework)\s*:?\s*$",
    re.I,
)
_SECTION_KIND = {
    "education": "education", "academic background": "education",
    "experience": "experience", "work experience": "experience", "professional experience": "experience",
    "employment": "experience", "relevant experience": "experience", "technical experience": "experience",
    "project": "projects", "projects": "projects", "personal projects": "projects", "technical projects": "projects",
    "skills": "skills", "technical skills": "skills", "skills and interests": "skills", "skills & interests": "skills",
}

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RANGE_RE = re.compile(
    rf"({_MONTH}\s*'?\d{{2,4}}|(?:winter|summer|fall|spring)\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})\s*"
    rf"(?:[-–—]+|to)\s*({_MONTH}\s*'?\d{{2,4}}|(?:winter|summer|fall|spring)\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}}|"
    rf"present|current|now|ongoing)",
    re.I,
)
_SINGLE_TERM_RE = re.compile(rf"\b((?:winter|summer|fall|spring)\s+\d{{4}}|{_MONTH}\s+\d{{4}})\b", re.I)
_BULLET_RE = re.compile(r"^\s*[•\-–▪◦*●■➢►✓]\s*")

_UNIS = [
    ("University of Waterloo", r"university of waterloo|\buwaterloo\b|\buw\b(?= |,)"),
    ("University of Toronto", r"university of toronto|\buoft\b|\bu of t\b|\butsc\b|\butm\b"),
    ("University of British Columbia", r"university of british columbia|\bubc\b"),
    ("McGill University", r"\bmcgill\b"),
    ("Queen's University", r"queen'?s university"),
    ("Western University", r"western university|university of western ontario|\buwo\b|ivey business"),
    ("McMaster University", r"\bmcmaster\b"),
    ("Toronto Metropolitan University", r"toronto metropolitan|\btmu\b|ryerson"),
    ("York University", r"york university|\bschulich\b|lassonde"),
    ("University of Ottawa", r"university of ottawa|\buottawa\b"),
    ("Carleton University", r"\bcarleton\b"),
    ("Simon Fraser University", r"simon fraser|\bsfu\b"),
    ("University of Alberta", r"university of alberta|\bualberta\b"),
    ("University of Calgary", r"university of calgary|\bucalgary\b"),
    ("Wilfrid Laurier University", r"wilfrid laurier|\blaurier\b"),
    ("University of Guelph", r"university of guelph"),
    ("Concordia University", r"\bconcordia\b"),
    ("Dalhousie University", r"\bdalhousie\b"),
    ("Ontario Tech University", r"ontario tech|\buoit\b"),
    ("University of Victoria", r"university of victoria|\buvic\b"),
    ("Polytechnique Montréal", r"polytechnique montr"),
    ("Université de Montréal", r"universit[eé] de montr[eé]al"),
]
_GENERIC_UNI_RE = re.compile(
    r"((?:[A-Z][\w'.&-]+\s+){0,4}(?:University|Institute of Technology|Polytechnic|College)"
    r"(?:\s+of\s+(?:[A-Z][\w'.&-]+\s?){1,4})?|University of (?:[A-Z][\w'.&-]+\s?){1,4})")

_PROGRAMS = [
    "Software Engineering", "Computer Engineering", "Computer Science", "Electrical Engineering",
    "Mechatronics Engineering", "Systems Design Engineering", "Mechanical Engineering", "Engineering Science",
    "Biomedical Engineering", "Management Engineering", "Industrial Engineering", "Chemical Engineering",
    "Civil Engineering", "Data Science", "Statistics", "Actuarial Science", "Mathematics", "Applied Mathematics",
    "Computational Mathematics", "Mathematical Finance", "Financial Mathematics", "Physics", "Economics",
    "Business Administration", "Commerce", "Accounting", "Finance", "Information Technology",
    "Computer Information Systems", "Cognitive Science", "Biology", "Health Sciences", "Psychology",
    "Information Systems", "Business Technology Management", "Computing", "Life Sciences", "Kinesiology",
]
_DEGREE_RE = re.compile(
    r"\b(Bachelor(?:'s)?|B\.?\s?A\.?\s?Sc\.?|B\.?\s?Sc\.?|B\.?\s?Eng\.?|BASc|BSc|BEng|BCom|B\.?\s?Comm?\.?|BBA|BMath|"
    r"B\.?\s?A\.?|Honou?rs|Master(?:'s)?|M\.?\s?Sc|MEng|MASc|MBA|Ph\.?\s?D|Doctor|Diploma|Associate)\b")

_GPA_RES = [
    re.compile(r"(?:c?gpa|grade point average)\s*[:\-]?\s*(\d\.\d{1,2})\s*(?:/|out of)\s*(\d(?:\.\d{1,2})?)", re.I),
    re.compile(r"(\d\.\d{1,2})\s*/\s*(4(?:\.0{1,2})?|4\.3|4\.33|9(?:\.0)?|10(?:\.0)?|12(?:\.0)?)\s*(?:c?gpa)?", re.I),
    re.compile(r"(?:c?gpa)\s*[:\-]?\s*(\d\.\d{1,2})\b", re.I),
]
_AVG_RE = re.compile(r"(?:cumulative|overall|term|major|program|current)?\s*(?:average|avg|grade)\s*[:\-]?\s*"
                     r"(\d{2}(?:\.\d{1,2})?)\s*%", re.I)
_AVG2_RE = re.compile(r"\b(\d{2}(?:\.\d{1,2})?)\s*%\s*(?:cumulative|overall|average|avg)", re.I)
_DEAN_RE = re.compile(r"dean'?s (?:honou?rs? )?list|president'?s scholar|scholarship|honou?r roll", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?((?:linkedin\.com/in|github\.com|gitlab\.com)/[\w./-]+|[\w-]+\.(?:dev|io|me|ca|com)(?:/[\w./-]*)?)", re.I)
_INTERN_TITLE_RE = re.compile(r"\b(intern|internship|co-?op|coop|student|research assistant|ura|usra|summer analyst)\b", re.I)


def _sections(lines: list[str]) -> dict[str, list[str]]:
    secs: dict[str, list[str]] = {"header": []}
    cur = "header"
    for ln in lines:
        m = _SECTION_RE.match(ln)
        if m and len(ln) < 45:
            key = m.group(1).lower()
            cur = _SECTION_KIND.get(key, key.split()[0])
            secs.setdefault(cur, [])
            continue
        secs.setdefault(cur, []).append(ln)
    return secs


def _year(s: str) -> int | None:
    m = re.search(r"(\d{4})|'(\d{2})|\b(\d{2})$", s)
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    return 2000 + int(m.group(2) or m.group(3))


def _month_index(s: str) -> int:
    s = s.lower()
    for i, mo in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
        if s.startswith(mo):
            return i + 1
    if s.startswith("winter"):
        return 1
    if s.startswith("spring"):
        return 5 if "spring" in s else 1
    if s.startswith("summer"):
        return 5
    if s.startswith("fall"):
        return 9
    m = re.match(r"(\d{1,2})/", s)
    return int(m.group(1)) if m else 1


def _months_between(a: str, b: str) -> int:
    ya = _year(a)
    if b.lower() in ("present", "current", "now", "ongoing"):
        t = date.today()
        yb, mb = t.year, t.month
    else:
        yb, mb = _year(b), _month_index(b)
    if not ya or not yb:
        return 4
    ma = _month_index(a)
    months = (yb - ya) * 12 + (mb - ma) + 1
    if re.fullmatch(r"\d{4}", a.strip()) and re.fullmatch(r"\d{4}", b.strip()):
        months = max(4, months - 11)
    return max(1, min(months, 60))


def _entries(lines: list[str], dated: bool) -> list[dict]:
    """Group a section into entries (header line(s) + highlight lines).

    PDF extraction usually drops bullet glyphs, so we can't rely on them: in dated sections
    (experience) a line with a date range starts an entry; in undated ones (projects) a short
    or pipe-separated line that isn't a sentence starts one.
    """
    entries: list[dict] = []
    cur: dict | None = None
    # when the section marks bullets with glyphs, an unmarked line right after a bullet is that bullet wrapping onto
    # the next line (unless it's clearly the next entry's heading)
    glyphs = sum(1 for ln in lines if _BULLET_RE.match(ln)) >= 2
    for ln in lines:
        is_bullet = bool(_BULLET_RE.match(ln))
        text = _BULLET_RE.sub("", ln).strip()
        if not text:
            continue
        has_date = bool(_DATE_RANGE_RE.search(text) or _SINGLE_TERM_RE.search(text))
        sentence_like = len(text) > 70 or text.endswith(".") or bool(_VERB_START_RE.match(text))
        if is_bullet and cur is not None:
            cur["bullets"].append(text)
            continue
        if glyphs and not is_bullet and cur is not None and cur["bullets"] and _continues(cur["bullets"][-1], text, has_date):
            cur["bullets"][-1] = _join_wrapped(cur["bullets"][-1], text)
            continue
        if glyphs and not is_bullet and not (dated and has_date):
            # bullets are marked, so any other line is a heading: the next entry, or more of this one's heading
            if cur is not None and not cur["bullets"] and len(cur["head"]) < 3:
                cur["head"].append(text)
                cur["dates"] = cur["dates"] or (text if has_date else "")
            else:
                cur = {"head": [text], "bullets": [], "dates": text if has_date else ""}
                entries.append(cur)
            continue
        if dated:
            if has_date and cur is not None and not cur["bullets"] and not cur["dates"] and len(cur["head"]) < 3:
                cur["head"].append(text)          # title on one line, company/dates on the next
                cur["dates"] = text
            elif has_date:
                cur = {"head": [text], "bullets": [], "dates": text}
                entries.append(cur)
            elif cur is None:
                cur = {"head": [text], "bullets": [], "dates": ""}
                entries.append(cur)
            elif not cur["bullets"] and len(cur["head"]) < 2 and not sentence_like:
                cur["head"].append(text)
            else:
                cur["bullets"].append(text)
        else:
            header_like = not sentence_like and ("|" in text or len(text) <= 55 or has_date)
            if cur is None or (header_like and cur["bullets"]):
                cur = {"head": [text], "bullets": [], "dates": text if has_date else ""}
                entries.append(cur)
            elif header_like and not cur["bullets"] and len(cur["head"]) < 2:
                cur["head"].append(text)
            else:
                cur["bullets"].append(text)
    return entries


_COMPOUND_LEFT = {"real", "cross", "end", "multi", "full", "high", "low", "open", "self", "well", "data", "user",
                  "client", "server", "front", "back", "non", "pre", "post", "state", "long", "short", "large", "small",
                  "fine", "first", "second", "third", "one", "two", "three", "in", "on", "off", "co", "peer", "e", "x"}


def _continues(prev: str, line: str, has_date: bool) -> bool:
    """Is `line` the rest of bullet `prev`, wrapped by the PDF, rather than a new entry heading?"""
    if has_date or " | " in line:
        return False  # "DataKnobs | Sept 2025 – Dec 2025" or "tradebot: … | Go, Python"
    if line[:1].islower() or line[:1] in "~(%$&,;" or line[:1].isdigit():
        return True
    return not prev.rstrip().endswith((".", "!", "?", ")"))


def _join_wrapped(prev: str, line: str) -> str:
    """Re-join a bullet split across lines, undoing LaTeX hyphenation ("reduc-" + "ing") but keeping real
    compounds ("real-" + "time")."""
    m = re.search(r"(\w+)-$", prev)
    if m and line[:1].islower() and m.group(1).lower() not in _COMPOUND_LEFT:
        return prev[:-1] + line
    return prev.rstrip() + " " + line


_VERB_START_RE = re.compile(
    r"^(built|developed|designed|created|implemented|led|wrote|worked|helped|managed|improved|reduced|increased|"
    r"automated|deployed|shipped|launched|analy[sz]ed|researched|collaborated|optimized|optimised|migrated|"
    r"integrated|engineered|architected|maintained|conducted|organized|organised|mentored|tested|trained|"
    r"real-time|an? )\b", re.I)


def _split_title_company(head: list[str]) -> tuple[str, str]:
    clean = [_DATE_RANGE_RE.sub("", h).strip(" |,–-") for h in head]
    clean = [re.sub(r"\s{2,}", " ", c) for c in clean if c]
    if not clean:
        return "", ""
    first = clean[0]
    for sep in (" | ", " — ", " – ", " - ", " @ ", " at ", ", "):
        if sep in first:
            a, b = first.split(sep, 1)
            if _INTERN_TITLE_RE.search(b) or re.search(r"engineer|developer|analyst|scientist|manager|designer", b, re.I):
                return b.strip(), a.strip()
            return a.strip(), b.strip()
    second = clean[1] if len(clean) > 1 else ""
    if re.search(r"engineer|developer|analyst|scientist|intern|co-?op|manager|designer|researcher|assistant", second, re.I):
        return second, first
    return first, second


_TITLE_WORDS = re.compile(
    r"\b(intern(ship)?|co-?op|engineer(ing)?|developer|analyst|associate|assistant|researcher|scientist|manager|lead|"
    r"consultant|designer|specialist|fellow|trader|architect|administrator|technician|officer|coordinator|"
    r"representative|tutor|teaching|founder|co-founder|president|director|head|member|student|programmer|ambassador)\b",
    re.I)
_PLACE = re.compile(r"^((?i:remote|hybrid|on-?site|online)|[A-Z][\w.' -]+,\s*[A-Z]{2}(?:,\s*\w+)?|"
                    r"[A-Z][\w.' -]+,\s*(?:Canada|USA|United States|India|UK))$")
def _date_in(s: str):
    return _DATE_RANGE_RE.search(s) or _SINGLE_TERM_RE.search(s)


def parse_exp_head(head: list[str]) -> dict:
    """Title / company / dates / location from an experience heading, in either common layout:
    "Software Engineer Intern | Jan 2025 – Apr 2025" + "Company | City, ON"   (Jake's)
    "Company | May 2026 – Present | Toronto, ON" + "Technical Systems Analyst Co-op"   (company first)."""
    out = {"title": "", "company": "", "dates": "", "location": ""}
    texts: list[tuple[int, str]] = []
    for i, ln in enumerate(head):
        for seg in re.split(r"\s+\|\s+|\s{2,}", ln):
            seg = seg.strip(" ,|–—-")
            if not seg:
                continue
            d = _date_in(seg)
            if d:
                out["dates"] = out["dates"] or d.group(0).strip()
                seg = (seg[:d.start()] + " " + seg[d.end():]).strip(" ,|–—-")
                if not seg:
                    continue
            if _PLACE.match(seg) and not out["location"]:
                out["location"] = seg
                continue
            texts.append((i, seg))
    if not texts:
        return out
    for i, seg in texts:  # "Software Engineer Intern at Shopify" / "... @ Shopify"
        m = re.match(r"(.+?)\s+(?:at|@)\s+(.+)", seg)
        if m and _TITLE_WORDS.search(m.group(1)):
            out["title"], out["company"] = m.group(1), m.group(2)
            return out
    titled = [t for t in texts if _TITLE_WORDS.search(t[1])]
    if titled:
        out["title"] = titled[0][1]
        rest = [t for t in texts if t is not titled[0]]
        out["company"] = rest[0][1] if rest else ""
        out["company_first"] = bool(rest) and rest[0][0] < titled[0][0]  # company on the line above the title
    elif len(texts) == 1:
        out["company"] = texts[0][1]
    else:
        out["title"], out["company"] = _split_title_company([t[1] for t in texts])
    return out


def heuristic_parse(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    secs = _sections(lines)
    header = " ".join(secs.get("header", [])[:6])
    edu_text = "\n".join(secs.get("education", [])) or text[:1500]

    name = ""
    for ln in lines[:3]:
        if 1 < len(ln.split()) <= 4 and not _EMAIL_RE.search(ln) and not re.search(r"\d", ln):
            name = ln.title() if ln.isupper() else ln
            break

    university = ""
    for canon, rx in _UNIS:
        if re.search(rx, edu_text, re.I) or re.search(rx, header, re.I):
            university = canon
            break
    if not university:
        m = _GENERIC_UNI_RE.search(edu_text)
        if m:
            university = m.group(1).strip()

    program = ""
    for p in _PROGRAMS:
        if re.search(rf"\b{re.escape(p)}\b", edu_text, re.I):
            program = p
            break
    deg = _DEGREE_RE.search(edu_text)
    degree_level = "Bachelor's"
    if deg:
        d = deg.group(1).lower().replace(".", "").replace(" ", "")
        if d.startswith(("master", "msc", "meng", "masc", "mba")):
            degree_level = "Master's"
        elif d.startswith(("phd", "doctor")):
            degree_level = "PhD"
        elif d.startswith(("diploma", "associate")):
            degree_level = "Diploma"

    gpa_display, gpa_norm = "", None
    for rx in _GPA_RES:
        m = rx.search(text)
        if m:
            val = float(m.group(1))
            scale = float(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else (4.0 if val <= 4.0 else 4.33)
            if 0 < val <= scale:
                gpa_display = f"{val:g}/{scale:g}"
                gpa_norm = round(val / scale * 4.0, 2)
                break
    if gpa_norm is None:
        m = _AVG_RE.search(text) or _AVG2_RE.search(text)
        if m:
            pct = float(m.group(1))
            if 50 <= pct <= 100:
                gpa_display = f"{pct:g}%"
                gpa_norm = round(max(0.0, min(4.0, (pct - 50) / 40 * 4.0)), 2)  # 90%+ ≈ 4.0, 70% ≈ 2.0

    grad_year = 0
    m = re.search(r"(?:expected|anticipated|graduat\w*|class of|exp\.?)\s*:?\s*(?:" + _MONTH + r"\s*)?(20\d\d)", edu_text, re.I)
    if m:
        grad_year = int(m.group(1))
    else:
        years = [int(y) for y in re.findall(r"\b(20[1-4]\d)\b", edu_text)]
        if years:
            grad_year = max(years)
    today = date.today()
    edu_range = _DATE_RANGE_RE.search(edu_text)
    grad_date = edu_range.group(2).strip() if edu_range and _year(edu_range.group(2)) else ""

    experiences = []
    total_months = 0
    for e in _entries(secs.get("experience", []), dated=True):
        h = parse_exp_head(e["head"])
        title, company = h["title"], h["company"]
        dr = _DATE_RANGE_RE.search(h["dates"] or e.get("dates") or "")
        months = _months_between(dr.group(1), dr.group(2)) if dr else 4
        total_months += months
        experiences.append({
            "title": title[:120],
            "company": company[:120],
            "dates": (dr.group(0) if dr else "").strip(),
            "is_internship": bool(_INTERN_TITLE_RE.search(" ".join(e["head"]))) or months <= 8,
            "highlights": e["bullets"][:6],
        })

    projects = []
    for e in _entries(secs.get("projects", []), dated=False):
        head = " ".join(e["head"])
        name_part = re.split(r"\s[|–—-]\s|\(|:", head)[0].strip()
        projects.append({
            "name": name_part[:80] or head[:80],
            "tech": detect_skills(head + " " + " ".join(e["bullets"]))[:8],
            "summary": (e["bullets"][0] if e["bullets"] else head)[:220],
        })

    skills = detect_skills(text)
    # skills the dictionary doesn't know but that are listed explicitly in the skills section
    extra = []
    for ln in secs.get("skills", []):
        body = ln.split(":", 1)[1] if ":" in ln else ln
        for tok in re.split(r",|;|\||•", body):
            tok = tok.strip(" .")
            if 1 < len(tok) <= 25 and not detect_skills(tok) and tok.lower() not in (s.lower() for s in extra):
                extra.append(tok)

    header_no_email = _EMAIL_RE.sub(" ", header)
    links = sorted({m.group(1) for m in _LINK_RE.finditer(header_no_email)})
    email = (_EMAIL_RE.search(header) or _EMAIL_RE.search(text[:600]))
    coop_terms = sum(1 for x in experiences if x["is_internship"])

    # co-op students: "co-op" anywhere (titles like "Analyst Co-op"), or a co-op school with several work terms
    coop_program = bool(re.search(r"\bco-?op\b", text, re.I)) or (bool(_COOP_SCHOOL_RE.search(university)) and coop_terms >= 2)
    span = 5 if coop_program else 4
    year_of_study = 0
    if edu_range and _year(edu_range.group(1)) and _year(edu_range.group(1)) <= today.year:
        # count academic years since the program started (Sept 2024 -> Oct 2026 is year 3)
        sy, sm = _year(edu_range.group(1)), _month_index(edu_range.group(1)) or 9
        year_of_study = max(1, min(span, today.year - sy - (1 if today.month < sm else 0) + 1))
    elif grad_year:
        year_of_study = max(1, min(span, span - (grad_year - today.year) + (1 if today.month >= 9 else 0)))

    profile = {
        "name": name,
        "email": email.group(0) if email else "",
        "links": links[:5],
        "university": university,
        "program": program,
        "degree_level": degree_level,
        "year_of_study": year_of_study,
        "grad_year": grad_year,
        "grad_date": grad_date,
        "coop_program": coop_program,
        "gpa": gpa_display,
        "gpa_normalized": gpa_norm,
        "coop_terms_completed": coop_terms,
        "experience_months": total_months,
        "experiences": experiences[:8],
        "projects": projects[:8],
        "skills": skills,
        "other_skills": extra[:20],
        "awards": bool(_DEAN_RE.search(text)),
        "target_categories": infer_targets(skills, experiences, program, evidence=" ".join(
            [edu_text, *[p["name"] + " " + p["summary"] for p in projects]])),
        "summary": "",
    }
    profile["summary"] = _summary(profile)
    return profile


_COOP_SCHOOL_RE = re.compile(r"waterloo|laurier|toronto metropolitan|ryerson|simon fraser|sherbrooke|northeastern|"
                             r"drexel|carleton|university of victoria|guelph|mcmaster|queen'?s|ottawa", re.I)
# what someone's own experience/projects say about where they're heading, beyond their skills list
_TARGET_EVIDENCE = {
    "Quant": r"quant|trading|derivative|options? pricing|black-?scholes|monte carlo|portfolio|hedge|market data|"
             r"\bvar\b|risk engine|statistics minor|actuarial",
    "AI/ML/Data": r"machine learning|\bml\b|\bllm|neural|deep learning|\brag\b|embedding|data scien|computer vision|nlp",
    "Business/Finance": r"\bfinance\b|banking|investment|equity research|consulting|accounting|\bbmc\b",
    "Hardware": r"embedded|firmware|fpga|pcb|circuit|robotics|microcontroller|verilog",
    "Product/Design": r"product manag|\bux\b|\bui\b design|figma|user research",
}


def infer_targets(skills: list[str], experiences: list[dict], program: str, evidence: str = "") -> list[str]:
    score: Counter = Counter()
    for s in skills:
        for i, c in enumerate(skill_categories(s)):
            score[c] += 2 if i == 0 else 1
    from .textutil import classify_category
    for e in experiences:
        cat = classify_category(e.get("title", ""))
        if cat != "Other":
            score[cat] += 4
    p = (program or "").lower()
    if any(k in p for k in ("software", "computer", "computing", "information tech")):
        score["Software"] += 5
    if any(k in p for k in ("electrical", "mechatronics", "mechanical", "biomedical", "engineering science")):
        score["Hardware"] += 4
    if any(k in p for k in ("statistic", "data", "actuarial", "mathematics", "physics")):
        score["AI/ML/Data"] += 3
        score["Quant"] += 2
    if any(k in p for k in ("commerce", "business", "finance", "accounting", "economics")):
        score["Business/Finance"] += 5
    for cat, rx in _TARGET_EVIDENCE.items():  # projects, minors, awards: up to +8 each
        score[cat] += min(8, 3 * len(re.findall(rx, evidence or "", re.I)))
    if not score:
        return ["Software"]
    top = score.most_common()
    best = top[0][1]
    return [c for c, v in top if v >= best * 0.3 and c in CATEGORIES][:3]


def _summary(p: dict) -> str:
    bits = []
    if p["year_of_study"]:
        bits.append(f"Year {p['year_of_study']}")
    if p["program"]:
        bits.append(p["program"])
    if p["university"]:
        bits.append(f"at {p['university']}")
    s = " ".join(bits) or "Student"
    if p["coop_terms_completed"]:
        s += f" with {p['coop_terms_completed']} internship/co-op term{'s' if p['coop_terms_completed'] != 1 else ''}"
    elif p["projects"]:
        s += f" with {len(p['projects'])} projects"
    if p["skills"]:
        s += f" · strongest in {', '.join(p['skills'][:4])}"
    return s


# --- rule-based assessment ------------------------------------------------------------

_WEAK_VERBS = re.compile(r"^(worked on|helped|assisted|responsible for|was responsible|participated|involved in|"
                         r"tasked with|duties included)", re.I)


def heuristic_assessment(profile: dict, text: str) -> dict:
    exps, projs, skills = profile.get("experiences", []), profile.get("projects", []), profile.get("skills", [])
    n_exp = sum(1 for e in exps if e.get("is_internship")) or len(exps)
    gpa = profile.get("gpa_normalized")
    bullets = [b for e in exps for b in e.get("highlights", [])] + [p.get("summary", "") for p in projs]
    with_metrics = sum(1 for b in bullets if re.search(r"\d", b))
    weak = [b for b in bullets if _WEAK_VERBS.match(b)]
    words = len(text.split())

    points = min(n_exp, 4) * 2 + min(len(projs), 4) + min(len(skills), 15) / 5
    if gpa:
        points += 2 if gpa >= 3.7 else 1 if gpa >= 3.3 else 0
    level = "Strong" if points >= 11 else "Competitive" if points >= 7 else "Developing" if points >= 4 else "Beginner"

    strengths, gaps, tips, steps = [], [], [], []
    if n_exp >= 2:
        strengths.append(f"{n_exp} prior internships/co-ops, which recruiters screen for first")
    elif n_exp == 1:
        strengths.append("Has a prior internship/co-op, so you're past the hardest hurdle")
    else:
        gaps.append("No internships yet: lean on projects, hackathons and smaller startups for the first term")
    if len(projs) >= 3:
        strengths.append(f"{len(projs)} projects show initiative beyond coursework")
    elif len(projs) <= 1:
        gaps.append("Few projects listed: 2-3 shipped projects with real users or measurable results stand out")
    if gpa and gpa >= 3.7:
        strengths.append(f"Strong GPA ({profile.get('gpa')}), which helps at banks, quant firms and big tech")
    elif gpa and gpa < 3.0:
        tips.append("Consider leaving GPA off; many startups and big tech roles don't ask for it")
    if len(skills) >= 10:
        strengths.append("Broad technical stack: " + ", ".join(skills[:6]))
    elif len(skills) < 5:
        gaps.append("Thin skills section: list the languages, frameworks and tools you've actually used")
    if profile.get("awards"):
        strengths.append("Scholarships or dean's list recognition")
    if bullets and with_metrics / len(bullets) < 0.4:
        tips.append(f"Only {with_metrics}/{len(bullets)} bullets have numbers; quantify impact "
                    "(latency cut 40%, 2k users, $15k saved)")
    if weak:
        tips.append(f"Rewrite weak openers like “{weak[0][:60]}…” with strong verbs (Built, Shipped, Reduced, Led)")
    if words > 750:
        tips.append(f"Resume is ~{words} words; tighten to one page for co-op applications")
    if not any("github" in l for l in profile.get("links", [])):
        tips.append("Add a GitHub (or portfolio) link to the header so reviewers can see your code")
    if not any("linkedin" in l for l in profile.get("links", [])):
        tips.append("Add your LinkedIn URL; recruiters cross-check it")

    cats = profile.get("target_categories") or ["Software"]
    has_quant = "Quant" in cats or any(s in skills for s in ("C++", "Statistics", "OCaml/Haskell"))

    def outlook(base: float) -> str:
        return "Likely" if base >= 2.6 else "Target" if base >= 1.4 else "Reach"

    exp_pts = min(n_exp, 3) * 0.8 + (0.6 if len(projs) >= 3 else 0.2 * len(projs))
    gpa_pts = 0.6 if (gpa or 0) >= 3.7 else 0.3 if (gpa or 0) >= 3.3 else 0
    tier_outlook = {
        "yc": outlook(exp_pts + 0.9 + (0.3 if len(projs) >= 3 else 0)),
        "startup": outlook(exp_pts + 0.8),
        "finance": outlook(exp_pts + gpa_pts + 0.6),
        "bigtech": outlook(exp_pts + gpa_pts - 0.4),
        "quant": outlook(exp_pts + gpa_pts - 1.0 + (0.5 if has_quant else 0)),
    }

    if n_exp == 0:
        steps.append("Apply broadly to YC/early-stage startups and campus co-op postings; they weigh projects heavily")
    steps.append("Apply in the first 48 hours after a posting goes live; Co-op Coach pings you for that")
    if "Quant" in cats:
        steps.append("For quant: practise probability/mental math (Zetamac, Green Book) before OAs open")
    if "Software" in cats:
        steps.append("Keep 2-3 LeetCode mediums a week going through recruiting season")
    steps.append("Book coffee chats with alumni at your top 5 favourite companies (see Events)")

    headline = f"{level} candidate for {', '.join(cats[:2])} roles"
    return {
        "headline": headline,
        "level": level,
        "strengths": strengths[:5],
        "gaps": gaps[:4],
        "resume_tips": tips[:5],
        "tier_outlook": tier_outlook,
        "suggested_roles": [f"{c} intern" for c in cats],
        "next_steps": steps[:5],
    }
