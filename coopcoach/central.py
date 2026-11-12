"""The shared job feed: one crawler reads every board a few times a day into its own schema on Supabase, and each
Co-op Coach pulls only what changed since it last looked, instead of reading ~9,500 boards itself.

* The crawler is the ordinary sync (`python -m coopcoach crawl`), run against a database with nobody's data in it.
  GitHub Actions runs it at the times in CRAWLS (.github/workflows/crawl.yml): full reads at posting times, and
  quick checks of the boards that just posted in between.
* jobs.changed_at moves whenever a posting changes (new, edited, reopened, closed, its full text read), and
  jobs.opened_at marks a real new opening (not a board's backlog the first time it's read): alerts go out for those.
* An app in the same Supabase project copies the changed rows inside the database ("schema" mode): nothing travels
  through this computer. Any other app reads them over Supabase's REST API ("rest" mode). That needs the feed opened
  to the public (publish_sql): its owner decides when.
* Scores, applications, resumes and everything else about a person stay in their own database. Boards someone added
  that the feed doesn't read are still read by their own app.
"""

import json
import logging
import os
import re
import time
from pathlib import Path

from . import config, db, store

log = logging.getLogger(__name__)
SETTINGS = config.DATA_DIR / "central.json"
SCHEMA = "coopcoach_central"
ROLE = "coopcoach_crawler"
OVERLAP = 600  # each pull looks back 10 minutes before the last one: a crawl's rows can commit after a pull started
PAGE = 1000     # rows per REST page (Supabase's default cap)
# what the crawler's workflow runs, in UTC (7:30, 12:30 and 17:30 Eastern for full reads; checks in between):
# companies post during North American business hours. tests check the workflow file says the same.
CRAWLS = {"full": ["30 11 * * *", "30 16 * * *", "30 21 * * *"],
          "quick": ["0 14 * * *", "0 19 * * *", "30 0 * * *"],
          "discover": ["0 9 * * 0"]}
# imported_at belongs to each app, not the feed: older published views need not expose it.
JOB_COLS = [c for c in db.COLUMNS["jobs"]] + [c for c in db._MIGRATIONS["jobs"]
                                             if c not in db.COLUMNS["jobs"] and c != "imported_at"]
# what an app needs about each board: whether the feed reads it, and how it's doing (Settings, niches, startups)
BOARD_COLS = ["ats", "slug", "name", "tier", "last_status", "last_count", "last_checked"]


def settings() -> dict:
    path = Path(os.environ.get("COOPCOACH_CENTRAL") or SETTINGS)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        return {}


def save_settings(d: dict) -> None:
    SETTINGS.write_text(json.dumps(d, indent=2), encoding="utf-8")
    SETTINGS.chmod(0o600)


def crawler() -> bool:
    """This process is the crawler itself (it writes the feed; it never pulls from it)."""
    return os.environ.get("COOPCOACH_CRAWLER") == "1"


def mode() -> str:
    """'schema' (same Supabase project), 'rest' (any other app), or '' (this app reads every board itself)."""
    s = settings()
    if crawler() or os.environ.get("COOPCOACH_DEMO") or not s.get("on"):
        return ""
    if s.get("mode") == "schema" and db.is_postgres():
        return "schema"
    if s.get("mode") == "rest" and s.get("url") and s.get("key"):
        return "rest"
    return ""


def enabled() -> bool:
    return bool(mode())


def _schema() -> str:
    name = settings().get("schema") or SCHEMA
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"not a schema name: {name}")
    return name


def available() -> bool:
    """The feed's schema is in this Supabase project and this app may read it."""
    if not db.is_postgres():
        return False
    try:
        r = db.row("SELECT has_table_privilege(?, 'SELECT') AS ok", (f"{_schema()}.jobs",))
        return bool(r and r["ok"])
    except Exception:
        return False


def turn_on() -> dict:
    if not available():
        return {"error": "The shared feed isn't set up in this Supabase project yet"}
    save_settings({**settings(), "on": True, "mode": "schema", "schema": _schema()})
    return {"ok": True, **pull()}


def turn_off() -> dict:
    save_settings({**settings(), "on": False})
    return {"ok": True}


# --- pulling ---------------------------------------------------------------------------------------------------

def _set_clause() -> str:
    """How a pulled row lands on this app's copy: the feed wins, except that a description, the skills and flags read
    from it, and who a role is open to (read from its application form) that this app found itself are kept when the
    feed has none."""
    keep_text = "COALESCE(excluded.description, '') = '' AND COALESCE(jobs.description, '') != ''"
    special = {
        "description": f"CASE WHEN {keep_text} THEN jobs.description ELSE excluded.description END",
        "skills": f"CASE WHEN {keep_text} THEN jobs.skills ELSE excluded.skills END",
        "flags": f"CASE WHEN {keep_text} THEN jobs.flags ELSE excluded.flags END",
        "requirements": "CASE WHEN jobs.form_checked = 1 AND excluded.form_checked = 0 THEN jobs.requirements"
                        f" WHEN {keep_text} THEN jobs.requirements ELSE excluded.requirements END",
        "form_checked": "MAX(jobs.form_checked, excluded.form_checked)",
    }
    return ", ".join(f"{c}={special.get(c, f'excluded.{c}')}" for c in JOB_COLS if c != "id")


# rows looked at again (the look-back) and already up to date aren't written again
_CHANGED = " WHERE COALESCE(jobs.changed_at, -1) != COALESCE(excluded.changed_at, -1)"


def _since() -> int:
    v = db.meta_get("central_since")
    return max(0, int(v) - OVERLAP) if v else 0


def pull() -> dict:
    """Bring this app's postings up to date with the feed. Returns the new postings, the real new openings among them
    (what alerts are for; none on the very first pull) and how many closed."""
    m, t0 = mode(), time.time()
    if not m:
        return {"skipped": "the shared feed is off"}
    first = db.meta_get("central_since") is None
    since = _since()
    out = _pull_schema(since) if m == "schema" else _pull_rest(since)
    if first:
        out["opened"] = []  # everything was already open when this app joined the feed
    boards = _pull_boards(m)
    # the newest change seen (the feed's own clock, not this computer's), or where the last pull left off
    db.meta_set("central_since", out.pop("high") or db.meta_get("central_since") or 0)
    db.meta_set("central_pulled_at", int(time.time()))
    store.invalidate_cache()
    out.update(boards=boards, seconds=round(time.time() - t0, 1), first=first)
    db.meta_set("central_last", db.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}))
    log.info("shared feed: %s new, %s changed, %s closed in %ss", len(out["new"]), out["changed"], out["closed"],
             out["seconds"])
    return out


def _pull_schema(since: int) -> dict:
    src = f"{_schema()}.jobs"
    high = (db.row(f"SELECT MAX(changed_at) AS t FROM {src}") or {}).get("t")
    fresh = db.rows(f"SELECT c.id, c.opened_at FROM {src} c LEFT JOIN jobs j ON j.id = c.id"
                    " WHERE COALESCE(c.changed_at, 0) >= ? AND j.id IS NULL", (since,))
    closed = db.row(f"SELECT COUNT(*) AS n FROM {src} c JOIN jobs j ON j.id = c.id WHERE COALESCE(c.changed_at, 0) >= ?"
                    " AND c.active = 0 AND j.active = 1", (since,))["n"]
    cols = ", ".join(JOB_COLS)
    with db.tx() as c:  # one statement, inside the database: the rows never come down to this computer
        n = c.execute(f"INSERT INTO jobs ({cols}, imported_at) SELECT {cols}, ? FROM {src} WHERE COALESCE(changed_at, 0) >= ?"
                      f" ON CONFLICT(id) DO UPDATE SET {_set_clause()}, imported_at=excluded.imported_at{_CHANGED}",
                      (db.now(), since)).rowcount
    return {"new": [r["id"] for r in fresh], "opened": [r["id"] for r in fresh if (r["opened_at"] or 0) >= since],
            "changed": n, "closed": closed, "high": high}


def _client():
    from .sources import client
    return client(timeout=60)


def _rest(path: str, params: dict) -> list[dict]:
    s = settings()
    url = s["url"].rstrip("/") + "/rest/v1/" + path
    with _client() as c:
        r = c.get(url, params=params, headers={"apikey": s["key"], "Authorization": f"Bearer {s['key']}",
                                               "Accept-Encoding": "gzip"})
        r.raise_for_status()
        return r.json()


def _rest_rows(since: int, first: bool) -> list[dict]:
    """Every changed posting, a page at a time, ordered by (changed_at, id) so a page never skips or repeats one.
    No descriptions: they're fetched one at a time when someone opens a posting (see description)."""
    cols = ",".join(c for c in JOB_COLS if c != "description")
    out, last = [], None
    while True:
        params = {"select": cols, "order": "changed_at.asc,id.asc", "limit": str(PAGE)}
        if last:
            params["or"] = f"(changed_at.gt.{last[0]},and(changed_at.eq.{last[0]},id.gt.{last[1]}))"
        else:
            params["changed_at"] = f"gte.{since}"
        if first:
            params["active"] = "eq.1"  # joining: only what's open (closed postings don't matter to a new app)
        page = _rest("coopcoach_jobs", params)
        out += page
        if len(page) < PAGE:
            return out
        last = (page[-1]["changed_at"], page[-1]["id"])


def _pull_rest(since: int) -> dict:
    rows = _rest_rows(since, first=since == 0 and db.meta_get("central_since") is None)
    if not rows:
        return {"new": [], "opened": [], "changed": 0, "closed": 0, "high": None}
    ids = [r["id"] for r in rows]
    have: dict[str, dict] = {}
    for i in range(0, len(ids), 800):
        chunk = ids[i:i + 800]
        have.update({r["id"]: r for r in db.rows(
            f"SELECT id, active, changed_at FROM jobs WHERE id IN ({','.join('?' * len(chunk))})", chunk)})
    cols = [c for c in JOB_COLS if c != "description"]
    todo = [r for r in rows if r["id"] not in have or have[r["id"]]["changed_at"] != r["changed_at"]]
    for k in range(0, len(todo), 500):  # a batch at a time: a first pull is 20,000 rows, and a save shouldn't wait for them all
        with db.tx() as c:
            received = db.now()
            c.executemany(f"INSERT INTO jobs ({', '.join(cols)}, description, imported_at)"
                          f" VALUES ({', '.join('?' * len(cols))}, '', ?) ON CONFLICT(id) DO UPDATE SET"
                          f" {_set_clause()}, imported_at=excluded.imported_at{_CHANGED}",
                          [[*[r.get(c2) for c2 in cols], received] for r in todo[k:k + 500]])
    fresh = [r for r in rows if r["id"] not in have]
    return {"new": [r["id"] for r in fresh], "opened": [r["id"] for r in fresh if (r.get("opened_at") or 0) >= since],
            "changed": len(todo), "closed": sum(1 for r in todo if not r["active"] and (have.get(r["id"]) or {}).get(
                "active") == 1), "high": max(r["changed_at"] or 0 for r in rows)}


def description(job_id: str) -> str:
    """A posting's full text from the feed (rest mode keeps descriptions out of the pull to keep it small)."""
    if mode() != "rest":
        return ""
    try:
        got = _rest("coopcoach_jobs", {"select": "description", "id": f"eq.{job_id}"})
        return (got[0].get("description") or "") if got else ""
    except Exception as e:
        log.info("shared feed description failed: %s", e)
        return ""


def _pull_boards(m: str) -> int:
    """Which boards the feed reads (this app then reads only the rest), with each one's latest status: once per new
    crawl, since a crawl touches nearly every board."""
    last = _last_crawl(m)
    if not last or str(last.get("finished_at")) == db.meta_get("central_boards_at"):
        return 0
    if m == "schema":
        s = _schema()
        cols = ", ".join(BOARD_COLS)
        with db.tx() as c:
            n = c.execute(f"INSERT INTO companies ({cols}, central) SELECT {cols}, 1 FROM {s}.companies WHERE enabled=1"
                          " AND removed=0 ON CONFLICT(ats, slug) DO UPDATE SET name=excluded.name, tier=excluded.tier,"
                          " last_status=excluded.last_status, last_count=excluded.last_count,"
                          " last_checked=excluded.last_checked, central=1").rowcount
            day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 86400))
            c.execute(f"INSERT INTO board_roles (board, day, roles, interns) SELECT board, day, roles, interns FROM"
                      f" {s}.board_roles WHERE day >= ? ON CONFLICT(board, day) DO UPDATE SET roles=excluded.roles,"
                      " interns=excluded.interns", (day,) if db.meta_get("central_boards_at") else ("",))
            c.execute(f"INSERT INTO firm_openings (company_key, term, opened_at) SELECT company_key, term, opened_at"
                      f" FROM {s}.firm_openings ON CONFLICT(company_key, term) DO UPDATE SET"
                      " opened_at=MIN(firm_openings.opened_at, excluded.opened_at)")
    else:
        rows, offset = [], 0
        while True:
            page = _rest("coopcoach_companies", {"select": ",".join(BOARD_COLS), "order": "ats.asc,slug.asc",
                                                  "limit": str(PAGE), "offset": str(offset)})
            rows += page
            offset += PAGE
            if len(page) < PAGE:
                break
        with db.tx() as c:
            c.executemany(f"INSERT INTO companies ({', '.join(BOARD_COLS)}, central) VALUES"
                          f" ({', '.join('?' * len(BOARD_COLS))}, 1) ON CONFLICT(ats, slug) DO UPDATE SET"
                          " name=excluded.name, tier=excluded.tier, last_status=excluded.last_status,"
                          " last_count=excluded.last_count, last_checked=excluded.last_checked, central=1",
                          [[r.get(k) for k in BOARD_COLS] for r in rows])
        n = len(rows)
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 3 * 86400))
        roles = _rest("coopcoach_board_roles", {"select": "board,day,roles,interns", "day": f"gte.{day}"})
        with db.tx() as c:
            c.executemany("INSERT INTO board_roles (board, day, roles, interns) VALUES (?,?,?,?) ON CONFLICT(board,"
                          " day) DO UPDATE SET roles=excluded.roles, interns=excluded.interns",
                          [(r["board"], r["day"], r["roles"], r["interns"]) for r in roles])
    db.meta_set("central_boards_at", last["finished_at"])
    return n


def _last_crawl(m: str | None = None) -> dict | None:
    m = m or mode()
    try:
        if m == "schema":
            return db.row(f"SELECT finished_at, kind, new_jobs FROM {_schema()}.sync_runs WHERE finished_at IS NOT NULL"
                          " ORDER BY finished_at DESC LIMIT 1")
        if m == "rest":
            got = _rest("coopcoach_runs", {"select": "finished_at,kind,new_jobs", "finished_at": "not.is.null",
                                           "order": "finished_at.desc", "limit": "1"})
            return got[0] if got else None
    except Exception as e:
        log.info("shared feed status failed: %s", e)
    return None


STALE_HOURS = 12


def fresh() -> bool:
    """The feed's crawler ran in the last half day. If it stops (the schedule off, the crawler failing), each app
    reads every board itself again until it's back, rather than quietly showing nothing new."""
    last = _last_crawl()
    return bool(last and (last.get("finished_at") or 0) > time.time() - STALE_HOURS * 3600)


def local_boards() -> list[dict]:
    """Boards this app reads itself while on the feed: ones added here that the feed doesn't read (yet)."""
    return db.rows("SELECT * FROM companies WHERE enabled=1 AND removed=0 AND central=0"
                   " AND origin IN ('you', 'niche', 'startups')")


def crawl_times() -> list[str]:
    """The full crawls in this computer's own time, e.g. ['7:30 AM', '12:30 PM', '5:30 PM']."""
    day = int(time.time() // 86400 * 86400)  # today, midnight UTC
    out = []
    for cron in CRAWLS["full"]:
        minute, hour = (int(x) for x in cron.split()[:2])
        out.append(time.strftime("%I:%M %p", time.localtime(day + hour * 3600 + minute * 60)).lstrip("0"))
    return out


def status() -> dict:
    m = mode()
    s = settings()
    out = {"on": bool(m), "mode": m, "available": available() if not m else True, "crawls": crawl_times(),
           "pulled_at": int(db.meta_get("central_pulled_at") or 0) or None,
           "last_pull": db.loads(db.meta_get("central_last"), None)}
    if m:
        out["last_crawl"] = _last_crawl(m)
        out["stale"] = not fresh()
        out["local_boards"] = len(local_boards())
        out["open"] = db.row("SELECT COUNT(*) AS n FROM jobs WHERE active=1")["n"]
    out["was_on"] = bool(s.get("on"))
    return out


# --- setting it up (once, by the project's owner) ----------------------------------------------------------------

def setup_sql(verifier: str, app_role: str = "coopcoach_app") -> str:
    """The crawler's own role and schema. The app may read the feed and nothing else; the crawler can't see anyone's
    data. Run as the project owner (the Supabase SQL editor, or Claude through the Supabase connector)."""
    return f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
    CREATE ROLE {ROLE} LOGIN PASSWORD '{verifier}';
  ELSE
    ALTER ROLE {ROLE} LOGIN PASSWORD '{verifier}';
  END IF;
END $$;
GRANT {ROLE} TO postgres;
CREATE SCHEMA IF NOT EXISTS {SCHEMA} AUTHORIZATION {ROLE};
ALTER ROLE {ROLE} SET search_path = {SCHEMA};
GRANT USAGE ON SCHEMA {SCHEMA} TO postgres, {app_role};
ALTER DEFAULT PRIVILEGES FOR ROLE {ROLE} IN SCHEMA {SCHEMA} GRANT ALL ON TABLES TO postgres;
ALTER DEFAULT PRIVILEGES FOR ROLE {ROLE} IN SCHEMA {SCHEMA} GRANT ALL ON SEQUENCES TO postgres;
ALTER DEFAULT PRIVILEGES FOR ROLE {ROLE} IN SCHEMA {SCHEMA} GRANT SELECT ON TABLES TO {app_role};
"""


def crawler_conninfo() -> str:
    """How the crawler signs in. Its password is made on this computer and kept in the Keychain (Supabase only has
    a one-way hash of it): used for a crawl run from here, and for the secret the scheduled crawls read."""
    from psycopg.conninfo import make_conninfo

    from . import supa, vault
    s = supa.settings()
    pw = vault.get(f"{supa.keychain_key(s['ref'])}:crawler")
    if not pw:
        raise RuntimeError("The crawler's password isn't in the Keychain")
    return make_conninfo(host=s["host"], port=s.get("port", 5432), dbname=s.get("dbname", "postgres"),
                         user=f"{ROLE}.{s['ref']}", password=pw, sslmode="require", connect_timeout=15,
                         application_name="coop-coach-crawler")


def seed_sql(app_schema: str = "coopcoach") -> list[str]:
    """Start the feed from what this app has already found (first-seen dates, Workday read plans, full postings read
    over many syncs), so the first crawl is an ordinary one. Run as the project owner once the crawler made its
    tables."""
    now = int(time.time())
    job_cols = [c for c in JOB_COLS if c not in ("changed_at", "opened_at")]
    co_cols = ["ats", "slug", "name", "tier", "enabled", "last_status", "last_count", "last_checked", "removed",
               "origin", "plan"]
    out = [
        f"INSERT INTO {SCHEMA}.jobs ({', '.join(job_cols)}, changed_at) SELECT {', '.join(job_cols)}, {now}"
        f" FROM {app_schema}.jobs ON CONFLICT (id) DO NOTHING",
        f"INSERT INTO {SCHEMA}.companies ({', '.join(co_cols)}) SELECT {', '.join(co_cols)} FROM {app_schema}.companies"
        " ON CONFLICT (ats, slug) DO NOTHING",
    ]
    for t, cols, pk in (("board_roles", "board, day, roles, interns", "board, day"),
                        ("firm_openings", "company_key, term, opened_at", "company_key, term")):
        out.append(f"INSERT INTO {SCHEMA}.{t} ({cols}) SELECT {cols} FROM {app_schema}.{t} ON CONFLICT ({pk}) DO NOTHING")
    out.append(f"INSERT INTO {SCHEMA}.meta (key, value) SELECT key, value FROM {app_schema}.meta WHERE key IN"
               " ('baseline_ts', 'flags_version', 'skills_version', 'requirements_version', 'req_ids_v1',"
               " 'seed_version', 'discover_last') ON CONFLICT (key) DO NOTHING")
    out.append(f"INSERT INTO {SCHEMA}.sync_runs (started_at, finished_at, new_jobs, new_events, report, kind)"
               f" SELECT started_at, finished_at, 0, 0, '{{}}', 'full' FROM {app_schema}.sync_runs WHERE finished_at"
               f" IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.sync_runs) ORDER BY finished_at DESC LIMIT 1")
    return out


def publish_sql() -> str:
    """Open the feed to every Co-op Coach (rest mode) through Supabase's REST API: read-only views of the postings
    and boards, no one's personal data (there is none in this schema). Not run automatically: making the feed public
    is its owner's decision."""
    jobs = ", ".join(JOB_COLS)
    return f"""
GRANT USAGE ON SCHEMA {SCHEMA} TO anon, authenticated;
GRANT SELECT ON {SCHEMA}.jobs, {SCHEMA}.companies, {SCHEMA}.board_roles, {SCHEMA}.sync_runs TO anon, authenticated;
ALTER TABLE {SCHEMA}.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE {SCHEMA}.companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE {SCHEMA}.board_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE {SCHEMA}.sync_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY feed_read ON {SCHEMA}.jobs FOR SELECT USING (true);
CREATE POLICY feed_read ON {SCHEMA}.companies FOR SELECT USING (enabled = 1 AND removed = 0);
CREATE POLICY feed_read ON {SCHEMA}.board_roles FOR SELECT USING (true);
CREATE POLICY feed_read ON {SCHEMA}.sync_runs FOR SELECT USING (true);
CREATE OR REPLACE VIEW public.coopcoach_jobs WITH (security_invoker = on) AS SELECT {jobs} FROM {SCHEMA}.jobs;
CREATE OR REPLACE VIEW public.coopcoach_companies WITH (security_invoker = on) AS
  SELECT {', '.join(BOARD_COLS)} FROM {SCHEMA}.companies;
CREATE OR REPLACE VIEW public.coopcoach_board_roles WITH (security_invoker = on) AS
  SELECT board, day, roles, interns FROM {SCHEMA}.board_roles;
CREATE OR REPLACE VIEW public.coopcoach_runs WITH (security_invoker = on) AS
  SELECT id, finished_at, kind, new_jobs FROM {SCHEMA}.sync_runs;
GRANT SELECT ON public.coopcoach_jobs, public.coopcoach_companies, public.coopcoach_board_roles, public.coopcoach_runs
  TO anon, authenticated;
"""
