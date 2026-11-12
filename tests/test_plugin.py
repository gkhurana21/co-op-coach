"""The Claude Code plugin: its server lists the tools at once without touching the database (a slow connection can't
time it out), and asks the open app when there is one; a lasting connection may use the app's relay, other tickets may
not; every tool a skill or agent names exists; an update to the plugin's files reaches an installed plugin by itself;
the Submit guard covers each browser it can read; and the new tools: tracks, calendar and status.

Run: .venv/bin/python -m tests.test_plugin
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="coopcoach-plugin-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent",
                  COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, auth, claude_setup, config, db, mcp_server, meetings, niches, server, store, vault  # noqa: E402
from coopcoach.tools import explore  # noqa: E402

results: dict[str, bool] = {}
ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "claude-plugin"
# backticked names in the skills that aren't tools: fields in what the tools return, and the like
FIELDS = {"leetcode_linked", "test_running", "tests_ready", "new_matches", "better_version", "who_can_apply", "needs_you", "apply_url", "quiet_3_weeks", "inbox_updates",
          "prep_for", "events_this_week", "ready_to_apply", "what_you_go_for", "job_id", "posted_within_days",
          "company_type", "to_book", "app_open", "last_sync_minutes_ago", "needs_sign_in", "last_status", "ai_engine",
          "first_solve"}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


async def _list_tools(env: dict) -> tuple[list[str], float]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    t = time.time()
    params = StdioServerParameters(command=sys.executable, args=["-m", "coopcoach", "mcp"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [x.name for x in (await s.list_tools()).tools]
    return names, time.time() - t


def starts_at_once():
    fresh = tempfile.mkdtemp(prefix="coopcoach-plugin-fresh-")
    env = {**os.environ, "COOPCOACH_DATA": fresh, "PYTHONPATH": str(ROOT)}
    for k in ("COOPCOACH_TICKET", "COOPCOACH_TICKET_REF", "COOPCOACH_TOOLS", "COOPCOACH_PG_SCHEMA", "COOPCOACH_PG_SETTINGS",
              "COOPCOACH_DATABASE_URL", "COOPCOACH_PG_RESET"):
        env.pop(k, None)
    names, took = asyncio.run(_list_tools(env))
    check(f"the plugin's server lists every tool quickly ({took:.1f} s; Claude Code gives up at 30)", set(names) == set(
        agent.TOOLS) and took < 20)
    check("...without opening the database at all", not list(Path(fresh).glob("*.db")))


class App(BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        App.seen.append((body, self.headers.get("Authorization")))
        self.send_response(App.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"answered_by": "the app"}).encode())


def asks_the_app(pid: int):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real = (vault.available, vault.get)
    token = auth.issue_ticket(pid, ttl=None, label="Claude Code plugin")
    vault.available, vault.get = (lambda: True), (lambda key: token if key.startswith(mcp_server.CONNECT_KEY) else None)
    note = config.DATA_DIR / "app.json"
    try:
        os.environ["COOPCOACH_PORT"] = "8123"  # as `serve` and `demo` both set it
        with TestClient(server.app, base_url="http://localhost"):
            check("the running app says where it is, however it was started", mcp_server.app_address()
                  == "http://127.0.0.1:8123")
        del os.environ["COOPCOACH_PORT"]
        note.unlink(missing_ok=True)
        p = mcp_server.Plugin("claude_code")
        check("no app open: no address", mcp_server.app_address() is None)
        out = p.call("list_tracks", {})
        check("...and the tool still works, here in this process", "roles" in out and p.pid == pid)
        note.write_text(json.dumps({"port": srv.server_port, "pid": os.getpid()}))
        out = p.call("search_jobs", {"query": "x"})
        check("with the app open, it asks the app, with the connection's token", out == {"answered_by": "the app"}
              and App.seen[-1][1] == f"Bearer {token}" and App.seen[-1][0]["tool"] == "search_jobs"
              and App.seen[-1][0]["source"] == "claude_code")
        App.status = 401
        check("...and an authorization denial cannot fall back to local access", p.call("list_tracks", {}).get("status") == 401)
        App.status = 200
        note.write_text(json.dumps({"port": srv.server_port, "pid": 999999}))
        check("an app that's gone isn't asked", mcp_server.app_address() is None)
        srv.shutdown()
        srv.server_close()
        note.write_text(json.dumps({"port": srv.server_port, "pid": os.getpid()}))
        check("...nor one that stopped answering", "roles" in p.call("list_tracks", {}))

        cl = TestClient(server.app, base_url="http://localhost")
        call = lambda t, tool="list_tracks": cl.post("/api/agent/call", json={"tool": tool, "args": {}}, headers={  # noqa: E731
            "Authorization": f"Bearer {t}"}).status_code
        run = auth.issue_ticket(pid, ttl=600, label="run")
        odd = auth.issue_ticket(pid, ttl=None, label="something else")
        check("the app answers a lasting connection, for any tool", call(token) == 200)
        check("...not a run's ticket beyond its own tools, nor another lasting one", call(run) == 403 and call(odd) == 403)
        check("...nor a tool that doesn't exist", call(token, "not_a_tool") == 403)
        auth.revoke_ticket(token)
        check("...and a revoked connection stops working", call(token) == 401)
    finally:
        vault.available, vault.get = real
        note.unlink(missing_ok=True)


def skills():
    files = list(PLUGIN.glob("skills/*/SKILL.md")) + list(PLUGIN.glob("agents/*.md"))
    names = {f.parent.name for f in PLUGIN.glob("skills/*/SKILL.md")}
    check("the skills are there, and the old ones gone", {"today", "neetcode", "tracks", "hackathon", "doctor", "apply",
                                                          "autopilot", "hunt"} <= names and "match" not in names)
    unknown = {}
    for f in files:
        text = f.read_text(encoding="utf-8")
        said = set(re.findall(r"`([a-z]+(?:_[a-z0-9]+)+)`", text)) | set(re.findall(r"coop-coach__([a-z_]+)", text))
        bad = {x for x in said if x not in agent.TOOLS and x not in FIELDS}
        if bad:
            unknown[f.parent.name + "/" + f.name] = sorted(bad)
    if unknown:
        print(unknown)
    check("every tool a skill or agent names exists", not unknown)
    text = " ".join(f.read_text(encoding="utf-8") for f in files)
    check("nothing still mentions the practice features or the old tabs", not re.search(
        r"weak_spots|practice_|mock interview me|Autopilot tab|Explore →|LeetCode ladder", text))
    for f in files:
        head = f.read_text(encoding="utf-8").split("---")[1]
        if "name:" not in head or "description:" not in head:
            check(f"{f} has a name and description", False)
    check("the plugin says what's in it", all(f"/coop-coach:{n}" in (PLUGIN / "README.md").read_text(encoding="utf-8")
                                               for n in names))


def stays_fresh():
    check("a plugin nobody connected is left alone", claude_setup.refresh_if_stale() == {
        "refreshed": False, "why": "not connected"})
    calls, real = [], claude_setup._claude
    claude_setup._claude = lambda *a, **k: (calls.append(a), (True, ""))[1]
    try:
        claude_setup.build()
        dest = claude_setup.plugin_dir() / claude_setup.PLUGIN
        manifest = json.loads((dest / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        check("the built plugin's version changes with its files", manifest["version"].endswith(claude_setup.template_hash()))
        check("...and has a guard covering each browser it can read", all(x in json.loads(
            (dest / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["matcher"] for x in (
                "mcp__claude-in-chrome__computer", "mcp__Claude_Browser__browser_batch")))
        check("up to date: nothing to do", claude_setup.refresh_if_stale()["why"] == "up to date" and not calls)
        (claude_setup.plugin_dir() / ".template-hash").write_text("older")
        out = claude_setup.refresh_if_stale()
        check("the app updated: the plugin is rebuilt and Claude Code told", out["refreshed"] and out["installed"]
              and ("plugin", "update", "coop-coach@coop-coach-local") in calls
              and claude_setup.refresh_if_stale()["why"] == "up to date")
    finally:
        claude_setup._claude = real
    hook = PLUGIN / "hooks" / "no_submit.py"
    run = lambda ti: subprocess.run([sys.executable, str(hook)], input=json.dumps({"tool_input": ti}),  # noqa: E731
                                    capture_output=True, text=True).returncode
    check("the guard reads a batch of browser steps too", run({"actions": [
        {"input": {"action_summary": "Types the name"}}, {"input": {"action_summary": "Clicks the Submit button"}}]}) == 2)


def tools(pid: int):
    now = db.now()
    store.upsert_jobs([store.normalize({"source": "lever:x", "company": "Acme Analytics", "title": "Forward Deployed Engineer Intern",
                                        "url": "https://x.test/1", "locations": ["Toronto, ON"], "description": "Python. " * 60}),
                       store.normalize({"source": "lever:y", "company": "Royal Bank of Canada", "title": "Business Analyst Co-op",
                                        "url": "https://x.test/2", "locations": ["Toronto, ON"], "description": "Excel. " * 60})], now)
    store.invalidate_cache()
    t = agent.call(pid, "list_tracks", {}, source="test")
    check("list_tracks: each group, with what's open to them", {"pins", "roles", "employers", "rising"} <= set(t) and any(
        c["id"] == "co:banks" and c["open_to_you"] == 1 for c in t["employers"]))
    s = agent.call(pid, "search_jobs", {"track": "banks"}, source="test")
    check("search_jobs takes a track", [j["company"] for j in s["jobs"]] == ["Royal Bank of Canada"])
    check("...and says when there's no such track", "no track" in agent.call(pid, "search_jobs", {"track": "zzz"},
                                                                             source="test")["error"])
    r = agent.call(pid, "pin_track", {"track": "Banks & financial firms"}, source="test")
    from coopcoach import tracks
    check("pin_track, by name, with Undo", r.get("ok") and "co:banks" in tracks.pins(pid)
          and db.row("SELECT undo FROM agent_actions WHERE tool='pin_track' ORDER BY id DESC LIMIT 1")["undo"])
    f = agent.call(pid, "follow_track", {"track": "co:quant-firms"}, source="test")
    check("follow_track: a niche track follows its firms", f.get("following") and "quant" in niches.followed(pid))
    agent.call(pid, "follow_track", {"track": "co:quant-firms", "on": False}, source="test")
    meetings.save(pid, [{"kind": "interview", "title": "Interview with Acme", "start_ts": now + 86400, "end_ts": now + 90000,
                         "status": "confirmed", "join_url": "https://meet.test/a"},
                        {"kind": "coffee_chat", "title": "Coffee with Sam", "status": "to_book", "book_url": "https://cal.test/s"}],
                  company="Acme", source="you")
    c = agent.call(pid, "my_calendar", {"days": 7}, source="test")
    check("my_calendar: what's coming, with its links, and what's still to book", any(
        i["title"] == "Interview with Acme" and i["join"] == "https://meet.test/a" and i["when"] for i in c["items"]) and any(
            i["to_book"] and i["book"] == "https://cal.test/s" for i in c["items"]) and "reminders" in c)
    with db.tx() as tx:
        tx.execute("INSERT INTO email_accounts (profile_id, kind, address, host, secret, created_at, last_status) VALUES"
                   " (?, 'outlook_web', 'r@uwaterloo.ca', 'outlook.office.com', '', ?, ?)",
                   (pid, now, "error: Outlook wants you to sign in again: press Sign in again"))
    st = agent.call(pid, "coop_status", {}, source="test")
    check("coop_status: what's connected, and an inbox that needs signing in", st["inboxes"][0]["needs_sign_in"]
          and st["postings_open"] == 2 and st["app_open"] is False and "secret" not in json.dumps(st))
    check("the read tools are read-only, the changes aren't", not {"list_tracks", "my_calendar", "coop_status"} & agent.WRITES
          and {"pin_track", "follow_track"} <= agent.WRITES)


def who_it_opens(pid: int):
    real = mcp_server.vault.available
    mcp_server.vault.available = lambda: False
    try:
        check("one person with no passcode just opens: nothing to set up", mcp_server.resolve(None) == pid)
        auth.set_passcode(pid, "1234")
        try:
            mcp_server.resolve(None)
            check("...a passcode still has to be connected from the app", False)
        except SystemExit as e:
            check("...a passcode still has to be connected from the app", "Connect" in str(e))
        auth.clear_passcode(pid)
        now = db.now()
        with db.tx() as c:
            other = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Second', ?, ?)", (now, now)).lastrowid
        try:
            mcp_server.resolve(None)
            check("...and with two people it doesn't guess", False)
        except SystemExit as e:
            check("...and with two people it doesn't guess", "Connect" in str(e))
        with db.tx() as c:
            c.execute("DELETE FROM profiles WHERE id=?", (other,))
    finally:
        mcp_server.vault.available = real


def small_answers(pid: int):
    now = db.now()
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO jobs (id, source, company, company_key, title, url, locations, regions, category, active, first_seen,"
                      " last_seen, changed_at, posted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [(f"page{i}", "test", f"Pager {i}", f"pager{i}", "Software Engineering Intern", f"https://x.test/p{i}", '["Toronto, ON"]', '["CA"]',
                        "Software", 1, now, now, now, now - i) for i in range(40)])
    store.invalidate_cache()
    a = agent.call(pid, "search_jobs", {"query": "Pager", "limit": 25}, source="test")
    b = agent.call(pid, "search_jobs", {"query": "Pager", "limit": 25, "offset": a["next_offset"]}, source="test")
    ids = lambda r: {j["job_id"] for j in r["jobs"]}  # noqa: E731
    check("search_jobs pages: 25 at a time, then the rest, nothing twice", len(a["jobs"]) == 25 and a["total"] >= 40 and a["next_offset"] == 25
          and not ids(a) & ids(b) and "next_offset" not in b)
    check("...a huge limit is still 25, and a silly offset is fine", len(agent.call(pid, "search_jobs", {"query": "Pager", "limit": 100000}, source="test")["jobs"]) == 25
          and agent.call(pid, "search_jobs", {"query": "Pager", "offset": -5}, source="test")["jobs"]
          and agent.call(pid, "search_jobs", {"query": "Pager", "offset": 99999}, source="test")["jobs"] == [])
    d = agent.call(pid, "explore_niche", {"niche": "quant"}, source="test")
    check("a niche answer stays small: the top of each segment, and how many there are", not d.get("error") and all(
        len(x["firms"]) <= explore.TOP_FIRMS and x["firms_total"] >= len(x["firms"]) for x in d["segments"]) and len(json.dumps(d, default=str)) < 12000)
    big = max(d["segments"], key=lambda x: x["firms_total"])
    all_of = agent.call(pid, "explore_niche", {"niche": "quant", "segment": big["segment"]}, source="test")
    check("...asking for a segment gives all of it", len(all_of["segments"][0]["firms"]) == big["firms_total"])


def other_clients():
    names = lambda src: [x.name for x in asyncio.run(mcp_server.build_plugin(src).list_prompts())]  # noqa: E731
    skills_on_disk = {f.parent.name for f in (PLUGIN / "skills").glob("*/SKILL.md")}
    check("Claude Code gets its commands from the plugin, not twice", names("claude_code") == [])
    check("other clients get every command as a prompt", set(names("codex")) == skills_on_disk)
    srv = mcp_server.build_plugin("codex")
    msg = asyncio.run(srv.get_prompt("neetcode", {"text": "solved Two Sum in 12 min"}))
    text = json.dumps(msg.model_dump(mode="json"))
    check("...with what they asked filled in", "solved Two Sum in 12 min" in text and "$ARGUMENTS" not in text and "record_problem" in text)
    msg = asyncio.run(srv.get_prompt("today", {}))
    check("...and it works with nothing given", "daily_brief" in json.dumps(msg.model_dump(mode="json")))


def main():
    db.init()
    server._sync_in_background = lambda *a, **k: None
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Robin', ?, ?)", (now, now)).lastrowid
    who_it_opens(pid)
    other_clients()
    starts_at_once()
    asks_the_app(pid)
    skills()
    stays_fresh()
    tools(pid)
    small_answers(pid)
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
