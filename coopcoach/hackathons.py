"""Hackathon packages: applying to attend one (Hack the North, UofTHacks...) and, afterwards, the Devpost submission
for what was built there, written from its GitHub repo.

* app_kit(): what a hackathon application asks for, in the same shape as an internship application kit, so
  apply.fill() fills the form in Chrome the same way (and never presses Submit). The short answers are drafted from
  the resume only: no invented projects, hackathons, awards or numbers. T-shirt size, dietary needs and travel are
  the student's, remembered once they're typed; MLH's Code of Conduct and privacy boxes, and demographic questions,
  are never touched.
* submission(): Devpost's write-up (its own headings), an elevator pitch, the "Built with" tags and a two-minute demo
  script, from the README, what the repo is built with and a few lines from the team. Anything only the team knows
  stays a [bracket] for them. The project can then go on the resume (undo puts the resume back).

Packages are keyed "hack:<name>" for the major hackathons (major_hackathons.yaml) and "hack:ev-<event id>" for
any other one in Events. Each saves what it needs about the hackathon itself: events are cleared a week after.
"""

import logging
import re
import time

from . import ai, apply, db, events, github, store

log = logging.getLogger(__name__)

# MLH's "level of study" choices (most student hackathons use MLH's registration fields)
LEVELS = {"Bachelor's": "Undergraduate University (3+ year)", "Diploma": "Undergraduate University (2 year - community "
          "college or similar)", "Master's": "Graduate University (Masters, Professional, Doctoral, etc)",
          "PhD": "Graduate University (Masters, Professional, Doctoral, etc)"}
TSHIRTS = ["XS", "S", "M", "L", "XL", "XXL"]
# never answered for anyone: agreeing to rules and policies, and voluntary self-identification
YOURS = re.compile(r"code of conduct|privacy policy|terms|i agree|consent|share my|marketing|newsletter|"
                   r"emails? from|photo release|waiver|liability", re.I)
SECTIONS = [("inspiration", "Inspiration"), ("what", "What it does"), ("how", "How we built it"),
            ("challenges", "Challenges we ran into"), ("proud", "Accomplishments that we're proud of"),
            ("learned", "What we learned"), ("next", "What's next for {project}")]
# README headings that already say what a section should
README_KEYS = {"inspiration": ("inspiration", "motivation", "why", "the problem", "problem", "background"),
               "what": ("what it does", "about", "overview", "features", "introduction", "description", "how it works"),
               "how": ("how we built it", "tech stack", "built with", "architecture", "technologies", "stack"),
               "challenges": ("challenges", "challenges we ran into"),
               "proud": ("accomplishments", "accomplishments that we're proud of", "achievements"),
               "learned": ("what we learned", "learnings", "lessons learned"),
               "next": ("whats next", "what's next", "roadmap", "future", "future work", "next steps", "todo")}
PRETTY = {"javascript": "JavaScript", "typescript": "TypeScript", "python": "Python", "java": "Java", "c++": "C++",
          "c#": "C#", "go": "Go", "rust": "Rust", "swift": "Swift", "kotlin": "Kotlin", "html": "HTML", "css": "CSS",
          "react": "React", "next.js": "Next.js", "vue.js": "Vue", "svelte": "Svelte", "express.js": "Express",
          "fastapi": "FastAPI", "flask": "Flask", "django": "Django", "postgresql": "PostgreSQL", "mongodb": "MongoDB",
          "firebase": "Firebase", "supabase": "Supabase", "openai": "OpenAI API", "claude": "Claude API",
          "gemini": "Gemini API", "pytorch": "PyTorch", "tensorflow": "TensorFlow", "scikit-learn": "scikit-learn",
          "opencv": "OpenCV", "tailwind": "Tailwind CSS", "three.js": "Three.js", "react-native": "React Native",
          "hugging-face": "Hugging Face", "amazon-web-services": "AWS", "langchain": "LangChain", "twilio": "Twilio",
          "streamlit": "Streamlit", "numpy": "NumPy", "pandas": "pandas", "flutter": "Flutter", "dart": "Dart",
          "latex": "LaTeX", "docker": "Docker", "verilog": "Verilog", "cuda": "CUDA"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:60]


def _themes(e: dict) -> list[str]:
    m = re.match(r"Hackathon by .*?\.\s+(.*)$", e.get("description") or "", re.S)  # how Devpost listings are saved
    return [t.strip() for t in m.group(1).split(",") if t.strip()][:8] if m else []


def find(pid: int, key: str) -> dict | None:
    """The hackathon behind a package key, from the major list, the events, or (after the event is cleared) the
    package itself."""
    key = key if key.startswith("hack:") else f"hack:{key}"
    rest = key[5:]
    info = None
    if rest.startswith("ev-"):
        e = db.row("SELECT id, title, start_ts, end_ts, url, location, region, description FROM events WHERE id=?"
                   " AND kind='hackathon'", (rest[3:],))
        if e:
            info = {"name": e["title"], "site": e["url"] or "", "url": e["url"] or "", "city": e["location"] or "",
                    "region": e["region"] or "", "start_ts": e["start_ts"], "end_ts": e["end_ts"], "event_id": e["id"],
                    "themes": _themes(e), "devpost": "devpost.com" in (e["url"] or ""), "when": ""}
    else:
        h = next((h for h in events.hackathon_season(pid) if slug(h["name"]) == rest), None)
        if h:
            e = h.get("event") or {}
            desc = (db.row("SELECT description FROM events WHERE id=?", (e["id"],)) or {}) if e else {}
            info = {"name": h["name"], "site": h["site"], "url": e.get("url") or h["site"], "city": h["city"],
                    "region": h["region"], "start_ts": e.get("start_ts"), "end_ts": e.get("end_ts"),
                    "event_id": e.get("id"), "themes": _themes(desc), "when": h["when"],
                    "devpost": "devpost.com" in (e.get("url") or "")}
    saved = _saved(pid, "hackathon_app", key) or _saved(pid, "devpost", key)
    if not info and saved:
        info = saved.get("hackathon")
    return {**info, "key": key} if info else None


def _saved(pid: int, kind: str, key: str) -> dict | None:
    d = db.row("SELECT id, content FROM documents WHERE profile_id=? AND kind=? AND job_id=? ORDER BY updated_at DESC"
               " LIMIT 1", (pid, kind, key))
    return {**(db.loads(d["content"], {}) or {}), "doc_id": d["id"]} if d else None


def _save(pid: int, kind: str, key: str, title: str, content: dict, by: str) -> int:
    from . import routes_docs
    return routes_docs._save_doc(pid, kind, db.dumps(content), title, key, by=by)


def _resume(pid: int) -> tuple[dict, str]:
    from . import resume_studio as rs
    from . import routes_docs
    r = routes_docs.master(pid) or {}
    return (rs.normalize(r), rs.plain_text(rs.normalize(r))) if r else ({}, "")


# --- A. applying to attend ------------------------------------------------------------------------------------

def _questions(h: dict) -> list[tuple[str, str, str]]:
    """(id, label, kind): what almost every student hackathon asks."""
    n = h["name"]
    return [("why", f"Why do you want to attend {n}?", "textarea"),
            ("project", "Tell us about a project you're proud of.", "textarea"),
            ("build", f"What do you hope to build or learn at {n}?", "textarea"),
            ("count", "How many hackathons have you attended?", "input_text")]


def _best_project(r: dict) -> dict | None:
    """The resume project with the most to say (bullets, then a link)."""
    projects = [p for p in r.get("projects") or [] if p.get("name")]
    return max(projects, key=lambda p: (len(p.get("bullets") or []), bool(p.get("link")))) if projects else None


def _skills(r: dict, n: int = 4) -> list[str]:
    items = []
    for s in r.get("skills") or []:
        items += [x.strip() for x in re.split(r",|;|\|", s.get("items") or "") if x.strip()]
    return items[:n]


def _rule_drafts(h: dict, r: dict, text: str) -> dict[str, str]:
    """Starting points from the resume when no AI is connected: real project names, tools and bullets, and a
    [bracket] where only the student can say it."""
    p = _best_project(r)
    tech = (p or {}).get("tech") or ", ".join(_skills(r, 3))
    out = {}
    if p:
        bullets = " ".join(b.rstrip(".") + "." for b in (p.get("bullets") or [])[:2])
        out["project"] = (f"I built {p['name']}{f' ({tech})' if tech else ''}. {bullets} "
                          "[What you're proudest of about it, and why].").replace("  ", " ")
        out["why"] = (f"I want to attend {h['name']} to [what you want out of the weekend]. I've been building projects "
                      f"like {p['name']}{f' with {tech}' if tech else ''}, and a weekend of building with other students "
                      "is the fastest way I know to learn something new.")
    else:
        out["why"] = f"I want to attend {h['name']} to [what you want out of the weekend]."
    skills = ", ".join(_skills(r)) or "[the tools you know]"
    theme = f" for the {h['themes'][0]} track" if h.get("themes") else ""
    out["build"] = (f"I'd like to build [your idea]{theme}, using {skills}. I also want to learn [a tool or skill you "
                    "haven't used yet].")
    return out


def _ai_drafts(h: dict, text: str, labels: list[str]) -> dict[str, str]:
    if not ai.available() or not text:
        return {}
    schema = ai._obj({"answers": {"type": "array", "items": ai._obj({"label": ai._STR, "answer": ai._STR})}})
    system = ("You draft a student's answers to a hackathon application, in first person, plain and specific, about "
              "60-120 words each. " + ai._RESUME_RULES + " Use real projects, skills and experience from the resume. "
              "For what they want to build, connect their skills to the hackathon's themes if any are given. Never "
              "claim hackathons, awards, teammates or ideas the resume doesn't mention: leave a short [bracket] for "
              "anything only the student can say (a personal reason, an idea they have). Return one answer per "
              "question, label copied exactly.")
    info = f"Hackathon: {h['name']}" + (f" in {h['city']}" if h.get("city") else "") + (
        f"\nThemes / tracks: {', '.join(h['themes'])}" if h.get("themes") else "")
    content = f"{info}\n\nResume:\n{text[:3500]}\n\nQuestions:\n" + "\n".join(f"- {q}" for q in labels)
    try:
        out = ai._clean(ai._call_json(system, [{"type": "text", "text": content}], schema, effort="medium",
                                      max_tokens=3000))
    except Exception as e:
        log.warning("AI hackathon answers failed: %s", e)
        return {}
    have = set(re.findall(r"\d+(?:\.\d+)?", text + " " + info))
    return {a["label"]: a["answer"] for a in out.get("answers") or []
            if not set(re.findall(r"\d+(?:\.\d+)?", a["answer"])) - have}  # drop anything with made-up numbers


def _level(prof: dict) -> str:
    return LEVELS.get(prof.get("degree_level") or "Bachelor's", "") if prof.get("university") else ""


def app_kit(pid: int, key: str, redraft: bool = False) -> dict:
    """Everything a hackathon application asks for, ready to review: the student's edits kept, drafts where
    nothing was written yet."""
    h = find(pid, key)
    if not h:
        return {"error": "no such hackathon"}
    p = store.get_profile(pid)
    r, text = _resume(pid)
    if not r:
        return {"error": "add your resume first (Settings → Profile)"}
    basics = apply._basics(p, r)
    prof = p["profile"]  # with what they corrected in Settings → Profile
    links = [x for x in (r.get("header") or {}).get("links") or [] if x]
    devpost = next((x if x.startswith("http") else "https://" + x for x in links if "devpost.com" in x), "")
    year = prof.get("year_of_study") or ""
    grad = str(prof.get("grad_year") or (prof.get("grad_date") or "")[-4:] or "")
    mine = p["prefs"].get("hackathon") or {}
    saved = _saved(pid, "hackathon_app", h["key"]) or {}
    written = {} if redraft else (saved.get("answers") or {})  # what the student wrote or changed themselves
    cached = {} if redraft else (saved.get("drafts") or {})  # the AI's drafts from last time (not asked again)
    qs = _questions(h)
    asked = set() if redraft else set(saved.get("asked") or [])  # questions the AI already had a go at
    todo = [label for _, label, kind in qs if kind == "textarea" and not written.get(label) and label not in asked]
    drafts = _ai_drafts(h, text, todo) if todo else {}
    if todo and ai.available():
        asked |= set(todo)
    ai_drafts = {**cached, **drafts}
    rules = _rule_drafts(h, r, text)
    questions = []
    for label, val in [("First Name", basics["first_name"]), ("Last Name", basics["last_name"]),
                       ("Email", basics["email"]), ("Phone Number", basics["phone"]), ("School", basics["school"]),
                       ("Level of Study", _level(prof)), ("Major / Field of Study", basics["discipline"]),
                       ("Year of study", str(year) if year else ""), ("Graduation Year", grad),
                       ("GitHub", basics["github"]), ("LinkedIn", basics["linkedin"]), ("Devpost", devpost),
                       ("Personal Website", basics["website"])]:
        questions.append({"label": label, "kind": "input_text", "answer": val, "needs_you": not val, "required": False,
                          "name": "", "options": [], "note": "" if val else "Not on your resume or profile",
                          "group": "you"})
    questions.append({"label": "Resume", "kind": "input_file", "answer": "(attach your resume)", "needs_you": False,
                      "required": True, "name": "", "options": [], "note": "", "file": "resume", "group": "you"})
    for qid, label, kind in qs:
        ans = written.get(label) or ai_drafts.get(label) or rules.get(qid, "")
        note = ("Yours" if written.get(label) else "Drafted from your resume: make it yours" if ai_drafts.get(label)
                else "A starting point from your resume: fill the [brackets]" if ans else "")
        if qid == "count":
            seen = len(re.findall(r"hackathon|\bhacks?\b", text, re.I))
            ans = written.get(label, "")
            note = "" if ans else (f"Your resume mentions hackathons {seen} time(s): count the ones you went to" if seen
                                   else "0 is a fine answer: many hackathons welcome first-timers")
        questions.append({"label": label, "kind": kind, "answer": ans, "needs_you": True, "required": True, "name": "",
                          "options": [], "note": note, "group": "answers", "id": qid})
    for label, k, opts in [("T-shirt size", "tshirt", TSHIRTS), ("Dietary restrictions", "dietary", []),
                           ("Where will you be travelling from?", "travel_from", []),
                           ("Do you need travel reimbursement?", "reimbursement", ["Yes", "No"])]:
        val = mine.get(k) or ("" if k != "travel_from" else basics["location"])
        questions.append({"label": label, "kind": "select" if opts else "input_text", "answer": val,
                          "needs_you": not val, "required": False, "name": "", "options": opts, "note": "", "group": "event",
                          "pref": k})
    tpl = p["prefs"].get("resume_template", "jake")
    kit = {"job": {"id": h["key"], "company": h["name"], "title": "Hacker application", "url": h["url"] or h["site"],
                   "apply_url": h["url"] or h["site"], "ats": "other", "eligibility": None, "who_can_apply": []},
           "basics": basics, "questions": questions, "letter_id": None,
           "files": {"resume_pdf": f"/api/resume/export?profile_id={pid}&fmt=pdf&template={tpl}", "letter_pdf": ""},
           "hackathon": h, "drafted_by": "you" if all(written.get(label) for _, label, kind in qs if kind == "textarea")
           else "ai" if ai_drafts else "rules",
           "left_for_you": ["MLH Code of Conduct, privacy policy and terms: read and tick them yourself",
                            "Demographic questions (gender, race...): optional, always your choice",
                            "Age or date of birth, and an emergency contact, if asked",
                            "Check the application deadline on the hackathon's site"]}
    app = db.row("SELECT status FROM applications WHERE profile_id=? AND job_id=?", (pid, h["key"]))
    kit["status"] = (app or {}).get("status")
    if drafts or redraft or not saved or asked != set(saved.get("asked") or []):
        _save(pid, "hackathon_app", h["key"], f"{h['name']} application",
              {"answers": written, "drafts": ai_drafts, "asked": sorted(asked), "hackathon": _brief(h)},
              by=kit["drafted_by"])
    return kit


def _brief(h: dict) -> dict:
    return {k: h.get(k) for k in ("key", "name", "site", "url", "city", "region", "start_ts", "end_ts", "themes", "when",
                                  "devpost")}


def save_answers(pid: int, key: str, answers: dict, prefs: dict | None = None) -> dict:
    """The student's own words, kept; and their t-shirt / dietary / travel answers, remembered for next time."""
    h = find(pid, key)
    if not h:
        return {"error": "no such hackathon"}
    saved = _saved(pid, "hackathon_app", h["key"]) or {}
    r, text = _resume(pid)
    rules = _rule_drafts(h, r, text) if r else {}
    suggested = {label: (saved.get("drafts") or {}).get(label) or rules.get(qid, "") for qid, label, _ in _questions(h)}
    # only what they actually wrote or changed counts as theirs: an untouched draft stays a draft
    keep = {k: str(v)[:4000] for k, v in (answers or {}).items()
            if k in suggested and str(v).strip() and str(v).strip() != (suggested[k] or "").strip()}
    _save(pid, "hackathon_app", h["key"], f"{h['name']} application",
          {**{k: v for k, v in saved.items() if k != "doc_id"}, "answers": keep, "hackathon": _brief(h)}, "you")
    if prefs:  # only this one key: the rest of the stored preferences stay exactly as they are
        raw = db.loads((db.row("SELECT prefs_json FROM profiles WHERE id=?", (pid,)) or {}).get("prefs_json"), {}) or {}
        raw["hackathon"] = {**(raw.get("hackathon") or {}), **{
            k: str(v)[:120] for k, v in prefs.items() if k in ("tshirt", "dietary", "travel_from", "reimbursement")}}
        with db.tx() as c:
            c.execute("UPDATE profiles SET prefs_json=? WHERE id=?", (db.dumps(raw), pid))
    return {"ok": True}


def track(pid: int, key: str, status: str = "applied") -> dict:
    """Put it on the tracker (Applied, then Accepted or Not this time) like any application."""
    h = find(pid, key)
    if not h:
        return {"error": "no such hackathon"}
    if status and status not in store.APP_STATUSES:
        return {"error": f"status must be one of {store.APP_STATUSES}"}
    before = (db.row("SELECT status FROM applications WHERE profile_id=? AND job_id=?", (pid, h["key"])) or {}).get("status")
    with db.tx() as c:
        if not status:
            c.execute("DELETE FROM applications WHERE profile_id=? AND job_id=?", (pid, h["key"]))
        else:
            c.execute("INSERT INTO applications (profile_id, job_id, status, company, title, url, source, applied_at,"
                      " updated_at) VALUES (?,?,?,?,?,?,'hackathon',?,?) ON CONFLICT(profile_id, job_id) DO UPDATE SET"
                      " status=excluded.status, updated_at=excluded.updated_at,"
                      " applied_at=COALESCE(applications.applied_at, excluded.applied_at)",
                      (pid, h["key"], status, h["name"], "Hacker application", h["url"] or h["site"] or None,
                       db.now() if status != "saved" else None, db.now()))
    store.refresh_scores()
    return {"ok": True, "hackathon": h["name"], "status": status or "removed",
            "_undo": {"tool": "track_hackathon", "args": {"hackathon": h["key"], "status": before or ""}}}


def answers_markdown(kit: dict) -> str:
    h = kit["hackathon"]
    lines = [f"# {h['name']}: hacker application", "", f"Apply: {kit['job']['apply_url'] or 'see the site'}", ""]
    for group, title in (("you", "About you"), ("answers", "Short answers"), ("event", "For the event")):
        lines += [f"## {title}", ""]
        for q in kit["questions"]:
            if q.get("group") != group or q.get("kind") == "input_file":
                continue
            words = len(q["answer"].split())
            if group == "answers":
                lines += [f"### {q['label']}", "", q["answer"] or "_(to write)_", "", f"_{words} words_", ""]
            else:
                lines.append(f"- **{q['label']}:** {q['answer'] or '_(yours to fill)_'}")
        lines.append("")
    lines += ["## Left for you", "", *[f"- [ ] {x}" for x in kit["left_for_you"]],
              "- [ ] Fill every [bracket] in the short answers", "- [ ] Submit, then mark it Applied in Co-op Coach"]
    return "\n".join(lines)


# --- B. the project, afterwards ---------------------------------------------------------------------------------

def _first_sentences(text: str, n: int = 2, limit: int = 600) -> str:
    text = re.sub(r"`{3}.*?`{3}", "", text or "", flags=re.S)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(("|", "```", "---"))]
    flat = re.sub(r"\s+", " ", " ".join(lines))
    flat = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", flat)  # markdown links -> their text
    parts = re.split(r"(?<=[.!?])\s+", flat)
    return " ".join(parts[:n])[:limit].strip()


def _bullets(text: str, n: int = 4) -> list[str]:
    out = [re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", m.strip()) for m in re.findall(r"^\s*[-*+]\s+(.+)$", text or "", re.M)]
    return [b.rstrip(".") for b in out if 8 <= len(b) <= 200][:n]


def _readme_part(sections: dict[str, str], keys: tuple[str, ...]) -> str:
    for k in keys:
        for heading, body in sections.items():
            if heading and (heading == k or heading.startswith(k)) and body.strip():
                return body.strip()
    return ""


def _rule_sections(project: str, rp: dict, notes: dict, hack: dict | None) -> dict[str, str]:
    """From the README and the team's own lines only; a [bracket] for anything neither says."""
    sec = github.readme_sections(rp.get("readme") or "")
    intro = _first_sentences(sec.get("", ""), 3)
    tags = [PRETTY.get(t, t) for t in rp.get("built_with") or []]
    out = {}
    what = _readme_part(sec, README_KEYS["what"])
    feats = _bullets(what) or _bullets(sec.get("", ""))
    out["what"] = (notes.get("built") or rp.get("description") or _first_sentences(what) or intro
                   or f"[What {project} does, in a sentence or two]").strip()
    if feats:
        out["what"] += "\n\n" + "\n".join(f"- {b}" for b in feats)
    how = _readme_part(sec, README_KEYS["how"])
    out["how"] = ((f"We built {project} with {', '.join(tags[:8])}." if tags else f"[What you built {project} with]")
                  + (f" {_first_sentences(how, 3)}" if how and not _bullets(how) else "")
                  + ("\n\n" + "\n".join(f"- {b}" for b in _bullets(how)) if _bullets(how) else ""))
    for k, prompt in (("inspiration", "[What got your team started on this: a problem you saw, or someone you wanted to help]"),
                      ("challenges", "[A problem you hit during the hackathon and how you got past it]"),
                      ("proud", "[What you're proudest of: something that works, something that was hard]"),
                      ("learned", "[Something new the team learned: a tool, an API, a skill]"),
                      ("next", f"[Where you'd take {project} next]")):
        mine = {"challenges": notes.get("challenge"), "learned": notes.get("learned")}.get(k)
        part = _readme_part(sec, README_KEYS[k])
        text = mine or (("\n".join(f"- {b}" for b in _bullets(part))) if _bullets(part) else _first_sentences(part, 3))
        out[k] = text.strip() or prompt
    if notes.get("prize"):
        out["proud"] = f"We won {notes['prize']}. " + (out["proud"] if not out["proud"].startswith("[") else "")
    pitch = rp.get("description") or _first_sentences(notes.get("built") or intro, 1)
    out["tagline"] = (pitch or f"[{project} in one line]")[:200]
    return out


def _ai_sections(project: str, rp: dict, notes: dict, hack: dict | None) -> tuple[dict[str, str], str]:
    """The same sections, written by the connected AI from the same facts. A section that brings in a number none of
    the facts have is replaced by the rules version."""
    if not ai.available():
        return {}, "rules"
    keys = ["tagline"] + [k for k, _ in SECTIONS]
    schema = ai._obj({k: ai._STR for k in keys})
    facts = (f"Project: {project}\nRepo: {rp['url']}\nDescription: {rp.get('description') or ''}\n"
             f"Built with (from its languages and dependency files): {', '.join(rp.get('built_with') or [])}\n"
             + (f"Hackathon: {hack['name']}" + (f" (themes: {', '.join(hack['themes'])})" if hack.get("themes") else "")
                + "\n" if hack else "")
             + "".join(f"Team's note, {k}: {v}\n" for k, v in notes.items() if v)
             + f"\nREADME:\n{(rp.get('readme') or '')[:7000]}")
    system = ("You write a hackathon team's Devpost submission: an elevator pitch (tagline, at most 200 characters) and "
              "the sections Inspiration, What it does, How we built it, Challenges we ran into, Accomplishments that "
              "we're proud of, What we learned, What's next. First person plural, plain and specific, 40-120 words "
              "each; short markdown lists are fine. Hard rules: use ONLY the facts below (the README, the repo's "
              "dependencies and the team's notes). Never invent features, users, numbers, results, prizes, judges or "
              "partners. Where the facts don't say something only the team knows (what inspired it, a challenge, what "
              "they learned), write one short [bracket] prompt for them instead of guessing.")
    try:
        out = ai._clean(ai._call_json(system, [{"type": "text", "text": facts}], schema, effort="medium",
                                      max_tokens=4000))
    except Exception as e:
        log.warning("AI Devpost write-up failed: %s", e)
        return {}, "rules"
    have = set(re.findall(r"\d+(?:\.\d+)?", facts))
    keep = {k: (out.get(k) or "").strip() for k in keys
            if (out.get(k) or "").strip() and not set(re.findall(r"\d+(?:\.\d+)?", out.get(k) or "")) - have}
    if keep.get("tagline"):
        keep["tagline"] = keep["tagline"][:200]
    return keep, ai.provider() or "ai"


def video_script(project: str, s: dict, tags: list[str]) -> str:
    """Two minutes, the way judges like it: the problem, the product working, how it's built, what's next."""
    tagline = (s.get("tagline") or "").strip().rstrip(".")
    feats = _bullets(s.get("what", ""), 3) or [x for x in re.split(r"(?<=[.!?])\s+", s.get("what", ""))
                                               if x and x.strip().rstrip(".") != tagline and not x.startswith("[")][:3]
    hook = _first_sentences(s.get("inspiration", ""), 1)
    lines = [f"{project}: two-minute demo", "",
             f"0:00-0:15  The problem. {hook if hook and not hook.startswith('[') else '[One line on the problem, ideally a real moment]'}",
             f"0:15-0:30  {project} in one line: {s.get('tagline', '')}",
             "0:30-1:20  Show it working (screen recording, not slides):"]
    lines += [f"           - {f.strip().rstrip('.')}" for f in feats] or ["           - [the main thing it does]"]
    lines += [f"1:20-1:40  How it's built: {', '.join(tags[:6]) or '[your stack]'}"
              + (" (one sentence on the hardest part)" if tags else ""),
              f"1:40-2:00  What's next for {project}, and thank the team", "",
              "Tips: keep it under 2 minutes, record at 1080p with the sound on, open with the product, "
              "upload it unlisted to YouTube and paste the link into Devpost."]
    return "\n".join(lines)


def markdown(project: str, s: dict) -> str:
    """Devpost's "About the project" field (markdown, its own headings)."""
    return "\n\n".join(f"## {title.format(project=project)}\n{s.get(k, '').strip()}" for k, title in SECTIONS)


def _month(ts: int | None) -> str:
    return time.strftime("%b %Y", time.localtime(ts or time.time()))


def resume_entry(project: str, rp: dict, s: dict, notes: dict, hack: dict | None, devpost_url: str = "") -> dict:
    """The project as a resume entry: what it is and what the student did, from the same facts, no new numbers."""
    tags = [PRETTY.get(t, t) for t in rp.get("built_with") or []][:5]
    what = _first_sentences(re.sub(r"^\s*[-*+].*$", "", s.get("what", ""), flags=re.M), 1, 180).rstrip(".")
    at = f" at {hack['name']}" if hack else ""
    bullets = []
    if what and not what.startswith("["):
        first = what.split(" ", 1)[0]  # "Recipes from..." reads on as "recipes from..."; "LaTeX", "PantryPal" stay
        bullets.append(f"Built {project}{at}: " + (what[0].lower() + what[1:] if first[1:].islower() else what))
    else:
        bullets.append(f"Built {project}{at} [: what it does]")
    if notes.get("mine"):
        bullets.append(notes["mine"].strip().rstrip(".")[:220])
    elif tags:
        bullets.append(f"[Your part], using {', '.join(tags[:3])}")
    if notes.get("prize"):
        bullets.append(f"Won {notes['prize'].strip().rstrip('.')}")
    return {"name": project, "tech": ", ".join(tags), "dates": _month((hack or {}).get("end_ts")), "link": rp["url"],
            "demo": devpost_url or rp.get("homepage") or "", "bullets": [b[0].upper() + b[1:] for b in bullets if b]}


def submission(pid: int, key: str, repo_url: str, project: str = "", built: str = "", challenge: str = "",
               learned: str = "", mine: str = "", prize: str = "", video: str = "", devpost_url: str = "") -> dict:
    """The Devpost submission for a hackathon project, written from its public GitHub repo and the team's own
    lines, and saved (open it again to keep editing)."""
    hack = find(pid, key) if key else None
    try:
        rp = github.repo(repo_url)
    except (github.NotARepo, LookupError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"couldn't read that repo: {str(e)[:160]}"}
    project = (project or "").strip() or re.sub(r"[-_]+", " ", rp["name"]).strip().title()
    notes = {k: (v or "").strip()[:1500] for k, v in (("built", built), ("challenge", challenge), ("learned", learned),
                                                       ("mine", mine), ("prize", prize)) if (v or "").strip()}
    rules = _rule_sections(project, rp, notes, hack)
    drafted, by = _ai_sections(project, rp, notes, hack)
    s = {**rules, **drafted}
    tags = rp.get("built_with") or []
    links = [x for x in (rp["url"], rp.get("homepage")) if x]
    out = {"project": project, "hackathon": _brief(hack) if hack else None, "repo": rp["url"], "sections": s,
           "tagline": s.get("tagline", ""), "built_with": tags, "links": links, "video": video.strip(),
           "devpost_url": devpost_url.strip(), "notes": notes, "by": by if drafted else "rules",
           "video_script": video_script(project, s, [PRETTY.get(t, t) for t in tags]),
           "resume_entry": resume_entry(project, rp, s, notes, hack, devpost_url.strip()),
           "readme_found": bool(rp.get("readme")), "languages": rp.get("languages") or []}
    out["markdown"] = markdown(project, s)
    out["brackets"] = len(re.findall(r"\[[^\]]{3,}\]", out["markdown"] + out["tagline"]))
    doc_key = (hack or {}).get("key") or f"devpost:{rp['owner']}/{rp['name']}".lower()
    out["doc_id"] = _save(pid, "devpost", doc_key, f"{project} · Devpost", out, by=out["by"])
    return out


def save_submission(pid: int, doc_id: int, sections: dict, tagline: str | None = None, project: str | None = None) -> dict:
    d = db.row("SELECT job_id, content FROM documents WHERE id=? AND profile_id=? AND kind='devpost'", (doc_id, pid))
    if not d:
        return {"error": "that write-up is gone"}
    out = db.loads(d["content"], {}) or {}
    out["sections"] = {**out.get("sections", {}), **{k: str(v)[:6000] for k, v in (sections or {}).items()
                                                     if k in dict(SECTIONS)}}
    if tagline is not None:
        out["tagline"] = out["sections"]["tagline"] = str(tagline)[:200]
    if project:
        out["project"] = str(project)[:80]
    out["markdown"] = markdown(out["project"], out["sections"])
    out["brackets"] = len(re.findall(r"\[[^\]]{3,}\]", out["markdown"] + out.get("tagline", "")))
    _save(pid, "devpost", d["job_id"], f"{out['project']} · Devpost", out, by="you")
    return {"ok": True, "brackets": out["brackets"]}


def add_to_resume(pid: int, doc_id: int, entry: dict | None = None) -> dict:
    """Put the project on the master resume (first in Projects; one with the same name is replaced). The resume
    before is kept, so it can be undone."""
    from . import resume_studio as rs
    from . import routes_docs
    from .tools.common import _new_doc
    d = db.row("SELECT content FROM documents WHERE id=? AND profile_id=? AND kind='devpost'", (doc_id, pid))
    if not d:
        return {"error": "that write-up is gone"}
    r = routes_docs.master(pid)
    if not r:
        return {"error": "add your resume first (Settings → Profile)"}
    sub = db.loads(d["content"], {}) or {}
    e = {**sub.get("resume_entry", {}), **{k: v for k, v in (entry or {}).items()
                                            if k in ("name", "tech", "dates", "link", "demo", "bullets")}}
    e["bullets"] = [str(b)[:300] for b in e.get("bullets") or [] if str(b).strip()][:4]
    if not e.get("name"):
        return {"error": "the project needs a name"}
    backup = _new_doc(pid, "resume_backup", db.dumps(r), f"Resume before adding {e['name']}", None, "you")
    new = rs.normalize(r)
    new["projects"] = [e] + [p for p in new["projects"] if (p.get("name") or "").lower() != e["name"].lower()]
    routes_docs._save_doc(pid, "resume_master", db.dumps(rs.normalize(new)), "Master resume", by="you")
    return {"ok": True, "project": e["name"], "backup_id": backup,
            "_undo": {"tool": "restore_resume", "args": {"backup_id": backup}}}


def devpost_answers(sub: dict) -> list[dict]:
    """What Devpost's submission pages ask, under the labels they use, for the form filler."""
    out = [("Project name", sub["project"]), ("Elevator pitch", sub.get("tagline", "")),
           ("About the project", sub.get("markdown", "")), ("Project story", sub.get("markdown", "")),
           ("Video demo link", sub.get("video", "")), ("Try it out", sub.get("repo", "")),
           ("Try it out links", sub.get("repo", ""))]
    return [{"label": label, "answer": val} for label, val in out if val]
