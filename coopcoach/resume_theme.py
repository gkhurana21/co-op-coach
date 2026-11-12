"""Resume look: the theme keys the Design editor sets, the starting presets, and the CSS variables they become.

Every visual choice is one key in the resume's `style`. The HTML (preview + PDF) reads them as CSS variables, Word maps
them onto fonts / sizes / colours, and LaTeX keeps Jake's layout (it only takes the colours).
The editor applies the same variables in the browser for a live preview, so `css_vars` is the single source of truth:
the page gets the variables from the server, and the editor only changes values, never the mapping.
"""

import random
import re

# Fonts that every ATS reads (plain system / Office faces with real text). css = the stack the browser and the PDF use;
# docx = the face Word gets (it has to exist on the reader's machine, so it's always a standard Office font).
FONTS = {
    "cmu": {"label": "Computer Modern", "css": "'CMU Serif', 'Latin Modern Roman', Charter, 'Bitstream Charter', Georgia, serif",
            "docx": "Cambria", "kind": "serif"},
    "charter": {"label": "Charter", "css": "Charter, 'Bitstream Charter', 'Iowan Old Style', Georgia, serif",
                "docx": "Georgia", "kind": "serif"},
    "georgia": {"label": "Georgia", "css": "Georgia, 'Times New Roman', serif", "docx": "Georgia", "kind": "serif"},
    "garamond": {"label": "Garamond", "css": "'EB Garamond', Garamond, 'Adobe Garamond Pro', 'Hoefler Text', Baskerville, "
                 "'Times New Roman', serif", "docx": "Garamond", "kind": "serif"},
    "palatino": {"label": "Palatino", "css": "Palatino, 'Palatino Linotype', 'Book Antiqua', Georgia, serif",
                 "docx": "Palatino Linotype", "kind": "serif"},
    "times": {"label": "Times New Roman", "css": "'Times New Roman', Times, 'Liberation Serif', serif",
              "docx": "Times New Roman", "kind": "serif"},
    "source": {"label": "Source Sans", "css": "'Source Sans 3', 'Source Sans Pro', Inter, 'Helvetica Neue', Arial, sans-serif",
               "docx": "Calibri", "kind": "sans"},
    "inter": {"label": "Inter", "css": "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif", "docx": "Arial", "kind": "sans"},
    "helvetica": {"label": "Helvetica / Arial", "css": "'Helvetica Neue', Helvetica, Arial, 'Liberation Sans', sans-serif",
                  "docx": "Arial", "kind": "sans"},
    "calibri": {"label": "Calibri", "css": "Calibri, Carlito, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
                "docx": "Calibri", "kind": "sans"},
    "verdana": {"label": "Verdana", "css": "Verdana, 'DejaVu Sans', Geneva, sans-serif", "docx": "Verdana", "kind": "sans"},
    "trebuchet": {"label": "Trebuchet", "css": "'Trebuchet MS', 'Lucida Grande', sans-serif", "docx": "Trebuchet MS",
                  "kind": "sans"},
}
HEADING_STYLES = {"rule": "Rule under", "caps": "Caps", "smallcaps": "Small caps", "none": "Plain"}
BULLETS = {"disc": "•", "dash": "–", "square": "▪", "circle": "◦", "arrow": "›"}
DATE_ALIGN = {"right": "Right edge", "inline": "After the title"}
DENSITY = {"compact": 0.72, "normal": 1.0, "airy": 1.3}
RULE_TINTS = (0.0, 0.45, 0.7)  # how far the heading rule moves from the accent toward white (0 = solid accent)

# Number keys: (min, max, default). Out-of-range values are clamped, junk falls back to the default.
NUMBERS = {
    "body_size": (8.5, 12.5, 10.4),   # body text in pt (before one-page fitting)
    "name_size": (14.0, 30.0, 23.0),  # your name in pt
    "line_height": (1.0, 1.6, 1.22),
    "margin": (0.3, 1.0, 0.55),       # side margin in inches (top/bottom are 0.82 of it)
    "section_space": (0.4, 2.0, 1.0),  # gap above each section heading, as a multiple of the usual gap
}

THEME_DEFAULTS = {
    "heading_font": "cmu", "body_font": "cmu", "body_size": 10.4, "name_size": 23.0, "line_height": 1.22,
    "margin": 0.55, "section_space": 1.0, "heading_style": "rule", "bullet": "disc", "date_align": "right",
    "columns": 1, "density": "normal", "header_align": "center", "rule_tint": 0.0, "layout": "standard",
}
# The keys a saved theme holds (what you see), as opposed to content settings like one-page fitting.
THEME_KEYS = ["accent", "link_color", "contact", "link_icons", *THEME_DEFAULTS]

# The three old templates, now starting points you can change.
PRESETS = {
    "signature": {"label": "Signature (recommended)", "style": {
        **THEME_DEFAULTS, "heading_font": "source", "body_font": "source", "body_size": 10.0, "name_size": 22.0, "line_height": 1.17,
        "margin": 0.5, "section_space": 0.85, "heading_style": "smallcaps", "accent": "#2a7a78", "link_color": "#111111",
        "rule_tint": 0.45, "contact": "icons", "link_icons": False, "company_first": True, "layout": "signature"}},
    "jake": {"label": "Jake's (classic CS)", "style": {**THEME_DEFAULTS}},
    "modern": {"label": "Modern (sans-serif)", "style": {
        **THEME_DEFAULTS, "heading_font": "inter", "body_font": "inter", "body_size": 9.8, "name_size": 22.0,
        "header_align": "left", "accent": "#1f3a68", "link_color": "#1f3a68", "rule_tint": 0.45}},
    "classic": {"label": "Traditional (banks / corporate)", "style": {
        **THEME_DEFAULTS, "heading_font": "times", "body_font": "times", "body_size": 10.8, "name_size": 19.0,
        "heading_style": "smallcaps"}},
}

_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def normalize_theme(st: dict) -> dict:
    """Clamp / validate the theme keys in place (the rest of `style` is handled by resume_studio.normalize)."""
    for k, (lo, hi, d) in NUMBERS.items():
        try:
            st[k] = round(min(max(float(st.get(k, d)), lo), hi), 2)
        except (TypeError, ValueError):
            st[k] = d
    for k in ("heading_font", "body_font"):
        if st.get(k) not in FONTS:
            st[k] = THEME_DEFAULTS[k]
    if st.get("layout") not in ("standard", "signature"):
        st["layout"] = "standard"
    for k, allowed in (("heading_style", HEADING_STYLES), ("bullet", BULLETS), ("date_align", DATE_ALIGN),
                       ("density", DENSITY), ("header_align", ("center", "left"))):
        if st.get(k) not in allowed:
            st[k] = THEME_DEFAULTS[k]
    try:
        st["columns"] = 2 if int(st.get("columns", 1)) == 2 else 1
    except (TypeError, ValueError):
        st["columns"] = 1
    try:
        t = float(st.get("rule_tint", 0))
        st["rule_tint"] = min(RULE_TINTS, key=lambda x: abs(x - t))
    except (TypeError, ValueError):
        st["rule_tint"] = 0.0
    return st


def with_preset(style: dict | None, template: str) -> dict:
    """A style saved before the theme editor existed has none of its keys: start it from the template it used."""
    style = dict(style or {})
    if "body_font" in style or template not in PRESETS:
        return style
    return {**PRESETS[template]["style"], **style}


# --- colour: mixing in OKLab, the same space the editor uses (culori), so preview, PDF and Word agree --------------

def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _to_oklab(hx: str) -> tuple[float, float, float]:
    r, g, b = (_lin(int(hx[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _from_oklab(L: float, a: float, b: float) -> str:
    l_, m_, s_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3, (L - 0.1055613458 * a - 0.0638541728 * b) ** 3, \
        (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
           -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
           -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)
    return "#" + "".join(f"{round(_gam(c) * 255):02x}" for c in rgb)


def mix_white(hx: str, t: float) -> str:
    """hx moved a fraction t toward white, in OKLab."""
    if not t:
        return hx.lower()
    a, w = _to_oklab(hx), _to_oklab("#ffffff")
    return _from_oklab(*(x + (y - x) * t for x, y in zip(a, w)))


def contrast(hx: str, bg: str = "#ffffff") -> float:
    """WCAG contrast ratio."""
    def lum(h):
        r, g, b = (_lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    a, b = sorted((lum(hx), lum(bg)), reverse=True)
    return round((a + 0.05) / (b + 0.05), 2)


# --- CSS --------------------------------------------------------------------------------------------------------

def heading_vars(kind: str) -> dict:
    return {"rule": {"--h-case": "uppercase", "--h-variant": "normal", "--h-rule": ".8px", "--h-ls": ".06em"},
            "caps": {"--h-case": "uppercase", "--h-variant": "normal", "--h-rule": "0px", "--h-ls": ".12em"},
            "smallcaps": {"--h-case": "none", "--h-variant": "small-caps", "--h-rule": ".8px", "--h-ls": ".05em"},
            "none": {"--h-case": "none", "--h-variant": "normal", "--h-rule": "0px", "--h-ls": "0"}}[kind]


def css_vars(st: dict) -> dict:
    """The CSS custom properties for one style. The editor sets these same names on the live preview."""
    out = {
        "--accent": st["accent"], "--link": st["link_color"], "--rule": mix_white(st["accent"], st["rule_tint"]),
        "--font-h": FONTS[st["heading_font"]]["css"], "--font-b": FONTS[st["body_font"]]["css"],
        "--base": f"{st['body_size']:g}pt", "--name": f"{st['name_size']:g}pt", "--t-lh": f"{st['line_height']:g}",
        "--t-margin": f"{st['margin']:g}in", "--t-sec": f"{st['section_space']:g}", "--t-sp": f"{DENSITY[st['density']]:g}",
        "--bullet": f'"{BULLETS[st["bullet"]]}"', "--h-align": st["header_align"],
        "--d-just": "space-between" if st["date_align"] == "right" else "flex-start",
        "--d-sep": '""' if st["date_align"] == "right" else '"·"',
        "--d-gap": "14px" if st["date_align"] == "right" else ".4em",
        "--sub-style": "italic" if FONTS[st["body_font"]]["kind"] == "serif" else "normal",
        "--sub-op": "1" if FONTS[st["body_font"]]["kind"] == "serif" else ".85",
    }
    out.update(heading_vars(st["heading_style"]))
    return out


def theme_css(st: dict) -> str:
    return "html { " + " ".join(f"{k}: {v};" for k, v in css_vars(st).items()) + " }"


def ats_report(st: dict) -> dict:
    """How safe this look is for applicant-tracking systems and recruiters' eyes. The editor shows the same checks
    live; this copy is for tests and anything server-side."""
    warn = []
    if st.get("columns") == 2:
        warn.append("Two columns: some ATS read straight across both, mixing your sections.")
    if contrast(st["accent"]) < 4.5:
        warn.append("Accent is too light to read well on white (contrast under 4.5:1).")
    if contrast(st["link_color"]) < 4.5:
        warn.append("Link colour is too light to read well on white.")
    if st["body_size"] < 9.5:
        warn.append("Body text under 9.5pt is hard to read once printed.")
    if st.get("contact") == "emoji":
        warn.append("Emoji in the contact line can come out garbled in ATS.")
    if st["line_height"] < 1.08:
        warn.append("Very tight line height: lines run together.")
    if st["margin"] < 0.4:
        warn.append("Margins under 0.4in can be cut off when printed.")
    if st["bullet"] == "arrow":
        warn.append("Unusual bullet glyphs sometimes come through as odd characters.")
    return {"score": max(0, 100 - 14 * len(warn)), "warnings": warn}


# Pairings that look deliberate together (heading font, body font).
_PAIRS = [("cmu", "cmu"), ("charter", "charter"), ("georgia", "helvetica"), ("garamond", "garamond"),
          ("inter", "inter"), ("helvetica", "georgia"), ("palatino", "palatino"), ("calibri", "calibri"),
          ("times", "times"), ("inter", "charter"), ("trebuchet", "georgia"), ("verdana", "verdana")]
_SHUFFLE_ACCENTS = ["#000000", "#1f3a68", "#0b5c8a", "#0f6e73", "#1f6f50", "#7a1f2b", "#4338ca", "#334155",
                    "#2b2b2b", "#6b2d6b"]


def readable(hx: str, target: float = 4.5) -> str:
    """The same colour, darkened just enough to read on white paper."""
    L, a, b = _to_oklab(hx)
    out = hx
    while contrast(out) < target and L > 0.05:
        L -= 0.03
        out = _from_oklab(L, a, b)
    return out


def from_ai(got: dict, current: dict) -> dict:
    """A look the AI proposed, made safe: only the keys a look has, everything in range, colours readable. What it
    got wrong falls back to the current look."""
    # what the AI didn't set (the Signature layout, say: it isn't one of its choices) stays as it was
    look = {**{k: current[k] for k in THEME_KEYS if k in current}, **{k: got[k] for k in THEME_KEYS if k in got}}
    for k in ("accent", "link_color"):
        c = str(look.get(k) or "").strip().lower()
        look[k] = readable(c) if _HEX.fullmatch(c) else current.get(k) or "#000000"
    if look.get("contact") not in ("icons", "emoji", "plain"):
        look["contact"] = current.get("contact") or "icons"
    look["link_icons"] = bool(look.get("link_icons", current.get("link_icons", True)))
    return normalize_theme(look)


def shuffle(rng: random.Random | None = None) -> dict:
    """A random look that still reads as one design and stays ATS-safe (one column, dark enough colours)."""
    rng = rng or random.Random()
    h, b = rng.choice(_PAIRS)
    accent = rng.choice(_SHUFFLE_ACCENTS)
    return {"heading_font": h, "body_font": b, "accent": accent, "link_color": rng.choice([accent, "#000000"]),
            "heading_style": rng.choice(list(HEADING_STYLES)), "bullet": rng.choice(["disc", "dash", "square"]),
            "header_align": rng.choice(["center", "left"]), "rule_tint": rng.choice(RULE_TINTS),
            "body_size": rng.choice([10.0, 10.4, 10.8]), "line_height": rng.choice([1.16, 1.22, 1.28]),
            "columns": 1}
