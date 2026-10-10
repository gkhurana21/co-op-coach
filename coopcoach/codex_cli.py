"""Your ChatGPT as Co-op Coach's engine, through the Codex CLI that ships with the ChatGPT app (GPT-6 Astra by
default), working the same way as claude_code.py:

* JSON tasks: `codex exec` with no shell (shell_tool and unified_exec disabled, read-only sandbox, your config
  ignored), only the prompt and a JSON schema. Contact details are redacted and put back.
* Agent runs (chat, Autopilot): the same, plus exactly one MCP server, Co-op Coach's own tools, opened with a
  short-lived ticket. Everything it changes goes in the Activity log with Undo.
* "Apply with ChatGPT": interactive Codex in Terminal with your own config (so its Chrome / browser plugin works),
  Co-op Coach's tools, and the rule to stop before Submit. You watch and approve.
* "Open in Atlas": the posting opens in the ChatGPT app's browser with the fill instructions on your clipboard.

It uses your ChatGPT plan (not an API key) and pauses itself when that plan's usage limit is hit.
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
from datetime import datetime, timedelta
from pathlib import Path

from . import auth, config
from .claude_code import Unavailable, _write_private, mcp_config
from .privacy import Redactor

log = logging.getLogger(__name__)
SERVER = "coopcoach"
APP_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex"
_LIMIT_RE = re.compile(r"usage limit|rate limit|limit reached|try again at|quota", re.I)
_status: dict = {"t": 0.0, "v": None}
_paused_until = 0.0
_refreshing = threading.Lock()


def binary() -> str | None:
    forced = os.environ.get("COOPCOACH_CODEX_BIN")
    if forced:
        return forced if os.path.exists(forced) else None
    return shutil.which("codex") or (APP_BIN if os.path.exists(APP_BIN) else None)


def _workdir() -> Path:
    d = config.DATA_DIR / "codex-work"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def _env() -> dict:
    """Your ChatGPT plan, not an API key; nothing inherited from an agent session that started the app."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "CLAUDE", "ANTHROPIC_"))}


def paused() -> float:
    return max(0.0, _paused_until - time.time())


def _check_limit(msg: str) -> None:
    """'You've hit your usage limit … try again at 6:22 PM': pause until then (or for a while)."""
    global _paused_until
    if not _LIMIT_RE.search(msg or ""):
        return
    until = time.time() + float(config.get("ai.codex_pause_minutes", 30)) * 60
    m = re.search(r"try again at (\d{1,2}):(\d{2})\s*([AP]M)?", msg or "", re.I)
    if m:
        h, mi = int(m.group(1)) % 12 if m.group(3) else int(m.group(1)), int(m.group(2))
        if m.group(3) and m.group(3).upper() == "PM":
            h += 12
        at = datetime.now().replace(hour=h % 24, minute=mi, second=0, microsecond=0)
        if at < datetime.now():
            at += timedelta(days=1)
        until = at.timestamp()
    _paused_until = until
    _status["t"] = 0


def status(refresh: bool = False) -> dict:
    if not refresh and _status["v"] and time.time() - _status["t"] < 120:
        return _status["v"]
    b = binary()
    v = {"installed": bool(b), "logged_in": False, "version": "", "method": "", "path": b or ""}
    if b:
        try:
            v["version"] = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=20, env=_env(),
                                          cwd=_workdir()).stdout.strip().replace("codex-cli ", "")
            out = subprocess.run([b, "login", "status"], capture_output=True, text=True, timeout=20, env=_env(),
                                 cwd=_workdir())
            text = (out.stdout + out.stderr).strip()
            v["logged_in"] = out.returncode == 0 and "logged in" in text.lower() and "not logged" not in text.lower()
            v["method"] = "ChatGPT" if "chatgpt" in text.lower() else ("API key" if "api key" in text.lower() else "")
        except Exception as e:
            log.info("codex status check failed: %s", e)
    v["paused_until"] = _paused_until if paused() else 0
    v["paused_minutes"] = int(paused() / 60) + 1 if paused() else 0
    v["model"] = model()
    _status.update(t=time.time(), v=v)
    return v


def status_cached() -> dict:
    v = _status["v"]
    if (not v or time.time() - _status["t"] > 120) and not _refreshing.locked():
        def refresh():
            with _refreshing:
                status(refresh=True)
        threading.Thread(target=refresh, daemon=True).start()
    return v or {"installed": bool(binary()), "logged_in": False, "version": "", "checking": True}


def usable() -> bool:
    s = status()
    return s["installed"] and s["logged_in"] and not paused()


def model() -> str:
    return str(config.get("ai.codex_model", "gpt-6-astra") or "")


def _toml(v) -> str:
    """A value for `-c key=value` (TOML): JSON strings, arrays and inline tables are close enough."""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_toml(x)}" for k, x in v.items()) + "}"
    return json.dumps(v)


def _base(fast: bool = False, effort: str = "medium") -> list[str]:
    m = config.get("ai.codex_fast_model", "") if fast else ""
    effort = "xhigh" if effort == "max" else effort
    args = [binary() or "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
            "--sandbox", "read-only", "--disable", "shell_tool", "--disable", "unified_exec",
            "-c", 'approval_policy="never"', "-c", f'model_reasoning_effort="{effort}"']
    if m or model():
        args += ["-m", m or model()]
    return args


def _mcp_args(cfg: dict) -> list[str]:
    srv = cfg["mcpServers"][SERVER]
    out = []
    for k in ("command", "args", "env"):
        out += ["-c", f"mcp_servers.{SERVER}.{k}={_toml(srv[k])}"]
    # preparing an application writes a letter and answers, which can take a few minutes
    return out + ["-c", f"mcp_servers.{SERVER}.startup_timeout_sec=60", "-c", f"mcp_servers.{SERVER}.tool_timeout_sec=600"]


def _events(stdout: str) -> list[dict]:
    out = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _failure(evs: list[dict], stderr: str = "") -> str | None:
    for e in evs:
        if e.get("type") == "turn.failed":
            return (e.get("error") or {}).get("message") or "turn failed"
        if e.get("type") == "error":
            return e.get("message") or "error"
    return None


def _raise(msg: str) -> None:
    _check_limit(msg)
    if re.search(r"log ?in|not logged|unauthori[sz]ed|401", msg, re.I):
        _status["t"] = 0
        raise Unavailable("ChatGPT (Codex) isn't signed in. Open the ChatGPT app and sign in.")
    if paused():
        raise Unavailable(f"ChatGPT hit its usage limit; back in {int(paused() / 60) + 1} min")
    raise RuntimeError(f"ChatGPT: {msg[:240]}")


def call_json(system: str, text: str, schema: dict, effort: str = "medium", fast: bool = False,
              timeout: int = 300) -> dict:
    if not binary():
        raise Unavailable("ChatGPT's Codex isn't installed")
    if paused():
        raise Unavailable(f"ChatGPT hit its usage limit; back in {int(paused() / 60) + 1} min")
    red = Redactor()
    wd = _workdir()
    tag = uuid.uuid4().hex[:10]
    sfile, ofile = wd / f"schema-{tag}.json", wd / f"out-{tag}.json"
    _write_private(sfile, json.dumps(schema))
    prompt = f"<instructions>\n{red.redact(system)}\n</instructions>\n\n{red.redact(text)}\n\nAnswer only in the JSON schema."
    try:
        p = subprocess.run([*_base(fast, effort), "--output-schema", str(sfile), "-o", str(ofile), "--json", "-"],
                           input=prompt, capture_output=True, text=True, timeout=timeout, env=_env(), cwd=wd)
        evs = _events(p.stdout)
        bad = _failure(evs)
        if bad or not ofile.exists():
            _raise(bad or (p.stderr or "no answer").strip()[-240:])
        out = ofile.read_text().strip()
        from .claude_code import _parse_json_text
        return red.restore(_parse_json_text(out))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ChatGPT took longer than {timeout}s")
    finally:
        sfile.unlink(missing_ok=True)
        ofile.unlink(missing_ok=True)


def chat(system: str, history: list[dict], timeout: int = 180) -> str:
    out = call_json(system + "\nReply to the student's last message in `reply` (plain text, brief).",
                    "\n\n".join(f"{'Student' if m['role'] == 'user' else 'You'}: {m['content']}" for m in history[-10:]),
                    {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
                     "additionalProperties": False}, effort="low", timeout=timeout)
    return out["reply"].strip()


def run_agent(pid: int, prompt: str, system: str, source: str = "codex", tools: list[str] | None = None,
              timeout: int = 600, on_step=None) -> dict:
    """ChatGPT works through Co-op Coach's tools until it's done. Returns its reply and the run id."""
    if not usable():
        s = status()
        raise Unavailable("ChatGPT's Codex isn't installed" if not s["installed"] else
                          "ChatGPT (Codex) isn't signed in" if not s["logged_in"] else
                          f"ChatGPT hit its usage limit; back in {s['paused_minutes']} min")
    run_id = uuid.uuid4().hex[:12]
    ticket = auth.issue_ticket(pid, ttl=timeout + 300, label=source)
    red = Redactor()
    args = [*_base(), *_mcp_args(mcp_config(pid, ticket, run_id, source, tools)), "--json", "-"]
    steps, reply, bad, err = [], "", None, []
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=_env(), cwd=_workdir())
        killer = threading.Timer(timeout, proc.kill)
        killer.start()
        threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True).start()
        proc.stdin.write(f"<instructions>\n{red.redact(system)}\n</instructions>\n\n{red.redact(prompt)}")
        proc.stdin.close()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            item = ev.get("item") or {}
            if ev.get("type") == "item.started" and item.get("type") == "mcp_tool_call":
                step = {"tool": item.get("tool", ""), "args": item.get("arguments") or {}}
                steps.append(step)
                if on_step:
                    on_step(step)
            elif ev.get("type") == "item.completed" and item.get("type") == "agent_message":
                reply = item.get("text") or reply
            elif ev.get("type") in ("turn.failed", "error"):
                bad = _failure([ev])
        proc.wait()
        killer.cancel()
    finally:
        auth.revoke_ticket(ticket)
    if bad or not reply:
        _raise(bad or "".join(err).strip()[-240:] or "stopped without an answer")
    return {"reply": red.restore(reply).strip(), "run_id": run_id, "tools_used": steps}


# --- interactive: you watching -----------------------------------------------------------------------------

APPLY_RULES = ("Rules: never press the final Submit / Send application button; the student does. Never solve CAPTCHAs, "
               "sign in, create accounts or type passwords: stop and ask. Leave voluntary demographic questions blank. "
               "Use only Co-op Coach's answers or what the student tells you; ask instead of guessing.")


def open_in_terminal(pid: int, job_id: str) -> dict:
    """Interactive Codex in Terminal with your own config (its Chrome/browser plugin) plus Co-op Coach's tools."""
    from . import agent
    b = binary()
    if not b:
        raise Unavailable("ChatGPT's Codex isn't installed")
    if sys.platform != "darwin":
        raise Unavailable("Opening Terminal is Mac-only for now")
    j = agent._find_job(pid, job_id)
    if not j:
        raise Unavailable("no such posting")
    wd = _workdir()
    run_id = uuid.uuid4().hex[:12]
    hours = int(config.get("ai.terminal_ticket_hours", 4))
    ticket = auth.issue_ticket(pid, ttl=hours * 3600, label="ChatGPT in Terminal")
    from . import db, vault
    ref = ""
    if vault.available():  # a ticket that lives for hours belongs in the Keychain, not in a file
        ref = run_id
        vault.put(f"claude-run:{ref}", ticket)
        db.meta_set("terminal_ticket_refs", json.dumps(
            [r for r in db.loads(db.meta_get("terminal_ticket_refs", "[]"), []) if r["until"] > time.time()]
            + [{"ref": ref, "until": time.time() + hours * 3600}]))
    cfg = mcp_config(pid, ticket, run_id, "codex", ticket_ref=ref)
    prompt = (f"{APPLY_RULES}\n\nApply to {j['company']}: {j['title']} (Co-op Coach job_id {j['id']}).\n"
              f"1. Call the coopcoach tool application_files with job_id {j['id']}: every answer, my details, and my "
              f"tailored resume and cover letter as local files.\n2. Open the apply_url in Chrome with your browser "
              f"tools and fill every field; upload the resume (and the letter if asked); go through the pages without "
              f"pressing the final Submit. If you can't use a browser, call fill_application instead.\n3. Tell me what's "
              f"left so I can review and submit.\n4. When I say I've submitted, call track with status applied.")
    pfile = wd / f"term-{run_id}-prompt.txt"
    _write_private(pfile, prompt)
    cmd = (f"cd {shlex.quote(str(wd))} && {shlex.quote(b)} " + " ".join(shlex.quote(a) for a in _mcp_args(cfg))
           + f' "$(cat {shlex.quote(str(pfile))})"')
    script = f'tell application "Terminal"\nactivate\ndo script {json.dumps(cmd)}\nend tell'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=20)
    return {"opened": True, "run_id": run_id, "message": "ChatGPT (Codex) opened in Terminal. Watch it fill the form, then press Submit."}


def open_in_atlas(pid: int, job_id: str, kit: dict) -> dict:
    """Open the application in the ChatGPT app's browser (Atlas) and put the fill instructions on the clipboard.
    Browser agents can't attach files from your disk, so the tailored resume goes to Downloads, shown in Finder."""
    from . import apply
    if sys.platform != "darwin":
        raise Unavailable("Atlas is Mac-only")
    files = kit.get("local_files") or apply._write_files(pid, kit)
    downloads = Path.home() / "Downloads"
    copied = []
    for kind, path in files.items():
        if path and Path(path).exists():
            dest = downloads / Path(path).name
            shutil.copyfile(path, dest)
            copied.append(str(dest))
    prompt = apply.agent_prompt(kit).replace(
        "Upload my resume from", "Ask me to attach my resume (it's in my Downloads folder) instead of fetching it from")
    subprocess.run(["pbcopy"], input=prompt, text=True, check=True, timeout=10)
    subprocess.run(["open", "-a", "ChatGPT", kit["job"]["apply_url"]], check=True, timeout=20)
    if copied:
        subprocess.run(["open", "-R", copied[0]], check=False, timeout=10)
    return {"opened": True, "files": copied,
            "message": "Opened in Atlas, instructions copied. Open Ask ChatGPT (agent), paste, and attach the resume from Downloads. You press Submit."}


def sign_in_in_terminal() -> None:
    b = binary()
    if not b or sys.platform != "darwin":
        raise Unavailable("Open the ChatGPT app and sign in")
    script = f'tell application "Terminal"\nactivate\ndo script {json.dumps(shlex.quote(b) + " login")}\nend tell'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=20)
    _status["t"] = 0
