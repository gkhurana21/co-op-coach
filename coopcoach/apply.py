"""Apply assistant: everything needed to apply to one posting, and a browser helper that fills the form.

build_kit() answers the posting's real application questions (Greenhouse publishes its forms) or the usual ones
(work authorization, sponsorship, start date, links), drafts free-text answers from the resume without inventing
anything, and points at a tailored resume + cover letter. fill() opens the application in Chrome and fills what it
can. It never presses Submit and never touches CAPTCHAs: the student reviews and submits.
"""

import logging
import re
import threading

from . import ai, config, db, store

log = logging.getLogger(__name__)

# Voluntary self-identification is the student's call, every time: never auto-answered.
_EEO = re.compile(r"gender|pronoun|race|ethnic|hispanic|latin|veteran|disabilit|sexual orientation|transgender|"
                  r"indigenous|aboriginal|visible minorit|demographic|self.?identif|nationality|citizenship|"
                  r"country of birth|place of birth|birthplace", re.I)
_CREDENTIAL = re.compile(r"\b(?:log[ -]?in|sign[ -]?in|user[ -]?name|password|passcode|one[ -]?time code|verification code)\b", re.I)


def _ats(url: str, source: str) -> str:
    for name in ("greenhouse", "lever", "ashby", "workday", "smartrecruiters", "icims"):
        if name in (source or "") or name in (url or ""):
            return name
    return "other"


def apply_url(job: dict) -> str:
    """The page with the application form itself."""
    url, ats = job["url"], _ats(job["url"], job.get("source", ""))
    if ats == "lever" and not url.rstrip("/").endswith("/apply"):
        return url.rstrip("/") + "/apply"
    if ats == "ashby" and "/application" not in url:
        return url.rstrip("/") + "/application"
    if ats == "greenhouse":
        m = re.search(r"gh_jid=(\d+)|/jobs/(\d+)", url)
        slug = (job.get("source") or "").split(":", 1)[-1]
        if m and slug:
            return f"https://job-boards.greenhouse.io/{slug}/jobs/{m.group(1) or m.group(2)}"
    return url


def _basics(p: dict, r: dict) -> dict:
    h = (r or {}).get("header") or {}
    prof = p["profile"]
    name = (h.get("name") or prof.get("name") or "").strip()
    first, _, last = name.partition(" ")
    links = [x for x in (h.get("links") or prof.get("links") or []) if x]
    pick = lambda host: next((l if l.startswith("http") else "https://" + l for l in links if host in l), "")  # noqa: E731
    website = next((l if l.startswith("http") else "https://" + l for l in links
                    if not any(x in l for x in ("linkedin.com", "github.com", "devpost.com"))), "")
    exp = (r or {}).get("experience") or []
    # never hand a typo'd address to an employer: use the first email that's actually valid
    email = next((e.strip() for e in (h.get("email"), prof.get("email")) if e and ".." not in e
                  and re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", e.strip())), h.get("email") or prof.get("email", ""))
    digits = re.sub(r"\D", "", h.get("phone") or "")
    phone = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else (h.get("phone") or "")
    return {"first_name": first, "last_name": last, "full_name": name, "email": email,
            "phone": phone, "location": h.get("location") or "",
            "country": h.get("country") or prof.get("country") or "", "linkedin": pick("linkedin.com"),
            "github": pick("github.com"), "website": website, "school": prof.get("university", ""),
            "degree": (f"{_degree(prof)} in {prof['program']}" if _degree(prof) else prof["program"])
            if prof.get("program") else "",
            "discipline": prof.get("program", ""), "grad_date": prof.get("grad_date") or str(prof.get("grad_year") or ""),
            "gpa": prof.get("gpa", ""), "current_company": exp[0]["company"] if exp else "",
            "current_title": exp[0]["title"] if exp else ""}


def _degree(prof: dict) -> str:
    level = prof.get("degree_level") or "Bachelor's"
    return level if level in ("Bachelor's", "Master's", "PhD", "Diploma") else ""


def _work_answers(job: dict, prefs: dict) -> tuple[str, str, str]:
    """(authorized?, needs sponsorship?, note) for the country this job is in."""
    auth = prefs.get("citizenship") or "Canadian"
    regions = set(job.get("regions") or [])
    us_job = "US" in regions and "Canada" not in regions
    if auth == "US":
        return "Yes", "No", ""
    if not us_job:  # Canada (or remote from Canada)
        if auth == "Canadian":
            return "Yes", "No", ""
        if auth == "Intl-CA":
            return "Yes", "No", "Check: answers assume your co-op work permit covers this term"
        return "", "", "Check your work authorization for this country"
    if auth == "F1":
        return "Yes", "Yes", "Check: CPT/OPT usually counts as authorized; some forms want 'Yes' to sponsorship later"
    return "No", "Yes", "Check: a US internship usually needs a J-1 (or TN) for you; answer honestly"


_START = {"winter": "January", "spring": "May", "summer": "May", "fall": "September"}
_MONTHS = {"jan": "January", "feb": "February", "mar": "March", "apr": "April", "may": "May", "jun": "June", "jul": "July",
           "aug": "August", "sep": "September", "oct": "October", "nov": "November", "dec": "December"}


def _rule_answer(label: str, kind: str, options: list[str], basics: dict, job: dict, prefs: dict,
                 letter: str) -> tuple[str, bool, str]:
    """(answer, needs_you, note) for a common question, or ("", True, "") when it needs a human or the AI."""
    l = label.lower()
    authorized, sponsor, note = _work_answers(job, prefs)

    def choose(*want):  # the option that says it, not one that happens to contain it ("No" isn't "Not sure")
        for w in want:
            i = _pick_option(w, options)
            if i is not None:
                return options[i]
        return want[0] if not options else ""

    if _EEO.search(l):
        return "", True, "Voluntary: your choice (Co-op Coach never answers these)"
    if _CREDENTIAL.search(l):
        return "", True, "Sign in yourself; Co-op Coach never enters credentials"
    if re.search(r"high school|secondary school", l):  # not the university the rules below would give
        return "", True, ""
    # Work authorization is not a residence or nationality. Only use an explicit contact country.
    home = basics.get("country") or ""
    if not home:
        location = str(basics.get("location") or "").split(",")[-1].strip().lower()
        home = {"canada": "Canada", "united states": "United States", "usa": "United States", "us": "United States"}.get(location, "")
    grad = re.search(r"(?:([A-Za-z]{3,9})\s+)?(20\d\d)", str(basics.get("grad_date") or ""))
    table = [
        (r"^(?:country(?: of (?:residence|residency))?|current country|country/region)$", home),
        (r"^location\b|\bcurrent location\b|^city\b", basics.get("location") or ""),
        (r"^end date year|graduation year|year of graduation", grad.group(2) if grad else ""),
        (r"^end date month|graduation month", _MONTHS.get((grad.group(1) or "")[:3].lower(), "") if grad else ""),
        (r"^first name|given name|preferred first", basics["first_name"]),
        (r"^last name|family name|surname", basics["last_name"]),
        (r"^(full )?name$", basics["full_name"]),
        (r"e-?mail", basics["email"]),
        (r"phone|^mobile$|mobile (number|no)|\bcell\b", basics["phone"]),  # not "mobile development"
        (r"linkedin", basics["linkedin"]),
        (r"github", basics["github"]),
        (r"website|portfolio|personal site", basics["website"] or basics["github"]),
        (r"school|university|college|institution", basics["school"]),
        (r"discipline|field of study|\bmajor\b|^program\b|program of study", basics["discipline"]),  # not "Koch Internship Program"
        (r"^degree", basics["degree"]),
        (r"graduat", basics["grad_date"]),
        (r"\bgpa\b|grade point", basics["gpa"]),
        (r"current (company|employer)|most recent employer", basics["current_company"]),
        (r"current (title|role)", basics["current_title"]),
        (r"cover letter", letter),
    ]
    for rx, val in table:
        if re.search(rx, l):
            return val, not val, ""
    if re.search(r"(legally )?authori[sz]ed to work|eligible to work|right to work", l):
        return choose(authorized) if authorized else "", not authorized or bool(note), note
    if re.search(r"sponsor", l):
        if kind in ("input_text", "textarea") and "type of support" in l:
            return ("J-1 visa (or TN) for the internship term" if sponsor == "Yes" else ""), True, note
        return choose(sponsor) if sponsor else "", not sponsor or bool(note), note
    if re.search(r"(how|where) did you (hear|find|learn)|referral source|^source$", l):  # not "open-source"
        return choose("Company website", "Career site", "Website", "Job board", "Other"), False, ""
    if re.search(r"enrolled|returning to school|are you (a |currently a )?(current )?student|still (a )?student", l) and options \
            and not re.search(r"doctoral|ph\.?d|master|mba|graduate (program|school|degree)|high school", l):
        return choose("Yes"), False, ""
    if re.search(r"relocat", l):
        return choose("Yes"), True, "Check: are you willing to relocate for this one?"
    if re.search(r"start date|available to start|availability", l):
        terms = prefs.get("terms") or job.get("terms") or []
        if terms:
            season, _, year = terms[0].partition(" ")
            return f"{_START.get(season.lower(), season)} {year}".strip(), True, "Check the start date"
        return "", True, ""
    if re.search(r"office|location (do you|would you) prefer|preferred (work |office )?location", l) and options:
        want = [x for x in (prefs.get("locations") or []) if x not in ("Canada", "US", "Remote")] + ["Toronto", "Remote"]
        for w in want:
            for o in options:
                if w.lower() in o.lower():
                    return o, False, ""
        return "", True, ""
    if re.search(r"(u\.?s\.?|united states) state do you|which state|province", l):
        return "", True, "Only answer if you actually live there"
    if re.search(r"18 years|\bage\b|years of age|how old", l):
        return "", True, ""
    if re.search(r"resume|cv", l):
        return "(attach your tailored resume)", False, ""
    return "", True, ""


def _ai_answers(questions: list[dict], job: dict, resume_text: str, desc: str) -> dict[str, str]:
    """Short first-person answers to free-text questions, from the resume only."""
    todo = [q for q in questions if q["needs_you"] and q["kind"] in ("textarea", "input_text") and not q["answer"]
            and not _EEO.search(q["label"]) and not _CREDENTIAL.search(q["label"]) and len(q["label"]) > 12][:5]
    if not todo or not ai.available():
        return {}
    schema = ai._obj({"answers": {"type": "array", "items": ai._obj({"label": ai._STR, "answer": ai._STR})}})
    system = ("You draft answers to a job application's free-text questions for a student, in first person, 2-4 "
              "sentences each, plain and specific. " + ai._RESUME_RULES +
              " For motivation questions, connect real resume facts to the posting; leave a short [bracket] only for "
              "something only the student can say. Return one answer per question, label copied exactly.")
    text = (f"Posting: {job['company']} - {job['title']}\n{desc[:2500]}\n\nResume:\n{resume_text[:3500]}\n\n"
            "Questions:\n" + "\n".join(f"- {q['label']}" for q in todo))
    try:
        out = ai._call_json(system, [{"type": "text", "text": text}], schema, effort="medium", max_tokens=3000, job="answers")
    except Exception as e:
        log.warning("AI application answers failed: %s", e)
        return {}
    have = set(re.findall(r"\d+(?:\.\d+)?", resume_text))
    out = ai._clean(out)
    return {a["label"]: a["answer"] for a in out.get("answers") or []
            if not set(re.findall(r"\d+(?:\.\d+)?", a["answer"])) - have}  # drop anything with made-up numbers


QUICK_FORM_WAIT = 1.5  # seconds a quick kit waits for the form's questions


def build_kit(pid: int, job_id: str, letter: bool = True, letter_fmt: str | None = None, ai_answers: bool = True,
              quick: bool = False) -> dict:
    """Answers to the application's questions, a tailored resume and (optionally) a cover letter. quick: whatever can be done
    at once, with no AI and at most a moment's wait on the form, for polish.py to improve in the background."""
    from . import jobdetail, routes_docs
    from .sources import ats
    j = next((x for x in store.scored_jobs(pid) if job_id in x["alt_ids"] or x["id"] == job_id), None)
    if not j:
        return {"error": "no such posting"}
    row = db.row("SELECT source, url FROM jobs WHERE id=?", (j["id"],)) or {}
    job = {**j, "source": row.get("source") or j["source"]}
    p = store.get_profile(pid)
    r = routes_docs._resume_for(pid, j["id"]) or {}
    basics = _basics(p, r)
    letter_doc = db.row("SELECT id, content FROM documents WHERE profile_id=? AND kind='cover_letter' AND job_id=?"
                        " ORDER BY updated_at DESC LIMIT 1", (pid, j["id"]))
    if not letter_doc and letter:
        try:
            made = routes_docs.generate_letter(pid, j["id"], letter_fmt, quick=quick)
            letter_doc = {"id": made.get("id"), "content": made["content"]}
        except Exception as e:
            log.warning("kit letter failed: %s", e)
            letter_doc = {"id": None, "content": ""}
    letter_doc = letter_doc or {"id": None, "content": ""}
    letter_text = letter_doc["content"] or ""

    raw = []
    if _ats(job["url"], job["source"]) == "greenhouse":
        try:
            gh = ats.greenhouse_job(job["url"], job["source"], **({"timeout": QUICK_FORM_WAIT} if quick else {}))
        except Exception as e:
            if not quick:
                raise
            log.info("the form didn't answer in time; the usual questions for now: %s", e)
            gh = None
        for q in (gh or {}).get("questions") or []:
            fields = q.get("fields") or [{}]
            f = fields[0]
            raw.append({"label": (q.get("label") or "").strip(), "required": bool(q.get("required")),
                        "kind": f.get("type") or "input_text", "name": f.get("name") or "",
                        "options": [v.get("label") for v in f.get("values") or [] if v.get("label")]})
    if not raw:  # forms we can't read ahead of time: the questions almost every application asks
        for label, kind in [("First Name", "input_text"), ("Last Name", "input_text"), ("Email", "input_text"),
                            ("Phone", "input_text"), ("LinkedIn Profile", "input_text"), ("Website", "input_text"),
                            ("GitHub", "input_text"), ("School", "input_text"), ("Degree", "input_text"),
                            ("Graduation date", "input_text"), ("Are you legally authorized to work in this country?", "select"),
                            ("Will you now or in the future require sponsorship?", "select"),
                            ("When can you start?", "input_text"), ("How did you hear about us?", "input_text"),
                            ("Why do you want to work here?", "textarea")]:
            raw.append({"label": label, "required": False, "kind": kind, "name": "", "options": ["Yes", "No"] if kind == "select" else []})

    questions = []
    for q in raw:
        if q["kind"] in ("input_file",) and re.search(r"resume|cv", q["label"], re.I):
            questions.append({**q, "answer": "(attach your tailored resume)", "needs_you": False, "note": "", "file": "resume"})
            continue
        if q["kind"] == "input_file":
            questions.append({**q, "answer": "(attach your cover letter)" if "cover" in q["label"].lower() else "",
                              "needs_you": "cover" not in q["label"].lower(), "note": "", "file": "letter"})
            continue
        ans, needs, note = _rule_answer(q["label"], q["kind"], q["options"], basics, job, p["prefs"], letter_text)
        questions.append({**q, "answer": ans, "needs_you": needs, "note": note})
    from . import resume_studio as rs
    drafts = _ai_answers(questions, job, rs.plain_text(rs.normalize(r)) if r else "",
                         jobdetail.ensure_description(j["id"]) or "") if ai_answers and not quick else {}
    for q in questions:
        if q["label"] in drafts:
            q["answer"], q["needs_you"], q["note"] = drafts[q["label"]], True, "Drafted from your resume: make it yours"
    tpl = p["prefs"].get("resume_template", "jake")
    return {"job": {"id": j["id"], "company": j["company"], "title": j["title"], "url": j["url"],
                    "apply_url": apply_url(job), "ats": _ats(job["url"], job["source"]), "eligibility": j.get("eligibility"),
                    "regions": j.get("regions") or [], "terms": j.get("terms") or [],
                    "who_can_apply": [x["text"] for x in j.get("requirements") or []][:4]},
            "basics": basics, "questions": questions,
            "files": {"resume_pdf": f"/api/resume/export?profile_id={pid}&job_id={j['id']}&fmt=pdf&template={tpl}",
                      "letter_pdf": f"/api/letters/{letter_doc['id']}/export?fmt=pdf" if letter_doc.get("id") else ""},
            "letter_id": letter_doc.get("id")}


# --- fill the form in Chrome --------------------------------------------------------------------------------

_state: dict[int, dict] = {}


def fill_state(pid: int) -> dict:
    return _state.get(pid) or {"state": "idle"}


def _write_files(pid: int, kit: dict) -> dict[str, str]:
    from . import render, routes_docs
    out_dir = config.DATA_DIR / "cache" / "apply" / str(pid)
    out_dir.mkdir(parents=True, exist_ok=True)
    who = render.safe_name(kit["basics"]["full_name"] or "Resume")
    files = {}
    r = routes_docs._resume_for(pid, kit["job"]["id"])
    if r:
        tpl = store.get_profile(pid)["prefs"].get("resume_template", "jake")
        path = out_dir / f"{who}_Resume.pdf"
        path.write_bytes(render.pdf(render.resume_html(r, tpl)))
        files["resume"] = str(path)
    letter = next((q["answer"] for q in kit["questions"] if "cover letter" in q["label"].lower() and q["answer"]
                   and not q["answer"].startswith("(")), "") or (db.row(
        "SELECT content FROM documents WHERE id=? AND profile_id=? AND kind='cover_letter'", (kit.get("letter_id"), pid)) or {}).get("content", "")
    if letter:
        h = (r or {}).get("header") or {}
        path = out_dir / f"{who}_Cover_Letter.pdf"
        path.write_bytes(render.pdf(render.letter_html(letter, h)))
        files["letter"] = str(path)
    return files


_FIELD_SAFETY_JS = r"""
  // Use the same exclusions in every pass, including dynamically discovered fields.
  const visible = (el) => {
    if (el.dataset.automationId === 'beecatcher' || el.name === 'beecatcher' || el.closest('[aria-hidden="true"], [hidden], [inert]')) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1 || r.right <= 0 || r.bottom <= 0) return false;
    for (let p = el; p; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.visibility === 'hidden' || cs.visibility === 'collapse' || cs.display === 'none' || Number(cs.opacity) === 0) return false;
    }
    return true;
  };
  const safeField = (el, label = '') => {
    if (!visible(el) || el.disabled || el.readOnly || el.type === 'password') return false;
    const autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/);
    if (autocomplete.some((x) => ['username', 'current-password', 'new-password', 'one-time-code'].includes(x))) return false;
    const info = [label, el.name, el.id].filter(Boolean).join(' ').replace(/[_-]/g, ' ');
    if (/\b(log\s?in|sign\s?in|user\s?name|password|passcode|one time code|verification code)\b/i.test(info)) return false;
    if (/gender|pronoun|race|ethnic|hispanic|latin|veteran|disabilit|sexual orientation|transgender|indigenous|aboriginal|visible minorit|demographic|self.?identif|nationality|citizenship|country of birth|place of birth|birthplace/i.test(label)) return false;
    // Password fields identify sign-in/account-creation context even when the email label is just "Email".
    const scope = el.closest('form, [role="dialog"]') || document;
    return ![...scope.querySelectorAll('input[type="password"]')].some(visible);
  };
"""


_FILL_JS = """(answers) => {""" + _FIELD_SAFETY_JS + """
  const norm = (s) => (s || '').toLowerCase().replace(/[*:✱†‡•]/g, ' ').replace(/\\((?:required|optional)\\)/g, ' ').replace(/\\s+/g, ' ').trim();
  const labelOf = (el) => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const wrap = el.closest('label'); if (wrap) return wrap.innerText;
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const lb = el.getAttribute('aria-labelledby'); if (lb) { const t = document.getElementById(lb); if (t) return t.innerText; }
    let p = el.parentElement; for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      const l = p.querySelector('label, legend, .label, [class*=label]'); if (l && l.innerText.length < 200) return l.innerText; }
    return el.getAttribute('placeholder') || el.name || '';
  };
  const filled = [], seen = new Set();
  const set = (el, v) => { const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); el.dispatchEvent(new Event('blur', {bubbles: true})); };
  for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=file]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio]), textarea, select')) {
    if ((el.value && el.tagName !== 'SELECT') || !safeField(el, labelOf(el))) continue;
    if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') || el.classList.contains('location-input')) continue;
    const lab = norm(labelOf(el)); if (!lab) continue;
    let best = null;
    for (const a of answers) { const k = norm(a.label); if (!a.answer || a.answer.startsWith('(')) continue;
      // short labels ("name", "email") must match exactly, so "Name of your university" never gets your name
      if (lab === k || (k.length >= 8 && (lab.startsWith(k) || k.startsWith(lab))) || (k.length > 12 && lab.includes(k))) { best = a; break; } }
    if (!best) continue;
    if (el.tagName === 'SELECT') { const a = norm(best.answer), options = [...el.options];
      const o = options.find((o) => norm(o.text) === a) || options.find((o) => [a + ' ', a + ',', a + ';'].some((prefix) => norm(o.text).startsWith(prefix)));
      if (!o) continue; Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, o.value); el.dispatchEvent(new Event('change', {bubbles: true})); }
    else set(el, best.answer);
    filled.push(best.label); seen.add(best.label);
  }
  return filled;
}"""


def _basic_answers(b: dict) -> list[dict]:
    """The contact basics under the labels forms commonly use for them."""
    variants = {"full_name": ["name", "full name", "legal name"], "first_name": ["first name", "given name"],
                "last_name": ["last name", "family name", "surname"], "email": ["email", "email address"],
                "phone": ["phone", "phone number", "mobile", "mobile phone"], "location": ["location", "city", "current location"],
                "linkedin": ["linkedin", "linkedin profile", "linkedin url"], "github": ["github", "github url"],
                "website": ["website", "website url", "portfolio", "portfolio url", "personal website"],
                "school": ["school", "university", "school name"], "discipline": ["discipline", "major", "field of study"],
                "grad_date": ["graduation date", "expected graduation"], "current_company": ["current company"]}
    return [{"label": lab, "answer": b[k]} for k, labs in variants.items() if b.get(k) for lab in labs]


# What's left on a page once the known answers are in: searchable dropdowns, plain ones and radio buttons, and fields under a
# label the kit didn't have. Each gets a tag to find it again, its question, and its choices where they can be read.
_CHOICES_JS = """() => {""" + _FIELD_SAFETY_JS + """
  const clean = (s) => (s || '').replace(/[*✱†‡•]/g, ' ').replace(/\\((?:required|optional)\\)/gi, ' ').replace(/\\s+/g, ' ').trim();
  const labelOf = (el) => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const by = el.getAttribute('aria-labelledby'); if (by) { const t = document.getElementById(by.split(' ')[0]); if (t) return t.innerText; }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const lever = el.closest('li.application-question, .application-question'); if (lever) { const l = lever.querySelector('.application-label'); if (l) return l.innerText; }
    const wrap = el.closest('label'); if (wrap) { const c = wrap.cloneNode(true); c.querySelectorAll('select, ul, input').forEach((x) => x.remove()); return c.innerText; }
    let p = el.parentElement; for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      const l = p.querySelector('label, legend, [class*=label]'); if (l && l.innerText.length < 200 && !l.contains(el)) return l.innerText; }
    return '';
  };
  const out = []; let n = 0;
  const tag = (el) => { const i = `c${n++}`; el.setAttribute('data-cc-choice', i); return i; };
  for (const el of document.querySelectorAll('input[role=combobox], input[aria-autocomplete], input.location-input')) {
    const box = el.closest('[class*=select__control], [class*=select-shell], .select');
    if (el.value || !safeField(el, labelOf(el)) || (box && box.querySelector('[class*=single-value], [class*=singleValue]'))) continue;
    out.push({id: tag(el), kind: 'combobox', label: clean(labelOf(el))});
  }
  for (const el of document.querySelectorAll('select')) {
    if (!safeField(el, labelOf(el)) || (el.value && el.selectedIndex > 0)) continue;
    out.push({id: tag(el), kind: 'select', label: clean(labelOf(el)), options: [...el.options].slice(1).map((o) => o.text.trim()).filter(Boolean)});
  }
  const groups = {};
  for (const el of document.querySelectorAll('input[type=radio]')) { if (el.name) (groups[el.name] = groups[el.name] || []).push(el); }
  for (const list of Object.values(groups)) {
    if (list.some((e) => e.checked) || !list.every((e) => safeField(e, labelOf(e)))) continue;
    const first = list[0], set = first.closest('fieldset, [role=radiogroup], li.application-question, [class*=fieldEntry], [class*=question]');
    const q = set && set.querySelector('legend, .application-label, label:not(:has(input)), [class*=label]:not(:has(input)), [class*=title]');
    const name = (e) => { const w = e.closest('label') || (e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`)) || e.parentElement; return clean(w.innerText); };
    out.push({id: list.map(tag), kind: 'radio', label: clean(q ? q.innerText : ''), options: list.map(name)});
  }
  for (const el of document.querySelectorAll('input[type=text], input:not([type]), input[type=email], input[type=tel], input[type=url], input[type=number]')) {
    if (el.value || el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') || el.classList.contains('location-input') || !safeField(el, labelOf(el))) continue;
    const label = clean(labelOf(el)); if (label && label.length <= 40) out.push({id: tag(el), kind: 'text', label});
  }
  return out;
}"""
_OPTIONS = "[role=option], .dropdown-location"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#' ]+", " ", (s or "").lower()).strip()


def _pick_option(answer: str, options: list[str]) -> int | None:
    """Which choice says this answer: the same words, then one inside the other, then the degree's kind."""
    a = _norm(answer)
    names = [_norm(o) for o in options]
    if not a:
        return None
    # Whole-word prefixes accept "Canada (+1)" and "No, I do not need sponsorship", but never "Not sure".
    for test in (lambda n: n == a, lambda n: n and n.startswith(a + " "),
                 lambda n: n and len(n) >= 4 and a.startswith(n + " "),
                 lambda n: n and len(a) >= 4 and re.search(rf"(?<!\w){re.escape(a)}(?!\w)", n)):
        for i, n in enumerate(names):
            if test(n):
                return i
    for kind in ("bachelor", "master", "phd", "diploma"):
        if kind in a:
            return next((i for i, n in enumerate(names) if kind in n or (kind == "phd" and "doctor" in n)), None)
    return None


def asker(k: dict, prefs: dict, letter: str = ""):
    """How a question found on the page gets answered: the student's facts by the same rules as the kit, nothing more. Empty when
    it's for them to answer, and always for demographic questions."""
    job = k["job"]

    def ask(label: str, kind: str, options: list[str]) -> str:
        if not label or _EEO.search(label) or re.search(r"\breferr?(al|er|ed)?\b|\breferences?\b|emergency|\bmanager|supervisor|parent|guardian", label, re.I):  # someone else's details
            return ""
        answer, needs, _ = _rule_answer(label, "select" if options or kind != "text" else "input_text", options, k["basics"], job, prefs, letter)
        if not answer or answer.startswith("("):
            return ""
        # a guess (relocating, a start date) is theirs to make; who may work where comes from what they told us
        return "" if needs and not re.search(r"authori[sz]ed|eligible to work|right to work|sponsor", label, re.I) else answer
    return ask


def _fill_choices(page, ask) -> list[str]:
    """Answer what's left on the page. The only things it ever clicks are a dropdown's own box and one of its options."""
    filled = []
    for frame in [page.main_frame, *page.frames[1:]]:
        try:
            todo = frame.evaluate(_CHOICES_JS)
        except Exception:
            continue
        for f in todo:
            try:
                if f["kind"] == "text":
                    answer = ask(f["label"], "text", [])
                    if answer:
                        frame.locator(f'[data-cc-choice="{f["id"]}"]').fill(str(answer), timeout=1500)
                        filled.append(f["label"])
                elif f["kind"] == "select":
                    i = _pick_option(ask(f["label"], "select", f["options"]), f["options"])
                    if i is not None:
                        frame.locator(f'[data-cc-choice="{f["id"]}"]').select_option(label=f["options"][i], timeout=1500)
                        filled.append(f["label"])
                elif f["kind"] == "radio":
                    i = _pick_option(ask(f["label"], "radio", f["options"]), f["options"])
                    if i is not None:
                        frame.locator(f'[data-cc-choice="{f["id"][i]}"]').evaluate("(e) => e.click()")
                        filled.append(f["label"])
                else:
                    if not f["label"] or _EEO.search(f["label"]):
                        continue
                    box = frame.locator(f'[data-cc-choice="{f["id"]}"]')
                    box.click(timeout=1500)
                    try:  # its options appearing, not a fixed pause: a slow computer draws the list later
                        frame.wait_for_function("(q) => [...document.querySelectorAll(q)].some((o) => o.getBoundingClientRect().height > 0)",
                                                arg=_OPTIONS, timeout=1000)
                    except Exception:  # some lists show nothing until something is typed
                        pass
                    read = lambda: frame.evaluate("(q) => [...document.querySelectorAll(q)].filter((o) => o.getBoundingClientRect().height > 0)"  # noqa: E731
                                                  ".map((o) => o.innerText.trim())", _OPTIONS)
                    options = read()
                    answer = ask(f["label"], "select", options)
                    if not answer and options:  # a long list that only shows a page of it: does the question have an answer at all?
                        answer = ask(f["label"], "select", [])
                    if answer:
                        if _pick_option(answer, options) is None:  # the list only shows what's searched for
                            box.fill(re.split(r"[,(]", str(answer))[0][:30], timeout=1500)
                            box.press("End", timeout=1000)  # (some lists only search on a key press)
                            frame.wait_for_timeout(1100)
                            options = read()
                        i = _pick_option(answer, options)
                        if i is not None:
                            frame.locator(_OPTIONS).locator("visible=true").nth(i).click(timeout=1500)
                            filled.append(f["label"])
                            continue
                        box.fill("", timeout=1000)
                    page.keyboard.press("Escape")
            except Exception as e:  # one field that won't take it doesn't stop the rest, and its list doesn't stay open over them
                log.debug("choice %s: %s", f.get("label"), e)
                page.keyboard.press("Escape")
    return filled


def _fill_page(page, answers: list[dict], files: dict[str, str]) -> list[str]:
    """Fill what's on the page now, in every frame: only empty fields a person can see, and file inputs for the resume
    and cover letter that have nothing attached yet. Clicks nothing."""
    filled = []
    for frame in [page.main_frame, *page.frames[1:]]:
        try:
            filled += frame.evaluate(_FILL_JS, answers)
        except Exception:
            pass
        for kind, path in files.items():  # resume / cover letter uploads (each one can redraw the form, so look again every time)
            for i in range(frame.locator("input[type=file]").count()):
                try:
                    inp = frame.locator("input[type=file]").nth(i)
                    lab, has = inp.evaluate("""(el) => [`${(el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`) || {}).innerText || ''} ${el.id} ${el.name}
                        ${(el.closest('div,fieldset,label') || el).innerText}`, el.files && el.files.length]""", timeout=2000)
                    lab = lab.lower()
                    if has or "autofill" in lab:  # Ashby's "autofill from resume" box rewrites the form; the Resume field is the one that counts
                        continue
                    if (kind == "resume" and re.search(r"resume|\bcv\b", lab)) or (kind == "letter" and "cover" in lab):
                        inp.set_input_files(path, timeout=5000)
                        filled.append(f"{kind} file")
                except Exception as e:
                    log.debug("file field: %s", e)
    return filled


def fill(pid: int, job_id: str, kit: dict | None = None, answers: list[dict] | None = None) -> None:
    """Open the application in a visible Chrome window and fill it in, in the background. Never submits. A kit made
    elsewhere (a hackathon application) can be passed in; extra answers (a Devpost write-up) go on top. While the
    window is open, each new page (after the person signs in, or the next step of a form) is filled too."""
    if (_state.get(pid) or {}).get("state") == "running":
        return
    _state[pid] = {"state": "running", "message": "Preparing your kit…"}

    def run():
        try:
            from playwright.sync_api import sync_playwright

            from .workday import PROFILES, _launch
            k = kit or build_kit(pid, job_id)
            if k.get("error"):
                _state[pid] = {"state": "error", "message": k["error"]}
                return
            files = _write_files(pid, k) if k.get("files") else {}
            todo = (answers or []) + [{"label": q["label"], "answer": q["answer"]} for q in k["questions"]] \
                + _basic_answers(k["basics"])
            with sync_playwright() as p:
                ctx = _launch(p, PROFILES / f"apply-{pid}", headless=False)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if k["job"].get("ats") == "workday":  # a wizard of pages, behind a sign-in: its own walk-through
                    from . import workday_apply
                    say = lambda m: _state.__setitem__(pid, {"state": "running", "message": m})  # noqa: E731
                    res = workday_apply.run(page, k, files, None, say)
                    _state[pid] = {"state": "open", "filled": res["filled"], "left": res["left"],
                                   "message": f"Filled {len(res['filled'])} fields. {res['message']}"}
                    try:
                        while ctx.pages:
                            ctx.pages[0].wait_for_timeout(1500)
                    except Exception:
                        pass
                    _state[pid] = {"state": "closed", "filled": res["filled"],
                                   "message": "Window closed. If you submitted it, mark it applied."}
                    return
                page.goto(k["job"]["apply_url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)
                if "regions" not in k["job"]:  # a kit prepared before it kept these
                    row = next((x for x in store.scored_jobs(pid) if x["id"] == k["job"]["id"]), None) or {}
                    k["job"] = {**k["job"], "regions": row.get("regions") or [], "terms": row.get("terms") or []}
                ask = asker(k, (store.get_profile(pid) or {}).get("prefs") or {})
                filled = set(_fill_page(page, todo, files)) | set(_fill_choices(page, ask))

                def say() -> None:
                    captcha = None
                    try:
                        captcha = ctx.pages[-1].query_selector(
                            "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile']")
                    except Exception:
                        pass
                    _state[pid] = {"state": "open", "filled": sorted(filled),
                                   "message": "No form on this page yet. Press Apply or I'm interested on it, and the form fills when it opens." if not filled else
                                   f"Filled {len(filled)} fields. Check everything, finish the rest"
                                              + (", solve the CAPTCHA" if captcha else "")
                                              + ", then press Submit yourself. Signing in or moving to the next page?"
                                                " The new page gets filled too."}
                say()
                seen = {id(page): page.url}
                try:  # keep the window open until the person closes it; fill each new page they get to
                    while ctx.pages:
                        current = ctx.pages[-1]
                        current.wait_for_timeout(1500)
                        if seen.get(id(current)) != current.url:
                            seen[id(current)] = current.url
                            current.wait_for_timeout(2000)  # let the new page draw its form
                            got = set(_fill_page(current, todo, files)) | set(_fill_choices(current, ask))
                            if got - filled:
                                filled |= got
                                say()
                except Exception:
                    pass  # they closed the window
                _state[pid] = {"state": "closed", "filled": sorted(filled),
                               "message": "Window closed. If you submitted it, mark it applied."}
        except Exception as e:
            log.warning("autofill failed: %s", e)
            _state[pid] = {"state": "error", "message": f"Couldn't open the form: {str(e)[:160]}"}

    threading.Thread(target=run, daemon=True).start()


def agent_prompt(kit: dict, base_url: str = "http://localhost:8765") -> str:
    """Instructions for any browser agent (ChatGPT Atlas, Claude in Chrome, Gemini): fill, don't submit."""
    lines = [f"Help me apply to {kit['job']['company']}: {kit['job']['title']}.",
             f"Open {kit['job']['apply_url']} and fill the application with the answers below.",
             f"Upload my resume from {base_url}{kit['files']['resume_pdf']}"
             + (f" and cover letter from {base_url}{kit['files']['letter_pdf']}" if kit["files"]["letter_pdf"] else "") + ".",
             "Rules: do NOT press Submit; stop when the form is filled and let me review. Don't solve CAPTCHAs. Leave "
             "voluntary demographic questions for me. If a question isn't covered below, ask me instead of guessing.", "",
             "Answers:"]
    for q in kit["questions"]:
        if q["answer"] and not q["answer"].startswith("("):
            lines.append(f"- {q['label']}: {q['answer']}")
    for k, v in kit["basics"].items():
        if v and k not in ("current_title",):
            lines.append(f"- {k.replace('_', ' ')}: {v}")
    return "\n".join(lines)
