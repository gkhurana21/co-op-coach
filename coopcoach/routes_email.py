"""Inbox routes: connecting Gmail / Outlook / any IMAP inbox, the found recruiting emails and corrections."""

import re
import threading
import time

import httpx
from fastapi import APIRouter, Body, HTTPException

from . import db, mail, vault

router = APIRouter()

_email_sync_running: set[int] = set()


def _start_email_sync(pid: int, blocking: bool = False) -> bool:
    if pid in _email_sync_running:
        return False
    _email_sync_running.add(pid)

    def run():
        try:
            mail.sync_all(pid)
        finally:
            _email_sync_running.discard(pid)

    if blocking:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()
    return True


@router.get("/api/email/accounts")
def email_accounts(profile_id: int):
    return db.rows("SELECT id, profile_id, kind, address, host, last_sync, last_status, created_at, enabled"
                   " FROM email_accounts WHERE profile_id=? ORDER BY id", (profile_id,))


@router.put("/api/email/accounts/{aid}")
def toggle_email_account(aid: int, body: dict = Body(...)):
    """The per-inbox "track emails" switch."""
    with db.tx() as c:
        c.execute("UPDATE email_accounts SET enabled=? WHERE id=?", (int(bool(body.get("enabled"))), aid))
    return {"ok": True}


@router.get("/api/email/provider")
def email_provider(address: str):
    return mail.detect_provider(address)


@router.put("/api/email/outlook/client-id")
def set_ms_client_id(body: dict = Body(...)):
    """Save the Microsoft app ID (one-time setup by whoever runs Co-op Coach) after Microsoft accepts it."""
    cid = (body.get("client_id") or "").strip()
    try:
        problem = mail.ms_check_client_id(cid)
    except httpx.HTTPError as e:
        raise HTTPException(400, f"Couldn't reach Microsoft to check it: {e}")
    if problem:
        raise HTTPException(400, problem)
    db.meta_set("ms_client_id", cid)
    return {"ok": True}


@router.post("/api/email/imap")
def add_imap(body: dict = Body(...)):
    pid = body.get("profile_id")
    address = (body.get("address") or "").strip().lower()
    password = (body.get("password") or "").replace(" ", "")
    if not pid or "@" not in address or not password:
        raise HTTPException(400, "email address and app password required")
    host = (body.get("host") or "").strip()
    prov = mail.detect_provider(address, lookup=not host)
    if prov["auth"] == "microsoft":
        raise HTTPException(400, "Microsoft (Outlook, Hotmail, school Microsoft 365) switched off passwords for other "
                                 "apps in 2024. Use “Sign in with Microsoft” instead.")
    if prov["auth"] == "unsupported":
        raise HTTPException(400, " ".join(prov.get("steps") or ["That provider doesn't allow other apps to read mail."]))
    host = host or prov.get("host")
    if not host:
        raise HTTPException(400, f"Enter the IMAP server for {prov['domain']} (often imap.{prov['domain']}).")
    port = int(body.get("port") or prov.get("port") or 993)
    username = address
    try:
        try:
            mail.imap_check(host, port, username, password)
        except Exception:
            if prov["id"] != "icloud":
                raise
            username = address.split("@")[0]  # some iCloud accounts sign in with just the name part
            mail.imap_check(host, port, username, password)
    except Exception as e:
        hint = {"gmail": " Gmail needs an app password, not your normal password: turn on 2-Step Verification, then "
                         "create one at myaccount.google.com/apppasswords.",
                "yahoo": " Yahoo needs an app password: Account Security → Generate app password.",
                "icloud": " iCloud needs an app-specific password from account.apple.com.",
                "aol": " AOL needs an app password: Account Security → Generate app password."}.get(prov["id"], "")
        raise HTTPException(400, f"Couldn't sign in to {host}: {e}.{hint}")
    stored = mail.save_secret(pid, address, password)
    with db.tx() as c:
        c.execute("INSERT INTO email_accounts (profile_id, kind, address, host, port, username, secret, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(profile_id, address) DO UPDATE SET secret=excluded.secret,"
                  " host=excluded.host, port=excluded.port, username=excluded.username, last_status=NULL, enabled=1",
                  (pid, "imap", address, host, port, username, stored, db.now()))
    _start_email_sync(pid)
    return {"ok": True}


_ms_flows: dict[str, dict] = {}


@router.post("/api/email/outlook/start")
def outlook_start(body: dict = Body(...)):
    pid = body.get("profile_id")
    if not mail.ms_client_id():
        raise HTTPException(400, "Microsoft sign-in needs a one-time app registration first (see the steps under "
                                 "“Sign in with Microsoft”).")
    try:
        flow = mail.ms_start_device_flow()
    except Exception as e:
        raise HTTPException(400, f"Microsoft sign-in failed to start: {e}")
    import secrets
    fid = secrets.token_urlsafe(8)
    _ms_flows[fid] = {"status": "pending", "profile_id": pid, "address": None, "error": None}

    def poll():
        interval = int(flow.get("interval", 5))
        deadline = time.time() + int(flow.get("expires_in", 900))
        while time.time() < deadline:
            time.sleep(interval)
            tok = mail.ms_poll_token(flow["device_code"])
            err = tok.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err:
                _ms_flows[fid].update(status="error", error=tok.get("error_description", err)[:300])
                return
            me = mail.ms_me(tok["access_token"])
            address = (me.get("mail") or me.get("userPrincipalName") or "").lower()
            stored = mail.save_secret(pid, address, tok["refresh_token"])
            with db.tx() as c:
                c.execute("INSERT INTO email_accounts (profile_id, kind, address, secret, created_at)"
                          " VALUES (?,?,?,?,?) ON CONFLICT(profile_id, address) DO UPDATE SET"
                          " secret=excluded.secret, kind='outlook', last_status=NULL",
                          (pid, "outlook", address, stored, db.now()))
            _ms_flows[fid].update(status="done", address=address)
            _start_email_sync(pid, blocking=True)
            return
        _ms_flows[fid].update(status="error", error="Sign-in code expired; try again.")

    threading.Thread(target=poll, daemon=True).start()
    return {"flow_id": fid, "user_code": flow["user_code"], "verification_uri": flow["verification_uri"],
            "message": flow.get("message", "")}


@router.post("/api/email/outlook-web/start")
def outlook_web_start(body: dict = Body(...)):
    """Open Outlook on the web in Co-op Coach's own window; the student signs in there (no app registration and no
    IT approval needed). Poll /api/email/outlook-web/{flow_id}."""
    from . import outlook_web
    pid, address = body.get("profile_id"), (body.get("address") or "").strip().lower()
    if not pid or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", address):
        raise HTTPException(400, "Type the Outlook address you want to connect first")
    return {"flow_id": outlook_web.connect(pid, address)}


@router.get("/api/email/outlook-web/{flow_id}")
def outlook_web_status(flow_id: str):
    from . import outlook_web
    f = outlook_web.flow(flow_id)
    if not f:
        raise HTTPException(404)
    return f


@router.get("/api/email/outlook/{flow_id}")
def outlook_status(flow_id: str):
    f = _ms_flows.get(flow_id)
    if not f:
        raise HTTPException(404)
    return f


@router.delete("/api/email/accounts/{aid}")
def delete_email_account(aid: int):
    a = db.row("SELECT * FROM email_accounts WHERE id=?", (aid,))
    if a and a["secret"] == vault.REF:
        vault.delete(vault.email_key(a["profile_id"], a["address"]))
    with db.tx() as c:
        c.execute("DELETE FROM email_accounts WHERE id=?", (aid,))
        c.execute("DELETE FROM meta WHERE key=?", (f"graph_delta:{aid}",))  # Microsoft's place in that inbox
    return {"ok": True}


@router.post("/api/email/sync")
def email_sync(body: dict = Body(...)):
    return {"started": _start_email_sync(body.get("profile_id"))}


@router.get("/api/email/resort")
def email_resort_preview(profile_id: int):
    """What today's rules would change in the emails already tracked; nothing changes until it's confirmed."""
    return mail.resort(profile_id)


@router.post("/api/email/resort")
def email_resort(body: dict = Body(...)):
    if body.get("profile_id") in _email_sync_running:
        raise HTTPException(409, "Your inbox is being checked right now. Try again when it finishes.")
    return mail.resort(body["profile_id"], apply=True)


@router.get("/api/email/events")
def email_events(profile_id: int, limit: int = 60):
    evs = db.rows("SELECT e.*, COALESCE(j.company, a.company) AS app_company, COALESCE(j.title, a.title) AS app_title"
                  " FROM email_events e LEFT JOIN applications a ON a.profile_id=e.profile_id AND a.job_id=e.job_id"
                  " LEFT JOIN jobs j ON j.id=e.job_id WHERE e.profile_id=? ORDER BY e.received_at DESC LIMIT ?",
                  (profile_id, limit))
    return {"events": evs, "syncing": profile_id in _email_sync_running}


@router.put("/api/email/events/{eid}")
def fix_email_event(eid: str, body: dict = Body(...)):
    ev = db.row("SELECT * FROM email_events WHERE id=?", (eid,))
    if not ev:
        raise HTTPException(404)
    if body.get("ignored"):  # not about a job: what it put on the calendar goes too
        from . import meetings
        meetings.forget(ev["profile_id"], ev["message_id"])
        meetings.forget(ev["profile_id"], eid)  # (a pasted email's items carry its own id)
    with db.tx() as c:
        if "ignored" in body:
            c.execute("UPDATE email_events SET ignored=? WHERE id=?", (1 if body["ignored"] else 0, eid))
        job_id = body.get("job_id") or ev["job_id"]
        status = body.get("status")
        if body.get("job_id") or status:
            c.execute("UPDATE email_events SET job_id=?, status=COALESCE(?, status), classified_by='you' WHERE id=?",
                      (job_id, status, eid))
    if (body.get("job_id") or status) and job_id:
        # a correction is authoritative: set the status directly (it may move backwards)
        s = status or ev["status"]
        with db.tx() as c:
            c.execute("INSERT INTO applications (profile_id, job_id, status, source, updated_at, last_email_at,"
                      " last_email_subject) VALUES (?,?,?,?,?,?,?) ON CONFLICT(profile_id, job_id) DO UPDATE SET"
                      " status=excluded.status, updated_at=excluded.updated_at",
                      (ev["profile_id"], job_id, s, "email", db.now(), ev["received_at"], ev["subject"]))
    return {"ok": True}
