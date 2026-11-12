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
from functools import lru_cache
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


# How long a status answer is reused. "Signed in" rarely changes; "not signed in" goes stale fast, so signing in
# shows up within seconds rather than minutes.
FRESH_SIGNED_IN = 120
FRESH_SIGNED_OUT = 5
SIGNIN_POLL = 1.0    # after opening Terminal on a sign-in: re-check this often...
SIGNIN_WATCH = 180   # ...for up to this long


def fresh(cache: dict) -> bool:
    v = cache["v"]
    return bool(v) and time.time() - cache["t"] < (FRESH_SIGNED_IN if v.get("logged_in") else FRESH_SIGNED_OUT)


def checked(cache: dict, lock: threading.Lock, check) -> dict:
    """Run `check()` and keep its answer, one check at a time: a caller that waited while another check ran takes
    that answer instead of starting its own."""
    asked = time.time()
    with lock:
        if cache["v"] and cache["t"] >= asked:
            return cache["v"]
        started = time.time()  # a check counts from when it started: one already running when you asked isn't fresh
        v = check()
        cache.update(t=started, v=v)
        return v


def in_background(cache: dict, lock: threading.Lock, refresh) -> None:
    """For /api/state, which must never wait on a subprocess: refresh a stale answer on a thread."""
    if (not cache["v"] or time.time() - cache["t"] > FRESH_SIGNED_IN) and not lock.locked():
        threading.Thread(target=refresh, kwargs={"refresh": True}, daemon=True).start()


_watch_lock = threading.Lock()
_watch_until: dict[str, float] = {}


def watch_sign_in(name: str, check) -> None:
    """Sign-in happens in Terminal and the browser, out of the app's sight: re-check every couple of seconds until
    it's done (or a few minutes pass), so the cached status flips to signed in as soon as it is."""
    with _watch_lock:
        running = _watch_until.get(name, 0) > 0
        _watch_until[name] = time.time() + SIGNIN_WATCH
        if running:
            return

    def run():
        while True:
            time.sleep(SIGNIN_POLL)
            try:
                done = check(refresh=True).get("logged_in")
            except Exception as e:
                log.info("%s sign-in check failed: %s", name, e)
                done = False
            with _watch_lock:
                if done or time.time() > _watch_until.get(name, 0):
                    _watch_until[name] = 0
                    return
    threading.Thread(target=run, daemon=True).start()


def _check() -> dict:
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
    return v


_checking = threading.Lock()


def status(refresh: bool = False) -> dict:
    """Is Claude Code installed and signed in? Two quick subprocess calls; signed in is reused for two minutes, not
    signed in for a few seconds."""
    if not refresh and fresh(_status):
        return _status["v"]
    return checked(_status, _checking, _check)


def status_cached() -> dict:
    """Never blocks: the last known status (an explicit check updates it at once), refreshed in the background
    when it's old (for /api/state)."""
    in_background(_status, _checking, status)
    return _status["v"] or {"installed": bool(binary()), "logged_in": False, "version": "", "checking": True}


def usable(wait: bool = True) -> bool:
    """`wait=False` answers from the last check without running one (for requests that must stay quick)."""
    s = status() if wait else status_cached()
    return bool(s.get("installed") and s.get("logged_in")) and not paused()


@lru_cache(maxsize=1)
def _model_list() -> dict:
    return config.read_yaml((config.PKG / "ai_models.yaml").read_text(encoding="utf-8")) or {}


def model_for(job: str) -> str:
    """Which model does this job: ai_models.yaml, unless Settings say otherwise."""
    jobs = _model_list().get("jobs") or {}
    job = {"fast": "scoring", "main": "default"}.get(job, job)
    return str(config.get(f"ai.models.{job}", "") or jobs.get(job) or jobs.get("default") or "")


def _model(job: str) -> list[str]:
    m = model_for(job)
    return ["--model", m] if m else []


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


_WINDOWS = os.name == "nt"


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _system_args(text: str) -> tuple[list[str], Path | None]:
    """The system prompt as command-line arguments. On Windows, Claude Code is often a .cmd launcher (npm's
    claude.cmd), and a .cmd file cuts an argument at its first line break, which would also drop every argument
    after it (the tool limits among them). There it goes in a private file instead."""
    if not _WINDOWS:
        return ["--system-prompt", text], None
    f = _workdir() / f"system-{uuid.uuid4().hex[:10]}.txt"
    _write_private(f, text)
    return ["--system-prompt-file", str(f)], f


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
              timeout: int = 300, web: bool = False, job: str = "") -> dict:
    """One structured answer, validated against `schema`. Contact details are redacted and put back. `web` lets it
    search and read the web first (and nothing else)."""
    red = Redactor()
    lean = list(_LEAN)
    if web:
        lean[lean.index("--tools") + 1] = "WebSearch,WebFetch"
        lean += ["--allowedTools", "WebSearch", "WebFetch", "--permission-mode", "dontAsk"]
    sys_args, sys_file = _system_args(red.redact(system))
    args = [binary() or "claude", "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
            *sys_args, "--effort", {"max": "high", "xhigh": "high"}.get(effort, effort),
            *lean, *_model(job or ("fast" if fast else "main"))]
    try:
        d = _run(args, red.redact(text), timeout)
    finally:
        if sys_file:
            sys_file.unlink(missing_ok=True)
    out = d.get("structured_output")
    if out is None:
        out = _parse_json_text(d.get("result") or "")
    return red.restore(out)


def chat(system: str, history: list[dict], timeout: int = 180) -> str:
    red = Redactor()
    convo = "\n\n".join(f"{'Student' if m['role'] == 'user' else 'You'}: {m['content']}" for m in history[-10:])
    sys_args, sys_file = _system_args(red.redact(system))
    args = [binary() or "claude", "-p", "--output-format", "json", *sys_args, "--effort", "low", *_LEAN,
            *_model("main")]
    try:
        d = _run(args, red.redact(convo + "\n\nReply to the student's last message."), timeout)
    finally:
        if sys_file:
            sys_file.unlink(missing_ok=True)
    return red.restore(d.get("result") or "").strip()


# --- agent runs: Claude Code + Co-op Coach's tools ---------------------------------------------------------

def mcp_config(pid: int, ticket: str, run_id: str = "", source: str = "claude_code", tools: list[str] | None = None,
               ticket_ref: str = "") -> dict:
    """`ticket_ref` points at a ticket kept in the Keychain (long-lived Terminal sessions); otherwise the short-lived
    ticket rides in the config, which only exists for the length of the run."""
    env = {"COOPCOACH_DATA": str(config.DATA_DIR), "COOPCOACH_SOURCE": source, "COOPCOACH_RUN_ID": run_id,
           "PYTHONPATH": str(config.ROOT)}
    port = os.environ.get("COOPCOACH_PORT")  # set while the app is serving
    if port and not ticket_ref:  # a run while the app is open: the app answers the tools (everything's loaded)
        from . import agent, auth, mcp_server
        names = [n for n in agent.TOOLS if not tools or n in tools]
        auth.allow_tools(ticket, names)
        spec = _workdir() / f"tools-{run_id or uuid.uuid4().hex[:12]}.json"
        _write_private(spec, json.dumps({"instructions": mcp_server.INSTRUCTIONS, "tools": [
            {"name": n, "description": agent.TOOLS[n][1], "schema": agent.TOOLS[n][2], "read_only": n not in agent.WRITES}
            for n in names]}))
        env.update(COOPCOACH_TICKET=ticket, COOPCOACH_TOOLS_FILE=str(spec), COOPCOACH_API=f"http://127.0.0.1:{port}")
        return {"mcpServers": {SERVER: {"type": "stdio", "command": sys.executable, "args": ["-m", "coopcoach.mcp_proxy"],
                                        "env": env}}}
    env.update({"COOPCOACH_TICKET_REF": ticket_ref} if ticket_ref else {"COOPCOACH_TICKET": ticket})
    if tools:
        env["COOPCOACH_TOOLS"] = ",".join(tools)
    return {"mcpServers": {SERVER: {"type": "stdio", "command": sys.executable,
                                    "args": ["-m", "coopcoach", "mcp", "--profile", str(pid)], "env": env}}}




def run_agent(pid: int, prompt: str, system: str, source: str = "claude_code", tools: list[str] | None = None,
              timeout: int = 600, on_step=None, model: str = "main") -> dict:
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
    sys_args, sys_file = _system_args(red.redact(system))
    args = [binary(), "-p", "--output-format", "stream-json", "--verbose", "--mcp-config", str(cfg),
            "--strict-mcp-config", "--tools", "", "--allowedTools", f"mcp__{SERVER}", "--permission-mode", "dontAsk",
            "--setting-sources", "project", "--disable-slash-commands", "--no-session-persistence",
            *sys_args, *_model(model)]
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
        (_workdir() / f"tools-{run_id}.json").unlink(missing_ok=True)
        if sys_file:
            sys_file.unlink(missing_ok=True)
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
    _osascript(script)
    _status["t"] = 0
    watch_sign_in("claude", status)


def _osascript(script: str) -> None:
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=20)
