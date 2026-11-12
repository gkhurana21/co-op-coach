"""Reads recruiting emails (Gmail/any IMAP, or Outlook via Microsoft Graph), works out which application
each one is about and what it means (received / online assessment / interview / offer / rejection), and
moves that application forward in the tracker.

Everything runs locally unless an AI engine is connected (`email.use_ai: auto`, the default) or `email.use_ai` is true in
config.yaml (then Claude classifies the emails instead of the built-in rules).
"""

import email
import email.policy
import email.utils
import hashlib
import imaplib
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from . import config, contacts, db, meetings, store, vault
from .mail_rules import (  # noqa: F401 (moved; still reachable here)
    _BAD_COMPANY,
    _DUE,
    _GENERIC_EDGE,
    _GREETING,
    _MONTHS,
    _NAME_NOISE,
    _P,
    _ROLE,
    _ROLE_WORD,
    _SIGNATURE,
    _SUBJ_COMPANY,
    _TEAM_WORDS,
    _TZ,
    _WHEN,
    ALERT_SENDERS,
    APPLIED,
    ATS_DOMAINS,
    GENERIC_DOMAINS,
    INTERVIEW,
    INTERVIEW_STRONG,
    JOBBY,
    LINK_KINDS,
    NEWS_NAME,
    NOT_JOB,
    OA,
    OA_STRONG,
    OFFER,
    REJECT_STRONG,
    REJECT_WEAK,
    STATUS_RANK,
    _clean_company,
    _domain_company,
    _named_like_domain,
    _rank,
    _same_company,
    _sim,
    _valid_company,
    apply_status,
    classify,
    email_links,
    email_when,
    match_application,
    match_by_req,
    read_pasted,
    remember_req,
    signature_company,
)
from .textutil import (
    company_key,
    find_req_ids,
    strip_html,
    title_key,
)

log = logging.getLogger(__name__)


# --- secrets -------------------------------------------------------------------------------

def account_secret(acct: dict) -> str:
    if acct.get("secret") == vault.REF:
        secret = vault.get(vault.email_key(acct["profile_id"], acct["address"]))
        if not secret:
            raise RuntimeError("couldn't read this account's password from the Keychain; reconnect it")
        return secret
    return acct.get("secret") or ""


def save_secret(profile_id: int, address: str, secret: str) -> str:
    """Store in the keychain when possible; returns what to put in the DB column."""
    if vault.available():
        vault.put(vault.email_key(profile_id, address), secret)
        return vault.REF
    return secret


def migrate_secrets() -> int:
    """Move any secrets saved by older versions out of the database and into the keychain."""
    if not vault.available():
        return 0
    moved = 0
    for a in db.rows("SELECT * FROM email_accounts WHERE secret IS NOT NULL AND secret != ?", (vault.REF,)):
        vault.put(vault.email_key(a["profile_id"], a["address"]), a["secret"])
        with db.tx() as c:
            c.execute("UPDATE email_accounts SET secret=? WHERE id=?", (vault.REF, a["id"]))
        moved += 1
    return moved


# --- providers ----------------------------------------------------------------------------

GMAIL_QUERY = ("-in:sent -category:promotions {application applying applied interview assessment offer candidate "
               "candidacy unfortunately hackerrank codesignal hirevue \"next steps\" \"coffee chat\" coffee invitation "
               "\"invited you\" \"info session\" rsvp \"career fair\" \"phone screen\" filename:ics}")


def _parse_message(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = email.utils.parseaddr(str(msg.get("From", "")))
    try:
        when = int(email.utils.parsedate_to_datetime(str(msg.get("Date"))).timestamp())
    except (TypeError, ValueError, IndexError):
        when = int(time.time())
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        try:
            content = part.get_content()
            body = strip_html(content) if part.get_content_type() == "text/html" else content
        except (LookupError, UnicodeDecodeError):
            body = ""
    ics = ""  # a calendar invite (an interview or coffee chat someone sent from their calendar)
    for part in msg.walk():
        if part.get_content_type() in ("text/calendar", "application/ics") or \
                (part.get_filename() or "").lower().endswith(".ics"):
            try:
                data = part.get_payload(decode=True) or b""
                ics += data.decode(part.get_content_charset() or "utf-8", "replace") + "\n"
            except (LookupError, ValueError):
                continue
    return {
        "message_id": str(msg.get("Message-ID", "")).strip() or hashlib.sha1(raw[:2000]).hexdigest(),
        "ics": ics[:20000],
        "received_at": when,
        "from_name": name,
        "from_addr": addr.lower(),
        "subject": str(msg.get("Subject", "")).strip(),
        "body": re.sub(r"\s+\n", "\n", body or "")[:8000],
        "web_link": "",
    }


def imap_fetch(acct: dict, since_ts: int, max_messages: int = 400) -> list[dict]:
    host = acct["host"] or "imap.gmail.com"
    gmail = "gmail" in host or "googlemail" in host
    M = imaplib.IMAP4_SSL(host, int(acct["port"] or 993), timeout=45)
    try:
        M.login(acct["username"] or acct["address"], account_secret(acct))
        if gmail:
            M.select('"[Gmail]/All Mail"', readonly=True)
            days = max(1, int((time.time() - since_ts) / 86400) + 1)
            typ, data = M.uid("SEARCH", "X-GM-RAW", f'"newer_than:{days}d {GMAIL_QUERY.replace(chr(34), "")}"')
        else:
            M.select("INBOX", readonly=True)
            since = time.strftime("%d-%b-%Y", time.gmtime(since_ts))
            typ, data = M.uid("SEARCH", None, "SINCE", since)
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {data}")
        uids = (data[0] or b"").split()
        if not gmail and uids:
            uids = _prefilter_headers(M, uids[-3000:])
        uids = uids[-max_messages:]
        out = []
        for i in range(0, len(uids), 25):
            chunk = b",".join(uids[i:i + 25]).decode()
            fetch_items = "(X-GM-MSGID BODY.PEEK[])" if gmail else "(BODY.PEEK[])"
            typ, resp = M.uid("FETCH", chunk, fetch_items)
            if typ != "OK":
                continue
            for item in resp:
                if not isinstance(item, tuple):
                    continue
                meta, raw = item
                m = _parse_message(raw)
                gm = re.search(rb"X-GM-MSGID (\d+)", meta)
                if gm:
                    m["web_link"] = f"https://mail.google.com/mail/u/0/#all/{int(gm.group(1)):x}"
                if m["from_addr"] == acct["address"].lower():
                    continue
                out.append(m)
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


# a meeting someone set up: worth reading for the calendar even when it's not about an application
MEETING = _P(r"coffee|invitation|invited you|meeting|info session|rsvp|career fair|phone screen|\bchat\b|\bcall\b")


def _prefilter_headers(M, uids: list[bytes]) -> list[bytes]:
    keep = []
    for i in range(0, len(uids), 200):
        typ, resp = M.uid("FETCH", b",".join(uids[i:i + 200]).decode(), "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
        if typ != "OK":
            continue
        for item in resp:
            if not isinstance(item, tuple):
                continue
            uid = re.search(rb"UID (\d+)", item[0])
            hdr = email.message_from_bytes(item[1], policy=email.policy.default)
            text = f"{hdr.get('Subject', '')} {hdr.get('From', '')}"
            if uid and (JOBBY.search(text) or APPLIED.search(text) or OA.search(text) or MEETING.search(text) or
                        any(d in text.lower() for d in ATS_DOMAINS)):
                keep.append(uid.group(1))
    return keep


def imap_check(host: str, port: int, username: str, password: str) -> None:
    M = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        M.login(username, password)
    finally:
        try:
            M.logout()
        except Exception:
            pass


# Microsoft (Outlook.com, Hotmail, Microsoft 365 school/work accounts) via device-code sign-in.
MS_AUTH = "https://login.microsoftonline.com/common/oauth2/v2.0"
MS_SCOPE = "offline_access User.Read Mail.Read"


def ms_client_id() -> str:
    """Microsoft app ID: MS_CLIENT_ID in .env, else email.microsoft_client_id in config.yaml (shareable: it isn't a
    secret), else one pasted into the app."""
    import os
    return (os.environ.get("MS_CLIENT_ID") or config.get("email.microsoft_client_id", "") or
            db.meta_get("ms_client_id") or "").strip()


_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def ms_check_client_id(cid: str) -> str | None:
    """None if Microsoft accepts this app ID for device sign-in, else a readable reason."""
    if not _GUID.match(cid):
        return "That isn't an Application (client) ID; it looks like 1a2b3c4d-…-…-…-… (36 characters)."
    r = httpx.post(f"{MS_AUTH}/devicecode", data={"client_id": cid, "scope": MS_SCOPE}, timeout=20)
    if r.status_code == 200:
        return None
    err = r.json().get("error_description", r.text) if r.headers.get("content-type", "").startswith("application/json") \
        else r.text
    if "AADSTS700016" in err or "AADSTS50059" in err:
        return ("Microsoft can't find an app with that ID for all account types. Check you copied the Application "
                "(client) ID, and that Supported account types is “Accounts in any organizational directory and "
                "personal Microsoft accounts”.")
    if "AADSTS50194" in err:
        return "The app only allows one organization: change Supported account types to include personal accounts."
    if "AADSTS7000218" in err or "public client" in err.lower():
        return "Turn on “Allow public client flows” (Authentication page) for the app, save, and try again."
    return err.split("\r\n")[0][:240]


# --- email providers --------------------------------------------------------------------------------
# How each provider lets a third-party app read mail. App passwords: Gmail (needs 2-Step Verification), Yahoo,
# iCloud, AOL, Fastmail, Zoho. Microsoft (Outlook.com, Hotmail, Live, MSN, school/work Microsoft 365) switched off
# passwords in September 2024, so it needs Microsoft's own sign-in.

PROVIDERS = {
    "gmail": {"name": "Gmail", "auth": "app_password", "host": "imap.gmail.com",
              "domains": ["gmail.com", "googlemail.com"],
              "steps": ["Turn on 2-Step Verification for your Google account "
                        "(myaccount.google.com/signinoptions/twosv).",
                        "Create an app password named “Co-op Coach” at myaccount.google.com/apppasswords.",
                        "Paste the 16-letter code below. It can only read mail, and you can revoke it any time."],
              "link": "https://myaccount.google.com/apppasswords"},
    "yahoo": {"name": "Yahoo", "auth": "app_password", "host": "imap.mail.yahoo.com",
              "domains": ["yahoo.com", "yahoo.ca", "yahoo.co.uk", "yahoo.co.in", "ymail.com", "rocketmail.com"],
              "steps": ["Open Yahoo Account Security (login.yahoo.com/account/security).",
                        "Click “Generate app password”, name it “Co-op Coach”, then Generate.",
                        "Paste that password below (not your normal Yahoo password)."],
              "link": "https://login.yahoo.com/account/security"},
    "icloud": {"name": "iCloud", "auth": "app_password", "host": "imap.mail.me.com",
               "domains": ["icloud.com", "me.com", "mac.com"],
               "steps": ["Go to account.apple.com → Sign-In and Security → App-Specific Passwords.",
                         "Create one named “Co-op Coach” (needs two-factor authentication on your Apple Account).",
                         "Paste it below."],
               "link": "https://account.apple.com"},
    "aol": {"name": "AOL", "auth": "app_password", "host": "imap.aol.com", "domains": ["aol.com", "aim.com"],
            "steps": ["Open AOL Account Security (login.aol.com/account/security).",
                      "Click “Generate app password”, name it “Co-op Coach”.", "Paste it below."],
            "link": "https://login.aol.com/account/security"},
    "fastmail": {"name": "Fastmail", "auth": "app_password", "host": "imap.fastmail.com",
                 "domains": ["fastmail.com", "fastmail.fm"],
                 "steps": ["Fastmail Settings → Privacy & Security → Manage app passwords → New app password.",
                           "Give it IMAP access, then paste it below."], "link": "https://app.fastmail.com/settings"},
    "zoho": {"name": "Zoho Mail", "auth": "app_password", "host": "imap.zoho.com",
             "domains": ["zoho.com", "zohomail.com"],
             "steps": ["Turn on IMAP in Zoho Mail settings.",
                       "accounts.zoho.com → Security → App Passwords → Generate, then paste it below."],
             "link": "https://accounts.zoho.com"},
    "microsoft": {"name": "Outlook / Hotmail", "auth": "microsoft",
                  "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com", "outlook.ca", "hotmail.ca",
                              "live.ca", "hotmail.co.uk", "passport.com"]},
    "school": {"name": "School / work Microsoft 365", "auth": "microsoft", "domains": ["uwaterloo.ca"]},
    "proton": {"name": "Proton Mail", "auth": "unsupported", "domains": ["proton.me", "protonmail.com", "pm.me"],
               "steps": ["Proton only allows other apps in through Proton Mail Bridge (paid plans), which Co-op "
                         "Coach doesn't support yet. Use a separate Gmail for applications instead."]},
}


def detect_provider(address: str, lookup: bool = True) -> dict:
    """Which provider hosts this address, and how to connect it. For unknown domains it asks DNS (Google's public
    resolver) for the mail servers and Thunderbird's provider database for IMAP settings; only the domain is sent."""
    domain = address.strip().lower().rpartition("@")[2]
    for pid, p in PROVIDERS.items():
        if domain in p["domains"]:
            return {"id": pid, "domain": domain, **{k: v for k, v in p.items() if k != "domains"}}
    out = {"id": "other", "name": domain or "Other", "auth": "app_password", "host": "", "domain": domain,
           "steps": ["Create an app password in your email account's security settings if it offers one "
                     "(otherwise use your normal password), and enter its IMAP server below."]}
    if not lookup or "." not in domain:
        return out
    try:
        mx = httpx.get("https://dns.google/resolve", params={"name": domain, "type": "MX"}, timeout=6).json()
        hosts = " ".join(a.get("data", "") for a in mx.get("Answer") or []).lower()
    except (httpx.HTTPError, ValueError):
        hosts = ""
    if "google.com" in hosts or "googlemail.com" in hosts:  # Google Workspace
        return {**PROVIDERS["gmail"], "id": "gmail", "domain": domain, "name": f"Google ({domain})",
                "steps": PROVIDERS["gmail"]["steps"] + ["School/work Google accounts: your admin must allow app "
                                                        "passwords; if the page says they're unavailable, they don't."]}
    if "outlook.com" in hosts or "office365" in hosts:  # Microsoft 365
        return {"id": "school", "domain": domain, "name": f"Microsoft 365 ({domain})", "auth": "microsoft"}
    try:
        r = httpx.get(f"https://autoconfig.thunderbird.net/v1.1/{domain}", timeout=6)
        m = re.search(r'<incomingServer type="imap">.*?<hostname>([^<]+)</hostname>.*?<port>(\d+)</port>.*?'
                      r'<socketType>SSL</socketType>', r.text, re.S) if r.status_code == 200 else None
        if m:
            out.update(host=m.group(1).replace("%EMAILDOMAIN%", domain), port=int(m.group(2)))
    except httpx.HTTPError:
        pass
    return out


def ms_start_device_flow() -> dict:
    r = httpx.post(f"{MS_AUTH}/devicecode", data={"client_id": ms_client_id(), "scope": MS_SCOPE}, timeout=20)
    r.raise_for_status()
    return r.json()


def ms_poll_token(device_code: str) -> dict:
    r = httpx.post(f"{MS_AUTH}/token", data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                             "client_id": ms_client_id(), "device_code": device_code}, timeout=20)
    return r.json()


def ms_refresh(refresh_token: str) -> dict:
    r = httpx.post(f"{MS_AUTH}/token", data={"grant_type": "refresh_token", "client_id": ms_client_id(),
                                             "refresh_token": refresh_token, "scope": MS_SCOPE}, timeout=20)
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(data.get("error_description") or data.get("error") or "token refresh failed")
    return data


def ms_me(access_token: str) -> dict:
    r = httpx.get("https://graph.microsoft.com/v1.0/me", headers={"Authorization": f"Bearer {access_token}"},
                  timeout=20)
    r.raise_for_status()
    return r.json()


GRAPH = "https://graph.microsoft.com/v1.0"
_GRAPH_SELECT = "id,internetMessageId,subject,from,receivedDateTime,bodyPreview,webLink"


def _graph_token(acct: dict) -> str:
    current = account_secret(acct)
    tok = ms_refresh(current)
    if tok.get("refresh_token") and tok["refresh_token"] != current:
        stored = save_secret(acct["profile_id"], acct["address"], tok["refresh_token"])
        with db.tx() as c:
            c.execute("UPDATE email_accounts SET secret=? WHERE id=?", (stored, acct["id"]))
    return tok["access_token"]


def _graph_worth_reading(m: dict) -> bool:
    """From the subject, sender and Graph's 255-character preview: could this be about an application? Only those get
    their whole body fetched. The same signals the IMAP header prefilter uses, plus the preview."""
    frm = (m.get("from") or {}).get("emailAddress") or {}
    addr = (frm.get("address") or "").lower()
    text = f"{m.get('subject') or ''} {frm.get('name') or ''} {addr} {m.get('bodyPreview') or ''}"
    if ALERT_SENDERS.search(addr) or NEWS_NAME.search(frm.get("name") or ""):
        return False
    return bool(JOBBY.search(text) or APPLIED.search(text) or OA.search(text) or MEETING.search(text)
                or any(addr.endswith(d) for d in ATS_DOMAINS))


def graph_fetch(acct: dict, since_ts: int, max_messages: int = 400, client: httpx.Client | None = None) -> list[dict]:
    """New mail through Microsoft Graph's delta query: the first check lists the inbox back to since_ts, later checks
    get only what arrived since the last one (Microsoft keeps the place), so a check is one or two requests. Each
    email's date is Microsoft's receivedDateTime. Bodies are fetched only for emails that look job-related, 20 to a
    request ($batch)."""
    token = _graph_token(acct)
    key = f"graph_delta:{acct['id']}"
    since = datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = (f"{GRAPH}/me/mailFolders/inbox/messages/delta?$select={_GRAPH_SELECT}"
             f"&$filter=receivedDateTime+ge+{since}")
    own = acct["address"].lower()
    headers = {"Authorization": f"Bearer {token}", "Prefer": "odata.maxpagesize=100"}
    c = client or httpx.Client(timeout=30)
    try:
        listed, delta, url, restarted = [], None, db.meta_get(key) or first, False
        while url:
            r = c.get(url, headers=headers)
            if r.status_code in (400, 404, 410) and not restarted and url != first:  # Microsoft forgot our place
                listed, url, restarted = [], first, True
                continue
            r.raise_for_status()
            data = r.json()
            listed += [m for m in data.get("value", []) if "@removed" not in m]
            url, delta = data.get("@odata.nextLink"), data.get("@odata.deltaLink") or delta
        wanted = [m for m in listed if ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower() != own
                  and _graph_worth_reading(m)]
        wanted.sort(key=lambda m: m.get("receivedDateTime") or "", reverse=True)
        wanted = wanted[:max_messages]
        bodies: dict[str, str] = {}
        for i in range(0, len(wanted), 20):
            reqs = [{"id": str(n), "method": "GET", "url": f"/me/messages/{m['id']}?$select=body",
                     "headers": {"Prefer": 'outlook.body-content-type="text"'}} for n, m in enumerate(wanted[i:i + 20])]
            r = c.post(f"{GRAPH}/$batch", json={"requests": reqs}, headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            for resp in r.json().get("responses", []):
                if resp.get("status") == 200:
                    bodies[wanted[i + int(resp["id"])]["id"]] = ((resp.get("body") or {}).get("body") or {}).get("content") or ""
        out = []
        for m in wanted:
            frm = (m.get("from") or {}).get("emailAddress") or {}
            out.append({
                "message_id": m.get("internetMessageId") or m.get("id"),
                "received_at": int(datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")).timestamp()),
                "from_name": frm.get("name") or "",
                "from_addr": (frm.get("address") or "").lower(),
                "subject": m.get("subject") or "",
                "body": (bodies.get(m["id"]) or m.get("bodyPreview") or "")[:8000],
                "web_link": m.get("webLink") or "",
                "graph_id": m["id"],
            })
        if delta:
            db.meta_set(key, delta)  # saved only after a whole pass, so a failed check repeats rather than skips
        return out
    finally:
        if client is None:
            c.close()


# --- sync ------------------------------------------------------------------------------------

def _event_id(pid: int, message_id: str) -> str:
    return hashlib.sha1(f"{pid}|{message_id}".encode()).hexdigest()[:20]


def ai_sorting() -> bool:
    """email.use_ai: "auto" (the default) sorts with the AI engine whenever one is connected, the rules deciding when
    none is; true or false forces it."""
    v = config.get("email.use_ai", "auto")
    if str(v).lower() in ("true", "on", "yes"):
        return True
    if str(v).lower() in ("false", "off", "no"):
        return False
    from . import ai
    return ai.available()


def process_messages(pid: int, account_id: int | None, messages: list[dict]) -> dict:
    """Classify + link + update statuses. Returns counts."""
    seen = {r["id"] for r in db.rows("SELECT id FROM email_events WHERE profile_id=?", (pid,))}
    fresh = [m for m in messages if _event_id(pid, m["message_id"]) not in seen]
    fresh.sort(key=lambda m: m["received_at"])  # oldest first so statuses advance in order

    results: dict[str, dict | None] = {}
    by = "rules"
    for_ai = [m for m in fresh if not _not_job_signal(m)]  # newsletters and alerts never go to the AI
    if for_ai and ai_sorting():
        from . import ai
        if ai.available():
            try:
                results = ai.classify_emails(for_ai)
                by = ai.provider()
            except Exception as e:
                log.warning("AI email classification failed, using rules: %s", e)
                results = {}
    stats = {"scanned": len(messages), "job_emails": 0, "status_changes": 0}
    if fresh:  # recruiters from emails read before that tab existed, before these add their own (none counts twice)
        try:
            contacts.backfill(pid)
        except Exception as e:
            log.warning("couldn't add recruiters from earlier emails: %s", e)
    for m in fresh:
        eid = _event_id(pid, m["message_id"])
        res = results[m["message_id"]] if m["message_id"] in results else \
            classify(m["subject"], m["from_name"], m["from_addr"], m["body"])
        if res and res["company"] and m["from_name"] and res["company"].lower() in (
                m["from_name"].lower(), m["from_name"].split(" ")[0].lower()):
            # "Sam" (who wrote it) isn't the company: the sign-off's company, or the sender's own domain, is
            better = signature_company(m["body"]) or _named_like_domain(m["from_addr"], m["body"]) or \
                _domain_company(m["from_addr"]).title()
            if better and better.lower() != res["company"].lower():  # ("Scotiabank Careers" is Scotiabank already)
                res["company"] = better
        kind = meetings.kind_of(m["subject"], m["body"], (res or {}).get("status"), bool(m.get("ics")))
        if kind in meetings.NETWORKING:  # a coffee chat or an info session: on the calendar, not the tracker
            res = None
        if not res:
            try:
                items = meetings.extract(m["subject"], m["body"], m.get("ics", ""), m["received_at"]) if kind else []
                if items:
                    meetings.save(pid, items, None, _sender_company(m), _who(m), m["message_id"], m["subject"],
                                  m.get("web_link") or "", "inbox")
                    stats["meetings"] = stats.get("meetings", 0) + len(items)
            except Exception as e:
                log.warning("couldn't put %s on the calendar: %s", m["subject"], e)
            continue
        stats["job_emails"] += 1
        req_ids = find_req_ids(f"{m['subject']}\n{m['body'][:8000]}")
        job_id = match_by_req(pid, req_ids, res["company"]) or match_application(pid, res["company"], res["role"])
        changed = bool(job_id) and apply_status(pid, job_id, res["status"], m["received_at"], m["subject"])
        remember_req(pid, job_id, req_ids)
        stats["status_changes"] += int(changed)
        try:  # an interview time or an assessment deadline goes on the calendar too
            items = meetings.extract(m["subject"], m["body"], m.get("ics", ""), m["received_at"], res["status"])
            if items:
                meetings.save(pid, items, job_id, res["company"], _who(m), m["message_id"], m["subject"],
                              m.get("web_link") or "", "inbox")
                stats["meetings"] = stats.get("meetings", 0) + len(items)
        except Exception as e:
            log.warning("couldn't put %s on the calendar: %s", m["subject"], e)
        snippet = re.sub(r"\s+", " ", m["body"])[:400]
        with db.tx() as c:
            c.execute("INSERT OR IGNORE INTO email_events (id, profile_id, account_id, message_id, received_at,"
                      " from_name, from_addr, subject, snippet, web_link, status, company, role, job_id,"
                      " classified_by, changed_status, created_at, req_ids)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, pid, account_id, m["message_id"], m["received_at"], m["from_name"], m["from_addr"],
                       m["subject"], snippet, m["web_link"], res["status"], res["company"], res["role"], job_id,
                       by, int(changed), db.now(), ", ".join(req_ids[:3]) or None))
        try:  # who wrote it goes to Recruiters; a contact problem must never stop the inbox scan
            contacts.record(pid, from_name=m["from_name"], from_addr=m["from_addr"], company=res["company"],
                            body=m["body"], subject=m["subject"], status=res["status"], job_id=job_id,
                            when=m["received_at"], source="inbox")
        except Exception as e:
            log.warning("couldn't save the sender of %s as a contact: %s", m["subject"], e)
    store.refresh_scores()
    return stats


def _not_job_signal(ev: dict) -> bool:
    """Plainly not about an application: an alert or newsletter sender, a news-desk name, a job-alert subject. Only
    these clear a stored email; anything subtler is left alone (stored emails keep just a 400-character snippet)."""
    return bool(ALERT_SENDERS.search(ev.get("from_addr") or "") or NOT_JOB.search(ev.get("subject") or "")
                or NEWS_NAME.search(ev.get("from_name") or ""))


def resort(pid: int, apply: bool = False) -> dict:
    """Run today's rules over the emails already in the tracker. Preview (apply=False) says what would change:
    - "not_job": newsletters and alerts that were taken for job emails go (with what they put on the calendar);
    - "relink": an email whose company or role now reads differently is filed again (one Mercor role per
      application, Shopify for earlytalent@shopify.com);
    - "remove": an application made only from such emails, and left with none, goes.
    Emails you corrected yourself are never touched."""
    evs = db.rows("SELECT * FROM email_events WHERE profile_id=? AND ignored=0 AND COALESCE(classified_by, '') <> 'you'"
                  " ORDER BY received_at", (pid,))
    changes, touched = [], set()
    for ev in evs:
        label = f"{ev['company'] or '?'} · {ev['role'] or 'role from email'} · {ev['status']}"
        if _not_job_signal(ev):
            changes.append({"id": ev["id"], "kind": "not_job", "subject": ev["subject"], "was": label})
            touched.add(ev["job_id"])
            continue
        new = classify(ev["subject"] or "", ev["from_name"] or "", ev["from_addr"] or "", ev["snippet"] or "")
        if not new or not new.get("company"):
            continue
        co_moved = company_key(new["company"]) != company_key(ev["company"] or "")
        role_moved = bool(new.get("role")) and title_key(new["role"]) != title_key(ev["role"] or "")
        if co_moved or role_moved:
            changes.append({"id": ev["id"], "kind": "relink", "subject": ev["subject"], "was": label,
                            "now": f"{new['company']} · {new.get('role') or ev['role'] or 'role from email'}"})
            touched.add(ev["job_id"])
    moving = {c["id"] for c in changes}
    removals = []
    for job_id in sorted(j for j in touched if j and j.startswith("ext:")):
        app = db.row("SELECT company, title, status, source FROM applications WHERE profile_id=? AND job_id=?",
                     (pid, job_id))
        left = db.rows("SELECT id FROM email_events WHERE profile_id=? AND job_id=? AND ignored=0", (pid, job_id))
        if app and app["source"] == "email" and all(r["id"] in moving for r in left):
            removals.append({"job_id": job_id, "kind": "remove", "was": f"{app['company']} · {app['title']} · {app['status']}"})
    out = {"changes": changes, "removals": removals, "applied": False}
    if not apply or not (changes or removals):
        return out
    by_id = {e["id"]: e for e in evs}
    redo = []
    for ch in changes:
        ev = by_id[ch["id"]]
        meetings.forget(pid, ev["message_id"])
        meetings.forget(pid, ev["id"])
        with db.tx() as c:
            if ch["kind"] == "not_job":
                c.execute("UPDATE email_events SET ignored=1 WHERE id=?", (ev["id"],))
            else:
                c.execute("DELETE FROM email_events WHERE id=?", (ev["id"],))
        if ch["kind"] == "relink":
            redo.append({"message_id": ev["message_id"], "received_at": ev["received_at"], "from_name": ev["from_name"] or "",
                         "from_addr": ev["from_addr"] or "", "subject": ev["subject"] or "", "body": ev["snippet"] or "",
                         "web_link": ev["web_link"] or ""})
    with db.tx() as c:
        for r in removals:
            c.execute("DELETE FROM applications WHERE profile_id=? AND job_id=?", (pid, r["job_id"]))
    if redo:
        process_messages(pid, None, redo)
    out["applied"] = True
    return out


def repair_scan_dates(pid: int, dry_run: bool = False) -> dict:
    """Undo what the old Outlook-window reader got wrong. It dated an email by the reading pane's time, which was
    often the moment of the scan, and read with older rules (newsletters taken for interviews, Mercor roles merged).
    Those emails are dropped here, with any application made only from them, and forgotten as read, so the next
    check opens them again: dated by their own row and sorted by today's rules. A copy goes to data/backups first.
    Emails the student corrected by hand are kept."""
    import json
    from pathlib import Path
    bad = [e for e in db.rows("SELECT * FROM email_events WHERE profile_id=? AND message_id LIKE 'owa:%'", (pid,))
           if abs((e["received_at"] or 0) - (e["created_at"] or 0)) < 300 and (e["classified_by"] or "") != "you"]
    if not bad:
        return {"emails": 0, "applications": 0}
    ids = {e["id"] for e in bad}
    apps = []
    for job_id in {e["job_id"] for e in bad if e["job_id"]}:
        others = [r for r in db.rows("SELECT id FROM email_events WHERE profile_id=? AND job_id=?", (pid, job_id))
                  if r["id"] not in ids]
        app = db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, job_id))
        if app and app["source"] == "email" and job_id.startswith("ext:") and not others:
            apps.append(app)
    out = {"emails": len(bad), "applications": len(apps),
           "subjects": [e["subject"] for e in bad], "removed": [f"{a['company']} · {a['title']}" for a in apps]}
    if dry_run:
        return out
    folder = Path(config.DATA_DIR) / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"outlook-repair-{pid}-{int(time.time())}.json").write_text(
        json.dumps({"email_events": bad, "applications": apps}, default=str, indent=1))
    for e in bad:
        meetings.forget(pid, e["message_id"])
        meetings.forget(pid, e["id"])
    with db.tx() as c:
        for e in bad:
            c.execute("DELETE FROM email_events WHERE id=?", (e["id"],))
        for a in apps:
            c.execute("DELETE FROM applications WHERE profile_id=? AND job_id=?", (pid, a["job_id"]))
    for acct_id in {e["account_id"] for e in bad if e["account_id"]}:  # read them again at the next check
        key = f"owa_seen:{acct_id}"
        gone = {e["message_id"].removeprefix("owa:") for e in bad}
        db.meta_set(key, " ".join(x for x in (db.meta_get(key) or "").split() if x not in gone))
    out["backup"] = str(folder)
    return out


def _who(m: dict) -> str:
    return f"{m['from_name']} <{m['from_addr']}>" if m.get("from_name") else (m.get("from_addr") or "")


def _sender_company(m: dict) -> str:
    """Who a networking email is with: the sign-off's company, else the sender's own domain (never Gmail)."""
    c = signature_company(m.get("body") or "") or _named_like_domain(m.get("from_addr") or "", m.get("body") or "")
    if c:
        return c
    dom = (m.get("from_addr") or "").rsplit("@", 1)[-1].lower()
    if dom and not re.search(r"gmail|outlook|hotmail|yahoo|icloud|proton|live\.|msn\.|aol\.|me\.com|calendar|google",
                             dom):
        return dom.split(".")[-2].capitalize() if dom.count(".") >= 1 else ""
    return ""


def sync_account(acct: dict, budget: float | None = None) -> dict:
    if acct["kind"] == "outlook_web" and not db.meta_get(f"owa_repaired:{acct['profile_id']}"):
        try:  # once: emails the old reader dated by the scan are read again, properly
            repair_scan_dates(acct["profile_id"])
        except Exception as e:
            log.warning("couldn't repair old Outlook dates: %s", e)
        db.meta_set(f"owa_repaired:{acct['profile_id']}", "1")
    lookback = int(config.get("email.lookback_days", 120))
    floor = int(time.time() - lookback * 86400)
    since = (acct["last_sync"] - 2 * 86400) if acct["last_sync"] else floor
    try:
        if acct["kind"] == "outlook_web":  # signed in to Outlook on the web in Co-op Coach's own window
            from . import outlook_web
            # always the whole window back: what was read is remembered by id, so this only opens what wasn't, and an
            # earlier check that ran out of time is carried on from where it stopped
            msgs = outlook_web.fetch(acct, floor, **({"budget": budget} if budget else {}))
        else:
            msgs = graph_fetch(acct, since) if acct["kind"] == "outlook" else imap_fetch(acct, since)
        stats = process_messages(acct["profile_id"], acct["id"], msgs)
        status = f"ok · {stats['job_emails']} job emails, {stats['status_changes']} updates"
        if acct["kind"] == "outlook_web":
            st = outlook_web.state(acct["id"])
            if st.get("back_to"):
                status += (" · read back to " + time.strftime("%b %d", time.localtime(st["back_to"]))
                           + ("" if st.get("finished") else ", more at the next check"))
    except Exception as e:
        log.warning("email sync failed for %s: %s", acct["address"], e)
        stats, status = {"error": str(e)[:200]}, f"error: {str(e)[:160]}"
        with db.tx() as c:
            c.execute("UPDATE email_accounts SET last_status=? WHERE id=?", (status, acct["id"]))
        return stats
    with db.tx() as c:
        c.execute("UPDATE email_accounts SET last_sync=?, last_status=? WHERE id=?", (db.now(), status, acct["id"]))
    return stats


def tracking_on(profile_id: int) -> bool:
    p = db.row("SELECT prefs_json FROM profiles WHERE id=?", (profile_id,))
    return bool(p) and (db.loads(p["prefs_json"], {}) or {}).get("track_email", True) is not False


def sync_all(profile_id: int | None = None) -> dict:
    """Scan every connected inbox whose "track emails" switches are on (the inbox's own and the person's)."""
    sql, params = "SELECT * FROM email_accounts WHERE enabled=1 AND kind <> 'outlook_tab'", ()  # (the extension reads that one)
    if profile_id:
        sql, params = sql + " AND profile_id=?", (profile_id,)
    return {a["address"]: sync_account(a) for a in db.rows(sql, params) if tracking_on(a["profile_id"])}


# --- stats ---------------------------------------------------------------------------------

def stats(pid: int) -> dict:
    counts = {s: 0 for s in STATUS_RANK}
    # internships only: hackathon applications (hack:...) are on the tracker but aren't job applications
    for r in db.rows("SELECT status, COUNT(*) AS n FROM applications WHERE profile_id=? AND job_id NOT LIKE 'hack:%'"
                     " GROUP BY status", (pid,)):
        counts[r["status"]] = r["n"]
    applied_total = sum(v for k, v in counts.items() if k != "saved")
    responded = counts["oa"] + counts["interviewing"] + counts["offer"] + counts["rejected"]
    stale = db.row("SELECT COUNT(*) AS n FROM applications WHERE profile_id=? AND status='applied' AND job_id NOT LIKE"
                   " 'hack:%' AND COALESCE(applied_at, updated_at) < ?", (pid, db.this_hour() - 21 * 86400))["n"]
    return {
        **counts,
        "applied_total": applied_total,
        "awaiting": counts["applied"],
        "in_progress": counts["oa"] + counts["interviewing"],
        "response_rate": round(100 * responded / applied_total) if applied_total else 0,
        "stale": stale,
    }
