"""Turn structured resumes and letters into HTML, PDF (via your installed Chrome) and DOCX."""

import html
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import resume_studio as rs

log = logging.getLogger(__name__)
e = html.escape

CSS = {
    "jake": """
      body { font-family: 'CMU Serif', 'Latin Modern Roman', Charter, 'Bitstream Charter', Georgia, serif; --base: 10.4pt; }
      h1 { text-align: center; font-size: calc(23pt * var(--fs)); letter-spacing: .4px; }
      .contact { text-align: center; justify-content: center; }
      h2 { text-transform: uppercase; font-size: calc(11pt * var(--fs)); font-weight: 600; letter-spacing: .6px; }
      .sub { font-style: italic; }
      .proj .tech { font-style: italic; font-weight: 400; }
    """,
    "modern": """
      body { font-family: Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif; --base: 9.8pt; }
      h1 { font-size: calc(22pt * var(--fs)); letter-spacing: -.3px; }
      h2 { text-transform: uppercase; font-size: calc(10pt * var(--fs)); letter-spacing: 1.2px; border-bottom-width: 1.4px; }
      .sub { opacity: .85; }
      .proj .tech { opacity: .85; font-weight: 400; }
    """,
    "classic": """
      body { font-family: 'Times New Roman', Times, 'Liberation Serif', serif; --base: 10.8pt; }
      h1 { text-align: center; font-size: calc(19pt * var(--fs)); text-transform: uppercase; letter-spacing: 2px; }
      .contact { text-align: center; justify-content: center; }
      h2 { text-transform: uppercase; font-size: calc(11pt * var(--fs)); letter-spacing: 1px; }
      .sub { font-style: italic; }
      .proj .tech { font-style: italic; font-weight: 400; }
    """,
}
BASE_CSS = """
  @page { size: Letter; margin: 0; }
  * { box-sizing: border-box; }
  html { --fs: 1; --sp: 1; --lh: 1.22; --mx: 0.55in; --my: 0.45in; }
  body { margin: 0; width: 8.5in; padding: var(--my) var(--mx); color: #000; font-size: calc(var(--base) * var(--fs));
         line-height: var(--lh); -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  h1 { margin: 0; font-weight: 700; color: var(--accent); }
  a { color: var(--link); text-decoration: none; }
  .contact { font-size: .92em; margin: calc(3px * var(--sp)) 0 calc(2px * var(--sp)); display: flex; flex-wrap: wrap;
             column-gap: 1.1em; row-gap: 2px; }
  .contact.plain { display: block; }
  .contact .ci { display: inline-flex; align-items: center; gap: .32em; white-space: nowrap; }
  .contact.plain a { text-decoration: underline; }
  svg.i { width: 1em; height: 1em; flex: none; vertical-align: -0.14em; }
  svg.i.s { width: .78em; height: .78em; margin-left: .25em; opacity: .75; }
  .em { font-size: .95em; }
  h2 { margin: calc(9px * var(--sp)) 0 calc(4px * var(--sp)); padding-bottom: 1px; color: var(--accent);
       border-bottom: .8px solid var(--accent); }
  .entry { margin: 0 0 calc(5px * var(--sp)); break-inside: avoid; }
  .row { display: flex; justify-content: space-between; gap: 14px; align-items: baseline; }
  .row .r { white-space: nowrap; }
  .row.main .l { font-weight: 700; }
  .row.sub { font-size: .95em; }
  .row a { color: var(--link); }
  ul { margin: calc(2px * var(--sp)) 0 0; padding-left: 15px; }
  li { margin: 0 0 calc(1.5px * var(--sp)); font-size: .95em; }
  .skills div { font-size: .95em; margin: calc(1px * var(--sp)) 0; }
  .letter { font-size: 11pt; line-height: 1.45; }
  .letter p { margin: 0 0 11px; }
  .letter .head { margin-bottom: 22px; }
  @media screen { html { background: #fff; } }
"""

# Feather-style line icons (MIT) and the GitHub / LinkedIn marks (Simple Icons, CC0), drawn inline so PDFs need
# no web fonts.
_ICON = {
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "email": '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>',
    "web": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "location": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
    "external": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "demo": '<polygon points="5 3 19 12 5 21 5 3"/>',
}
_BRAND = {
    "github": "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12",
    "linkedin": "M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.71 1.637-1.459 3.37-1.459 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z",
}
_EMOJI = {"phone": "📞", "email": "✉️", "linkedin": "💼", "github": "💻", "gitlab": "💻", "web": "🌐", "location": "📍",
          "external": "🔗", "link": "🔗", "demo": "🚀", "devpost": "🏆", "leetcode": "🧩", "kaggle": "📊",
          "medium": "✍️", "youtube": "🎥", "x": "🐦", "twitter": "🐦", "behance": "🎨", "dribbble": "🎨",
          "scholar": "🎓"}


def _icon(kind: str, mode: str, small: bool = False) -> str:
    if mode == "emoji":
        return f"<span class='em'>{_EMOJI.get(kind, '🔗')}</span>"
    if mode != "icons":
        return ""
    cls = "i s" if small else "i"
    if kind in _BRAND:
        return f"<svg class='{cls}' viewBox='0 0 24 24' fill='currentColor' aria-hidden='true'><path d='{_BRAND[kind]}'/></svg>"
    body = _ICON.get(kind) or _ICON["link"]
    return (f"<svg class='{cls}' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' "
            f"stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>{body}</svg>")


def _href(u: str) -> str:
    return u if re.match(r"^(https?:|mailto:|tel:)", u) else f"https://{u}"


def _link(u: str) -> str:
    return f'<a href="{e(_href(u), quote=True)}">{e(u)}</a>'


def contact_items(h: dict) -> list[tuple[str, str, str]]:
    """(kind, label, href) for everything in the header after the name."""
    out = []
    if h.get("phone"):
        out.append(("phone", h["phone"], "tel:" + re.sub(r"[^\d+]", "", h["phone"])))
    if h.get("email"):
        out.append(("email", h["email"], "mailto:" + h["email"]))
    for l in h.get("links") or []:
        if l:
            label, kind = rs.link_label(l)
            out.append((kind, label, _href(l)))
    if h.get("location"):
        out.append(("location", h["location"], ""))
    return out


def _header(h: dict, style: dict | None = None) -> str:
    st = style or rs.STYLE_DEFAULTS
    mode = st.get("contact", "icons")
    if mode == "plain":
        bits = [e(h["phone"])] if h.get("phone") else []
        if h.get("email"):
            bits.append(f'<a href="mailto:{e(h["email"], quote=True)}">{e(h["email"])}</a>')
        bits += [_link(l) for l in h.get("links", []) if l]
        if h.get("location"):
            bits.append(e(h["location"]))
        return f"<h1>{e(h.get('name', ''))}</h1><div class='contact plain'>{' &nbsp;|&nbsp; '.join(bits)}</div>"
    items = []
    for kind, label, href in contact_items(h):
        inner = f"{_icon(kind, mode)}<span>{e(label)}</span>"
        items.append(f"<a class='ci' href='{e(href, quote=True)}'>{inner}</a>" if href else f"<span class='ci'>{inner}</span>")
    return f"<h1>{e(h.get('name', ''))}</h1><div class='contact'>{''.join(items)}</div>"


def _bullets(items: list[str]) -> str:
    items = [i for i in items if i and i.strip()]
    return "<ul>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>" if items else ""


def _linked(text: str, url: str, st: dict, kind: str = "external") -> str:
    """Company / project name, clickable when it has a link, with an optional small icon after it."""
    if not url:
        return e(text)
    icon = _icon(kind, st["contact"] if st["contact"] != "plain" else "icons", small=True) if st.get("link_icons") else ""
    return f"<a href='{e(_href(url), quote=True)}'>{e(text)}{icon}</a>"


# One-page fitting: each step shrinks type, spacing and margins a little, never below a readable 8.6pt-ish body.
FIT_LEVELS = [  # (font scale, spacing scale, line height, side margin in, top margin in)
    (1.00, 1.00, 1.22, 0.55, 0.45), (0.98, 0.85, 1.20, 0.52, 0.42), (0.96, 0.72, 1.18, 0.50, 0.40),
    (0.94, 0.60, 1.16, 0.48, 0.38), (0.92, 0.50, 1.15, 0.45, 0.36), (0.90, 0.42, 1.13, 0.42, 0.34),
    (0.88, 0.35, 1.12, 0.40, 0.32), (0.86, 0.30, 1.10, 0.38, 0.30), (0.84, 0.25, 1.09, 0.35, 0.28)]
_FIT_JS = """<script>(function(){
var L=%s,H=1056,root=document.documentElement;
function set(i){var l=L[i];root.style.setProperty('--fs',l[0]);root.style.setProperty('--sp',l[1]);
 root.style.setProperty('--lh',l[2]);root.style.setProperty('--mx',l[3]+'in');root.style.setProperty('--my',l[4]+'in');}
window.__fitNow=function(start){var i=start||0;for(;i<L.length;i++){set(i);if(document.body.scrollHeight<=H-2)break;}
 if(i>=L.length)i=L.length-1;set(i);var h=document.body.scrollHeight;
 window.__fit={level:i,fits:h<=H-2,pages:Math.ceil(h/H),over:Math.max(0,h-H),
   pt:Math.round(parseFloat(getComputedStyle(document.body).fontSize)*0.75*10)/10};
 return window.__fit;};
window.__fitNow();if(document.fonts&&document.fonts.ready)document.fonts.ready.then(function(){window.__fitNow();});
})();</script>"""


def resume_html(r: dict, template: str = "jake", level: int | None = None) -> str:
    r = rs.normalize(r)
    st = r["style"]
    parts = []
    for sec in r["section_order"]:
        items = r.get(sec) or []
        if not items:
            continue
        if sec == "education":
            body = "".join(
                f"<div class='entry'><div class='row main'><span class='l'>{e(x['school'])}</span><span class='r'>{e(x['location'])}</span></div>"
                f"<div class='row sub'><span class='l'>{e(x['degree'])}</span><span class='r'>{e(x['dates'])}</span></div>"
                f"{_bullets(x.get('details', []))}</div>" for x in items)
            parts.append(f"<h2>Education</h2>{body}")
        elif sec == "experience":
            rows = []
            for x in items:
                company = _linked(x["company"], x.get("link", ""), st)
                if st.get("company_first"):
                    top, bottom = (company, x["dates"]), (e(x["title"]), x["location"])
                else:
                    top, bottom = (e(x["title"]), x["dates"]), (company, x["location"])
                rows.append(f"<div class='entry'><div class='row main'><span class='l'>{top[0]}</span><span class='r'>{e(top[1])}</span></div>"
                            f"<div class='row sub'><span class='l'>{bottom[0]}</span><span class='r'>{e(bottom[1])}</span></div>"
                            f"{_bullets(x.get('bullets', []))}</div>")
            parts.append(f"<h2>Experience</h2>{''.join(rows)}")
        elif sec == "projects":
            rows = []
            for x in items:
                name = _linked(x["name"], x.get("link", ""), st, "github" if "github.com" in (x.get("link") or "") else "external")
                right = []
                if x.get("demo"):
                    icon = _icon("demo", st["contact"] if st["contact"] != "plain" else "icons", small=True)
                    right.append(f"<a class='lk' href='{e(_href(x['demo']), quote=True)}'>Live Demo{icon}</a>")
                if x.get("dates"):
                    right.append(e(x["dates"]))
                rows.append(f"<div class='entry proj'><div class='row main'><span class='l'>{name}"
                            + (f" <span class='tech'>| {e(x['tech'])}</span>" if x.get("tech") else "")
                            + f"</span><span class='r'>{' &nbsp;·&nbsp; '.join(right)}</span></div>{_bullets(x.get('bullets', []))}</div>")
            parts.append(f"<h2>Projects</h2>{''.join(rows)}")
        elif sec == "skills":
            body = "".join(f"<div><b>{e(s['label'])}:</b> {e(s['items'])}</div>" for s in items if s.get("items"))
            parts.append(f"<h2>Technical Skills</h2><div class='skills'>{body}</div>")
        elif sec == "extras":
            for x in items:
                if x.get("items"):
                    parts.append(f"<h2>{e(x.get('heading') or 'Other')}</h2>{_bullets(x['items'])}")
    css = BASE_CSS + CSS.get(template, CSS["jake"])
    colors = f"html {{ --accent: {st['accent']}; --link: {st['link_color']}; }}"
    lv = FIT_LEVELS[min(max(level or 0, 0), len(FIT_LEVELS) - 1)]
    fixed = (f"html {{ --fs: {lv[0]}; --sp: {lv[1]}; --lh: {lv[2]}; --mx: {lv[3]}in; --my: {lv[4]}in; }}"
             if level is not None else "")
    fit = _FIT_JS % json.dumps(FIT_LEVELS) if st.get("one_page") and level is None else ""
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(r['header'].get('name', 'Resume'))}</title>"
            f"<style>{css}{colors}{fixed}</style></head><body>{_header(r['header'], st)}{''.join(parts)}{fit}</body></html>")


def letter_html(text: str, header: dict, template: str = "jake") -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    body = "".join(f"<p>{'<br>'.join(e(line) for line in p.splitlines())}</p>" for p in paras)
    css = (BASE_CSS + CSS.get(template, CSS["jake"]) + "\n@page { margin: 0.8in 0.9in; }"
           "\nbody { padding: 0; width: auto; } html { --accent: #000; --link: #000; }")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Cover letter</title><style>{css}</style></head>"
            f"<body><div class='head'>{_header(header)}</div><div class='letter'>{body}</div></body></html>")


# --- PDF ------------------------------------------------------------------------------------

def _chrome_binary() -> str | None:
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"):
        if Path(p).exists():
            return p
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def pdf(html_doc: str, info: dict | None = None) -> bytes:
    """Render HTML to a Letter-size PDF with real (selectable, ATS-readable) text. Resumes set to one page are
    re-checked after printing and squeezed one more step if Chrome's print layout still spills over."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome")
            except Exception:
                browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 816, "height": 1056})
            page.set_content(html_doc, wait_until="load")
            fit = page.evaluate("window.__fitNow ? document.fonts.ready.then(() => window.__fitNow()) : null")
            data = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
            while fit and page_count(data) > 1 and fit["level"] < len(FIT_LEVELS) - 1:
                fit = page.evaluate(f"window.__fitNow({fit['level'] + 1})")
                data = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
            browser.close()
            if info is not None and fit:
                info.update(fit, pages=page_count(data))
            return data
    except ImportError:
        pass
    except Exception as ex:
        log.warning("playwright PDF render failed (%s); trying Chrome directly", ex)
    chrome = _chrome_binary()
    if not chrome:
        raise RuntimeError("PDF export needs Google Chrome (or Chromium/Edge/Brave) installed")
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / "doc.html", Path(d) / "doc.pdf"
        src.write_text(html_doc, encoding="utf-8")
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={out}", src.as_uri()], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out.read_bytes()


def page_count(pdf_bytes: bytes) -> int:
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


# --- DOCX -----------------------------------------------------------------------------------

def _docx_base():
    import docx
    from docx.shared import Inches, Pt
    d = docx.Document()
    for s in d.sections:
        s.top_margin = s.bottom_margin = Inches(0.45)
        s.left_margin = s.right_margin = Inches(0.55)
    st = d.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10)
    st.paragraph_format.space_after = Pt(0)
    st.paragraph_format.space_before = Pt(0)
    return d


def _rule_below(paragraph, color: str = "000000") -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    p_pr = paragraph._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", color)):
        bottom.set(qn(k), v)
    bdr.append(bottom)
    p_pr.append(bdr)


def _docx_link(paragraph, url: str, text: str, color: str = "000000", bold=False, italic=False) -> None:
    """A real clickable hyperlink run in Word."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    rid = paragraph.part.relate_to(_href(url), RT.HYPERLINK, is_external=True)
    link, run, props = OxmlElement("w:hyperlink"), OxmlElement("w:r"), OxmlElement("w:rPr")
    link.set(qn("r:id"), rid)
    c = OxmlElement("w:color")
    c.set(qn("w:val"), color)
    props.append(c)
    for flag, tag in ((bold, "w:b"), (italic, "w:i")):
        if flag:
            props.append(OxmlElement(tag))
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(props)
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _two_col(d, left: str, right: str, bold=False, italic=False, link: str = "", color: str = "000000"):
    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.shared import Inches
    p = d.add_paragraph()
    p.paragraph_format.tab_stops.add_tab_stop(Inches(7.4), WD_TAB_ALIGNMENT.RIGHT)
    if link:
        _docx_link(p, link, left, color, bold, italic)
    else:
        a = p.add_run(left)
        a.bold, a.italic = bold, italic
    if right:
        b = p.add_run("\t" + right)
        b.italic = italic
    return p


def _docx_header(d, h: dict, style: dict | None = None):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor
    st = style or rs.STYLE_DEFAULTS
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(h.get("name", ""))
    run.bold, run.font.size = True, Pt(20)
    run.font.color.rgb = RGBColor.from_string(st["accent"][1:].upper())
    c = d.add_paragraph()
    c.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if st["contact"] == "plain":
        c.add_run(" | ".join(x for x in [h.get("phone"), h.get("email"), *h.get("links", []), h.get("location")] if x))
        return
    for n, (kind, label, href) in enumerate(contact_items(h)):
        if n:
            c.add_run("   ")
        if st["contact"] == "emoji":
            c.add_run(_EMOJI.get(kind, "🔗") + " ")
        if href:
            _docx_link(c, href, label, st["link_color"][1:].upper())
        else:
            c.add_run(label)


def resume_docx(r: dict, level: int = 0) -> bytes:
    from docx.shared import Pt, RGBColor
    r = rs.normalize(r)
    st = r["style"]
    lv = FIT_LEVELS[min(max(level, 0), len(FIT_LEVELS) - 1)]
    d = _docx_base()
    d.styles["Normal"].font.size = Pt(round(10 * lv[0] * 2) / 2)
    _docx_header(d, r["header"], st)
    accent, linkc = st["accent"][1:].upper(), st["link_color"][1:].upper()

    def heading(text):
        p = d.add_paragraph()
        p.paragraph_format.space_before = Pt(round(8 * max(lv[1], 0.4)))
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run(text.upper())
        run.bold, run.font.size = True, Pt(11)
        run.font.color.rgb = RGBColor.from_string(accent)
        _rule_below(p, accent)

    def bullets(items):
        for b in items:
            if b and b.strip():
                p = d.add_paragraph(b, style="List Bullet")
                p.paragraph_format.space_after = Pt(1)

    for sec in r["section_order"]:
        items = r.get(sec) or []
        if not items:
            continue
        if sec == "education":
            heading("Education")
            for x in items:
                _two_col(d, x["school"], x["location"], bold=True)
                _two_col(d, x["degree"], x["dates"], italic=True)
                bullets(x.get("details", []))
        elif sec == "experience":
            heading("Experience")
            for x in items:
                if st.get("company_first"):
                    _two_col(d, x["company"], x["dates"], bold=True, link=x.get("link", ""), color=linkc)
                    _two_col(d, x["title"], x["location"], italic=True)
                else:
                    _two_col(d, x["title"], x["dates"], bold=True)
                    _two_col(d, x["company"], x["location"], italic=True, link=x.get("link", ""), color=linkc)
                bullets(x.get("bullets", []))
        elif sec == "projects":
            heading("Projects")
            for x in items:
                p = _two_col(d, x["name"], "", bold=True, link=x.get("link", ""), color=linkc)
                if x.get("tech"):
                    p.add_run(f" | {x['tech']}").italic = True
                if x.get("demo") or x.get("dates"):
                    p.add_run("\t")
                    if x.get("demo"):
                        _docx_link(p, x["demo"], "Live Demo", linkc)
                    if x.get("dates"):
                        p.add_run(("  ·  " if x.get("demo") else "") + x["dates"])
                bullets(x.get("bullets", []))
        elif sec == "skills":
            heading("Technical Skills")
            for s in items:
                p = d.add_paragraph()
                p.add_run(f"{s['label']}: ").bold = True
                p.add_run(s["items"])
        elif sec == "extras":
            for x in items:
                heading(x.get("heading") or "Other")
                bullets(x.get("items", []))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def letter_docx(text: str, header: dict) -> bytes:
    from docx.shared import Inches, Pt
    d = _docx_base()
    for s in d.sections:
        s.top_margin = s.bottom_margin = Inches(0.8)
        s.left_margin = s.right_margin = Inches(0.9)
    d.styles["Normal"].font.size = Pt(11)
    _docx_header(d, header, {**rs.STYLE_DEFAULTS, "contact": "plain"})
    d.add_paragraph()
    for para in re.split(r"\n\s*\n", text.strip()):
        p = d.add_paragraph(para.strip())
        p.paragraph_format.space_after = Pt(10)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# --- application package --------------------------------------------------------------------

def safe_name(*parts: str) -> str:
    return "_".join(re.sub(r"[^\w]+", "-", p).strip("-") for p in parts if p)[:90]


def package_zip(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


# --- Jake's Resume (LaTeX) ------------------------------------------------------------------
# Template by Jake Gutierrez (MIT License, github.com/jakegut/resume), based on sb2nov/resume.
# Built deterministically from the structured resume (no AI writing LaTeX), so it always compiles
# and never changes your wording.

_JAKE_PREAMBLE = r"""%%-------------------------
%% Resume in LaTeX, generated by Co-op Coach
%% Template: Jake's Resume by Jake Gutierrez (MIT License), based on sb2nov/resume
%%-------------------------
\documentclass[letterpaper,%(size)s]{article}

\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[dvipsnames]{xcolor}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage{fontawesome}  %% FontAwesome icons (v4 package: works with pdfLaTeX, XeLaTeX and Tectonic)
\usepackage{hyperref}
\definecolor{accent}{HTML}{%(accent)s}
\definecolor{linkc}{HTML}{%(link)s}
\hypersetup{%(links)s}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\ifdefined\pdfgentounicode
  \input{glyphtounicode}
  \pdfgentounicode=1 %% machine-readable PDF for ATS parsers (pdfLaTeX)
\fi

\pagestyle{fancy}
\fancyhf{}
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

\addtolength{\oddsidemargin}{-%(side)sin}
\addtolength{\evensidemargin}{-%(side)sin}
\addtolength{\textwidth}{%(width)sin}
\addtolength{\topmargin}{-%(top)sin}
\addtolength{\textheight}{%(height)sin}

\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}

\titleformat{\section}{
  \vspace{%(secsp)spt}\scshape\raggedright\large\color{accent}
}{}{0em}{}[\color{accent}\titlerule \vspace{-5pt}]

\newcommand{\resumeItem}[1]{
  \item\small{
    {#1 \vspace{%(itemsp)spt}}
  }
}
\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & #2 \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-7pt}
}
\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{@{}p{0.80\textwidth}@{\extracolsep{\fill}}r@{}}
      \small#1 & \small#2 \\
    \end{tabular*}\vspace{-7pt}
}
\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}

\begin{document}
"""

_TEX_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
                "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
                "<": r"\textless{}", ">": r"\textgreater{}", "|": r"\textbar{}"}
_TEX_UNICODE = {"–": "--", "—": "---", "‘": "`", "’": "'", "“": "``", "”": "''", "…": "...", "•": "", "·": "|",
                "→": "->", "×": "x", "≥": ">=", "≤": "<=", " ": " "}


def tex(s: str) -> str:
    s = "".join(_TEX_UNICODE.get(ch, ch) for ch in (s or ""))
    s = "".join(_TEX_ESCAPES.get(ch, ch) for ch in s)
    # keep Latin-1 accents (é, ü...), drop emoji and other symbols pdfLaTeX can't typeset
    return "".join(ch for ch in s if ord(ch) < 0x250)


def _tex_url(u: str) -> str:
    return _href(u).replace("\\", "").replace("%", r"\%").replace("#", r"\#").replace("~", r"\~{}")


def _tex_link(u: str) -> str:
    return r"\href{%s}{\underline{%s}}" % (_tex_url(u), tex(u))


_FA = {"phone": r"\faPhone", "email": r"\faEnvelope", "linkedin": r"\faLinkedin", "github": r"\faGithub",
       "gitlab": r"\faGitlab", "web": r"\faGlobe", "location": r"\faMapMarker", "external": r"\faExternalLink",
       "demo": r"\faPlayCircle", "kaggle": r"\faLink", "medium": r"\faMedium", "youtube": r"\faYoutube",
       "x": r"\faTwitter", "twitter": r"\faTwitter", "behance": r"\faBehance", "dribbble": r"\faDribbble",
       "devpost": r"\faCode", "leetcode": r"\faCode", "scholar": r"\faGraduationCap"}
# (class font size, extra side margin, extra top margin, space after bullets, space above sections), Jake's = level 0
TEX_LEVELS = [("11pt", 0.0, 0.0, -2, -4), ("11pt", 0.05, 0.05, -3, -6), ("10pt", 0.05, 0.05, -2, -5),
              ("10pt", 0.1, 0.1, -3, -7), ("10pt", 0.15, 0.12, -4, -8)]


def resume_latex(r: dict, level: int = 0) -> str:
    r = rs.normalize(r)
    st, h = r["style"], r["header"]
    size, side, top, itemsp, secsp = TEX_LEVELS[min(max(level, 0), len(TEX_LEVELS) - 1)]
    black = st["link_color"].lower() == "#000000"
    pre = _JAKE_PREAMBLE % {
        "size": size, "accent": st["accent"][1:].upper(), "link": st["link_color"][1:].upper(),
        "links": "hidelinks" if black else "colorlinks=true, urlcolor=linkc, linkcolor=linkc",
        "side": f"{0.5 + side:.2f}", "width": f"{1 + 2 * side:.2f}", "top": f"{0.5 + top:.2f}",
        "height": f"{1 + 2 * top:.2f}", "itemsp": itemsp, "secsp": secsp}
    if st["contact"] == "plain":
        contact = [tex(h["phone"])] if h.get("phone") else []
        if h.get("email"):
            contact.append(r"\href{mailto:%s}{\underline{%s}}" % (h["email"], tex(h["email"])))
        contact += [_tex_link(l) for l in h.get("links", []) if l]
        if h.get("location"):
            contact.append(tex(h["location"]))
        contact_line = r"    \small " + r" $|$ ".join(contact)
    else:  # icon + label, like most polished LaTeX resumes (emoji style uses the same icons: LaTeX can't print emoji)
        bits = []
        for kind, label, href in contact_items(h):
            inner = r"%s\ %s" % (_FA.get(kind, r"\faLink"), tex(label))
            bits.append(r"\href{%s}{%s}" % (_tex_url(href), inner) if href else inner)
        contact_line = r"    \small " + r" \quad ".join(bits)

    def linked(text: str, url: str, bold: bool = False) -> str:
        t = r"\textbf{%s}" % tex(text) if bold else tex(text)
        if not url:
            return t
        icon = r"\,{\scriptsize\faExternalLink}" if st.get("link_icons") else ""
        return r"\href{%s}{%s%s}" % (_tex_url(url), t, icon)

    out = [pre, r"\begin{center}", r"    {\color{accent}\textbf{\Huge \scshape %s}} \\ \vspace{1pt}" % tex(h.get("name", "")),
           contact_line, r"\end{center}", ""]

    def items(lines: list[str]) -> list[str]:
        lines = [l for l in lines if l and l.strip()]
        if not lines:
            return []
        return [r"      \resumeItemListStart"] + [r"        \resumeItem{%s}" % tex(l) for l in lines] + [r"      \resumeItemListEnd"]

    for sec in r["section_order"]:
        rows = r.get(sec) or []
        if not rows:
            continue
        if sec == "education":
            out += [r"\section{Education}", r"  \resumeSubHeadingListStart"]
            for x in rows:
                out.append(r"    \resumeSubheading{%s}{%s}{%s}{%s}" % (tex(x["school"]), tex(x["location"]), tex(x["degree"]), tex(x["dates"])))
                out += items(x.get("details", []))
            out.append(r"  \resumeSubHeadingListEnd")
        elif sec == "experience":
            out += [r"\section{Experience}", r"  \resumeSubHeadingListStart"]
            for x in rows:
                company = linked(x["company"], x.get("link", ""))
                if st.get("company_first"):
                    out.append(r"    \resumeSubheading{%s}{%s}{%s}{%s}" % (company, tex(x["dates"]), tex(x["title"]), tex(x["location"])))
                else:
                    out.append(r"    \resumeSubheading{%s}{%s}{%s}{%s}" % (tex(x["title"]), tex(x["dates"]), company, tex(x["location"])))
                out += items(x.get("bullets", []))
            out.append(r"  \resumeSubHeadingListEnd")
        elif sec == "projects":
            out += [r"\section{Projects}", r"    \resumeSubHeadingListStart"]
            for x in rows:
                head = linked(x["name"], x.get("link", ""), bold=True) + (r" $|$ \emph{%s}" % tex(x["tech"]) if x.get("tech") else "")
                right = []
                if x.get("demo"):
                    right.append(r"\href{%s}{Live Demo%s}" % (_tex_url(x["demo"]), r"\,{\scriptsize\faExternalLink}" if st.get("link_icons") else ""))
                if x.get("dates"):
                    right.append(tex(x["dates"]))
                out.append(r"      \resumeProjectHeading{%s}{%s}" % (head, r" $\cdot$ ".join(right)))
                out += items(x.get("bullets", []))
            out.append(r"    \resumeSubHeadingListEnd")
        elif sec == "skills":
            lines = [r"     \textbf{%s}{: %s}" % (tex(s_["label"]), tex(s_["items"])) for s_ in rows if s_.get("items")]
            if lines:
                out += [r"\section{Technical Skills}", r" \begin{itemize}[leftmargin=0.15in, label={}]",
                        r"    \small{\item{", " \\\\\n".join(lines), r"    }}", r" \end{itemize}"]
        elif sec == "extras":
            for x in rows:
                if x.get("items"):
                    out += [r"\section{%s}" % tex(x.get("heading") or "Other"), r"  \resumeSubHeadingListStart",
                            r"    \item", *items(x["items"]), r"  \resumeSubHeadingListEnd"]
        out.append("")
    out.append(r"\end{document}")
    return "\n".join(out) + "\n"


def latex_pdf_fit(r: dict) -> tuple[bytes | None, int]:
    """Compile, stepping through TEX_LEVELS until it fits on one page (when the resume asks for that)."""
    one_page = rs.normalize(r)["style"].get("one_page")
    data, level = None, 0
    for level in range(len(TEX_LEVELS) if one_page else 1):
        data = latex_pdf(resume_latex(r, level))
        if data is None or page_count(data) <= 1:
            break
    return data, level


def latex_pdf(tex_source: str) -> bytes | None:
    """Compile with Tectonic if it's installed (exact Jake's look). Returns None when no compiler is available."""
    exe = shutil.which("tectonic")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "resume.tex"
        src.write_text(tex_source, encoding="utf-8")
        res = subprocess.run([exe, "--chatter", "minimal", "--outdir", d, str(src)], capture_output=True, timeout=180)
        pdf_path = Path(d) / "resume.pdf"
        if res.returncode != 0 or not pdf_path.exists():
            raise RuntimeError("LaTeX compile failed: " + res.stderr.decode(errors="ignore")[-600:])
        return pdf_path.read_bytes()
