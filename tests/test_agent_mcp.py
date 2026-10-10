"""The chat agent's tools, its tool loop (against a mock Groq), the application kit, and the MCP server end to end.

Run: .venv/bin/python -m tests.test_agent_mcp
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

TMP = tempfile.mkdtemp(prefix="coopcoach-agenttest-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="test-key", GROQ_BASE_URL="http://127.0.0.1:8798/openai/v1",
                  ANTHROPIC_API_KEY="")

from coopcoach import agent, apply, auth, db, store  # noqa: E402

SENT: list[dict] = []


class Mock(BaseHTTPRequestHandler):
    """Round 1: call search_jobs. Round 2: answer, mentioning the student by name (must come back restored)."""
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SENT.append(body)
        if body.get("tools") and not any(m["role"] == "tool" for m in body["messages"]):
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "search_jobs", "arguments": json.dumps({"location": "Toronto", "role": "software"})}}]}
        else:
            name_token = next((t for t in ("[[P0]]", "[[P1]]") if t in json.dumps(body)), "")
            msg = {"role": "assistant", "content": f"Top pick for {name_token}: Shopify in Toronto."}
        data = json.dumps({"choices": [{"message": msg, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def seed():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES (?,?,?,?,?)",
                        ("Tester", now, now, json.dumps({"locations": ["Toronto", "Canada"], "citizenship": "Canadian"}),
                         json.dumps({"name": "Alex Chen", "email": "alex@example.com", "skills": ["Python", "Go"],
                                     "target_categories": ["Software"], "university": "University of Waterloo",
                                     "program": "Computer Science", "summary": "Year 3 CS"}))).lastrowid
        locked = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES"
                           " ('Locked',?,?,'{}','{}')", (now, now)).lastrowid
        c.execute("INSERT INTO event_feeds (id, url, label, created_at) VALUES (1, 'https://x', 'Meetup', ?)", (now,))
        for eid, title, kind in (("e1", "Waterloo Hack Night", "hackathon"), ("e2", "RBC Info Session", "info_session")):
            c.execute("INSERT INTO events (id, feed_id, title, start_ts, location, url, description, kind, first_seen,"
                      " last_seen, region) VALUES (?,1,?,?,?,?,?,?,?,?,?)",
                      (eid, title, now + 86400, "Waterloo, ON", "https://e", "", kind, now, now, "Waterloo Region"))
    auth.set_passcode(locked, "2468")
    jobs = [store.normalize({"source": "lever:shopify", "company": "Shopify", "title": "Software Engineer Intern",
                             "url": "https://jobs.lever.co/shopify/abc-123", "locations": ["Toronto, ON"],
                             "description": "Build things in Python and Go. Canada based applicants only."}),
            store.normalize({"source": "lever:acme", "company": "Acme", "title": "Software Developer Co-op",
                             "url": "https://jobs.lever.co/acme/def-456", "locations": ["Vancouver, BC"],
                             "description": "Python"})]
    store.upsert_jobs(jobs, now)
    return pid, jobs


async def mcp_session(profile: str, env: dict):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-m", "coopcoach", "mcp", "--profile", profile],
                                   env={**os.environ, **env})
    async with stdio_client(params, errlog=open(os.devnull, "w")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            res = await s.call_tool("search_jobs", {"location": "Toronto"})
            star = await s.call_tool("star", {"company": "Shopify"})
            return [t.name for t in tools.tools], res, star


def main():
    pid, jobs = seed()
    checks = {}
    shop = jobs[0]["id"]

    r = agent.call(pid, "search_jobs", {"location": "Toronto", "role": "software"})
    checks["search_jobs filters by city and role"] = [j["company"] for j in r["jobs"]] == ["Shopify"]
    d = agent.call(pid, "job_details", {"job_id": shop})
    checks["job_details says who can apply"] = d["eligibility"] == "fit" and any("Canada" in w for w in d["who_can_apply"])
    s = agent.call(pid, "star", {"job_id": shop})
    checks["star writes a favourite and returns an undo"] = bool(db.row(
        "SELECT 1 FROM favourites WHERE profile_id=? AND key=?", (pid, shop))) and s["_undo"]["args"]["on"] is False
    agent.call(pid, s["_undo"]["tool"], s["_undo"]["args"])
    checks["...and undo removes it"] = not db.row("SELECT 1 FROM favourites WHERE profile_id=? AND key=?", (pid, shop))
    t = agent.call(pid, "track", {"job_id": shop, "status": "applied"})
    checks["track sets the status"] = (db.row("SELECT status FROM applications WHERE profile_id=? AND job_id=?",
                                              (pid, shop)) or {}).get("status") == "applied"
    agent.call(pid, t["_undo"]["tool"], t["_undo"]["args"])
    checks["...and undo puts it back (untracked)"] = not db.row(
        "SELECT 1 FROM applications WHERE profile_id=? AND job_id=?", (pid, shop))
    ev = agent.call(pid, "find_events", {"region": "Waterloo", "kind": "hackathon,info_session"})
    checks["find_events understands several kinds at once"] = ev["total"] == 2
    agent.call(pid, "save_event", {"event_id": "e1", "mark": "going"})
    checks["save_event marks it"] = db.row("SELECT mark FROM event_marks WHERE event_id='e1'")["mark"] == "going"
    u = agent.call(pid, "update_preferences", {"locations": ["Waterloo"], "roles": ["quant"]})
    p = store.get_profile(pid)["prefs"]
    checks["update_preferences changes locations + roles"] = p["locations"] == ["Waterloo"] and p["categories"] == ["Quant"]
    agent.call(pid, u["_undo"]["tool"], u["_undo"]["args"])
    checks["...and undo restores them"] = store.get_profile(pid)["prefs"]["locations"] == ["Toronto", "Canada"]
    checks["unknown tools and bad args come back as errors, not crashes"] = (
        "error" in agent.call(pid, "delete_everything", {}) and "error" in agent.call(pid, "track", {"job_id": "nope", "status": "applied"}))

    os.environ["GROQ_API_KEY"] = ""  # the kit's free-text drafts are AI; check the deterministic part
    kit = apply.build_kit(pid, shop)
    by = {q["label"]: q for q in kit["questions"]}
    checks["kit: Lever form opens at /apply"] = kit["job"]["apply_url"].endswith("/apply")
    checks["kit: name and email filled from the profile"] = by["First Name"]["answer"] == "Alex" and \
        by["Email"]["answer"] == "alex@example.com"
    checks["kit: Canadian + Canada role -> authorized Yes, sponsorship No"] = (
        by["Are you legally authorized to work in this country?"]["answer"] == "Yes"
        and by["Will you now or in the future require sponsorship?"]["answer"] == "No")
    from coopcoach.apply import _rule_answer
    a, needs, _ = _rule_answer("What is your gender identity?", "select", ["Man", "Woman"], kit["basics"], kit["job"], {}, "")
    checks["kit never answers voluntary demographic questions"] = a == "" and needs
    prompt = apply.agent_prompt(kit)
    checks["hand-off prompt tells the agent not to submit"] = "do NOT press Submit" in prompt and "Alex" in prompt
    os.environ["GROQ_API_KEY"] = "test-key"

    srv = HTTPServer(("127.0.0.1", 8798), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out = agent.run(pid, [{"role": "user", "content": "best software roles in Toronto?"}])
    checks["agent loop: calls the tool, then answers"] = [s_["tool"] for s_ in out["steps"]] == ["search_jobs"] and \
        "Shopify" in out["reply"]
    checks["agent loop: shows results as a card"] = out["cards"] and out["cards"][0]["type"] == "jobs"
    checks["agent loop: the student's name and email never leave the machine"] = (
        "Alex Chen" not in json.dumps(SENT) and "alex@example.com" not in json.dumps(SENT))
    srv.shutdown()

    names, res, star = asyncio.run(mcp_session("Tester", {"GROQ_API_KEY": ""}))
    checks["MCP: Claude sees every tool"] = len(names) == len(agent.TOOLS) and "application_kit" in names
    text = json.dumps([c.model_dump() for c in res.content])
    checks["MCP: search_jobs works over the protocol"] = "Shopify" in text and not res.is_error
    checks["MCP: actions write to the same tracker"] = bool(db.row(
        "SELECT 1 FROM favourites WHERE profile_id=? AND kind='company'", (pid,)))
    try:
        asyncio.run(mcp_session("Locked", {"COOPCOACH_PASSCODE": "wrong"}))
        checks["MCP: a locked profile needs its passcode"] = False
    except BaseException:
        checks["MCP: a locked profile needs its passcode"] = True

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
