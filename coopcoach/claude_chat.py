"""The chat on your own Claude Code, kept quick. Starting `claude` takes 2-3 seconds before it does anything, so the next
process is started ahead and waits, using no tokens, until a message comes; one is only ever used for one reply. Replies arrive
as they're written.

Measured on the demo: first words of a plain reply in 0.9 s, down from 4 s; a reply that used a tool in 3.8 s, down from 6.2 s.
"""

import atexit
import json
import logging
import subprocess
import threading
import time
import uuid

from . import agent, auth
from . import claude_code as cc
from .privacy import Redactor

log = logging.getLogger(__name__)
MAX_AGE = 15 * 60  # seconds a process waits unused before it's closed: nobody's chatting
_spares: dict[int, dict] = {}
_lock = threading.Lock()


def _spawn(pid: int) -> dict:
    run_id = uuid.uuid4().hex[:12]
    ticket = auth.issue_ticket(pid, ttl=MAX_AGE + 900, label="chat")
    cfg = cc._workdir() / f"mcp-{run_id}.json"
    cc._write_private(cfg, json.dumps(cc.mcp_config(pid, ticket, run_id, "chat", agent.chat_tools())))
    sys_args, sys_file = cc._system_args(agent.SYSTEM)  # what's said about the student goes with each message
    proc = subprocess.Popen(
        [cc.binary(), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
         "--mcp-config", str(cfg), "--strict-mcp-config", "--tools", "", "--allowedTools", f"mcp__{cc.SERVER}", "--permission-mode", "dontAsk",
         "--setting-sources", "project", "--disable-slash-commands", "--no-session-persistence", *sys_args, *cc._model("chat")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=cc._env(), cwd=cc._workdir())
    err: list[str] = []
    threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True).start()
    return {"proc": proc, "run_id": run_id, "ticket": ticket, "cfg": cfg, "sys_file": sys_file, "born": time.time(), "err": err, "model": cc.model_for("chat")}


def _close(s: dict) -> None:
    try:
        s["proc"].kill()
    except OSError:
        pass
    s["cfg"].unlink(missing_ok=True)
    (cc._workdir() / f"tools-{s['run_id']}.json").unlink(missing_ok=True)
    if s["sys_file"]:
        s["sys_file"].unlink(missing_ok=True)
    auth.revoke_ticket(s["ticket"])


def warm(pid: int) -> bool:
    """Have one ready for this person, without waiting for it."""
    if not cc.usable():
        return False
    with _lock:
        have = _spares.get(pid)
        if have and have["proc"].poll() is None and time.time() - have["born"] < MAX_AGE:
            return True
        _spares[pid] = {"proc": None}  # holds the place while it starts

    def start():
        try:
            s = _spawn(pid)
        except Exception as e:
            log.info("couldn't start Claude Code ahead: %s", e)
            with _lock:
                _spares.pop(pid, None)
            return
        with _lock:
            if _spares.get(pid, {}).get("proc", 0) is None:
                _spares[pid] = s
            else:
                _close(s)
                return
        t = threading.Timer(MAX_AGE, _expire, args=(pid, s))
        t.daemon = True
        t.start()
    threading.Thread(target=start, daemon=True, name="chat-warm").start()
    return True


def _expire(pid: int, s: dict) -> None:
    with _lock:
        if _spares.get(pid) is s:
            del _spares[pid]
        else:
            return
    _close(s)


def _take(pid: int) -> dict | None:
    with _lock:
        s = _spares.get(pid)
        if not s or not s.get("proc"):
            return None
        del _spares[pid]
        if s["proc"].poll() is None and time.time() - s["born"] < MAX_AGE and s["model"] == cc.model_for("chat"):
            return s
    _close(s)  # (it stopped, or the model list changed since)
    return None


def close_all() -> None:
    with _lock:
        waiting = [s for s in _spares.values() if s.get("proc")]
        _spares.clear()
    for s in waiting:
        _close(s)


atexit.register(close_all)


class _Unmask:
    """Puts contact details back into text as it streams, holding back any placeholder that's only half arrived."""

    def __init__(self, red: Redactor):
        self.red, self.held = red, ""

    def feed(self, text: str) -> str:
        self.held += text
        cut = self.held.rfind("[")
        if cut == -1 or "]]" in self.held[cut:]:
            out, self.held = self.held, ""
        else:
            out, self.held = self.held[:cut], self.held[cut:]
        return self.red.restore(out)

    def flush(self) -> str:
        out, self.held = self.held, ""
        return self.red.restore(out)


def run(pid: int, prompt: str, on_event=None, timeout: int = 300) -> dict:
    """One reply. on_event gets ("delta", text) as it's written and ("step", tool name) as tools start."""
    if not cc.usable():
        raise cc.Unavailable("Claude Code isn't available")
    say = on_event or (lambda *_: None)
    s = _take(pid) or _spawn(pid)
    red = Redactor()
    unmask = _Unmask(red)
    final = None
    killer = threading.Timer(timeout, s["proc"].kill)
    killer.start()
    try:
        s["proc"].stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": red.redact(prompt)}}) + "\n")
        s["proc"].stdin.flush()
        for line in s["proc"].stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            if kind == "stream_event":
                ev = e.get("event") or {}
                if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                    text = unmask.feed(ev["delta"].get("text") or "")
                    if text:
                        say("delta", text)
            elif kind == "assistant":
                for b in (e.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        say("step", b.get("name", "").split("__")[-1])
            elif kind == "result":
                final = e
                break
    except (BrokenPipeError, ValueError, OSError) as e:
        log.info("Claude Code stopped mid-reply: %s", e)
    finally:
        killer.cancel()
        tail = unmask.flush()
        if tail:
            say("delta", tail)
        _close(s)
        warm(pid)  # the next message finds one ready
    if not final:
        text = "".join(s["err"]).strip()[-300:]
        cc._check_limit(text)
        raise RuntimeError(f"Claude Code stopped without an answer{': ' + text if text else ''}")
    cc._raise_if_error(final)
    return {"reply": red.restore(final.get("result") or "").strip(), "run_id": s["run_id"]}
