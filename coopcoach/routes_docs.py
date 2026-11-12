"""API for Resume Studio, cover letters, application packages and the chat helper."""

import json
import logging
import queue
import re
import secrets
import shutil
import threading
import time

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from . import (
    ai,
    companies,
    coverletter,
    db,
    events,
    jobdetail,
    mail,
    render,
    resume,
    store,
)
from . import resume_studio as rs
from . import resume_theme as theme
from .config import UPLOAD_DIR

log = logging.getLogger(__name__)
router = APIRouter()


def _profile(pid: int) -> dict:
    p = store.get_profile(pid)
    if not p:
        raise HTTPException(404, "profile not found")
    return p


def _doc(pid: int, kind: str, job_id: str | None = None) -> dict | None:
    if job_id is None:
        return db.row("SELECT * FROM documents WHERE profile_id=? AND kind=? AND job_id IS NULL ORDER BY updated_at DESC"
                      " LIMIT 1", (pid, kind))
    return db.row("SELECT * FROM documents WHERE profile_id=? AND kind=? AND job_id=? ORDER BY updated_at DESC LIMIT 1",
                  (pid, kind, job_id))


def _save_doc(pid: int, kind: str, content: str, title: str, job_id: str | None = None, fmt: str | None = None,
              by: str = "you") -> int:
    now = int(time.time())
    existing = _doc(pid, kind, job_id)
    with db.tx() as c:
        if existing:
            c.execute("UPDATE documents SET content=?, title=?, format_id=COALESCE(?, format_id), generated_by=?,"
                      " updated_at=? WHERE id=?", (content, title, fmt, by, now, existing["id"]))
            return existing["id"]
        return c.execute("INSERT INTO documents (profile_id, kind, job_id, title, format_id, content, generated_by,"
                         " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (pid, kind, job_id, title, fmt, content, by, now, now)).lastrowid


def build_master(pid: int, use_ai: bool = True, if_unchanged_since: int | None = None) -> dict | None:
    """Structure the uploaded resume for Resume Studio. The rule-based build is instant; the AI one (run in the
    background after an upload) only replaces it if nobody has edited the resume in the meantime."""
    p = _profile(pid)
    if not p.get("resume_text"):
        return None
    rules = rs.build_from_text(p["resume_text"], p["profile"])
    r, by = None, "rules"
    if use_ai and ai.available() and p.get("resume_filename"):
        f = UPLOAD_DIR / f"{pid}_{p['resume_filename']}"
        if f.exists():
            try:
                r, by = ai.structure_resume(p["resume_filename"], f.read_bytes(), p["resume_text"]), ai.provider()
                r["style"] = rules["style"]  # layout detected from the file (e.g. company above title)
                r["header"]["name"] = resume.whole_name(r["header"].get("name"), resume.name_line(p["resume_text"]))
            except Exception as e:
                log.warning("AI resume structuring failed, using rules: %s", e)
    old = _doc(pid, "resume_master")
    if if_unchanged_since is not None and old and old["updated_at"] > if_unchanged_since:
        return rs.normalize(db.loads(old["content"], {}))  # edited while the AI was working: keep theirs
    r = r or rules
    if not old:  # a new resume starts in the Signature look
        r["style"] = {**(r.get("style") or {}), **theme.PRESETS["signature"]["style"]}
    if old and old["generated_by"] == "you":  # keep colours / icon choices someone already picked
        mine = (db.loads(old["content"], {}) or {}).get("style") or {}
        r["style"] = {**r.get("style", {}), **{k: v for k, v in mine.items() if k != "company_first"}}
    r = rs.normalize(rs.attach_file_links(r, p["profile"]))
    _save_doc(pid, "resume_master", db.dumps(r), "Master resume", by=by)
    _save_doc(pid, "resume_source", db.dumps(r), "What the file said", by="rules")  # for "Check against my file"
    return r


def master(pid: int) -> dict | None:
    d = _doc(pid, "resume_master")
    if d:
        r = db.loads(d["content"], {}) or {}
        tpl = (store.get_profile(pid) or {}).get("prefs", {}).get("resume_template", "jake")
        r["style"] = theme.with_preset(r.get("style") if isinstance(r.get("style"), dict) else {}, tpl)
        return rs.normalize(r)
    return build_master(pid)


@router.get("/api/resume")
def get_resume(profile_id: int):
    p = _profile(profile_id)
    r = master(profile_id)
    d = _doc(profile_id, "resume_master")
    import shutil
    return {"resume": r, "lint": rs.lint(r) if r else None, "templates": rs.TEMPLATES, "theme": theme_schema(),
            "themes": p["prefs"].get("resume_themes") or {},
            "latex_compiler": bool(shutil.which("tectonic")),
            "template": p["prefs"].get("resume_template", "jake"), "built_by": d["generated_by"] if d else None,
            "updated_at": d["updated_at"] if d else None, "ai": ai.available()}


@router.put("/api/resume")
def put_resume(body: dict = Body(...)):
    pid = body["profile_id"]
    _profile(pid)
    r = rs.normalize(body["resume"])
    _save_doc(pid, "resume_master", db.dumps(r), "Master resume", by="you")
    if body.get("template") in rs.TEMPLATES:
        _set_pref(pid, "resume_template", body["template"])
    return {"ok": True, "lint": rs.lint(r)}


def theme_schema() -> dict:
    """What the Design editor offers (fonts, presets, options), so the page and the server agree on the lists."""
    return {"fonts": theme.FONTS, "presets": theme.PRESETS, "numbers": theme.NUMBERS, "keys": theme.THEME_KEYS,
            "heading_styles": theme.HEADING_STYLES, "bullets": theme.BULLETS, "date_align": theme.DATE_ALIGN,
            "density": theme.DENSITY, "rule_tints": theme.RULE_TINTS, "palette": rs.PALETTE,
            "heading_vars": {k: theme.heading_vars(k) for k in theme.HEADING_STYLES}}


@router.put("/api/resume/themes")
def save_theme(body: dict = Body(...)):
    """Save the look under a name (only the look: fonts, colours, sizes... never resume content)."""
    pid = body["profile_id"]
    p = _profile(pid)
    name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:40]
    if not name:
        raise HTTPException(400, "give the theme a name")
    st = rs.normalize({"style": body.get("style") or {}})["style"]
    themes = dict(p["prefs"].get("resume_themes") or {})
    if name not in themes and len(themes) >= 30:
        raise HTTPException(400, "30 saved themes is the limit; delete one first")
    themes[name] = {k: st[k] for k in theme.THEME_KEYS}
    _set_pref(pid, "resume_themes", themes)
    return {"themes": themes}


@router.delete("/api/resume/themes")
def delete_theme(profile_id: int, name: str):
    p = _profile(profile_id)
    themes = dict(p["prefs"].get("resume_themes") or {})
    themes.pop(name, None)
    _set_pref(profile_id, "resume_themes", themes)
    return {"themes": themes}


@router.post("/api/resume/theme/ai")
def design_with_ai(body: dict = Body(...)):
    """Design → "Describe your look": settings only; the resume's words aren't sent."""
    ask = str(body.get("ask") or "").strip()
    if not ask:
        raise HTTPException(400, "Say what you'd like, e.g. clean and modern, navy headings")
    if not ai.available():
        raise HTTPException(400, "This needs an AI connected: Settings → AI. You can still change everything by hand.")
    style = body.get("style") if isinstance(body.get("style"), dict) else {}
    try:
        return ai.design_resume(ask, style)
    except Exception as e:
        log.warning("AI resume design failed: %s", e)
        raise HTTPException(502, "The AI couldn't do that just now. Try again, or change it by hand.")


@router.get("/api/resume/theme/shuffle")
def shuffle_theme():
    return theme.shuffle()


@router.post("/api/resume/lint")
def lint_resume(body: dict = Body(...)):
    return rs.lint(body["resume"])


def fresh_from_file(pid: int) -> dict | None:
    """Resume Studio's structure read straight from the uploaded file with today's parser (nothing is saved)."""
    p = _profile(pid)
    if not p.get("resume_text"):
        return None
    return rs.normalize(rs.attach_file_links(rs.build_from_text(p["resume_text"], p["profile"]), p["profile"]))


@router.post("/api/resume/fix")
def fix_resume(body: dict = Body(...)):
    """Apply checker fixes: a list of issue ids, or "auto" for every safe small one (spaces, split lines, capitals)."""
    r, n = rs.apply_fixes(body.get("resume") or master(body["profile_id"]) or {}, body.get("ids") or "auto")
    return {"resume": r, "lint": rs.lint(r), "applied": n}


@router.post("/api/resume/compare")
def compare_resume(body: dict = Body(...)):
    """Every difference between the Studio copy (with your edits) and a fresh read of your uploaded file."""
    pid = body["profile_id"]
    fresh = fresh_from_file(pid)
    if not fresh:
        raise HTTPException(400, "add your resume in Settings → Profile first")
    mine = rs.normalize(body.get("resume") or master(pid) or {})
    snap = _doc(pid, "resume_source")
    items = rs.compare(mine, fresh, db.loads(snap["content"]) if snap else None)
    auto = [i for i in rs.lint(mine)["issues"] if i.get("auto")]
    return {"items": items, "small_fixes": len(auto)}


@router.post("/api/resume/compare/apply")
def compare_apply(body: dict = Body(...)):
    pid = body["profile_id"]
    fresh = fresh_from_file(pid)
    if not fresh:
        raise HTTPException(400, "add your resume in Settings → Profile first")
    snap = _doc(pid, "resume_source")
    r, n = rs.apply_compare(body.get("resume") or master(pid) or {}, fresh, body.get("ids") or [],
                            db.loads(snap["content"]) if snap else None)
    _save_doc(pid, "resume_source", db.dumps(fresh), "What the file said", by="rules")
    return {"resume": r, "lint": rs.lint(r), "applied": n}


@router.post("/api/resume/rebuild")
def rebuild_resume(body: dict = Body(...)):
    """Re-read the uploaded file with the current parser (text, hidden links, layout) and rebuild."""
    from . import server
    pid = body["profile_id"]
    r = master(pid) if server.reread_resume(pid, background_ai=False) else build_master(pid, use_ai=False)
    if not r:
        raise HTTPException(400, "add your resume in Settings → Profile first")
    return {"resume": r, "lint": rs.lint(r)}


@router.post("/api/resume/preview", response_class=HTMLResponse)
def preview_resume(body: dict = Body(...)):
    return render.resume_html(body["resume"], body.get("template", "jake"))


@router.post("/api/resume/review")
def review(body: dict = Body(...)):
    """🧠 Resume Coach: a whole-resume read with the few changes that matter (AI), or a rule-based summary."""
    pid = body["profile_id"]
    p = _profile(pid)
    r = rs.normalize(body.get("resume") or master(pid) or {})
    job = _job(body["job_id"], pid) if body.get("job_id") else None
    rules = rs.rule_review(r)
    mistakes = [c for c in rules["changes"] if c.get("fix_id") or c["title"] == "Needs you"]
    if ai.available():
        try:
            v = ai.review_resume(r, p["profile"], p["prefs"], job)
            # exact proofreading fixes first (one click each), then the coach's bigger-picture changes
            v["changes"] = [c for c in mistakes if any(w in c["why"] for w in ("Typo", "Capitals", "Spelling", "mid-sentence",
                                                                             "split", "line above", "stuck"))][:4] + v["changes"]
            return {"review": v, "by": ai.provider()}
        except Exception as e:
            log.warning("AI resume review failed, using rules: %s", e)
    return {"review": rules, "by": "rules"}


@router.post("/api/resume/improve")
def improve(body: dict = Body(...)):
    bullets = [b for b in body.get("bullets", []) if b and b.strip()][:12]
    if not bullets:
        return {"results": [], "by": "rules"}
    if ai.available():
        try:
            return {"results": ai.improve_bullets(bullets, body.get("context", ""), body.get("job_title", "")),
                    "by": ai.provider()}
        except Exception as e:
            log.warning("AI bullet rewrite failed, using rules: %s", e)
    return {"results": [{"index": i, "options": rs.suggest_rewrites(b), "note": "Rule-based: fill the [brackets] with real numbers"}
                        for i, b in enumerate(bullets)], "by": "rules"}


def _job(job_id: str, pid: int | None = None) -> dict:
    j = db.row("SELECT * FROM jobs WHERE id=?", (job_id,))
    if j:
        return store._decode(j)
    a = db.row("SELECT * FROM applications WHERE job_id=? AND (? IS NULL OR profile_id=?)", (job_id, pid, pid))
    if a:
        return {"id": job_id, "company": a["company"] or "", "title": a["title"] or "", "url": a["url"] or "",
                "locations": [], "terms": [], "description": "", "tier": "other"}
    raise HTTPException(404, "job not found")


@router.post("/api/resume/tailor")
def tailor(body: dict = Body(...)):
    pid, job_id = body["profile_id"], body["job_id"]
    base = master(pid)
    if not base:
        raise HTTPException(400, "add your resume in Settings → Profile first")
    job = _job(job_id, pid)
    desc = jobdetail.ensure_description(job_id) if not job_id.startswith("ext:") else ""
    tailored, report = rs.tailor(base, desc, job["title"])
    by = "rules"
    if ai.available() and body.get("use_ai", True):
        try:
            out = ai.tailor_resume(base, job["title"], job["company"], desc)
            tailored, by = rs.normalize(out["resume"]), ai.provider()
            report["changes"] = out.get("changes") or report["changes"]
            report["missing"] = out.get("missing_keywords") or report["missing"]
        except Exception as e:
            log.warning("AI tailoring failed, using rule-based reorder: %s", e)
    after = rs.keyword_report(tailored, desc, job["title"])
    report["coverage_after"] = after["coverage"]
    doc_id = _save_doc(pid, "resume", db.dumps(tailored), f"{job['company']} · {job['title']}", job_id=job_id, by=by)
    return {"doc_id": doc_id, "resume": tailored, "report": report, "lint": rs.lint(tailored), "by": by,
            "has_description": bool(desc)}


def _resume_for(pid: int, job_id: str | None) -> dict | None:
    m = master(pid)
    if job_id:
        d = _doc(pid, "resume", job_id)
        if d:
            r = rs.normalize(db.loads(d["content"], {}))
            if m:  # a tailored copy changes the content, never the look: that follows your current theme
                r["style"].update({k: m["style"][k] for k in theme.THEME_KEYS})
            return r
    return m


@router.post("/api/resume/warm")
def warm_pdf():
    """The Documents page is open: have Chrome ready, so the first PDF is quick (it closes itself after 90 idle seconds)."""
    render.warm_printer()
    return {"ok": True}


@router.get("/api/resume/export")
def export_resume(profile_id: int, fmt: str = "pdf", template: str = "", job_id: str = "", level: int | None = None):
    p = _profile(profile_id)
    r = _resume_for(profile_id, job_id or None)
    if not r:
        raise HTTPException(400, "no resume yet")
    tpl = template or p["prefs"].get("resume_template", "jake")
    name = render.safe_name(r["header"].get("name") or p["name"], "Resume")
    if fmt == "docx":
        return Response(render.resume_docx(r, level if level is not None else _fit_level(r, tpl), tpl),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'})
    if fmt == "html":
        return HTMLResponse(render.resume_html(r, tpl))
    if fmt == "tex":
        return Response(render.resume_latex(r, _tex_level(r)), media_type="application/x-tex",
                        headers={"Content-Disposition": f'attachment; filename="{name}.tex"'})
    if fmt == "latexpdf":
        try:
            data, lv = render.latex_pdf_fit(r)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        if data is None:
            raise HTTPException(400, "Install Tectonic (brew install tectonic) for the exact LaTeX PDF, or use Open in Overleaf")
        _TEX_FIT[_key(r)] = lv
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}.pdf"',
                                                                     "X-Page-Count": str(render.page_count(data))})
    info: dict = {}
    data = render.pdf(render.resume_html(r, tpl), info)
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{name}.pdf"', "X-Page-Count": str(render.page_count(data)),
        "X-Fit": json.dumps(info)})


# One-page fitting results, remembered per resume version so Word/LaTeX downloads match the PDF.
_TEX_FIT: dict[str, int] = {}


def _key(r: dict) -> str:
    import hashlib
    return hashlib.sha1(json.dumps(rs.normalize(r), sort_keys=True).encode()).hexdigest()


def _tex_level(r: dict) -> int:
    """LaTeX fit level for this exact resume: from a recent LaTeX PDF, else compile to find it (Tectonic)."""
    k = _key(r)
    if k not in _TEX_FIT and shutil.which("tectonic") and rs.normalize(r)["style"].get("one_page"):
        try:
            _TEX_FIT[k] = render.latex_pdf_fit(r)[1]
        except Exception:
            _TEX_FIT[k] = 0
    return _TEX_FIT.get(k, 0)


def _fit_level(r: dict, tpl: str) -> int:
    if not rs.normalize(r)["style"].get("one_page"):
        return 0
    info: dict = {}
    try:
        render.pdf(render.resume_html(r, tpl), info)
    except Exception:
        return 0
    return int(info.get("level") or 0)


# --- cover letters --------------------------------------------------------------------------------

def _set_pref(pid: int, key: str, value) -> None:
    p = store.get_profile(pid)
    prefs = p["prefs"]
    prefs[key] = value
    with db.tx() as c:
        c.execute("UPDATE profiles SET prefs_json=? WHERE id=?", (db.dumps(prefs), pid))


def generate_letter(pid: int, job_id: str, format_id: str | None = None, use_ai: bool = True, quick: bool = False) -> dict:
    """quick: right now, from what's already stored (no AI, nothing fetched), for the polish queue to improve on."""
    p = _profile(pid)
    job = _job(job_id, pid)
    fmt = coverletter.get_format(pid, format_id or p["prefs"].get("letter_format", "classic"))
    if quick:
        use_ai, desc = False, (db.row("SELECT description FROM jobs WHERE id=?", (job_id,)) or {}).get("description") or ""
    else:
        desc = jobdetail.ensure_description(job_id) if not job_id.startswith("ext:") else ""
    yc = companies.yc_lookup(job["company"])
    resume = master(pid) or {}
    text, by = None, "rules"
    if use_ai and ai.available():
        try:
            info = f"YC {yc['batch']}: {yc.get('one_liner')}" if yc else ""
            text, by = ai.write_cover_letter(fmt, resume, p["profile"], job, desc, info), ai.provider()
        except Exception as e:
            log.warning("AI cover letter failed, using template: %s", e)
    if text is None:
        body = fmt["body"] if fmt.get("style") == "template" else coverletter.BUILTIN_FORMATS[0]["body"]
        text = coverletter.fill(body, coverletter.facts(p["profile"], resume, job, desc, yc))
    doc_id = coverletter.save_letter(pid, job_id, f"{job['company']} · {job['title']}", fmt["id"], text, by)
    return _letter(doc_id)


def _letter(doc_id: int) -> dict:
    d = db.row("SELECT * FROM documents WHERE id=? AND kind='cover_letter'", (doc_id,))
    if not d:
        raise HTTPException(404)
    d["placeholders"] = coverletter.placeholders_left(d["content"])
    return d


def auto_letter(pid: int, job_id: str) -> None:
    """Called when a posting is starred or tracked: draft a letter in the background if there isn't one."""
    p = store.get_profile(pid)
    if not p or not p["prefs"].get("auto_cover_letters", True) or not p.get("resume_text"):
        return
    if db.row("SELECT 1 FROM documents WHERE profile_id=? AND kind='cover_letter' AND job_id=?", (pid, job_id)):
        return

    def run():
        try:
            generate_letter(pid, job_id)
        except Exception as e:
            log.warning("auto cover letter failed: %s", e)

    threading.Thread(target=run, daemon=True).start()


@router.get("/api/letters/formats")
def formats(profile_id: int):
    p = _profile(profile_id)
    return {"formats": coverletter.list_formats(profile_id), "default": p["prefs"].get("letter_format", "classic"),
            "placeholders": coverletter.PLACEHOLDERS, "auto": p["prefs"].get("auto_cover_letters", True)}


@router.post("/api/letters/formats")
def add_format(body: dict = Body(...)):
    pid = body["profile_id"]
    name, text = (body.get("name") or "").strip(), (body.get("body") or "").strip()
    if not name or len(text) < 40:
        raise HTTPException(400, "give the format a name and paste at least a few lines")
    style = "template" if re.search(r"\{(company|role|name)\}", text) else "sample"
    fid = f"custom-{secrets.token_hex(3)}"
    with db.tx() as c:
        c.execute("INSERT INTO letter_formats (id, profile_id, name, description, body, style, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (fid, pid, name[:60], (body.get("description") or "")[:200], text, style,
                                              int(time.time())))
    return {"id": fid, "style": style}


@router.delete("/api/letters/formats/{fid}")
def delete_format(fid: str, profile_id: int):
    with db.tx() as c:
        c.execute("DELETE FROM letter_formats WHERE id=? AND profile_id=?", (fid, profile_id))
    return {"ok": True}


@router.put("/api/letters/settings")
def letter_settings(body: dict = Body(...)):
    pid = body["profile_id"]
    if "default" in body:
        _set_pref(pid, "letter_format", body["default"])
    if "auto" in body:
        _set_pref(pid, "auto_cover_letters", bool(body["auto"]))
    return {"ok": True}


@router.post("/api/letters/generate")
def gen_letter(body: dict = Body(...)):
    return generate_letter(body["profile_id"], body["job_id"], body.get("format_id"), body.get("use_ai", True))


@router.get("/api/letters")
def list_letters(profile_id: int):
    docs = db.rows("SELECT d.id, d.job_id, d.title, d.format_id, d.generated_by, d.updated_at, length(d.content) AS chars,"
                   " d.content FROM documents d WHERE d.profile_id=? AND d.kind='cover_letter' ORDER BY d.updated_at DESC",
                   (profile_id,))
    for d in docs:
        d["placeholders"] = len(coverletter.placeholders_left(d.pop("content")))
    return docs


@router.get("/api/letters/{doc_id}")
def get_letter(doc_id: int):
    return _letter(doc_id)


@router.put("/api/letters/{doc_id}")
def put_letter(doc_id: int, body: dict = Body(...)):
    with db.tx() as c:
        c.execute("UPDATE documents SET content=?, generated_by='you', updated_at=? WHERE id=? AND kind='cover_letter'",
                  (body["content"], int(time.time()), doc_id))
    return _letter(doc_id)


@router.delete("/api/letters/{doc_id}")
def delete_letter(doc_id: int):
    with db.tx() as c:
        c.execute("DELETE FROM documents WHERE id=? AND kind='cover_letter'", (doc_id,))
    return {"ok": True}


@router.get("/api/letters/{doc_id}/export")
def export_letter(doc_id: int, fmt: str = "pdf"):
    d = _letter(doc_id)
    p = _profile(d["profile_id"])
    r = master(d["profile_id"]) or {"header": {"name": p["name"]}}
    name = render.safe_name(r["header"].get("name") or p["name"], "Cover-Letter", d["title"].split(" · ")[0])
    if fmt == "txt":
        return Response(d["content"], media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{name}.txt"'})
    if fmt == "docx":
        return Response(render.letter_docx(d["content"], r["header"]),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'})
    tpl = p["prefs"].get("resume_template", "jake")
    return Response(render.pdf(render.letter_html(d["content"], r["header"], tpl)), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})


# --- application package --------------------------------------------------------------------------

@router.get("/api/package")
def package(profile_id: int, job_id: str):
    p = _profile(profile_id)
    job = _job(job_id, profile_id)
    letter = db.row("SELECT id FROM documents WHERE profile_id=? AND kind='cover_letter' AND job_id=?"
                    " ORDER BY updated_at DESC LIMIT 1", (profile_id, job_id))
    letter = _letter(letter["id"]) if letter else (generate_letter(profile_id, job_id) if p.get("resume_text") else None)
    desc = db.row("SELECT description FROM jobs WHERE id=?", (job_id,)) or {}
    base = _resume_for(profile_id, job_id)
    report = rs.keyword_report(base, desc.get("description") or "", job["title"]) if base else None
    tailored = _doc(profile_id, "resume", job_id)
    return {"job": {k: job.get(k) for k in ("id", "company", "title", "url", "locations", "terms", "tier")},
            "letter": letter, "report": report, "tailored": bool(tailored),
            "resume_score": rs.lint(base)["score"] if base else None,
            "formats": [{"id": f["id"], "name": f["name"]} for f in coverletter.list_formats(profile_id)]}


@router.get("/api/package/download")
def package_download(profile_id: int, job_id: str):
    p = _profile(profile_id)
    job = _job(job_id, profile_id)
    r = _resume_for(profile_id, job_id)
    if not r:
        raise HTTPException(400, "upload a resume first")
    tpl = p["prefs"].get("resume_template", "jake")
    letter = db.row("SELECT * FROM documents WHERE profile_id=? AND kind='cover_letter' AND job_id=?"
                    " ORDER BY updated_at DESC LIMIT 1", (profile_id, job_id)) or generate_letter(profile_id, job_id)
    who = render.safe_name(r["header"].get("name") or p["name"])
    comp = render.safe_name(job["company"])
    desc = (db.row("SELECT description FROM jobs WHERE id=?", (job_id,)) or {}).get("description") or ""
    rep = rs.keyword_report(r, desc, job["title"])
    todo = coverletter.placeholders_left(letter["content"])
    notes = "\n".join([
        f"# {job['company']}: {job['title']}", "", f"Apply: {job.get('url') or 'n/a'}", "",
        "## Checklist",
        f"- [ ] Fill {len(todo)} bracketed spot(s) in the cover letter" if todo else "- [x] Cover letter has no blanks left",
        "- [ ] Re-read the resume PDF (one page?)",
        "- [ ] Submit, then mark it Applied in Co-op Coach (or let the inbox sync do it)", "",
        "## Keywords from the posting",
        f"Coverage: {rep['coverage']}%" if rep["coverage"] is not None else "No description available.",
        f"On your resume: {', '.join(rep['matched']) or 'n/a'}",
        f"Missing (add only if true): {', '.join(rep['missing']) or 'none'}", "",
        *(["## Blanks to fill in the letter", *[f"- {t}" for t in todo]] if todo else []),
    ])
    fit: dict = {}
    files = {
        f"{who}_Resume_{comp}.pdf": render.pdf(render.resume_html(r, tpl), fit),
        f"{who}_Resume_{comp}.docx": render.resume_docx(r, int(fit.get("level") or 0), tpl),
        f"{who}_Resume_{comp}.tex": render.resume_latex(r, _tex_level(r)),  # Jake's template, opens in Overleaf
        f"{who}_Cover-Letter_{comp}.pdf": render.pdf(render.letter_html(letter["content"], r["header"], tpl)),
        f"{who}_Cover-Letter_{comp}.docx": render.letter_docx(letter["content"], r["header"]),
        f"{who}_Cover-Letter_{comp}.txt": letter["content"],
        "README_checklist.md": notes,
    }
    zname = render.safe_name(who, comp, "application")
    return Response(render.package_zip(files), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{zname}.zip"'})


# --- agent tools + apply assistant ---------------------------------------------------------------------------

@router.post("/api/agent/tool")
def agent_tool(body: dict = Body(...)):
    """Run one agent tool directly (chat card buttons, Undo)."""
    from . import agent
    out = agent.call(body["profile_id"], body.get("name", ""), body.get("args") or {}, source="card")
    out.pop("_card", None)
    return out


@router.get("/api/apply/{job_id}/kit")
def apply_kit(job_id: str, profile_id: int, fresh: bool = False):
    from . import agent, apply
    kit = (None if fresh else agent.cached_kit(profile_id, job_id)) or apply.build_kit(profile_id, job_id)
    if kit.get("error"):
        raise HTTPException(404, kit["error"])
    return kit


@router.post("/api/apply/{job_id}/fill")
def apply_fill(job_id: str, body: dict = Body(...)):
    from . import apply, workday
    if not workday.available():
        raise HTTPException(400, "Filling forms needs the playwright package: .venv/bin/pip install playwright")
    apply.fill(body["profile_id"], job_id)
    return {"started": True}


@router.get("/api/apply/fill-state")
def apply_fill_state(profile_id: int):
    from . import apply
    return apply.fill_state(profile_id)


@router.get("/api/apply/{job_id}/agent-prompt")
def apply_agent_prompt(job_id: str, profile_id: int, request: Request):
    from . import apply
    kit = apply.build_kit(profile_id, job_id)
    if kit.get("error"):
        raise HTTPException(404, kit["error"])
    return {"prompt": apply.agent_prompt(kit, str(request.base_url).rstrip("/"))}


# --- chat ------------------------------------------------------------------------------------------------

def _chat_context(pid: int) -> dict:
    p = _profile(pid)
    jobs = sorted(store.scored_jobs(pid), key=lambda j: -j["score"])
    st = mail.stats(pid)
    apps = db.rows("SELECT a.status, COALESCE(j.company, a.company) AS company, COALESCE(j.title, a.title) AS title"
                   " FROM applications a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.profile_id=? ORDER BY a.updated_at DESC"
                   " LIMIT 25", (pid,))
    evs = events.upcoming(21)[:8]
    r = _resume_for(pid, None)
    lint = rs.lint(r) if r else None
    new = [j for j in jobs if (j.get("posted_at") or 0) > time.time() - 86400][:8]
    return {"p": p, "jobs": jobs, "stats": st, "apps": apps, "events": evs, "lint": lint, "new": new}


def _rules_reply(pid: int, msg: str) -> str:
    c = _chat_context(pid)
    m = msg.lower()
    st = c["stats"]
    fmt_job = lambda j: f"• {j['company']}: {j['title']} ({j['score']}%) {j['url']}"  # noqa: E731
    if re.search(r"\bpath\b|streak|\bxp\b|\blevel\b|daily goal|leetcode|coding problem|lesson", m):
        from . import path
        s = path.summary(pid)
        lv = s["level"]
        nxt = s["next_steps"][:3]
        return (f"🔥 {s['streak']}-day streak (best {s['best_streak']}) · {s['today']}/{s['goal']} XP today · level "
                f"{lv['level']}, {lv['name']}" + (f" ({lv['to'] - lv['xp']} XP to {lv['next_name']})" if lv["to"] else "")
                + ".\n" + ("Next on your Path:\n" + "\n".join(f"• {t}" for t in nxt) + "\n" if nxt else "")
                + "Open Today → 🏆 Path for your NeetCode 150 and where you're at.")
    if re.search(r"startup|\byc\b|y combinator", m):
        from . import startups
        d = startups.feed(pid, "waterloo", hiring=True, limit=5)
        if not d["startups"]:
            return "No Waterloo startups hiring right now that I know of. Jobs → 🚀 Startups shows Toronto and Canada too."
        return ("Startups hiring in Waterloo Region, fastest first:\n" + "\n".join(
            f"• {r['name']}: {r['one_liner'] or r['where']}" + (f" ({r['open']} open)" if r["open"] else "")
            for r in d["startups"]) + "\nMore (Toronto, Canada) in Jobs → 🚀 Startups.")
    if re.search(r"how many|stats|count|applied|rejection|interview|offer|status|progress", m):
        if not st["applied_total"]:
            return ("You haven't tracked any applications yet. Use the Track… menu on a posting, add one by hand on the "
                    "Applications tab, or connect your inbox and I'll pick them up from confirmation emails.")
        return (f"You've applied to {st['applied_total']}: {st['awaiting']} waiting, {st['oa']} OA, "
                f"{st['interviewing']} interviewing, {st['offer']} offers, {st['rejected']} rejections "
                f"(response rate {st['response_rate']}%)."
                + (f" {st['stale']} have been quiet for 3+ weeks; worth a follow-up." if st["stale"] else ""))
    if re.search(r"devpost|submi\w* (my|our|the) (project|hack)|hackathon (submission|project|write.?up)", m):
        return ("After a hackathon: Events → Hackathons → 🏁 Submit (or 🏁 Submit project on the tracker). Paste your "
                "project's public GitHub link and I write the Devpost submission from its README and what it's built "
                "with: the elevator pitch, Devpost's sections, the Built with tags and a two-minute demo script, with "
                "[brackets] for what only your team knows. Then one click puts it on your resume.")
    if re.search(r"(apply|application|register|sign up)\w* (to|for) (a |the )?hackathon|hackathon (application|apply)"
                 r"|apply to (hack|uofthacks|hackthe)", m):
        return ("Events → Hackathons → 📝 Apply on any of them: your details from your resume, the usual short answers "
                "drafted from your real projects, and your t-shirt and dietary answers remembered. Download it, or let "
                "me fill the form in Chrome. You tick MLH's boxes and press Submit, then ✓ I applied puts it on your "
                "tracker.")
    if re.search(r"event|coffee|network|info session|career fair|hackathon|meetup", m):
        if not c["events"]:
            return "No upcoming career events yet. Add your career centre's calendar on the Events tab."
        return "Coming up:\n" + "\n".join(
            f"• {time.strftime('%a %b %d', time.localtime(e['start_ts']))}: {e['title']}" for e in c["events"][:6])
    if re.search(r"resume|cv", m):
        if not c["lint"]:
            return "Add your resume in Settings → Profile, then open Documents → Resume Studio."
        top = "\n".join(f"• {i['message']}" for i in c["lint"]["issues"][:4])
        return f"Your resume scores {c['lint']['score']}/100 ({c['lint']['grade']}). Biggest wins:\n{top}\nFix them in Documents → Resume Studio."
    if re.search(r"cover letter|letter", m):
        return ("Hit 📦 Package on any posting: it drafts a cover letter in your default format, tailors your resume and "
                "zips everything. Change formats or add your own in Documents → Cover letters.")
    if re.search(r"new|today|latest|just posted|fresh", m):
        return ("Posted in the last 24h that fit you:\n" + "\n".join(fmt_job(j) for j in c["new"][:5])) if c["new"] \
            else "Nothing new in the last 24 hours yet. I'll ping you when something lands."
    if re.search(r"best|top|match|recommend|apply|should i|which", m):
        return "Your strongest open matches:\n" + "\n".join(fmt_job(j) for j in c["jobs"][:5])
    if re.search(r"workday|password|login", m):
        return "Sign in to company sites yourself. Connect your inbox in Settings → Inbox for application updates."
    return ("I can tell you about your applications, best matches, new postings, upcoming events, your resume score "
            "or cover letters. Try “what should I apply to?” or “how many interviews do I have?”"
            + ("" if ai.available() else "\n(Add a free Groq key in .env for full conversations.)"))


@router.post("/api/applications/from-email")
def application_from_email(body: dict = Body(...)):
    """Applications → Paste an email: the same as pasting it into the chat (rules only, nothing sent anywhere)."""
    from . import agent
    out = agent.call(body["profile_id"], "log_email", {"text": body.get("text") or ""}, source="app")
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


@router.post("/api/chat/warm")
def chat_warm(body: dict = Body(...)):
    """The chat was opened: have Claude Code ready before the first message."""
    from . import ai, claude_chat
    if ai.provider() == "claude_code":
        claude_chat.warm(int(body["profile_id"]))
    return {"ok": True}


@router.post("/api/chat/stream")
def chat_stream(body: dict = Body(...)):
    """The chat, as it's written: one JSON object a line (delta, step, then done with everything /api/chat returns)."""
    said = [m for m in body.get("messages") or [] if m.get("role") in ("user", "assistant") and m.get("content")]
    if "profile_id" not in body or not said or said[-1]["role"] != "user":  # (checked now: after this, the reply has begun)
        raise HTTPException(400, "send a user message")
    out: queue.Queue = queue.Queue()

    def work():
        try:
            out.put({"t": "done", "v": _chat_turn(body, lambda kind, v: out.put({"t": kind, "v": v}))})
        except HTTPException as e:
            out.put({"t": "error", "v": str(e.detail)})
        except Exception as e:
            log.warning("chat failed: %s", e)
            out.put({"t": "error", "v": str(e)[:300]})
        out.put(None)
    threading.Thread(target=work, daemon=True, name="chat").start()

    def lines():
        while True:
            item = out.get()
            if item is None:
                return
            yield json.dumps(item, ensure_ascii=False, default=str) + "\n"
    return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@router.post("/api/chat")
def chat(body: dict = Body(...)):
    return _chat_turn(body)


def _chat_turn(body: dict, on_event=None) -> dict:
    pid = body["profile_id"]
    history = [{"role": m["role"], "content": str(m["content"])[:10000]} for m in body.get("messages", [])
               if m.get("role") in ("user", "assistant") and m.get("content")]
    if not history or history[-1]["role"] != "user":
        raise HTTPException(400, "send a user message")
    if ai.available() and body.get("focus") != "resume":  # the main chat is an agent that can act
        from . import agent
        try:
            return agent.run(pid, history, on_event=on_event)
        except Exception as e:
            log.warning("agent chat failed, falling back: %s", e)
    elif body.get("focus") != "resume":  # with no AI connected, the exact requests still get a real answer
        from . import quick_chat
        quick = quick_chat.answer(pid, history[-1]["content"])
        if quick:
            return quick
    if ai.available():
        c = _chat_context(pid)
        p = c["p"]
        ctx = "\n".join([
            f"Student: {p['name']}; {p['profile'].get('summary', '')}",
            f"Preferences: {db.dumps({k: p['prefs'].get(k) for k in ('categories', 'locations', 'terms', 'aim')})}",
            f"Application stats: {db.dumps(c['stats'])}",
            "Recent applications: " + "; ".join(f"{a['company']} {a['title']} [{a['status']}]" for a in c["apps"]),
            "Top matches: " + "; ".join(f"{j['company']} | {j['title']} | {j['score']}% | {j['url']}" for j in c["jobs"][:15]),
            "Posted in last 24h: " + "; ".join(f"{j['company']} | {j['title']} | {j['score']}%" for j in c["new"]),
            "Upcoming events: " + "; ".join(f"{time.strftime('%a %b %d %H:%M', time.localtime(e['start_ts']))} {e['title']}"
                                             for e in c["events"]),
            f"Resume score: {c['lint']['score'] if c['lint'] else 'n/a'}; top issues: "
            + "; ".join(i["message"] for i in (c["lint"] or {}).get("issues", [])[:5]),
        ])
        if body.get("focus") == "resume":  # asked from Resume Studio's coach box: work on the actual lines
            r = rs.normalize(body.get("resume") or master(pid) or {})
            ctx = ("You are their resume coach inside Resume Studio. When you suggest a rewrite, give the complete new "
                   "line, quoted. " + ai._RESUME_RULES + "\n\nTheir resume:\n"
                   + "\n".join(f"[{p_}] {t}" for p_, t in ai._resume_lines(r)) + "\n\n" + ctx)
        try:
            return {"reply": ai.chat(history, ctx), "by": ai.provider()}
        except Exception as e:
            log.warning("chat failed, using rules: %s", e)
    pasted = _pasted_email(pid, history[-1]["content"])
    if pasted:
        return pasted
    return {"reply": _rules_reply(pid, history[-1]["content"]), "by": "rules"}


def _pasted_email(pid: int, text: str) -> dict | None:
    """Without an AI engine the chat still tracks a pasted recruiting email (the agent does this itself)."""
    from . import agent, mail
    if len(text) < 150 or text.count("\n") < 2:
        return None
    r = mail.read_pasted(text)
    if not (r["status"] and r["company"]):
        return None
    out = agent.call(pid, "log_email", {"text": text}, source="chat")
    if out.get("error"):
        return None
    label = {"oa": "online assessment", "interviewing": "interviewing", "applied": "applied", "offer": "offer",
             "rejected": "rejected", "saved": "saved"}
    reply = f"Tracked: **{out['application']}** → {label.get(out['status'], out['status'])}"
    reply += f" (was {out['was']})." if out["status_changed"] else "."
    reply += "".join(f"\n• {x}" for x in out["next_steps"])
    return {"reply": reply, "by": "rules", "cards": [out.pop("_card")], "undo": [out.pop("_undo")],
            "steps": [{"tool": "log_email", "ok": True, "summary": agent._summary("log_email", out)}]}
