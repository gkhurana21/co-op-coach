"""Your own Claude Code as Co-op Coach's engine: your Claude plan does the thinking, no API key needed.

When the `claude` command is installed and signed in, Co-op Coach can hand it everything AI: the chat agent,
Autopilot, resume reviews, cover letters, application answers and match scoring.

* JSON tasks run `claude -p` with no tools at all (--tools ""): just the prompt and a JSON schema.
* Agent runs (chat, Autopilot) get exactly one MCP server, Co-op Coach's own tools. No shell, no files, no web.
  That server opens with a short-lived ticket (never the passcode), and everything it changes goes in the
  Activity log with Undo.
* Contact details are redacted before anything is sent, same as for Groq.
* "Apply with my Claude Code" opens Claude Code in Terminal so you watch it fill the form and approve each step.

Nothing here can submit an application.
"""

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import auth, config
from .privacy import Redactor

log = logging.getLogger(__name__)
SERVER = "coopcoach"                      # MCP server name, so tools show up as mcp__coopcoach__<tool>
_CANDIDATES = ["~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude", "~/.claude/local/claude"]
_LIMIT_RE = re.compile(r"usage limit|rate limit|limit reached|limit will reset|resets? at|overloaded|quota", re.I)
_status: dict = {"t": 0.0, "v": None}
_paused_until = 0.0


class Unavailable(RuntimeError):
    pass


def binary() -> str | None:
    forced = os.environ.get("COOPCOACH_CLAUDE_BIN")
    if forced:
        return forced if os.path.exists(forced) else None
    found = shutil.which("claude")
    if found:
        return found
    for c in _CANDIDATES:
        p = Path(c).expanduser()
        if p.exists():
            return str(p)
    return None


def _workdir() -> Path:
    """An empty folder to run in, so Claude Code picks up no project files, CLAUDE.md or settings."""
    d = config.DATA_DIR / "claude-work"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def _env() -> dict:
    """A clean environment: no Anthropic API key (so it runs on your Claude plan rather than billing a key) and
    nothing inherited from a Claude session that may have started this app."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC_"))}
    b = binary()
    if b:
        env["PATH"] = os.path.dirname(b) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    return env


def paused() -> float:
    return max(0.0, _paused_until - time.time())


def _check_limit(msg: str) -> None:
    global _paused_until
    if _LIMIT_RE.search(msg or ""):
        _paused_until = time.time() + float(config.get("ai.claude_code_pause_minutes", 30)) * 60
        _status["t"] = 0


def status(refresh: bool = False) -> dict:
    """Is Claude Code installed and signed in? Cached for two minutes (it's two quick subprocess calls)."""
    if not refresh and _status["v"] and time.time() - _status["t"] < 120:
        return _status["v"]
    b = binary()
    v = {"installed": bool(b), "logged_in": False, "version": "", "method": "", "path": b or ""}
    if b:
        try:
            out = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=20, env=_env(),
                                 cwd=_workdir()).stdout
            v["version"] = (out.strip().splitlines() or [""])[0].replace(" (Claude Code)", "")
            raw = subprocess.run([b, "auth", "status"], capture_output=True, text=True, timeout=20, env=_env(),
                                 cwd=_workdir()).stdout
            a = json.loads(raw[raw.find("{"):]) if "{" in raw else {}
            v["logged_in"] = bool(a.get("loggedIn"))
            v["method"] = a.get("authMethod") or ""
            v["plan"] = a.get("subscriptionType") or ""
        except Exception as e:  # an old version without `auth status`, a hung keychain prompt...
            log.info("claude status check failed: %s", e)
    v["paused_minutes"] = int(paused() / 60) + 1 if paused() else 0
    _status.update(t=time.time(), v=v)
    return v


def status_cached() -> dict:
    """Never blocks: the last known status, refreshed in the background when it's stale (for /api/state)."""
    v = _status["v"]
    if not v or time.time() - _status["t"] > 120:
        if not _refreshing.locked():
            def refresh():
                with _refreshing:
                    status(refresh=True)
            threading.Thread(target=refresh, daemon=True).start()
    return v or {"installed": bool(binary()), "logged_in": False, "version": "", "checking": True}


_refreshing = threading.Lock()


def usable() -> bool:
    s = status()
    return s["installed"] and s["logged_in"] and not paused()


def _model(kind: str) -> list[str]:
    m = config.get("ai.claude_code_fast_model" if kind == "fast" else "ai.claude_code_model", "sonnet" if kind == "fast" else "")
    return ["--model", str(m)] if m else []


_LEAN = ["--tools", "", "--strict-mcp-config", "--setting-sources", "project", "--disable-slash-commands",
         "--no-session-persistence"]


def _result(stdout: str) -> dict:
    """The final `result` message from --output-format json or stream-json."""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "result":
            return d
    try:
        d = json.loads(stdout)
        if isinstance(d, dict):
            return d
    except ValueError:
        pass
    raise RuntimeError("Claude Code returned no result")


def _raise_if_error(d: dict, stderr: str = "") -> None:
    if d.get("is_error") or d.get("subtype", "success") != "success":
        msg = str(d.get("result") or d.get("subtype") or stderr or "unknown error")
        _check_limit(msg)
        if re.search(r"authenticat|log ?in|oauth|credential", msg, re.I):
            _status["t"] = 0
            raise Unavailable("Claude Code isn't signed in. Run `claude auth login` in Terminal.")
        raise RuntimeError(f"Claude Code: {msg[:240]}")


def _run(args: list[str], prompt: str, timeout: int) -> dict:
    if not binary():
        raise Unavailable("Claude Code isn't installed")
    if paused():
        raise Unavailable(f"Claude Code hit its usage limit; back in {int(paused() / 60) + 1} min")
    try:
        p = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, env=_env(),
                           cwd=_workdir())
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Claude Code took longer than {timeout}s")
    if not p.stdout.strip():
        _check_limit(p.stderr)
        raise RuntimeError(f"Claude Code failed: {(p.stderr or 'no output').strip()[-240:]}")
    d = _result(p.stdout)
    _raise_if_error(d, p.stderr)
    return d


def _parse_json_text(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


def call_json(system: str, text: str, schema: dict, effort: str = "medium", fast: bool = False,
              timeout: int = 300, web: bool = False) -> dict:
    """One structured answer, validated against `schema`. Contact details are redacted and put back. `web` lets it
    search and read the web first (and nothing else)."""
    red = Redactor()
    lean = list(_LEAN)
    if web:
        lean[lean.index("--tools") + 1] = "WebSearch,WebFetch"
        lean += ["--allowedTools", "WebSearch", "WebFetch", "--permission-mode", "dontAsk"]
    args = [binary() or "claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
            "--system-prompt", red.redact(system), "--effort", {"max": "high", "xhigh": "high"}.get(effort, effort),
            *lean, *_model("fast" if fast else "main")]
    d = _run(args, red.redact(text), timeout)
    out = d.get("structured_output")
    if out is None:
        out = _parse_json_text(d.get("result") or "")
    return red.restore(out)


def chat(system: str, history: list[dict], timeout: int = 180) -> str:
    red = Redactor()
    convo = "\n\n".join(f"{'Student' if m['role'] == 'user' else 'You'}: {m['content']}" for m in history[-10:])
    args = [binary() or "claude", "-p", "--output-format", "json", "--system-prompt", red.redact(system),
            "--effort", "low", *_LEAN, *_model("main")]
    d = _run(args, red.redact(convo + "\n\nReply to the student's last message."), timeout)
    return red.restore(d.get("result") or "").strip()


# --- agent runs: Claude Code + Co-op Coach's tools ---------------------------------------------------------

def mcp_config(pid: int, ticket: str, run_id: str = "", source: str = "claude_code", tools: list[str] | None = None,
               ticket_ref: str = "") -> dict:
    """`ticket_ref` points at a ticket kept in the Keychain (long-lived Terminal sessions); otherwise the short-lived
    ticket rides in the config, which only exists for the length of the run."""
    env = {"COOPCOACH_DATA": str(config.DATA_DIR), "COOPCOACH_SOURCE": source, "COOPCOACH_RUN_ID": run_id,
           "PYTHONPATH": str(config.ROOT)}
    env.update({"COOPCOACH_TICKET_REF": ticket_ref} if ticket_ref else {"COOPCOACH_TICKET": ticket})
    if tools:
        env["COOPCOACH_TOOLS"] = ",".join(tools)
    return {"mcpServers": {SERVER: {"type": "stdio", "command": sys.executable,
                                    "args": ["-m", "coopcoach", "mcp", "--profile", str(pid)], "env": env}}}


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def run_agent(pid: int, prompt: str, system: str, source: str = "claude_code", tools: list[str] | None = None,
              timeout: int = 600, on_step=None) -> dict:
    """Claude Code works through Co-op Coach's tools until it's done. Returns its reply and the run id (the
    Activity log has what it changed)."""
    if not usable():
        s = status()
        raise Unavailable("Claude Code isn't installed" if not s["installed"] else
                          "Claude Code isn't signed in. Run `claude auth login` in Terminal." if not s["logged_in"] else
                          f"Claude Code hit its usage limit; back in {s['paused_minutes']} min")
    run_id = uuid.uuid4().hex[:12]
    ticket = auth.issue_ticket(pid, ttl=timeout + 300, label=source)
    cfg = _workdir() / f"mcp-{run_id}.json"
    _write_private(cfg, json.dumps(mcp_config(pid, ticket, run_id, source, tools)))
    red = Redactor()
    args = [binary(), "-p", "--output-format", "stream-json", "--verbose", "--mcp-config", str(cfg),
            "--strict-mcp-config", "--tools", "", "--allowedTools", f"mcp__{SERVER}", "--permission-mode", "dontAsk",
            "--setting-sources", "project", "--disable-slash-commands", "--no-session-persistence",
            "--system-prompt", red.redact(system), *_model("main")]
    steps, final, err = [], None, []
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=_env(), cwd=_workdir())
        killer = threading.Timer(timeout, proc.kill)
        killer.start()
        threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True).start()
        proc.stdin.write(red.redact(prompt))
        proc.stdin.close()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        step = {"tool": b.get("name", "").split("__")[-1], "args": b.get("input") or {}}
                        steps.append(step)
                        if on_step:
                            on_step(step)
            elif ev.get("type") == "result":
                final = ev
        proc.wait()
        killer.cancel()
    finally:
        cfg.unlink(missing_ok=True)
        auth.revoke_ticket(ticket)
    if not final:
        tail = "".join(err).strip()[-300:]
        _check_limit(tail)
        raise RuntimeError(f"Claude Code stopped without an answer{': ' + tail if tail else ''}")
    _raise_if_error(final)
    return {"reply": red.restore(final.get("result") or "").strip(), "run_id": run_id, "tools_used": steps,
            "turns": final.get("num_turns")}


# --- interactive: Claude Code in Terminal, you watching -----------------------------------------------------

def open_in_terminal(pid: int, prompt: str, rules: str, chrome: bool = True, label: str = "terminal") -> dict:
    """Start an interactive Claude Code session in a new Terminal window with Co-op Coach's tools loaded. You see
    every step and approve it there. The ticket lasts a few hours; the config file is removed on the next run."""
    b = binary()
    if not b:
        raise Unavailable("Claude Code isn't installed")
    if sys.platform != "darwin":
        raise Unavailable("Opening Terminal is Mac-only for now: run the command shown instead")
    wd = _workdir()
    for old in wd.glob("term-*"):  # tidy up earlier sessions' files
        if time.time() - old.stat().st_mtime > 6 * 3600:
            old.unlink(missing_ok=True)
    run_id = uuid.uuid4().hex[:12]
    hours = int(config.get("ai.terminal_ticket_hours", 4))
    ticket = auth.issue_ticket(pid, ttl=hours * 3600, label=label)
    ref = ""
    from . import db, vault
    if vault.available():  # a ticket that lives for hours belongs in the Keychain, not in a file
        refs = [r for r in db.loads(db.meta_get("terminal_ticket_refs", "[]"), []) if r["until"] > time.time()
                or vault.delete(f"claude-run:{r['ref']}")]
        ref = run_id
        vault.put(f"claude-run:{ref}", ticket)
        db.meta_set("terminal_ticket_refs", json.dumps(refs + [{"ref": ref, "until": time.time() + hours * 3600}]))
    cfg, pfile, rfile = wd / f"term-{run_id}-mcp.json", wd / f"term-{run_id}-prompt.txt", wd / f"term-{run_id}-rules.txt"
    _write_private(cfg, json.dumps(mcp_config(pid, ticket, run_id, "claude_code", ticket_ref=ref)))
    _write_private(pfile, prompt)
    _write_private(rfile, rules)
    cmd = (f"cd {shlex.quote(str(wd))} && {shlex.quote(b)}" + (" --chrome" if chrome else "")
           + f" --mcp-config {shlex.quote(str(cfg))} --allowedTools mcp__{SERVER}"
           + f' --append-system-prompt "$(cat {shlex.quote(str(rfile))})" "$(cat {shlex.quote(str(pfile))})"')
    script = f'tell application "Terminal"\nactivate\ndo script {json.dumps(cmd)}\nend tell'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=20)
    return {"opened": True, "run_id": run_id}


def sign_in_in_terminal() -> None:
    """Open Terminal on `claude auth login`: the person signs in to their own account in their browser."""
    b = binary()
    if not b or sys.platform != "darwin":
        raise Unavailable("Run `claude auth login` in a terminal")
    script = f'tell application "Terminal"\nactivate\ndo script {json.dumps(shlex.quote(b) + " auth login")}\nend tell'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=20)
    _status["t"] = 0
