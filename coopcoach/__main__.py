"""CLI: python -m coopcoach [serve|sync] ..."""

import argparse
import json
import logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="coopcoach", description="Co-op Coach: internship finder + matcher")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the web app (default)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    y = sub.add_parser("sync", help="run one sync now (good for cron/launchd)")
    y.add_argument("--no-notify", action="store_true")
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
    d.add_argument("--ai", action="store_true", help="let the demo use your Claude Code / ChatGPT / Groq")
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
        if not args.ai:  # rules only: nothing leaves this computer and nobody's AI plan is used
            os.environ.update(COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", GROQ_API_KEY="",
                              ANTHROPIC_API_KEY="")
        logging.basicConfig(level=logging.WARNING)
        if not (demo_dir / "coopcoach.db").exists():
            demo_dir.mkdir(parents=True, exist_ok=True)
            print("Building the demo (a minute)…")
            from . import demo
            demo.build(demo_dir)
        import uvicorn
        print(f"\n  Co-op Coach demo → http://127.0.0.1:{args.port}  (fictional student, sample postings)\n")
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
        report = sync.run(notify_users=not args.no_notify)
        print(json.dumps({k: v for k, v in report.items() if k != "sources"}, indent=2))
        bad = {k: v["error"] for k, v in report.get("sources", {}).items() if not v.get("ok")}
        if bad:
            print("failed sources:", json.dumps(bad, indent=2))
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
    uvicorn.run("coopcoach.server:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
