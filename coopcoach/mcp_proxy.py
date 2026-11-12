"""The tools for a chat or Autopilot run on your Claude Code, answered by the Co-op Coach that's already running.

Claude Code starts its tool server afresh for every message. The full server (mcp_server.py) would load the app and
every posting from the database each time (about ten seconds before the first tool can answer); this one only knows
the tools' names and arguments (from a file the app writes for the run) and hands each call to the running app over
localhost, where everything is already loaded. The run's ticket says which profile it is and which tools it may use.

    COOPCOACH_TOOLS_FILE=... COOPCOACH_API=http://127.0.0.1:8765 COOPCOACH_TICKET=... python -m coopcoach.mcp_proxy
"""

import inspect
import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools import Tool
from mcp.types import ToolAnnotations

_TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


def annotation(prop: dict):
    """The Python type for a JSON-schema property (Any for a union like ["object", "string", "null"])."""
    t = prop.get("type")
    return _TYPES.get(t, Any) if isinstance(t, str) else Any


def make_tool(name: str, desc: str, schema: dict, call, read_only: bool) -> Tool:
    """One tool over MCP with the same JSON schema (and argument descriptions) the in-app chat gets, not one read
    from a Python signature. An optional argument that isn't given arrives as None and is left out, so the tool's own
    default applies."""
    required = set(schema.get("required") or [])
    params = [inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, annotation=annotation(p),
                                default=inspect.Parameter.empty if k in required else None)
              for k, p in schema["properties"].items()]

    def run(**kwargs):
        return call(name, {k: v for k, v in kwargs.items() if v is not None})

    run.__name__ = name
    run.__signature__ = inspect.Signature(params, return_annotation=dict)
    run.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict}
    tool = Tool.from_function(run, name=name, description=desc, annotations=ToolAnnotations(
        readOnlyHint=read_only, destructiveHint=False, openWorldHint=name == "fill_application"))
    tool.parameters = {**schema, "properties": dict(schema["properties"])}  # what Claude sees: the chat's schema
    return tool


def main() -> None:
    spec = json.loads(open(os.environ["COOPCOACH_TOOLS_FILE"], encoding="utf-8").read())
    api, ticket = os.environ["COOPCOACH_API"].rstrip("/"), os.environ["COOPCOACH_TICKET"]
    client = httpx.Client(timeout=600, headers={"Authorization": f"Bearer {ticket}"})
    source, run_id = os.environ.get("COOPCOACH_SOURCE") or "mcp", os.environ.get("COOPCOACH_RUN_ID") or None

    def call(name: str, args: dict) -> dict:
        try:
            r = client.post(f"{api}/api/agent/call", json={"tool": name, "args": args, "source": source,
                                                           "run_id": run_id})
        except httpx.HTTPError as e:
            return {"error": f"Co-op Coach isn't answering: {e}"}
        if r.status_code != 200:
            return {"error": (r.json().get("detail") if r.headers.get("content-type", "").startswith(
                "application/json") else r.text)[:300] or f"HTTP {r.status_code}"}
        return r.json()

    MCPServer(name="Co-op Coach", instructions=spec["instructions"], tools=[
        make_tool(t["name"], t["description"], t["schema"], call, t["read_only"]) for t in spec["tools"]]).run("stdio")


if __name__ == "__main__":
    main()
