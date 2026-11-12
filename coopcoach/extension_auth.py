"""Session-only Chrome pairing. No passcode or persistent bearer secret leaves the app.

Pairing codes and the link to the unlocking browser session live only in this process.
The extension keeps its ticket in chrome.storage.session, inaccessible to content scripts.
Restarting either side requires reconnecting; revoking a database ticket takes effect at once.
"""

import re
import secrets
import threading
import time

from fastapi import HTTPException, Request

from . import auth, db

ORIGIN = re.compile(r"chrome-extension://[a-p]{32}")
CODE_SECONDS = 300
TICKET_SECONDS = 12 * 3600
_codes: dict[str, dict] = {}
_connections: dict[str, dict] = {}
_lock = threading.RLock()
CLIENT_PATHS = {"/api/extension/pair", "/api/extension/today", "/api/extension/context",
                "/api/extension/answers", "/api/extension/files", "/api/extension/submitted",
                "/api/extension/disconnect-self", "/api/extension/inbox-plan", "/api/extension/inbox"}


def origin(request: Request) -> str:
    value = request.headers.get("origin", "")
    if not ORIGIN.fullmatch(value):
        raise HTTPException(403, "Only the paired Chrome extension can use this endpoint")
    return value


def _bearer(headers) -> str:
    return headers.get("authorization", "").removeprefix("Bearer ").strip()


def allowed(headers: dict, path: str) -> bool:
    """The CSRF exception is confined to this API and this connection's extension origin."""
    value = headers.get("origin", "")
    if path not in CLIENT_PATHS or not ORIGIN.fullmatch(value):
        return False
    if path == "/api/extension/pair":
        return True  # the handler requires an unguessable single-use code from an unlocked app
    with _lock:
        c = _connections.get(auth._token_hash(_bearer(headers)))
        return bool(c and c["origin"] == value and c["until"] > time.time())


def new_code(pid: int, session: str | None) -> dict:
    if not db.row("SELECT id FROM profiles WHERE id=?", (pid,)):
        raise HTTPException(404, "No such profile")
    if not auth.is_unlocked(session, pid):
        raise HTTPException(423, "Unlock your profile in the app first")
    code = secrets.token_urlsafe(24)
    with _lock:
        for key in [k for k, v in _codes.items() if v["until"] < time.time() or v["pid"] == pid]:
            _codes.pop(key, None)
        _codes[auth._token_hash(code)] = {"pid": pid, "session": session, "until": time.time() + CODE_SECONDS}
    return {"code": code, "expires_in": CODE_SECONDS}


def pair(code: str, extension_origin: str) -> dict:
    if not ORIGIN.fullmatch(extension_origin):
        raise HTTPException(403, "Invalid extension origin")
    with _lock:
        key = auth._token_hash(code)
        c = _codes.get(key)
        if not c or c["until"] < time.time():
            raise HTTPException(401, "That pairing code expired or was already used. Get a new one in Settings.")
        if not auth.is_unlocked(c["session"], c["pid"], touch=False):
            raise HTTPException(423, "Unlock your profile in the app first")
        ticket = auth.issue_ticket(c["pid"], ttl=TICKET_SECONDS, label="Chrome extension")
        _connections[auth._token_hash(ticket)] = {**c, "origin": extension_origin,
                                                 "until": time.time() + TICKET_SECONDS}
        del _codes[key]
    return {"ticket": ticket, "profile": db.row("SELECT id, name FROM profiles WHERE id=?", (c["pid"],)),
            "expires_in": TICKET_SECONDS}


def require(request: Request, *, touch: bool = False) -> int:
    value = origin(request)
    token = _bearer(request.headers)
    with _lock:
        c = _connections.get(auth._token_hash(token))
        if not c or c["origin"] != value or c["until"] <= time.time():
            raise HTTPException(401, "Reconnect Chrome from Settings in the app")
        pid = auth.check_ticket(token)
        if not pid or pid != c["pid"]:
            raise HTTPException(401, "This connection was revoked. Reconnect from Settings.")
        if not auth.is_unlocked(c["session"], pid, touch=touch):
            raise HTTPException(423, "Your profile is locked. Unlock it in the app, then try again.")
        return pid


def disconnect(pid: int) -> None:
    with _lock:
        keys = [k for k, c in _connections.items() if c["pid"] == pid]
        for key in keys:
            _connections.pop(key, None)
        for key in [k for k, c in _codes.items() if c["pid"] == pid]:
            _codes.pop(key, None)
    with db.tx() as c:
        c.execute("DELETE FROM agent_tickets WHERE profile_id=? AND label='Chrome extension'", (pid,))
