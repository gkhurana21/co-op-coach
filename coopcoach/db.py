"""The database: a local SQLite file by default, or Supabase Postgres once you move your data there (supa.py).

Both sit behind the same small API (rows, row, tx, meta_get/meta_set, init). Code writes SQLite-flavoured SQL with
? placeholders; for Postgres it's translated once per statement (placeholders, INSERT OR IGNORE/REPLACE, LIKE,
two-argument MIN/MAX, lastrowid via RETURNING id).
"""

import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from functools import lru_cache

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    locations TEXT NOT NULL DEFAULT '[]',
    regions TEXT NOT NULL DEFAULT '[]',
    terms TEXT NOT NULL DEFAULT '[]',
    category TEXT,
    tier TEXT,
    yc_batch TEXT,
    skills TEXT NOT NULL DEFAULT '[]',
    flags TEXT NOT NULL DEFAULT '[]',
    description TEXT,
    posted_at INTEGER,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_key);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(active, first_seen);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    resume_filename TEXT,
    resume_text TEXT,
    profile_json TEXT NOT NULL DEFAULT '{}',
    assessment_json TEXT NOT NULL DEFAULT '{}',
    prefs_json TEXT NOT NULL DEFAULT '{}',
    parsed_by TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS favourites (
    profile_id INTEGER NOT NULL,
    kind TEXT NOT NULL,          -- 'company' (key = company_key) or 'job' (key = job id)
    key TEXT NOT NULL,
    label TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, kind, key)
);

CREATE TABLE IF NOT EXISTS applications (
    profile_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,        -- saved | applied | interviewing | offer | rejected
    notes TEXT,
    deadline TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, job_id)
);

CREATE TABLE IF NOT EXISTS ai_scores (
    profile_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    fit INTEGER NOT NULL,
    why TEXT,
    tip TEXT,
    profile_version INTEGER NOT NULL,
    computed_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, job_id)
);

CREATE TABLE IF NOT EXISTS notified (
    profile_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    notified_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, item_id)
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ats TEXT NOT NULL,           -- greenhouse | lever | ashby | workday
    slug TEXT NOT NULL,          -- board slug, or the full careers URL for workday
    name TEXT NOT NULL,
    tier TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_status TEXT,
    last_count INTEGER,
    UNIQUE (ats, slug)
);

CREATE TABLE IF NOT EXISTS event_feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    label TEXT,
    career_only INTEGER NOT NULL DEFAULT 1,
    last_status TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    feed_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER,
    all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    url TEXT,
    description TEXT,
    kind TEXT,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS email_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    kind TEXT NOT NULL,          -- imap | outlook
    address TEXT NOT NULL,
    host TEXT,
    port INTEGER,
    username TEXT,
    secret TEXT,                 -- IMAP app password or Microsoft refresh token (stays in this local file)
    last_sync INTEGER,
    last_status TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE (profile_id, address)
);

CREATE TABLE IF NOT EXISTS email_events (
    id TEXT PRIMARY KEY,         -- hash of profile + Message-ID
    profile_id INTEGER NOT NULL,
    account_id INTEGER,
    message_id TEXT,
    received_at INTEGER,
    from_name TEXT,
    from_addr TEXT,
    subject TEXT,
    snippet TEXT,
    web_link TEXT,
    status TEXT,                 -- applied | oa | interviewing | offer | rejected
    company TEXT,
    role TEXT,
    job_id TEXT,                 -- application this email was linked to
    classified_by TEXT,
    changed_status INTEGER NOT NULL DEFAULT 0,
    ignored INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_events_profile ON email_events(profile_id, received_at);

CREATE TABLE IF NOT EXISTS portal_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'workday',
    site_url TEXT NOT NULL,      -- https://<tenant>.wd<N>.myworkdayjobs.com/<site>
    company TEXT NOT NULL,
    username TEXT NOT NULL,      -- password lives in the OS keychain (vault.py)
    last_check INTEGER,
    last_status TEXT,
    needs_manual INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE (profile_id, site_url, username)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    due_at INTEGER NOT NULL,
    job_id TEXT,
    notified INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_profile ON reminders(profile_id, due_at);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    kind TEXT NOT NULL,          -- resume_master | resume | cover_letter
    job_id TEXT,
    title TEXT NOT NULL,
    format_id TEXT,              -- cover-letter format or resume template
    content TEXT NOT NULL,       -- JSON for resumes, plain text for letters
    generated_by TEXT,           -- rules | claude | you
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_profile ON documents(profile_id, kind, job_id);

CREATE TABLE IF NOT EXISTS letter_formats (
    id TEXT PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    body TEXT NOT NULL,          -- template with {placeholders}, or a sample letter to imitate
    style TEXT NOT NULL,         -- template | sample
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS event_marks (
    profile_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    mark TEXT NOT NULL,          -- interested | going
    created_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, event_id)
);

-- everything an AI did through the tools (in-app chat, your Claude Code, Autopilot), with how to undo it
CREATE TABLE IF NOT EXISTS agent_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    source TEXT NOT NULL,        -- chat | claude_code | mcp | autopilot | app
    run_id TEXT,
    tool TEXT NOT NULL,
    args TEXT,
    summary TEXT,
    ok INTEGER NOT NULL DEFAULT 1,
    writes INTEGER NOT NULL DEFAULT 0,
    card TEXT,
    undo TEXT,
    undone INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_actions_profile ON agent_actions(profile_id, ts);
CREATE INDEX IF NOT EXISTS idx_actions_run ON agent_actions(run_id);

-- applications prepared ahead of time (answers + tailored resume + letter), waiting for the person to submit
CREATE TABLE IF NOT EXISTS ready_queue (
    profile_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,        -- ready | opened | applied | skipped
    why TEXT,
    kit TEXT,
    source TEXT,
    run_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, job_id)
);

-- short-lived run tickets and long-lived "connected Claude" tokens (hashes only; the raw token is in the Keychain)
CREATE TABLE IF NOT EXISTS agent_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    label TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    last_used_at INTEGER
);

CREATE TABLE IF NOT EXISTS autopilot_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    trigger TEXT,
    engine TEXT,
    run_id TEXT,
    queued INTEGER DEFAULT 0,
    brief TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    new_jobs INTEGER DEFAULT 0,
    new_events INTEGER DEFAULT 0,
    report TEXT
);
"""

_local = threading.local()
_write_lock = threading.RLock()
_pg = None          # PgBackend once Supabase is the database
_pg_checked = False


def _postgres():
    """The Supabase backend if data/database.json says so (checked once per process)."""
    global _pg, _pg_checked
    if not _pg_checked:
        _pg_checked = True
        try:
            from . import supa
            if supa.enabled():
                _pg = PgBackend(supa.conninfo())
        except Exception as e:  # can't reach it: say so loudly rather than silently using a stale local copy
            raise RuntimeError(f"Supabase is set as the database but isn't reachable: {e}") from e
    return _pg


def is_postgres() -> bool:
    return _postgres() is not None


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


@contextmanager
def tx():
    """Serialized write transaction (SQLite allows one writer at a time anyway)."""
    with _write_lock:
        pg = _postgres()
        if pg:
            with pg.tx() as c:
                yield c
            return
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


# columns added after the first release; init() adds them to older databases
_MIGRATIONS = {
    "profiles": {"pass_hash": "TEXT", "pass_salt": "TEXT", "parser_version": "INTEGER"},
    "companies": {"last_checked": "INTEGER", "removed": "INTEGER NOT NULL DEFAULT 0", "origin": "TEXT"},
    "applications": {
        "company": "TEXT", "title": "TEXT", "url": "TEXT", "source": "TEXT", "applied_at": "INTEGER",
        "last_email_at": "INTEGER", "last_email_subject": "TEXT", "req_id": "TEXT",
        "interview_at": "INTEGER", "oa_due_at": "INTEGER",  # put on the calendar feed with a reminder
    },
    "jobs": {"req_id": "TEXT", "requirements": "TEXT", "form_checked": "INTEGER NOT NULL DEFAULT 0"},  # the company's requisition number (Workday R-0000181145, ...)
    "email_events": {"req_ids": "TEXT"},  # requisition numbers quoted in the email, comma-separated
    "portal_accounts": {"uses_default": "INTEGER NOT NULL DEFAULT 0"},  # signs in with the one shared Workday login
    "email_accounts": {"enabled": "INTEGER NOT NULL DEFAULT 1"},  # the per-inbox "track emails" switch
    "events": {"region": "TEXT"},  # Waterloo Region, Toronto / GTA, SF Bay Area, Online, ...
    "event_feeds": {"enabled": "INTEGER NOT NULL DEFAULT 1",  # paused sources stay listed but aren't fetched
                    "grp": "TEXT",  # Canadian universities, US universities, Toronto & Waterloo, Cities, Hackathons
                    "region": "TEXT",  # where the source is (a campus building name says nothing about the city)
                    "last_fetched": "INTEGER"},  # university calendars are read at most twice a day
    "ready_queue": {"skip_reason": "TEXT", "note": "TEXT"},  # "Not for me" reasons teach Autopilot; note = closed/expired
    "autopilot_runs": {"notified": "INTEGER NOT NULL DEFAULT 1", "picks": "TEXT"},  # 0 = alert held for quiet hours
}


def init() -> None:
    pg = _postgres()
    if pg:
        pg.init()
        return
    with tx() as c:
        c.executescript(SCHEMA)
        for table, cols in _MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    # the database holds resumes and email credentials: keep it readable by this user only
    for suffix in ("", "-wal", "-shm"):
        p = DB_PATH.with_name(DB_PATH.name + suffix)
        if p.exists():
            p.chmod(0o600)


def now() -> int:
    return int(time.time())


def loads(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def rows(sql: str, params=()) -> list[dict]:
    pg = _postgres()
    if pg:
        return pg.rows(sql, params)
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def row(sql: str, params=()) -> dict | None:
    pg = _postgres()
    if pg:
        r = pg.rows(sql, params, one=True)
        return r[0] if r else None
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def meta_get(key: str, default=None):
    r = row("SELECT value FROM meta WHERE key=?", (key,))
    return r["value"] if r else default


def meta_set(key: str, value) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))


# --- Postgres (Supabase) ---------------------------------------------------------------------------------------

def _table_keys() -> tuple[dict[str, list[str]], set[str], dict[str, list[str]]]:
    """Primary keys per table, the tables with an integer id (INSERTs there return it as lastrowid), and each
    table's columns in order (for INSERT ... VALUES without a column list)."""
    pks, ids, cols = {}, set(), {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA, re.S):
        table, body = m.group(1), m.group(2)
        cols[table] = [c.group(1) for c in re.finditer(r"^\s*(\w+) (?:TEXT|INTEGER|REAL|BLOB)", body, re.M)]
        comp = re.search(r"PRIMARY KEY \(([^)]+)\)", body)
        if comp:
            pks[table] = [c.strip() for c in comp.group(1).split(",")]
        else:
            col = re.search(r"^\s*(\w+) [A-Z ]*PRIMARY KEY", body, re.M)
            pks[table] = [col.group(1)] if col else []
        if re.search(r"^\s*id INTEGER PRIMARY KEY", body, re.M):
            ids.add(table)
    return pks, ids, cols


PRIMARY_KEYS, ID_TABLES, COLUMNS = _table_keys()


def pg_schema() -> list[str]:
    ddl = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
    ddl = re.sub(r"\bid INTEGER PRIMARY KEY\b", "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", ddl)
    ddl = re.sub(r"\bINTEGER\b", "BIGINT", ddl).replace(" REAL", " DOUBLE PRECISION")
    ddl = re.sub(r"--[^\n]*", "", ddl)
    return [x.strip() for x in ddl.split(";") if x.strip()]


def _two_arg(sql: str, fn: str, to: str) -> str:
    """MIN(a, b) / MAX(a, b) (SQLite's scalar forms) -> LEAST / GREATEST; one-argument aggregates stay."""
    out, i = [], 0
    pat = re.compile(rf"\b{fn}\(", re.I)
    while True:
        m = pat.search(sql, i)
        if not m:
            out.append(sql[i:])
            return "".join(out)
        depth, j, comma = 1, m.end(), False
        while j < len(sql) and depth:
            ch = sql[j]
            depth += ch == "("
            depth -= ch == ")"
            comma |= ch == "," and depth == 1
            j += 1
        out.append(sql[i:m.start()] + (to + "(" if comma else sql[m.start():m.end()]))
        i = m.end()


@lru_cache(maxsize=2048)
def translate(sql: str) -> tuple[str, bool]:
    """SQLite-flavoured SQL -> Postgres. Returns (sql, returns_id)."""
    q = sql.strip().rstrip(";")
    conflict = ""
    if re.match(r"(?is)UPDATE OR IGNORE\b", q):  # run in a savepoint by _Tx; a duplicate key is ignored there
        q = "UPDATE" + q[len("UPDATE OR IGNORE"):]
    m = re.match(r"(?is)INSERT OR (IGNORE|REPLACE) INTO (\w+)(\s*\(([^)]*)\))?", q)
    if m:
        kind, table = m.group(1).upper(), m.group(2)
        cols = [c.strip() for c in m.group(4).split(",")] if m.group(4) else COLUMNS.get(table, [])
        q = "INSERT INTO" + q[len(f"INSERT OR {m.group(1)} INTO"):]
        pk = PRIMARY_KEYS.get(table) or []
        rest = [c for c in cols if c not in pk]
        if kind == "IGNORE" or not rest or not pk:
            conflict = " ON CONFLICT DO NOTHING"
        else:
            conflict = f" ON CONFLICT ({', '.join(pk)}) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in rest)
    # placeholders: ? outside quotes -> %s, and every literal % doubled
    out, quote = [], False
    for ch in q:
        if ch == "'":
            quote = not quote
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif ch == "?" and not quote:
            out.append("%s")
        else:
            out.append(ch)
    q = "".join(out)
    q = re.sub(r"\bLIKE\b", "ILIKE", q)
    q = re.sub(r"(\w+(?:\.\w+)?) COLLATE NOCASE", r"lower(\1)", q)
    q = _two_arg(_two_arg(q, "MIN", "LEAST"), "MAX", "GREATEST")
    if conflict:
        r = re.search(r"\bRETURNING\b", q, re.I)
        q = q[:r.start()] + conflict.strip() + " " + q[r.start():] if r else q + conflict
    returns_id = False
    ins = re.match(r"(?is)INSERT INTO (\w+)", q)
    if ins and ins.group(1) in ID_TABLES and not re.search(r"\bRETURNING\b", q, re.I):
        q += " RETURNING id"
        returns_id = True
    return q, returns_id


def _params(params) -> tuple:
    return tuple(int(p) if isinstance(p, bool) else p for p in (params or ()))


class Row(dict):
    """A dict that also answers row[0], like sqlite3.Row."""
    def __getitem__(self, k):
        if isinstance(k, int):
            return list(self.values())[k]
        return dict.__getitem__(self, k)


def _row_factory(cursor):
    names = [d.name for d in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


class _Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self):
        return iter(self.fetchall())


class _Tx:
    """What `with db.tx() as c` gives you on Postgres: execute / executemany like sqlite3."""
    def __init__(self, conn):
        self.c = conn

    def execute(self, sql: str, params=()):
        q, returns_id = translate(sql)
        cur = self.c.cursor()
        if re.match(r"(?i)\s*UPDATE OR IGNORE\b", sql):
            import psycopg
            try:
                with self.c.transaction():  # a savepoint, so a clash doesn't spoil the whole transaction
                    cur.execute(q, _params(params))
            except psycopg.errors.UniqueViolation:
                pass
            return _Cursor(cur)
        cur.execute(q, _params(params))
        rid = None
        if returns_id:
            r = cur.fetchone()
            rid = r[0] if r else None
        return _Cursor(cur, rid)

    def executemany(self, sql: str, seq):
        q, returns_id = translate(sql)
        if returns_id:
            q = q[: -len(" RETURNING id")]
        cur = self.c.cursor()
        cur.executemany(q, [_params(p) for p in seq])
        return _Cursor(cur)

    def executescript(self, script: str):
        for stmt in [x.strip() for x in script.split(";") if x.strip()]:
            self.c.execute(stmt)


class PgBackend:
    def __init__(self, conninfo: str, min_size: int = 1, max_size: int = 6):
        import atexit

        from psycopg_pool import ConnectionPool
        # autocommit: a read is one round trip; writes get an explicit transaction in tx()
        self.pool = ConnectionPool(conninfo, min_size=min_size, max_size=max_size, open=True, timeout=30,
                                   kwargs={"row_factory": _row_factory, "prepare_threshold": None, "autocommit": True})
        atexit.register(self.pool.close)

    @contextmanager
    def tx(self):
        with self.pool.connection() as c:
            with c.transaction():
                yield _Tx(c)

    def rows(self, sql: str, params=(), one: bool = False) -> list[dict]:
        q, _ = translate(sql)
        with self.pool.connection() as c:
            cur = c.execute(q, _params(params))
            if not cur.description:
                return []
            got = [cur.fetchone()] if one else cur.fetchall()
            return [dict(r) for r in got if r is not None]

    def reset(self) -> None:
        """Tests only: empty the scratch schema."""
        with self.pool.connection() as c:
            for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall():
                c.execute(f'DROP TABLE IF EXISTS "{r["tablename"]}" CASCADE')

    def init(self) -> None:
        import os
        if os.environ.get("COOPCOACH_PG_RESET") == "1" and os.environ.get("COOPCOACH_PG_SCHEMA", "").endswith("_test"):
            os.environ["COOPCOACH_PG_RESET"] = "0"  # once per test process; MCP servers it starts keep the data
            self.reset()
        with self.pool.connection() as c:
            for stmt in pg_schema():
                c.execute(stmt)
            c.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, data BYTEA NOT NULL, updated_at BIGINT NOT NULL)")
            for table, cols in _MIGRATIONS.items():
                have = {r["column_name"] for r in c.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND"
                    " table_name=%s", (table,))}
                for col, typ in cols.items():
                    if col not in have:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {re.sub(r'INTEGER', 'BIGINT', typ)}")

    def columns(self, table: str) -> set[str]:
        return {r["column_name"] for r in self.rows(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?",
            (table,))}

    def count(self, table: str) -> int:
        return self.rows(f"SELECT COUNT(*) AS n FROM {table}", one=True)[0]["n"]

    def copy_rows(self, table: str, cols: list[str], data: list[tuple]) -> None:
        if not data or not cols:
            return
        with self.pool.connection() as c, c.transaction():
            c.execute(f"DELETE FROM {table}")
            with c.cursor().copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
                for r in data:
                    cp.write_row(r)

    def fix_sequences(self) -> None:
        """Rows copied with their ids: move each identity counter past the highest id."""
        with self.pool.connection() as c:
            for t in ID_TABLES:
                c.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), GREATEST((SELECT MAX(id) FROM {t}), 1))")

    def put_file(self, path: str, data: bytes) -> None:
        with self.pool.connection() as c:
            c.execute("INSERT INTO files (path, data, updated_at) VALUES (%s, %s, %s) ON CONFLICT (path) DO UPDATE SET"
                      " data=EXCLUDED.data, updated_at=EXCLUDED.updated_at", (path, data, int(time.time())))

    def files(self, prefix: str = ""):
        with self.pool.connection() as c:
            for r in c.execute("SELECT path, data FROM files WHERE path LIKE %s", (prefix + "%",)).fetchall():
                yield r["path"], bytes(r["data"])
