"""Calendar → Yours | Events: your own blocks, adding and removing events, and what shows on Yours. Scratch data only.

Run: .venv/bin/python -m tests.test_calendar_board
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-board-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import db, features, meetings, server  # noqa: E402


def main():
    db.init()
    server._sync_in_background = lambda: None
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Robin', ?, ?)", (now, now)).lastrowid
    checks: dict[str, bool] = {}
    with TestClient(server.app, base_url="http://localhost", headers={"Origin": "http://localhost"}) as web:
        start = now + 2 * 86400
        r = web.post("/api/calendar", json={"profile_id": pid, "title": "OA prep", "start_ts": start, "end_ts": start + 5400})
        checks["a block is saved"] = r.status_code == 200 and r.json()["item_id"]
        item = r.json()["item_id"]
        mine = web.get("/api/events", params={"profile_id": pid}).json()["mine"]
        checks["it shows on Yours with its own time and kind"] = \
            [(m["title"], m["kind"], m["start_ts"], m["end_ts"]) for m in mine] == [("OA prep", "block", start, start + 5400)]
        r2 = web.post("/api/calendar", json={"profile_id": pid, "title": "OA prep", "start_ts": start + 86400})
        checks["two blocks with the same name are two blocks"] = r2.json()["item_id"] != item
        checks["...and one without an end lasts an hour"] = \
            db.row("SELECT end_ts - start_ts AS n FROM calendar_items WHERE id=?", (r2.json()["item_id"],))["n"] == 3600
        checks["a block goes on the calendar link with a reminder"] = \
            any(x["title"] == "OA prep" and x["alarm"] == 15 for x in meetings.for_calendar(pid))
        bad = [web.post("/api/calendar", json={"profile_id": pid, "title": "", "start_ts": start}),
               web.post("/api/calendar", json={"profile_id": pid, "title": "x"}),
               web.post("/api/calendar", json={"profile_id": pid, "title": "x", "start_ts": start, "end_ts": start - 60}),
               web.post("/api/calendar", json={"profile_id": 9999, "title": "x", "start_ts": start})]
        checks["no name, no start, an end before the start or an unknown person are refused"] = \
            [b.status_code for b in bad] == [400, 400, 400, 404]
        checks["removing it takes it off"] = web.delete(f"/api/calendar/{item}", params={"profile_id": pid}).status_code == 200 \
            and not db.row("SELECT 1 AS k FROM calendar_items WHERE id=?", (item,))
        with db.tx() as c:
            c.execute("INSERT INTO event_feeds (id, url, label, created_at) VALUES (9001,'https://x/9001','Campus',?)", (now,))
            c.execute("INSERT INTO events (id, feed_id, title, start_ts, kind, first_seen, last_seen) VALUES ('e1',9001,'Tech talk',?, 'other',?,?)",
                      (start, now, now))
        put = lambda mark: web.put("/api/events/e1/mark", json={"profile_id": pid, "mark": mark})  # noqa: E731
        got = lambda: next((e.get("mark") for e in web.get("/api/events", params={"profile_id": pid}).json()["events"] if e["id"] == "e1"), "missing")  # noqa: E731
        checks["an event starts on the Events side"] = got() in (None, "")
        checks["dropping it on Yours saves it"] = put("going").status_code == 200 and got() == "going"
        checks["removing it sends it back"] = put(None).status_code == 200 and got() in (None, "")
    checks["events are shown in lean"] = "events" not in features.hidden()
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print(f"\n{sum(bool(v) for v in checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
