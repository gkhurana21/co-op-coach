"""Per-person passcodes.

* Passcodes are stored as salted PBKDF2-SHA256 hashes, never in plain text.
* Unlocking gives this browser an in-memory session (HttpOnly, SameSite=Strict cookie). Restarting the app,
  pressing Lock, or `security.auto_lock_minutes` of inactivity locks it again.
* One person is unlocked per browser at a time: unlocking someone else locks the previous person.
* Wrong guesses are throttled (5 free tries, then an increasing wait).

This keeps people who share a laptop (or the app on your Wi-Fi) out of each other's resume, tracker and saved
logins. It is not disk encryption: anyone logged into your Mac account can still read the database file.
"""

import hashlib
import hmac
import secrets
import threading
import time

from . import config, db

COOKIE = "cc_session"
ITERATIONS = 240_000
MIN_LENGTH = 4

_sessions: dict[str, dict] = {}   # token -> {"pid": int | None, "seen": float}
_failures: dict[int, dict] = {}   # pid -> {"count": int, "until": float}
_lock = threading.Lock()


def _hash(code: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, ITERATIONS).hex()


def has_passcode(pid: int) -> bool:
    r = db.row("SELECT pass_hash FROM profiles WHERE id=?", (pid,))
    return bool(r and r["pass_hash"])


def set_passcode(pid: int, code: str) -> None:
    if len(code or "") < MIN_LENGTH:
        raise ValueError(f"passcode must be at least {MIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    with db.tx() as c:
        c.execute("UPDATE profiles SET pass_hash=?, pass_salt=? WHERE id=?", (_hash(code, salt), salt.hex(), pid))


def clear_passcode(pid: int) -> None:
    with db.tx() as c:
        c.execute("UPDATE profiles SET pass_hash=NULL, pass_salt=NULL WHERE id=?", (pid,))
    with _lock:
        _failures.pop(pid, None)


def wait_seconds(pid: int) -> int:
    f = _failures.get(pid)
    return max(0, int(f["until"] - time.time())) if f else 0


def verify(pid: int, code: str) -> bool:
    """Constant-time check with throttling. Call wait_seconds() first to report lockouts."""
    r = db.row("SELECT pass_hash, pass_salt FROM profiles WHERE id=?", (pid,))
    if not r or not r["pass_hash"]:
        return True
    ok = hmac.compare_digest(_hash(code or "", bytes.fromhex(r["pass_salt"])), r["pass_hash"])
    with _lock:
        if ok:
            _failures.pop(pid, None)
        else:
            f = _failures.setdefault(pid, {"count": 0, "until": 0.0})
            f["count"] += 1
            if f["count"] >= 5:  # 30s, 60s, 120s, ... capped at 15 min
                f["until"] = time.time() + min(900, 30 * 2 ** (f["count"] - 5))
    return ok


def _idle_limit() -> float:
    return float(config.get("security.auto_lock_minutes", 30)) * 60


def session_pid(token: str | None, touch: bool = False) -> int | None:
    if not token:
        return None
    with _lock:
        s = _sessions.get(token)
        if not s:
            return None
        if time.time() - s["seen"] > _idle_limit():
            s["pid"] = None
            return None
        if touch:
            s["seen"] = time.time()
        return s["pid"]


def is_unlocked(token: str | None, pid: int, touch: bool = True) -> bool:
    if not has_passcode(pid):
        return True
    return session_pid(token, touch=touch) == pid


def unlock(token: str | None, pid: int) -> str:
    with _lock:
        if not token or token not in _sessions:
            token = secrets.token_urlsafe(32)
        _sessions[token] = {"pid": pid, "seen": time.time()}
        # drop long-dead sessions
        for t in [t for t, s in _sessions.items() if time.time() - s["seen"] > 7 * 86400]:
            _sessions.pop(t, None)
    return token


def lock(token: str | None) -> None:
    with _lock:
        if token in _sessions:
            _sessions[token]["pid"] = None


# --- tickets for your own Claude ---------------------------------------------------------------------------
# A run ticket lets Co-op Coach start your Claude Code on an unlocked profile without ever handling the passcode.
# A connection (no expiry) is what the Claude Code plugin uses; its raw token lives in the Keychain, the database
# keeps only a hash, and "Disconnect" deletes it.

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_ticket(pid: int, ttl: int | None = 3600, label: str = "run") -> str:
    token = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute("DELETE FROM agent_tickets WHERE expires_at IS NOT NULL AND expires_at < ?", (db.now(),))
        c.execute("INSERT INTO agent_tickets (profile_id, token_hash, label, created_at, expires_at) VALUES (?,?,?,?,?)",
                  (pid, _token_hash(token), label, db.now(), db.now() + ttl if ttl else None))
    return token


def check_ticket(token: str | None) -> int | None:
    """The profile a ticket opens, or None if it's unknown or expired."""
    if not token:
        return None
    r = db.row("SELECT id, profile_id, expires_at FROM agent_tickets WHERE token_hash=?", (_token_hash(token),))
    if not r or (r["expires_at"] and r["expires_at"] < db.now()):
        return None
    with db.tx() as c:
        c.execute("UPDATE agent_tickets SET last_used_at=? WHERE id=?", (db.now(), r["id"]))
    return r["profile_id"]


_run_tools: dict[str, set[str]] = {}  # a run's ticket -> the tools it may use through the running app


CONNECTION_LABELS = ("Claude Code plugin", "ChatGPT plugin")


def allow_tools(token: str, tools) -> None:
    _run_tools[_token_hash(token)] = set(tools)


def ticket_tools(token: str | None) -> set[str]:
    """The tools a run's ticket may use through the running app (none for any other ticket)."""
    return _run_tools.get(_token_hash(token), set()) if token else set()


def is_connection(token: str | None) -> bool:
    """A lasting connection made from the app ("Connect my Claude Code"), which opens every tool over MCP anyway."""
    if not token:
        return False
    r = db.row("SELECT label, expires_at FROM agent_tickets WHERE token_hash=?", (_token_hash(token),))
    return bool(r) and r["expires_at"] is None and r["label"] in CONNECTION_LABELS


def revoke_ticket(token: str) -> None:
    _run_tools.pop(_token_hash(token), None)
    with db.tx() as c:
        c.execute("DELETE FROM agent_tickets WHERE token_hash=?", (_token_hash(token),))


def connections(pid: int) -> list[dict]:
    return db.rows("SELECT id, label, created_at, last_used_at FROM agent_tickets WHERE profile_id=? AND"
                   " expires_at IS NULL ORDER BY created_at DESC", (pid,))


def revoke_connection(pid: int, cid: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM agent_tickets WHERE profile_id=? AND id=? AND expires_at IS NULL", (pid, cid))
