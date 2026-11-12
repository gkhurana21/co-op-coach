"""CLI: python -m coopcoach [serve|sync] ..."""

import argparse
import json
import logging


def _open_when_ready(url: str, port: int) -> None:
    """Open the browser once the server answers (opening it first just shows "can't connect")."""
    import socket
    import threading
    import time
    import webbrowser

    def wait():
        for _ in range(240):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    webbrowser.open(url)
                    return
            except OSError:
                time.sleep(0.5)

    threading.Thread(target=wait, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser(prog="coopcoach", description="Co-op Coach: internship finder + matcher")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the web app (default)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true", help="open the app in your browser once it's ready")
    y = sub.add_parser("sync", help="run one sync now (good for cron/launchd)")
    y.add_argument("--no-notify", action="store_true")
    y.add_argument("--quick", action="store_true", help="only the boards that matter most to you (seconds, not minutes)")
    cr = sub.add_parser("crawl", help="the shared feed's crawler: read every board into its own schema (see central.py)")
    cr.add_argument("--quick", action="store_true", help="only the boards that posted lately")
    cr.add_argument("--discover", action="store_true", help="look for new boards on the public web first (weekly)")
    r = sub.add_parser("reset-passcode", help="remove a forgotten passcode (needs access to this computer)")
    r.add_argument("name", help="the person's name as shown in the app")
    m = sub.add_parser("mcp", help="let your own Claude use Co-op Coach (MCP server over stdio)")
    m.add_argument("--profile", help="whose Co-op Coach (name as shown in the app); optional once connected")
    sb = sub.add_parser("supabase", help="keep everything in your own Supabase project (Postgres)")
    sb.add_argument("--connect", metavar="PROJECT_REF", help="use this Supabase project (its app role must exist)")
    sb.add_argument("--region", default="ca-central-1")
    sb.add_argument("--move", action="store_true", help="copy everything from this computer to Supabase and switch")
    sb.add_argument("--local", action="store_true", help="switch back to the local database file")
    d = sub.add_parser("demo", help="try it with a fictional student and sample postings (a separate data-demo/ folder)")
    d.add_argument("--port", type=int, default=8790)
    d.add_argument("--fresh", action="store_true", help="rebuild the demo data")
    d.add_argument("--ai", action="store_true", help="let the demo use your Claude Code / ChatGPT too (not just Groq)")
    d.add_argument("--no-ai", action="store_true", help="rules only: no AI at all, nothing leaves this computer")
    d.add_argument("--open", action="store_true", help="open the demo in your browser once it's ready")
    c = sub.add_parser("claude", help="connect your Claude Code: install the Co-op Coach plugin (skills + tools)")
    c.add_argument("--profile", required=True, help="whose Co-op Coach (name as shown in the app)")
    args = ap.parse_args()
    if args.cmd == "demo":  # must run before anything imports coopcoach.config (it reads COOPCOACH_DATA once)
        import os
        import shutil
        from pathlib import Path
        demo_dir = Path(__file__).resolve().parent.parent / "data-demo"
        if args.fresh and demo_dir.exists():
            shutil.rmtree(demo_dir)
        os.environ.update(COOPCOACH_DATA=str(demo_dir), COOPCOACH_DEMO="1")
        if args.no_ai:  # rules only: nothing leaves this computer
            os.environ.update(COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", GROQ_API_KEY="",
                              ANTHROPIC_API_KEY="")
        elif not args.ai:  # Jarvis runs on the free Groq key from .env (if there is one); nobody's paid plan is used
            os.environ.update(COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", ANTHROPIC_API_KEY="")
            from .config import _load_dotenv  # noqa: F401 (reads .env, so GROQ_API_KEY is set when it's there)
        logging.basicConfig(level=logging.WARNING)
        if not (demo_dir / "coopcoach.db").exists():
            demo_dir.mkdir(parents=True, exist_ok=True)
            print("Building the demo (a minute)…")
            from . import demo
            demo.build(demo_dir)
        import uvicorn
        print(f"\n  Co-op Coach demo → http://127.0.0.1:{args.port}  (fictional student, sample postings)\n")
        if args.open:
            _open_when_ready(f"http://127.0.0.1:{args.port}", args.port)
        os.environ["COOPCOACH_PORT"] = str(args.port)  # runs on your Claude Code ask this app for their tools
        uvicorn.run("coopcoach.server:app", host="127.0.0.1", port=args.port, log_level="warning")
        return
    if args.cmd == "mcp":  # stdout carries the protocol: logs go to stderr only
        import sys
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        from . import mcp_server
        mcp_server.main(args.profile)
        return
    if args.cmd == "supabase":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from . import supa
        if args.connect:
            s = supa.connect(args.connect, args.region)
            print(f"Connected to {args.connect} through {s['host']}")
        if args.move:
            print(json.dumps(supa.move_to_supabase(print), indent=2))
        if args.local:
            supa.use_local()
            print("Using the local database file again")
        s = supa.settings()
        print(f"Database: {'Supabase ' + s.get('ref', '') if supa.enabled() else 'local file'}")
        return
    if args.cmd == "claude":
        import getpass

        from . import auth, claude_setup, db
        db.init()
        p = db.row("SELECT id, name FROM profiles WHERE lower(name)=lower(?)", (args.profile,))
        if not p:
            raise SystemExit(f"No profile named {args.profile!r}")
        if auth.has_passcode(p["id"]) and not auth.verify(p["id"], getpass.getpass(f"{p['name']}'s passcode: ")):
            raise SystemExit("Wrong passcode")
        print(json.dumps(claude_setup.connect(p["id"]), indent=2))
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "sync":
        from . import db, store, sync
        db.init()
        store.seed_companies()
        store.seed_event_feeds()
        report = sync.run_quick() if args.quick else sync.run(notify_users=not args.no_notify)
        print(json.dumps({k: v for k, v in report.items() if k != "sources"}, indent=2))
        bad = {k: v["error"] for k, v in report.get("sources", {}).items() if not v.get("ok")}
        if bad:
            print("failed sources:", json.dumps(bad, indent=2))
        return

    if args.cmd == "crawl":
        import os
        os.environ["COOPCOACH_CRAWLER"] = "1"
        from . import db, store, sync
        if not db.is_postgres() or not os.environ.get("COOPCOACH_PG_SCHEMA"):
            raise SystemExit("The crawler writes to the shared feed's own schema: set COOPCOACH_DATABASE_URL and"
                             " COOPCOACH_PG_SCHEMA")
        db.init()
        if db.row("SELECT 1 FROM profiles LIMIT 1"):  # never crawl into someone's own database by mistake
            raise SystemExit("This schema has people in it: the crawler only writes to the shared feed's schema")
        store.seed_companies()
        if args.discover:
            from . import discover
            print(json.dumps(discover.run(), indent=2, default=str)[:2000])
        report = sync.run_quick() if args.quick else sync.run(notify_users=False)
        print(json.dumps({k: v for k, v in report.items() if k != "sources"}, indent=2, default=str))
        bad = {k: v["error"] for k, v in report.get("sources", {}).items() if not v.get("ok")}
        print(f"{len(bad)} sources failed" + (": " + ", ".join(list(bad)[:20]) if bad else ""))
        return

    if args.cmd == "reset-passcode":
        from . import auth, db
        db.init()
        p = db.row("SELECT id, name FROM profiles WHERE lower(name)=lower(?)", (args.name,))
        if not p:
            raise SystemExit(f"No profile named {args.name!r}")
        auth.clear_passcode(p["id"])
        print(f"Passcode removed for {p['name']}. Set a new one in Settings → Passcode & account.")
        return

    import os

    import uvicorn
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8765)
    if host not in ("127.0.0.1", "localhost", "::1"):
        os.environ["COOPCOACH_ANY_HOST"] = "1"
        print("\n  Shared on your network: anyone on it can reach the app. Give everyone a passcode.")
    print(f"\n  Co-op Coach → http://{host}:{port}\n")
    if getattr(args, "open", False):
        _open_when_ready(f"http://127.0.0.1:{port}", port)
    os.environ["COOPCOACH_PORT"] = str(port)  # runs on your Claude Code ask this app for their tools
    uvicorn.run("coopcoach.server:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
