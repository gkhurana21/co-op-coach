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
                  r"indigenous|aboriginal|visible minorit|demographic|self.?identif", re.I)


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
                    if not any(x in l for x in ("linkedin.com", "github.com"))), "")
    exp = (r or {}).get("experience") or []
    # never hand a typo'd address to an employer: use the first email that's actually valid
    email = next((e.strip() for e in (h.get("email"), prof.get("email")) if e and ".." not in e
                  and re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", e.strip())), h.get("email") or prof.get("email", ""))
    digits = re.sub(r"\D", "", h.get("phone") or "")
    phone = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else (h.get("phone") or "")
    return {"first_name": first, "last_name": last, "full_name": name, "email": email,
            "phone": phone, "location": h.get("location") or "", "linkedin": pick("linkedin.com"),
            "github": pick("github.com"), "website": website, "school": prof.get("university", ""),
            "degree": f"Bachelor's in {prof.get('program')}" if prof.get("program") else "",
            "discipline": prof.get("program", ""), "grad_date": prof.get("grad_date") or str(prof.get("grad_year") or ""),
            "gpa": prof.get("gpa", ""), "current_company": exp[0]["company"] if exp else "",
            "current_title": exp[0]["title"] if exp else ""}


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


def _rule_answer(label: str, kind: str, options: list[str], basics: dict, job: dict, prefs: dict,
                 letter: str) -> tuple[str, bool, str]:
    """(answer, needs_you, note) for a common question, or ("", True, "") when it needs a human or the AI."""
    l = label.lower()
    authorized, sponsor, note = _work_answers(job, prefs)

    def choose(*want):
        for w in want:
            for o in options:
                if w.lower() in o.lower():
                    return o
        return want[0] if not options else ""

    if _EEO.search(l):
        return "", True, "Voluntary: your choice (Co-op Coach never answers these)"
    table = [
        (r"^first name|given name|preferred first", basics["first_name"]),
        (r"^last name|family name|surname", basics["last_name"]),
        (r"^(full )?name$", basics["full_name"]),
        (r"e-?mail", basics["email"]),
        (r"phone|mobile", basics["phone"]),
        (r"linkedin", basics["linkedin"]),
        (r"github", basics["github"]),
        (r"website|portfolio|personal site", basics["website"] or basics["github"]),
        (r"school|university|college|institution", basics["school"]),
        (r"discipline|field of study|major|program", basics["discipline"]),
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
    if re.search(r"how did you (hear|find)|source", l):
        return choose("Company website", "Career site", "Website", "Job board", "Other"), False, ""
    if re.search(r"(currently )?enrolled|returning to school|student", l) and options:
        return choose("Yes"), False, ""
    if re.search(r"relocat", l):
        return choose("Yes"), True, "Check: are you willing to relocate for this one?"
    if re.search(r"start date|available to start|availability", l):
        terms = prefs.get("terms") or job.get("terms") or []
        if terms:
            season, _, year = terms[0].partition(" ")
            return f"{_START.get(season.lower(), season)} {year}".strip(), True, "Check the start date"
        return "", True, ""
    if re.search(r"office|location (do you|would you) prefer|preferred location", l) and options:
        want = [x for x in (prefs.get("locations") or []) if x not in ("Canada", "US", "Remote")] + ["Toronto", "Remote"]
        for w in want:
            for o in options:
                if w.lower() in o.lower():
                    return o, False, ""
        return "", True, ""
    if re.search(r"(u\.?s\.?|united states) state do you|which state|province", l):
        return "", True, "Only answer if you actually live there"
    if re.search(r"18 years|age", l):
        return "", True, ""
    if re.search(r"resume|cv", l):
        return "(attach your tailored resume)", False, ""
    return "", True, ""


def _ai_answers(questions: list[dict], job: dict, resume_text: str, desc: str) -> dict[str, str]:
    """Short first-person answers to free-text questions, from the resume only."""
    todo = [q for q in questions if q["needs_you"] and q["kind"] in ("textarea", "input_text") and not q["answer"]
            and not _EEO.search(q["label"]) and len(q["label"]) > 12][:5]
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
        out = ai._call_json(system, [{"type": "text", "text": text}], schema, effort="medium", max_tokens=3000)
    except Exception as e:
        log.warning("AI application answers failed: %s", e)
        return {}
    have = set(re.findall(r"\d+(?:\.\d+)?", resume_text))
    out = ai._clean(out)
    return {a["label"]: a["answer"] for a in out.get("answers") or []
            if not set(re.findall(r"\d+(?:\.\d+)?", a["answer"])) - have}  # drop anything with made-up numbers


def build_kit(pid: int, job_id: str, letter: bool = True, letter_fmt: str | None = None, ai_answers: bool = True) -> dict:
    """Answers to the application's questions, a tailored resume and (optionally) a cover letter."""
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
            made = routes_docs.generate_letter(pid, j["id"], letter_fmt)
            letter_doc = {"id": made.get("id"), "content": made["content"]}
        except Exception as e:
            log.warning("kit letter failed: %s", e)
            letter_doc = {"id": None, "content": ""}
    letter_doc = letter_doc or {"id": None, "content": ""}
    letter_text = letter_doc["content"] or ""

    raw = []
    if _ats(job["url"], job["source"]) == "greenhouse":
        gh = ats.greenhouse_job(job["url"], job["source"])
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
                         jobdetail.ensure_description(j["id"]) or "") if ai_answers else {}
    for q in questions:
        if q["label"] in drafts:
            q["answer"], q["needs_you"], q["note"] = drafts[q["label"]], True, "Drafted from your resume: make it yours"
    tpl = p["prefs"].get("resume_template", "jake")
    return {"job": {"id": j["id"], "company": j["company"], "title": j["title"], "url": j["url"],
                    "apply_url": apply_url(job), "ats": _ats(job["url"], job["source"]), "eligibility": j.get("eligibility"),
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
        "SELECT content FROM documents WHERE id=?", (kit.get("letter_id"),)) or {}).get("content", "")
    if letter:
        h = (r or {}).get("header") or {}
        path = out_dir / f"{who}_Cover_Letter.pdf"
        path.write_bytes(render.pdf(render.letter_html(letter, h)))
        files["letter"] = str(path)
    return files


_FILL_JS = """(answers) => {
  const norm = (s) => (s || '').toLowerCase().replace(/[*:]/g, ' ').replace(/\\s+/g, ' ').trim();
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
    if (el.disabled || el.readOnly || (el.value && el.tagName !== 'SELECT')) continue;
    const lab = norm(labelOf(el)); if (!lab) continue;
    let best = null;
    for (const a of answers) { const k = norm(a.label); if (!a.answer || a.answer.startsWith('(')) continue;
      // short labels ("name", "email") must match exactly, so "Name of your university" never gets your name
      if (lab === k || (k.length >= 8 && (lab.startsWith(k) || k.startsWith(lab))) || (k.length > 12 && lab.includes(k))) { best = a; break; } }
    if (!best) continue;
    if (el.tagName === 'SELECT') { const o = [...el.options].find((o) => norm(o.text) === norm(best.answer) || norm(o.text).startsWith(norm(best.answer)));
      if (!o) continue; el.value = o.value; el.dispatchEvent(new Event('change', {bubbles: true})); }
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


def fill(pid: int, job_id: str) -> None:
    """Open the application in a visible Chrome window and fill it in, in the background. Never submits."""
    if (_state.get(pid) or {}).get("state") == "running":
        return
    _state[pid] = {"state": "running", "message": "Preparing your kit…"}

    def run():
        try:
            from playwright.sync_api import sync_playwright

            from .workday import PROFILES, _launch
            kit = build_kit(pid, job_id)
            if kit.get("error"):
                _state[pid] = {"state": "error", "message": kit["error"]}
                return
            files = _write_files(pid, kit)
            answers = [{"label": q["label"], "answer": q["answer"]} for q in kit["questions"]] + _basic_answers(kit["basics"])
            with sync_playwright() as p:
                ctx = _launch(p, PROFILES / f"apply-{pid}", headless=False)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(kit["job"]["apply_url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)
                filled = []
                for frame in [page.main_frame, *page.frames[1:]]:
                    try:
                        filled += frame.evaluate(_FILL_JS, answers)
                    except Exception:
                        pass
                    for kind, path in files.items():  # resume / cover letter uploads
                        for inp in frame.query_selector_all("input[type=file]"):
                            lab = (frame.evaluate("(el) => (el.closest('div,fieldset,label')||el).innerText || el.name || el.id", inp) or "").lower()
                            if (kind == "resume" and re.search(r"resume|cv", lab)) or (kind == "letter" and "cover" in lab):
                                try:
                                    inp.set_input_files(path)
                                    filled.append(f"{kind} file")
                                except Exception:
                                    pass
                captcha = page.query_selector("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile']")
                _state[pid] = {"state": "open", "filled": sorted(set(filled)),
                               "message": f"Filled {len(set(filled))} fields. Check everything, finish the rest"
                                          + (", solve the CAPTCHA" if captcha else "") + ", then press Submit yourself."}
                # keep the window open until the person closes it
                try:
                    while ctx.pages:
                        ctx.pages[0].wait_for_timeout(1500)
                except Exception:
                    pass  # they closed the window
                _state[pid] = {"state": "closed", "filled": sorted(set(filled)),
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
