"""A stand-in for the `codex` CLI, for tests: same flags and --json event shapes as `codex exec`.

* `--version`, `login status`, `plugin ...`, `mcp ...`: canned answers.
* `exec --output-schema S -o OUT`: writes an answer that fits S to OUT.
* `exec -c mcp_servers.coopcoach.*`: starts that MCP server (the real Co-op Coach one) and calls tools like an
  agent would, printing item.started/item.completed events.
Every call is appended to $FAKE_CODEX_LOG.
"""

import asyncio
import json
import os
import re
import sys
import time

import tomllib

sys.path.insert(0, os.path.dirname(__file__))
from fake_claude import fake_value  # noqa: E402


def log(entry: dict) -> None:
    if os.environ.get("FAKE_CODEX_LOG"):
        with open(os.environ["FAKE_CODEX_LOG"], "a") as f:
            f.write(json.dumps(entry) + "\n")


def opt(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def overrides(args: list[str]) -> dict:
    out = {}
    for i, a in enumerate(args):
        if a == "-c" and i + 1 < len(args):
            k, _, v = args[i + 1].partition("=")
            try:
                out[k] = tomllib.loads(f"v = {v}")["v"]
            except tomllib.TOMLDecodeError:
                out[k] = v
    return out


def emit(ev: dict) -> None:
    print(json.dumps(ev), flush=True)


async def agent(srv: dict, prompt: str) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command=srv["command"], args=srv["args"], env={**os.environ, **srv.get("env", {})})
    n = 0
    async with stdio_client(params, errlog=open(os.devnull, "w")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            log({"mcp_tools": names})

            async def use(tool, args):
                nonlocal n
                n += 1
                item = {"id": f"item_{n}", "type": "mcp_tool_call", "server": "coopcoach", "tool": tool,
                        "arguments": args, "status": "in_progress"}
                emit({"type": "item.started", "item": item})
                res = await s.call_tool(tool, args)
                text = "".join(getattr(c, "text", "") for c in res.content)
                emit({"type": "item.completed", "item": {**item, "status": "completed"}})
                return json.loads(text) if text.startswith("{") else {}

            if "Prepare up to" in prompt:
                await use("daily_brief", {"days": 1})
                ids = re.findall(r"^- ([0-9a-f]{16}) ·", prompt, re.M)
                done = []
                for jid in ids[:1]:
                    await use("job_details", {"job_id": jid})
                    done.append((await use("queue_application", {"job_id": jid, "why": "Strong fit"})).get("job", jid))
                return "Ready to apply:\n• " + "\n• ".join(done)
            found = await use("search_jobs", {"location": "Toronto"})
            jid = (found.get("jobs") or [{}])[0].get("job_id")
            if jid:
                await use("star", {"job_id": jid})
            return "Starred your best Toronto match: Shopify."


def main() -> None:
    args = sys.argv[1:]
    leaked = any(k.startswith("OPENAI_API_KEY") for k in os.environ)
    if args[:1] == ["--version"]:
        print("codex-cli 0.155.0-test")
        return
    if args[:2] == ["login", "status"]:
        time.sleep(float(os.environ.get("FAKE_CODEX_SLOW") or 0))
        print("Logged in using ChatGPT" if os.environ.get("FAKE_CODEX_SIGNED_OUT") != "1" else "Not logged in")
        sys.exit(0 if os.environ.get("FAKE_CODEX_SIGNED_OUT") != "1" else 1)
    if args[:1] in (["plugin"], ["mcp"]):
        log({"cmd": args, "leaked_key": leaked})
        print("ok")
        return
    if args[:1] != ["exec"]:
        log({"interactive": args})
        return
    prompt = sys.stdin.read() if args[-1:] == ["-"] else ""
    log({"args": args, "stdin": prompt, "leaked_key": leaked})
    emit({"type": "thread.started", "thread_id": "t1"})
    emit({"type": "turn.started"})
    if os.environ.get("FAKE_CODEX_MODE") == "limit":
        msg = "You've hit your usage limit. Upgrade to Pro or try again at 6:22 PM."
        emit({"type": "error", "message": msg})
        emit({"type": "turn.failed", "error": {"message": msg}})
        sys.exit(1)
    o = overrides(args)
    if "mcp_servers.coopcoach.command" in o:
        srv = {"command": o["mcp_servers.coopcoach.command"], "args": o["mcp_servers.coopcoach.args"],
               "env": o.get("mcp_servers.coopcoach.env", {})}
        reply = asyncio.run(agent(srv, prompt))
        emit({"type": "item.completed", "item": {"id": "item_x", "type": "agent_message", "text": reply}})
    else:
        schema = json.load(open(opt(args, "--output-schema"))) if opt(args, "--output-schema") else {}
        text = json.dumps(fake_value(schema)) if schema else "ok"
        if opt(args, "-o"):
            open(opt(args, "-o"), "w").write(text)
        emit({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})


if __name__ == "__main__":
    main()
