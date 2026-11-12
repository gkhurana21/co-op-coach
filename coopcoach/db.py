"""The database: a local SQLite file by default, or Supabase Postgres once you move your data there (supa.py).

Both sit behind the same small API (rows, row, tx, meta_get/meta_set, init). Code writes SQLite-flavoured SQL with
? placeholders; for Postgres it's translated once per statement (placeholders, INSERT OR IGNORE/REPLACE, LIKE,
two-argument MIN/MAX, lastrowid via RETURNING id).
"""

import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from functools import lru_cache

from .config import DB_PATH

log = logging.getLogger(__name__)

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

-- niches someone started themselves (the shipped ones live in niches.yaml)
CREATE TABLE IF NOT EXISTS niches (
    id TEXT PRIMARY KEY,         -- custom-<hex>
    profile_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    blurb TEXT,
    keywords TEXT NOT NULL DEFAULT '[]',
    category TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS niche_follows (
    profile_id INTEGER NOT NULL,
    niche_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, niche_id)
);

-- firms someone added to a niche, hid from it, or that "Find more" suggested
CREATE TABLE IF NOT EXISTS niche_firms (
    profile_id INTEGER NOT NULL,
    niche_id TEXT NOT NULL,
    key TEXT NOT NULL,           -- company_key of the firm
    name TEXT NOT NULL,
    segment TEXT,
    status TEXT NOT NULL,        -- added | suggested | hidden | dismissed
    origin TEXT,                 -- you | ai | postings | catalog
    info TEXT,                   -- board, careers page, cities, segment name (JSON)
    why TEXT,
    verified TEXT,               -- what the live check found
    created_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, niche_id, key)
);

-- recruiters and others who wrote about an application (from the inbox scan, pasted emails, or added by hand)
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    key TEXT NOT NULL,           -- their email address, or name:<company>:<name> for someone who signed an automated email
    kind TEXT NOT NULL DEFAULT 'person',  -- person | team (a shared inbox like careers@)
    name TEXT,
    email TEXT,
    title TEXT,
    phone TEXT,
    linkedin TEXT,
    company TEXT,
    company_key TEXT,
    first_seen INTEGER,
    last_seen INTEGER,
    emails INTEGER NOT NULL DEFAULT 0,
    last_subject TEXT,
    last_status TEXT,
    job_ids TEXT NOT NULL DEFAULT '[]',
    source TEXT,                 -- inbox | pasted | you
    notes TEXT,
    starred INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    edited TEXT NOT NULL DEFAULT '[]',  -- fields changed by hand: later emails never overwrite them
    created_at INTEGER NOT NULL,
    UNIQUE (profile_id, key)
);

-- the first internship seen from a company for a term: when that company opened for it
CREATE TABLE IF NOT EXISTS firm_openings (
    company_key TEXT NOT NULL,
    term TEXT NOT NULL,
    opened_at INTEGER NOT NULL,
    PRIMARY KEY (company_key, term)
);

-- what a company has disclosed raising, as your own AI's web search found it (People → Check funding); public facts,
-- shared by everyone on this computer
CREATE TABLE IF NOT EXISTS company_funding (
    company_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    found INTEGER NOT NULL DEFAULT 0,
    total_usd INTEGER,
    last_round TEXT,
    last_date TEXT,
    investors TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    note TEXT,
    checked_by TEXT,
    checked_at INTEGER NOT NULL
);

-- how many roles (of every kind) each job board lists, once a day: how fast a company is hiring (the Startups in Canada track)
CREATE TABLE IF NOT EXISTS board_roles (
    board TEXT NOT NULL,         -- "greenhouse:acme", "yc:acme"
    day TEXT NOT NULL,           -- 2026-10-26
    roles INTEGER NOT NULL,
    interns INTEGER,
    PRIMARY KEY (board, day)
);

-- what an email puts on the calendar: an interview, an assessment deadline, a coffee chat, an info session... (only the
-- item and a link back to the email are kept, never the email itself)
CREATE TABLE IF NOT EXISTS calendar_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- interview | oa | recruiter_call | coffee_chat | info_session | career_fair | other
    title TEXT NOT NULL,
    start_ts INTEGER,
    end_ts INTEGER,
    due_ts INTEGER,                  -- a deadline (an assessment) rather than a time to be somewhere
    all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    join_url TEXT,                   -- the call or the assessment
    book_url TEXT,                   -- pick a time (Calendly, GoodTime...)
    status TEXT NOT NULL,            -- confirmed | to_book | cancelled
    job_id TEXT,                     -- the application it's about (none for networking)
    company TEXT,
    who TEXT,                        -- who it's with: "Jane Doe <jane@shopify.com>"
    email_ref TEXT,                  -- the email it came from (undoing or ignoring the email removes it)
    email_subject TEXT,
    email_link TEXT,
    ics_uid TEXT,                    -- the invite's own id: a moved or cancelled invite updates this item
    source TEXT NOT NULL,            -- inbox | pasted | you
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calendar_items ON calendar_items(profile_id, start_ts);

-- the Path: LeetCode problems, tracked (they're solved on LeetCode; Co-op Coach keeps links, never problem text)
CREATE TABLE IF NOT EXISTS coding_solved (
    profile_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    solved_at INTEGER NOT NULL,      -- the first solve (what earns the problem's XP)
    PRIMARY KEY (profile_id, slug)
);

CREATE TABLE IF NOT EXISTS coding_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    result TEXT NOT NULL,            -- solved | hints | unsolved
    seconds INTEGER,                 -- how long it took (the in-app timer, or typed)
    notes TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coding_attempts ON coding_attempts(profile_id, slug, created_at);

-- a problem kept on a list: for a job (job_id), a kind of role or industry ("Quant"), or a company
CREATE TABLE IF NOT EXISTS coding_stars (
    profile_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    tag_kind TEXT NOT NULL,          -- job | track | company
    tag TEXT NOT NULL,
    label TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, slug, tag_kind, tag)
);

-- LeetCode problems someone added to their ladder by link (name and level only)

-- the Path's tests: real problems solved on LeetCode against the clock
CREATE TABLE IF NOT EXISTS path_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- topic | checkpoint | final
    scope TEXT NOT NULL,             -- the topic's id, c3, c6... or final
    slugs TEXT NOT NULL,             -- the problems it picked (JSON)
    started_at INTEGER NOT NULL,
    deadline INTEGER NOT NULL,
    finished_at INTEGER,
    passed INTEGER,
    results TEXT                     -- how each went (JSON)
);
CREATE INDEX IF NOT EXISTS idx_path_tests ON path_tests(profile_id, scope, started_at);

-- tracks (Jobs → Matches | your tracks | ＋ Tracks): the ones someone pinned, in order, and the ones they follow
CREATE TABLE IF NOT EXISTS track_pins (
    profile_id INTEGER NOT NULL,
    track_id TEXT NOT NULL,          -- role:software | co:quant-firms | co:custom-1a2b3c | auto:forward-deployed
    position INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, track_id)
);

CREATE TABLE IF NOT EXISTS track_follows (
    profile_id INTEGER NOT NULL,
    track_id TEXT NOT NULL,          -- niche tracks follow through niche_follows (their boards join the sync)
    created_at INTEGER NOT NULL,
    PRIMARY KEY (profile_id, track_id)
);

-- kinds of role the feed keeps posting that no shipped track covers (track_discovery.py)
CREATE TABLE IF NOT EXISTS tracks_discovered (
    id TEXT PRIMARY KEY,             -- forward-deployed
    label TEXT NOT NULL,             -- Forward Deployed Engineer
    phrases TEXT NOT NULL,           -- the title phrases that count, JSON
    status TEXT NOT NULL,            -- active | steady | faded
    trend TEXT,                      -- new | rising | established
    stats TEXT,                      -- open, companies, recent and prior companies (JSON)
    folded_into TEXT,                -- a shipped track that covers it now: pins to it open that one
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);

-- what a posting's full text says, read once per description (market.facts): skills it requires and would like, and
-- the pay it states
CREATE TABLE IF NOT EXISTS job_facts (
    job_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    facts TEXT NOT NULL,             -- JSON
    computed_at INTEGER NOT NULL
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
            import os
            url = os.environ.get("COOPCOACH_DATABASE_URL")  # any Postgres (CI uses a throwaway one)
            if url:
                from psycopg.conninfo import make_conninfo
                schema = os.environ.get("COOPCOACH_PG_SCHEMA")
                _pg = PgBackend(make_conninfo(url, **({"options": f"-c search_path={schema}"} if schema else {})))
                return _pg
            from . import supa
            if supa.enabled():
                _not_a_test_on_real_data(os.environ.get("COOPCOACH_PG_SCHEMA", ""))
                _pg = PgBackend(supa.conninfo())
        except TestOnRealData:
            raise
        except Exception as e:  # can't reach it: say so loudly rather than silently using a stale local copy
            raise RuntimeError(f"Supabase is set as the database but isn't reachable: {e}") from e
    return _pg


class TestOnRealData(RuntimeError):
    pass


def _not_a_test_on_real_data(schema: str) -> None:
    """A test runs with a temporary data folder. Pointed at the Supabase project, it must use a scratch schema
    (`tests.run_all --postgres` sets coopcoach_test), never the real one: a test run by hand with only the settings
    file once wrote a test profile and a made-up posting into the real data."""
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.gettempdir()).resolve()
    if tmp in DB_PATH.resolve().parents and not schema.endswith("_test"):
        raise TestOnRealData("this looks like a test (its data folder is temporary) pointed at the real Supabase "
                             "data; set COOPCOACH_PG_SCHEMA=coopcoach_test, or run it with tests.run_all --postgres")


def is_postgres() -> bool:
    return _postgres() is not None


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.set_trace_callback(_note_write)  # which tables this connection writes (see "reads from memory")
        _local.conn = c
    return c


# --- reads from memory ---------------------------------------------------------------------------------
#
# With the data on Supabase every query is a round trip over the internet (about 190 ms), and a page makes 5 to 35 of them:
# that, not the code, is what makes a page take seconds. So reads are kept in memory, per query. Every write says which
# tables it touched, which ends what was read from them; a write by another process on this computer (the Claude Code
# plugin, a sync worker) is noticed through a file's timestamp; and what's being used is refreshed in the background, so a
# page never waits for it. If the internet drops, what was read last answers. Tickets are always asked for.
#
# So the first visit is quick too: what a write ended is asked for again right behind it, the same read isn't asked twice at
# once, and a start asks in parallel for what was in use last time (only the queries are saved for that, never the answers).
#
# COOPCOACH_READ_CACHE=1 turns it on for SQLite too (to test it, and for tools/bench.py); a local file doesn't need it.
_READ_TTL = 300
_READ_MAX_ENTRIES = 4000
_READ_MAX_ROWS = 12000  # more than every board (about 5,000 and growing); less than the postings, which have their own copy
_UNCACHED = {"agent_tickets"}
_WRITE_RE = re.compile(r"^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM|"
                       r"DROP\s+TABLE(?:\s+IF\s+EXISTS)?|ALTER\s+TABLE|TRUNCATE(?:\s+TABLE)?)\s+\"?([A-Za-z_]+)", re.I)
_READ_TABLES_RE = re.compile(r"\b(?:FROM|JOIN)\s+\"?([A-Za-z_]+)", re.I)
_read_lock = threading.Lock()
_reads: dict = {}
_table_gen: dict[str, int] = {}
_all_gen = [0]
_stamp_seen = [0]
_pending = threading.local()
_refresher = [False]
_inflight: dict = {}
_wanted: dict = {}  # reads a write just ended, which were in use: {key: last used}
_wake = threading.Event()
_REFILL_WORKERS = 3
_REFILL_RECENT = 600  # seconds: a read not used this recently isn't worth asking for again
_HOT_MAX = 150
_WARM_WORKERS = 5
round_trips = [0]  # how many queries really left this process (tools/bench.py)


def _read_cache_on() -> bool:
    import os
    want = os.environ.get("COOPCOACH_READ_CACHE")
    return want == "1" if want in ("0", "1") else _postgres() is not None


def _stamp_file():
    return DB_PATH.with_name(".db-changed")


def _check_stamp() -> None:
    """Another process on this computer wrote: everything read before that may be out of date."""
    try:
        m = _stamp_file().stat().st_mtime_ns
    except OSError:
        return
    if m != _stamp_seen[0]:
        with _read_lock:  # (this process's own mark is made under this lock, so it can't be taken for someone else's)
            if m > _stamp_seen[0]:
                _stamp_seen[0] = m
                _all_gen[0] += 1
                _reads.clear()


def _note_write(sql: str) -> None:
    m = _WRITE_RE.match(sql)
    if not m:
        return
    if not hasattr(_pending, "tables"):
        _pending.tables = set()
    _pending.tables.add(m.group(1).lower())
    _bump({m.group(1).lower()}, stamp=False)


def _bump(tables: set[str], stamp: bool = True) -> None:
    with _read_lock:
        for t in tables:
            _table_gen[t] = _table_gen.get(t, 0) + 1
        now_ = time.time()
        for k in [k for k, e in _reads.items() if e["tables"] & tables]:
            if _refresher[0] and now_ - _reads[k]["used"] < _REFILL_RECENT:
                _wanted[k] = _reads[k]["used"]
            del _reads[k]
    if stamp:
        _check_stamp()  # another process wrote just before this: don't let this write's mark hide that
        try:
            f = _stamp_file()
            with _read_lock:
                f.touch()
                _stamp_seen[0] = f.stat().st_mtime_ns
        except OSError:
            pass
        if _wanted:
            _wake.set()


def _flush_writes() -> None:
    tables = getattr(_pending, "tables", None)
    if tables:
        _pending.tables = set()
        _bump(tables)


def forget_reads() -> None:
    with _read_lock:
        _all_gen[0] += 1
        _reads.clear()


def table_version(*tables: str) -> tuple:
    """Changes whenever one of these tables is written (here or by another process on this computer)."""
    _check_stamp()
    return (_all_gen[0], *(_table_gen.get(t, 0) for t in tables))


def _raw(sql: str, params, one: bool) -> list[dict]:
    round_trips[0] += 1
    pg = _postgres()
    if pg:
        return pg.rows(sql, params, one=one) if one else pg.rows(sql, params)
    if one:
        r = conn().execute(sql, params).fetchone()
        return [dict(r)] if r else []
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def _read(sql: str, params, one: bool = False) -> list[dict]:
    if not re.match(r"\s*(?:SELECT|WITH)\b", sql, re.I):  # a write that returns rows
        _note_write(sql)
        out = _raw(sql, params, one)
        _flush_writes()
        return out
    if not _read_cache_on():
        return _raw(sql, params, one)
    tables = {t.lower() for t in _READ_TABLES_RE.findall(sql)}
    if not tables or tables & _UNCACHED or tables - set(COLUMNS) - {"meta"}:
        return _raw(sql, params, one)
    _check_stamp()
    try:
        key = (sql, tuple(params) if not isinstance(params, dict) else tuple(sorted(params.items())), one)
        hash(key)
    except TypeError:
        return _raw(sql, params, one)
    now_ = time.time()
    with _read_lock:
        e = _reads.get(key)
        if e and now_ - e["at"] < _READ_TTL:
            e["used"] = now_
            return [dict(r) for r in e["rows"]]
        gen = (_all_gen[0], *(_table_gen.get(t, 0) for t in sorted(tables)))
        job = _inflight.get(key)
        mine = not job or job["gen"] != gen  # asked before a write since? then it's not an answer to this question
        if mine:
            job = _inflight[key] = {"gen": gen, "done": threading.Event(), "rows": None}
    if not mine:  # the same read is on its way: wait for it rather than ask again
        if job["done"].wait(35) and job["rows"] is not None:
            return [dict(r) for r in job["rows"]]
        return _raw(sql, params, one)
    try:
        got = _raw(sql, params, one)
        job["rows"] = got
    except Exception as err:  # the internet dropped: what was read last is better than an error
        if e and type(err).__name__ in ("OperationalError", "PoolTimeout"):
            return [dict(r) for r in e["rows"]]
        raise
    finally:
        with _read_lock:
            if _inflight.get(key) is job:
                del _inflight[key]
        job["done"].set()
    if len(got) <= _READ_MAX_ROWS:
        with _read_lock:
            if gen == (_all_gen[0], *(_table_gen.get(t, 0) for t in sorted(tables))):  # nothing wrote while it was asked
                if len(_reads) >= _READ_MAX_ENTRIES:
                    for k in sorted(_reads, key=lambda k: _reads[k]["used"])[:_READ_MAX_ENTRIES // 4]:
                        del _reads[k]
                _reads[key] = {"rows": got, "at": now_, "used": now_, "tables": tables}
    return [dict(r) for r in got]


def refresh_reads(limit: int = 25) -> int:
    """Ask again for what's in use and getting old, so a page finds it fresh instead of waiting."""
    now_ = time.time()
    with _read_lock:
        due = sorted((k for k, e in _reads.items() if now_ - e["at"] > _READ_TTL * 0.7 and now_ - e["used"] < 1800),
                     key=lambda k: _reads[k]["at"])[:limit]
        for k in [k for k, e in _reads.items() if now_ - e["used"] >= 1800]:
            del _reads[k]
    done = 0
    for key in due:
        sql, params, one = key
        with _read_lock:
            e = _reads.get(key)
            if not e:
                continue
            gen = (_all_gen[0], *(_table_gen.get(t, 0) for t in sorted(e["tables"])))
            tables = e["tables"]
        try:
            got = _raw(sql, params, one)
        except Exception:
            return done  # offline: what's there stays
        with _read_lock:
            if key in _reads and gen == (_all_gen[0], *(_table_gen.get(t, 0) for t in sorted(tables))):
                _reads[key].update(rows=got, at=time.time())
                done += 1
    return done


def _ask_again(keys: list, workers: int, used: dict | None = None) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def one(key):
        sql, params, single = key
        try:
            _read(sql, params, single)
            if used and key in used:  # asking on its own isn't using it: what nobody looks at can stop being asked for
                with _read_lock:
                    if key in _reads:
                        _reads[key]["used"] = used[key]
        except Exception as e:  # offline, or a query from an older version
            log.debug("asking again: %s", e)
    with ThreadPoolExecutor(workers, thread_name_prefix="read-ask") as pool:
        list(pool.map(one, keys))


def refill_wanted() -> int:
    """Ask again for what a write just ended, so the next page finds it instead of waiting for each read in turn."""
    with _read_lock:
        used = dict(list(_wanted.items())[:60])
        _wanted.clear()
    if used:
        _ask_again(list(used), _REFILL_WORKERS, used)
    return len(used)


def _hot_file():
    return DB_PATH.with_name("cache") / "hot_reads.json"


def _about_now(p) -> bool:
    return isinstance(p, int) and not isinstance(p, bool) and now() - 400 * 86400 <= p <= now() + 400 * 86400


def _worth_keeping(p) -> bool:
    """A query's parameter, for asking it again next start: not a moment that won't come again (this_hour() and what's counted
    from it can be moved on to the new hour) and not text that could be anything: ids and short keys, never what was read."""
    if p is None or isinstance(p, bool):
        return True
    if isinstance(p, int):
        return not _about_now(p) or p % 3600 == 0
    return isinstance(p, str) and len(p) <= 80


def save_hot_reads() -> int:
    with _read_lock:
        keys = sorted(_reads, key=lambda k: -_reads[k]["used"])
    keep = [[sql, list(params), one] for sql, params, one in keys if all(_worth_keeping(p) for p in params)][:_HOT_MAX]
    if not keep:
        return 0
    try:
        f = _hot_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"hour": this_hour(), "reads": keep}), encoding="utf-8")
        tmp.replace(f)
    except OSError as e:
        log.info("couldn't save what's in use: %s", e)
    return len(keep)


def warm_start() -> int:
    """Ask in parallel for what was in use when the app last ran. Whatever the page asks for meanwhile waits for these."""
    try:
        saved = json.loads(_hot_file().read_text(encoding="utf-8"))
        moved = this_hour() - saved["hour"]
        keys = [(sql, tuple(p + moved if _about_now(p) and p % 3600 == 0 else p for p in params), bool(one)) for sql, params, one in saved["reads"][:_HOT_MAX]]
    except (OSError, ValueError, TypeError, KeyError):
        return 0
    _ask_again(keys, _WARM_WORKERS)
    return len(keys)


def start_refreshing(every: float = 30) -> None:
    """Keep what's in use fresh, in the background (only where reads are slow)."""
    if _refresher[0] or not _read_cache_on():
        return
    _refresher[0] = True

    def loop():
        warm_start()
        while True:
            time.sleep(every)
            try:
                refresh_reads()
                save_hot_reads()
            except Exception as e:
                log.info("refreshing reads: %s", e)

    def refill():
        last = 0.0
        while True:
            _wake.wait()
            time.sleep(max(0.05, 1 - (time.time() - last)))  # the transaction is finishing; and a sync writes constantly
            _wake.clear()
            last = time.time()
            try:
                refill_wanted()
            except Exception as e:
                log.info("asking again: %s", e)
    threading.Thread(target=loop, daemon=True, name="read-refresh").start()
    threading.Thread(target=refill, daemon=True, name="read-refill").start()


@contextmanager
def tx():
    """Serialized write transaction (SQLite allows one writer at a time anyway)."""
    with _write_lock:
        pg = _postgres()
        try:
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
        finally:
            _flush_writes()


# columns added after the first release; init() adds them to older databases
_MIGRATIONS = {
    "profiles": {"pass_hash": "TEXT", "pass_salt": "TEXT", "parser_version": "INTEGER"},
    "companies": {"last_checked": "INTEGER", "removed": "INTEGER NOT NULL DEFAULT 0", "origin": "TEXT",
                  "plan": "TEXT",  # plan: how a Workday board is best read (its internship filter), as JSON
                  "central": "INTEGER NOT NULL DEFAULT 0"},  # 1 = the shared feed reads it (see central.py)
    "sync_runs": {"kind": "TEXT"},  # full | quick (the check of the boards that matter most, between full syncs)
    "applications": {
        "company": "TEXT", "title": "TEXT", "url": "TEXT", "source": "TEXT", "applied_at": "INTEGER",
        "last_email_at": "INTEGER", "last_email_subject": "TEXT", "req_id": "TEXT",
        "interview_at": "INTEGER", "oa_due_at": "INTEGER",  # put on the calendar feed with a reminder
    },
    "jobs": {"req_id": "TEXT",  # the company's requisition number (Workday R-0000181145, ...)
             "requirements": "TEXT", "form_checked": "INTEGER NOT NULL DEFAULT 0",
             "fp": "TEXT",  # fingerprint of what the board last sent (a sync skips rewriting unchanged postings)
             "changed_at": "INTEGER",  # when anything about it last changed (opened, edited, closed): what apps pull
             "imported_at": "INTEGER",  # local receipt of a changed feed row; its changed_at is the crawler's clock
             "opened_at": "INTEGER"},  # when it went up, if it's a real new opening (not a board's backlog)
    "email_events": {"req_ids": "TEXT"},  # requisition numbers quoted in the email, comma-separated
    "portal_accounts": {"uses_default": "INTEGER NOT NULL DEFAULT 0"},  # legacy account metadata; monitoring removed
    "email_accounts": {"enabled": "INTEGER NOT NULL DEFAULT 1"},  # the per-inbox "track emails" switch
    "events": {"region": "TEXT"},  # Waterloo Region, Toronto / GTA, SF Bay Area, Online, ...
    "event_feeds": {"enabled": "INTEGER NOT NULL DEFAULT 1",  # paused sources stay listed but aren't fetched
                    "grp": "TEXT",  # Canadian universities, US universities, Toronto & Waterloo, Cities, Hackathons
                    "region": "TEXT",  # where the source is (a campus building name says nothing about the city)
                    "last_fetched": "INTEGER"},  # university calendars are read at most twice a day
    "ready_queue": {"skip_reason": "TEXT", "note": "TEXT", "polish": "TEXT", "polish_tries": "INTEGER"},  # "Not for me" reasons teach Autopilot; note = closed/expired
    "autopilot_runs": {"notified": "INTEGER NOT NULL DEFAULT 1", "picks": "TEXT"},  # 0 = alert held for quiet hours
    "reminders": {"ref": "TEXT"},  # what a reminder is for ("niche:quant:2027-07"), so it isn't set twice
}


# indexes on columns _MIGRATIONS adds (an older database only has them once its migrations ran)
AFTER_MIGRATIONS = ["CREATE INDEX IF NOT EXISTS idx_jobs_changed ON jobs(changed_at)",
                    "CREATE INDEX IF NOT EXISTS idx_jobs_imported ON jobs(imported_at)"]
# features taken out (interview practice, quiz lessons): their tables go too, or deleting a person would leave their
# rows behind (the list of tables to clear comes from SCHEMA) and moving to Supabase would trip on them
RETIRED_TABLES = ["practice_sessions", "practice_answers", "drill_results", "lesson_done",
                  "coding_custom"]  # problems added by hand: the Path is the NeetCode 150 only
RETIRED_DOCS = ["weakspots", "workbook"]  # documents those features wrote


def table_ident(t: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", t):
        raise ValueError(f"not a table name: {t}")
    return t


def schema_columns() -> dict[str, list[str]]:
    """table -> the columns its CREATE TABLE in SCHEMA defines (the ones _MIGRATIONS adds are checked separately)."""
    out = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA, re.S):
        cols = []
        for line in m.group(2).split("\n"):
            mm = re.match(r"\s*(\w+)\s+(?:INTEGER|TEXT|REAL|BLOB)\b", re.sub(r"--.*", "", line))
            if mm and mm.group(1).upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK"):
                cols.append(mm.group(1))
        out[m.group(1)] = cols
    return out


def _stale_tables(columns_of, count_of) -> list[str]:
    """Tables an older Co-op Coach made with a different layout (CREATE TABLE IF NOT EXISTS keeps them as they are).
    Empty ones are returned to be made again; one that holds data is left alone and logged."""
    stale = []
    for table, cols in schema_columns().items():
        have = columns_of(table)
        missing = [col for col in cols if col not in have]
        if not have or not missing:
            continue
        if count_of(table):
            log.warning("table %s is missing %s but holds data: left as it is", table, missing)
        else:
            stale.append(table)
    return stale


def init() -> None:
    pg = _postgres()
    if pg:
        pg.init()
        return
    with tx() as c:
        for table in _stale_tables(lambda t: {r["name"] for r in c.execute(f"PRAGMA table_info({t})")},
                                   lambda t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]):
            c.execute(f"DROP TABLE {table}")
        c.executescript(SCHEMA)
        for table, cols in _MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        for stmt in AFTER_MIGRATIONS:
            c.execute(stmt)
        for t in RETIRED_TABLES:
            c.execute(f"DROP TABLE IF EXISTS {table_ident(t)}")
        c.execute(f"DELETE FROM documents WHERE kind IN ({','.join('?' * len(RETIRED_DOCS))})", RETIRED_DOCS)
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
    return _read(sql, params)


def row(sql: str, params=()) -> dict | None:
    got = _read(sql, params, one=True)
    return got[0] if got else None


def this_hour() -> int:
    """The time, to the hour: for reads keyed on the time ("from now on"), so the same read later is still the same read."""
    n = now()
    return n - n % 3600


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


def _unquoted(s: str) -> str:
    """The rewrites for SQL outside string literals."""
    s = re.sub(r"(\w+(?:\.\w+)?|\?) COLLATE NOCASE", r"lower(\1)", s)
    s = re.sub(r"\bLIKE\b", "ILIKE", s)
    return s.replace("%", "%%").replace("?", "%s")


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
    # every literal % doubled; outside quotes, x COLLATE NOCASE -> lower(x), LIKE -> ILIKE and ? -> %s
    # (string literals keep their text, and "? COLLATE NOCASE" becomes lower(%s))
    parts = re.split(r"('[^']*(?:'|$))", q)  # odd items are the quoted parts
    q = "".join(p.replace("%", "%%") if i % 2 else _unquoted(p) for i, p in enumerate(parts))
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
        _note_write(sql)
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
        _note_write(sql)
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
        # keepalives: Supabase drops a connection that sits silent (a long background job, the app left open
        # overnight); TCP keepalives keep it talking, idle extras close after two minutes, and a read that still
        # meets a dropped connection is asked again once (see rows)
        self.pool = ConnectionPool(conninfo, min_size=min_size, max_size=max_size, open=True, timeout=30, max_idle=120,
                                   kwargs={"row_factory": _row_factory, "prepare_threshold": None, "autocommit": True,
                                           "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10,
                                           "keepalives_count": 3})
        atexit.register(self.pool.close)

    @contextmanager
    def tx(self):
        with self.pool.connection() as c:
            with c.transaction():
                yield _Tx(c)

    def rows(self, sql: str, params=(), one: bool = False) -> list[dict]:
        import psycopg
        q, _ = translate(sql)
        for attempt in (1, 2):
            try:
                with self.pool.connection() as c:
                    cur = c.execute(q, _params(params))
                    if not cur.description:
                        return []
                    got = [cur.fetchone()] if one else cur.fetchall()
                    return [dict(r) for r in got if r is not None]
            except psycopg.OperationalError:  # a connection the server dropped: the pool discards it; ask once more
                if attempt == 2 or not re.match(r"(?i)\s*(SELECT|WITH)\b", q):
                    raise
        return []

    def reset(self) -> None:
        """Tests only: empty the scratch schema."""
        with self.pool.connection() as c:
            for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()").fetchall():
                c.execute(f'DROP TABLE IF EXISTS "{r["tablename"]}" CASCADE')

    def init(self) -> None:
        import hashlib
        import os
        # the same tables as last time: one round trip instead of a hundred (this runs on every start)
        shape = hashlib.sha256(repr((SCHEMA, _MIGRATIONS, AFTER_MIGRATIONS, RETIRED_TABLES)).encode()).hexdigest()[:16]
        if os.environ.get("COOPCOACH_PG_RESET") != "1":
            try:
                got = self.rows("SELECT value FROM meta WHERE key='schema_shape'", one=True)
                if got and got[0]["value"] == shape:
                    return
            except Exception:  # no meta table yet
                pass
        self._init_schema()
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_shape', ?)", (shape,))

    def _init_schema(self) -> None:
        import os
        if os.environ.get("COOPCOACH_PG_RESET") == "1" and os.environ.get("COOPCOACH_PG_SCHEMA", "").endswith("_test"):
            os.environ["COOPCOACH_PG_RESET"] = "0"  # once per test process; MCP servers it starts keep the data
            self.reset()
        schema = os.environ.get("COOPCOACH_PG_SCHEMA")
        if schema and re.fullmatch(r"[a-z_][a-z0-9_]*", schema):  # a scratch schema for tests
            with self.pool.connection() as c:  # only when missing: Supabase's app role may not create schemas
                if not c.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", (schema,)).fetchone():
                    c.execute(f"CREATE SCHEMA {schema}")
        with self.pool.connection() as c:
            def columns_of(t):
                return {r["column_name"] for r in c.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND"
                    " table_name=%s", (t,))}
            for table in _stale_tables(columns_of, lambda t: c.execute(
                    f"SELECT COUNT(*) AS n FROM {table_ident(t)}").fetchone()["n"]):
                c.execute(f"DROP TABLE {table_ident(table)} CASCADE")
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
            for stmt in AFTER_MIGRATIONS:
                c.execute(stmt)
            for t in RETIRED_TABLES:
                c.execute(f"DROP TABLE IF EXISTS {table_ident(t)} CASCADE")
            c.execute(f"DELETE FROM documents WHERE kind IN ({','.join(['%s'] * len(RETIRED_DOCS))})", RETIRED_DOCS)

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
