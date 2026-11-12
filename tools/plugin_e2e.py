"""Run the plugin's real commands in real Claude Code, headless, on a copy of the fictional demo student.

    .venv/bin/python -m tools.plugin_e2e [--model haiku] [--only today,neetcode] [--out report.json]

Nothing real is reachable: Claude Code gets only this copy's server (the installed plugin is switched off for the run), and the
only tools it may call are this server's, minus anything that opens a browser or could send something. Uses your Claude account,
so it's run by hand, not in the test suites.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEVER = {"fill_application", "apply_session_next", "connect_outlook"}
CASES = [  # (the command, tools it should call, words the answer should have)
    ("/coop-coach:today", {"daily_brief", "my_path"}, ["NeetCode"]),
    ("/coop-coach:doctor", {"coop_status"}, []),
    ("/coop-coach:neetcode", {"coding_progress"}, ["/150"]),
    ("/coop-coach:neetcode I solved Two Sum in 12 minutes, the trick was a hash map", {"record_problem"}, ["XP"]),
    ("/coop-coach:neetcode I want a test on Arrays & Hashing, explain it but don't start it yet", set(), ["clock"]),
    ("/coop-coach:neetcode Start the Two Pointers test now, go.", {"start_path_test"}, ["leetcode.com/problems"]),
    ("/coop-coach:neetcode My LeetCode username is no_such_user_zz91, link it", {"link_leetcode"}, []),
    ("/coop-coach:hunt software internships in Toronto", {"search_jobs"}, []),
    ("/coop-coach:tracks", {"list_tracks"}, []),
    ("/coop-coach:followups", set(), []),
    ("/coop-coach:events", {"find_events"}, []),
    ("/coop-coach:autopilot what would you do with my matches? Don't change anything.", set(), []),
    ("/coop-coach:interview-prep Shopify", set(), []),
    ("/coop-coach:resume how's my resume?", set(), []),
    ("/coop-coach:apply", {"ready_queue"}, []),
    ("/coop-coach:track-email Subject: Interview at Robinhood. Hi Alex, we'd like to invite you to interview for the Data Science Intern role next Tuesday at 2 PM ET.", {"log_email"}, []),
    ("/coop-coach:hackathon Hack the Valley", set(), []),
]


def scratch_copy() -> tuple[Path, Path]:
    d = Path(tempfile.mkdtemp(prefix="coopcoach-e2e-"))
    src = ROOT / "data-demo" / "coopcoach.db"
    if not src.exists():
        raise SystemExit("No demo yet: run `python -m coopcoach demo` once first.")
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as a, sqlite3.connect(d / "coopcoach.db") as b:
        a.backup(b)
    cfg = d / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"coop-coach": {
        "type": "stdio", "command": sys.executable, "args": ["-m", "coopcoach", "mcp"],
        "env": {"COOPCOACH_DATA": str(d), "COOPCOACH_DEMO": "1", "PYTHONPATH": str(ROOT), "COOPCOACH_SOURCE": "claude_code",
                "GROQ_API_KEY": "", "ANTHROPIC_API_KEY": ""}}}}))
    return d, cfg


def run(prompt: str, cfg: Path, cwd: Path, model: str) -> dict:
    sys.path.insert(0, str(ROOT))
    from coopcoach import agent
    allowed = [f"mcp__coop-coach__{t}" for t in agent.TOOLS if t not in NEVER]
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose", "--max-turns", "10",
           "--plugin-dir", str(ROOT / "claude-plugin"), "--mcp-config", str(cfg), "--strict-mcp-config",
           "--settings", json.dumps({"enabledPlugins": {"coop-coach@coop-coach-local": False}}),
           "--allowedTools", ",".join(allowed + ["Skill"])]
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=420, stdin=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if not k.startswith("COOPCOACH")})
        out = p.stdout
    except subprocess.TimeoutExpired as e:
        return {"error": "timed out", "stderr": str(e)[:200], "seconds": 420, "tools": [], "other_tools": [], "answer": "", "tool_errors": [], "denied": []}
    tools, other, answer, denied, errors, cost = [], [], "", [], [], None
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "assistant":
            for b in e["message"].get("content", []):
                if b.get("type") == "tool_use":
                    n = b["name"]
                    (tools if n.startswith("mcp__coop-coach__") else other).append(n.removeprefix("mcp__coop-coach__"))
        elif e.get("type") == "user":
            for b in (e.get("message") or {}).get("content", []) if isinstance((e.get("message") or {}).get("content"), list) else []:
                if b.get("type") == "tool_result" and b.get("is_error"):
                    errors.append(str(b.get("content"))[:200])
                elif b.get("type") == "tool_result" and '"error"' in str(b.get("content"))[:400]:
                    errors.append(str(b.get("content"))[:200])
        elif e.get("type") == "result":
            answer, cost = e.get("result") or "", e.get("total_cost_usd")
            denied = [d.get("tool_name") for d in e.get("permission_denials") or []]
    return {"seconds": round(time.time() - t), "tools": tools, "other_tools": other, "answer": answer, "denied": denied, "tool_errors": errors,
            "cost": cost, "stderr": (p.stderr or "")[:300]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    d, cfg = scratch_copy()
    report = []
    only = [x for x in args.only.split(",") if x]
    for prompt, want, words in CASES:
        if only and not any(o in prompt for o in only):
            continue
        r = run(prompt, cfg, d, args.model)
        missing = sorted(want - set(r["tools"]))
        lost = [w for w in words if w.lower() not in r["answer"].lower()]
        r.update(prompt=prompt, ok=not missing and not lost and not r.get("error") and bool(r["answer"]), missing=missing, lost=lost)
        report.append(r)
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {prompt[:60]:62} {r['seconds']:4}s tools={','.join(r['tools'])[:80]} other={','.join(r['other_tools'])[:40]}"
              f"{' denied=' + str(r['denied'])[:80] if r['denied'] else ''}{' errors=' + str(r['tool_errors'])[:160] if r['tool_errors'] else ''}{' cost=' + format(r['cost'], '.3f') if r['cost'] else ''}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
