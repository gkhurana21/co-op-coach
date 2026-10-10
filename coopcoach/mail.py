"""Reads recruiting emails (Gmail/any IMAP, or Outlook via Microsoft Graph), works out which application
each one is about and what it means (received / online assessment / interview / offer / rejection), and
moves that application forward in the tracker.

Everything runs locally; message text only leaves your machine if you turn on `email.use_ai` in
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

from . import config, db, store, vault
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
    NOT_JOB,
    OA,
    OA_STRONG,
    OFFER,
    REJECT_STRONG,
    REJECT_WEAK,
    STATUS_RANK,
    _clean_company,
    _domain_company,
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
    find_req_ids,
    strip_html,
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
               "candidacy unfortunately hackerrank codesignal hirevue \"next steps\"}")


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
    return {
        "message_id": str(msg.get("Message-ID", "")).strip() or hashlib.sha1(raw[:2000]).hexdigest(),
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
            if uid and (JOBBY.search(text) or APPLIED.search(text) or OA.search(text) or
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


def graph_fetch(acct: dict, since_ts: int, max_messages: int = 400) -> list[dict]:
    current = account_secret(acct)
    tok = ms_refresh(current)
    if tok.get("refresh_token") and tok["refresh_token"] != current:
        stored = save_secret(acct["profile_id"], acct["address"], tok["refresh_token"])
        with db.tx() as c:
            c.execute("UPDATE email_accounts SET secret=? WHERE id=?", (stored, acct["id"]))
    headers = {"Authorization": f"Bearer {tok['access_token']}", "Prefer": 'outlook.body-content-type="text"'}
    since = datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = ("https://graph.microsoft.com/v1.0/me/messages"
           f"?$filter=receivedDateTime ge {since}&$orderby=receivedDateTime desc&$top=50"
           "&$select=id,internetMessageId,subject,from,receivedDateTime,body,webLink")
    out = []
    with httpx.Client(timeout=30, headers=headers) as c:
        while url and len(out) < max_messages:
            r = c.get(url)
            r.raise_for_status()
            data = r.json()
            for m in data.get("value", []):
                frm = (m.get("from") or {}).get("emailAddress") or {}
                if (frm.get("address") or "").lower() == acct["address"].lower():
                    continue
                out.append({
                    "message_id": m.get("internetMessageId") or m.get("id"),
                    "received_at": int(datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")).timestamp()),
                    "from_name": frm.get("name") or "",
                    "from_addr": (frm.get("address") or "").lower(),
                    "subject": m.get("subject") or "",
                    "body": ((m.get("body") or {}).get("content") or "")[:8000],
                    "web_link": m.get("webLink") or "",
                })
            url = data.get("@odata.nextLink")
    return out


# --- sync ------------------------------------------------------------------------------------

def _event_id(pid: int, message_id: str) -> str:
    return hashlib.sha1(f"{pid}|{message_id}".encode()).hexdigest()[:20]


def process_messages(pid: int, account_id: int | None, messages: list[dict]) -> dict:
    """Classify + link + update statuses. Returns counts."""
    seen = {r["id"] for r in db.rows("SELECT id FROM email_events WHERE profile_id=?", (pid,))}
    fresh = [m for m in messages if _event_id(pid, m["message_id"]) not in seen]
    fresh.sort(key=lambda m: m["received_at"])  # oldest first so statuses advance in order

    results: dict[str, dict | None] = {}
    by = "rules"
    if config.get("email.use_ai", False) and fresh:
        from . import ai
        if ai.available():
            try:
                results = ai.classify_emails(fresh)
                by = ai.provider()
            except Exception as e:
                log.warning("AI email classification failed, using rules: %s", e)
                results = {}
    stats = {"scanned": len(messages), "job_emails": 0, "status_changes": 0}
    for m in fresh:
        eid = _event_id(pid, m["message_id"])
        res = results[m["message_id"]] if m["message_id"] in results else \
            classify(m["subject"], m["from_name"], m["from_addr"], m["body"])
        if not res:
            continue
        stats["job_emails"] += 1
        req_ids = find_req_ids(f"{m['subject']}\n{m['body'][:8000]}")
        job_id = match_by_req(pid, req_ids, res["company"]) or match_application(pid, res["company"], res["role"])
        changed = bool(job_id) and apply_status(pid, job_id, res["status"], m["received_at"], m["subject"])
        remember_req(pid, job_id, req_ids)
        stats["status_changes"] += int(changed)
        snippet = re.sub(r"\s+", " ", m["body"])[:400]
        with db.tx() as c:
            c.execute("INSERT OR IGNORE INTO email_events (id, profile_id, account_id, message_id, received_at,"
                      " from_name, from_addr, subject, snippet, web_link, status, company, role, job_id,"
                      " classified_by, changed_status, created_at, req_ids)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, pid, account_id, m["message_id"], m["received_at"], m["from_name"], m["from_addr"],
                       m["subject"], snippet, m["web_link"], res["status"], res["company"], res["role"], job_id,
                       by, int(changed), db.now(), ", ".join(req_ids[:3]) or None))
    store.refresh_scores()
    return stats


def sync_account(acct: dict) -> dict:
    lookback = int(config.get("email.lookback_days", 120))
    since = (acct["last_sync"] - 2 * 86400) if acct["last_sync"] else int(time.time() - lookback * 86400)
    try:
        msgs = graph_fetch(acct, since) if acct["kind"] == "outlook" else imap_fetch(acct, since)
        stats = process_messages(acct["profile_id"], acct["id"], msgs)
        status = f"ok · {stats['job_emails']} job emails, {stats['status_changes']} updates"
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
    sql, params = "SELECT * FROM email_accounts WHERE enabled=1", ()
    if profile_id:
        sql, params = sql + " AND profile_id=?", (profile_id,)
    return {a["address"]: sync_account(a) for a in db.rows(sql, params) if tracking_on(a["profile_id"])}


# --- stats ---------------------------------------------------------------------------------

def stats(pid: int) -> dict:
    counts = {s: 0 for s in STATUS_RANK}
    for r in db.rows("SELECT status, COUNT(*) AS n FROM applications WHERE profile_id=? GROUP BY status", (pid,)):
        counts[r["status"]] = r["n"]
    applied_total = sum(v for k, v in counts.items() if k != "saved")
    responded = counts["oa"] + counts["interviewing"] + counts["offer"] + counts["rejected"]
    stale = db.row("SELECT COUNT(*) AS n FROM applications WHERE profile_id=? AND status='applied'"
                   " AND COALESCE(applied_at, updated_at) < ?", (pid, db.now() - 21 * 86400))["n"]
    return {
        **counts,
        "applied_total": applied_total,
        "awaiting": counts["applied"],
        "in_progress": counts["oa"] + counts["interviewing"],
        "response_rate": round(100 * responded / applied_total) if applied_total else 0,
        "stale": stale,
    }
