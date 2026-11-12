"""Turn structured resumes and letters into HTML, PDF (via your installed Chrome) and DOCX."""

import base64
import concurrent.futures
import hashlib
import html
import io
import json
import logging
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path

from . import resume_studio as rs
from . import resume_theme as theme

log = logging.getLogger(__name__)
e = html.escape

# Every look setting is a CSS variable (see resume_theme.css_vars); the Design editor changes the same variables on its
# live preview. One-page fitting multiplies on top of them through the --fit-* ratios, so it shrinks from *your* sizes.
BASE_CSS = """
  @page { size: Letter; margin: 0; }
  * { box-sizing: border-box; }
  html { --fit-fs: 1; --fit-sp: 1; --fit-lh: 1; --fit-m: 1; }
  body { --fs: var(--fit-fs); --sp: calc(var(--t-sp) * var(--fit-sp)); --lh: calc(var(--t-lh) * var(--fit-lh));
         --mx: calc(var(--t-margin) * var(--fit-m)); --my: calc(var(--t-margin) * .82 * var(--fit-m));
         margin: 0; width: 8.5in; padding: var(--my) var(--mx); color: #000; font-family: var(--font-b);
         font-size: calc(var(--base) * var(--fs)); line-height: var(--lh);
         -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  h1 { margin: 0; font-weight: 700; color: var(--accent); font-family: var(--font-h); text-align: var(--h-align);
       font-size: calc(var(--name) * var(--fs)); letter-spacing: .2px; line-height: 1.15; }
  a { color: var(--link); text-decoration: none; }
  .contact { font-size: .92em; margin: calc(3px * var(--sp)) 0 calc(2px * var(--sp)); display: flex; flex-wrap: wrap;
             column-gap: 1.1em; row-gap: 2px; justify-content: var(--h-align); text-align: var(--h-align); }
  .contact.plain { display: block; }
  .contact .ci { display: inline-flex; align-items: center; gap: .32em; white-space: nowrap; }
  .contact.plain a { text-decoration: underline; }
  svg.i { width: 1em; height: 1em; flex: none; vertical-align: -0.14em; }
  svg.i.s { width: .78em; height: .78em; margin-left: .25em; opacity: .75; }
  .em { font-size: .95em; }
  h2 { margin: calc(9px * var(--sp) * var(--t-sec)) 0 calc(4px * var(--sp)); padding-bottom: 1px; color: var(--accent);
       font-family: var(--font-h); font-size: calc(var(--base) * 1.06 * var(--fs)); font-weight: 650;
       text-transform: var(--h-case); font-variant: var(--h-variant); letter-spacing: var(--h-ls);
       border-bottom: var(--h-rule) solid var(--rule); }
  .entry { margin: 0 0 calc(5px * var(--sp)); break-inside: avoid; }
  .row { display: flex; justify-content: var(--d-just); gap: var(--d-gap); align-items: baseline; }
  .row .r { white-space: nowrap; }
  .row .r:not(:empty)::before { content: var(--d-sep); margin-right: .4em; }
  .row.main .l { font-weight: 700; }
  .row.sub { font-size: .95em; font-style: var(--sub-style); opacity: var(--sub-op); }
  .proj .tech { font-style: var(--sub-style); opacity: var(--sub-op); font-weight: 400; }
  .row a { color: var(--link); }
  ul { margin: calc(2px * var(--sp)) 0 0; padding-left: 15px; }
  li { margin: 0 0 calc(1.5px * var(--sp)); font-size: .95em; }
  li::marker { content: var(--bullet) "  "; }
  .skills div { font-size: .95em; margin: calc(1px * var(--sp)) 0; }
  .cols { display: grid; grid-template-columns: minmax(0, 1fr) 31%; column-gap: .3in; }
  .cols > div > h2:first-child { margin-top: calc(6px * var(--sp)); }
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
function set(i){var l=L[i];root.style.setProperty('--fit-fs',l[0]);root.style.setProperty('--fit-sp',l[1]);
 root.style.setProperty('--fit-lh',l[2]/L[0][2]);root.style.setProperty('--fit-m',l[3]/L[0][3]);}
window.__fitNow=function(start){var i=start||0;for(;i<L.length;i++){set(i);if(document.body.scrollHeight<=H-2)break;}
 if(i>=L.length)i=L.length-1;set(i);var h=document.body.scrollHeight;
 window.__fit={level:i,fits:h<=H-2,pages:Math.ceil(h/H),over:Math.max(0,h-H),
   pt:Math.round(parseFloat(getComputedStyle(document.body).fontSize)*0.75*10)/10};
 return window.__fit;};
window.__fitNow();if(document.fonts&&document.fonts.ready)document.fonts.ready.then(function(){window.__fitNow();});
})();</script>"""


def fit_css(level: int) -> str:
    """Pin one fitting level (as ratios of the first, which is 'no squeezing')."""
    lv, l0 = FIT_LEVELS[min(max(level, 0), len(FIT_LEVELS) - 1)], FIT_LEVELS[0]
    return (f"html {{ --fit-fs: {lv[0]}; --fit-sp: {lv[1]}; --fit-lh: {round(lv[2] / l0[2], 4)}; "
            f"--fit-m: {round(lv[3] / l0[3], 4)}; }}")


_FONT_DIR = Path(__file__).parent / "static" / "fonts"


def _source_sans() -> str:
    """Source Sans 3 (OFL), inside the page, so a PDF prints the same on any computer and needs no network."""
    out = ""
    for name, style in (("source-sans-3.woff2", "normal"), ("source-sans-3-italic.woff2", "italic")):
        data = base64.b64encode((_FONT_DIR / name).read_bytes()).decode()
        out += f"@font-face{{font-family:'Source Sans 3';font-style:{style};font-weight:400 700;src:url(data:font/woff2;base64,{data}) format('woff2');}}"
    return out


SIGNATURE_CSS = """
  h1 { text-transform: uppercase; letter-spacing: .01em; }
  .tagline { text-align: center; text-transform: uppercase; font-weight: 600; color: #7b8590; font-size: 1.02em; letter-spacing: .015em; margin-top: 1px; }
  h2 { font-weight: 700; }
  .row.sub .l { font-style: italic; opacity: 1; }
  .row.sub { opacity: 1; font-size: 1em; }
  li { color: #4a5159; text-align: justify; font-size: 1em; }
  .r .sep { margin: 0 .45em; }
  .proj .r { font-style: italic; font-weight: 400; }
  .honors { margin-top: 2px; }
  .skills div { font-size: 1em; }
"""


def _tagline(r: dict) -> str:
    """"Computer Science at the University of Waterloo", from the first school on the resume."""
    edu = (r.get("education") or [{}])[0]
    field = re.sub(r"^(bachelor|master|doctor)s?('s)?( of| in)?( science| arts| applied science| engineering)?( in| of)?\s*", "", (edu.get("degree") or "").split(",")[0], flags=re.I).strip()
    field = field or (edu.get("degree") or "").split(",")[0].strip()
    school = (edu.get("school") or "").strip()
    if not field or not school:
        return ""
    return f"{field} at {'the ' if school.lower().startswith('university') else ''}{school}"


def _pipe(a: str, b: str) -> str:
    return "<span class='sep'>|</span>".join(e(x) for x in (a, b) if x)


def _signature_body(r: dict, st: dict) -> str:
    order = ["skills", "education", "experience", "projects", "extras"]
    extras = r.get("extras") or []
    honors = [x for x in extras if re.search(r"award|honou?r", x.get("heading") or "", re.I)]
    parts = []
    for sec in order + [s for s in r["section_order"] if s not in order]:
        items = r.get(sec) or []
        if not items:
            continue
        if sec == "skills":
            parts.append("<h2>Technical Skills</h2><div class='skills'>" + "".join(f"<div><b>{e(s['label'])}:</b> {e(s['items'])}</div>" for s in items if s.get("items")) + "</div>")
        elif sec == "education":
            rows = []
            for i, x in enumerate(items):
                extra = ""
                if i == len(items) - 1 and honors:
                    lines = [y.strip() for h in honors for it in h["items"] for y in it.split(";") if y.strip()]
                    extra = "<div class='honors'><b>Honors &amp; Awards:</b>" + "".join(f"<div>{e(y)}</div>" for y in lines) + "</div>"
                rows.append(f"<div class='entry'><div class='row main'><span class='l'>{e(x['school'])}</span><span class='r'>{_pipe(x['dates'], x['location'])}</span></div>"
                            f"<div class='row sub'><span class='l'>{e(x['degree'])}</span></div>{_bullets(x.get('details', []))}{extra}</div>")
            parts.append("<h2>Education</h2>" + "".join(rows))
        elif sec == "experience":
            parts.append("<h2>Professional Experience</h2>" + "".join(
                f"<div class='entry'><div class='row main'><span class='l'>{_linked(x['company'], x.get('link', ''), st)}</span><span class='r'>{_pipe(x['dates'], x['location'])}</span></div>"
                f"<div class='row sub'><span class='l'>{e(x['title'])}</span></div>{_bullets(x.get('bullets', []))}</div>" for x in items))
        elif sec == "projects":
            parts.append("<h2>Projects</h2>" + "".join(
                f"<div class='entry proj'><div class='row main'><span class='l'>{_linked(x['name'], x.get('link', ''), st)}</span><span class='r'>{e(x.get('tech') or x.get('dates') or '')}</span></div>{_bullets(x.get('bullets', []))}</div>" for x in items))
        elif sec == "extras":
            for x in items:
                if x not in honors and x.get("items"):
                    parts.append(f"<h2>{e(x.get('heading') or 'Other')}</h2>{_bullets(x['items'])}")
    return "".join(parts)


def resume_html(r: dict, template: str = "jake", level: int | None = None) -> str:
    """template only matters for a resume saved before the Design editor (no theme keys yet): it picks the preset."""
    if isinstance(r, dict):  # anything else is refused by normalize, as before
        r = {**r, "style": theme.with_preset(r.get("style") if isinstance(r.get("style"), dict) else {}, template)}
    r = rs.normalize(r)
    st = r["style"]
    if st.get("layout") == "signature":
        tag = _tagline(r)
        head = _header(r["header"], st).replace("</h1>", f"</h1><div class='tagline'>{e(tag)}</div>" if tag else "</h1>", 1)
        fixed = fit_css(level) if level is not None else ""
        fit = _FIT_JS % json.dumps(FIT_LEVELS) if st.get("one_page") and level is None else ""
        fonts = _source_sans() if "source" in (st["heading_font"], st["body_font"]) else ""
        return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(r['header'].get('name', 'Resume'))}</title>"
                f"<style>{fonts}{BASE_CSS}{theme.theme_css(st)}{SIGNATURE_CSS}{fixed}</style></head>"
                f"<body>{head}{_signature_body(r, st)}{fit}</body></html>")
    parts, side = [], []
    two = st["columns"] == 2
    for sec in r["section_order"]:
        items = r.get(sec) or []
        if not items:
            continue
        if sec == "education":
            body = "".join(
                f"<div class='entry'><div class='row main'><span class='l'>{e(x['school'])}</span><span class='r'>{e(x['location'])}</span></div>"
                f"<div class='row sub'><span class='l'>{e(x['degree'])}</span><span class='r'>{e(x['dates'])}</span></div>"
                f"{_bullets(x.get('details', []))}</div>" for x in items)
            (side if two else parts).append(f"<h2>Education</h2>{body}")
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
            (side if two else parts).append(f"<h2>Technical Skills</h2><div class='skills'>{body}</div>")
        elif sec == "extras":
            for x in items:
                if x.get("items"):
                    (side if two else parts).append(f"<h2>{e(x.get('heading') or 'Other')}</h2>{_bullets(x['items'])}")
    if two and parts and side:  # experience & projects on the left, the rest in a narrow column on the right
        body = f"<div class='cols'><div class='c1'>{''.join(parts)}</div><div class='c2'>{''.join(side)}</div></div>"
    else:
        body = "".join(parts + side)
    fixed = fit_css(level) if level is not None else ""
    fit = _FIT_JS % json.dumps(FIT_LEVELS) if st.get("one_page") and level is None else ""
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(r['header'].get('name', 'Resume'))}</title>"
            f"<style>{BASE_CSS}{theme.theme_css(st)}{fixed}</style></head>"
            f"<body>{_header(r['header'], st)}{body}{fit}</body></html>")


def letter_html(text: str, header: dict, template: str = "jake") -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    body = "".join(f"<p>{'<br>'.join(e(line) for line in p.splitlines())}</p>" for p in paras)
    st = rs.normalize({"style": {**theme.PRESETS.get(template, theme.PRESETS["jake"])["style"],
                                 "accent": "#000000", "link_color": "#000000"}})["style"]
    css = (BASE_CSS + theme.theme_css(st) + "\n@page { margin: 0.8in 0.9in; }\nbody { padding: 0; width: auto; }")
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
    import os
    for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):  # Windows
        for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            if base and Path(base, rel).exists():
                return str(Path(base, rel))
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "chrome", "msedge"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def _launch_browser(p):
    for channel in ("chrome", "msedge", None):  # Edge comes with every Windows 11 laptop
        try:
            return p.chromium.launch(channel=channel) if channel else p.chromium.launch()
        except Exception:
            continue
    raise RuntimeError("no Chrome or Edge found")


def _print(browser, html_doc: str, info: dict | None) -> bytes:
    page = browser.new_page(viewport={"width": 816, "height": 1056})
    try:
        page.set_content(html_doc, wait_until="load")
        fit = page.evaluate("window.__fitNow ? document.fonts.ready.then(() => window.__fitNow()) : null")
        data = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
        while fit and page_count(data) > 1 and fit["level"] < len(FIT_LEVELS) - 1:
            fit = page.evaluate(f"window.__fitNow({fit['level'] + 1})")
            data = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
        if info is not None and fit:
            info.update(fit, pages=page_count(data))
        return data
    finally:
        page.close()


class _Printer:
    """One headless Chrome kept open while there's printing to do: a run of 20 applications prints 40 PDFs, and
    starting Chrome for each one cost more than the printing. It lives on its own thread (Playwright's sync API stays
    on one thread) and closes after a minute and a half with nothing to print."""
    IDLE = 90

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.accepting = False

    def _start(self) -> None:  # (with the lock held)
        if not (self.accepting and self.thread and self.thread.is_alive()):
            self.accepting = True
            self.thread = threading.Thread(target=self._loop, name="pdf-printer", daemon=True)
            self.thread.start()

    def warm(self) -> None:
        """Start Chrome now, so the first PDF doesn't wait for it."""
        with self.lock:
            self._start()

    def print(self, html_doc: str, info: dict | None) -> bytes:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        with self.lock:
            self._start()
            self.q.put((html_doc, info, fut))
        return fut.result(timeout=180)

    def _loop(self) -> None:
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as p:
                browser = _launch_browser(p)
                while True:
                    try:
                        html_doc, info, fut = self.q.get(timeout=self.IDLE)
                    except queue.Empty:
                        with self.lock:
                            if self.q.empty():
                                self.accepting = False
                                break
                        continue
                    try:
                        fut.set_result(_print(browser, html_doc, info))
                    except Exception as e:
                        fut.set_exception(e)
                browser.close()
        except Exception as e:  # Chrome wouldn't start: everyone waiting hears so (and falls back)
            with self.lock:
                self.accepting = False
                while not self.q.empty():
                    self.q.get_nowait()[2].set_exception(e)


_printer = _Printer()


_pdf_cache: OrderedDict[str, tuple[bytes, dict]] = OrderedDict()  # the same page prints the same: don't print it twice
_pdf_lock = threading.Lock()


def warm_printer() -> None:
    try:
        import playwright  # noqa: F401
        _printer.warm()
    except ImportError:
        pass


def pdf(html_doc: str, info: dict | None = None) -> bytes:
    """A Letter-size PDF of this HTML, printed once and remembered (a download and a Word file's one-page check ask for
    the same page)."""
    key = hashlib.sha256(html_doc.encode("utf-8", "replace")).hexdigest()
    with _pdf_lock:
        if key in _pdf_cache:
            _pdf_cache.move_to_end(key)
            data, seen = _pdf_cache[key]
            if info is not None:
                info.update(seen)
            return data
    got: dict = {}
    data = _print_pdf(html_doc, got)
    if info is not None:
        info.update(got)
    with _pdf_lock:
        _pdf_cache[key] = (data, got)
        while len(_pdf_cache) > 40:
            _pdf_cache.popitem(last=False)
    return data


def _print_pdf(html_doc: str, info: dict | None = None) -> bytes:
    """Render HTML to a Letter-size PDF with real (selectable, ATS-readable) text. Resumes set to one page are
    re-checked after printing and squeezed one more step if Chrome's print layout still spills over."""
    try:
        import playwright  # noqa: F401
        return _printer.print(html_doc, info)
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


def _two_col(d, left: str, right: str, bold=False, italic=False, link: str = "", color: str = "000000",
             inline: bool = False):
    """left text, then right text on a right-aligned tab stop at the margin (or after a dot when inline)."""
    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.shared import Emu
    p = d.add_paragraph()
    sec = d.sections[0]
    p.paragraph_format.tab_stops.add_tab_stop(Emu(sec.page_width - sec.left_margin - sec.right_margin),
                                              WD_TAB_ALIGNMENT.RIGHT)
    if link:
        _docx_link(p, link, left, color, bold, italic)
    else:
        a = p.add_run(left)
        a.bold, a.italic = bold, italic
    if right:
        b = p.add_run(("  ·  " if inline else "\t") + right)
        b.italic = italic
    return p


def _docx_header(d, h: dict, style: dict | None = None, scale: float = 1.0):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor
    st = style or rs.STYLE_DEFAULTS
    align = WD_ALIGN_PARAGRAPH.LEFT if st.get("header_align") == "left" else WD_ALIGN_PARAGRAPH.CENTER
    p = d.add_paragraph()
    p.alignment = align
    run = p.add_run(h.get("name", ""))
    run.bold, run.font.size = True, Pt(round(float(st.get("name_size") or 20) * scale * 2) / 2)
    run.font.color.rgb = RGBColor.from_string(st["accent"][1:].upper())
    if st.get("heading_font"):
        run.font.name = theme.FONTS[st["heading_font"]]["docx"]
    c = d.add_paragraph()
    c.alignment = align
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


def resume_docx(r: dict, level: int = 0, template: str = "jake") -> bytes:
    """Word keeps one column and Word's own bullets; fonts, colours, sizes, margins, spacing and headings follow
    the theme."""
    from docx.shared import Inches, Pt, RGBColor
    if isinstance(r, dict):  # anything else is refused by normalize, as before
        r = {**r, "style": theme.with_preset(r.get("style") if isinstance(r.get("style"), dict) else {}, template)}
    r = rs.normalize(r)
    st = r["style"]
    lv = FIT_LEVELS[min(max(level, 0), len(FIT_LEVELS) - 1)]
    l0 = FIT_LEVELS[0]
    d = _docx_base()
    for s in d.sections:  # your margins, squeezed by the same ratio as the PDF
        s.left_margin = s.right_margin = Inches(st["margin"] * lv[3] / l0[3])
        s.top_margin = s.bottom_margin = Inches(st["margin"] * 0.82 * lv[3] / l0[3])
    normal = d.styles["Normal"]
    normal.font.size = Pt(round(st["body_size"] * lv[0] * 2) / 2)
    normal.font.name = theme.FONTS[st["body_font"]]["docx"]
    normal.paragraph_format.line_spacing = round(st["line_height"] * lv[2] / l0[2] / 1.15, 2)  # Word's 1.0 ≈ CSS 1.15
    _docx_header(d, r["header"], st, lv[0])
    accent, linkc = st["accent"][1:].upper(), st["link_color"][1:].upper()
    rule = theme.mix_white(st["accent"], st["rule_tint"])[1:].upper()
    hs, gap = st["heading_style"], theme.DENSITY[st["density"]] * lv[1]
    inline = st["date_align"] == "inline"

    def heading(text):
        p = d.add_paragraph()
        p.paragraph_format.space_before = Pt(round(8 * max(gap, 0.4) * st["section_space"], 1))
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run(text.upper() if hs in ("rule", "caps") else text)
        run.bold, run.font.size = True, Pt(round(st["body_size"] * 1.06 * lv[0] * 2) / 2)
        run.font.small_caps = hs == "smallcaps"
        run.font.name = theme.FONTS[st["heading_font"]]["docx"]
        run.font.color.rgb = RGBColor.from_string(accent)
        if hs in ("rule", "smallcaps"):
            _rule_below(p, rule)

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
                _two_col(d, x["school"], x["location"], bold=True, inline=inline)
                _two_col(d, x["degree"], x["dates"], italic=True, inline=inline)
                bullets(x.get("details", []))
        elif sec == "experience":
            heading("Experience")
            for x in items:
                if st.get("company_first"):
                    _two_col(d, x["company"], x["dates"], bold=True, link=x.get("link", ""), color=linkc, inline=inline)
                    _two_col(d, x["title"], x["location"], italic=True, inline=inline)
                else:
                    _two_col(d, x["title"], x["dates"], bold=True, inline=inline)
                    _two_col(d, x["company"], x["location"], italic=True, link=x.get("link", ""), color=linkc, inline=inline)
                bullets(x.get("bullets", []))
        elif sec == "projects":
            heading("Projects")
            for x in items:
                p = _two_col(d, x["name"], "", bold=True, link=x.get("link", ""), color=linkc)
                if x.get("tech"):
                    p.add_run(f" | {x['tech']}").italic = True
                if x.get("demo") or x.get("dates"):
                    p.add_run("  ·  " if inline else "\t")
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
