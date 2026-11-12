"""Jarvis on your Claude Code, quickly: while the app is open, a run's tools are answered by the app itself (where the
postings are already loaded) through a light relay, instead of a fresh copy of the app per message; the chat uses
the quicker model; a run's ticket opens only its own profile and only the tools it was given, and stops working when
the run ends. Uses a stand-in `claude` (tests/fake_claude.py) that really starts the tool server and calls tools.

Run: .venv/bin/python -m tests.test_fast_chat
"""

import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="coopcoach-fastchat-")
BIN = Path(TMP) / "bin" / "claude"
BIN.parent.mkdir()
if os.name == "nt":
    BIN = BIN.with_suffix(".cmd")
    BIN.write_text(f'@"{sys.executable}" "{Path(__file__).with_name("fake_claude.py")}" %*\r\n', encoding="utf-8")
else:
    BIN.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).with_name("fake_claude.py")}" "$@"\n',
                   encoding="utf-8")
    BIN.chmod(BIN.stat().st_mode | stat.S_IEXEC)
LOG = Path(TMP) / "fake.log"
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
os.environ.update(COOPCOACH_DATA=TMP, COOPCOACH_CLAUDE_BIN=str(BIN), FAKE_CLAUDE_LOG=str(LOG), GROQ_API_KEY="",
                  ANTHROPIC_API_KEY="", COOPCOACH_PORT=str(PORT), PYTHONPATH=str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from coopcoach import agent, auth, claude_code, db, server, store  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def calls() -> list[dict]:
    return [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []


def main():
    db.init()
    db.meta_set("ai_engine", "claude_code")
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES (?,?,?,?,?)",
                        ("Tester", now, now, json.dumps({"locations": ["Toronto"]}),
                         json.dumps({"skills": ["Python"], "target_categories": ["Software"]}))).lastrowid
    store.upsert_jobs([store.normalize({"source": "lever:shopify", "company": "Shopify", "title": "Software Engineer Intern",
                                        "url": "https://jobs.lever.co/shopify/abc", "locations": ["Toronto, ON"],
                                        "description": "Python."})], now)
    server._sync_in_background = lambda: None
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(6000):  # starting seeds the company boards: seconds on SQLite, a minute or more on Supabase
        if srv.started:
            break
        time.sleep(0.1)

    loaded = []
    real = store.collapsed_jobs
    store.collapsed_jobs = lambda: loaded.append(1) or real()  # counts loads in this (the app's) process
    t = time.time()
    out = agent.run(pid, [{"role": "user", "content": "find me a software job in Toronto and star it"}])
    took = time.time() - t
    log = calls()
    cfg_args = next(c["args"] for c in log if "args" in c and "--mcp-config" in c["args"])
    tools = next((c["mcp_tools"] for c in log if "mcp_tools" in c), [])
    check("with the app open, Jarvis's tools are answered by the app itself (the postings it already has loaded)",
          out["reply"].startswith("Starred") and [s["tool"] for s in out["steps"]] == ["search_jobs", "star"]
          and loaded and db.row("SELECT COUNT(*) AS n FROM favourites WHERE profile_id=?", (pid,))["n"] == 1)
    check("...through the light relay, with the chat's tools and the same Activity log and Undo",
          "star" in tools and "forget_email" not in tools and out["undo"] and out["cards"] is not None)
    check("the chat asks for the model the list gives it", cfg_args[cfg_args.index("--model") + 1] == claude_code.model_for("chat") == "haiku")
    check("...and the whole message takes seconds (this stand-in: under 15 s)", took < 15)
    print(f"     (one chat message with two tools: {took:.1f}s)")
    from coopcoach import claude_chat
    for _ in range(100):  # the next message's process is on its way; it has a list of its own until it's closed
        if (claude_chat._spares.get(pid) or {}).get("proc"):
            break
        time.sleep(0.1)
    used = [p.name for p in claude_code._workdir().glob("tools-*.json")]
    claude_chat.close_all()
    leftovers = [p.name for p in claude_code._workdir().glob("tools-*.json")]
    check("the run's tool list is removed when it ends: only the next message's is left, until it's closed", len(used) == 1 and leftovers == [])

    api = f"http://127.0.0.1:{PORT}/api/agent/call"
    ticket = auth.issue_ticket(pid, ttl=60, label="test")
    auth.allow_tools(ticket, ["search_jobs"])
    ok = httpx.post(api, json={"tool": "search_jobs", "args": {"role": "software"}},
                    headers={"Authorization": f"Bearer {ticket}"})
    no = httpx.post(api, json={"tool": "star", "args": {"company": "Shopify"}}, headers={"Authorization": f"Bearer {ticket}"})
    check("a run's ticket uses only the tools that run was given", ok.status_code == 200 and "jobs" in ok.json()
          and no.status_code == 403)
    other = auth.issue_ticket(pid, ttl=60, label="connection")  # e.g. "Connect my Claude Code": not a run
    check("...other tickets can't use the relay at all, nor no ticket", httpx.post(
        api, json={"tool": "search_jobs", "args": {}}, headers={"Authorization": f"Bearer {other}"}).status_code == 403
          and httpx.post(api, json={"tool": "search_jobs", "args": {}}).status_code == 401)
    auth.revoke_ticket(ticket)
    check("...and once the run ends its ticket stops working", httpx.post(
        api, json={"tool": "search_jobs", "args": {}}, headers={"Authorization": f"Bearer {ticket}"}).status_code == 401)
    evil = httpx.post(api, json={"tool": "search_jobs", "args": {}}, headers={"Origin": "https://evil.example"})
    check("a web page can't call it either", evil.status_code in (401, 403))
    srv.should_exit = True
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
