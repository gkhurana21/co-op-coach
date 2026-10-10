"""Links hidden behind words ("GitHub", "LinkedIn", a project name) are read from PDF and DOCX resumes.

Run: .venv/bin/python -m tests.test_resume_links
"""

import io
import os
import tempfile

os.environ.setdefault("COOPCOACH_DATA", tempfile.mkdtemp(prefix="coopcoach-linktest-"))

from coopcoach import render, resume  # noqa: E402
from coopcoach import resume_studio as rs  # noqa: E402

GH, LI, REPO = "https://github.com/alexchen-dev", "https://www.linkedin.com/in/alexchen-dev/", \
    "https://github.com/alexchen-dev/coop-coach"

TEX = r"""\documentclass[letterpaper,11pt]{article}
\usepackage[hidelinks]{hyperref}
\usepackage[margin=0.5in]{geometry}
\begin{document}
\begin{center}
{\Huge Alex Chen} \\ 416-555-0199 $|$ \href{mailto:alex.chen@example.com}{Email} $|$
\href{%s}{LinkedIn} $|$ \href{%s}{GitHub}
\end{center}
\section*{Education}
University of Waterloo \hfill Sep 2024 -- Apr 2029 \\ Bachelor of Computer Science
\section*{Experience}
Software Developer Intern \hfill May 2026 -- Present \\ Royal Bank of Canada \\
- Built a Python service used by 40 analysts
\section*{Projects}
\textbf{\href{%s}{Co-op Coach}} $|$ Python, FastAPI \\
- Built a job tracker that reads 4,000 career sites
\section*{Technical Skills}
Languages: Python, Java, C++
\end{document}
""" % (LI, GH, REPO)

HTML = f"""<html><body style="font-family:sans-serif">
<h1 style="text-align:center">Alex Chen</h1>
<p style="text-align:center">416-555-0199 | <a href="mailto:alex.chen@example.com">Email</a> |
<a href="{LI}">LinkedIn</a> | <a href="{GH}">GitHub</a></p>
<h2>EDUCATION</h2><p>University of Waterloo — Bachelor of Computer Science, Sep 2024 – Apr 2029</p>
<h2>PROJECTS</h2><p><b><a href="{REPO}">Co-op Coach</a></b> | Python, FastAPI</p>
<ul><li>Built a job tracker that reads 4,000 career sites</li></ul>
<h2>EXPERIENCE</h2><p>Software Developer Intern, Royal Bank of Canada, May 2026 – Present</p>
<p style="margin-top:600px">See also <a href="https://example.org/blog">my blog</a></p>
</body></html>"""


def docx_bytes() -> bytes:
    import docx
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = docx.Document()

    def link(par, url, text):
        rid = par.part.relate_to(url, RT.HYPERLINK, is_external=True)
        h = OxmlElement("w:hyperlink")
        h.set(qn("r:id"), rid)
        r, t = OxmlElement("w:r"), OxmlElement("w:t")
        t.text = text
        r.append(t)
        h.append(r)
        par._p.append(h)

    d.add_paragraph("Alex Chen")
    p = d.add_paragraph("416-555-0199 | ")
    link(p, "mailto:alex.chen@example.com", "Email")
    p.add_run(" | ")
    link(p, LI, "LinkedIn")
    p.add_run(" | ")
    link(p, GH, "GitHub")
    d.add_paragraph("EDUCATION")
    d.add_paragraph("University of Waterloo, Bachelor of Computer Science, Sep 2024 – Apr 2029")
    d.add_paragraph("PROJECTS")
    link(d.add_paragraph(), REPO, "Co-op Coach")
    d.add_paragraph("• Built a job tracker that reads 4,000 career sites")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def check_file(label: str, fname: str, data: bytes, checks: dict) -> None:
    text = resume.extract_text(fname, data)
    links = resume.extract_links(fname, data)
    checks[f"{label}: plain text has no URLs (the case this fixes)"] = "github.com" not in text
    urls = [l["url"] for l in links]
    checks[f"{label}: finds GitHub, LinkedIn, repo and email links"] = all(
        u in urls for u in (GH, LI, REPO, "mailto:alex.chen@example.com"))
    prof = resume.merge_file_links(resume.heuristic_parse(text), links)
    checks[f"{label}: profile links are GitHub + LinkedIn, not the project repo"] = (
        "github.com/alexchen-dev" in prof["links"] and "linkedin.com/in/alexchen-dev" in prof["links"]
        and "github.com/alexchen-dev/coop-coach" not in prof["links"])
    checks[f"{label}: email filled from the mailto link"] = prof["email"] == "alex.chen@example.com"
    tips = resume.heuristic_assessment(prof, text)["resume_tips"]
    checks[f"{label}: coach no longer says to add GitHub/LinkedIn"] = not any(
        "github" in t.lower() or "linkedin" in t.lower() for t in tips)
    r = rs.normalize(rs.attach_file_links(rs.build_from_text(text, prof), prof))
    checks[f"{label}: Resume Studio header gets both links"] = {"github.com/alexchen-dev",
                                                                "linkedin.com/in/alexchen-dev"} <= set(r["header"]["links"])
    proj = next((p for p in r["projects"] if "co-op coach" in p["name"].lower()), None)
    checks[f"{label}: project 'Co-op Coach' gets its repo link"] = bool(proj) and proj["link"].endswith("coop-coach")
    if label == "Chrome PDF":
        checks[f"{label}: a link far down the page isn't treated as a header link"] = \
            "example.org/blog" not in prof["links"]


def main():
    checks: dict[str, bool] = {}
    tex_pdf = render.latex_pdf(TEX)
    if tex_pdf:
        check_file("LaTeX PDF", "r.pdf", tex_pdf, checks)
    else:
        print("SKIP LaTeX PDF (tectonic not installed)")
    check_file("Chrome PDF", "r.pdf", render.pdf(HTML), checks)
    check_file("DOCX", "r.docx", docx_bytes(), checks)

    a = {"resume_tips": ["Add a GitHub (or portfolio) link to the header so reviewers can see your code",
                         "Add your LinkedIn URL; recruiters cross-check it", "Pin your best repos with a README",
                         "Quantify the RBC bullet"]}
    resume.drop_stale_link_tips(a, {"links": ["github.com/alexchen-dev", "linkedin.com/in/alexchen-dev"]})
    checks["AI tips about adding links already there are dropped; real tips kept"] = a["resume_tips"] == [
        "Pin your best repos with a README", "Quantify the RBC bullet"]
    checks["describe_links tells the AI what kinds exist, never the addresses"] = (
        lambda d: "GitHub profile" in d and "LinkedIn profile" in d and "alexchen" not in d)(
        resume.describe_links([{"url": GH, "header": True}, {"url": LI, "header": True},
                               {"url": REPO, "header": False}]))
    checks["a broken file just yields no links"] = resume.extract_links("x.pdf", b"not a pdf") == []

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
