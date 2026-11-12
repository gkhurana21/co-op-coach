"""Resume Design editor: the theme keys survive normalize (clamped, validated, unknown ones dropped), become the CSS
variables in resume_html, the old templates still look like themselves, Word follows the theme, saved themes and
Shuffle work over the API, a tailored resume follows the current look, and the editor's JS builds exactly the same
variables as the server.

Run: .venv/bin/python -m tests.test_resume_theme
"""

import io
import json
import os
import re
import shutil
import subprocess
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-theme-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import db, render, routes_docs  # noqa: E402
from coopcoach import resume_studio as rs  # noqa: E402
from coopcoach import resume_theme as theme  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


RESUME = {"header": {"name": "Robin Doe", "email": "robin@example.com", "links": ["github.com/robindoe"]},
          "education": [{"school": "University of Waterloo", "degree": "BASc Computer Engineering", "dates": "2024 - 2029"}],
          "experience": [{"title": "Software Developer Intern", "company": "Acme", "dates": "May 2025 - Aug 2025",
                          "bullets": ["Built REST APIs in Python and FastAPI for the billing team"]}],
          "projects": [{"name": "Trailmix", "tech": "React", "bullets": ["Built a trip planner for hiking routes"]}],
          "skills": [{"label": "Languages", "items": "Python, TypeScript"}]}


def var(html: str, name: str) -> str | None:
    found = re.findall(re.escape(name) + r":\s*([^;]+);", html)  # the last declaration is the one that wins
    return found[-1].strip() if found else None


def normalize_checks():
    st = rs.normalize({})["style"]
    check("every theme key has a default in STYLE_DEFAULTS (normalize would drop it otherwise)",
          all(k in rs.STYLE_DEFAULTS for k in theme.THEME_KEYS) and all(k in st for k in theme.THEME_KEYS))
    mine = {"heading_font": "garamond", "body_font": "calibri", "body_size": 11, "name_size": 20, "line_height": 1.3,
            "margin": 0.7, "section_space": 1.5, "heading_style": "smallcaps", "bullet": "dash", "date_align": "inline",
            "columns": 2, "density": "airy", "header_align": "left", "rule_tint": 0.45, "accent": "#1f3a68"}
    kept = rs.normalize({"style": mine})["style"]
    check("valid theme values are kept as they are", all(kept[k] == v for k, v in mine.items()))
    bad = rs.normalize({"style": {"body_size": 40, "name_size": "huge", "line_height": 0.2, "margin": -1,
                                  "heading_font": "Comic Sans", "bullet": "🦄", "columns": 3, "density": "x",
                                  "rule_tint": 0.5, "heading_style": None, "made_up": 1, "one_page": 0}})["style"]
    check("out-of-range numbers are clamped, junk falls back to the default",
          bad["body_size"] == 12.5 and bad["name_size"] == 23.0 and bad["line_height"] == 1.0 and bad["margin"] == 0.3)
    check("unknown fonts / bullets / columns / density / heading style fall back to the defaults",
          bad["heading_font"] == "cmu" and bad["bullet"] == "disc" and bad["columns"] == 1 and bad["density"] == "normal"
          and bad["heading_style"] == "rule")
    check("a rule tint snaps to the nearest offered tint", bad["rule_tint"] == 0.45)
    check("unknown style keys are dropped and switches are real booleans", "made_up" not in bad and bad["one_page"] is False)
    check("normalize is stable (normalizing twice changes nothing)", rs.normalize(rs.normalize({"style": mine})) ==
          rs.normalize({"style": mine}))


def html_checks():
    base = render.resume_html({**RESUME, "style": {}})
    check("default look: Jake's fonts, 10.4pt body, rule under headings, dates on the right",
          var(base, "--font-b") == theme.FONTS["cmu"]["css"] and var(base, "--base") == "10.4pt"
          and var(base, "--h-rule") == ".8px" and var(base, "--d-just") == "space-between" and var(base, "--bullet") == '"•"')
    st = {"heading_font": "inter", "body_font": "georgia", "body_size": 11, "name_size": 20, "line_height": 1.3,
          "margin": 0.7, "section_space": 1.5, "heading_style": "caps", "bullet": "dash", "date_align": "inline",
          "density": "compact", "header_align": "left", "rule_tint": 0.7, "accent": "#1f3a68", "link_color": "#000000"}
    h = render.resume_html({**RESUME, "style": st})
    check("fonts become --font-h / --font-b", var(h, "--font-h") == theme.FONTS["inter"]["css"]
          and var(h, "--font-b") == theme.FONTS["georgia"]["css"])
    check("sizes and spacing become --base / --name / --t-lh / --t-margin / --t-sec / --t-sp",
          (var(h, "--base"), var(h, "--name"), var(h, "--t-lh"), var(h, "--t-margin"), var(h, "--t-sec"), var(h, "--t-sp"))
          == ("11pt", "20pt", "1.3", "0.7in", "1.5", "0.72"))
    check("caps headings: uppercase, no rule", var(h, "--h-case") == "uppercase" and var(h, "--h-rule") == "0px")
    check("bullet glyph, inline dates and left-aligned name", var(h, "--bullet") == '"–"' and var(h, "--d-just") == "flex-start"
          and var(h, "--d-sep") == '"·"' and var(h, "--h-align") == "left")
    check("colours: accent, links and a tinted heading rule lighter than the accent",
          var(h, "--accent") == "#1f3a68" and var(h, "--link") == "#000000"
          and theme.contrast(var(h, "--rule")) < theme.contrast("#1f3a68"))
    check("one column by default; two columns puts experience/projects left, the rest right",
          "class='cols'" not in base and re.search(r"<div class='c1'><h2>Experience.*<div class='c2'><h2>Education",
                                                  render.resume_html({**RESUME, "style": {"columns": 2}}), re.S))
    check("one-page fitting is still in the page and works on --fit-* ratios",
          "__fitNow" in base and "--fit-fs" in base)
    fixed = render.resume_html({**RESUME, "style": {"one_page": True}}, level=3)
    check("a pinned fit level sets the ratios and drops the fitting script",
          "__fitNow" not in fixed and var(fixed, "--fit-fs") == "0.94")
    check("a resume saved before the editor keeps its template's look (modern = Inter, left name)",
          var(render.resume_html({**RESUME, "style": {"accent": "#000000"}}, "modern"), "--font-b") == theme.FONTS["inter"]["css"])
    check("once it has theme keys, the template no longer overrides them",
          var(render.resume_html({**RESUME, "style": {"body_font": "times"}}, "modern"), "--font-b") == theme.FONTS["times"]["css"])
    check("the cover letter still renders from the template's preset",
          theme.FONTS["times"]["css"] in render.letter_html("Dear team,\n\nHello.", RESUME["header"], "classic"))
    check("colour mixing: no tint is the accent itself, full tint is white",
          theme.mix_white("#1F3A68", 0) == "#1f3a68" and theme.mix_white("#1f3a68", 1) == "#ffffff")
    check("ATS meter: the default look is clean, two columns and a pale accent are flagged",
          theme.ats_report(rs.normalize({})["style"])["score"] == 100
          and len(theme.ats_report(rs.normalize({"style": {"columns": 2, "accent": "#ffcc00"}})["style"])["warnings"]) == 2)


def docx_checks():
    import docx
    st = {"heading_font": "garamond", "body_font": "calibri", "body_size": 11, "margin": 0.7, "heading_style": "smallcaps",
          "accent": "#7a1f2b", "rule_tint": 0.45, "date_align": "inline"}
    d = docx.Document(io.BytesIO(render.resume_docx({**RESUME, "style": st})))
    normal = d.styles["Normal"]
    check("Word: body font and size follow the theme", normal.font.name == "Calibri" and normal.font.size.pt == 11)
    check("Word: margins follow the theme", abs(d.sections[0].left_margin.inches - 0.7) < 0.01)
    head = next(p for p in d.paragraphs if p.text == "Experience")
    run = head.runs[0]
    check("Word: headings in the heading font, small caps, accent colour",
          run.font.name == "Garamond" and run.font.small_caps and str(run.font.color.rgb) == "7A1F2B")
    check("Word: the heading rule uses the tinted accent",
          theme.mix_white("#7a1f2b", 0.45)[1:].upper() in head._p.xml)
    check("Word: inline dates sit after the title", any("  ·  May 2025 - Aug 2025" in p.text for p in d.paragraphs))
    name = d.paragraphs[0].runs[0]
    check("Word: the name uses the heading font and the name size", name.font.name == "Garamond" and name.font.size.pt == 23)


def api_checks(cl: TestClient, pid: int):
    got = cl.get(f"/api/resume?profile_id={pid}").json()
    check("a resume saved before the editor opens with its template's preset (modern)",
          got["resume"]["style"]["body_font"] == "inter" and got["resume"]["style"]["header_align"] == "left")
    check("the editor gets its fonts, presets and options from the server",
          set(got["theme"]["fonts"]) == set(theme.FONTS) and set(got["theme"]["presets"]) == {"signature", "jake", "modern", "classic"}
          and got["themes"] == {})
    r = cl.put("/api/resume/themes", json={"profile_id": pid, "name": "  Banks ", "style": {
        **got["resume"]["style"], "body_font": "times", "body_size": 99, "one_page": False}}).json()
    saved = r["themes"].get("Banks") or {}
    check("save a named theme: only the look is kept, clamped like everything else",
          saved.get("body_font") == "times" and saved.get("body_size") == 12.5 and "one_page" not in saved
          and set(saved) == set(theme.THEME_KEYS))
    check("saved themes come back with the resume", "Banks" in cl.get(f"/api/resume?profile_id={pid}").json()["themes"])
    check("a theme needs a name", cl.put("/api/resume/themes", json={"profile_id": pid, "name": " ", "style": {}}).status_code == 400)
    check("delete a saved theme", cl.delete(f"/api/resume/themes?profile_id={pid}&name=Banks").json()["themes"] == {})
    sh = [cl.get("/api/resume/theme/shuffle").json() for _ in range(12)]
    check("Shuffle gives valid, one-column, readable looks",
          all(rs.normalize({"style": s})["style"] == {**rs.STYLE_DEFAULTS, **rs.normalize({"style": s})["style"]} and
              s["columns"] == 1 and theme.contrast(s["accent"]) >= 4.5 and s["heading_font"] in theme.FONTS for s in sh))
    new = {**got["resume"], "style": {**got["resume"]["style"], "body_font": "palatino", "accent": "#0f6e73"}}
    check("saving the resume keeps the theme",
          cl.put("/api/resume", json={"profile_id": pid, "resume": new}).status_code == 200
          and routes_docs.master(pid)["style"]["body_font"] == "palatino")
    routes_docs._save_doc(pid, "resume", db.dumps({**RESUME, "style": {"body_font": "verdana"}}), "Tailored", job_id="job1")
    t = routes_docs._resume_for(pid, "job1")
    check("a tailored copy follows the current look (content stays the tailored one)",
          t["style"]["body_font"] == "palatino" and t["style"]["accent"] == "#0f6e73" and t["header"]["name"] == "Robin Doe")
    x = cl.get(f"/api/resume/export?profile_id={pid}&fmt=html").text
    check("the exported page carries the saved theme", var(x, "--font-b") == theme.FONTS["palatino"]["css"])
    import docx
    w = docx.Document(io.BytesIO(cl.get(f"/api/resume/export?profile_id={pid}&fmt=docx&level=0").content))
    check("the Word download carries the saved theme", w.styles["Normal"].font.name == "Palatino Linotype")


def js_parity_checks():
    """design.js sets the variables on the live preview itself; they must be exactly the server's."""
    node = shutil.which("node")
    if not node:
        print("SKIP the editor's JS: node isn't installed")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    js = os.path.join(here, "..", "coopcoach", "static", "design.js")
    styles = [rs.normalize({})["style"]] + [rs.normalize({"style": {**theme.shuffle(), "density": d, "date_align": a,
                                                                       "rule_tint": 0, "name_size": 21.5}})["style"]
                                           for d, a in (("compact", "inline"), ("airy", "right"), ("normal", "inline"))]
    schema = routes_docs.theme_schema()
    prog = ("const vm=require('vm'),fs=require('fs');const ctx={};vm.createContext(ctx);"
            "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
            "const a=JSON.parse(fs.readFileSync(0,'utf8'));vm.runInContext('TE.schema='+JSON.stringify(a.schema),ctx);"
            "ctx.__s=a.styles;console.log(JSON.stringify(vm.runInContext('__s.map(teVars)',ctx)));")
    run = subprocess.run([node, "-e", prog, js], input=json.dumps({"schema": schema, "styles": styles}),
                         capture_output=True, text=True, timeout=60)
    out = json.loads(run.stdout or "null") if run.returncode == 0 else None
    check("the editor's live variables match the server's for the same look (no drift between preview and PDF)",
          out is not None and out == [theme.css_vars(s) for s in styles])
    if run.returncode:
        print(run.stderr[-800:])


def main():
    from coopcoach import server
    server._sync_in_background = lambda: None
    db.init()
    normalize_checks()
    html_checks()
    sig = {**theme.PRESETS["signature"]["style"]}
    look = theme.from_ai({"accent": "#008080", "heading_style": "smallcaps"}, sig)
    check("an AI look keeps the Signature layout (the AI doesn't choose layouts)", look["layout"] == "signature" and look["accent"] == "#008080")
    docx_checks()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, resume_text, profile_json, prefs_json, created_at, updated_at) VALUES"
                        " (?,?,?,?,?,?)", ("Robin", "Robin Doe resume", db.dumps({"name": "Robin Doe"}),
                                           db.dumps({"resume_template": "modern"}), now, now)).lastrowid
    routes_docs._save_doc(pid, "resume_master", db.dumps({**RESUME, "style": {"accent": "#000000"}}), "Master resume")
    with TestClient(app, base_url="http://localhost") as cl:
        api_checks(cl, pid)
    js_parity_checks()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
