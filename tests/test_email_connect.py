"""Inbox connection: provider detection, the "track emails" switches, and Microsoft-only addresses.

Run: .venv/bin/python -m tests.test_email_connect
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-mailtest-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="")

from fastapi import HTTPException  # noqa: E402

from coopcoach import db, mail, server  # noqa: E402


def main():
    db.init()
    checks: dict[str, bool] = {}
    want = {"you@gmail.com": ("gmail", "app_password", "imap.gmail.com"),
            "you@yahoo.ca": ("yahoo", "app_password", "imap.mail.yahoo.com"),
            "you@icloud.com": ("icloud", "app_password", "imap.mail.me.com"),
            "you@aol.com": ("aol", "app_password", "imap.aol.com"),
            "you@hotmail.com": ("microsoft", "microsoft", None),
            "you@outlook.com": ("microsoft", "microsoft", None),
            "you@uwaterloo.ca": ("school", "microsoft", None),
            "you@proton.me": ("proton", "unsupported", None)}
    for addr, (pid_, auth, host) in want.items():
        d = mail.detect_provider(addr, lookup=False)
        checks[f"{addr} → {d['name']} ({auth})"] = d["id"] == pid_ and d["auth"] == auth and d.get("host") == host

    with db.tx() as c:
        on = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES"
                       " ('A',1,1,'{}','{}')").lastrowid
        off = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES"
                        " ('B',1,1,'{\"track_email\": false}','{}')").lastrowid
        for pid, addr in ((on, "a1@gmail.com"), (on, "a2@yahoo.com"), (off, "b@gmail.com")):
            c.execute("INSERT INTO email_accounts (profile_id, kind, address, host, secret, created_at)"
                      " VALUES (?,?,?,?,?,?)", (pid, "imap", addr, "imap.gmail.com", "x", 1))
    a2 = db.row("SELECT id FROM email_accounts WHERE address='a2@yahoo.com'")["id"]
    server.toggle_email_account(a2, {"enabled": False})

    scanned = []
    mail.sync_account = lambda acct: scanned.append(acct["address"]) or {}
    mail.sync_all()
    checks["only inboxes with both switches on are read"] = scanned == ["a1@gmail.com"]
    scanned.clear()
    mail.sync_all(off)
    checks["a person who turned tracking off isn't read even when asked directly"] = scanned == []
    server.toggle_email_account(a2, {"enabled": True})
    scanned.clear()
    mail.sync_all(on)
    checks["switching an inbox back on resumes it"] = sorted(scanned) == ["a1@gmail.com", "a2@yahoo.com"]

    try:
        server.add_imap({"profile_id": on, "address": "me@hotmail.com", "password": "hunter2"})
        checks["Hotmail with a password is refused with a pointer to Microsoft sign-in"] = False
    except HTTPException as e:
        checks["Hotmail with a password is refused with a pointer to Microsoft sign-in"] = \
            e.status_code == 400 and "Sign in with Microsoft" in e.detail
    checks["a malformed Microsoft app ID is rejected before calling Microsoft"] = \
        "isn't an Application" in (mail.ms_check_client_id("abc") or "")

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
