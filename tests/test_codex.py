"""Your ChatGPT (through the Codex CLI) as the engine: JSON tasks, the chat agent and Autopilot through the real MCP
server, usage-limit pauses, the Codex plugin build, and that no API key leaks in. Uses tests/fake_codex.py, so
nothing touches a real ChatGPT account or the Keychain.

Run: .venv/bin/python -m tests.test_codex
"""

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="coopcoach-codextest-")
BIN = Path(TMP) / "bin" / "codex"
BIN.parent.mkdir()
if os.name == "nt":  # Windows runs a .cmd wrapper; elsewhere a shell script
    BIN = BIN.with_suffix(".cmd")
    BIN.write_text(f'@"{sys.executable}" "{Path(__file__).with_name("fake_codex.py")}" %*\r\n', encoding="utf-8")
else:
    BIN.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).with_name("fake_codex.py")}" "$@"\n', encoding="utf-8")
    BIN.chmod(BIN.stat().st_mode | stat.S_IEXEC)
LOG = Path(TMP) / "fake.log"
os.environ.update(COOPCOACH_DATA=TMP, COOPCOACH_CODEX_BIN=str(BIN), COOPCOACH_CLAUDE_BIN="/nonexistent",
                  FAKE_CODEX_LOG=str(LOG), GROQ_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="sk-should-not-leak",
                  PYTHONPATH=str(Path(__file__).resolve().parent.parent))

from coopcoach import agent, ai, autopilot, claude_code, claude_setup, codex_cli, db, store  # noqa: E402


def calls() -> list[dict]:
    return [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines()] if LOG.exists() else []


def seed():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES (?,?,?,?,?)",
                        ("Tester", now, now, json.dumps({"locations": ["Toronto", "Canada"], "citizenship": "Canadian",
                                                         "autopilot": {"notify": False}}),
                         json.dumps({"name": "Alex Chen", "email": "alex@example.com", "skills": ["Python", "Go"],
                                     "target_categories": ["Software"], "summary": "Year 3 CS"}))).lastrowid
    jobs = [store.normalize({"source": "lever:shopify", "company": "Shopify", "title": "Software Engineer Intern",
                             "url": "https://jobs.lever.co/shopify/abc-123", "locations": ["Toronto, ON"],
                             "description": "Python and Go. Canada based applicants only."}),
            store.normalize({"source": "lever:wealthsimple", "company": "Wealthsimple", "title": "Backend Developer Co-op",
                             "url": "https://jobs.lever.co/wealthsimple/ws-1", "locations": ["Toronto, ON"],
                             "description": "Python, Go, Postgres."})]
    store.upsert_jobs(jobs, now)
    return pid


def wait_for(cond, seconds: float = 8) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return bool(cond())


def sign_in_checks(pid: int, checks: dict) -> None:
    """Same as Claude Code: signing in shows up within seconds, /api/state never waits on `codex`."""
    from types import SimpleNamespace

    from coopcoach import routes_auto, server
    os.environ["FAKE_CODEX_SIGNED_OUT"] = "1"
    codex_cli.status(refresh=True)
    os.environ.pop("FAKE_CODEX_SIGNED_OUT")
    cached = codex_cli.status()["logged_in"]
    codex_cli._status["t"] -= claude_code.FRESH_SIGNED_OUT + 0.5
    checks["'not signed in' is only reused for a few seconds"] = not cached and codex_cli.status()["logged_in"]

    os.environ["FAKE_CODEX_SIGNED_OUT"] = "1"
    codex_cli.status(refresh=True)
    claude_code.SIGNIN_POLL = 0.2
    opened = []
    real_osascript, claude_code._osascript = claude_code._osascript, opened.append  # never opens a real Terminal
    try:
        if sys.platform == "darwin":
            codex_cli.sign_in_in_terminal()
        else:
            claude_code.watch_sign_in("codex", codex_cli.status)
        time.sleep(0.5)
        still_out = not codex_cli.status_cached()["logged_in"]
        os.environ.pop("FAKE_CODEX_SIGNED_OUT")
        t = time.time()
        flipped = wait_for(lambda: codex_cli.status_cached()["logged_in"])
        checks["after Sign in, the app sees it finish within ~2 s, without a reload"] = \
            still_out and flipped and time.time() - t < 3 and (opened or sys.platform != "darwin")
        checks["...and then stops checking"] = wait_for(lambda: not claude_code._watch_until.get("codex"), 3)
    finally:
        claude_code._osascript = real_osascript

    os.environ["FAKE_CODEX_SLOW"] = "3"
    codex_cli._status["t"] = 0
    t = time.time()
    st = server.get_state(SimpleNamespace(cookies={}))
    checks["/api/state never waits on the `codex` command"] = time.time() - t < 1.5 and st["codex"]["logged_in"] \
        and st["ai_provider"] == "codex"
    wait_for(lambda: not codex_cli._checking.locked(), 6)
    os.environ.pop("FAKE_CODEX_SLOW")
    before = len([c for c in calls() if "cmd" in c])
    r = routes_auto.codex_status(pid, refresh=True)
    checks["Settings' status check skips the slow plugin listing"] = r["logged_in"] and "plugin_installed" not in r \
        and len([c for c in calls() if "cmd" in c]) == before


def main():
    pid = seed()
    checks = {}
    s = codex_cli.status(refresh=True)
    checks["finds ChatGPT's Codex and that it's signed in"] = s["installed"] and s["logged_in"] and s["model"] == "gpt-6-astra"
    db.meta_set("ai_engine", "codex")
    checks["choosing it makes ChatGPT the engine"] = ai.provider() == "codex" and ai.label() == "your ChatGPT"
    sign_in_checks(pid, checks)

    out = ai._call_json("Be terse.", [{"type": "text", "text": "Alex Chen (alex@example.com) wants a letter"}],
                        ai._obj({"letter": ai._STR}), effort="medium")
    last = calls()[-1]
    checks["JSON tasks come back in the schema"] = out == {"letter": "Fake answer from Claude Code"}
    checks["...with its shell switched off and your config ignored"] = all(x in last["args"] for x in (
        "--ignore-user-config", "shell_tool", "unified_exec", "--output-schema")) and \
        last["args"][last["args"].index("--sandbox") + 1] == "read-only"
    checks["...contact details redacted"] = "Alex Chen" not in last["stdin"] and "alex@example.com" not in last["stdin"]
    checks["...on your ChatGPT plan: no API key reaches it"] = not any(c.get("leaked_key") for c in calls())

    LOG.unlink()
    res = agent.run(pid, [{"role": "user", "content": "best roles in Toronto? star the top one"}])
    checks["chat on ChatGPT uses the real tools through MCP"] = res["by"] == "codex" and \
        [x["tool"] for x in res["steps"]] == ["search_jobs", "star"]
    checks["...with cards and Undo"] = res["cards"] and res["undo"] and res["undo"][0]["tool"] == "star"
    checks["...and only Co-op Coach's chat tools"] = "application_files" not in next(c["mcp_tools"] for c in calls() if "mcp_tools" in c)

    with db.tx() as c:
        c.execute("DELETE FROM favourites")
    LOG.unlink()
    autopilot.save_settings(pid, {"engine": "codex", "per_day": 1, "min_score": 0})
    r = autopilot.run(pid, "manual")
    q = db.rows("SELECT job_id, source FROM ready_queue WHERE profile_id=?", (pid,))
    checks["Autopilot on ChatGPT queues through the MCP server"] = r.get("engine") == "codex" and len(q) == 1 and \
        q[0]["source"] == "autopilot"
    checks["...with read tools + queue only"] = set(next(c["mcp_tools"] for c in calls() if "mcp_tools" in c)) == \
        set(agent.AUTOPILOT_TOOLS)

    os.environ["FAKE_CODEX_MODE"] = "limit"
    os.environ["GROQ_API_KEY"] = "test-key"
    try:
        codex_cli.call_json("x", "y", {"type": "object", "properties": {}})
    except Exception:
        pass
    checks["a usage limit pauses ChatGPT until the time it says"] = codex_cli.paused() > 0 and ai.provider() == "groq"
    os.environ["FAKE_CODEX_MODE"], os.environ["GROQ_API_KEY"] = "", ""
    codex_cli._paused_until = 0

    kept: dict[str, str] = {}
    claude_setup.vault.available, claude_setup.vault.put = (lambda: True), kept.__setitem__
    claude_setup.vault.get, claude_setup.vault.delete = kept.get, (lambda k: kept.pop(k, None))
    LOG.unlink(missing_ok=True)
    res = claude_setup.connect_codex(pid)
    plug = Path(TMP) / "codex-plugin" / "plugins" / "coop-coach"
    manifest = json.loads((plug / ".codex-plugin" / "plugin.json").read_text())
    checks["Connect builds a Codex plugin with the same skills"] = res["ok"] and manifest["skills"] == "./skills/" and \
        (plug / "skills" / "apply" / "SKILL.md").exists() and json.loads((plug / ".mcp.json").read_text())[
        "mcpServers"]["coop-coach"]["command"] == sys.executable
    checks["...listed in a local marketplace and installed"] = (Path(TMP) / "codex-plugin" / ".agents" / "plugins" /
                                                              "marketplace.json").exists() and \
        [c["cmd"][:2] for c in calls() if "cmd" in c][:2] == [["plugin", "marketplace"], ["plugin", "add"]]
    checks["...with a connection token in the Keychain"] = bool(kept.get(f"claude-connect:{pid}"))

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
