"""Recruiters you've heard from: who wrote, for which company, and how to reach them again.

Every recruiting email the inbox scan reads (and every one you paste) adds or refreshes a contact: the sender's name
and address, plus what their sign-off gives (title, phone, LinkedIn). The Recruiters tab groups them by kind of
company and by company. Automated senders (no-reply addresses, applicant-tracking systems) aren't people; when such
an email is signed by someone ("Best, Priya / Northwind Robotics Recruiting"), that person is kept, without an
address. Shared inboxes (careers@, talent@) are kept but marked as such. Anything you edit by hand stays as you wrote
it. Contacts live in your own database only.
"""

import csv
import datetime
import io
import logging
import os
import re
import zipfile
from pathlib import Path

from . import companies, db
from .mail_rules import ATS_DOMAINS, signature_company
from .textutil import company_key

log = logging.getLogger(__name__)

FIELDS = ("name", "email", "title", "phone", "linkedin", "company", "notes")
_NOREPLY = re.compile(r"^(?:no-?reply|do-?not-?reply|donotreply|notifications?|notify|mailer(?:-daemon)?|bounces?|"
                      r"system|automated|alerts?|updates?|news(?:letter)?|support|info)\b", re.I)
# a shared inbox: careers@, university-recruiting@, talent.acquisition.ca@ (but not ta.smith@)
_TEAM_FIRST = re.compile(r"careers?|jobs?|recruit\w*|talent\w*|university|campus|hr|hiring|people|internships?|early|"
                         r"students?|staffing|apply|applications?|grad\w*|interns?|co-?op", re.I)
_TEAM_REST = re.compile(_TEAM_FIRST.pattern + r"|acquisition|programs?|team|ca|us|uk|na|emea|apac|amer|nyc|sf|tor|"
                        r"toronto|canada|global|\d+", re.I)
_TEAM_NAME = re.compile(r"\b(?:team|recruiting|recruitment|talent acquisition|careers?|hiring|human resources|hr|"
                        r"university programs?|campus|no-?reply|notifications?)\b", re.I)
_SIGNOFF = re.compile(r"^(?:best|thanks|thank you|many thanks|regards|kind regards|best regards|warm regards|cheers|"
                      r"sincerely|all the best|warmly|talk soon|looking forward)\b[^a-z]*$", re.I)
_TITLE = re.compile(r"\b(?:recruit\w*|talent|sourc\w*|university|campus|people|hr|human resources|hiring|coordinator|"
                    r"partner|manager|lead|specialist|director|head|engineer\w*|founder|ceo|cto|associate|analyst|"
                    r"programs?|acquisition)\b", re.I)
# North American numbers (area code and exchange never start with 0 or 1), so "R-0000181999" isn't a phone
_PHONE = re.compile(r"(?<![\w+-])(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]?[2-9]\d{2}[\s.-]?\d{4}"
                    r"(?:\s*(?:x|ext\.?)\s*\d{1,5})?(?![\d-])")
_LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", re.I)
_PERSON = re.compile(r"[A-Z][a-zA-Z'’.-]+(?: [A-Z][a-zA-Z'’.-]*){0,3}")
_PRONOUNS = re.compile(r"\s*[(\[](?:she|he|they|ze)\s*/[^)\]]*[)\]]", re.I)
# where a reply's quoted history starts: everything after it was written by someone else (often the student)
_QUOTED = re.compile(r"^(?:>|On .{4,200}wrote:\s*$|-{2,}\s*(?:Original Message|Forwarded message)|_{10,}\s*$|"
                     r"From: .+\s+(?:Sent|Date): )", re.I)


def _person_name(s: str) -> str:
    """'Priya Shah' or 'Priya' as written in a sign-off, or '' for anything else ('Northwind Recruiting Team')."""
    s = re.sub(r"\s+", " ", (s or "").strip(" ,-–—|*_\t"))
    if not s or len(s) > 40 or "@" in s or re.search(r"\d", s) or _TEAM_NAME.search(s) or _TITLE.search(s):
        return ""
    return s if _PERSON.fullmatch(s) else ""


def _own_part(body: str) -> list[str]:
    """The lines the sender wrote, without the quoted history of a reply."""
    lines = [ln.strip() for ln in (body or "").replace("\r\n", "\n").splitlines()]
    cut = next((i for i, ln in enumerate(lines) if i and _QUOTED.match(ln)), len(lines))
    return lines[:cut]


def signature(body: str) -> dict:
    """What the sign-off of an email says about who wrote it: name, title, phone, LinkedIn, company."""
    lines = _own_part(body)
    body = "\n".join(lines)
    out = {"name": "", "title": "", "phone": "", "linkedin": "", "company": signature_company(body)}
    start = max((i for i, ln in enumerate(lines) if _SIGNOFF.match(ln)), default=None)
    block = [ln for ln in lines[start + 1:] if ln][:6] if start is not None else []
    if not block:  # "Best, Priya" on one line
        m = next((re.match(r"^(?:best|thanks|regards|cheers|sincerely|warmly)[^a-z]{1,3}(.+)$", ln, re.I)
                  for ln in reversed(lines) if ln), None)
        block = [m.group(1)] if m else []
    if block:
        split = lambda ln: [p.strip() for p in re.split(r"\s+[|·•–—]\s+", ln) if p.strip()]  # "Priya | Recruiter"
        parts = split(block[0])
        out["name"] = _person_name(parts[0]) if parts else ""
        rest = parts[1:] + [p for ln in block[1:] for p in split(ln)]
        out["title"] = next((p for p in rest if _TITLE.search(p) and len(p) <= 70 and "@" not in p
                             and not _PHONE.search(p) and "linkedin" not in p.lower()
                             and not re.search(r"\bteam$", p, re.I)), "")
    tail = "\n".join(block or lines[-12:])
    m = _PHONE.search(tail)
    out["phone"] = re.sub(r"\s+", " ", m.group(0)).strip() if m else ""
    m = _LINKEDIN.search(body)
    out["linkedin"] = ("https://" + re.sub(r"^https?://", "", m.group(0)).rstrip("/")) if m else ""
    return out


def _sender(from_name: str, from_addr: str) -> tuple[str, str, str]:
    """(kind, name, address): kind is 'person', 'team' (a shared inbox) or 'automated' (no one to write back to)."""
    addr = (from_addr or "").strip().lower()
    local, _, domain = addr.partition("@")
    name = _PRONOUNS.sub("", (from_name or "").strip().strip('"')).strip()
    name = re.sub(r"\s+(?:via|from|at)\s+.+$", "", name, flags=re.I)  # "Priya via Greenhouse"
    if not addr or _NOREPLY.match(local) or any(domain == d or domain.endswith("." + d) for d in ATS_DOMAINS):
        return "automated", name, ""
    words = [w for w in re.split(r"[._+-]", local) if w]
    if (words and _TEAM_FIRST.fullmatch(words[0]) and all(_TEAM_REST.fullmatch(w) for w in words[1:])
            or name and _TEAM_NAME.search(name)):
        return "team", name, addr
    return "person", name, addr


def _own_addresses(pid: int) -> set[str]:
    """The student's own addresses: their own emails (a reply they cc'd themselves on) are never a recruiter."""
    from . import store
    p = store.get_profile(pid) or {}
    own = {(p.get("profile") or {}).get("email") or ""}
    own |= {r["address"] or "" for r in db.rows("SELECT address FROM email_accounts WHERE profile_id=?", (pid,))}
    return {a.strip().lower() for a in own if a and "@" in a}


def record(pid: int, *, from_name: str, from_addr: str, company: str, body: str, subject: str, status: str,
           job_id: str | None, when: int, source: str) -> list[int]:
    """Add or refresh the people behind one recruiting email. Returns the contact ids touched."""
    kind, name, addr = _sender(from_name, from_addr)
    if addr and addr in _own_addresses(pid):
        return []
    sig = signature(body)
    company = (company or sig["company"] or "").strip()
    if company and company_key(company) in {company_key(sig["name"]), company_key(_person_name(name))} - {""}:
        company = sig["company"]  # "Maria Gomez via Greenhouse" read as the company: it's the person
    # the From name is the person unless it's really the company or a team ("Northwind Robotics", "RBC Careers")
    named = _person_name(name) if name and company_key(name) != company_key(company) else ""
    people = []
    if kind == "person":
        people.append({"key": addr, "kind": "person", "email": addr,
                       "name": named or sig["name"] or (name if company_key(name) != company_key(company) else ""),
                       **{k: sig[k] for k in ("title", "phone", "linkedin")}})
    elif kind == "team":
        people.append({"key": addr, "kind": "team", "name": (name if not named else "") or addr, "email": addr,
                       "title": "", "phone": "", "linkedin": ""})
    # an email from a hiring system or a shared inbox that a person signed ("Best, Priya" or "Priya via Greenhouse");
    # their reply-to is the shared inbox, if there is one
    who = sig["name"] or (named if kind == "team" or re.search(r"\svia\s", from_name or "", re.I) else "")
    if kind != "person" and who:
        people.append({"key": f"name:{company_key(company)}:{who.lower()}", "kind": "person", "name": who,
                       "email": addr if kind == "team" else "", **{k: sig[k] for k in ("title", "phone", "linkedin")}})
    ids = []
    for p in people:
        ids.append(_upsert(pid, p, company, subject, status, job_id, when, source))
    return ids


def _upsert(pid: int, p: dict, company: str, subject: str, status: str, job_id: str | None, when: int,
            source: str) -> int:
    now = db.now()
    with db.tx() as c:
        old = c.execute("SELECT * FROM contacts WHERE profile_id=? AND key=?", (pid, p["key"])).fetchone()
        if not old:
            return c.execute(
                "INSERT INTO contacts (profile_id, key, kind, name, email, title, phone, linkedin, company, company_key,"
                " first_seen, last_seen, emails, last_subject, last_status, job_ids, source, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
                (pid, p["key"], p["kind"], p["name"] or None, p["email"] or None, p["title"] or None,
                 p["phone"] or None, p["linkedin"] or None, company or None, company_key(company) or None, when, when,
                 subject or None, status or None, db.dumps([job_id] if job_id else []), source, now)).lastrowid
        old = dict(old)
        edited = set(db.loads(old["edited"], []) or [])
        sets = {"emails": (old["emails"] or 0) + 1, "first_seen": min(old["first_seen"] or when, when),
                "last_seen": max(old["last_seen"] or when, when)}
        if when >= (old["last_seen"] or 0):
            sets.update(last_subject=subject or old["last_subject"], last_status=status or old["last_status"])
        for k in ("name", "title", "phone", "linkedin"):  # fill what's missing; never undo a hand edit
            if p.get(k) and k not in edited and not old[k]:
                sets[k] = p[k]
        if company and "company" not in edited and not old["company"]:
            sets.update(company=company, company_key=company_key(company))
        jobs = db.loads(old["job_ids"], []) or []
        if job_id and job_id not in jobs:
            sets["job_ids"] = db.dumps(jobs + [job_id])
        c.execute("UPDATE contacts SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?", (*sets.values(), old["id"]))
        return old["id"]


def backfill(pid: int) -> int:
    """Once per person: contacts from the recruiting emails read before the Recruiters tab existed (their stored
    first lines rarely include a sign-off, so mostly names and addresses)."""
    flag = f"contacts_backfilled:{pid}"
    if db.meta_get(flag):
        return 0
    rows = db.rows("SELECT * FROM email_events WHERE profile_id=? AND ignored=0 ORDER BY received_at", (pid,))
    for r in rows:
        try:
            record(pid, from_name=r["from_name"] or "", from_addr=r["from_addr"] or "", company=r["company"] or "",
                   body=r["snippet"] or "", subject=r["subject"] or "", status=r["status"] or "", job_id=r["job_id"],
                   when=r["received_at"] or r["created_at"], source="pasted" if r["classified_by"] == "pasted" else "inbox")
        except Exception as e:  # one odd row must not stop the rest
            log.warning("contact from email %s skipped: %s", r["id"], e)
    db.meta_set(flag, "1")
    return len(rows)


def _industry(row: dict) -> str:
    tier = companies.classify(row["company"] or "")[0] if row["company"] else "other"
    return tier


SOURCES = {"recruiters": ("inbox", "pasted"), "linkedin": ("linkedin",), "you": ("you",)}


def listing(pid: int, q: str = "", industry: str = "", teams: bool = False, starred: bool = False,
            hidden: bool = False, source: str = "", role: str = "", startups: bool = False, hiring: bool = False,
            min_funding: int = 0, yc: bool = False, min_team: int = 0) -> dict:
    """Contacts grouped by kind of company, then company. Newest contact first within each. `source`: recruiters
    (from emails), linkedin (your imported connections) or you (added by hand)."""
    backfill(pid)
    rows = db.rows("SELECT * FROM contacts WHERE profile_id=? ORDER BY last_seen DESC", (pid,))
    by_source = {k: sum(1 for r in rows if r["source"] in v and not r["hidden"]) for k, v in SOURCES.items()}
    if source in SOURCES:
        rows = [r for r in rows if r["source"] in SOURCES[source]]
    sig = signals(pid, {r["company_key"]: r["company"] for r in rows if r["company_key"]})
    by_role: dict[str, int] = {}
    for r in rows:
        r["role"] = role_of(r["title"], r["kind"])
        if not r["hidden"]:
            by_role[r["role"]] = by_role.get(r["role"], 0) + 1
    filtering = startups or hiring or min_funding or yc or min_team
    rows = [r for r in rows if (not role or r["role"] == role)
            and (not filtering or (r["company_key"] in sig and _passes(sig[r["company_key"]], startups, hiring,
                                                                        min_funding, yc, min_team)))]
    apps = {a["job_id"]: a for a in db.rows("SELECT job_id, status, company, title FROM applications WHERE profile_id=?",
                                            (pid,))}
    ql = q.lower().strip()
    shown, counts = [], {}
    for r in rows:
        r["industry"] = _industry(r)
        r["applications"] = [{"job_id": j, "title": apps[j]["title"], "status": apps[j]["status"]}
                             for j in db.loads(r["job_ids"], []) or [] if j in apps and apps[j].get("title")]
        if bool(r["hidden"]) != hidden or (r["kind"] == "team" and not teams) or (starred and not r["starred"]):
            continue
        if ql and ql not in " ".join(str(r[k] or "") for k in ("name", "email", "company", "title", "notes")).lower():
            continue
        counts[r["industry"]] = counts.get(r["industry"], 0) + 1
        if industry and r["industry"] != industry:
            continue
        shown.append(r)
    groups: dict[str, dict] = {}
    for r in shown:
        g = groups.setdefault(r["industry"], {"industry": r["industry"], "label": companies.TIERS.get(r["industry"],
                                                                                                         "Other"),
                                              "companies": {}})
        co = g["companies"].setdefault(r["company_key"] or "", {"company": r["company"] or "Company not known",
                                                                "signals": sig.get(r["company_key"]), "people": []})
        co["people"].append({k: r[k] for k in ("id", "kind", "name", "email", "title", "phone", "linkedin", "company",
                                               "first_seen", "last_seen", "emails", "last_subject", "last_status",
                                               "source", "notes", "starred", "hidden", "applications", "role")})
    order = list(companies.TIERS)
    out = sorted(groups.values(), key=lambda g: order.index(g["industry"]) if g["industry"] in order else 99)
    for g in out:
        g["companies"] = sorted(g["companies"].values(), key=lambda c: -max(p["last_seen"] or 0 for p in c["people"]))
    people = sum(1 for r in rows if r["kind"] == "person" and not r["hidden"])
    return {"groups": out, "industries": [{"industry": k, "label": companies.TIERS.get(k, "Other"), "count": n}
                                          for k, n in sorted(counts.items(), key=lambda kv: order.index(kv[0])
                                                             if kv[0] in order else 99)],
            "total": len(shown), "people": people,
            "companies": len({r["company_key"] for r in rows if r["company_key"] and not r["hidden"]}),
            "teams": sum(1 for r in rows if r["kind"] == "team" and not r["hidden"]),
            "hidden": sum(1 for r in rows if r["hidden"]), "sources": by_source, "roles": by_role,
            "role_labels": [{"role": k, "label": lbl} for k, lbl, _ in ROLES],
            "funding_check": dict(FUNDING_STATE.get(pid) or {}),
            "inbox_connected": bool(db.row("SELECT 1 FROM email_accounts WHERE profile_id=?", (pid,)))}


def _clean(fields: dict) -> dict:
    out = {}
    for k in FIELDS:
        if k in fields:
            v = re.sub(r"[\x00-\x1f\x7f]", " ", str(fields[k] or "")).strip()[:500 if k == "notes" else 200]
            out[k] = v or None
    if out.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", out["email"]):
        raise ValueError("that email address doesn't look right")
    if out.get("linkedin") and not re.match(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/", out["linkedin"], re.I):
        raise ValueError("the LinkedIn link should be a linkedin.com address")
    if out.get("linkedin") and not out["linkedin"].lower().startswith("http"):
        out["linkedin"] = "https://" + out["linkedin"]
    return out


def add(pid: int, fields: dict) -> dict:
    """Someone you met or found yourself."""
    f = _clean(fields)
    if not f.get("name") and not f.get("email"):
        return {"error": "add at least a name or an email address"}
    key = (f.get("email") or "").lower() or f"name:{company_key(f.get('company') or '')}:{(f.get('name') or '').lower()}"
    if db.row("SELECT 1 FROM contacts WHERE profile_id=? AND key=?", (pid, key)):
        return {"error": "they're already in your list"}
    now = db.now()
    with db.tx() as c:
        cid = c.execute("INSERT INTO contacts (profile_id, key, kind, name, email, title, phone, linkedin, company,"
                        " company_key, notes, first_seen, last_seen, emails, source, edited, created_at)"
                        " VALUES (?,?,'person',?,?,?,?,?,?,?,?,?,?,0,'you',?,?)",
                        (pid, key, f.get("name"), (f.get("email") or "").lower() or None, f.get("title"), f.get("phone"),
                         f.get("linkedin"), f.get("company"), company_key(f.get("company") or "") or None,
                         f.get("notes"), now, now, db.dumps(sorted(f)), now)).lastrowid
    return {"ok": True, "id": cid}


def update(pid: int, cid: int, fields: dict) -> dict:
    row = db.row("SELECT * FROM contacts WHERE id=? AND profile_id=?", (cid, pid))
    if not row:
        return {"error": "no such contact"}
    f = _clean(fields)
    sets = dict(f)
    if "company" in f:
        sets["company_key"] = company_key(f["company"] or "") or None
    for flag in ("starred", "hidden"):
        if flag in fields:
            sets[flag] = 1 if fields[flag] else 0
    if not sets:
        return {"ok": True}
    sets["edited"] = db.dumps(sorted(set(db.loads(row["edited"], []) or []) | set(f)))
    with db.tx() as c:
        c.execute("UPDATE contacts SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=? AND profile_id=?",
                  (*sets.values(), cid, pid))
    return {"ok": True}


def delete(pid: int, cid: int) -> dict:
    with db.tx() as c:
        n = c.execute("DELETE FROM contacts WHERE id=? AND profile_id=?", (cid, pid)).rowcount
    return {"ok": True} if n else {"error": "no such contact"}


def search(pid: int, q: str = "", company: str = "", limit: int = 12) -> list[dict]:
    """For the chat: people matching a name, company or note."""
    backfill(pid)
    ql, ck = q.lower().strip(), company_key(company)
    out = []
    for r in db.rows("SELECT * FROM contacts WHERE profile_id=? AND hidden=0 ORDER BY last_seen DESC", (pid,)):
        if ck and not (r["company_key"] or "").startswith(ck):
            continue
        if ql and ql not in " ".join(str(r[k] or "") for k in ("name", "email", "company", "title", "notes")).lower():
            continue
        out.append({k: r[k] for k in ("id", "kind", "name", "email", "title", "phone", "linkedin", "company",
                                      "last_seen", "last_subject", "last_status", "emails", "notes", "source")})
    return out[:limit]


def to_csv(pid: int) -> str:
    backfill(pid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Email", "Title", "Company", "Kind of company", "Phone", "LinkedIn", "Last contact",
                "Last email", "Emails", "Notes"])
    for r in db.rows("SELECT * FROM contacts WHERE profile_id=? AND hidden=0 ORDER BY company, name", (pid,)):
        last = datetime.date.fromtimestamp(r["last_seen"]).isoformat() if r["last_seen"] else ""
        w.writerow([_cell(x) for x in (r["name"], r["email"], r["title"], r["company"],
                                       companies.TIERS.get(_industry(r), "Other"), r["phone"], r["linkedin"], last,
                                       r["last_subject"], r["emails"], r["notes"])])
    return buf.getvalue()


def _cell(v) -> str:
    """Spreadsheet-safe: a cell that starts with = + - @ would run as a formula when opened."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


# --- who works where: your LinkedIn connections, and your school's alumni ---------------------------------------

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), 1)}
MAX_CONNECTIONS = 30000


def _connected_on(v: str) -> int | None:
    """LinkedIn writes "05 Oct 2025" (older exports: "10/5/25")."""
    v = (v or "").strip()
    m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]{3})\w* (\d{4})", v)
    try:
        if m and m.group(2).lower() in _MONTHS:
            d = datetime.date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        else:
            m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", v)
            if not m:
                return None
            y = int(m.group(3))
            d = datetime.date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return int(datetime.datetime.combine(d, datetime.time(12)).timestamp())


# --- one click: LinkedIn's export, found in Downloads ----------------------------------------------------------------
# LinkedIn emails a zip ("Basic_LinkedInDataExport_10-20-2026.zip") with Connections.csv inside. Most people save it to
# Downloads and stop there: the People tab offers to read it. Only that one file is read, here on this computer.
_EXPORT = re.compile(r"linkedin.*\.zip$|^connections.*\.csv$", re.I)
EXPORT_MAX = 12_000_000


def downloads_dir() -> Path:
    return Path(os.environ.get("COOPCOACH_DOWNLOADS") or Path.home() / "Downloads")


def _export_text(path: Path) -> str | None:
    if path.suffix.lower() == ".csv":
        return path.read_text(encoding="utf-8-sig", errors="replace") if path.stat().st_size <= EXPORT_MAX else None
    with zipfile.ZipFile(path) as z:
        info = next((i for i in z.infolist() if i.filename.rsplit("/", 1)[-1].lower() == "connections.csv"), None)
        if not info or info.file_size > EXPORT_MAX:  # (a zip that unpacks to something huge isn't LinkedIn's)
            return None
        return z.read(info).decode("utf-8-sig", errors="replace")


def find_export(pid: int, days: int = 60) -> dict | None:
    """The newest LinkedIn export in Downloads (last two months) with a connections list, unless it was imported or
    dismissed already."""
    folder = downloads_dir()
    try:
        files = sorted((f for f in folder.iterdir() if f.is_file() and _EXPORT.search(f.name)
                        and f.stat().st_mtime > db.now() - days * 86400), key=lambda f: -f.stat().st_mtime)
    except OSError:
        return None
    seen = set(db.loads(db.meta_get(f"linkedin_export_seen:{pid}"), []) or [])
    for f in files[:5]:
        key = f"{f.name}:{int(f.stat().st_mtime)}"
        if key in seen:
            return None  # the newest one was imported or waved off: don't keep asking
        try:
            text = _export_text(f)
        except (OSError, zipfile.BadZipFile):
            continue
        if text and "first name" in text.lower()[:4000]:
            rows = max(0, sum(1 for line in text.splitlines() if line.strip()) - 1)
            return {"name": f.name, "key": key, "modified": int(f.stat().st_mtime), "rows": rows}
    return None


def import_export(pid: int, key: str) -> dict:
    """Import the export find_export offered (by its key), and don't offer it again."""
    found = find_export(pid)
    if not found or found["key"] != key:
        return {"error": "That LinkedIn export isn't in Downloads any more."}
    out = import_linkedin(pid, _export_text(downloads_dir() / found["name"]) or "")
    if "error" not in out:
        dismiss_export(pid, key)
    return out


def dismiss_export(pid: int, key: str) -> None:
    seen = db.loads(db.meta_get(f"linkedin_export_seen:{pid}"), []) or []
    db.meta_set(f"linkedin_export_seen:{pid}", db.dumps((seen + [key])[-20:]))


def import_linkedin(pid: int, text: str) -> dict:
    """Your LinkedIn connections file (LinkedIn → Settings & Privacy → Data privacy → Get a copy of your data →
    Connections): who works where, as of your export. Importing again updates people who changed jobs, except what
    you've edited by hand, and someone already here from an email (same name, same company) gets their LinkedIn link
    rather than a second entry. Everything stays in your own database."""
    lines = (text or "").lstrip("\ufeff").splitlines()
    start = next((i for i, ln in enumerate(lines[:20]) if ln.lower().replace('"', "").startswith("first name")), None)
    if start is None:
        return {"error": "That isn't LinkedIn's Connections.csv: its table starts with First Name, Last Name, URL."}
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))[:MAX_CONNECTIONS]
    have = db.rows("SELECT id, key, name, company_key, linkedin, title, company, edited, source FROM contacts WHERE"
                   " profile_id=?", (pid,))
    by_key = {r["key"]: r for r in have}
    by_name = {((r["name"] or "").lower(), r["company_key"] or ""): r for r in have if r["name"]}
    added = updated = skipped = 0
    now = db.now()
    with db.tx() as c:
        for r in rows:
            name = " ".join(f"{r.get('First Name') or ''} {r.get('Last Name') or ''}".split())
            if not name:
                skipped += 1
                continue
            company = " ".join((r.get("Company") or "").split())[:200]
            title = " ".join((r.get("Position") or "").split())[:200]
            url = (r.get("URL") or "").strip()
            url = url if re.match(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/", url, re.I) else ""
            email = (r.get("Email Address") or "").strip().lower()
            email = email if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) else ""
            when = _connected_on(r.get("Connected On") or "") or now
            ck = company_key(company) or ""
            key = f"li:{url.lower().rstrip('/')}" if url else f"li:{ck}:{name.lower()}"
            old = by_key.get(key) or by_name.get((name.lower(), ck))
            if old:
                edited = set(db.loads(old["edited"], []) or [])
                sets = {}
                if url and not old["linkedin"] and "linkedin" not in edited:
                    sets["linkedin"] = url
                if old["source"] == "linkedin":  # people change jobs: the newest export wins, hand edits stay
                    if company and company != old["company"] and "company" not in edited:
                        sets.update(company=company, company_key=ck or None)
                    if title and title != old["title"] and "title" not in edited:
                        sets["title"] = title
                if sets:
                    c.execute("UPDATE contacts SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?",
                              (*sets.values(), old["id"]))
                    updated += 1
                continue
            c.execute("INSERT INTO contacts (profile_id, key, kind, name, email, title, linkedin, company, company_key,"
                      " first_seen, last_seen, emails, source, created_at) VALUES (?,?,'person',?,?,?,?,?,?,?,?,0,"
                      "'linkedin',?)", (pid, key, name[:200], email or None, title or None, url or None,
                                        company or None, ck or None, when, when, now))
            by_key[key] = {"id": None, "key": key, "name": name, "company_key": ck, "linkedin": url,
                           "title": title, "company": company, "edited": "[]", "source": "linkedin"}
            added += 1
    return {"ok": True, "added": added, "updated": updated, "skipped": skipped, "read": len(rows)}


def at_companies(pid: int) -> dict[str, int]:
    """company_key -> how many people you know there (any source)."""
    out: dict[str, int] = {}
    for r in db.rows("SELECT company_key FROM contacts WHERE profile_id=? AND hidden=0 AND kind='person' AND"
                     " company_key IS NOT NULL", (pid,)):
        out[r["company_key"]] = out.get(r["company_key"], 0) + 1
    return out


def alumni_search(pid: int, company: str) -> str:
    """LinkedIn's own search for people from your school at a company, opened in your browser (signed in as you)."""
    from urllib.parse import quote

    from . import store
    p = store.get_profile(pid) or {"profile": {}}
    uni = (p["profile"].get("university") or "").strip()
    if re.search(r"\bwaterloo\b", uni, re.I) and not re.search(r"laurier", uni, re.I):
        return f"https://www.linkedin.com/school/uwaterloo/people/?keywords={quote(company)}"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(' '.join(x for x in (company, uni) if x))}"


# --- who they are and what their company is: roles, startup signals, funding ----------------------------------------

ROLES = [("founder", "Founders & CEOs", r"\b(?:co-?)?found(?:er|ing)\b|\bowner\b|\bceo\b|\bchief executive\b"),
         ("recruiter", "Recruiters", r"recruit|talent|sourc(?:er|ing)|university relations|campus|people partner|"
                                     r"\bhr\b|human resources"),
         ("leader", "Leaders", r"\b(?:cto|cfo|coo|cpo|vp|svp|vice president|head of|director|chief|partner)\b"),
         ("engineer", "Engineers & researchers", r"engineer|developer|scientist|research|programmer|quant|architect|"
                                                  r"\bsre\b|devops|analyst"),
         ("product", "Product & design", r"product|design|\bux\b|\bui\b")]
_ROLE_RE = [(k, re.compile(rx, re.I)) for k, _, rx in ROLES]
FUNDING_STEPS = [1_000_000, 5_000_000, 20_000_000, 100_000_000]


def role_of(title: str, kind: str = "person") -> str:
    if kind == "team":
        return "recruiter"
    return next((k for k, rx in _ROLE_RE if rx.search(title or "")), "other")


def funding(keys: set[str] | None = None) -> dict[str, dict]:
    rows = db.rows("SELECT * FROM company_funding")
    return {r["company_key"]: {**r, "investors": db.loads(r["investors"], []), "sources": db.loads(r["sources"], [])}
            for r in rows if keys is None or r["company_key"] in keys}


def signals(pid: int, names: dict[str, str]) -> dict[str, dict]:
    """company_key -> what's known about the company: YC batch, team size and status (the YC directory), internships
    open to you now (your postings), what it has raised (checked funding), and whether it's a startup."""
    from . import store
    opened: dict[str, int] = {}
    tiers: dict[str, str] = {}
    for j in store.scored_jobs(pid):
        if j["company_key"] in names:
            tiers.setdefault(j["company_key"], j.get("tier") or "")
            if j["eligibility"] != "no":
                opened[j["company_key"]] = opened.get(j["company_key"], 0) + 1
    fund = funding(set(names))
    out = {}
    for ck, name in names.items():
        yc = companies.yc_lookup(name) or {}
        f = fund.get(ck)
        public = ((f or {}).get("last_round") or "").lower() in ("public", "acquired") or yc.get("status") in ("Public",
                                                                                                          "Acquired")
        startup = not public and (bool(yc) or tiers.get(ck) in ("yc", "startup") or bool(f and f["found"]))
        out[ck] = {"yc": yc.get("batch") or "", "team": yc.get("team_size") or None, "yc_hiring": bool(yc.get("is_hiring")),
                   "one_liner": yc.get("one_liner") or "", "open": opened.get(ck, 0), "startup": startup,
                   "funding": {k: f[k] for k in ("found", "total_usd", "last_round", "last_date", "investors",
                                                 "sources", "note", "checked_at")} if f else None}
    return out


def _passes(sig: dict, startups: bool, hiring: bool, min_funding: int, yc: bool, min_team: int) -> bool:
    if startups and not sig["startup"]:
        return False
    if hiring and not (sig["open"] or sig["yc_hiring"]):
        return False
    if yc and not sig["yc"]:
        return False
    if min_team and (sig["team"] or 0) < min_team:
        return False
    if min_funding:
        f = sig["funding"]
        if not (f and f["found"] and (f["total_usd"] or 0) >= min_funding):
            return False
    return True


def check_funding(company: str) -> dict:
    """Look a company's disclosed funding up with your own AI's web search and keep it (public facts)."""
    from . import ai
    name = (company or "").strip()[:120]
    ck = company_key(name)
    if not ck:
        return {"error": "which company?"}
    yc = companies.yc_lookup(name) or {}
    try:
        got = ai.company_funding(name, yc.get("website") or "")
    except Exception as e:
        return {"error": str(e)[:200]}
    with db.tx() as c:
        c.execute("INSERT INTO company_funding (company_key, name, found, total_usd, last_round, last_date, investors,"
                  " sources, note, checked_by, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(company_key) DO"
                  " UPDATE SET name=excluded.name, found=excluded.found, total_usd=excluded.total_usd,"
                  " last_round=excluded.last_round, last_date=excluded.last_date, investors=excluded.investors,"
                  " sources=excluded.sources, note=excluded.note, checked_by=excluded.checked_by,"
                  " checked_at=excluded.checked_at",
                  (ck, name, int(bool(got.get("found"))), max(0, int(got.get("total_raised_usd") or 0)) or None,
                   (got.get("last_round") or "")[:40] or None, (got.get("last_date") or got.get("last_round_date") or "")
                   [:10] or None, db.dumps((got.get("investors") or [])[:8]), db.dumps((got.get("sources") or [])[:5]),
                   (got.get("note") or "")[:300] or None, ai.provider(), db.now()))
    return {"ok": True, "company": name, **funding({ck})[ck]}


FUNDING_STATE: dict[int, dict] = {}


def check_founders_funding(pid: int, limit: int = 12) -> dict:
    """Check funding for the companies of founders you know that haven't been checked yet (one at a time; your AI
    searches the web for each). Run in a thread; FUNDING_STATE has progress."""
    st = FUNDING_STATE.get(pid) or {}
    if st.get("running"):
        return {"already_running": True}
    done = set(funding())
    todo, seen = [], set()
    for r in db.rows("SELECT name, title, kind, company, company_key FROM contacts WHERE profile_id=? AND hidden=0 AND"
                     " company_key IS NOT NULL ORDER BY last_seen DESC", (pid,)):
        if role_of(r["title"], r["kind"]) == "founder" and r["company_key"] not in done and r["company_key"] not in seen:
            seen.add(r["company_key"])
            todo.append(r["company"])
    todo = todo[:max(1, min(limit, 40))]
    FUNDING_STATE[pid] = {"running": True, "done": 0, "total": len(todo), "found": 0}
    try:
        for name in todo:
            out = check_funding(name)
            FUNDING_STATE[pid]["done"] += 1
            if out.get("found"):
                FUNDING_STATE[pid]["found"] += 1
            if out.get("error") and "needs your" in out["error"]:
                FUNDING_STATE[pid]["error"] = out["error"]
                break
    finally:
        FUNDING_STATE[pid]["running"] = False
    return dict(FUNDING_STATE[pid])
