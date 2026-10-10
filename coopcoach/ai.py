"""Optional AI: deep resume parsing/assessment, per-job fit scoring, letters, rewrites, tailoring and chat.

Three providers:
  * Your Claude Code (the `claude` command, signed in): runs on your Claude plan, no key. Picked in the app.
  * Claude (ANTHROPIC_API_KEY): best quality, paid.
  * Groq (GROQ_API_KEY): free tier running openai/gpt-oss-120b. Contact details are redacted before sending.
`ai.provider` in config.yaml picks one; "auto" uses Claude when it's configured, otherwise Groq. Choosing
"My Claude Code" in the app overrides that, and Groq stays the fallback if Claude Code is unavailable.

Everything here is best-effort. If nothing is configured or a call fails, callers fall back to the rule-based
parser in resume.py and the heuristic matcher in matching.py.
"""

import base64
import json
import logging
import os
import re
from pathlib import Path

import anthropic

from . import claude_code, codex_cli, config, db, groq_client
from .textutil import CATEGORIES

log = logging.getLogger(__name__)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_client: anthropic.Anthropic | None = None


def _claude_configured() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


def engine_choice() -> str:
    """What the person picked in the app: 'claude_code' or 'auto' (config.yaml decides)."""
    try:
        return db.meta_get("ai_engine", "auto") or "auto"
    except Exception:
        return "auto"


def _fallback() -> str | None:
    want = str(config.get("ai.provider", "auto") or "auto").lower()
    if want in ("claude", "anthropic"):
        return "claude" if _claude_configured() else None
    if want == "groq":
        return "groq" if groq_client.configured() else None
    if want in ("none", "off", "rules"):
        return None
    if _claude_configured():
        return "claude"
    return "groq" if groq_client.configured() else None


def provider() -> str | None:
    """'claude_code', 'codex', 'claude', 'groq' or None."""
    choice = engine_choice()
    if choice == "claude_code" and claude_code.usable():
        return "claude_code"
    if choice == "codex" and codex_cli.usable():
        return "codex"
    return _fallback()


def available() -> bool:
    p = provider()
    return p is not None and not (p == "groq" and groq_client.paused())


def label() -> str:
    p = provider()
    if p == "claude_code":
        return "your Claude Code"
    if p == "codex":
        return "your ChatGPT"
    if p == "claude":
        return "Claude"
    if p == "groq":
        return "Groq (free)" + (" · paused, daily limit hit" if groq_client.paused() else "")
    return ""


def _small() -> bool:
    """Groq's free tier allows ~8k tokens/minute, so requests there are kept compact."""
    return provider() == "groq"


def _model() -> str:
    return config.get("ai.model", "claude-opus-5-5")


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=3, timeout=300)
    return _client


def _as_text(content: list) -> str:
    return "\n\n".join(b["text"] for b in content if b.get("type") == "text")


def _call_json(system: str, content: list, schema: dict, effort: str, max_tokens: int = 16000,
               fast: bool = False) -> dict:
    """`fast` marks bulk work (match scoring, inbox sorting) that a quicker model handles fine."""
    if provider() in ("claude_code", "codex"):
        try:
            mod = claude_code if provider() == "claude_code" else codex_cli
            return mod.call_json(system, _as_text(content), schema, effort, fast=fast)
        except Exception as e:  # signed out, usage limit, timeout: the free fallback keeps things working
            if _fallback() is None or (_fallback() == "groq" and groq_client.paused()):
                raise
            log.warning("your own AI failed (%s); using %s for this one", e, _fallback())
            if _fallback() == "groq":
                return groq_client.call_json(system, _as_text(content), schema, effort, max_tokens=min(max_tokens, 4500))
    if provider() == "groq":
        return groq_client.call_json(system, _as_text(content), schema, effort, max_tokens=min(max_tokens, 4500))
    resp = _get_client().beta.messages.create(
        model=_model(),
        max_tokens=max_tokens,
        betas=[_FALLBACK_BETA],
        fallbacks="default",  # if a safety classifier declines, Anthropic re-runs on its recommended model
        system=system,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined the request ({getattr(resp.stop_details, 'category', None)})")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Claude response was cut off (max_tokens)")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)


def _clean(v):
    """Models like non-breaking hyphens/spaces ("co‑op", "May 2026"); keep plain ones so fonts and ATS read them."""
    if isinstance(v, str):
        return v.replace("\u2011", "-").replace("\u202f", " ").replace("\u00a0", " ")
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}
_OUTLOOK = {"type": "string", "enum": ["Reach", "Target", "Likely"]}

RESUME_SCHEMA = _obj({
    "profile": _obj({
        "name": _STR,
        "email": _STR,
        "links": _STRS,
        "university": _STR,
        "program": _STR,
        "degree_level": {"type": "string", "enum": ["Bachelor's", "Master's", "PhD", "Diploma", "Other"]},
        "year_of_study": {"type": "integer", "description": "1-5; 0 if unknown"},
        "grad_year": {"type": "integer", "description": "expected graduation year; 0 if unknown"},
        "gpa": {"type": "string", "description": "as written, e.g. '3.8/4.0' or '87%'; empty if absent"},
        "gpa_normalized": {"type": "number", "description": "converted to a 4.0 scale; -1 if absent"},
        "coop_terms_completed": {"type": "integer"},
        "experience_months": {"type": "integer"},
        "experiences": {"type": "array", "items": _obj({
            "title": _STR, "company": _STR, "dates": _STR, "is_internship": {"type": "boolean"},
            "highlights": _STRS,
        })},
        "projects": {"type": "array", "items": _obj({"name": _STR, "tech": _STRS, "summary": _STR})},
        "skills": _STRS,
        "target_categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}},
        "summary": {"type": "string", "description": "one sentence, third person"},
    }),
    "assessment": _obj({
        "headline": _STR,
        "level": {"type": "string", "enum": ["Beginner", "Developing", "Competitive", "Strong"]},
        "strengths": _STRS,
        "gaps": _STRS,
        "resume_tips": {"type": "array", "items": {"type": "string"},
                        "description": "specific edits; quote the line being improved where possible"},
        "tier_outlook": _obj({"yc": _OUTLOOK, "startup": _OUTLOOK, "bigtech": _OUTLOOK, "quant": _OUTLOOK,
                              "finance": _OUTLOOK}),
        "suggested_roles": _STRS,
        "next_steps": _STRS,
    }),
})

RESUME_SYSTEM = f"""You are an experienced university co-op/internship advisor (think Waterloo, UofT, UBC co-op
offices) who also screens resumes for tech, quant, bank and startup internship programs.

Read the student's resume and return:
1. profile: faithful structured data extracted from the resume. Do not invent anything; use empty strings,
   0 or -1 for missing fields. `skills` should use common canonical names (Python, C++, React, PyTorch, AWS,
   SQL, Figma...). `target_categories` picks the 1-3 role categories from {CATEGORIES} this resume is most
   competitive for. `coop_terms_completed` counts internships/co-op work terms only.
2. assessment: an honest, specific evaluation for the current recruiting season.
   - strengths/gaps: 2-5 items each, concrete and tied to the resume content.
   - resume_tips: 2-5 specific edits that matter most (quote the line, show the improved version). Use ONLY facts
     already in the resume: never invent numbers, metrics, users, percentages or achievements, not even as examples.
     Don't push numbers into every bullet; a specific technical bullet is strong without one.
   - tier_outlook: how competitive they are right now for YC startups, other startups, big tech,
     quant/trading firms, and banks/large corporates (Reach / Target / Likely).
   - next_steps: 3-5 actions for the next month (applications, projects, prep, networking/coffee chats).
Be direct and encouraging; no generic filler."""


def parse_resume(filename: str, data: bytes, text: str, hidden_links: str = "") -> tuple[dict, dict]:
    content: list = []
    if provider() == "claude" and filename.lower().endswith(".pdf") and len(data) < 25_000_000:
        content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                       "data": base64.standard_b64encode(data).decode()}})
        content.append({"type": "text", "text": "Here is my resume (PDF above)."})
    else:
        content.append({"type": "text", "text": f"Here is my resume:\n\n<resume>\n{text}\n</resume>"})
    if hidden_links:  # kinds only; the addresses themselves are merged in locally after parsing
        content.append({"type": "text", "text": f"The file also has clickable links whose addresses aren't in the "
                        f"text: {hidden_links}. Treat those as present; don't suggest adding them."})
    out = _clean(_call_json(RESUME_SYSTEM, content, RESUME_SCHEMA, effort="medium"))
    profile, assessment = out["profile"], out["assessment"]
    # a tip that brings in numbers the resume doesn't have is made up: drop it
    have = set(re.findall(r"\d+(?:\.\d+)?", text))
    assessment["resume_tips"] = [t for t in assessment.get("resume_tips") or []
                                 if not set(re.findall(r"\d+(?:\.\d+)?", t)) - have]
    if profile.get("gpa_normalized", -1) < 0:
        profile["gpa_normalized"] = None
    return profile, assessment


# --- job scoring --------------------------------------------------------------------

SCORE_SCHEMA = _obj({
    "results": {"type": "array", "items": _obj({
        "job_id": _STR,
        "fit": {"type": "integer", "description": "0-100 likelihood this student gets an interview and wants it"},
        "why": {"type": "string", "description": "one short sentence"},
        "tip": {"type": "string", "description": "one short, specific tip for standing out on this application"},
    })},
})

SCORE_SYSTEM = """You match a student to internship/co-op postings. For each posting, estimate fit 0-100:
how likely this student is to land an interview AND how well it fits their goals. Weigh: role/skill alignment,
seniority/year requirements (e.g. PhD-only, 'final year' or US-citizenship requirements are hard blockers
for students who don't meet them), location/term preferences, and company competitiveness relative to the
student's experience. International students routinely intern at US companies, especially startups: F-1 students
in the US on CPT/OPT (no employer sponsorship needed), Canadian co-op students (e.g. Waterloo) on J-1/TN or remotely.
So a US location is NOT a blocker unless the posting requires US citizenship/clearance; an explicit "no visa
sponsorship" lowers odds for J-1 students but rarely matters for CPT/OPT. Each posting's `eligibility` lists who it
is open to (read from the whole posting and its application form): if it says applicants must be based somewhere the
student isn't (e.g. "US based applicants only" for a student based in Canada) or verdict is "no", fit is under 30.
A stated graduation window is NOT a blocker (dates shift; mention it in the tip at most). 85+ = apply today,
65-84 = good fit, 40-64 = stretch or partial fit, <40 = poor fit.
Return one result per posting, using the exact job_id given. Keep `why` and `tip` under 25 words each."""


def score_jobs(profile: dict, prefs: dict, jobs: list[dict]) -> list[dict]:
    student = {
        "summary": profile.get("summary"),
        "university": profile.get("university"),
        "program": profile.get("program"),
        "degree_level": profile.get("degree_level"),
        "year_of_study": profile.get("year_of_study"),
        "gpa": profile.get("gpa"),
        "coop_terms_completed": profile.get("coop_terms_completed"),
        "experiences": [f"{e.get('title')} @ {e.get('company')}" for e in profile.get("experiences", [])][:6],
        "projects": [f"{p.get('name')}: {', '.join(p.get('tech', [])[:5])}" for p in profile.get("projects", [])][:6],
        "skills": profile.get("skills", [])[:30],
        "target_categories": prefs.get("categories") or profile.get("target_categories"),
        "preferred_locations": prefs.get("locations"),
        "preferred_terms": prefs.get("terms"),
        "aiming_for": prefs.get("aim", "balanced"),
        "work_authorization": prefs.get("citizenship", ""),
        "based_in": {"Canadian": "Canada", "Intl-CA": "Canada", "F1": "United States", "US": "United States"}.get(
            prefs.get("citizenship") or "Canadian", "outside Canada and the US"),
        "co_op_student": bool(profile.get("coop_program")),
        "open_to_us_roles": bool(prefs.get("us_open", True)),
    }
    postings = [{
        "job_id": j["id"],
        "company": j["company"],
        "title": j["title"],
        "locations": j.get("locations", [])[:4],
        "terms": j.get("terms", []),
        "company_tier": j.get("tier"),
        # who it's open to is usually at the END of a posting, so send those lines rather than just the intro
        "eligibility": {"verdict": j.get("eligibility", "ok"),
                        "requirements": [r["text"] for r in j.get("requirements") or []][:5]},
        "description_excerpt": (j.get("description") or "")[:400 if _small() else 900],
    } for j in jobs]
    content = [
        {"type": "text", "text": "<student>\n" + json.dumps(student, ensure_ascii=False) + "\n</student>",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "<postings>\n" + json.dumps(postings, ensure_ascii=False) + "\n</postings>"},
    ]
    out = _call_json(SCORE_SYSTEM, content, SCORE_SCHEMA, effort="low", fast=True)
    valid = {j["id"] for j in jobs}
    return [r for r in out.get("results", []) if r.get("job_id") in valid]


# --- recruiting email classification (opt-in: email.use_ai in config.yaml) -----------

EMAIL_SCHEMA = _obj({
    "results": {"type": "array", "items": _obj({
        "email_id": _STR,
        "job_related": {"type": "boolean", "description": "true only if about one of the student's own job/internship applications"},
        "status": {"type": "string", "enum": ["applied", "oa", "interviewing", "offer", "rejected", "none"]},
        "company": {"type": "string", "description": "the hiring company (not the ATS vendor); empty if unknown"},
        "role": {"type": "string", "description": "job title if stated; empty otherwise"},
    })},
})

EMAIL_SYSTEM = """You sort a student's inbox for internship/co-op recruiting updates. For each email decide whether it is
about one of THEIR OWN applications (job alerts, newsletters, marketing, school admin and bank/credit 'applications'
are not). If it is, classify: applied (confirmation/received), oa (online assessment / coding challenge / HireVue
invitation), interviewing (interview invite, scheduling, next round), offer, rejected. Use 'none' when job_related is
false. Name the hiring company, never Greenhouse/Workday/Lever/LinkedIn. Use the exact email_id given."""


def classify_emails(messages: list[dict]) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    step = 6 if _small() else 20
    for i in range(0, len(messages), step):
        batch = messages[i:i + step]
        items = [{"email_id": str(n), "from": f"{m['from_name']} <{m['from_addr']}>", "subject": m["subject"],
                  "body": m["body"][:700 if _small() else 1500]} for n, m in enumerate(batch)]
        res = _call_json(EMAIL_SYSTEM, [{"type": "text", "text": json.dumps(items, ensure_ascii=False)}],
                         EMAIL_SCHEMA, effort="low", fast=True)
        for r in res.get("results", []):
            try:
                m = batch[int(r["email_id"])]
            except (ValueError, IndexError):
                continue
            ok = r.get("job_related") and r.get("status") in ("applied", "oa", "interviewing", "offer", "rejected")
            out[m["message_id"]] = {"status": r["status"], "company": r.get("company", ""),
                                    "role": r.get("role", "")} if ok else None
    return out


# --- Resume Studio + cover letters ---------------------------------------------------------

_NO_FABRICATION = """Hard rules: use ONLY facts present in the student's resume and the posting. Never invent employers,
projects, technologies, metrics, dates or results. If a number would help but isn't given, write a bracketed
placeholder such as [N users] or [X%] for the student to fill in. Never claim experience with a technology the
resume doesn't mention."""

# Resume edits: numbers are welcome when they're real, never required. A crisp technical bullet with no metric is
# better than one padded with "[X%]".
_RESUME_RULES = """Hard rules: use ONLY facts in the student's resume (and the posting, if given). Never invent employers,
projects, technologies, metrics, dates or results, and never add a number that isn't already in the resume, not even
as a [placeholder]. Not every bullet needs a number: specific, technical, clearly-scoped bullets are strong on their
own. If a real number would clearly help, say so in a note or question instead of writing one in."""

BULLETS_SCHEMA = _obj({"results": {"type": "array", "items": _obj({
    "index": {"type": "integer"},
    "options": {"type": "array", "items": {"type": "string"}, "description": "2 rewrites, best first"},
    "note": {"type": "string", "description": "what changed, under 15 words"},
})}})


def improve_bullets(bullets: list[str], context: str = "", job_title: str = "") -> list[dict]:
    system = ("You are a technical recruiter who has reviewed thousands of internship/co-op resumes (Waterloo, UofT, "
              "UBC). Rewrite each bullet so it leads with a strong verb and says what was built, how (tech), and "
              "the outcome when the resume states one. 1-2 lines (under ~190 characters), past tense for past "
              "roles, no first person, no filler. Keep what already works. " + _RESUME_RULES)
    items = [{"index": i, "bullet": b} for i, b in enumerate(bullets)]
    prompt = (f"Role context: {context or 'n/a'}\nTarget job: {job_title or 'general'}\n\n"
              f"Bullets:\n{json.dumps(items, ensure_ascii=False)}")
    out = _call_json(system, [{"type": "text", "text": prompt}], BULLETS_SCHEMA, effort="medium")
    return out.get("results", [])


_RES_ITEM = {"type": "array", "items": {"type": "string"}}
RESUME_JSON_SCHEMA = _obj({
    "resume": _obj({
        "header": _obj({"name": _STR, "email": _STR, "phone": _STR, "location": _STR, "links": _STRS}),
        "education": {"type": "array", "items": _obj({"school": _STR, "degree": _STR, "location": _STR, "dates": _STR,
                                                      "details": _RES_ITEM})},
        "experience": {"type": "array", "items": _obj({"title": _STR, "company": _STR, "location": _STR, "dates": _STR,
                                                       "bullets": _RES_ITEM})},
        "projects": {"type": "array", "items": _obj({"name": _STR, "tech": _STR, "dates": _STR, "link": _STR,
                                                     "bullets": _RES_ITEM})},
        "skills": {"type": "array", "items": _obj({"label": _STR, "items": _STR})},
        "extras": {"type": "array", "items": _obj({"heading": _STR, "items": _RES_ITEM})},
        "section_order": {"type": "array", "items": {"type": "string", "enum": ["education", "experience", "projects",
                                                                              "skills", "extras"]}},
    }),
    "changes": _STRS,
    "missing_keywords": {"type": "array", "items": {"type": "string"},
                         "description": "important posting keywords the resume doesn't show"},
})


REVIEW_SCHEMA = _obj({
    "verdict": {"type": "string", "enum": ["Ready to send", "Almost there", "Needs work"]},
    "first_impression": {"type": "string", "description": "what a recruiter takes away in a 10-second skim, 2 sentences"},
    "strengths": {"type": "array", "items": {"type": "string"}, "description": "2-4 things to keep as they are"},
    "changes": {"type": "array", "items": _obj({
        "title": {"type": "string", "description": "the change in under 10 words"},
        "why": {"type": "string", "description": "why it matters for getting interviews, one sentence"},
        "path": {"type": "string", "description": "the [path] of the line to change, copied exactly, or '' for advice"},
        "before": {"type": "string", "description": "that line's current text, copied exactly"},
        "after": {"type": "string", "description": "the full replacement text for that line"},
    }), "description": "3-5 changes that matter most, most important first"},
    "questions": {"type": "array", "items": {"type": "string"},
                  "description": "facts only the student knows that would make a line stronger"},
})

REVIEW_SYSTEM = """You are a senior tech recruiter and resume coach for co-op and internship students (Waterloo, UofT,
UBC level). Think about the WHOLE resume the way a recruiter does: what story it tells in a 10-second skim, whether
the strongest work is visible first, whether each line earns its space, and how it reads for the student's targets.
Proofread first: any typo, wrong capital, unfinished sentence or inconsistent punctuation is a change, with the exact
corrected line. Then pick only the 3-5 changes that would most improve their interview chances. Leave strong lines
alone and say so. Every change must give the complete replacement line, never just advice.
Good changes: sharper verbs, saying what was built and with what, cutting vague or duplicate lines, tightening long
bullets, reordering so the best work leads, making skills match the targets. Do NOT pad bullets with numbers: a
specific technical bullet without a metric is fine. For each change, copy the line's [path] and current text exactly
and give the complete replacement line. """ + _RESUME_RULES


def _resume_lines(r: dict) -> list[tuple[str, str]]:
    """(path, text) for every editable line, in reading order."""
    out = []
    for sec in r.get("section_order") or []:
        for i, x in enumerate(r.get(sec) or []):
            if sec == "experience":
                out.append((f"experience.{i}.title", f"{x.get('title', '')} @ {x.get('company', '')} ({x.get('dates', '')})"))
            elif sec == "projects":
                out.append((f"projects.{i}.name", f"{x.get('name', '')} | {x.get('tech', '')}"))
            elif sec == "education":
                out.append((f"education.{i}.degree", f"{x.get('school', '')}: {x.get('degree', '')}"))
            elif sec == "skills":
                out.append((f"skills.{i}.items", f"{x.get('label', '')}: {x.get('items', '')}"))
            for j, b in enumerate(x.get("bullets") or x.get("details") or (x.get("items") if sec == "extras" else []) or []):
                key = "bullets" if "bullets" in x else "details" if "details" in x else "items"
                out.append((f"{sec}.{i}.{key}.{j}", b))
    return out


def review_resume(r: dict, profile: dict, prefs: dict, job: dict | None = None) -> dict:
    lines = _resume_lines(r)
    student = {"year": profile.get("year_of_study"), "program": profile.get("program"), "school": profile.get("university"),
               "co_op": profile.get("coop_program"), "targets": prefs.get("categories") or profile.get("target_categories")}
    text = "Student: " + json.dumps(student, ensure_ascii=False) + "\n"
    if job:
        text += f"Reviewing for this posting: {job.get('company')} - {job.get('title')}\n{(job.get('description') or '')[:2500]}\n"
    text += "\nResume (one line per [path]):\n" + "\n".join(f"[{p}] {t}" for p, t in lines)
    # Claude thinks hard (effort high); Groq's free tier budget fits medium reasoning plus the answer
    out = _call_json(REVIEW_SYSTEM, [{"type": "text", "text": text}], REVIEW_SCHEMA,
                     effort="high" if provider() == "claude" else "medium", max_tokens=6000)
    known = dict(lines)
    have_numbers = set(re.findall(r"\d+(?:\.\d+)?", " ".join(known.values())))
    changes = []
    for c in out.get("changes") or []:
        path = (c.get("path") or "").strip("[] ")
        if path and path not in known:
            c["path"] = ""  # not a real line: keep it as advice
        elif path:
            c["before"] = known[path]
            if path.endswith((".title", ".name", ".degree")):
                c["path"], c["after"] = "", ""  # heading lines are composites; show as advice, not one-click edits
        if set(re.findall(r"\d+(?:\.\d+)?", c.get("after") or "")) - have_numbers:
            continue  # it made up a number: drop the suggestion
        changes.append(c)
    out["changes"] = changes[:5]
    return _clean(out)


def structure_resume(filename: str, data: bytes, text: str) -> dict:
    content: list = []
    as_pdf = provider() == "claude" and filename.lower().endswith(".pdf")
    if as_pdf:
        content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                       "data": base64.standard_b64encode(data).decode()}})
    content.append({"type": "text", "text": "Convert this resume into the JSON structure. Copy every bullet verbatim; "
                                            "do not improve or shorten anything. changes and missing_keywords: empty."
                                            + ("" if as_pdf else f"\n\n<resume>\n{text}\n</resume>")})
    return _clean(_call_json("You convert resumes to structured JSON exactly as written.", content, RESUME_JSON_SCHEMA,
                             effort="low")["resume"])


def tailor_resume(resume: dict, job_title: str, company: str, description: str) -> dict:
    system = ("You tailor a student's one-page resume to a specific internship posting. You may: reorder sections, "
              "entries' bullets and skills; rephrase bullets to surface relevant skills the resume ALREADY shows; "
              "tighten wording; drop the least relevant bullet or project to stay on one page. You may not add new "
              "facts. " + _NO_FABRICATION + " List each change you made in `changes` (short).")
    prompt = (f"Posting: {job_title} at {company}\n<posting>\n{description[:2500 if _small() else 7000]}\n</posting>\n\n"
              f"<resume_json>\n{json.dumps(resume, ensure_ascii=False)}\n</resume_json>")
    return _call_json(system, [{"type": "text", "text": prompt}], RESUME_JSON_SCHEMA, effort="medium")


def write_cover_letter(fmt: dict, resume: dict, profile: dict, job: dict, description: str, company_info: str) -> str:
    system = ("You write internship and co-op cover letters that sound like a sharp, genuine student, not a template. "
              "Specific beats generic: connect 2-3 concrete things from the resume to what the posting asks for. "
              "Keep it under 330 words unless the format is shorter. Plain text only, no markdown. Use Canadian spelling "
              "if the student studies in Canada. " + _NO_FABRICATION +
              " If you don't know a genuine reason the student likes this company, write one bracketed placeholder "
              "like [why this company: a product you use or a recent launch] rather than inventing one.")
    if fmt.get("style") == "sample":
        fmt_text = f"Match the structure, tone and length of this sample letter the student wrote before:\n{fmt['body']}"
    else:
        fmt_text = (f"Format: {fmt['name']}: {fmt.get('description', '')}\nFollow this skeleton (placeholders in braces "
                    f"show what goes where; write real prose, not braces):\n{fmt['body']}")
    prompt = (f"{fmt_text}\n\nStudent profile: {json.dumps({k: profile.get(k) for k in ('name', 'university', 'program', 'year_of_study', 'coop_terms_completed')}, ensure_ascii=False)}\n"
              f"<resume_json>\n{json.dumps(resume, ensure_ascii=False)}\n</resume_json>\n\n"
              f"Job: {job.get('title')} at {job.get('company')} ({', '.join(job.get('locations') or [])}; "
              f"terms: {', '.join(job.get('terms') or []) or 'n/a'})\n<posting>\n{description[:2500 if _small() else 7000] or '(no description available)'}\n</posting>\n"
              f"Company info: {company_info or 'n/a'}\n\nWrite the letter now.")
    out = _call_json(system, [{"type": "text", "text": prompt}], _obj({"letter": _STR}), effort="medium")
    return out["letter"].strip() + "\n"


# --- follow-ups and interview prep (Autopilot) ---------------------------------------

FOLLOWUP_SCHEMA = _obj({"subject": _STR, "body": _STR})
FOLLOWUP_SYSTEM = """You write short, polite emails for a co-op/internship student: a follow-up on an application that has
gone quiet, or a thank-you after an interview. 3-5 sentences, specific to the role and company, warm but not needy,
no flattery clichés. Use ONLY facts from the student's resume and the posting; never invent experience, numbers,
names or dates. Use [Recruiter name] when the recipient isn't known. Sign off with the student's first name."""


def draft_followup(kind: str, resume_text: str, job: dict, description: str, days: int = 0) -> dict:
    what = ("a thank-you note after an interview" if kind == "thankyou" else
            f"a follow-up on an application submitted about {days} days ago with no reply yet")
    text = (f"Write {what}.\nRole: {job.get('title')} at {job.get('company')}\n<posting>\n{description[:1500]}\n"
            f"</posting>\n<resume>\n{resume_text[:3000]}\n</resume>")
    return _clean(_call_json(FOLLOWUP_SYSTEM, [{"type": "text", "text": text}], FOLLOWUP_SCHEMA, effort="low"))


PREP_SCHEMA = _obj({
    "role_summary": _STR,
    "format": {"type": "string", "description": "what the OA/interview likely looks like for interns at this company"},
    "likely_questions": {"type": "array", "items": _obj({"question": _STR, "why": _STR})},
    "stories": {"type": "array", "items": _obj({"title": _STR, "situation": _STR, "action": _STR, "result": _STR,
                                                "use_for": _STR})},
    "questions_to_ask": _STRS,
    "plan": _STRS,
})
PREP_SYSTEM = """You prepare a co-op/internship student for an online assessment or interview. Be concrete and
specific to the company and role. Likely questions: topics and question types, not leaked questions. Stories: build
3-4 STAR stories ONLY from experience in the student's resume; never invent results, numbers or facts (where a number
would help, write [ask yourself: roughly how many?]). Questions to ask: specific to this team. Plan: a short
day-by-day prep plan for the next 3 days."""


def interview_prep(stage: str, resume_text: str, job: dict, description: str, research: bool = False) -> dict:
    text = (f"Stage: {'online assessment' if stage == 'oa' else 'interview'}\nRole: {job.get('title')} at "
            f"{job.get('company')}\n<posting>\n{description[:3000]}\n</posting>\n<resume>\n{resume_text[:4000]}\n</resume>")
    if research and provider() == "claude_code":  # Claude Code can look the company up first
        try:
            out = claude_code.call_json(PREP_SYSTEM + "\nSearch the web for the company's intern interview process "
                                        "and recent news before answering.", text, PREP_SCHEMA, "medium", web=True,
                                        timeout=420)
            return _clean(out)
        except Exception as e:
            log.warning("prep with web research failed, without it: %s", e)
    return _clean(_call_json(PREP_SYSTEM, [{"type": "text", "text": text}], PREP_SCHEMA, effort="medium"))


RESEARCH_SCHEMA = _obj({
    "what_they_do": _STR, "products": _STRS,
    "recent_news": {"type": "array", "items": _obj({"headline": _STR, "when": _STR, "source": _STR})},
    "tech_stack": _STRS, "intern_program": _STR, "interview_process": _STR, "culture": _STR,
    "talking_points": _STRS, "questions_to_ask": _STRS, "sources": _STRS,
})
RESEARCH_SYSTEM = """You brief a co-op/internship student on a company before they apply, network or interview. Be
specific and current, in plain words. Only state what you found or reliably know; when unsure leave the field empty
or write "not found". Never invent news, numbers, dates or people. Talking points connect the student's REAL resume
to the company (no invented experience). Questions to ask: specific to the company. List the pages you used."""


def research_company(company: str, resume_text: str, job: dict | None = None, description: str = "") -> tuple[dict, bool]:
    """A company brief. Returns (brief, searched_the_web). Your own Claude Code searches the web first."""
    text = (f"Company: {company}\n" + (f"Role they care about: {job.get('title')}\n<posting>\n{description[:2500]}\n"
                                       f"</posting>\n" if job else "") + f"<resume>\n{resume_text[:3500]}\n</resume>")
    if provider() == "claude_code":
        try:
            return _clean(claude_code.call_json(RESEARCH_SYSTEM + "\nSearch the web first: the company's own site, recent "
                                                "news (last 12 months) and intern interview reports.", text,
                                                RESEARCH_SCHEMA, "medium", web=True, timeout=420)), True
        except Exception as e:
            log.warning("company research with the web failed, without it: %s", e)
    return _clean(_call_json(RESEARCH_SYSTEM + "\nYou can't browse right now: use what you reliably know, leave "
                             "recent_news empty and say in culture/interview_process if it may be out of date.",
                             [{"type": "text", "text": text}], RESEARCH_SCHEMA, effort="medium")), False


MESSAGE_KINDS = {
    "reply": "a reply to a recruiter confirming interest and answering what they asked",
    "availability": "a reply sharing availability for an interview or call",
    "extension": "a polite request for a little more time (an assessment or offer deadline)",
    "accept_offer": "accepting an offer",
    "decline_offer": "politely declining an offer, keeping the door open",
    "withdraw": "politely withdrawing an application",
    "networking": "a short LinkedIn / cold message to someone at the company asking for a 15-minute chat about their team",
    "referral": "asking someone they know at the company for a referral to a specific role",
    "recruiter_intro": "a short note to a recruiter introducing themselves for a role",
    "thank_you": "a thank-you after a coffee chat or networking call",
    "other": "the message described in the notes",
}
MESSAGE_SYSTEM = """You write short job-search messages for a co-op/internship student (emails and LinkedIn messages):
3-6 sentences, or under 300 characters for a LinkedIn connection note. Specific, warm, professional, no clichés or
flattery. Use ONLY facts from the student's resume, the posting and their notes; never invent experience, numbers,
names, dates or availability: use [placeholders] like [Name] or [your available times]. Sign off with the student's
first name."""


def draft_message(kind: str, notes: str, resume_text: str, job: dict, description: str = "") -> dict:
    text = (f"Write {MESSAGE_KINDS.get(kind, MESSAGE_KINDS['other'])}.\nCompany: {job.get('company')}\n"
            f"Role: {job.get('title') or '(not a specific role)'}\nRecipient: {job.get('person') or 'unknown'}\n"
            f"Student's notes: {notes or '(none)'}\n<posting>\n{description[:1500]}\n</posting>\n"
            f"<resume>\n{resume_text[:3000]}\n</resume>")
    return _clean(_call_json(MESSAGE_SYSTEM, [{"type": "text", "text": text}], FOLLOWUP_SCHEMA, effort="low"))


def chat(history: list[dict], context: str) -> str:
    system = ("You are Co-op Coach, a friendly, concise career assistant inside a student's internship-hunting app. "
              "You can see their profile, top matches, applications and upcoming events below. Answer in 1-6 short "
              "sentences or a few bullets. Be specific to their data. If they ask you to do something the app does "
              "(star a job, generate a cover letter, tailor a resume), tell them exactly which tab/button to use. "
              "Never invent postings or facts not in the context.\n\n<context>\n" + context + "\n</context>")
    if provider() in ("claude_code", "codex"):
        try:
            return (claude_code if provider() == "claude_code" else codex_cli).chat(system, history[-10:])
        except Exception as e:
            log.warning("your own AI's chat failed (%s), falling back", e)
            if _fallback() != "groq":
                raise
            return groq_client.chat(system, history[-8:])
    if provider() == "groq":
        return groq_client.chat(system, history[-8:])
    resp = _get_client().beta.messages.create(
        model=_model(), max_tokens=2000, betas=[_FALLBACK_BETA], fallbacks="default", system=system,
        output_config={"effort": "low"}, messages=history[-12:],
    )
    if resp.stop_reason == "refusal":
        return "Sorry, I can't help with that one."
    return next((b.text for b in resp.content if b.type == "text"), "").strip()


def batch_size() -> int:
    """How many postings to fit-score per request."""
    return 6 if _small() else 25


def max_scored_per_sync() -> int:
    if provider() in ("claude_code", "codex"):
        return int(config.get("ai.claude_code_max_jobs_scored_per_sync", 60))
    if _small():  # stay well inside ~200k tokens/day on the free tier
        return int(config.get("ai.groq_max_jobs_scored_per_sync", 18))
    return int(config.get("ai.max_jobs_scored_per_sync", 60))
