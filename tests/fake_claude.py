"""A stand-in for the `claude` command, for tests: same flags and output shapes as Claude Code's print mode.

* `--version`, `auth status`, `plugin ...`: canned answers ($FAKE_CLAUDE_SLOW seconds of delay on `auth status`).
* `-p --json-schema S`: a result whose structured_output fits S.
* `-p --mcp-config F`: actually starts the MCP server from F (the real Co-op Coach one) and calls tools like an agent
  would, streaming stream-json events.
Every call is appended to $FAKE_CLAUDE_LOG (args, stdin, whether an API key leaked into the environment).
"""

import asyncio
import json
import os
import re
import sys
import time


def log(entry: dict) -> None:
    path = os.environ.get("FAKE_CLAUDE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def fake_value(schema: dict):
    t = schema.get("type")
    if t == "object":
        return {k: fake_value(v) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return []
    if t == "integer":
        return 72
    if t == "boolean":
        return False
    if "enum" in schema:
        return schema["enum"][0]
    return "Fake answer from Claude Code"


def opt(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


async def agent(cfg_path: str, prompt: str) -> tuple[list, str]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    srv = json.load(open(cfg_path))["mcpServers"]["coopcoach"]
    params = StdioServerParameters(command=srv["command"], args=srv["args"], env={**os.environ, **srv.get("env", {})})
    events, calls = [], []
    async with stdio_client(params, errlog=open(os.devnull, "w")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            log({"mcp_tools": names})

            async def use(name, args):
                calls.append(name)
                events.append({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": f"t{len(calls)}", "name": f"mcp__coopcoach__{name}", "input": args}]}})
                res = await s.call_tool(name, args)
                text = "".join(getattr(c, "text", "") for c in res.content)
                events.append({"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"t{len(calls)}", "content": text}]}})
                return json.loads(text) if text.startswith("{") else {}

            if "Prepare up to" in prompt:  # Autopilot
                await use("daily_brief", {"days": 1})
                ids = re.findall(r"^- ([0-9a-f]{16}) ·", prompt, re.M)
                queued = []
                for jid in ids[:1]:
                    await use("job_details", {"job_id": jid})
                    out = await use("queue_application", {"job_id": jid, "why": "Strong Python + Go fit"})
                    queued.append(out.get("job", jid))
                if "star" in names:
                    await use("star", {"company": "Shopify"})  # must not be possible for Autopilot
                reply = "Ready to apply:\n• " + "\n• ".join(queued) + "\n\nNeeds you:\n• nothing today"
            else:  # chat
                found = await use("search_jobs", {"location": "Toronto", "role": "software"})
                jid = (found.get("jobs") or [{}])[0].get("job_id")
                if jid:
                    await use("star", {"job_id": jid})
                reply = "Starred your best Toronto match: Shopify."
    return events, reply


def main() -> None:
    args = sys.argv[1:]
    leaked = any(k.startswith("ANTHROPIC_API_KEY") for k in os.environ) or "CLAUDECODE" in os.environ
    if args[:1] == ["--version"]:
        print("2.1.282 (Claude Code)")
        return
    if args[:2] == ["auth", "status"]:
        time.sleep(float(os.environ.get("FAKE_CLAUDE_SLOW") or 0))  # a slow keychain prompt, a cold start...
        print(json.dumps({"loggedIn": os.environ.get("FAKE_CLAUDE_SIGNED_OUT") != "1", "authMethod": "claude.ai"}))
        return
    if args[:1] == ["plugin"]:
        log({"plugin": args, "leaked_key": leaked})
        print("[]" if args[1:2] == ["list"] else "✔ done")
        return
    streamed = opt(args, "--input-format") == "stream-json"  # a process started ahead: it waits here for the message
    if streamed:
        line = sys.stdin.readline()
        if not line:
            return
        prompt = json.loads(line)["message"]["content"]
    else:
        prompt = sys.stdin.read() if "-p" in args else ""
    if "--system-prompt-file" in args:  # Windows: the system prompt comes in a file; log it like the argument
        i = args.index("--system-prompt-file")
        with open(args[i + 1], encoding="utf-8") as f:
            args = args[:i] + ["--system-prompt", f.read()] + args[i + 2:]
    log({"args": args, "stdin": prompt, "leaked_key": leaked})
    mode = os.environ.get("FAKE_CLAUDE_MODE", "")
    if mode == "limit":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                          "result": "Claude AI usage limit reached|resets 5pm"}))
        return
    if opt(args, "--mcp-config"):
        events, reply = asyncio.run(agent(opt(args, "--mcp-config"), prompt))
        print(json.dumps({"type": "system", "subtype": "init", "mcp_servers": [{"name": "coopcoach", "status": "connected"}]}))
        for e in events:
            print(json.dumps(e))
        if "--include-partial-messages" in args:
            for i in range(0, len(reply), 7):
                print(json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": reply[i:i + 7]}}}), flush=True)
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": reply, "num_turns": 4}), flush=True)
        return
    schema = json.loads(opt(args, "--json-schema") or "{}")
    out = {"type": "result", "subtype": "success", "is_error": False, "result": "ok"}
    if schema:
        out["structured_output"] = fake_value(schema)
    else:
        out["result"] = "Hi from Claude Code."
    print(json.dumps(out))


if __name__ == "__main__":
    main()
