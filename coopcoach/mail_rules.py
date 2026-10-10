"""How a recruiting email is read: is it about an application, what it means (received, assessment, interview,
offer, rejection), which company and role, its links and dates, and which application it belongs to."""

import email
import email.policy
import email.utils
import hashlib
import re
import time
from datetime import datetime

from . import db
from .textutil import company_key, find_req_ids, req_key, same_req, title_key

STATUS_RANK = {"saved": 0, "applied": 1, "oa": 2, "interviewing": 3, "rejected": 4, "offer": 5}

# --- classification -------------------------------------------------------------------

_P = lambda s: re.compile(s, re.I)  # noqa: E731
OFFER = _P(r"\b(pleased|delighted|happy|thrilled|excited) to (extend|offer)|offer of employment|offer letter|"
           r"\bjob offer\b|we(?:'d| would) like to offer you|extend (?:you )?an offer|congratulations[^.]{0,80}\boffer\b")
REJECT_STRONG = _P(r"regret to inform|not (?:be )?moving forward with your|decided (?:not )?to (?:move|proceed) forward with "
                   r"(?:other|another)|will not be (?:moving|proceeding) forward|(?:have|has) not been selected|"
                   r"were not selected|no longer (?:under consideration|being considered)|position has (?:now )?been filled|"
                   r"(?:decided to )?pursue other candidates|other candidates whose|not able to offer you|unable to offer you|"
                   r"won't be moving forward|will not be progressing|not to progress your application|"
                   r"decided to move forward with other|we have decided not to")
REJECT_WEAK = _P(r"\bunfortunately\b|after careful (?:consideration|review)")
OA = _P(r"online assessment|coding (?:challenge|assessment|test|exercise)|technical (?:assessment|challenge)|hackerrank|"
        r"codesignal|codility|hirevue|karat|assessment invitation|invit\w+ to (?:complete|take) (?:an? |the |our )?"
        r"(?:\w+ )?(?:assessment|test|challenge)|complete (?:the|your|an|our) (?:\w+ )?assessment|take-home|"
        r"pymetrics|numerical reasoning|game-based assessment|one-way video|video (?:assessment|interview) "
        r"(?:invitation|link)|\bOA\b")
INTERVIEW = _P(r"\binterview(?:s|ing)?\b|schedule (?:a|your|an|the) (?:call|chat|time|meeting|conversation|interview)|"
               r"phone screen|recruiter (?:call|screen)|your availability|availability for|next round|superday|"
               r"\bonsite\b|would (?:like|love) to (?:speak|chat|meet|connect) with you|invite you to (?:an? )?"
               r"(?:interview|call|chat)|calendly\.com|book a time|move (?:you )?forward to the next")
OA_STRONG = _P(r"invit\w+ (?:you )?to (?:complete|take)|please complete (?:the|your|an|our|this)|complete (?:the|your) "
               r"(?:\w+ )?(?:assessment|challenge|test) (?:by|within|before)|assessment link|hackerrank\.com/|"
               r"codesignal\.com/|app\.codility\.com|hirevue\.com/|(?:assessment|challenge) (?:invitation|invite)")
INTERVIEW_STRONG = _P(r"invite you to (?:an? )?(?:interview|call|chat|phone screen)|(?:like|love) to (?:invite|schedule|set up) "
                      r"(?:you|an?|a time)|schedule (?:a|your|an|the) (?:call|chat|interview|time)|your availability|"
                      r"phone screen|recruiter (?:call|screen)|superday|next round|calendly\.com|book a time|"
                      r"(?:like|love) to (?:speak|chat|meet|connect) with you|move (?:you )?forward to the next")
APPLIED = _P(r"thank(?:s| you) for (?:applying|your application|submitting your application|your interest in)|"
             r"application (?:has been |was )?(?:received|submitted)|we(?:'ve| have) received your application|"
             r"received your (?:job )?application|your application (?:for|to|with)|successfully (?:applied|submitted)|"
             r"application confirmation|we will (?:carefully )?review your (?:application|resume|qualifications)|"
             r"your application was sent to|application (?:is|was) (?:now )?(?:under review|being reviewed)")
JOBBY = _P(r"applica|candida|recruit|talent|career|hiring|position|\brole\b|intern|co-?op|interview|assessment|offer")
NOT_JOB = _P(r"job alert|jobs? (?:you may|you might|for you|matching)|new jobs|recommended jobs|is hiring|"
             r"people (?:also )?viewed|weekly digest|newsletter|webinar|your daily|top job picks|similar jobs|"
             r"credit card|mortgage|loan|apartment|rental|visa application|passport|scholarship application|"
             r"admission|graduate school|course registration|osap")
ALERT_SENDERS = _P(r"jobalerts-noreply@linkedin|jobs-listings@linkedin|alert@indeed|noreply@glassdoor|"
                   r"invitations@linkedin|messages-noreply@linkedin|notifications-noreply@linkedin|"
                   r"newsletters?@|digest@|marketing@")

ATS_DOMAINS = ("greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com", "myworkday.com", "workday.com",
               "myworkdayjobs.com", "icims.com", "smartrecruiters.com", "taleo.net", "successfactors.com",
               "successfactors.eu", "jobvite.com", "linkedin.com", "indeed.com", "indeedemail.com", "hackerrank.com",
               "codesignal.com", "codility.com", "hirevue.com", "karat.io", "gem.com", "rippling.com", "bamboohr.com",
               "workable.com", "recruitee.com", "eightfold.ai", "avature.net", "oraclecloud.com", "brassring.com",
               "phenom.com", "paradox.ai", "simplify.jobs", "handshake.com", "joinhandshake.com", "breezy.hr",
               "teamtailor.com", "pinpointhq.com", "dover.com", "wellfound.com", "ycombinator.com",
               "workatastartup.com", "calendly.com", "goodtime.io", "modernloop.io", "zoom.us")
GENERIC_DOMAINS = ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "live.com", "me.com",
                   "proton.me", "protonmail.com")
_NAME_NOISE = _P(r"\b(university|campus|early careers?|emerging talent|talent acquisition|talent team|talent|"
                 r"recruiting|recruitment|recruiters?|careers?|jobs?|hiring team|hiring|team|hr|people ops|"
                 r"no-?reply|notifications?|via \w+|do not reply|candidate experience|the)\b")


_GENERIC_EDGE = {"online", "technical", "coding", "video", "final", "phone", "virtual", "your", "the", "re", "fwd",
                 "fw", "new", "important", "action", "required", "update", "application", "interview", "assessment",
                 "offer", "invitation", "regarding", "about", "from", "with", "at", "to", "for", "on", "in", "of",
                 "a", "an", "and", "is", "has", "this", "my"}
_BAD_COMPANY = _P(r"\b(thank|thanks|you|your|next steps?|update|status|invitation|invite|regarding|reminder|"
                  r"confirm\w*|received|we|our|congratulations|unfortunately|hello|hi|dear)\b")
_ROLE_WORD = _P(r"\b(intern|internship|co-?op|engineer\w*|developer|analyst|scientist|designer|manager|associate|"
                r"researcher|student|specialist|consultant|programmer|technologist|architect|trader|quant\w*)\b")


def _valid_company(c: str) -> str:
    words = c.split()
    while words and words[0].lower() in _GENERIC_EDGE:
        words.pop(0)
    while words and words[-1].lower() in _GENERIC_EDGE:
        words.pop()
    c = " ".join(words)
    # company names start with a capital or digit ("on", "your team" etc. are leftovers, not companies)
    if not c or not re.match(r"[A-Z0-9]", c) or _BAD_COMPANY.search(c) or len(c) > 45:
        return ""
    if re.search(r"\b(?:role|position|opportunity|internships?|interns?|co-?op)\s*$|\b(?:intern|internship)\b", c, re.I):
        return ""  # "the Software Engineering Intern role" is the job, not who it's at
    return c


def _clean_company(s: str) -> str:
    s = re.sub(r"[\"'“”]", "", s or "")
    s = re.split(r"\s+[-–|:]\s+|!|\.(?:\s|$)|,\s", s)[0]
    s = _NAME_NOISE.sub(" ", s)
    s = re.sub(r"\s+@\s+.*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -–|:,.")
    return _valid_company(s) if 1 < len(s) <= 50 else ""


_SUBJ_COMPANY = [
    _P(r"(?:thank(?:s| you) for (?:applying|your application|your interest)|your application) "
       r"(?:to|at|with|in) (?:the )?(?P<c>[A-Z0-9][\w&.'’ -]{1,48}?)(?:[!.,]|$| [-–|]| for | team\b)"),
    _P(r"application (?:for|to) (?P<r>.+?) (?:at|with) (?P<c>[A-Z0-9][\w&.'’ -]{1,48}?)(?:[!.,]|$| [-–|])"),
    _P(r"your application was sent to (?P<c>[A-Z0-9][\w&.'’ -]{1,48}?)(?:[!.,]|$)"),
    _P(r"(?:invitation|invite) (?:to|for) (?:an? )?(?:interview|assessment|chat|call) (?:with|at) "
       r"(?P<c>[A-Z0-9][\w&.'’ -]{1,48}?)(?:[!.,]|$| [-–|])"),
    _P(r"^(?:re: |fwd?: )?(?P<c>[A-Z0-9][\w&.'’ ]{1,40}?) (?:application|candidacy|interview|assessment|offer|"
       r"recruiting|careers?)\b"),
    _P(r"^(?:re: |fwd?: )?(?P<c>[A-Z0-9][\w&.'’ ]{1,40}?)\s*[|–-]\s*(?P<r>.{3,90})$"),
]
_ROLE = [
    _P(r"(?:for|to) (?:the|our) (?P<r>[^.;\n]{3,90}?) (?:position|role|opportunity|req(?:uisition)?)\b"),
    _P(r"application (?:for|to) (?:the )?(?P<r>[^.,;\n]{3,90}?)(?: at | with | position| role|[.,;\n]|$)"),
    _P(r"(?:position|role|job|requisition)(?: title)?: ?(?P<r>[^.,;\n]{3,90})"),
    # a capitalized title ending in Intern / Co-op, e.g. "Software Engineering Intern (Summer 2027)"
    re.compile(r"(?P<r>(?:[A-Z][\w/&+.-]*\s){0,6}(?:Intern|Internship|Co-?op|Coop|Student)\b(?:\s*\([^)]{0,30}\))?)"),
]


def _domain_company(addr: str) -> str:
    dom = (addr or "").rsplit("@", 1)[-1].lower()
    if not dom or any(dom == d or dom.endswith("." + d) for d in ATS_DOMAINS + GENERIC_DOMAINS):
        return ""
    parts = [p for p in dom.split(".") if p not in ("mail", "email", "careers", "jobs", "recruiting", "talent",
                                                    "hr", "notifications", "noreply", "us", "eu", "ca", "co", "com",
                                                    "org", "net", "io", "ai", "uk", "app")]
    return parts[-1] if parts else ""


def classify(subject: str, from_name: str, from_addr: str, body: str) -> dict | None:
    """Rule-based: returns {status, company, role} or None if this isn't about one of your applications."""
    text = f"{subject}\n{body[:6000]}"
    if ALERT_SENDERS.search(from_addr or "") or NOT_JOB.search(subject or ""):
        return None
    sender_is_ats = any((from_addr or "").lower().endswith(d) for d in ATS_DOMAINS)
    strong_body = any(rx.search(body[:3000]) for rx in (APPLIED, INTERVIEW_STRONG, OA_STRONG, OFFER, REJECT_STRONG))
    if not (sender_is_ats or JOBBY.search(f"{subject} {from_name} {from_addr}") or
            (strong_body and JOBBY.search(body[:2000]))):
        return None

    head = body[:4000]
    if OFFER.search(text):
        status = "offer"
    elif REJECT_STRONG.search(text):
        status = "rejected"
    elif OA.search(subject) or OA_STRONG.search(head) or (OA.search(head) and not APPLIED.search(text)):
        status = "oa"
    elif INTERVIEW.search(subject) or INTERVIEW_STRONG.search(head) or \
            (INTERVIEW.search(head) and not APPLIED.search(text)):
        status = "interviewing"
    elif REJECT_WEAK.search(body[:3000]) and APPLIED.search(text):
        status = "rejected"  # "thank you for applying ... unfortunately ..."
    elif APPLIED.search(text):
        status = "applied"
    else:
        return None

    company, role = "", ""
    for rx in _SUBJ_COMPANY:
        m = rx.search(subject or "")
        if m:
            company = _clean_company(m.group("c"))
            if "r" in m.groupdict() and m.group("r"):
                role = m.group("r").strip()
            if company:
                break
    if " at " in company:
        before, company = company.rsplit(" at ", 1)
        role = role or re.sub(r"\b(position|role|opportunity)\b", "", before).strip()
        company = _clean_company(company)
    if not company:
        m = re.search(r"(?:applying|application|interest) (?:to|at|with|in) (?:the )?([A-Z][\w&.'’ -]{1,40}?)"
                      r"(?:[!.,]| for | team\b|\n)", body[:2000])
        if m:
            company = _clean_company(m.group(1))
    if not company:
        # "Sam (Notion)", "Jordan from Cohere", "Priya | Ramp", "Alex @ Stripe"
        m = re.search(r"\(([^)]+)\)|\b(?:from|at)\s+(.+)$|[|@]\s*(.+)$", from_name or "")
        if m:
            company = _clean_company(next(g for g in m.groups() if g))
    if not company:
        company = signature_company(body)
    if not company:
        company = _clean_company(from_name or "")
    if not company or company.lower() in ("greenhouse", "lever", "workday", "ashby", "linkedin", "indeed",
                                          "hackerrank", "codesignal", "icims", "smartrecruiters"):
        company = _domain_company(from_addr).title() or ""
    if role and not _ROLE_WORD.search(role):
        role = ""
    if not role:
        for rx in _ROLE:
            m = rx.search(f"{subject}\n{body[:2500]}")
            if m and _ROLE_WORD.search(m.group("r")):
                role = re.sub(r"^(?:you(?:'ve| have)? (?:been )?|we |i )\S*\s*", "", m.group("r")).strip(" -–:")
                break
    role = re.sub(r"^.*?[.!?]\s+(?=[A-Z])", "", role)  # "Shopify. Dev Degree Intern" -> "Dev Degree Intern"
    if company and role.lower().startswith(company.lower() + " "):
        role = role[len(company) + 1:]
    role = re.sub(r"\s*[-–|:]\s*(?:online assessment|(?:technical |coding )?assessment|(?:\w+ )?interview\b.*|"
                  r"next steps?|invitation\b.*|update\b.*|your application\b.*)$", "", role, flags=re.I)
    role = re.sub(r"\s{2,}", " ", role).strip()[:120]
    return {"status": status, "company": company, "role": role}


_TEAM_WORDS = r"(?:hiring|recruiting|recruitment|talent(?: acquisition)?|people|careers?|university recruiting|" \
              r"campus recruiting|early careers?|engineering|hr)"
_SIGNATURE = [  # the sign-off names the company: "Lumen Robotics Hiring Team", "The Stripe Team", "Recruiter | Ramp"
    _P(rf"^(?:the\s+)?(?P<c>[A-Z0-9][\w&.'’ -]{{1,48}}?)\s+{_TEAM_WORDS}?\s*team\b"),
    _P(r"^(?:\w+\s){0,3}(?:recruiter|recruiting|talent|sourcer|coordinator|partner|people|hr)\w*\s*(?:at|@|\||,|-|–)\s*"
       r"(?:the\s+)?(?P<c>[A-Z0-9][\w&.'’ -]{1,48})$"),
]


def signature_company(body: str) -> str:
    """The company from the last lines of an email (where people sign off), or ''."""
    lines = [ln.strip(" \t*_-–—") for ln in (body or "").strip().splitlines()]
    for ln in [x for x in lines if x and len(x) <= 70][-8:][::-1]:
        ln = re.sub(r"^(?:best|thanks|thank you|regards|kind regards|best regards|cheers|sincerely|all the best|warmly)"
                    r"[,!.]?\s+", "", ln, flags=re.I)
        for rx in _SIGNATURE:
            m = rx.search(ln)
            if m:
                c = _clean_company(m.group("c"))
                if c and not re.fullmatch(r"(?i)our|my|your|hiring|recruiting|talent|the", c):
                    return c
    return ""


# --- an email pasted into the chat or Applications ---------------------------------------------------

LINK_KINDS = [  # (kind, pattern on the URL): what a link in a recruiting email is for
    ("book", _P(r"ashbyhq\.com/meeting|calendly\.com|goodtime\.io|\bcal\.com/|calendar\.app\.google|"
                r"calendar\.google\.com/calendar/(?:appointments|u/\d/appointments)|bookwithme|bookings|youcanbook\.me|"
                r"zcal\.co|savvycal|chilipiper|hubspot\.com/meetings|doodle\.com|modernloop|schedul|availability")),
    ("assessment", _P(r"hackerrank|codesignal|codility|hirevue|karat|coderpad|testgorilla|mettl|imocha|qualified\.io|"
                      r"byteboard|leetcode\.com/assess|glider\.ai|pymetrics|harver|criteriacorp|shl\.com|canditech|"
                      r"assessment|/test/|challenge")),
    ("video", _P(r"zoom\.us/(?:j|my)/|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|webex\.com")),
]
_TZ = {"ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York", "CT": "America/Chicago",
       "CST": "America/Chicago", "CDT": "America/Chicago", "MT": "America/Denver", "MST": "America/Denver",
       "MDT": "America/Denver", "PT": "America/Los_Angeles", "PST": "America/Los_Angeles",
       "PDT": "America/Los_Angeles", "UTC": "UTC", "GMT": "UTC"}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], 1)}
_WHEN = _P(r"\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?"
           r"(?:,?\s+(?P<year>20\d\d))?(?:,?\s*(?:at|@|from|-|–)?\s*(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)"
           r"(?:\s*(?P<tz>[ECMP][SD]?T|UTC|GMT)\b)?)?")
_DUE = _P(r"(?:complete|submit|finish|due|deadline|expires?|closes?|by|before|within)\D{0,40}?(?:within\s+)?"
          r"(?P<n>\d{1,2})\s*(?P<unit>days?|hours?|hrs?)\b")


def email_when(text: str, now: float | None = None) -> dict:
    """{"at": a stated date+time (epoch) or None, "due": a deadline (a date, or "within N days") or None}."""
    from zoneinfo import ZoneInfo
    now = now or time.time()
    at = due = None
    for m in _WHEN.finditer(text or ""):
        try:
            year = int(m.group("year") or datetime.fromtimestamp(now).year)
            mon, day = _MONTHS[m.group("mon").lower()[:3]], int(m.group("day"))
            hour, minute = 23, 59
            if m.group("h"):
                hour, minute = int(m.group("h")) % 12, int(m.group("m") or 0)
                if m.group("ap").lower().startswith("p"):
                    hour += 12
            tz = ZoneInfo(_TZ[m.group("tz").upper()]) if m.group("tz") and m.group("tz").upper() in _TZ else None
            d = datetime(year, mon, day, hour, minute, tzinfo=tz) if tz else datetime(year, mon, day, hour, minute)
            ts = d.timestamp()
            if not m.group("year") and ts < now - 30 * 86400:  # "Jan 12" written in December means next year
                ts = d.replace(year=year + 1).timestamp()
        except (ValueError, KeyError):
            continue
        if ts < now - 86400:
            continue
        before = (text or "")[max(0, m.start() - 60):m.start()].lower()
        if m.group("h") and at is None and not re.search(r"due|deadline|by|before|complete|submit", before):
            at = int(ts)
        elif due is None and re.search(r"due|deadline|by|before|complete|submit|expire|close", before):
            due = int(ts)
    m = _DUE.search(text or "")
    if due is None and m:
        n = int(m.group("n"))
        due = int(now + n * (3600 if m.group("unit").startswith("h") else 86400))
    return {"at": at, "due": due}


def email_links(text: str) -> dict:
    """The links in an email sorted by what they're for: book (a time), assessment, video (call), other."""
    out: dict = {"book": "", "assessment": "", "video": "", "other": []}
    for url in dict.fromkeys(u.rstrip(").,;>\"'") for u in re.findall(r"https?://[^\s<>\"')]+", text or "")):
        kind = next((k for k, rx in LINK_KINDS if rx.search(url)), "")
        if kind and not out[kind]:
            out[kind] = url
        elif not kind and not re.search(r"unsubscribe|privacy|linkedin\.com/(?:company|in)/|facebook|twitter|x\.com/|"
                                        r"instagram|\.(?:png|jpe?g|gif)$", url, re.I):
            out["other"].append(url)
    out["other"] = out["other"][:3]
    return out


_GREETING = _P(r"^(?:hi|hello|hey|dear|good (?:morning|afternoon|evening))\b[^.!?]{0,40}[,!]?$")


def read_pasted(text: str) -> dict:
    """What a pasted recruiting email says: status, company, role, sender, subject, links, dates, req numbers. Header
    lines (From:, Subject:) are used when they were pasted too; otherwise the body and its sign-off are enough."""
    text = (text or "").replace("\r\n", "\n").strip()
    hdr = {}
    for k in ("from", "subject", "to", "date", "sent", "cc"):
        m = re.search(rf"^{k}:\s*(.+)$", text, re.I | re.M)
        if m:
            hdr[k] = m.group(1).strip()
    body = re.sub(r"^(?:from|subject|to|date|sent|cc):.*$\n?", "", text, flags=re.I | re.M).strip()
    from_name, from_addr = email.utils.parseaddr(hdr.get("from", ""))
    subject = hdr.get("subject", "")
    res = classify(subject, from_name, from_addr, body)
    if not res:  # they told us it's about an application, so skip the "is this a job email?" gate
        res = classify(subject, from_name, from_addr or "talent@greenhouse.io", body) or \
            {"status": "", "company": signature_company(body), "role": ""}
        if res["company"].lower() == "greenhouse":
            res["company"] = signature_company(body)
    sig = signature_company(body)
    if sig and (not res["company"] or res["company"].lower() == (from_name or "").lower()
                or res["company"].lower() == from_name.split(" ")[0].lower()):
        res["company"] = sig  # "Sam" (who wrote it) -> "Lumen Robotics" (who they write for)
    first = next((ln.strip() for ln in body.splitlines() if ln.strip() and not _GREETING.match(ln.strip())), "")
    return {**res, "subject": subject or re.split(r"(?<=[.!?])\s", first)[0][:120],
            "from_name": from_name, "from_addr": from_addr, "links": email_links(text),
            "when": email_when(body), "req_ids": find_req_ids(f"{subject}\n{body[:8000]}")}


# --- matching an email to an application --------------------------------------------

def _sim(a: str, b: str) -> float:
    ta = set(title_key(a).split()) - {"intern", "internship", "co", "op", "coop", "the", "and", "of", "2026", "2027",
                                      "2028", "summer", "winter", "fall", "spring", "student"}
    tb = set(title_key(b).split()) - {"intern", "internship", "co", "op", "coop", "the", "and", "of", "2026", "2027",
                                      "2028", "summer", "winter", "fall", "spring", "student"}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _rank(role: str, title: str) -> tuple[float, float]:
    """Core-word similarity first; the full title (term, year, location) breaks ties."""
    full_a, full_b = set(title_key(role).split()), set(title_key(title).split())
    full = len(full_a & full_b) / len(full_a | full_b) if full_a and full_b else 0.0
    return _sim(role, title), full


def _same_company(a: str, b: str) -> bool:
    ka, kb = company_key(a), company_key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka.startswith(kb + " ") or kb.startswith(ka + " ") or \
        (len(ka) >= 4 and len(kb) >= 4 and (ka in kb.split() or kb in ka.split()))


def match_by_req(pid: int, req_ids: list[str], company: str = "") -> str | None:
    """The posting an email (or Workday row) is about, by the requisition number it quotes. Exact, so it wins over
    company/title guessing when a company has several near-identical intern roles."""
    if not req_ids:
        return None
    apps = db.rows("SELECT a.job_id, COALESCE(a.req_id, j.req_id) AS req_id FROM applications a"
                   " LEFT JOIN jobs j ON j.id=a.job_id WHERE a.profile_id=? AND COALESCE(a.req_id, j.req_id) IS NOT NULL"
                   " ORDER BY a.updated_at DESC", (pid,))
    for rid in req_ids:
        hit = next((a["job_id"] for a in apps if same_req(rid, a["req_id"])), None)
        if hit:
            return hit
    ck = company_key(company) if company else ""
    for rid in req_ids:
        digits = re.sub(r"\D", "", req_key(rid))
        if len(digits) < 4:
            continue
        cands = db.rows("SELECT id, req_id, company_key FROM jobs WHERE req_id LIKE ?", (f"%{digits[-4:]}%",))
        cands = [j for j in cands if same_req(rid, j["req_id"])]
        same_co = [j for j in cands if ck and (j["company_key"] == ck or j["company_key"].startswith(ck + " "))]
        # a short number could collide across companies: only trust it alone when it's long and unique
        if same_co:
            return same_co[0]["id"]
        if len(cands) == 1 and len(digits) >= 6:
            return cands[0]["id"]
    return None


def remember_req(pid: int, job_id: str, req_ids: list[str]) -> None:
    """An email quoting exactly one requisition number teaches the tracker that application's number."""
    if job_id and len(req_ids) == 1:
        with db.tx() as c:
            c.execute("UPDATE applications SET req_id=? WHERE profile_id=? AND job_id=? AND req_id IS NULL",
                      (req_ids[0], pid, job_id))


def match_application(pid: int, company: str, role: str) -> str | None:
    """Return the application id (job id) this email is about, creating a tracker entry if needed."""
    if not company:
        return None
    apps = db.rows("SELECT a.job_id, a.status, COALESCE(j.company, a.company) AS company,"
                   " COALESCE(j.title, a.title) AS title FROM applications a LEFT JOIN jobs j ON j.id=a.job_id"
                   " WHERE a.profile_id=? ORDER BY a.updated_at DESC", (pid,))
    same = [a for a in apps if _same_company(a["company"] or "", company)]
    if same:
        if len(same) == 1 and (not role or _sim(role, same[0]["title"] or "") >= 0.15 or
                               not re.search(r"\w", same[0]["title"] or "")):
            return same[0]["job_id"]
        if role:
            best = max(same, key=lambda a: _rank(role, a["title"] or ""))
            if _sim(role, best["title"] or "") >= 0.25:
                return best["job_id"]
        else:
            # no role in the email: the most recently active application at that company
            return same[0]["job_id"]
    # not tracked yet: look for the posting itself
    ck = company_key(company)
    cands = db.rows("SELECT id, title FROM jobs WHERE company_key=? OR company_key LIKE ?", (ck, ck + " %"))
    if cands and role:
        best = max(cands, key=lambda j: _rank(role, j["title"]))
        if _sim(role, best["title"]) >= 0.4:
            return best["id"]
    # applied somewhere Co-op Coach doesn't track: create an external entry
    ext = "ext:" + hashlib.sha1(f"{ck}|{title_key(role)}".encode()).hexdigest()[:12]
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO applications (profile_id, job_id, status, company, title, source, updated_at)"
                  " VALUES (?,?,?,?,?,?,?)", (pid, ext, "saved", company, role or "Role from email", "email", db.now()))
    return ext


def apply_status(pid: int, job_id: str, status: str, when: int, subject: str) -> bool:
    """Move an application forward (never backwards) based on an email. Returns True if status changed."""
    cur = db.row("SELECT status, updated_at, last_email_at, last_email_subject FROM applications"
                 " WHERE profile_id=? AND job_id=?", (pid, job_id))
    with db.tx() as c:
        if cur is None:
            c.execute("INSERT INTO applications (profile_id, job_id, status, source, applied_at, updated_at,"
                      " last_email_at, last_email_subject) VALUES (?,?,?,?,?,?,?,?)",
                      (pid, job_id, status, "email", when, db.now(), when, subject))
            return True
        changed = STATUS_RANK.get(status, 0) > STATUS_RANK.get(cur["status"], 0)
        newer = when >= (cur["last_email_at"] or 0)
        c.execute("UPDATE applications SET status=?, updated_at=?, last_email_at=?, last_email_subject=?,"
                  " applied_at=COALESCE(applied_at, ?) WHERE profile_id=? AND job_id=?",
                  (status if changed else cur["status"],
                   db.now() if changed else cur["updated_at"],
                   when if newer else cur["last_email_at"],
                   subject if newer else cur["last_email_subject"],
                   when, pid, job_id))
        return changed
