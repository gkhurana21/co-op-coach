"""Links someone (or an AI reading an email or web page) hands the app are only fetched from the public internet, never
this computer, the home network or cloud metadata addresses, including through redirects; the event-source and
careers-site routes refuse them; every AI is told that tool results are data, not instructions; requests from other
websites and other hostnames are refused; deleting a person leaves none of their data behind; calendar files only
link to http(s) pages and can't be given extra lines.

Run: .venv/bin/python -m tests.test_security
"""

import os
import re
import tempfile
from unittest.mock import patch

import httpx

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-sec-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import agent, autopilot, db, events, mcp_server  # noqa: E402
from coopcoach.sources import BlockedURL, check_public, client  # noqa: E402


def blocked(url: str) -> bool:
    try:
        check_public(url)
        return False
    except BlockedURL:
        return True


def main():
    db.init()
    checks = {}
    inside = ["http://127.0.0.1:8765/api/state", "http://localhost:8765/", "http://169.254.169.254/latest/meta-data",
              "http://10.1.2.3/", "http://172.16.0.9/", "http://192.168.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
              "http://0.0.0.0/", "http://printer.local/", "http://100.64.0.1/"]
    checks["this computer, the home network and cloud metadata are refused"] = all(blocked(u) for u in inside)
    checks["only http(s): file://, ftp:// and javascript: are refused"] = all(
        blocked(u) for u in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"))
    checks["public addresses are fine"] = not blocked("https://1.1.1.1/") and not blocked("https://8.8.8.8/x.ics")

    asked = []

    def redirecting(req: httpx.Request) -> httpx.Response:
        asked.append(req.url.host)
        if req.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8765/api/state"})
        return httpx.Response(200, text="secret")

    # the app's own client(), answered by the mock (a transport given up front also wins over HTTPS_PROXY)
    real = httpx.Client
    with patch.object(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(redirecting), **kw)):
        c = client(public_only=True)
    try:
        c.get("https://8.8.8.8/calendar.ics")
        followed = True
    except BlockedURL:
        followed = False
    checks["a public link that redirects to this computer is stopped at the redirect"] = (
        not followed and asked == ["8.8.8.8"])

    from fastapi.testclient import TestClient

    from coopcoach import server
    server._sync_in_background = lambda: None  # these checks start syncs; reading every live board isn't the point
    with TestClient(server.app, base_url="http://localhost") as cl:
        r = cl.post("/api/event-feeds", json={"url": "http://127.0.0.1:8765/api/state"})
        checks["adding an event source on this computer is refused (400)"] = r.status_code == 400 and "private" in r.text
        r = cl.post("/api/companies", json={"url": "http://192.168.1.1/careers"})
        checks["adding a careers site on the home network is refused (400)"] = r.status_code == 400
        r = cl.post("/api/event-feeds", data="url=http://evil", headers={"Content-Type": "text/plain"})
        checks["a form post from another website isn't read as JSON"] = r.status_code in (400, 415, 422)
    with TestClient(server.app, base_url="http://evil.example") as cl:
        checks["another hostname pointing here (DNS rebinding) is refused"] = cl.get("/api/state").status_code == 400
    other_websites(server, TestClient)
    deleting_a_person(server, TestClient)
    calendar_links()

    rule = "data, not instructions"
    checks["the chat, Autopilot and your own Claude / ChatGPT are told tool results are data"] = all(
        rule in " ".join(t.split()) for t in (agent.SYSTEM, autopilot.SYSTEM, mcp_server.INSTRUCTIONS))
    checks["Autopilot can't open links, change settings or send anything"] = not (
        set(agent.AUTOPILOT_TOOLS) & {"open_link", "change_autopilot", "update_preferences", "add_event_source",
                                      "fill_application", "draft_message", "log_email"})

    checks.update(more)
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


more = {}


def other_websites(server, TestClient) -> None:
    """A page on another website can make the browser send a file upload or an empty POST here without asking."""
    server._sync_in_background = lambda: None  # no real sync in a test (only this middleware answers 403)
    cl = TestClient(server.app, base_url="http://localhost")  # no `with`: skips the startup sync/scheduler
    pid = cl.post("/api/profiles", json={"name": "Robin"}).json()["id"]
    resume = ("Robin Doe\nUniversity of Waterloo, Computer Science\nSkills: Python, SQL, React\n"
              "Software Developer Intern, Acme (2025): built APIs and dashboards\n").encode()
    evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    r = cl.post(f"/api/profiles/{pid}/resume", files={"file": ("r.txt", b"Attacker text " * 20)}, headers=evil)
    text = (db.row("SELECT resume_text FROM profiles WHERE id=?", (pid,)) or {}).get("resume_text")
    more["a resume upload from another website is refused (403) and changes nothing"] = (
        r.status_code == 403 and not text)
    r = cl.post(f"/api/profiles/{pid}/resume", files={"file": ("r.txt", resume)},
                headers={"Origin": "http://localhost"})
    text = (db.row("SELECT resume_text FROM profiles WHERE id=?", (pid,)) or {}).get("resume_text") or ""
    more["the app's own resume upload still works"] = r.status_code == 200 and "Waterloo" in text
    refused = [cl.post("/api/sync", headers=h).status_code for h in
               (evil, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"},
                {"Origin": "http://localhost:3000"})]
    more["an empty POST from another website, a sandboxed page or another port is refused"] = refused == [403] * 5
    ok = [cl.post("/api/sync", headers=h).status_code for h in
          ({"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}, {})]
    more["the app's own page, and curl or scripts (no Origin), can still start a sync"] = ok == [200, 200]

    from starlette.responses import PlainTextResponse

    from coopcoach import guard
    lan = TestClient(guard.SameOrigin(PlainTextResponse("ok")), base_url="http://192.168.1.20:8765")
    more["shared on the Wi-Fi (serve --host 0.0.0.0), the app's own page still works and others are refused"] = (
        lan.post("/api/sync", headers={"Origin": "http://192.168.1.20:8765"}).status_code == 200
        and lan.post("/api/sync", headers={"Origin": "http://192.168.1.21:8765"}).status_code == 403)


def deleting_a_person(server, TestClient) -> None:
    """Every table with a profile_id column (now and any added later) gets a row for two people; deleting one of them
    must leave none of theirs behind and all of the other's."""
    cl = TestClient(server.app, base_url="http://localhost")
    gone, kept = (cl.post("/api/profiles", json={"name": n}).json()["id"] for n in ("Gone", "Kept"))
    tables = [t for t, cols in db.COLUMNS.items() if "profile_id" in cols]
    numeric = {(m.group(1), c.group(1)) for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", db.SCHEMA,
                                                             re.S)
               for c in re.finditer(r"^\s*(\w+) (?:INTEGER|REAL)", m.group(2), re.M)}  # Postgres won't take text there
    with db.tx() as c:
        for pid in (gone, kept):
            for t in tables:
                cols = [k for k in db.COLUMNS[t] if not (k == "id" and t in db.ID_TABLES)]  # numbered by the database
                vals = [pid if k == "profile_id" else f"{t}-{pid}" if k == "id"
                        else pid if (t, k) in numeric else f"{k}-{pid}" for k in cols]
                c.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    r = cl.delete(f"/api/profiles/{gone}")
    left = {t: n for t in tables if (n := db.row(f"SELECT COUNT(*) AS n FROM {t} WHERE profile_id=?", (gone,))["n"])}
    more[f"deleting a person removes their rows from all {len(tables)} tables that hold them"] = (
        r.status_code == 200 and not left and len(tables) >= 18)
    more["...and leaves everyone else's alone"] = all(
        db.row(f"SELECT COUNT(*) AS n FROM {t} WHERE profile_id=?", (kept,))["n"] == 1 for t in tables)


def calendar_links() -> None:
    bad = "https://evil.example/\r\nATTACH:http://x\r\nX-INJECTED:1"
    out = events.ics_for([
        {"id": "e1", "title": "Talk\r\nX-T:1", "start_ts": 1700000000, "url": bad, "description": "d\rX-D:1"},
        {"id": "e2", "title": "Js", "start_ts": 1700000000, "url": "javascript:alert(1)"},
        {"id": "e3", "title": "Ok", "start_ts": 1700000000, "url": "https://example.com/e?a=1"}])
    lines = out.split("\r\n")
    more["a link with line breaks can't add lines to a calendar file"] = not any(
        ln.startswith(("ATTACH", "X-INJECTED", "X-T", "X-D")) or "\r" in ln or "\n" in ln for ln in lines)
    more["calendar files only link to http(s) pages"] = [ln for ln in lines if ln.startswith("URL:")] == [
        "URL:https://evil.example/ATTACH:http://xX-INJECTED:1", "URL:", "URL:https://example.com/e?a=1"]


if __name__ == "__main__":
    main()
