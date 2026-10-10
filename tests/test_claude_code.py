"""Your Claude Code as the engine (JSON calls, chat agent through the real MCP server), Autopilot (rules and Claude
Code), the Ready to apply queue, the Activity log with Undo, run tickets, the plugin build, and the Submit guard.
Uses a stand-in `claude` (tests/fake_claude.py), so nothing touches a real Claude account or the Keychain.

Run: .venv/bin/python -m tests.test_claude_code
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="coopcoach-cctest-")
BIN = Path(TMP) / "bin" / "claude"
BIN.parent.mkdir()
BIN.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).with_name("fake_claude.py")}" "$@"\n')
BIN.chmod(BIN.stat().st_mode | stat.S_IEXEC)
LOG = Path(TMP) / "fake.log"
os.environ.update(COOPCOACH_DATA=TMP, COOPCOACH_CLAUDE_BIN=str(BIN), FAKE_CLAUDE_LOG=str(LOG), GROQ_API_KEY="",
                  ANTHROPIC_API_KEY="sk-ant-should-not-leak", PYTHONPATH=str(Path(__file__).resolve().parent.parent))

from coopcoach import agent, ai, auth, autopilot, claude_code, claude_setup, db, store  # noqa: E402


def calls() -> list[dict]:
    return [json.loads(x) for x in LOG.read_text().splitlines()] if LOG.exists() else []


def seed():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES (?,?,?,?,?)",
                        ("Tester", now, now, json.dumps({"locations": ["Toronto", "Canada"], "citizenship": "Canadian",
                                                         "autopilot": {"notify": False}}),
                         json.dumps({"name": "Alex Chen", "email": "alex@example.com", "skills": ["Python", "Go"],
                                     "target_categories": ["Software"], "university": "University of Waterloo",
                                     "program": "Computer Science", "summary": "Year 3 CS"}))).lastrowid
    jobs = [store.normalize({"source": "lever:shopify", "company": "Shopify", "title": "Software Engineer Intern",
                             "url": "https://jobs.lever.co/shopify/abc-123", "locations": ["Toronto, ON"],
                             "description": "Build things in Python and Go. Canada based applicants only."}),
            store.normalize({"source": "lever:wealthsimple", "company": "Wealthsimple", "title": "Backend Developer Co-op",
                             "url": "https://jobs.lever.co/wealthsimple/ws-1", "locations": ["Toronto, ON"],
                             "description": "Python, Go, Postgres. Software engineering co-op."}),
            store.normalize({"source": "lever:usonly", "company": "Usonly", "title": "Software Engineer Intern (USA)",
                             "url": "https://jobs.lever.co/usonly/us-1", "locations": ["Toronto, ON"],
                             "description": "Python and Go. US based applicants only."})]
    store.upsert_jobs(jobs, now)
    return pid, [j["id"] for j in jobs]


def main():
    pid, (shop, ws, usonly) = seed()
    checks = {}

    s = claude_code.status(refresh=True)
    checks["detects Claude Code and that it's signed in"] = s["installed"] and s["logged_in"] and s["version"] == "2.1.282"
    os.environ["FAKE_CLAUDE_SIGNED_OUT"] = "1"
    checks["...and notices when it's signed out"] = not claude_code.status(refresh=True)["logged_in"]
    os.environ.pop("FAKE_CLAUDE_SIGNED_OUT")
    claude_code.status(refresh=True)

    checks["the app keeps its old engine until you pick Claude Code"] = ai.provider() != "claude_code"
    db.meta_set("ai_engine", "claude_code")
    checks["picking it makes Claude Code the engine"] = ai.provider() == "claude_code" and ai.label() == "your Claude Code"

    out = ai._call_json("Be terse.", [{"type": "text", "text": "Alex Chen (alex@example.com) wants a letter"}],
                        ai._obj({"letter": ai._STR}), effort="medium")
    last = calls()[-1]
    checks["JSON tasks come back as structured output"] = out == {"letter": "Fake answer from Claude Code"}
    checks["...with no tools at all and only the JSON schema"] = "--tools" in last["args"] and \
        last["args"][last["args"].index("--tools") + 1] == "" and "--json-schema" in last["args"]
    checks["...with contact details redacted"] = "Alex Chen" not in last["stdin"] and "alex@example.com" not in last["stdin"]
    checks["...on your Claude plan: no API key reaches Claude Code"] = not any(c.get("leaked_key") for c in calls())

    LOG.unlink()
    res = agent.run(pid, [{"role": "user", "content": "best software roles in Toronto? star the top one"}])
    checks["chat on Claude Code: it uses the real tools through MCP"] = res["by"] == "claude_code" and \
        [x["tool"] for x in res["steps"]] == ["search_jobs", "star"]
    checks["...and the star really happened"] = bool(db.row("SELECT 1 FROM favourites WHERE profile_id=? AND kind='job'", (pid,)))
    checks["...with cards and Undo like the built-in agent"] = res["cards"] and res["cards"][0]["type"] == "jobs" and \
        res["cards"][0]["jobs"] and res["undo"] and res["undo"][0]["tool"] == "star"
    agent_args = next(c["args"] for c in calls() if "--mcp-config" in c.get("args", []))
    checks["...with only Co-op Coach's tools allowed (no shell/files/web)"] = agent_args[agent_args.index("--tools") + 1] == "" \
        and "--strict-mcp-config" in agent_args and "mcp__coopcoach" in agent_args
    chat_tools = next(c["mcp_tools"] for c in calls() if "mcp_tools" in c)
    checks["...and the chat doesn't get the raw contact-details tool"] = "application_files" not in chat_tools and "star" in chat_tools
    checks["the run's ticket is gone afterwards"] = not db.row("SELECT 1 FROM agent_tickets WHERE label='chat'")
    checks["...and so is its MCP config file"] = not list((Path(TMP) / "claude-work").glob("mcp-*.json"))

    out = agent.call(pid, "queue_application", {"job_id": usonly})
    checks["never prepares a posting they can't apply to"] = "error" in out and "can't apply" in out["error"]

    autopilot.save_settings(pid, {"engine": "rules", "per_day": 2, "min_score": 0})
    r = autopilot.run(pid, "manual")
    q = db.rows("SELECT job_id, source, status, kit FROM ready_queue WHERE profile_id=?", (pid,))
    checks["Autopilot (rules) prepares the best untouched matches"] = r.get("queued") == 2 and \
        {x["job_id"] for x in q} == {shop, ws} and all(x["source"] == "autopilot" for x in q)
    checks["...each with a full kit (answers + letter)"] = all(json.loads(x["kit"])["questions"] for x in q)
    checks["...and a brief"] = "Ready to apply" in (r.get("brief") or "")
    checks["...without ever submitting (status stays ready)"] = all(x["status"] == "ready" for x in q)
    checks["a prepared kit opens instantly from the queue"] = agent.cached_kit(pid, shop) is not None

    acts = agent.activity(pid)
    qa = next(a for a in acts if a["tool"] == "queue_application" and a["source"] == "autopilot")
    agent.undo_action(pid, qa["id"])
    checks["Activity log + Undo takes a prepared application back out"] = len(db.rows(
        "SELECT 1 FROM ready_queue WHERE profile_id=?", (pid,))) == 1 and agent.activity(pid)[0]["source"] == "undo"

    with db.tx() as c:
        c.execute("DELETE FROM ready_queue")
        c.execute("DELETE FROM favourites")
    LOG.unlink()
    autopilot.save_settings(pid, {"engine": "claude_code", "per_day": 1})
    r = autopilot.run(pid, "manual")
    q = db.rows("SELECT job_id, source, run_id FROM ready_queue WHERE profile_id=?", (pid,))
    checks["Autopilot on Claude Code queues through the MCP server"] = r.get("engine") == "claude_code" and len(q) == 1 \
        and q[0]["source"] == "autopilot" and q[0]["run_id"]
    ap_tools = next(c["mcp_tools"] for c in calls() if "mcp_tools" in c)
    checks["...with read tools + queue only (can't star, track or change preferences)"] = set(ap_tools) == set(
        agent.AUTOPILOT_TOOLS) and not db.row("SELECT 1 FROM favourites")
    checks["...and its brief is saved"] = "Ready to apply" in (autopilot.last_run(pid)["brief"] or "")

    os.environ["FAKE_CLAUDE_MODE"] = "limit"
    os.environ["GROQ_API_KEY"] = "test-key"
    try:
        claude_code.call_json("x", "y", {"type": "object", "properties": {}})
    except Exception:
        pass
    os.environ.pop("ANTHROPIC_API_KEY")  # with no Claude key either, the free tier is the backup
    checks["a usage limit pauses Claude Code and Groq takes over"] = claude_code.paused() > 0 and ai.provider() == "groq"
    os.environ["FAKE_CLAUDE_MODE"], os.environ["GROQ_API_KEY"] = "", ""
    claude_code._paused_until = 0

    t = auth.issue_ticket(pid, ttl=60)
    checks["tickets open only their own profile"] = auth.check_ticket(t) == pid and auth.check_ticket("nope") is None
    with db.tx() as c:
        c.execute("UPDATE agent_tickets SET expires_at=1")
    checks["...and stop working when they expire"] = auth.check_ticket(t) is None

    kept: dict[str, str] = {}  # never touch the real Keychain from a test
    claude_setup.vault.available, claude_setup.vault.put = (lambda: True), kept.__setitem__
    claude_setup.vault.get, claude_setup.vault.delete = kept.get, (lambda k: kept.pop(k, None))
    LOG.unlink(missing_ok=True)
    res = claude_setup.connect(pid)
    plug = Path(TMP) / "claude-plugin" / "coop-coach"
    mcp = json.loads((plug / ".mcp.json").read_text())["mcpServers"]["coop-coach"]
    checks["Connect builds the plugin with this computer's paths"] = res["ok"] and mcp["command"] == sys.executable \
        and mcp["env"]["COOPCOACH_DATA"] == TMP and (plug / "skills" / "apply" / "SKILL.md").exists()
    checks["...installs it through `claude plugin`"] = [c["plugin"][1:3] for c in calls() if "plugin" in c][-2:] == \
        [["marketplace", "add"], ["install", "coop-coach@coop-coach-local"]]
    tok = kept.get(f"claude-connect:{pid}")
    checks["...and its token lives in the Keychain, only a hash in the database"] = bool(tok) and auth.check_ticket(tok) == pid \
        and not db.row("SELECT 1 FROM agent_tickets WHERE token_hash=?", (tok,))
    claude_setup.disconnect(pid)
    checks["Disconnect revokes it"] = auth.check_ticket(tok) is None and f"claude-connect:{pid}" not in kept

    hook = Path(__file__).resolve().parent.parent / "claude-plugin" / "hooks" / "no_submit.py"
    run = lambda s: subprocess.run([sys.executable, str(hook)], input=json.dumps({"tool_input": {"action_summary": s}}),  # noqa: E731
                                   capture_output=True, text=True).returncode
    checks["the plugin's guard blocks Claude from submitting an application"] = run("Submits the application to Shopify") == 2 \
        and run("Clicks the Submit button") == 2
    checks["...but lets it fill and move between pages"] = run("Clicks Next to page 2") == 0 and run("Uploads the resume") == 0

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
