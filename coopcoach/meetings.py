"""Meetings and deadlines from email, on the calendar: interviews, online assessments, recruiter calls, coffee chats,
info sessions and career fairs.

extract() reads an email (and any calendar invite attached to it) for what it's about, when (a stated time, a
deadline, "within 5 days", "tomorrow at 3pm"), and the links that go with it (join a call, book a time, start the
assessment). save() keeps only that: the item, who it's with, which application it's about, and a link back to the
email, never the email itself. An invite updated or cancelled by the organizer moves or cancels the item it made.
Coffee chats, info sessions and career fairs are networking, not applications: they go on the calendar without
touching the tracker.
"""

import re
import time
from datetime import datetime, timedelta

from . import db

KINDS = {"interview": "Interview", "oa": "Online assessment", "recruiter_call": "Recruiter call",
         "coffee_chat": "Coffee chat", "info_session": "Info session", "career_fair": "Career fair", "other": "Meeting",
         "block": "Your block"}
MINUTES = {"interview": 60, "oa": 30, "recruiter_call": 30, "coffee_chat": 30, "info_session": 60, "career_fair": 180,
           "other": 30, "block": 60}
ALARM = {"interview": 60, "oa": 1440, "recruiter_call": 30, "coffee_chat": 30, "info_session": 60, "career_fair": 1440,
         "other": 30, "block": 15}  # minutes before: a reminder on the calendar link
NETWORKING = {"coffee_chat", "info_session", "career_fair"}

_P = lambda s: re.compile(s, re.I)  # noqa: E731
_FAIR = _P(r"career (?:fair|expo|day)|job fair|recruiting fair|hiring fair")
_INFO = _P(r"info(?:rmation)? session|info sesh|employer (?:presentation|info)|open house|tech talk|lunch (?:and|&) learn|"
           r"\brsvp\b")
_COFFEE = _P(r"coffee chat|virtual coffee|grab (?:a )?coffee|over coffee|informational (?:interview|chat|call)|"
             r"networking (?:call|chat)|chat about (?:your|my) (?:career|interests|goals|path)|happy to (?:chat|connect)|"
             r"\bcoffee\b")
_RECRUITER = _P(r"recruiter (?:call|screen|chat)|phone screen|intro(?:ductory)? call|initial (?:call|chat|conversation)|"
                r"screening call|quick call")
_APPLICATION = _P(r"your application|you applied|applying to|candidacy|candidate|requisition|\breq\b|next (?:step|round)|"
                  r"interview|assessment|the (?:\w+ )?(?:role|position) you")
_BOOKED = _P(r"interview (?:is |has been |was )?(?:scheduled|confirmed|booked|set)|confirm(?:ing|ed)? (?:your|the) "
             r"interview|interview (?:invitation|details|confirmation)|(?:scheduled|booked) (?:your|an|the) interview")
_CANCELLED = _P(r"\b(?:has been|was|is) cancel+ed\b|\bcancel+ing (?:our|the|your)\b|\bcancel+ation\b")
_DUE_WORD = _P(r"due|deadline|by|before|complete|submit|expire|close|no later than")


def kind_of(subject: str, body: str, status: str | None = None, invite: bool = False) -> str | None:
    """What an email is arranging, if anything: interview, oa, recruiter_call, coffee_chat, info_session,
    career_fair, other (a calendar invite we can't place), or None."""
    from .mail_rules import INTERVIEW, INTERVIEW_STRONG, OA, OA_STRONG
    text = f"{subject}\n{(body or '')[:4000]}"
    if _FAIR.search(text):
        return "career_fair"
    applying = bool(_APPLICATION.search(text))
    if _INFO.search(text) and not applying:
        return "info_session"
    if _COFFEE.search(text) and not (status in ("interviewing", "oa") and applying):
        return "coffee_chat"
    if status == "oa" or OA_STRONG.search(text) or OA.search(subject or ""):
        return "oa"
    if _RECRUITER.search(text):
        return "recruiter_call"
    if status == "interviewing" or INTERVIEW_STRONG.search(text) or _BOOKED.search(text) or INTERVIEW.search(subject or ""):
        return "interview"
    return "other" if invite else None


# --- when: dates and times written the ways recruiting emails and invites write them ---------------------------

_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_WD = {d: i for i, d in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])}
_DATE = _P(r"(?P<rel>today|tonight|tomorrow)"
           r"|(?:(?P<next>next|this)\s+)?(?P<wd>mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?(?=[\s,])(?!\s*,?\s*(?:jan|feb|mar|apr|"
           r"may|jun|jul|aug|sep|oct|nov|dec|\d))"
           r"|(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
           r"(?:,?\s+(?P<year>20\d\d))?"
           r"|(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon2>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
           r"(?:,?\s+(?P<year2>20\d\d))?"
           r"|(?P<iy>20\d\d)-(?P<im>\d{1,2})-(?P<id>\d{1,2})"
           r"|(?P<na>\d{1,2})/(?P<nb>\d{1,2})/(?P<ny>20\d\d|\d\d)\b")
_TIME = _P(r"(?<![\d:/])(?P<h>\d{1,2})(?::(?P<m>[0-5]\d))?\s*(?P<ap>[ap]\.?m\.?)(?![a-z])"
           r"|(?<![\d:/])(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)(?![\d:])")
_RANGE = _P(r"^\s*(?:-|–|—|to|until|till)\s*")
_ZONE = _P(r"^\s*\(?\s*(?P<abbr>[ECMP][SD]?T|UTC|GMT)\b(?:\s*(?P<sign>[+-])\s*(?P<oh>\d{1,2})(?::?(?P<om>\d{2}))?)?"
           r"|^\s*\(\s*UTC\s*(?P<sign2>[+-])(?P<oh2>\d{2}):(?P<om2>\d{2})\)")
_ZONES = {"ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York", "CT": "America/Chicago",
          "CST": "America/Chicago", "CDT": "America/Chicago", "MT": "America/Denver", "MST": "America/Denver",
          "MDT": "America/Denver", "PT": "America/Los_Angeles", "PST": "America/Los_Angeles",
          "PDT": "America/Los_Angeles", "UTC": "UTC", "GMT": "UTC"}


def _day(m, now: datetime) -> datetime | None:
    g = m.groupdict()
    if g["rel"]:
        return now + timedelta(days=1 if g["rel"].lower() == "tomorrow" else 0)
    if g["wd"]:
        ahead = (_WD[g["wd"].lower()[:3]] - now.weekday()) % 7
        if g["next"] and g["next"].lower() == "next" and ahead == 0:
            ahead = 7
        return now + timedelta(days=ahead)
    try:
        if g["mon"] or g["mon2"]:
            mon = _MON[(g["mon"] or g["mon2"]).lower()[:3]]
            day = int(g["day"] or g["d1"])
            year = int(g["year"] or g["year2"] or now.year)
            d = datetime(year, mon, day)
            if not (g["year"] or g["year2"]) and d < now - timedelta(days=30):  # "Jan 12" written in December
                d = d.replace(year=year + 1)
            return d
        if g["iy"]:
            return datetime(int(g["iy"]), int(g["im"]), int(g["id"]))
        if g["na"]:
            a, b, y = int(g["na"]), int(g["nb"]), int(g["ny"])
            y = y + 2000 if y < 100 else y
            mon, day = (a, b) if a <= 12 else (b, a)  # 11/04 is November 4th (North America); 25/11 can only be a day
            return datetime(y, mon, day)
    except (ValueError, KeyError):
        return None
    return None


def _hm(t) -> tuple[int, int]:
    if t.group("h24") is not None:
        return int(t.group("h24")), int(t.group("m24"))
    h, mi = int(t.group("h")) % 12, int(t.group("m") or 0)
    return (h + 12 if t.group("ap").lower().startswith("p") else h), mi


def _zone(text: str):
    from datetime import timezone as tz
    from zoneinfo import ZoneInfo
    z = _ZONE.match(text)
    if not z:
        return None, 0
    if z.group("abbr"):
        base = _ZONES.get(z.group("abbr").upper())
        if z.group("sign") and z.group("abbr").upper() in ("UTC", "GMT"):
            mins = int(z.group("oh")) * 60 + int(z.group("om") or 0)
            return tz(timedelta(minutes=mins if z.group("sign") == "+" else -mins)), z.end()
        return (ZoneInfo(base) if base else None), z.end()
    mins = int(z.group("oh2")) * 60 + int(z.group("om2"))
    return tz(timedelta(minutes=mins if z.group("sign2") == "+" else -mins)), z.end()


def moments(text: str, now: float | None = None) -> list[dict]:
    """Every date written in the text, each with its time (and end time, and time zone) when one is written next to it:
    [{"start", "end", "has_time", "due": a deadline word just before it}], in reading order."""
    now_dt = datetime.fromtimestamp(now or time.time())
    out = []
    text = text or ""
    for m in _DATE.finditer(text):
        day = _day(m, now_dt)
        if not day:
            continue
        after, before = text[m.end():m.end() + 40], text[max(0, m.start() - 30):m.start()]
        t = re.match(r"^[\s,]*(?:at|@|from|,|-)?\s*", after)
        tm = _TIME.match(after, t.end()) if t else None
        if not tm:  # "3pm on Tuesday, Nov 3": the time written before the date
            tb = list(_TIME.finditer(before))
            tm_before = tb[-1] if tb and re.search(r"(?:on|,)\s*$", before[tb[-1].end():]) else None
        else:
            tm_before = None
        start, end, rest = day.replace(hour=23, minute=59), None, ""
        has_time = bool(tm or tm_before)
        if tm or tm_before:
            h, mi = _hm(tm or tm_before)
            start = day.replace(hour=h, minute=mi, second=0, microsecond=0)
            rest = after[tm.end():] if tm else after
            r = _RANGE.match(rest)
            if r and tm:
                te = _TIME.match(rest, r.end())
                if te:
                    eh, em = _hm(te)
                    if te.group("ap") is None and tm.group("ap") and eh < 12 and tm.group("ap").lower().startswith("p"):
                        eh += 12
                    end = start.replace(hour=eh, minute=em)
                    if end <= start:
                        end += timedelta(days=1)
                    rest = rest[te.end():]
        zone, _ = _zone(rest)
        if zone is not None:
            start = start.replace(tzinfo=zone)
            end = end.replace(tzinfo=zone) if end else None
        s = int(start.timestamp())
        out.append({"start": s, "end": int(end.timestamp()) if end else None, "has_time": has_time,
                    "due": bool(_DUE_WORD.search(before[-25:]))})
    return out


# --- an email to calendar items -------------------------------------------------------------------------------

def _invites(ics_text: str) -> list[dict]:
    """The events in an attached invite, with what the organizer is doing with them (a new time, or a cancellation:
    METHOD:CANCEL or STATUS:CANCELLED, which come with the UID of the event they cancel)."""
    from .events import _unfold, parse_ics
    lines = _unfold(ics_text or "")
    method = next((ln.split(":", 1)[1].strip().upper() for ln in lines if ln.upper().startswith("METHOD:")), "")
    parsed = {e["uid"]: e for e in parse_ics(ics_text or "")}  # (it leaves cancelled events out)
    out, cur = [], None
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            cur = {}
        elif ln == "END:VEVENT" and cur is not None:
            uid = cur.get("uid", "")
            cancelled = method == "CANCEL" or cur.get("status") == "CANCELLED"
            e = parsed.get(uid)
            if cancelled:
                out.append({"uid": uid, "cancelled": True, "title": "", "start_ts": None, "end_ts": None})
            elif e:
                out.append({**e, "cancelled": False})
            cur = None
        elif cur is not None and ":" in ln:
            name, value = ln.split(":", 1)
            name = name.split(";")[0].upper()
            if name in ("UID", "STATUS"):
                cur[name.lower()] = value.strip().upper() if name == "STATUS" else value.strip()
    return out


def extract(subject: str, body: str, ics: str = "", received_at: int | None = None, status: str | None = None) -> list[dict]:
    """What an email puts on the calendar: [{kind, start_ts, end_ts, due_ts, all_day, location, join_url, book_url,
    ics_uid, status (confirmed | to_book | cancelled), title}]. Dates are read relative to when the email arrived."""
    from .mail_rules import email_links, email_when
    now = received_at or int(time.time())
    text = f"{subject or ''}\n{body or ''}"
    links = email_links(text)
    if ics and "BEGIN:VEVENT" in ics:
        out = []
        for e in _invites(ics):
            k = kind_of(f"{e.get('title') or ''} {subject or ''}", f"{e.get('description') or ''}\n{body or ''}",
                        status, invite=True)
            join = e.get("url") or links["video"]
            out.append({"kind": k, "title": e.get("title") or "", "start_ts": e.get("start_ts"), "end_ts": e.get("end_ts"),
                        "due_ts": None, "all_day": int(bool(e.get("all_day"))), "location": e.get("location") or "",
                        "join_url": join if re.match(r"https?://", join or "") else "", "book_url": "",
                        "ics_uid": e["uid"], "status": "cancelled" if e["cancelled"] else "confirmed"})
        return out
    k = kind_of(subject, body, status)
    if not k:
        return []
    found = [m for m in moments(text, now) if m["start"] >= now - 3600]
    item = {"kind": k, "title": "", "start_ts": None, "end_ts": None, "due_ts": None, "all_day": 0, "location": "",
            "join_url": links["assessment"] if k == "oa" else links["video"], "book_url": links["book"], "ics_uid": None,
            "status": "confirmed"}
    if _CANCELLED.search(f"{subject or ''}\n{(body or '')[:600]}"):
        return [{**item, "status": "cancelled"}]
    if k == "oa":
        due = next((m["start"] for m in found if m["due"]), None) or email_when(text, now)["due"]
        if not due and not links["assessment"]:
            return []
        return [{**item, "due_ts": due, "status": "confirmed" if due else "to_book"}]
    when = next((m for m in found if m["has_time"] and not m["due"]), None)
    if when:
        return [{**item, "start_ts": when["start"], "end_ts": when["end"] or when["start"] + MINUTES[k] * 60}]
    day_only = next((m for m in found if not m["due"]), None) if k in ("career_fair", "info_session") else None
    if day_only:  # a fair or session on a stated day, no time given
        d = datetime.fromtimestamp(day_only["start"]).replace(hour=0, minute=0)
        return [{**item, "start_ts": int(d.timestamp()), "end_ts": int(d.timestamp()) + 86400, "all_day": 1}]
    if links["book"]:  # they still have to pick a time
        return [{**item, "status": "to_book"}]
    return []


def save(pid: int, items: list[dict], job_id: str | None = None, company: str = "", who: str = "",
         email_ref: str = "", email_subject: str = "", email_link: str = "", source: str = "inbox") -> list[int]:
    """Keep these items: a moved or cancelled invite updates the item it made; a newer time for the same interview (or
    coffee chat with the same person) replaces the old one; an interview's time and an assessment's deadline also go
    on the application, as the tracker shows them."""
    now = db.now()
    ids = []
    for it in items:
        k = it["kind"] or "other"
        title = it.get("title") or f"{KINDS[k]}: {company or who or 'from email'}"
        prior = None
        if it.get("ics_uid"):
            prior = db.row("SELECT * FROM calendar_items WHERE profile_id=? AND ics_uid=?", (pid, it["ics_uid"]))
        if not prior and (job_id or who):
            prior = db.row("SELECT * FROM calendar_items WHERE profile_id=? AND kind=? AND " +
                           ("job_id=?" if job_id else "who=?") + " AND status != 'cancelled' AND (status='to_book' OR"
                           " COALESCE(start_ts, due_ts, 0) >= ?) ORDER BY updated_at DESC LIMIT 1",
                           (pid, k, job_id or who, now - 86400))
        if not prior and email_ref:
            prior = db.row("SELECT * FROM calendar_items WHERE profile_id=? AND email_ref=? AND kind=?", (pid, email_ref, k))
        if it["status"] == "cancelled" and not prior:
            continue  # nothing of ours to cancel
        vals = {"kind": k, "title": title[:200], "start_ts": it.get("start_ts"), "end_ts": it.get("end_ts"),
                "due_ts": it.get("due_ts"), "all_day": int(it.get("all_day") or 0), "location": (it.get("location") or "")[:300],
                "join_url": it.get("join_url") or "", "book_url": it.get("book_url") or "", "status": it["status"],
                "job_id": job_id, "company": company or None, "who": who or None, "email_ref": email_ref or None,
                "email_subject": (email_subject or "")[:200] or None, "email_link": email_link or None,
                "ics_uid": it.get("ics_uid"), "source": source}
        with db.tx() as c:
            if prior:
                keep = {kk: v for kk, v in vals.items() if v not in (None, "") or kk == "status"}
                if it["status"] == "cancelled":
                    keep = {"status": "cancelled"}
                elif it["status"] == "to_book" and prior["start_ts"]:
                    keep = {kk: v for kk, v in keep.items() if kk in ("book_url", "join_url")}  # already booked
                c.execute("UPDATE calendar_items SET " + ", ".join(f"{kk}=?" for kk in keep) + ", updated_at=? WHERE id=?",
                          (*keep.values(), now, prior["id"]))
                ids.append(prior["id"])
            else:
                cols = list(vals)
                ids.append(c.execute(f"INSERT INTO calendar_items ({', '.join(cols)}, profile_id, created_at, updated_at)"
                                     f" VALUES ({', '.join('?' * len(cols))}, ?, ?, ?)",
                                     (*vals.values(), pid, now, now)).lastrowid)
            if job_id and it["status"] == "confirmed":
                if k in ("interview", "recruiter_call") and it.get("start_ts"):
                    c.execute("UPDATE applications SET interview_at=? WHERE profile_id=? AND job_id=?",
                              (it["start_ts"], pid, job_id))
                if k == "oa" and it.get("due_ts"):
                    c.execute("UPDATE applications SET oa_due_at=? WHERE profile_id=? AND job_id=?",
                              (it["due_ts"], pid, job_id))
    return ids


def forget(pid: int, email_ref: str) -> int:
    """The items an email made, gone (the email was undone, or marked not about a job)."""
    with db.tx() as c:
        return c.execute("DELETE FROM calendar_items WHERE profile_id=? AND email_ref=?", (pid, email_ref)).rowcount


def remove(pid: int, item_id: int) -> bool:
    with db.tx() as c:
        return c.execute("DELETE FROM calendar_items WHERE profile_id=? AND id=?", (pid, item_id)).rowcount > 0


def upcoming(pid: int, days: int = 120, past_hours: int = 6) -> list[dict]:
    """What's coming: items with a time or deadline in the window (cancelled ones left out), and ones still to book."""
    now = db.this_hour()
    rows = db.rows("SELECT * FROM calendar_items WHERE profile_id=? AND status != 'cancelled' AND (status='to_book' OR"
                   " COALESCE(start_ts, due_ts) BETWEEN ? AND ?) ORDER BY COALESCE(start_ts, due_ts, 9e18)",
                   (pid, now - past_hours * 3600, now + days * 86400))
    return [{**r, "label": KINDS.get(r["kind"], "Meeting"), "networking": r["kind"] in NETWORKING} for r in rows]


def for_calendar(pid: int) -> list[dict]:
    """Items shaped like events for the calendar link (ics_for), each with its reminder."""
    out = []
    for r in db.rows("SELECT * FROM calendar_items WHERE profile_id=? AND status='confirmed' AND"
                     " COALESCE(start_ts, due_ts) > ?", (pid, db.this_hour() - 30 * 86400)):
        due = r["start_ts"] is None
        start = r["start_ts"] or r["due_ts"]
        title = f"{r['title']} (due)" if due else r["title"]
        out.append({"id": f"item-{r['id']}", "title": title, "start_ts": start,
                    "end_ts": r["end_ts"] or start + (1800 if due else MINUTES.get(r["kind"], 30) * 60),
                    "all_day": bool(r["all_day"]), "location": r["location"] or "",
                    "url": r["join_url"] or r["book_url"] or r["email_link"] or "", "alarm": ALARM.get(r["kind"], 30),
                    "description": " · ".join(x for x in (r["who"], r["email_subject"]) if x), "job_id": r["job_id"],
                    "kind": r["kind"]})
    return out
