"""Preparing is immediate; the slow part finishes behind it. With an AI that takes far too long, three applications are Ready in
under a second each, the AI's letter arrives later, a restart picks up interrupted work, your own edits are never replaced, and an
AI that keeps failing leaves the application Ready with what it had. Instant chat answers and the model list are checked here too.

Run: .venv/bin/python -m tests.test_instant_prepare
"""

import json
import os
import tempfile
import threading
import time

TMP = tempfile.mkdtemp(prefix="coopcoach-prepare-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="", ANTHROPIC_API_KEY="")

from coopcoach import agent, ai, apply, claude_code, config, db, jobdetail, polish, quick_chat, store  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def letter_of(pid, job_id):
    return db.row("SELECT id, content, generated_by FROM documents WHERE profile_id=? AND kind='cover_letter' AND job_id=?", (pid, job_id))


def row_of(pid, job_id):
    return db.row("SELECT status, polish, polish_tries FROM ready_queue WHERE profile_id=? AND job_id=?", (pid, job_id))


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json, resume_text) VALUES (?,?,?,?,?,?)",
                        ("Tester", now, now, json.dumps({"locations": ["Toronto"], "citizenship": "Canadian"}),
                         json.dumps({"skills": ["Python"], "target_categories": ["Software"], "name": "Alex Tester", "email": "a@example.com"}),
                         "Alex Tester. Python.")).lastrowid
    store.upsert_jobs([store.normalize({"source": "lever:firm", "company": f"Firm{i}", "title": "Software Engineer Intern",
                                        "url": f"https://jobs.lever.co/firm{i}/abc", "locations": ["Toronto, ON"],
                                        "description": "Python. " * 60}) for i in range(8)], now)
    ids = [j["id"] for j in store.scored_jobs(pid)]

    slow, gate = [], threading.Event()
    ai.available = lambda: True
    ai.provider = lambda: "claude_code"

    def writer(*a, **k):
        slow.append(1)
        gate.wait(30)  # an AI that takes as long as the test wants
        return "An AI letter."
    ai.write_cover_letter = writer
    ai._call_json = lambda *a, **k: (gate.wait(30), {"answers": []})[1]
    jobdetail.ensure_description = lambda job_id: "Python. " * 60

    took = []
    for job_id in ids[:3]:
        t = time.time()
        out = agent.call(pid, "queue_application", {"job_id": job_id, "why": "test"}, source="chat", run_id="r1")
        took.append(time.time() - t)
        check(f"it's Ready as soon as it's asked for ({took[-1] * 1000:.0f} ms)", out.get("ok") and out.get("polishing") and took[-1] < 1.5)
    check("three applications Ready inside the 5 second target, with an AI that hasn't finished", sum(took) < 5 and not gate.is_set())
    check("...each is really usable: answers and a letter already", all(
        json.loads(db.row("SELECT kit FROM ready_queue WHERE profile_id=? AND job_id=?", (pid, i))["kit"])["questions"] and letter_of(pid, i) for i in ids[:3]))
    check("...and says it's still being finished", {row_of(pid, i)["polish"] for i in ids[:3]} <= {"pending", "running"})
    check("the quick letter was written without AI", all(letter_of(pid, i)["generated_by"] == "rules" for i in ids[:3]))

    # a letter you edit while the AI is writing stays yours
    time.sleep(0.2)
    with db.tx() as c:
        c.execute("UPDATE documents SET content='Mine.' WHERE profile_id=? AND job_id=?", (pid, ids[2]))
    # something you applied to meanwhile stops being worked on
    with db.tx() as c:
        c.execute("UPDATE ready_queue SET status='applied' WHERE profile_id=? AND job_id=?", (pid, ids[1]))
    gate.set()
    polish.wait(30)
    check("the AI's letter replaces the quick one later", letter_of(pid, ids[0])["content"] == "An AI letter." and letter_of(pid, ids[0])["generated_by"] == "claude_code")
    check("...it's marked finished", row_of(pid, ids[0])["polish"] == "done")
    check("a letter edited while the AI was writing is kept", letter_of(pid, ids[2])["content"] == "Mine.")

    # a letter you edited before it started is kept too, through the app's own route
    from coopcoach import routes_docs
    agent.call(pid, "queue_application", {"job_id": ids[3], "why": "x"}, source="chat", run_id="r2")
    polish.wait(30)
    routes_docs.put_letter(letter_of(pid, ids[3])["id"], {"content": "My words."})
    with db.tx() as c:
        c.execute("UPDATE ready_queue SET polish='pending' WHERE profile_id=? AND job_id=?", (pid, ids[3]))
    polish.enqueue(pid, ids[3])
    polish.wait(30)
    check("a letter you've edited is never replaced", letter_of(pid, ids[3])["content"] == "My words.")

    # a restart: interrupted work is found on the row and finished
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO ready_queue (profile_id, job_id, status, why, kit, source, run_id, created_at, updated_at, polish, polish_tries)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,0)", (pid, ids[4], "ready", "x", db.dumps(apply.build_kit(pid, ids[4], quick=True)), "chat", "r3", now, now, "running"))
    check("after a restart, interrupted work goes back in the queue", polish.resume() >= 1)
    polish.wait(30)
    check("...and gets finished", row_of(pid, ids[4])["polish"] == "done" and letter_of(pid, ids[4])["generated_by"] == "claude_code")

    # an AI that keeps failing
    def broken(*a, **k):
        raise RuntimeError("the AI is down")
    ai.write_cover_letter = broken
    polish.RETRY_WAIT = 0.05
    agent.call(pid, "queue_application", {"job_id": ids[5], "why": "x"}, source="chat", run_id="r4")
    for _ in range(100):
        polish.wait(10)
        if row_of(pid, ids[5])["polish"] == "failed":
            break
        time.sleep(0.05)
    check("an AI that keeps failing is retried, then left alone", row_of(pid, ids[5])["polish"] == "failed" and row_of(pid, ids[5])["polish_tries"] == polish.MAX_TRIES)
    check("...and the application stays Ready, with its quick letter", row_of(pid, ids[5])["status"] == "ready" and letter_of(pid, ids[5])["generated_by"] == "rules")

    # a slow form doesn't hold it up
    apply._ats = lambda *a: "greenhouse"
    from coopcoach.sources import ats
    seen = []

    def slow_form(url, source, timeout=20):
        seen.append(timeout)
        raise TimeoutError("no answer")
    ats.greenhouse_job = slow_form
    kit = apply.build_kit(pid, ids[6], quick=True)
    check("a form that doesn't answer gets a moment, then the usual questions", seen == [apply.QUICK_FORM_WAIT] and kit["questions"])

    # the model list
    check("the bulk work follows the list too, not an older setting", claude_code.model_for("fast") == claude_code.model_for("scoring") == "haiku")
    check("the list decides which model does what", claude_code.model_for("chat") == "haiku" and claude_code.model_for("letter") == "sonnet"
          and claude_code.model_for("prep_sheet") == "opus" and claude_code.model_for("nothing listed") == claude_code.model_for("default") == "sonnet")
    config.get, real_get = (lambda k, d=None: "opus" if k == "ai.models.chat" else d), config.get
    check("...and a setting can override it", claude_code.model_for("chat") == "opus")
    config.get = real_get
    lst = claude_code._model_list()
    plugin = config.PKG.parent / "claude-plugin"
    wrong = [k for k, m in lst["commands"].items() if f"\nmodel: {m}\n" not in (plugin / "skills" / k / "SKILL.md").read_text(encoding="utf-8")]
    wrong += [k for k, m in lst["agents"].items() if f"\nmodel: {m}\n" not in (plugin / "agents" / f"{k}.md").read_text(encoding="utf-8")]
    missing = [p.name for p in (plugin / "skills").iterdir() if p.is_dir() and p.name not in lst["commands"]]
    check("the plugin's commands use the same list", not wrong and not missing)

    # instant answers
    t = time.time()
    out = agent.run(pid, [{"role": "user", "content": "how's my neetcode?"}])
    took = time.time() - t
    check(f"a plain question needs no AI ({took * 1000:.0f} ms)", out["by"] == "instant" and "NeetCode 150" in out["reply"] and took < (1.5 if db.is_postgres() else 0.5))  # (Postgres: over the internet)
    check("...and shows in the steps like any other", [s["tool"] for s in out["steps"]] == ["coding_progress"])
    out = agent.run(pid, [{"role": "user", "content": "I solved Two Sum in 12 minutes"}])
    check("logging a problem is instant", out["by"] == "instant" and "Two Sum" in out["reply"] and "12 minutes" in out["reply"])
    check("...it's really recorded, with Undo", db.row("SELECT result, seconds FROM coding_attempts WHERE profile_id=? AND slug='two-sum'", (pid,)) and out["undo"])
    ai.available = lambda: False
    out = routes_docs.chat({"profile_id": pid, "messages": [{"role": "user", "content": "what's my streak?"}]})
    check("...and with no AI connected at all", out["by"] == "instant" and "streak" in out["reply"])
    out = routes_docs.chat({"profile_id": pid, "messages": [{"role": "user", "content": "what's ready to apply?"}]})
    check("what's ready, from the list itself", out["by"] == "instant" and "ready to apply" in out["reply"])
    out = routes_docs.chat({"profile_id": pid, "messages": [{"role": "user", "content": "what's coming up?"}]})
    check("what's coming up, from the calendar", out["by"] == "instant" and "calendar" in out["reply"])
    out = routes_docs.chat({"profile_id": pid, "messages": [{"role": "user", "content": "what's next?"}]})
    check("what's next, a link to the problem", out["by"] == "instant" and "leetcode.com/problems/" in out["reply"])
    ai.available = lambda: True
    from fastapi.testclient import TestClient

    from coopcoach import server
    with TestClient(server.app, base_url="http://localhost") as web:
        same = {"Origin": "http://localhost"}
        r = web.post("/api/chat/stream", json={"profile_id": pid, "messages": [{"role": "user", "content": "what's my streak?"}]}, headers=same)
        lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        check("the chat's stream ends with the finished reply", r.status_code == 200 and lines[-1]["t"] == "done" and "streak" in lines[-1]["v"]["reply"])
        check("...and refuses a request with no message before it starts", web.post("/api/chat/stream", json={"profile_id": pid, "messages": []}, headers=same).status_code == 400)
    for text in ("how's my neetcode compared to my friends?", "I solved world hunger", "I solved Two Sum, what should I do about my Google interview?", "why is my streak so low", "what's ready for the Shopify one"):
        check(f"not an exact request, so the AI answers: {text!r}", quick_chat.answer(pid, text) is None)

    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
