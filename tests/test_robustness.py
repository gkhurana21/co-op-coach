"""Nothing an outside caller sends crashes the app: every API route and every tool, fed missing, empty and junk input,
answers with a proper status or error message, never a server error. (A 500 means an unhandled exception.)

Run: .venv/bin/python -m tests.test_robustness
"""

import json
import os
import re
import tempfile
from urllib.parse import quote

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-robust-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, db, server, store  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}
# never run for real here: they open windows, start long work on the network, or wipe the test's own person
SKIP = re.compile(r"/api/sync|/api/discover|/outlook-web/start|/api/events/refresh|/fill|/apply|/open-|/profiles$|"
                  r"/profiles/\{pid\}$|/api/auth/|/claude/|/codex/|/workday|/portals|/supabase|/auth/|/passcode|/autopilot/run|"
                  r"/api/central")
SKIP_TOOLS = {"fill_application", "apply_session_next", "refresh_jobs", "open_link", "run_autopilot", "check_inbox",
              "research_company"}
JUNK = ["", "x", "'; DROP TABLE jobs; --", "<script>alert(1)</script>", "../../etc/passwd", "💥" * 40, "9" * 400]
JUNK_BODIES = [{}, {"profile_id": "abc"}, {"profile_id": None}, {"profile_id": 999999}, {"profile_id": [1]},
               {"profile_id": {"a": 1}}, {"profile_id": 1, "id": "x", "job_id": "x", "name": "", "url": "x", "q": "x"},
               {"profile_id": 1, "job_id": None, "status": 5, "slug": 5, "track": [], "text": {}, "on": "x", "resume": "x"},
               {"profile_id": 1}, {"profile_id": 1, "job_id": "no-such-job", "status": "applied", "name": "x", "key": "x",
                                   "kind": "x", "text": "x", "title": "x", "address": "x@y.z", "password": "x",
                                   "resume": {}, "prefs": {}, "goal": 50, "tag": "x", "slug": "two-sum", "track": "role:nope",
                                   "url": "https://example.test/x", "pins": ["x"], "tiers": "x", "days": "x", "n": "x"},
               {"profile_id": 1, "resume": [], "job_id": 5, "name": 5, "text": 5, "title": 5, "address": 5, "url": 5, "key": 5,
                "kind": 5, "status": [], "prefs": [], "goal": [], "keys": 5, "months": "x", "n": [], "pins": 5, "days": []}]


INTERNAL = re.compile(r"object has no attribute|not supported between|Error binding|is not iterable|NoneType|"
                      r"not subscriptable|unexpected keyword|must be str|expected str|invalid literal|has no len|"
                      r"unhashable|takes \d+ positional|list index|KeyError|syntax error", re.I)


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def blocked_network(*a, **k):
    raise httpx.ConnectError("no network in this test")


def quoted(v: str) -> str:
    return quote(v or "x", safe="")


def routes(cl, pid):
    bad: dict[str, str] = {}
    n = covered = 0
    spec = app.openapi()["paths"]
    for path, ops in spec.items():
        if SKIP.search(path):
            continue
        for method, op in ops.items():
            covered += 1
            params = op.get("parameters") or []
            in_path = [p["name"] for p in params if p["in"] == "path"]
            query = [p for p in params if p["in"] == "query"]
            for value in (JUNK[:5] if in_path else JUNK[:2]):
                url = path
                for name in in_path:
                    kind = next(p["schema"].get("type") for p in params if p["name"] == name)
                    url = url.replace("{%s}" % name, "999999" if kind == "integer" else quoted(value))
                def fill(v):
                    out = {}
                    for p in query:
                        kind = p["schema"].get("type", "string")
                        out[p["name"]] = pid if p["name"] == "profile_id" else (3 if kind == "integer" else "true" if kind ==
                                                                                 "boolean" else v)
                    return out
                if method in ("get", "delete"):
                    tries = [{"params": fill(value)}, {"params": {"profile_id": pid}}, {"params": {}}]
                else:
                    tries = [{"json": b} for b in JUNK_BODIES] + ([{"params": fill(value), "json": {"profile_id": pid}}] if query else [])
                for kw in tries:
                    n += 1
                    try:
                        res = cl.request(method.upper(), url, **kw)
                    except Exception as e:
                        bad[f"{method} {path} {json.dumps(kw)[:90]}"] = repr(e)[:160]
                        continue
                    if res.status_code >= 500:
                        bad[f"{method.upper()} {path} {json.dumps(kw)[:100]}"] = res.text[:80]
    print(f"     ({covered} routes, {n} requests)")
    seen: dict[str, str] = {}
    for k, v in sorted(bad.items()):
        seen.setdefault(" ".join(k.split()[:2]), k)
    print(f"     {len(seen)} routes crash:")
    for k in seen.values():
        print("     500:", k)
    check(f"every API route answers junk with a proper status, never a crash ({len(bad)} crashes on {len(seen)} routes)",
          not bad)
    check("the fuzz really reaches the routes", covered > 100)


def brand_new_student(cl):
    """Everything a page asks for, for someone who just signed up: no resume, nothing applied to, no postings read."""
    now = db.now()
    with db.tx() as c:
        new = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('New', ?, ?)", (now, now)).lastrowid
    bad, n = {}, 0
    for path, ops in app.openapi()["paths"].items():
        op = ops.get("get")
        if not op or SKIP.search(path):
            continue
        params = op.get("parameters") or []
        required = [p["name"] for p in params if p.get("required")]
        if "{" in path or not set(required) <= {"profile_id"}:
            continue
        n += 1
        r = cl.get(path, params={"profile_id": new} if any(p["name"] == "profile_id" for p in params) else {})
        if r.status_code >= 400 and not (path == "/api/resume/export" and "no resume" in r.text):  # says so, correctly
            bad[path] = f"{r.status_code} {r.text[:90]}"
    for k, v in bad.items():
        print("     new student:", k, v)
    check(f"a brand-new student's pages all load ({n} of them)", not bad and n > 25)
    r = cl.get("/api/export/xlsx", params={"profile_id": new})
    check("...the Excel download too, with every sheet empty", r.status_code == 200 and len(r.content) > 1000)


def tools(pid):
    bad = {}
    n = 0
    for name, (_, _desc, schema) in agent.TOOLS.items():
        if name in SKIP_TOOLS:
            continue
        props = schema.get("properties", {})
        cases = [{}, *[{k: v for k in props} for v in JUNK[:5]], {k: None for k in props}, {k: 999999 for k in props},
                 {k: [] for k in props}, {k: {} for k in props}]
        for args in cases:
            n += 1
            try:
                out = agent.call(pid, name, args, source="test")
                json.dumps(out, default=str)
            except Exception as e:
                bad[f"{name}({json.dumps(args)[:70]})"] = repr(e)[:150]
                continue
            err = str(out.get("error") or "") if isinstance(out, dict) else ""
            if INTERNAL.search(err):  # the tool fell over: this is a Python error, not a message for a person
                bad[f"{name}({json.dumps(args)[:70]})"] = err[:150]
    print(f"     ({n} calls)")
    for k, v in sorted(bad.items())[:60]:
        print("     crash:", k, "->", v)
    check(f"every tool answers junk with an error it can read, never an exception ({len(bad)} crashes)", not bad)


def main():
    db.init()
    server._sync_in_background = lambda *a, **k: None
    httpx.HTTPTransport.handle_request = blocked_network  # nothing here reaches the internet (the test client has its own)
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, created_at, updated_at) VALUES ('Robin', ?, ?, ?)",
                        (db.dumps({"skills": ["Python"]}), now, now)).lastrowid
    store.upsert_jobs([store.normalize({"source": "lever:x", "company": "Acme", "title": "Software Intern", "url": "https://x.test/1",
                                        "locations": ["Toronto, ON"], "description": "Python. " * 60})], now)
    store.invalidate_cache()
    cl = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    brand_new_student(cl)
    routes(cl, pid)
    tools(pid)
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
