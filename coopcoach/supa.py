"""Supabase: Co-op Coach's database in the cloud (Postgres), so everything is stored there.

* The app connects as its own Postgres role (`coopcoach_app`) through Supabase's connection pooler (IPv4). Its
  password is generated on this computer and kept in the Keychain; Supabase only ever receives a SCRAM verifier
  (a one-way hash), so the password never travels or shows up anywhere.
* Tables live in a private `coopcoach` schema that Supabase's public REST API doesn't expose.
* data/database.json (never committed) says which database to use; without it, the local SQLite file is used.
* `move_to_supabase()` copies every table from SQLite (in batches, then checks the counts), stores uploaded resumes
  in a `files` table, and switches over. The SQLite file is kept as a backup.

Passwords and tokens (inbox, Workday, Claude/ChatGPT connections) stay in the Keychain, never in Supabase.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)
SETTINGS = config.DATA_DIR / "database.json"
ROLE = "coopcoach_app"
SCHEMA = "coopcoach"
POOLERS = ["aws-0-{region}.pooler.supabase.com", "aws-1-{region}.pooler.supabase.com"]


def settings() -> dict:
    path = Path(os.environ.get("COOPCOACH_PG_SETTINGS") or SETTINGS)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}


def save_settings(d: dict) -> None:
    SETTINGS.write_text(json.dumps(d, indent=2), encoding="utf-8")
    SETTINGS.chmod(0o600)


def enabled() -> bool:
    """Supabase is the database. COOPCOACH_PG_SCHEMA (tests) points at a scratch schema in the same project."""
    return settings().get("backend") == "postgres" or bool(os.environ.get("COOPCOACH_PG_SCHEMA") and settings().get("ref"))


def keychain_key(ref: str) -> str:
    return f"supabase-db:{ref}"


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """What Postgres stores for a SCRAM-SHA-256 password. Safe to send: it can't be turned back into the password
    or used to sign in."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def new_credentials(ref: str) -> str:
    """Make the app role's password, keep it in the Keychain, return only the verifier for CREATE/ALTER ROLE."""
    from . import vault
    pw = secrets.token_urlsafe(32)
    vault.put(keychain_key(ref), pw)
    return scram_verifier(pw)


def setup_sql(verifier: str) -> str:
    """Run once in the Supabase SQL editor (or by Claude through the Supabase connector) as the project owner."""
    return f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
    CREATE ROLE {ROLE} LOGIN PASSWORD '{verifier}';
  ELSE
    ALTER ROLE {ROLE} LOGIN PASSWORD '{verifier}';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS {SCHEMA} AUTHORIZATION {ROLE};
ALTER ROLE {ROLE} SET search_path = {SCHEMA};
GRANT USAGE ON SCHEMA {SCHEMA} TO postgres;
ALTER DEFAULT PRIVILEGES FOR ROLE {ROLE} IN SCHEMA {SCHEMA} GRANT ALL ON TABLES TO postgres;
ALTER DEFAULT PRIVILEGES FOR ROLE {ROLE} IN SCHEMA {SCHEMA} GRANT ALL ON SEQUENCES TO postgres;
"""


def conninfo(s: dict | None = None) -> str:
    import keyring

    from . import vault
    s = s or settings()
    schema = os.environ.get("COOPCOACH_PG_SCHEMA")
    pw = keyring.get_password(vault.SERVICE, keychain_key(s["ref"]))
    if not pw and sys.platform == "darwin":  # something swapped the keyring backend (tests): ask the Keychain itself
        from keyring.backends import macOS
        pw = macOS.Keyring().get_password(vault.SERVICE, keychain_key(s["ref"]))
    if not pw:
        raise RuntimeError("The Supabase database password isn't in the Keychain")
    from psycopg.conninfo import make_conninfo
    return make_conninfo(host=s["host"], port=s.get("port", 5432), dbname=s.get("dbname", "postgres"),
                         user=f"{ROLE}.{s['ref']}", password=pw, sslmode="require", connect_timeout=15,
                         application_name="coop-coach", **({"options": f"-c search_path={schema}"} if schema else {}))


def find_pooler(ref: str, region: str) -> str:
    """Supabase projects sit on one of two pooler clusters per region; use the one that knows this project."""
    import psycopg
    last = ""
    for tpl in POOLERS:
        host = tpl.format(region=region)
        try:
            with psycopg.connect(conninfo({"ref": ref, "host": host}), autocommit=True) as c:
                c.execute("SELECT 1")
            return host
        except Exception as e:
            last = str(e)
            continue
    raise RuntimeError(f"Couldn't reach the Supabase pooler: {last[:200]}")


def connect(ref: str, region: str = "ca-central-1") -> dict:
    """Remember the project and the pooler that works (the role must already exist, see setup_sql)."""
    host = find_pooler(ref, region)
    s = {**settings(), "ref": ref, "region": region, "host": host, "port": 5432, "dbname": "postgres"}
    s.setdefault("backend", "sqlite")
    save_settings(s)
    return s


# --- moving the data -----------------------------------------------------------------------------------------

_state: dict = {"running": False}


def state() -> dict:
    return dict(_state)


def move_to_supabase(progress=None) -> dict:
    """Copy everything from the local SQLite file into Supabase, check it, then switch the app over."""
    import sqlite3

    from . import db
    say = progress or (lambda m: log.info("supabase: %s", m))
    s = settings()
    if not s.get("ref"):
        raise RuntimeError("Connect a Supabase project first")
    _state.update(running=True, step="Connecting…", done=False, error=None)
    try:
        src = sqlite3.connect(config.DB_PATH)
        src.row_factory = sqlite3.Row
        pg = db.PgBackend(conninfo(s))
        say("Creating the tables…")
        pg.init()
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                  if r[0] in db.COLUMNS]  # only tables Co-op Coach still has (not ones a removed feature left)
        counts = {}
        for t in tables:
            cols_pg = pg.columns(t)
            rows = src.execute(f"SELECT * FROM {t}").fetchall()
            cols = [c for c in (rows[0].keys() if rows else []) if c in cols_pg]
            _state["step"] = f"Copying {t} ({len(rows):,} rows)…"
            say(_state["step"])
            pg.copy_rows(t, cols, [tuple(r[c] for c in cols) for r in rows])
            counts[t] = len(rows)
        say("Checking the copy…")
        mismatch = {t: (n, pg.count(t)) for t, n in counts.items() if pg.count(t) != n}
        if mismatch:
            raise RuntimeError(f"Row counts don't match: {mismatch}")
        say("Storing uploaded resumes…")
        files = 0
        for f in sorted(config.UPLOAD_DIR.glob("*")):
            if f.is_file():
                pg.put_file(f"resumes/{f.name}", f.read_bytes())
                files += 1
        pg.fix_sequences()
        backup = config.DB_PATH.with_name("coopcoach.sqlite.bak")
        bk = sqlite3.connect(backup)
        src.backup(bk)
        bk.close()
        src.close()
        save_settings({**s, "backend": "postgres", "moved_at": int(time.time()), "counts": counts})
        _state.update(running=False, done=True, step="Done", counts=counts, files=files)
        return {"ok": True, "counts": counts, "files": files, "backup": str(backup)}
    except Exception as e:
        _state.update(running=False, error=str(e)[:300])
        raise


def use_local() -> dict:
    """Switch back to the local SQLite file (Supabase keeps its copy)."""
    s = settings()
    save_settings({**s, "backend": "sqlite"})
    return {"ok": True}


def restore_files(pg) -> int:
    """Uploaded resumes this computer doesn't have yet come back from Supabase."""
    n = 0
    for path, data in pg.files("resumes/"):
        dest = config.UPLOAD_DIR / Path(path).name
        if not dest.exists():
            dest.write_bytes(data)
            n += 1
    return n
