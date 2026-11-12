"""Connect your Claude Code to Co-op Coach: installs the Co-op Coach plugin (skills, tools, submit guard).

The plugin in claude-plugin/ is copied to data/claude-plugin/ with this machine's paths filled in (the Python that
runs Co-op Coach, its data folder), wrapped in a local marketplace, then installed with `claude plugin`. Its MCP
server opens your profile with a connection token kept in the Keychain; the database stores only its hash.
After connecting, any Claude Code session has /coop-coach:autopilot, /coop-coach:apply, /coop-coach:hunt and more.
"""

import hashlib
import json
import logging
import shutil
import subprocess
import sys

from . import auth, claude_code, config, vault
from .mcp_server import CONNECT_KEY

log = logging.getLogger(__name__)
MARKETPLACE = "coop-coach-local"
PLUGIN = "coop-coach"
TEMPLATE = config.ROOT / "claude-plugin"
MATCHER = "|".join(f"mcp__{srv}__{tool}" for srv in ("claude-in-chrome", "Claude_Browser")
                   for tool in ("computer", "browser_batch"))  # every browser the guard can read the action of


def template_hash() -> str:
    """Changes whenever the plugin's files do."""
    h = hashlib.sha256()
    for f in sorted(p for p in TEMPLATE.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(str(f.relative_to(TEMPLATE)).encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:10]


def plugin_dir():
    return config.DATA_DIR / "claude-plugin"


def build() -> str:
    """Write the marketplace + plugin with absolute paths. Returns the marketplace folder."""
    root = plugin_dir()
    dest = root / PLUGIN
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(TEMPLATE, dest)
    stamp = template_hash()
    manifest = json.loads((dest / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    manifest["version"] = f"{manifest['version']}-{stamp}"  # `claude plugin update` only moves on a new version
    (dest / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / ".template-hash").write_text(stamp, encoding="utf-8")
    py = sys.executable
    env = {"COOPCOACH_DATA": str(config.DATA_DIR), "PYTHONPATH": str(config.ROOT), "COOPCOACH_SOURCE": "claude_code"}
    (dest / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {PLUGIN: {"type": "stdio", "command": py, "args": ["-m", "coopcoach", "mcp"], "env": env}}},
        indent=2), encoding="utf-8")
    (dest / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [{
        "matcher": MATCHER,
        "hooks": [{"type": "command", "command": json.dumps(py) + ' "${CLAUDE_PLUGIN_ROOT}/hooks/no_submit.py"'}],
    }]}}, indent=2), encoding="utf-8")
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": MARKETPLACE, "owner": {"name": "Co-op Coach"},
        "description": "Co-op Coach's plugin, built for this computer by the app.",
        "plugins": [{"name": PLUGIN, "source": f"./{PLUGIN}",
                     "description": "Your internship hunt inside Claude Code: today, apply (you submit), hunt, tracks, "
                                    "NeetCode 150, follow-ups, interviews, hackathons, events, resume."}]}, indent=2), encoding="utf-8")
    return str(root)


def _claude(*args: str, timeout: int = 120) -> tuple[bool, str]:
    b = claude_code.binary()
    if not b:
        return False, "Claude Code isn't installed"
    p = subprocess.run([b, *args], capture_output=True, text=True, timeout=timeout, env=claude_code._env(),
                       cwd=claude_code._workdir())
    return p.returncode == 0, (p.stdout + p.stderr).strip()[-600:]


def installed() -> bool:
    ok, out = _claude("plugin", "list", "--json", timeout=60)
    if not ok:
        return False
    try:
        items = json.loads(out[out.find("["):]) if "[" in out else []
    except ValueError:
        return f"{PLUGIN}@{MARKETPLACE}" in out
    return any(PLUGIN in json.dumps(i) and MARKETPLACE in json.dumps(i) for i in items)


def refresh_if_stale() -> dict:
    """The app updated: bring an installed plugin along, or it keeps offering skills and tools that are gone. Only for
    a plugin someone already connected."""
    if not (plugin_dir() / PLUGIN).exists():
        return {"refreshed": False, "why": "not connected"}
    marker = plugin_dir() / ".template-hash"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == template_hash():
        return {"refreshed": False, "why": "up to date"}
    build()
    if codex_plugin_dir().exists():
        build_codex()
    ok1, out1 = _claude("plugin", "marketplace", "update", MARKETPLACE)
    ok2, out2 = _claude("plugin", "update", f"{PLUGIN}@{MARKETPLACE}")
    if not (ok1 and ok2):
        log.info("refreshing the Claude Code plugin: %s %s", out1[-160:], out2[-160:])
    return {"refreshed": True, "installed": ok2}


def connect(pid: int) -> dict:
    """Create (or replace) this profile's connection and install/refresh the plugin."""
    if not vault.available():
        return {"ok": False, "error": "No Keychain available to hold the connection token"}
    for c in auth.connections(pid):
        if c["label"] == "Claude Code plugin":
            auth.revoke_connection(pid, c["id"])
    token = auth.issue_ticket(pid, ttl=None, label="Claude Code plugin")
    vault.put(f"{CONNECT_KEY}:{pid}", token)
    vault.put(CONNECT_KEY, token)  # sessions that don't name a profile open this one
    market = build()
    steps = []
    ok, out = _claude("plugin", "validate", str(plugin_dir() / PLUGIN))
    steps.append({"step": "check the plugin", "ok": ok, "out": out})
    ok, out = _claude("plugin", "marketplace", "add", market)
    if not ok and "already" in out.lower():
        ok, out = _claude("plugin", "marketplace", "update", MARKETPLACE)
    steps.append({"step": "add the local marketplace", "ok": ok, "out": out})
    ok, out = _claude("plugin", "install", f"{PLUGIN}@{MARKETPLACE}")
    if not ok and "already" in out.lower():
        ok, out = _claude("plugin", "update", f"{PLUGIN}@{MARKETPLACE}")
    steps.append({"step": "install Co-op Coach", "ok": ok, "out": out})
    return {"ok": all(s["ok"] for s in steps[1:]), "steps": steps, "marketplace": market,
            "commands": [f"claude plugin marketplace add {market}", f"claude plugin install {PLUGIN}@{MARKETPLACE}"],
            "skills": sorted(p.name for p in (TEMPLATE / "skills").iterdir() if p.is_dir())}


def disconnect(pid: int, uninstall: bool = False) -> dict:
    for c in auth.connections(pid):
        auth.revoke_connection(pid, c["id"])
    mine = vault.get(f"{CONNECT_KEY}:{pid}") if vault.available() else None
    if vault.available():
        if mine and vault.get(CONNECT_KEY) == mine:
            vault.delete(CONNECT_KEY)
        vault.delete(f"{CONNECT_KEY}:{pid}")
    out = ""
    if uninstall:
        _, out = _claude("plugin", "uninstall", f"{PLUGIN}@{MARKETPLACE}")
    return {"ok": True, "out": out}


# --- the same plugin for ChatGPT's Codex -------------------------------------------------------------------
CODEX_MARKETPLACE = "coop-coach-local"


def codex_plugin_dir():
    return config.DATA_DIR / "codex-plugin"


def build_codex() -> str:
    """Codex plugins: .codex-plugin/plugin.json + skills/ + .mcp.json, listed in .agents/plugins/marketplace.json.
    Same skills as the Claude Code plugin. (Codex plugins have no hooks, so the stop-at-Submit rule is in the
    skills and the server's instructions.)"""
    root = codex_plugin_dir()
    dest = root / "plugins" / PLUGIN
    if dest.exists():
        shutil.rmtree(dest)
    (dest / ".codex-plugin").mkdir(parents=True)
    shutil.copytree(TEMPLATE / "skills", dest / "skills")
    py = sys.executable
    env = {"COOPCOACH_DATA": str(config.DATA_DIR), "PYTHONPATH": str(config.ROOT), "COOPCOACH_SOURCE": "codex"}
    (dest / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {PLUGIN: {"command": py, "args": ["-m", "coopcoach", "mcp"], "env": env}}}, indent=2), encoding="utf-8")
    manifest = json.loads((TEMPLATE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    manifest.update({"skills": "./skills/", "mcpServers": "./.mcp.json", "interface": {
        "displayName": "Co-op Coach", "shortDescription": "Your internship hunt: autopilot, apply (you submit), follow-ups",
        "category": "Productivity", "defaultPrompt": ["Run my Co-op Coach autopilot"]}})
    (dest / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / ".agents" / "plugins").mkdir(parents=True, exist_ok=True)
    (root / ".agents" / "plugins" / "marketplace.json").write_text(json.dumps({
        "name": CODEX_MARKETPLACE, "interface": {"displayName": "Co-op Coach (this computer)"},
        "plugins": [{"name": PLUGIN, "source": {"source": "local", "path": f"./plugins/{PLUGIN}"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]},
        indent=2), encoding="utf-8")
    return str(root)


def _codex(*args: str, timeout: int = 120) -> tuple[bool, str]:
    from . import codex_cli
    b = codex_cli.binary()
    if not b:
        return False, "ChatGPT's Codex isn't installed"
    p = subprocess.run([b, *args], capture_output=True, text=True, timeout=timeout, env=codex_cli._env(),
                       cwd=codex_cli._workdir())
    return p.returncode == 0, (p.stdout + p.stderr).strip()[-600:]


def connect_codex(pid: int) -> dict:
    """Codex gets the Co-op Coach plugin (skills + tools); the connection token is the Keychain one."""
    if not vault.available():
        return {"ok": False, "error": "No Keychain available to hold the connection token"}
    have = vault.get(f"{CONNECT_KEY}:{pid}")
    if not (have and auth.check_ticket(have) == pid):  # reuse the Claude Code connection if there is one
        token = auth.issue_ticket(pid, ttl=None, label="ChatGPT plugin")
        vault.put(f"{CONNECT_KEY}:{pid}", token)
        vault.put(CONNECT_KEY, token)
    market = build_codex()
    steps = []
    ok, out = _codex("plugin", "marketplace", "add", market)
    if not ok and "already" in out.lower():
        ok, out = True, out
    steps.append({"step": "add the local marketplace", "ok": ok, "out": out})
    ok, out = _codex("plugin", "add", f"{PLUGIN}@{CODEX_MARKETPLACE}")
    if not ok and "already" in out.lower():
        ok = True
    steps.append({"step": "install Co-op Coach", "ok": ok, "out": out})
    if not ok:  # older Codex without plugins: register the tools on their own
        ok2, out2 = _codex("mcp", "add", PLUGIN, "--env", f"COOPCOACH_DATA={config.DATA_DIR}", "--env",
                           f"PYTHONPATH={config.ROOT}", "--", sys.executable, "-m", "coopcoach", "mcp")
        steps.append({"step": "add Co-op Coach's tools", "ok": ok2, "out": out2})
        ok = ok2
    return {"ok": ok, "steps": steps, "marketplace": market,
            "commands": [f"codex plugin marketplace add {market}", f"codex plugin add {PLUGIN}@{CODEX_MARKETPLACE}"],
            "skills": sorted(p.name for p in (TEMPLATE / "skills").iterdir() if p.is_dir())}


def codex_installed() -> bool:
    ok, out = _codex("mcp", "list", timeout=60)
    ok2, out2 = _codex("plugin", "list", timeout=60)
    return PLUGIN in out or PLUGIN in out2


def disconnect_codex(pid: int) -> dict:
    _codex("plugin", "remove", f"{PLUGIN}@{CODEX_MARKETPLACE}")
    _codex("mcp", "remove", PLUGIN)
    for c in auth.connections(pid):
        if c["label"] == "ChatGPT plugin":
            auth.revoke_connection(pid, c["id"])
    return {"ok": True}
