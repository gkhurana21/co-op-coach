"""Career events: coffee chats, info sessions, networking nights, career fairs, hackathons.

Feeds can be:
  * any iCalendar URL (campus career centre, club Google Calendar, CampusGroups, webcal://...)
  * a Luma city page, e.g. https://lu.ma/toronto (uses Luma's public discover listing)
  * a Luma calendar, e.g. https://lu.ma/genai-sf (fetched as iCalendar)
  * a Meetup search, e.g. https://www.meetup.com/find/?location=ca--on--Waterloo&source=EVENTS&categoryId=546
  * an Eventbrite browse page, e.g. https://www.eventbrite.ca/d/canada--toronto/science-and-tech--events/
  * Devpost's hackathon list (https://devpost.com/hackathons) and MLH's season page (https://mlh.io/seasons/2027/events)
  * university calendars: Localist (/api/2/events), LiveWhale (/live/json/events), WordPress "The Events Calendar"
    (/wp-json/tribe/events/v1/events) and Trumba / any .ics. Paste a calendar's home page and resolve_feed() finds which.

The universities and Toronto/Waterloo organizers in event_sources.yaml, and the well-known hackathons in
major_hackathons.yaml, were each checked live with tools/discover_event_sources.py.

Every event is also sorted into a region (Waterloo Region, Toronto/GTA, SF Bay Area, New York, Online, ...).
LinkedIn events need a signed-in account, so they aren't read automatically.
"""

import concurrent.futures
import hashlib
import html
import logging
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import httpx
import yaml

from . import config, db
from .sources import client
from .textutil import parse_iso, strip_html

log = logging.getLogger(__name__)

KINDS = [
    ("coffee_chat", r"coffee chat|coffee-chat|meet (?:the|our) (?:team|recruiters?)|office hours|1:1|one-on-one"),
    ("info_session", r"info(?:rmation)? session|infosession|employer session|company (?:presentation|showcase)|"
                     r"recruiting session|application session|lunch (?:and|&) learn|open house"),
    ("career_fair", r"career fair|job fair|career expo|recruitment fair|co-?op fair|internship fair|career day"),
    ("hackathon", r"hackathon|hack night|buildathon|\bhack\w*|game jam|datathon|case competition"),
    ("workshop", r"workshop|resume|cv review|interview prep|mock interview|leetcode|bootcamp|how to land|"
                 r"tutorial|masterclass"),
    ("networking", r"networking|mixer|meetup|meet-up|social|happy hour|founders?|demo day|pitch night|"
                   r"community night|after ?party|breakfast|dinner"),
    ("talk", r"panel|fireside|tech talk|speaker|keynote|summit|conference|ama\b"),
]
_KIND_RES = [(k, re.compile(p, re.I)) for k, p in KINDS]
CAREER_RE = re.compile(
    r"career|recruit|hiring|\binterns?\b|internships?|co-?op|coffee chat|info(?:rmation)? session|networking|resume|interview|"
    r"hackathon|job|employer|student|new grad|mentor|founder|startup|demo day|yc\b|y combinator|tech talk|"
    r"engineer|developer|\bai\b|machine learning|product|fintech|quant|women in|panel|fireside|meetup|mixer",
    re.I,
)
# university calendars list everything from concerts to thesis defences: only career, recruiting, startup and hackathon
# events count, and nothing aimed at staff, alumni, high schoolers or applicants to graduate programs
CAMPUS_RE = re.compile(
    r"\bcareers?\b|recruit(?:ing|ment|ers?)\b|\bhiring\b|\binternships?\b|\binterns?\b|\bco-?op\b|employer|"
    r"info(?:rmation)? session|networking|r[ée]sum[ée]|\bcv\b|interview|job (?:fair|search|shadow)|career fair|\bjobs?\b|"
    r"hackathon|datathon|\bhacks?\b|startup|start-up|entrepreneur|founder|pitch (?:night|competition|day)|demo day|"
    r"venture|accelerator|incubator|coffee chat|tech talk|industry night|meet the (?:firms?|employers?|companies)|"
    r"company (?:presentation|showcase)|case competition|linkedin|handshake",
    re.I,
)
NOT_STUDENT_RE = re.compile(
    r"high school|college fair|\badmissions?\b|prospective|freshm[ae]n|transfer students?|\bparents?\b|family weekend|"
    r"open house|campus tour|\bmba\b|"
    r"master'?s (?:program|degree|info)|graduate (?:program|certificate|admissions)|program overview|ph\.?d\. (?:info|program)|"
    r"alumni (?:reunion|weekend)|reunion|faculty (?:meeting|search|candidate)|thesis defen[cs]e|dissertation|"
    r"\bstaff\b|employees?\b|retire|benefits enrol|\bhr\b|executive education|book club|homecoming",
    re.I,
)
CAMPUS_GROUPS = ("Canadian universities", "US universities")


# --- regions ----------------------------------------------------------------------

REGIONS = [  # (region, pattern on the location text); first match wins, so Boston's Cambridge beats Waterloo's
    ("Online", r"\bonline\b|\bvirtual\b|\bzoom\b|\bremote\b|\bwebinar\b|livestream"),
    ("Amherst", r"amherst,?\s*(?:ma\b|massachusetts)|\bumass\b"),
    ("Boston", r"boston|cambridge,?\s*(?:ma\b|massachusetts)|somerville|massachusetts"),
    ("Waterloo Region", r"waterloo|kitchener|cambridge|guelph|\bN[123][A-Z]\b|laurier|conestoga|communitech|velocity"),
    ("Hamilton", r"hamilton|mcmaster"),
    ("Toronto / GTA", r"toronto|mississauga|missisauga|markham|vaughan|brampton|oakville|richmond hill|scarborough|"
                      r"north york|etobicoke|burlington|pickering|ajax|oshawa|whitby|durham region|\bM\d[A-Z]\b|\bGTA\b"),
    ("Ottawa", r"ottawa|gatineau|kanata"),
    ("Montréal", r"montr[eé]al|laval|longueuil"),
    ("Québec City", r"qu[eé]bec city|ville de qu[eé]bec|universit[eé] laval"),
    ("Kingston", r"kingston,?\s*(?:on\b|ontario)|queen'?s university"),
    ("London ON", r"london,?\s*(?:on\b|ontario)|western university|\buwo\b"),
    ("Victoria", r"victoria,?\s*(?:bc\b|b\.c\.|british columbia)|\buvic\b|university of victoria"),
    ("Vancouver", r"vancouver|burnaby|richmond,?\s*bc|surrey,?\s*bc|british columbia"),
    ("Calgary / Edmonton", r"calgary|edmonton|alberta"),
    ("Halifax", r"halifax|dartmouth,?\s*(?:ns|nova scotia)|dalhousie"),
    ("Winnipeg", r"winnipeg|manitoba"),
    ("Saskatoon", r"saskatoon|regina|saskatchewan"),
    ("SF Bay Area", r"san francisco|\bsf\b|palo alto|stanford|mountain view|menlo park|sunnyvale|san jose|oakland|berkeley|"
                    r"redwood city|cupertino|santa clara|san mateo|bay area"),
    ("Los Angeles", r"los angeles|\bucla\b|santa monica|pasadena|\busc\b|irvine|long beach"),
    ("San Diego", r"san diego|la jolla|\bucsd\b"),
    ("New York", r"new york|\bnyc\b|brooklyn|manhattan|queens|jersey city|hoboken|flushing"),
    ("Seattle", r"seattle|bellevue|redmond|kirkland"),
    ("Philadelphia", r"philadelphia|\bupenn\b|university of pennsylvania"),
    ("Pittsburgh", r"pittsburgh|carnegie mellon"),
    ("Princeton / NJ", r"princeton|new brunswick,?\s*nj|piscataway|rutgers|newark,?\s*nj|new jersey"),
    ("New Haven", r"new haven"),
    ("Ithaca", r"ithaca"),
    ("Providence", r"providence,?\s*(?:ri|rhode island)|brown university"),
    ("Washington DC", r"washington,?\s*d\.?c|district of columbia|college park|baltimore|johns hopkins|hyattsville|"
                      r"arlington,?\s*va"),
    ("Atlanta", r"atlanta"),
    ("Austin", r"austin,?\s*(?:tx|texas)|ut austin|university of texas at austin"),
    ("Houston", r"houston|rice university"),
    ("Dallas", r"dallas|richardson,?\s*(?:tx|texas)|plano"),
    ("Chicago", r"chicago|evanston"),
    ("Champaign", r"champaign|urbana"),
    ("Ann Arbor", r"ann arbor"),
    ("Madison", r"madison,?\s*(?:wi\b|wisconsin)"),
    ("West Lafayette", r"west lafayette|purdue"),
    ("Research Triangle", r"chapel hill|durham,?\s*(?:nc\b|north carolina)|raleigh|duke university"),
    ("Gainesville", r"gainesville"),
    ("College Station", r"college station|texas a&m"),
    ("Other Canada", r"canada|\b(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE)\b|ontario|qu[eé]bec|manitoba|nova scotia"),
    ("Other US", r"\b(?:CA|NY|WA|MA|TX|IL|PA|NC|GA|FL|CO|MI|OH|VA|NJ|MD|MN|AZ|OR|UT|TN|IN|WI|MO)\b|united states|"
                 r"\bUSA?\b|california|texas|illinois|pennsylvania|carolina|georgia|florida|colorado|michigan|ohio|"
                 r"virginia|washington|oregon|arizona|utah|tennessee|indiana|wisconsin|minnesota|maryland|new jersey"),
]
_REGION_RES = [(name, re.compile(p, re.I)) for name, p in REGIONS]
GENERIC_REGIONS = ("Unknown", "Elsewhere", "Other US", "Other Canada")
# close enough to go to a hackathon or a career fair (about two hours or less)
NEARBY = {"Toronto / GTA": {"Waterloo Region", "Hamilton"}, "Waterloo Region": {"Toronto / GTA", "Hamilton", "London ON"},
          "Hamilton": {"Toronto / GTA", "Waterloo Region"}, "London ON": {"Waterloo Region"}, "Kingston": {"Ottawa"},
          "Ottawa": {"Montréal", "Kingston"}, "Montréal": {"Ottawa", "Québec City"}, "Québec City": {"Montréal"},
          "Vancouver": {"Victoria"}, "Victoria": {"Vancouver"}, "SF Bay Area": set(),
          "New York": {"Boston", "Princeton / NJ", "Philadelphia", "New Haven"}, "Boston": {"New York", "Providence", "Amherst"},
          "Providence": {"Boston"}, "Amherst": {"Boston"}, "New Haven": {"New York"},
          "Philadelphia": {"New York", "Princeton / NJ"}, "Princeton / NJ": {"New York", "Philadelphia"},
          "Los Angeles": {"San Diego"}, "San Diego": {"Los Angeles"}, "Champaign": {"Chicago"}, "Chicago": {"Champaign"}}


def event_region(location: str, title: str = "") -> str:
    loc = (location or "").strip()
    if not loc:
        return "Online" if re.search(r"\bvirtual|online|webinar\b", title or "", re.I) else "Unknown"
    for name, rx in _REGION_RES:
        if rx.search(loc):
            return name
    return "Elsewhere"


# Meetup/Eventbrite tech listings carry a lot of noise that isn't about careers at all
JUNK_RE = re.compile(r"passive income|make money online|get rich|side hustle income|dropshipping|forex|crypto ?trading|"
                     r"bitcoin|real estate invest|mlm\b|network marketing|astrology|tarot|speed dating|singles\b|"
                     r"wine tasting|yoga|meditation retreat|weight loss|financial freedom", re.I)


def classify(title: str, description: str = "") -> str:
    for kind, rx in _KIND_RES:
        if rx.search(title):
            return kind
    for kind, rx in _KIND_RES:
        if rx.search(description[:500]):
            return kind
    return "other"


# --- iCalendar ----------------------------------------------------------------

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_dt(value: str, params: dict) -> tuple[int | None, bool]:
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            d = datetime.strptime(value[:8], "%Y%m%d")
            return int(d.replace(tzinfo=timezone.utc).timestamp()), True
        if value.endswith("Z"):
            d = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        else:
            d = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
            tz = params.get("TZID")
            try:
                d = d.replace(tzinfo=ZoneInfo(tz)) if tz else d.astimezone()
            except Exception:
                d = d.astimezone()
        return int(d.timestamp()), False
    except ValueError:
        return None, False


def parse_ics(text: str) -> list[dict]:
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
            continue
        if line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *rawparams = head.split(";")
        params = dict(p.split("=", 1) for p in rawparams if "=" in p)
        name = name.upper()
        if name in ("DTSTART", "DTEND"):
            ts, all_day = _parse_dt(value, params)
            cur[name.lower()] = ts
            if name == "DTSTART":
                cur["all_day"] = all_day
        elif name in ("SUMMARY", "DESCRIPTION", "LOCATION", "URL", "UID", "STATUS"):
            cur[name.lower()] = _unescape(value)
    out = []
    for e in events:
        if not e.get("dtstart") or not e.get("summary") or e.get("status") == "CANCELLED":
            continue
        url = e.get("url") or ""
        if not url:
            m = re.search(r"https?://\S+", e.get("description") or "")
            url = m.group(0).rstrip(").,") if m else ""
        out.append({
            "uid": e.get("uid") or f"{e['summary']}-{e['dtstart']}",
            "title": e["summary"].strip(),
            "start_ts": e["dtstart"],
            "end_ts": e.get("dtend"),
            "all_day": e.get("all_day", False),
            "location": (e.get("location") or "").strip(),
            "url": url,
            "description": (e.get("description") or "").strip(),
        })
    return out


# --- Luma -----------------------------------------------------------------------

_LUMA_RE = re.compile(r"https?://(?:www\.)?(?:lu\.ma|luma\.com)/([\w-]+)/?$")


def _luma_resolve(c: httpx.Client, slug: str) -> dict:
    r = c.get("https://api.lu.ma/url", params={"url": slug})
    r.raise_for_status()
    return r.json()


def _luma_discover(c: httpx.Client, place_id: str, days: int = 45) -> list[dict]:
    out, cursor = [], None
    horizon = time.time() + days * 86400
    for _ in range(6):
        params = {"discover_place_api_id": place_id, "pagination_limit": 50}
        if cursor:
            params["pagination_cursor"] = cursor
        r = c.get("https://api.lu.ma/discover/get-paginated-events", params=params)
        r.raise_for_status()
        data = r.json()
        for entry in data.get("entries", []):
            ev = entry.get("event") or {}
            start = parse_iso(ev.get("start_at"))
            if not start:
                continue
            geo = ev.get("geo_address_info") or {}
            loc = geo.get("full_address") or geo.get("city_state") or ("Online" if ev.get("location_type") == "online" else "")
            hosts = ", ".join(h.get("name", "") for h in (entry.get("hosts") or [])[:3] if h.get("name"))
            out.append({
                "uid": ev.get("api_id"),
                "title": ev.get("name") or "",
                "start_ts": start,
                "end_ts": parse_iso(ev.get("end_at")),
                "all_day": False,
                "location": loc,
                "url": f"https://lu.ma/{ev.get('url')}" if ev.get("url") else "",
                "description": f"Hosted by {hosts}" if hosts else "",
            })
            if start > horizon:
                return out
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


# --- Devpost, MLH, Meetup, Eventbrite ----------------------------------------------------

_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _devpost_dates(s: str) -> tuple[int | None, int | None]:
    """"Oct 09 - 12, 2026" / "Oct 20 - Dec 21, 2026" / "Dec 28, 2026 - Jan 03, 2027" -> (start, end) timestamps."""
    m = re.match(r"(\w{3})\w* (\d{1,2})(?:, (\d{4}))? - (?:(\w{3})\w* )?(\d{1,2}), (\d{4})", s or "")
    if not m:
        return None, None
    m1, d1, y1, m2, d2, y2 = m.groups()
    try:
        start = datetime(int(y1 or y2), _MON[m1.lower()[:3]], int(d1), 12, tzinfo=timezone.utc)
        end = datetime(int(y2), _MON[(m2 or m1).lower()[:3]], int(d2), 23, tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None, None
    return int(start.timestamp()), int(end.timestamp())


def _devpost(c: httpx.Client) -> list[dict]:
    out = []
    for page in range(1, 6):
        r = c.get("https://devpost.com/api/hackathons", params={"status[]": "upcoming", "page": page, "per_page": 50},
                  headers={"Accept": "application/json"})
        r.raise_for_status()
        hs = r.json().get("hackathons") or []
        for h in hs:
            start, end = _devpost_dates(h.get("submission_period_dates") or "")
            loc = (h.get("displayed_location") or {}).get("location") or ""
            if not start or h.get("invite_only") or re.search(r"high school|middle school", f"{h.get('title')} {loc}", re.I):
                continue
            themes = ", ".join(t.get("name", "") for t in h.get("themes") or [])
            out.append({"uid": f"devpost-{h.get('id')}", "title": h.get("title") or "", "start_ts": start, "end_ts": end,
                        "all_day": True, "location": loc, "url": h.get("url") or "",
                        "description": f"Hackathon by {h.get('organization_name') or 'Devpost'}. {themes}".strip(),
                        "kind": "hackathon"})
        if len(hs) < 50:
            break
    return out


def _mlh(c: httpx.Client, url: str) -> list[dict]:
    r = c.get(url)
    r.raise_for_status()
    m = re.search(r'<script data-page="app" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        raise ValueError("MLH page format changed")
    out = []
    for ev in (import_json(m.group(1)).get("props") or {}).get("upcomingEvents") or []:
        v = ev.get("venueAddress") or {}
        country = (v.get("country") or "").upper()
        loc = "Online" if ev.get("formatType") == "digital" else ", ".join(x for x in (v.get("city"), v.get("state"), v.get("country")) if x)
        # MLH gives the country as a code: only US/Canada get sorted by city ("IN" is India here, not Indiana)
        region = "Online" if loc == "Online" else None if country in ("US", "USA", "CA", "CAN", "") else "Elsewhere"
        out.append({"uid": f"mlh-{ev.get('id') or ev.get('slug')}", "title": ev.get("name") or "", "region": region,
                    "start_ts": parse_iso(ev.get("startsAt")), "end_ts": parse_iso(ev.get("endsAt")), "all_day": True,
                    "location": loc, "url": ev.get("websiteUrl") or f"https://mlh.io/events/{ev.get('slug')}",
                    "description": f"MLH member hackathon ({ev.get('formatType') or 'in person'})", "kind": "hackathon"})
    return [e for e in out if e["start_ts"]]


def _meetup(c: httpx.Client, url: str) -> list[dict]:
    r = c.get(url)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        raise ValueError("Meetup page format changed")
    state = (import_json(m.group(1)).get("props") or {}).get("pageProps", {}).get("__APOLLO_STATE__") or {}
    out = []
    for ev in (v for v in state.values() if isinstance(v, dict) and v.get("__typename") == "Event"):
        venue = ev.get("venue") or {}
        if isinstance(venue, dict) and "__ref" in venue:
            venue = state.get(venue["__ref"]) or {}
        group = state.get((ev.get("group") or {}).get("__ref", "")) or {}
        online = ev.get("eventType") == "ONLINE"
        loc = "Online" if online else ", ".join(x for x in (venue.get("name"), venue.get("city"), venue.get("state")) if x)
        out.append({"uid": f"meetup-{ev.get('id')}", "title": ev.get("title") or "", "start_ts": parse_iso(ev.get("dateTime")),
                    "end_ts": None, "all_day": False, "location": loc, "url": ev.get("eventUrl") or "",
                    "description": f"Meetup by {group.get('name') or 'a local group'}. {strip_html(ev.get('description') or '')[:600]}"})
    return [e for e in out if e["start_ts"]]


def _eventbrite(c: httpx.Client, url: str, pages: int = 3) -> list[dict]:
    out, seen = [], set()
    for page in range(1, pages + 1):
        r = c.get(url, params={"page": page} if page > 1 else None)
        if r.status_code == 404 and page > 1:
            break
        r.raise_for_status()
        m = re.search(r"window\.__SERVER_DATA__\s*=\s*(\{.*?\});\s*(?:window\.|</script>)", r.text, re.S)
        if not m:
            raise ValueError("Eventbrite page format changed")
        blocks = import_json(m.group(1)).get("jsonld") or []
        flat = [b for blk in blocks for b in (blk if isinstance(blk, list) else [blk]) if isinstance(b, dict)]
        items = [it.get("item") or {} for b in flat for it in b.get("itemListElement") or [] if isinstance(it, dict)]
        for it in items:
            if not it.get("url") or it["url"] in seen:
                continue
            seen.add(it["url"])
            place = it.get("location") or {}
            addr = place.get("address") or {} if isinstance(place, dict) else {}
            online = isinstance(place, dict) and place.get("@type") == "VirtualLocation"
            loc = "Online" if online else ", ".join(x for x in (place.get("name") if isinstance(place, dict) else "",
                                                               addr.get("addressLocality"), addr.get("addressRegion")) if x)
            start = it.get("startDate") or ""
            out.append({"uid": f"eb-{it['url'].rstrip('/').rsplit('-', 1)[-1]}", "title": it.get("name") or "",
                        "start_ts": parse_iso(start if "T" in start else f"{start}T12:00:00"),
                        "end_ts": None, "all_day": "T" not in start, "location": loc, "url": it["url"],
                        "description": (it.get("description") or "")[:800]})
        if len(items) < 8:
            break
    return [e for e in out if e["start_ts"]]


# --- university calendars: Localist, LiveWhale, The Events Calendar (WordPress) ------------------------------

def _localist(c: httpx.Client, url: str, pages: int = 5) -> list[dict]:
    """A Localist calendar's API, e.g. https://events.stanford.edu/api/2/events?type[]=<career type id> (the ids, found
    by the discover tool, keep it to career / entrepreneurship events)."""
    out = []
    for page in range(1, pages + 1):
        # merge (not replace) the URL's own type[] filters with the paging parameters
        r = c.get(httpx.URL(url).copy_merge_params({"days": 90, "pp": 100, "page": page}))
        r.raise_for_status()
        data = r.json()
        for item in data.get("events") or []:
            ev = item.get("event") or {}
            tags = [f.get("name", "") for fs in (ev.get("filters") or {}).values() if isinstance(fs, list) for f in fs]
            tags += [d.get("name", "") for d in ev.get("departments") or []]
            loc = "Online" if ev.get("experience") == "virtual" else ", ".join(
                x for x in (ev.get("location_name"), ev.get("address") or ev.get("location")) if x)
            for inst in (ev.get("event_instances") or [])[:3]:  # a weekly drop-in shouldn't fill the list
                i = inst.get("event_instance") or {}
                start = parse_iso(i.get("start"))
                if not start:
                    continue
                out.append({"uid": f"localist-{i.get('id') or ev.get('id')}", "title": html.unescape(ev.get("title") or "").strip(),
                            "start_ts": start, "end_ts": parse_iso(i.get("end")), "all_day": bool(i.get("all_day")),
                            "location": loc, "url": ev.get("localist_url") or ev.get("url") or "",
                            "description": (ev.get("description_text") or "")[:1500], "tags": " ".join(tags)})
        pg = data.get("page") or {}
        if not pg.get("total") or page >= pg["total"]:
            break
    return out


def _livewhale(c: httpx.Client, url: str) -> list[dict]:
    """A LiveWhale calendar's JSON feed, e.g. https://events.brown.edu/live/json/events/max/400 (filtered here by
    event types, tags and group, since LiveWhale can't filter on them by URL)."""
    r = c.get(url)
    r.raise_for_status()
    data = r.json()
    items = data.get("data") if isinstance(data, dict) else data
    out = []
    for ev in items or []:
        if not isinstance(ev, dict) or ev.get("is_canceled"):
            continue
        start = parse_iso(ev.get("date_iso")) or ev.get("date_ts")
        if not start:
            continue
        tags = " ".join(str(t) for t in [*(ev.get("event_types") or []), *(ev.get("tags") or []),
                                         ev.get("group") or ev.get("group_title") or ""] if t)
        out.append({"uid": f"lw-{ev.get('id')}", "title": html.unescape(ev.get("title") or "").strip(), "start_ts": int(start),
                    "end_ts": parse_iso(ev.get("date2_iso")) or ev.get("date2_ts"), "all_day": bool(ev.get("is_all_day")),
                    "location": "Online" if ev.get("is_online") and not ev.get("location") else
                    html.unescape(ev.get("location") or ev.get("location_title") or ""),
                    "url": ev.get("url") or "", "description": strip_html(ev.get("summary") or ev.get("description") or "")[:1500],
                    "tags": tags})
    return out


def _tribe(c: httpx.Client, url: str, pages: int = 6) -> list[dict]:
    """WordPress sites running The Events Calendar, e.g. https://events.ubc.ca/wp-json/tribe/events/v1/events."""
    out = []
    for page in range(1, pages + 1):
        r = c.get(url, params={"per_page": 50, "page": page, "start_date": "now"})
        if r.status_code == 400 and page > 1:  # past the last page
            break
        r.raise_for_status()
        data = r.json()
        for ev in data.get("events") or []:
            tz = ev.get("timezone") or "UTC"
            try:
                start = int(datetime.strptime(ev["start_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(tz)).timestamp())
                end = int(datetime.strptime(ev["end_date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(tz)).timestamp())
            except (KeyError, ValueError):
                continue
            v = ev.get("venue") or {}
            v = v if isinstance(v, dict) else {}
            tags = " ".join(x.get("name", "") for x in [*(ev.get("categories") or []), *(ev.get("tags") or [])])
            out.append({"uid": f"tribe-{ev.get('id')}", "title": html.unescape(ev.get("title") or "").strip(), "start_ts": start,
                        "end_ts": end, "all_day": bool(ev.get("all_day")),
                        "location": ", ".join(x for x in (html.unescape(v.get("venue") or ""), v.get("city"), v.get("province")) if x),
                        "url": ev.get("url") or "", "description": strip_html(ev.get("description") or "")[:1500], "tags": tags})
        if page >= int(data.get("total_pages") or 1):
            break
    return out


def resolve_feed(url: str) -> str:
    """Turn a pasted calendar page into the feed Co-op Coach can read: a Localist / LiveWhale / The Events Calendar API,
    or an .ics link on the page. Returns the URL unchanged when it's already readable or nothing better is found."""
    url = url.strip().replace("webcal://", "https://")
    if any(rx.match(url) for rx, _, _ in _SITE_FEEDS) or _LUMA_RE.match(url) or re.search(r"\.ics(\?|$)|[?&]ical=1", url):
        return url
    m = re.match(r"(https?://[^/]+)", url)
    if not m:
        return url
    origin = m.group(1)
    with client(timeout=20) as c:
        probes = [(f"{origin}/api/2/events", lambda r: '"events"' in r.text[:300]),
                  (f"{origin}/live/json/events/max/400", lambda r: r.text.lstrip().startswith("[")),
                  (f"{origin}/wp-json/tribe/events/v1/events", lambda r: '"events"' in r.text[:500])]
        for feed, ok in probes:
            try:
                r = c.get(feed if "/api/2/" not in feed else feed + "?pp=1")
                if r.status_code == 200 and ok(r):
                    return feed
            except httpx.HTTPError:
                continue
        try:
            page = c.get(url)
            link = re.search(r'(?:href|src)=["\']((?:webcal|https?)://[^"\']+\.ics[^"\']*|[^"\']*(?:\?|&|&amp;)ical=1[^"\']*)', page.text)
            if link:
                return httpx.URL(url).join(html.unescape(link.group(1)).replace("webcal://", "https://")).__str__()
        except httpx.HTTPError:
            pass
    return url


def import_json(s: str) -> dict:
    import json
    return json.loads(s)


_SITE_FEEDS = [  # (pattern, fetcher, label)
    (re.compile(r"https?://(?:www\.)?devpost\.com/hackathons", re.I), lambda c, u: _devpost(c), "Devpost hackathons"),
    (re.compile(r"https?://(?:www\.)?mlh\.io/seasons/\d{4}/events", re.I), _mlh, "MLH hackathons"),
    (re.compile(r"https?://(?:www\.)?meetup\.com/find/", re.I), _meetup, "Meetup"),
    (re.compile(r"https?://(?:www\.)?eventbrite\.[a-z.]+/d/", re.I), _eventbrite, "Eventbrite"),
    (re.compile(r"https?://[^/]+/api/2/events\b", re.I), _localist, ""),
    (re.compile(r"https?://[^/]+/live/json/", re.I), _livewhale, ""),
    (re.compile(r"https?://[^/]+/wp-json/tribe/events/v1/events", re.I), _tribe, ""),
]


def _site_label(url: str, base: str) -> str:
    place = re.search(r"location=([a-z]{2})--[a-z]{2}--([\w-]+)|/d/[a-z-]+--([\w-]+)/", url, re.I)
    city = next((g for g in (place.groups() if place else ()) if g and len(g) > 2), "")
    return f"{base} · {city.replace('-', ' ').title()}" if city else base


def fetch_feed(url: str) -> tuple[list[dict], str]:
    """Return (events, label-hint)."""
    url = url.strip().replace("webcal://", "https://")
    with client() as c:
        for rx, fetch, base in _SITE_FEEDS:
            if rx.match(url):
                return fetch(c, url), _site_label(url, base) if base else ""
        m = _LUMA_RE.match(url)
        if m:
            info = _luma_resolve(c, m.group(1))
            kind, data = info.get("kind"), info.get("data") or {}
            if kind == "discover-place":
                place = data.get("place") or {}
                return _luma_discover(c, place["api_id"]), f"Luma · {place.get('name') or m.group(1).title()}"
            if kind == "calendar":
                cal = data.get("calendar") or {}
                r = c.get("https://api.lu.ma/ics/get", params={"entity": "calendar", "id": cal["api_id"]})
                r.raise_for_status()
                return parse_ics(r.text), f"Luma · {cal.get('name') or m.group(1)}"
            raise ValueError(f"Luma link is a single {kind or 'page'}, not a calendar or city")
        r = c.get(url)
        r.raise_for_status()
        if "BEGIN:VCALENDAR" not in r.text[:2000]:
            raise ValueError("URL did not return an iCalendar (.ics) feed")
        name = re.search(r"X-WR-CALNAME:(.+)", r.text)
        return parse_ics(r.text), (name.group(1).strip() if name else "")


@lru_cache(maxsize=1)
def campus_sources() -> list[dict]:
    """Universities and Toronto/Waterloo organizers, each checked live by tools/discover_event_sources.py."""
    data = yaml.safe_load((config.PKG / "event_sources.yaml").read_text()) or {}
    return data.get("sources") or []


@lru_cache(maxsize=1)
def major_hackathons() -> list[dict]:
    data = yaml.safe_load((config.PKG / "major_hackathons.yaml").read_text()) or {}
    out = []
    for h in data.get("hackathons") or []:
        names = [h["name"], *(h.get("aka") or [])]
        rx = re.compile("|".join(r"\b" + r"\W*".join(re.escape(w) for w in re.findall(r"[a-z0-9]+", n.lower())) + r"\b"
                                 for n in names), re.I)
        out.append({**h, "rx": rx})
    return out


def major_of(title: str) -> str:
    return next((h["name"] for h in major_hackathons() if h["rx"].search(title or "")), "")


CAMPUS_EVERY = 12 * 3600  # university calendars change slowly: twice a day is plenty (and polite)


def _keep(e: dict, feed: dict, typed: bool) -> bool:
    """Should this event be listed? Campus calendars need a career / startup / hackathon reason and nothing aimed at
    staff, alumni or applicants; other "career only" feeds use the broader career-and-tech filter."""
    text = f"{e['title']} {e.get('tags', '')} {e['description'][:400]}"
    if JUNK_RE.search(e["title"]):
        return False
    if (feed.get("grp") or "") in CAMPUS_GROUPS:
        if NOT_STUDENT_RE.search(f"{e['title']} {e.get('tags', '')}"):
            return False
        return typed or e.get("kind") == "hackathon" or bool(CAMPUS_RE.search(text))
    return not feed["career_only"] or e.get("kind") == "hackathon" or bool(CAREER_RE.search(text))


def sync_feeds(force: bool = False) -> tuple[int, dict]:
    """Fetch every feed (several at once); return (new_event_count, per-feed status). University calendars are read at
    most twice a day unless `force`."""
    now = db.now()
    feeds = [f for f in db.rows("SELECT * FROM event_feeds WHERE enabled=1")
             if force or (f.get("grp") or "") not in CAMPUS_GROUPS or (f.get("last_fetched") or 0) < now - CAMPUS_EVERY]
    status, new = {}, 0

    def fetch(f):
        try:
            return f, fetch_feed(f["url"]), None
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            return f, None, f"error: {e}"[:200]

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(fetch, feeds))
    for feed, got, err in results:
        if err:
            with db.tx() as c:
                c.execute("UPDATE event_feeds SET last_status=? WHERE id=?", (err, feed["id"]))
            status[feed["url"]] = err
            continue
        evs, label = got
        hint = feed.get("region") or event_region(f"{feed['label'] or ''} {label} {feed['url']}".replace("--", " "))
        typed = "type[]=" in feed["url"] or "type=" in feed["url"]  # Localist already filtered to career event types
        rows, seen_titles = {}, {}
        for e in sorted(evs, key=lambda x: x["start_ts"] or 0):
            kind = e.get("kind") or classify(e["title"], e["description"])
            horizon = (270 if kind == "hackathon" else 120) * 86400  # hackathons are announced months ahead
            if e["start_ts"] < now - 86400 or e["start_ts"] > now + horizon or not e["title"]:
                continue
            if not _keep({**e, "kind": kind}, feed, typed):
                continue
            tkey = re.sub(r"[^a-z0-9]", "", e["title"].lower())
            seen_titles[tkey] = seen_titles.get(tkey, 0) + 1
            if seen_titles[tkey] > 2:  # a daily drop-in: the next two are enough
                continue
            region = e.get("region") or event_region(e["location"], e["title"])
            if region in GENERIC_REGIONS and hint not in ("Unknown", "Elsewhere"):
                region = hint  # "Gates 104" at Stanford, or a Meetup venue that's just a street corner
            eid = hashlib.sha1(f"{feed['id']}|{e['uid']}".encode()).hexdigest()[:16]
            rows[eid] = (eid, feed["id"], e["title"][:300], e["start_ts"], e["end_ts"], int(e["all_day"]), e["location"][:300],
                         e["url"], e["description"][:1500], kind, now, now, region)
        with db.tx() as c:
            known = {r["id"] for r in c.execute("SELECT id FROM events WHERE feed_id=?", (feed["id"],)).fetchall()}
            if rows:
                c.executemany(
                    "INSERT INTO events (id, feed_id, title, start_ts, end_ts, all_day, location, url, description,"
                    " kind, first_seen, last_seen, region) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET title=excluded.title, start_ts=excluded.start_ts,"
                    " end_ts=excluded.end_ts, location=excluded.location, url=excluded.url,"
                    " description=excluded.description, kind=excluded.kind, last_seen=excluded.last_seen,"
                    " region=excluded.region", list(rows.values()))
            new += len(set(rows) - known)
            if label and not feed["label"]:
                c.execute("UPDATE event_feeds SET label=? WHERE id=?", (label, feed["id"]))
            c.execute("UPDATE event_feeds SET last_status=?, last_fetched=? WHERE id=?",
                      (f"ok · {len(rows)} upcoming career events", now, feed["id"]))
        status[feed["url"]] = f"ok ({len(rows)})"
    with db.tx() as c:
        c.execute("DELETE FROM events WHERE start_ts < ?", (now - 7 * 86400,))
        for e in c.execute("SELECT id, location, title FROM events WHERE region IS NULL").fetchall():
            c.execute("UPDATE events SET region=? WHERE id=?", (event_region(e["location"], e["title"]), e["id"]))
    return new, status


def _dedupe_key(e: dict) -> tuple:
    return re.sub(r"[^a-z0-9]", "", (e["title"] or "").lower())[:40], (e["start_ts"] or 0) // 3600


def for_person(pid: int, days: int = 90) -> list[dict]:
    """Upcoming events for someone: duplicates across sources merged, their ⭐/✅ marks, and a flag when an event
    mentions a company they follow or applied to."""
    marks = {r["event_id"]: r["mark"] for r in db.rows("SELECT event_id, mark FROM event_marks WHERE profile_id=?", (pid,))}
    names = {r["label"] or r["key"] for r in db.rows("SELECT key, label FROM favourites WHERE profile_id=? AND kind='company'", (pid,))}
    names |= {r["company"] for r in db.rows(
        "SELECT COALESCE(j.company, a.company) AS company FROM applications a LEFT JOIN jobs j ON j.id=a.job_id"
        " WHERE a.profile_id=?", (pid,)) if r["company"]}
    names = {n for n in names if n and len(n) >= 3}
    rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b", re.I) if names else None
    seen: dict[tuple, dict] = {}
    out = []
    for e in upcoming(days):
        if JUNK_RE.search(e["title"] or ""):
            continue
        k = _dedupe_key(e)
        if k in seen:  # same event listed by two sources
            first = seen[k]
            if e["feed_label"] and e["feed_label"] not in first["also"] and e["feed_label"] != first["feed_label"]:
                first["also"].append(e["feed_label"])
            first["mark"] = first["mark"] or marks.get(e["id"])
            continue
        e = {**e, "also": [], "mark": marks.get(e["id"]), "online": e.get("region") == "Online",
             "major": major_of(e["title"]) if e.get("kind") == "hackathon" else ""}
        m = rx.search(f"{e['title']} {(e['description'] or '')[:600]}") if rx else None
        e["company"] = m.group(1) if m else ""
        e["description"] = (e["description"] or "")[:600]
        seen[k] = e
        out.append(e)
    return out


def ics_for(evs: list[dict], name: str = "Co-op Coach events") -> str:
    """An iCalendar file (one event or a whole subscribable list)."""
    def esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def stamp(ts: int, all_day: bool) -> str:
        d = datetime.fromtimestamp(ts, timezone.utc)
        return f";VALUE=DATE:{d.strftime('%Y%m%d')}" if all_day else f":{d.strftime('%Y%m%dT%H%M%SZ')}"

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Co-op Coach//Events//EN", f"X-WR-CALNAME:{esc(name)}"]
    for e in evs:
        end = e.get("end_ts") or (e["start_ts"] + (86400 if e.get("all_day") else 3600))
        lines += ["BEGIN:VEVENT", f"UID:{e['id']}@coopcoach", f"DTSTAMP{stamp(int(time.time()), False)}",
                  f"DTSTART{stamp(e['start_ts'], e.get('all_day'))}", f"DTEND{stamp(end, e.get('all_day'))}",
                  f"SUMMARY:{esc(e['title'])}", f"LOCATION:{esc(e.get('location') or '')}",
                  f"DESCRIPTION:{esc(((e.get('url') or '') + chr(10) + (e.get('description') or ''))[:900])}",
                  f"URL:{e.get('url') or ''}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def application_dates(pid: int) -> list[dict]:
    """Interview times and online-assessment deadlines from the tracker, shaped like events for ics_for()."""
    out = []
    for a in db.rows("SELECT a.job_id, a.interview_at, a.oa_due_at, COALESCE(j.company, a.company) AS company,"
                     " COALESCE(j.title, a.title) AS title, COALESCE(j.url, a.url) AS url FROM applications a"
                     " LEFT JOIN jobs j ON j.id=a.job_id WHERE a.profile_id=? AND (a.interview_at IS NOT NULL OR"
                     " a.oa_due_at IS NOT NULL)", (pid,)):
        if a["interview_at"]:
            out.append({"id": f"interview-{a['job_id']}", "title": f"Interview: {a['company']} · {a['title']}",
                        "start_ts": a["interview_at"], "end_ts": a["interview_at"] + 3600, "url": a["url"]})
        if a["oa_due_at"]:
            out.append({"id": f"oa-{a['job_id']}", "title": f"Online assessment due: {a['company']} · {a['title']}",
                        "start_ts": a["oa_due_at"], "end_ts": a["oa_due_at"] + 1800, "url": a["url"]})
    for r in db.rows("SELECT id, title, due_at FROM reminders WHERE profile_id=? AND done=0 AND due_at > ?",
                     (pid, db.now() - 30 * 86400)):  # reminders set in the chat
        out.append({"id": f"reminder-{r['id']}", "title": f"⏰ {r['title']}", "start_ts": r["due_at"],
                    "end_ts": r["due_at"] + 900, "url": ""})
    return out


def count_upcoming(days: int = 60) -> int:
    now = db.now()
    return db.row("SELECT COUNT(*) AS n FROM events WHERE COALESCE(end_ts, start_ts) >= ? AND start_ts <= ?",
                  (now - 3600, now + days * 86400))["n"]


def upcoming(days: int = 60, kind: str = "") -> list[dict]:
    now = db.now()
    return db.rows(
        "SELECT e.id, e.feed_id, e.title, e.start_ts, e.end_ts, e.all_day, e.location, e.url,"
        " SUBSTR(e.description, 1, 600) AS description, e.kind, e.first_seen, e.region, f.label AS feed_label,"
        " f.grp AS feed_group FROM events e JOIN event_feeds f ON f.id = e.feed_id"
        " WHERE COALESCE(e.end_ts, e.start_ts) >= ? AND e.start_ts <= ?" + (" AND e.kind=?" if kind else "")
        + " ORDER BY e.start_ts", (now - 3600, now + days * 86400, *((kind,) if kind else ())),
    )


def hackathon_season(pid: int) -> list[dict]:
    """Every major hackathon with its status: dates out (from MLH / Devpost, with the listing to save) or when it
    usually happens."""
    marks = {r["event_id"]: r["mark"] for r in db.rows("SELECT event_id, mark FROM event_marks WHERE profile_id=?", (pid,))}
    found: dict[str, dict] = {}
    for e in upcoming(300, "hackathon"):
        name = major_of(e["title"])
        if name and name not in found:
            found[name] = e
    out = []
    for h in major_hackathons():
        e = found.get(h["name"])
        out.append({"name": h["name"], "city": h.get("city", ""), "region": h.get("region", ""), "country": h.get("country", ""),
                    "when": h.get("when", ""), "site": h.get("site", ""),
                    "event": {"id": e["id"], "title": e["title"], "start_ts": e["start_ts"], "end_ts": e["end_ts"],
                              "url": e["url"], "mark": marks.get(e["id"])} if e else None})
    out.sort(key=lambda x: (x["event"] is None, (x["event"] or {}).get("start_ts") or 0, x["name"]))
    return out
