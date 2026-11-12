"""Pairing/CSRF/lock/revocation through the real API; optional real unpacked-extension smoke test."""
import asyncio
import os
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="coopcoach-extension-test-"))
os.environ.update(COOPCOACH_DATA=str(TMP), COOPCOACH_PG_SETTINGS=str(TMP / "none.json"),
                  COOPCOACH_DATABASE_URL="", COOPCOACH_PG_SCHEMA="", GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, auth, db, extension_auth, mcp_server, vault  # noqa: E402
from coopcoach.server import app  # noqa: E402

checks = {}


def check(name, ok):
    checks[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def main():
    db.init()
    owner = TestClient(app, base_url="http://localhost")
    pid = owner.post("/api/profiles", json={"name": "Extension Fiction", "passcode": "scratch-123"}).json()["id"]
    origin = "chrome-extension://" + "a" * 32
    stranger = TestClient(app, base_url="http://localhost")
    check("locked app cannot issue pairing code", stranger.post("/api/extension/code", json={"profile_id": pid}).status_code == 423)
    code = owner.post("/api/extension/code", json={"profile_id": pid}).json()["code"]
    check("website cannot exchange even a valid pairing code", stranger.post("/api/extension/pair", json={"code": code}, headers={"Origin": "https://evil.example"}).status_code == 403)
    r = stranger.post("/api/extension/pair", json={"code": code}, headers={"Origin": origin})
    check("extension exchanges unlocked app's code", r.status_code == 200)
    token = r.json()["ticket"]
    headers = {"Origin": origin, "Authorization": "Bearer " + token}
    check("code is single-use", stranger.post("/api/extension/pair", json={"code": code}, headers=headers).status_code == 401)
    check("paired extension can read Today", stranger.post("/api/extension/today", headers=headers).status_code == 200)
    unknown = "https://example.test/unknown"
    r = stranger.post("/api/extension/context", json={"url": unknown}, headers=headers)
    check("unknown posting provides basics without claiming prepared files", r.status_code == 200 and not r.json()["prepared"] and not r.json()["job"]["id"])
    r = stranger.post("/api/extension/answers", headers=headers, json={"url": unknown, "questions": [
        {"id": str(i), "label": label, "options": ["Yes", "No"]} for i, label in enumerate([
            "Will you require sponsorship?", "Country of birth", "Citizenship", "Login email", "Gender"])]})
    check("unknown job authorization, demographics and credentials stay unanswered", r.status_code == 200
          and all(not a["answer"] and a["needs_you"] for a in r.json()["answers"]))
    check("unknown posting cannot change tracker", stranger.post("/api/extension/submitted", json={"url": unknown}, headers=headers).status_code == 404)
    check("unprepared posting returns no attachment", stranger.post("/api/extension/files", json={"url": unknown}, headers=headers).json() == {"files": []})
    check("malformed URL is refused cleanly", stranger.post("/api/extension/context", json={"url": "http://[bad"}, headers=headers).status_code == 400)
    # reading the Outlook tab the student has open: only page text comes in, and the app decides what to open
    plan = stranger.post("/api/extension/inbox-plan", headers=headers, json={"rows": [
        {"id": "r1", "label": "Shopify Thank you for applying", "dated": "Mon 2026-08-24 00:51"},
        {"id": "r2", "label": "LinkedIn Job Alert: 30 new jobs for you", "dated": "Mon 2026-08-24 00:52"}]})
    check("inbox plan opens a possible application email and skips a job alert",
          plan.status_code == 200 and plan.json()["open"] == ["r1"])
    got = stranger.post("/api/extension/inbox", headers=headers, json={"messages": [
        {"id": "r1", "dated": "Mon 2026-08-24 00:51", "when": "Tue 2026-11-03 21:27", "subject": "Thank you for applying to Shopify",
         "sender": "Shopify Early Talent <earlytalent@shopify.com>", "body": "We received your application for the Developer Intern role."}]})
    ev = db.row("SELECT received_at, company FROM email_events WHERE message_id='owa:r1'")
    check("an email read from the tab is filed, dated by its own row (not the scan time)",
          got.status_code == 200 and ev and time.localtime(ev["received_at"]).tm_mon == 8 and ev["company"] == "Shopify")
    check("the same row isn't opened twice",
          stranger.post("/api/extension/inbox-plan", headers=headers, json={"rows": [{"id": "r1", "label": "Shopify"}]}).json()["open"] == [])
    check("a website can't post inbox text with the ticket",
          stranger.post("/api/extension/inbox", headers={**headers, "Origin": "https://evil.example"}, json={"messages": []}).status_code == 403)
    check("other extension cannot reuse ticket",stranger.post("/api/extension/today", headers={**headers, "Origin": "chrome-extension://" + "b" * 32}).status_code == 403)
    check("website cannot reuse ticket", stranger.post("/api/extension/today", headers={**headers, "Origin": "https://evil.example"}).status_code == 403)
    check("ticket cannot bypass ordinary API CSRF", stranger.post("/api/profiles", json={"name": "Injected"}, headers=headers).status_code == 403)
    owner.post("/api/auth/lock")
    check("locking paired session blocks existing ticket", stranger.post("/api/extension/today", headers=headers).status_code == 423)
    check("lock covers every form endpoint", all(stranger.post("/api/extension/" + endpoint,
          json={"url": unknown, **({"questions": []} if endpoint == "answers" else {})}, headers=headers).status_code == 423
          for endpoint in ("context", "answers", "files", "submitted")))
    owner.post("/api/auth/unlock", json={"profile_id": pid, "passcode": "scratch-123"})
    check("unlock restores paired session", stranger.post("/api/extension/today", headers=headers).status_code == 200)
    auth.revoke_ticket(token)
    check("database revocation blocks the existing connection", stranger.post("/api/extension/today", headers=headers).status_code == 401)
    # No real Keychain: reproduce the previously cached MCP authorization with a memory vault.
    memory = {mcp_server.CONNECT_KEY: auth.issue_ticket(pid, ttl=None, label="Claude Code plugin")}
    real = vault.available, vault.get
    vault.available, vault.get = lambda: True, memory.get
    try:
        plugin = mcp_server.Plugin("test")
        check("MCP initially authorized", "error" not in plugin.call("coding_progress", {}))
        auth.revoke_ticket(memory[mcp_server.CONNECT_KEY])
        memory.clear()
        check("existing MCP process loses revoked authorization", "error" in plugin.call("record_problem", {"problem": "two-sum", "result": "solved"}))
    finally:
        vault.available, vault.get = real
    run_ticket = auth.issue_ticket(pid, ttl=60, label="test run")
    previous = os.environ.get("COOPCOACH_TICKET")
    os.environ["COOPCOACH_TICKET"] = run_ticket
    try:
        _, description, schema = agent.TOOLS["coding_progress"]
        tool = mcp_server._tool(pid, "coding_progress", description, schema, "test", None)
        check("direct MCP run ticket initially authorizes the tool", "error" not in asyncio.run(tool.run({}, None)))
        auth.revoke_ticket(run_ticket)
        check("direct MCP tool rechecks a revoked run ticket", "error" in asyncio.run(tool.run({}, None)))
    finally:
        if previous is None:
            os.environ.pop("COOPCOACH_TICKET", None)
        else:
            os.environ["COOPCOACH_TICKET"] = previous
    check("session credentials are not persistent connection grants", "Chrome extension" not in auth.CONNECTION_LABELS)
    extension_auth.disconnect(pid)
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
