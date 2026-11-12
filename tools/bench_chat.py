"""Real timings of the chat and of preparing, on your own Claude Code and a throwaway copy of the demo. It spends a little of your
Claude usage. It never touches your real data.

Run: .venv/bin/python -m tools.bench_chat
"""

import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="coopcoach-bench-chat-")
for name in ("coopcoach.db", "app.json"):
    if (ROOT / "data-demo" / name).exists():
        shutil.copy(ROOT / "data-demo" / name, TMP)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
os.environ.update(COOPCOACH_DATA=TMP, COOPCOACH_PORT=str(PORT), GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_DEMO="1")
os.environ.pop("COOPCOACH_DATABASE_URL", None)

import uvicorn  # noqa: E402

from coopcoach import agent, claude_chat, claude_code, db, polish, server, store  # noqa: E402


def ask(pid: int, text: str) -> dict:
    first, steps = [], []
    t = time.time()

    def on_event(kind, v):
        if kind == "delta" and not first:
            first.append(time.time() - t)
        elif kind == "step":
            steps.append(v)
    out = agent.run(pid, [{"role": "user", "content": text}], on_event=on_event)
    took = time.time() - t
    print(f"  {text[:58]!r:62} first word {(first[0] if first else took):5.1f} s, done {took:5.1f} s  by {out.get('by')}, tools {[s['tool'] for s in out['steps']]}")
    return out


def main() -> None:
    db.init()
    db.meta_set("ai_engine", "claude_code")
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.1)
    if not claude_code.usable():
        raise SystemExit("Claude Code isn't signed in")
    pid = db.row("SELECT id FROM profiles ORDER BY id LIMIT 1")["id"]
    store.scored_jobs(pid)
    print("chat model:", claude_code.model_for("chat"))
    print("no process ready (the very first message):")
    ask(pid, "Say hello in five words.")
    time.sleep(4)
    print("process ready (every message after the chat is opened):")
    ask(pid, "Say good morning in five words.")
    time.sleep(4)
    ask(pid, "Find me one software internship in Toronto.")
    time.sleep(4)
    print("no AI at all:")
    ask(pid, "how's my neetcode?")
    ask(pid, "what's ready to apply?")
    print("preparing, as you'd ask for it:")
    done = {r["job_id"] for r in db.rows("SELECT job_id FROM ready_queue WHERE profile_id=?", (pid,))}
    fresh = [j for j in store.scored_jobs(pid) if j["id"] not in done and j["eligibility"] != "no" and not j.get("app_status")][:4]
    t = time.time()
    agent.call(pid, "queue_application", {"job_id": fresh[0]["id"], "why": "bench"}, source="chat", run_id="bench1")
    print(f"  one application on Ready: {time.time() - t:.2f} s")
    time.sleep(4)
    t = time.time()
    ask(pid, f"Prepare these three for me: {', '.join(j['id'] for j in fresh[1:4])}")
    print(f"  three more, asked for in the chat: on Ready in {time.time() - t:.1f} s")
    t = time.time()
    polish.wait(600)
    print(f"  letters and answers finished {time.time() - t:.0f} s after that, in the background")
    for j in fresh:
        r = db.row("SELECT polish FROM ready_queue WHERE profile_id=? AND job_id=?", (pid, j["id"]))
        d = db.row("SELECT generated_by FROM documents WHERE profile_id=? AND kind='cover_letter' AND job_id=?", (pid, j["id"]))
        print(f"    {j['company'][:24]:26} polish={r and r['polish']} letter by {d and d['generated_by']}")
    claude_chat.close_all()
    srv.should_exit = True


if __name__ == "__main__":
    main()
