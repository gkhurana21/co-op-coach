"""Company tiers (Big Tech / Quant / Banks & Corporate / YC / Startup) and the YC directory."""

import json
import logging
import threading
import time

import httpx

from .config import CACHE_DIR
from .textutil import company_key

log = logging.getLogger(__name__)

TIERS = {
    "yc": "YC Startup",
    "startup": "Startup",
    "bigtech": "Big Tech",
    "quant": "Quant & Trading",
    "finance": "Banks & Corporate",
    "other": "Other",
}

_BIGTECH = """
google, alphabet, google deepmind, deepmind, meta, facebook, instagram, whatsapp, apple, amazon, amazon web services,
aws, microsoft, linkedin, github, netflix, nvidia, salesforce, slack, tableau, adobe, oracle, ibm, intel, amd,
advanced micro devices, qualcomm, cisco, uber, airbnb, stripe, shopify, snowflake, databricks, tesla, spacex, palantir,
spotify, pinterest, snap, x, twitter, bytedance, tiktok, bloomberg, bloomberg lp, coinbase, robinhood, doordash,
instacart, lyft, reddit, dropbox, atlassian, workday, servicenow, intuit, paypal, block, square, roblox,
electronic arts, ubisoft, autodesk, openai, anthropic, samsung, samsung electronics, sony, arista networks, cloudflare,
datadog, mongodb, figma, zoom, twilio, okta, hubspot, crowdstrike, palo alto networks, fortinet, zscaler, vmware,
broadcom, dell, hp, hewlett packard enterprise, micron, texas instruments, analog devices, marvell, marvell technology,
arm, synopsys, cadence, cadence design systems, applied materials, lam research, kla, asml, waymo, cruise, zoox, yahoo,
ebay, etsy, wayfair, booking holdings, expedia, duolingo, grammarly, canva, gitlab, elastic, confluent, twitch, discord,
epic games, riot games, activision blizzard, nintendo, thomson reuters, ciena, opentext, blackberry, nokia, ericsson,
siemens, bosch, general motors, ford motor, rivian, lucid motors, tesla motors, scale ai, xai, mistral ai
"""

_QUANT = """
jane street, citadel, citadel securities, two sigma, hudson river trading, hrt, jump trading, optiver, imc,
imc trading, susquehanna, sig, susquehanna international group, drw, tower research capital, tower research,
five rings, five rings capital, d e shaw, de shaw, the d e shaw group, akuna capital, virtu, virtu financial, point72,
millennium, millennium management, old mission, old mission capital, radix trading, bridgewater,
bridgewater associates, aqr, aqr capital management, renaissance technologies, squarepoint, squarepoint capital,
flow traders, maven securities, xtx markets, headlands technologies, belvedere trading, wolverine trading,
chicago trading company, cubist, schonfeld, balyasny, balyasny asset management, man group, qube research,
qube research and technologies, g research, arrowstreet capital, pdt partners, the voleon group, voleon,
tgs management, sigma point, peak6, ctc, tudor investment, garda capital
"""

_FINANCE = """
rbc, royal bank of canada, rbc capital markets, td, td bank, td securities, td bank group, cibc, bmo,
bmo financial group, bank of montreal, scotiabank, bank of nova scotia, national bank of canada, national bank,
desjardins, manulife, sun life, sun life financial, canada life, intact, intact financial, omers, cpp investments,
cppib, ontario teachers pension plan, otpp, psp investments, cdpq, hoopp, brookfield, brookfield asset management,
goldman sachs, morgan stanley, jpmorgan, jp morgan, jpmorgan chase, j p morgan, jpmorganchase, bank of america,
citi, citigroup, citibank, wells fargo, capital one, blackrock, fidelity, fidelity investments, vanguard,
american express, visa, mastercard, barclays, hsbc, ubs, deutsche bank, bnp paribas, societe generale, nomura, mufg,
state street, northern trust, charles schwab, deloitte, pwc, pricewaterhousecoopers, ey, ernst and young, kpmg,
accenture, mckinsey, mckinsey and company, bcg, boston consulting group, bain, bain and company, rogers,
rogers communications, bell, bell canada, bce, telus, loblaw, canadian tire, magna, magna international, cae,
bombardier, cn, canadian national railway, air canada, hydro one, ontario power generation, enbridge, suncor,
interac, moneris, lululemon, s and p global, s and p, standard and poor s, moody s, morningstar, fitch ratings,
msci, nasdaq, tmx group, ice, intercontinental exchange, geotab, honeywell, lockheed martin, northrop grumman, boeing, raytheon, rtx, ge,
ge aerospace, ge healthcare, johnson and johnson, pfizer, procter and gamble, pepsico, l oreal, unilever, walmart,
target, costco, home depot, nike, disney, the walt disney company
"""


def _keyset(block: str) -> set[str]:
    return {company_key(n) for n in block.replace("\n", " ").split(",") if n.strip()}


_TIER_KEYS = {
    "quant": _keyset(_QUANT),
    "finance": _keyset(_FINANCE),
    "bigtech": _keyset(_BIGTECH),
}
# Names that are also common words only count as an exact full-name match, never as a prefix.
_NO_PREFIX = {"x", "block", "square", "arm", "elastic", "bell", "visa", "target", "ge", "cn", "td", "ey", "sig",
              "hp", "snap", "zoom", "citi", "ctc", "rbc", "bmo", "imc", "drw"}


def static_tier(name: str) -> str | None:
    key = company_key(name)
    if not key:
        return None
    for tier in ("quant", "finance", "bigtech"):
        if key in _TIER_KEYS[tier]:
            return tier
    # "Google DeepMind", "Amazon Web Services", "RBC Capital Markets", "TD Securities": match leading words
    words = key.split()
    for n in (3, 2, 1):
        if len(words) > n:
            head = " ".join(words[:n])
            if n == 1 and head in _NO_PREFIX and head not in ("rbc", "td", "bmo", "cibc"):
                continue
            for tier in ("quant", "finance", "bigtech"):
                if head in _TIER_KEYS[tier]:
                    return tier
    return None


# --- YC directory ------------------------------------------------------------

YC_URL = "https://yc-oss.github.io/api/companies/all.json"
YC_CACHE = CACHE_DIR / "yc_companies.json"
_yc_lock = threading.Lock()
_yc_index: dict[str, dict] | None = None


def _yc_keys(name: str) -> list[str]:
    key = company_key(name)
    keys = [key]
    for suffix in (" ai", " labs", " hq", " app", " io"):
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            keys.append(key[: -len(suffix)])
    return keys


def refresh_yc(force: bool = False) -> int:
    """Download the YC company directory (cached for 7 days). Returns company count."""
    global _yc_index
    with _yc_lock:
        fresh = YC_CACHE.exists() and time.time() - YC_CACHE.stat().st_mtime < 7 * 86400
        if force or not fresh:
            try:
                r = httpx.get(YC_URL, timeout=60, follow_redirects=True)
                r.raise_for_status()
                slim = [
                    {
                        "name": c.get("name"),
                        "slug": c.get("slug"),
                        "batch": c.get("batch"),
                        "website": c.get("website"),
                        "team_size": c.get("team_size"),
                        "status": c.get("status"),
                        "one_liner": c.get("one_liner"),
                        "is_hiring": c.get("isHiring"),
                        "former_names": c.get("former_names") or [],
                    }
                    for c in r.json()
                ]
                YC_CACHE.write_text(json.dumps(slim))
            except (httpx.HTTPError, ValueError) as e:
                log.warning("YC directory refresh failed: %s", e)
        _yc_index = None
        return len(_load_yc())


def _load_yc() -> dict[str, dict]:
    global _yc_index
    if _yc_index is not None:
        return _yc_index
    idx: dict[str, dict] = {}
    if YC_CACHE.exists():
        try:
            for c in json.loads(YC_CACHE.read_text()):
                if c.get("status") in ("Inactive", "Acquired") and not c.get("is_hiring"):
                    continue
                for nm in [c.get("name")] + list(c.get("former_names") or []):
                    for k in _yc_keys(nm or ""):
                        # very short keys collide with real words; require 4+ chars
                        if len(k) >= 4 and k not in idx:
                            idx[k] = c
        except ValueError:
            pass
    _yc_index = idx
    return idx


def yc_lookup(name: str) -> dict | None:
    idx = _load_yc()
    for k in _yc_keys(name):
        if k in idx:
            return idx[k]
    return None


def classify(name: str, hinted_tier: str | None = None) -> tuple[str, str | None]:
    """Return (tier, yc_batch)."""
    yc = yc_lookup(name)
    batch = yc.get("batch") if yc else None
    tier = static_tier(name)
    if tier:
        return tier, batch
    if hinted_tier in ("bigtech", "quant", "finance"):
        return hinted_tier, batch
    if yc:
        size = yc.get("team_size") or 0
        if yc.get("status") == "Public" or size >= 2000:
            return "bigtech", batch
        return "yc", batch
    if hinted_tier:
        return hinted_tier, batch
    return "other", batch


# US employers that regularly take international interns: Canadian co-op students (usually J-1), F-1 students on
# CPT/OPT, etc. The usual Waterloo/UofT/UBC co-op destinations. A heuristic, not a promise: add names under
# `us_friendly` in config.yaml.
_US_FRIENDLY = """
google, alphabet, meta, facebook, amazon, aws, microsoft, apple, nvidia, tesla, databricks, stripe, robinhood,
coinbase, snowflake, salesforce, uber, lyft, airbnb, pinterest, snap, figma, notion, ramp, scale ai, roblox, datadog,
cloudflare, doordash, instacart, bloomberg, linkedin, twitch, reddit, dropbox, plaid, brex, openai, anthropic,
duolingo, asana, palo alto networks, intuit, adobe, oracle, qualcomm, amd, intel, cisco, ibm, workday, servicenow,
jane street, citadel, citadel securities, two sigma, hudson river trading, hrt, jump trading, optiver, imc,
imc trading, susquehanna, sig, susquehanna international group, drw, tower research capital, five rings,
akuna capital, d e shaw, de shaw, point72, millennium, old mission, radix trading, cubist, headlands technologies,
virtu, virtu financial, wolverine trading, belvedere trading, chicago trading company, flow traders, sigma point
"""


def _friendly_keys() -> set[str]:
    from . import config
    names = [n for n in _US_FRIENDLY.replace("\n", " ").split(",") if n.strip()]
    names += list(config.get("us_friendly", []) or [])
    return {company_key(n) for n in names}


_FRIENDLY: set[str] | None = None


def hires_international_interns(name: str) -> bool:
    global _FRIENDLY
    if _FRIENDLY is None:
        _FRIENDLY = _friendly_keys()
    key = company_key(name)
    words = key.split()
    return key in _FRIENDLY or any(" ".join(words[:n]) in _FRIENDLY for n in (1, 2, 3) if len(words) > n)
