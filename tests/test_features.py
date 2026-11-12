"""Lean or everything: a new install starts lean (People, hackathons and the Excel export
tucked away, with Jarvis not offering them), one switch brings everything back, and nothing is deleted either way.

Run: .venv/bin/python -m tests.test_features
"""

import os
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-feat-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, db, events, features, server, sync  # noqa: E402

results: dict[str, bool] = {}


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def main():
    db.init()
    server._sync_in_background = lambda: None
    check("a new install starts lean", features.mode() == "lean" and features.hidden() == set(features.LEAN_HIDES))
    tools = set(agent.chat_tools())
    check("...and Jarvis doesn't offer what's tucked away", not tools & {"recruiters", "hackathon_application"}
          and {"search_jobs", "queue_application", "my_calendar", "coding_progress", "pin_track", "my_market",
               "hires_interns", "find_events"} <= tools)  # (Tracks and events are shown in lean: the owner uses them daily)
    with TestClient(server.app, base_url="http://localhost") as web:
        same = {"Origin": "http://localhost"}
        st = web.get("/api/state").json()
        check("the page is told what's tucked away", st["features"] == {"mode": "lean", "hidden": sorted(features.LEAN_HIDES)})
        check("a mode that isn't one is refused", web.put("/api/features", json={"mode": "most"}, headers=same).status_code == 400)
        r = web.put("/api/features", json={"mode": "all"}, headers=same).json()
        check("one switch brings everything back", r == {"mode": "all", "hidden": []} and web.get("/api/state").json()["features"]["hidden"] == []
              and set(agent.chat_tools()) == set(agent.CHAT_TOOLS))
        web.put("/api/features", json={"mode": "lean"}, headers=same)

    # lean's alerts don't list public events (nothing would show them); everything's do
    asked = []
    events.upcoming = lambda days=60, kind="": asked.append(days) or []
    sync.notify_profiles(set(), db.now())
    lean_asked = len(asked)
    features.set_mode("all")
    sync.notify_profiles(set(), db.now())
    check("lean's alerts leave public events out, everything's include them", lean_asked == 0 and len(asked) == 1)
    features.set_mode("lean")
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
