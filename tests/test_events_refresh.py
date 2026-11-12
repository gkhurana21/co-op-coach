"""Refreshing events: sources near you and hackathon lists are read first and each is saved as it arrives, so their events
show while the slow university calendars are still loading; a source read in the last 15 minutes isn't read again; and
the page can ask how far along it is, cheaply. Timed against stand-in sources (fast nearby ones, slow campus ones).

Run: .venv/bin/python -m tests.test_events_refresh
"""

import os
import tempfile
import time

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-evref-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="")

from coopcoach import db, events  # noqa: E402

results: dict[str, bool] = {}
NOW = int(time.time())
SLOW = 2.5  # seconds a big university calendar takes here (the real ones: 14-23 s)


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def fake_fetch(url: str):
    campus = "campus" in url
    time.sleep(SLOW if campus else 0.15)
    n = url.rsplit("/", 1)[-1]
    return [{"uid": f"{n}-{i}", "title": f"Career fair {n} {i}", "start_ts": NOW + (i + 2) * 86400, "end_ts": None,
             "all_day": False, "location": "Toronto, ON" if not campus else "Providence, RI", "url": "", "description": "career",
             "kind": "career"} for i in range(3)], ""


def main():
    db.init()
    events.fetch_feed = fake_fetch
    with db.tx() as c:
        for i in range(10):
            c.execute("INSERT INTO event_feeds (url, label, career_only, created_at, grp) VALUES (?,?,0,?,?)",
                      (f"https://near.example/{i}", f"Near {i}", NOW, "Toronto & Waterloo" if i % 2 else "Hackathons"))
        for i in range(6):
            c.execute("INSERT INTO event_feeds (url, label, career_only, created_at, grp) VALUES (?,?,0,?,?)",
                      (f"https://campus.example/{i}", f"Campus {i}", NOW, "US universities"))

    seen = []

    def progress(done, total, new):
        near = db.row("SELECT COUNT(*) AS n FROM events e JOIN event_feeds f ON f.id=e.feed_id WHERE f.url LIKE 'https://near%'")["n"]
        seen.append((round(time.time() - t, 2), done, total, new, near))
    t = time.time()
    new, status = events.sync_feeds(progress=progress)
    took = time.time() - t
    first_near_all = next(s for s in seen if s[4] == 30)
    check(f"every source is read: {len(status)} of 16, {new} new events in {took:.1f} s", len(status) == 16 and new == 48)
    check(f"the events near you are saved and showing after {first_near_all[0]:.1f} s, not after all {took:.1f} s",
          first_near_all[0] < 1.5 and first_near_all[0] < took / 2)
    check("...because nearby sources and hackathon lists go first, and each is saved as it arrives",
          [s[1] for s in seen] == list(range(17)) and seen[0][2] == 16)

    t = time.time()
    again, status = events.sync_feeds(min_age=events.RECENT)
    check(f"pressing Refresh again within 15 minutes reads nothing again ({time.time() - t:.2f} s)", status == {} and again == 0)
    with db.tx() as c:
        c.execute("UPDATE event_feeds SET last_fetched=? WHERE url LIKE 'https://near%'", (NOW - 20 * 60,))
    t = time.time()
    _, status = events.sync_feeds(min_age=events.RECENT)
    check(f"...an older one is read again, while the university calendars wait their 12 hours ({time.time() - t:.2f} s)",
          len(status) == 10 and all("near" in u for u in status))

    # the page asks how far along it is: a small answer, not the whole list of events
    from fastapi.testclient import TestClient

    from coopcoach import server
    server._sync_in_background = lambda: None
    with db.tx() as c:
        c.execute("UPDATE event_feeds SET last_fetched=0")
    with TestClient(server.app, base_url="http://localhost") as web:
        h = {"Origin": "http://localhost"}
        start = web.post("/api/events/refresh", headers=h).json()
        polls, last = 0, {}
        while polls < 100:
            last = web.get("/api/events/refresh").json()
            polls += 1
            if not last["refreshing"]:
                break
            time.sleep(0.2)
        check("the page can follow a refresh: how many sources are done, of how many, and how many events are new",
              start["started"] and last["done"] == last["total"] >= 16 and not last["refreshing"] and polls > 1)
        check("...and a second press while it runs doesn't start another", web.post("/api/events/refresh", headers=h).json()["started"]
              in (True, False))
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
