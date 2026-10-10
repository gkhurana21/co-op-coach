"""ASGI middleware that enforces per-person passcodes on every API call.

It works out which profile a request touches (profile_id in the query or JSON body, or the owner of the letter /
login / email account / inbox item in the path) and answers 423 Locked if that person has a passcode and this
browser hasn't unlocked them.
"""

import json
import re
from urllib.parse import parse_qs

from . import auth, db

_OWNER_BY_PATH = [
    (re.compile(r"^/api/profiles/(\d+)(?:/|$)"), None),
    (re.compile(r"^/api/letters/(\d+)(?:/|$)"), "SELECT profile_id FROM documents WHERE id=?"),
    (re.compile(r"^/api/portals/(\d+)(?:/|$)"), "SELECT profile_id FROM portal_accounts WHERE id=?"),
    (re.compile(r"^/api/email/accounts/(\d+)$"), "SELECT profile_id FROM email_accounts WHERE id=?"),
    (re.compile(r"^/api/email/events/([\w-]+)$"), "SELECT profile_id FROM email_events WHERE id=?"),
]
_OPEN = {"/api/state", "/api/auth/unlock", "/api/auth/lock", "/api/resume/lint", "/api/resume/preview"}


def _owners(path: str, method: str, query: str, body: bytes, content_type: str) -> set[int]:
    pids: set[int] = set()
    q = parse_qs(query)
    if "profile_id" in q:
        try:
            pids.add(int(q["profile_id"][0]))
        except ValueError:
            pass
    if body and "json" in content_type:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and data.get("profile_id") is not None:
                pids.add(int(data["profile_id"]))
        except (ValueError, TypeError):
            pass
    for rx, sql in _OWNER_BY_PATH:
        m = rx.match(path)
        if m:
            if sql is None:
                pids.add(int(m.group(1)))
            else:
                r = db.row(sql, (m.group(1),))
                if r:
                    pids.add(r["profile_id"])
    return pids


def _cookie(headers: dict) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == auth.COOKIE:
            return v
    return None


class ProfileLock:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path in _OPEN:
            return await self.app(scope, receive, send)
        method = scope["method"]
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        body = b""
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            more = True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
        creating = method == "POST" and path == "/api/profiles"
        if not creating:
            token = _cookie(headers)
            for pid in _owners(path, method, scope.get("query_string", b"").decode(), body,
                               headers.get("content-type", "")):
                if not auth.is_unlocked(token, pid):
                    r = db.row("SELECT name FROM profiles WHERE id=?", (pid,))
                    payload = json.dumps({"detail": "locked", "locked": pid, "name": r["name"] if r else ""}).encode()
                    await send({"type": "http.response.start", "status": 423,
                                "headers": [(b"content-type", b"application/json"),
                                            (b"content-length", str(len(payload)).encode())]})
                    await send({"type": "http.response.body", "body": payload})
                    return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
