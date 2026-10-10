"""Co-op Coach as an MCP server, so your own Claude (Desktop or Code) can use the same tools as the in-app agent.

    .venv/bin/python -m coopcoach mcp --profile "your name"

Runs over stdio on your machine. How it knows it's allowed to open a profile, in order:
  1. A ticket from the app itself: COOPCOACH_TICKET for a run (Autopilot, chat on your Claude Code), or
     COOPCOACH_TICKET_REF naming one kept in the Keychain ("Apply with my Claude Code" in Terminal, a few hours).
     The app never hands out the passcode.
  2. A connection made with "Connect my Claude Code" in the app: its token lives in the Keychain.
  3. COOPCOACH_PASSCODE for a profile that has one (profiles without a passcode just open).
COOPCOACH_TOOLS limits the tools (Autopilot gets read tools plus queue_application). Like the in-app agent, it
never submits applications.
"""

import inspect
import os
import sys

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import agent, auth, db, vault

INSTRUCTIONS = """Co-op Coach is the student's internship/co-op finder and application assistant. Start with
daily_brief to see what's new and what needs doing. search_jobs finds ranked matches (already scored for this student,
including who each posting is open to); job_details before advising on one. queue_application prepares an application
(answers + tailored resume + letter) into the student's Ready to apply list; application_files gives a browser agent
every answer plus the resume/letter as local files to upload; fill_application opens and fills the form in Chrome on
their screen. track/star keep their tracker current. Never submit an application yourself: fill forms, then stop for
the student to review and press Submit. Never solve CAPTCHAs, sign in or create accounts for them, or answer
voluntary demographic questions. Never invent experience, numbers or facts."""

CONNECT_KEY = "claude-connect"


def _tool(pid: int, name: str, fn, source: str, run_id: str | None):
    params = [p for p in inspect.signature(fn).parameters.values() if p.name != "pid"]

    def run(**kwargs):
        out = agent.call(pid, name, kwargs, source=source, run_id=run_id)
        out.pop("_card", None)
        out.pop("_undo", None)
        return out

    run.__name__ = name
    run.__signature__ = inspect.Signature(params, return_annotation=dict)
    run.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict}
    return run


def build(pid: int, tools: list[str] | None = None, source: str = "mcp", run_id: str | None = None) -> MCPServer:
    srv = MCPServer(name="Co-op Coach", instructions=INSTRUCTIONS)
    for name, (fn, desc, _) in agent.TOOLS.items():
        if tools and name not in tools:
            continue
        srv.add_tool(_tool(pid, name, fn, source, run_id), name=name, description=desc,
                     annotations=ToolAnnotations(readOnlyHint=name not in agent.WRITES, destructiveHint=False,
                                                 openWorldHint=name == "fill_application"))
    return srv


def _find(profile: str | None) -> dict | None:
    if not profile:
        return None
    return db.row("SELECT id, name FROM profiles WHERE lower(name)=lower(?) OR CAST(id AS TEXT)=?", (profile, profile))


def resolve(profile: str | None) -> int:
    """Which profile this server may open. Raises SystemExit with a reason it can't."""
    p = _find(profile)
    if profile and not p:
        names = ", ".join(r["name"] for r in db.rows("SELECT name FROM profiles"))
        raise SystemExit(f"No Co-op Coach profile named {profile!r}. Profiles: {names or 'none yet'}")
    ticket = os.environ.get("COOPCOACH_TICKET")
    ref = os.environ.get("COOPCOACH_TICKET_REF")
    if ref and not ticket:
        ticket = (vault.get(f"claude-run:{ref}") if vault.available() else None) or "expired"
    if ticket:
        tid = auth.check_ticket(ticket)
        if tid and (not p or p["id"] == tid):
            return tid
        raise SystemExit("This Co-op Coach ticket has expired. Start again from the app.")
    token = None
    if vault.available():
        token = vault.get(f"{CONNECT_KEY}:{p['id']}") if p else vault.get(CONNECT_KEY)
    cid = auth.check_ticket(token) if token else None
    if cid and (not p or p["id"] == cid):
        return cid
    if not p:
        raise SystemExit("Connect Claude Code from the app first (Autopilot tab → Connect my Claude Code), "
                         "or pass --profile.")
    if auth.has_passcode(p["id"]) and not auth.verify(p["id"], os.environ.get("COOPCOACH_PASSCODE", "")):
        raise SystemExit(f"{p['name']} is locked: connect from the app (Autopilot tab) or set COOPCOACH_PASSCODE.")
    return p["id"]


def main(profile: str | None) -> None:
    db.init()
    try:
        pid = resolve(profile)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)
    tools = [t for t in (os.environ.get("COOPCOACH_TOOLS") or "").split(",") if t] or None
    agent.SHORT_LIVED = True  # this process ends with the chat: long work (prep sheets) is left to the app
    build(pid, tools, source=os.environ.get("COOPCOACH_SOURCE") or "mcp",
          run_id=os.environ.get("COOPCOACH_RUN_ID") or None).run("stdio")
