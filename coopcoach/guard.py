"""ASGI middleware that guards the API.

ProfileLock enforces per-person passcodes on every API call. It works out which profile a request touches
(profile_id in the query or JSON body, or the owner of the letter / login / email account / inbox item in the path)
and answers 423 Locked if that person has a passcode and this browser hasn't unlocked them. An id it can't read
exactly the way the routes will (profile_id given twice, "2.0", "+2", "/api/letters/1.0") is refused with 400
instead of skipped, so the guard and the route can never disagree about whose data a request touches.

SameOrigin refuses changes (POST / PUT / PATCH / DELETE) that another website makes the browser send here.
"""

import json
import re
from urllib.parse import parse_qs

from . import auth, db, extension_auth

# (path, who owns the item it names, whether that item's id is a number)
_OWNER_BY_PATH = [
    (re.compile(r"^/api/profiles/([^/]+)(?:/|$)"), None, True),
    (re.compile(r"^/api/letters/([^/]+)(?:/|$)"), "SELECT profile_id FROM documents WHERE id=?", True),
    (re.compile(r"^/api/email/accounts/([^/]+)(?:/|$)"), "SELECT profile_id FROM email_accounts WHERE id=?", True),
    (re.compile(r"^/api/email/events/([^/]+)(?:/|$)"), "SELECT profile_id FROM email_events WHERE id=?", False),
    (re.compile(r"^/api/recruiters/([^/]+)(?:/|$)"), "SELECT profile_id FROM contacts WHERE id=?", True),
]
# /api/agent/call answers a run's own ticket (which is the profile and its tools), not a browser's unlock cookie
_OPEN = {"/api/state", "/api/auth/unlock", "/api/auth/lock", "/api/resume/lint", "/api/resume/preview", "/api/agent/call"}
_OPEN.update(extension_auth.CLIENT_PATHS)  # each handler validates the ticket and its paired unlocking session
_ID = re.compile(r"0|[1-9][0-9]{0,17}")  # a plain whole number written one way only, small enough for the database
_PAGE = re.compile(r"[a-z]+(?:-[a-z]+)*")  # /api/letters/formats: a page, not an id
_CHANGES = {"POST", "PUT", "PATCH", "DELETE"}


class BadId(ValueError):
    """An id the routes might read differently from the guard."""


def _id(v) -> int:
    """A profile or item id from the query string, a JSON body or the path."""
    if isinstance(v, str) and _ID.fullmatch(v):
        return int(v)
    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2 ** 63:
        return v
    raise BadId(repr(v))


def _owners(path: str, method: str, query: str, body: bytes, content_type: str) -> set[int]:
    pids: set[int] = set()
    given = parse_qs(query, keep_blank_values=True).get("profile_id", [])
    if len(given) > 1:  # the routes read the last one
        raise BadId("profile_id given more than once")
    pids.update(_id(v) for v in given)
    if body and "json" in content_type.lower():  # the routes read "Application/JSON" too
        try:
            data = json.loads(body)
        except ValueError:
            data = None  # the route can't read it either
        if isinstance(data, dict) and data.get("profile_id") is not None:
            pids.add(_id(data["profile_id"]))
    for rx, sql, numeric in _OWNER_BY_PATH:
        m = rx.match(path)
        if not m:
            continue
        key = m.group(1)
        if numeric:
            if _PAGE.fullmatch(key):
                continue
            key = _id(key)
        if sql is None:
            pids.add(key)
        else:
            r = db.row(sql, (key,))
            if r:
                pids.add(r["profile_id"])
    return pids


def _cookie(headers: dict) -> str | None:
    for part in headers.get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == auth.COOKIE:
            return v
    return None


def _headers(scope) -> dict:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


async def _reply(send, status: int, data: dict) -> None:
    payload = json.dumps(data).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})


class ProfileLock:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path in _OPEN:
            return await self.app(scope, receive, send)
        method = scope["method"]
        headers = _headers(scope)
        body = b""
        if method in _CHANGES:
            more = True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
        creating = method == "POST" and path == "/api/profiles"
        if not creating:
            token = _cookie(headers)
            try:
                owners = _owners(path, method, scope.get("query_string", b"").decode(), body,
                                 headers.get("content-type", ""))
            except BadId:
                return await _reply(send, 400, {"detail": "That profile or item id isn't valid. "
                                                          "Send it once, as a plain whole number."})
            for pid in owners:
                if not auth.is_unlocked(token, pid):
                    r = db.row("SELECT name FROM profiles WHERE id=?", (pid,))
                    return await _reply(send, 423, {"detail": "locked", "locked": pid, "name": r["name"] if r else ""})
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _cross_site(scope) -> bool:
    """The browser says this came from another website: its Origin isn't this app's own address (the one in the
    address bar, so sharing on your Wi-Fi with `serve --host 0.0.0.0` still works), or it's marked cross-site.
    Requests with no Origin (curl, scripts) weren't sent by a web page. Origin "null" is refused too: keep the app's
    pages off Referrer-Policy: no-referrer, which makes a browser send "null" even to its own site."""
    headers = _headers(scope)
    if headers.get("sec-fetch-site", "").lower() == "cross-site":
        return True
    origin = headers.get("origin")
    own = f"{scope.get('scheme', 'http')}://{headers.get('host', '')}"
    return origin is not None and origin.lower() != own.lower()


class SameOrigin:
    """Any web page can make the browser send a form, a file upload or an empty POST to this app without asking.
    Changes like these from another site are refused (403)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = _headers(scope)
        path = scope.get("path", "")
        extension = (path in extension_auth.CLIENT_PATHS and
                     extension_auth.ORIGIN.fullmatch(headers.get("origin", "")))
        if extension and scope.get("method") == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": [
                (b"access-control-allow-origin", headers["origin"].encode()),
                (b"access-control-allow-methods", b"POST"),
                (b"access-control-allow-headers", b"Authorization, Content-Type"), (b"vary", b"Origin")]})
            return await send({"type": "http.response.body", "body": b""})
        if (scope["type"] == "http" and scope["method"] in _CHANGES and scope.get("path", "").startswith("/api/")
                and _cross_site(scope) and not extension_auth.allowed(headers, path)):
            return await _reply(send, 403, {"detail": "Changes sent from another website aren't allowed."})

        async def reply(message):
            if extension and message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"access-control-allow-origin", headers["origin"].encode()), (b"vary", b"Origin")]
            await send(message)
        await self.app(scope, receive, reply)
