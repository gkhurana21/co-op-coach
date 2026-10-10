"""Event sources and location sorting, offline.

Run: .venv/bin/python -m tests.test_events
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-evtest-")

from coopcoach import db, events  # noqa: E402

CASES = {
    "Toronto, ON": "Toronto / GTA", "MaRS Discovery District, 101 College St, Toronto": "Toronto / GTA",
    "Kitchener, ON N2G 4X8": "Waterloo Region", "Communitech Hub": "Waterloo Region", "Cambridge, ON": "Waterloo Region",
    "Cambridge, MA": "Boston", "Online": "Online", "Zoom": "Online", "Palo Alto, California": "SF Bay Area",
    "Brooklyn, NY": "New York", "Chapel Hill, North Carolina, US": "Research Triangle", "Halifax, NS": "Halifax",
    "London, UK": "Elsewhere", "Bellevue, WA": "Seattle", "Montréal, QC": "Montréal",
}


def main():
    checks = {}
    for loc, want in CASES.items():
        checks[f"{loc!r} → {want}"] = events.event_region(loc) == want
    checks["no location but 'virtual' in the title → Online"] = events.event_region("", "Virtual Info Session") == "Online"
    s, e = events._devpost_dates("Oct 09 - 12, 2026")
    checks["Devpost 'Oct 09 - 12, 2026' parses to a 3-day span"] = s and e and 2 * 86400 < e - s < 4 * 86400
    s2, e2 = events._devpost_dates("Dec 28, 2026 - Jan 03, 2027")
    checks["Devpost dates across a year boundary"] = s2 and e2 and e2 > s2
    checks["Meetup feed label picks up the city"] = events._site_label(
        "https://www.meetup.com/find/?location=ca--on--Waterloo&source=EVENTS", "Meetup") == "Meetup · Waterloo"
    checks["Eventbrite feed label picks up the city"] = events._site_label(
        "https://www.eventbrite.ca/d/canada--toronto/science-and-tech--events/", "Eventbrite") == "Eventbrite · Toronto"
    checks["'Hack the North' / 'HackNC' / 'Banana Hacks' are hackathons"] = all(
        events.classify(t) == "hackathon" for t in ("Hack the North 2026", "HackNC", "Banana Hacks"))
    checks["a Product Management meetup isn't a hackathon"] = events.classify("Product Management Meetup") != "hackathon"
    # saved events, dedupe, company badges, calendar export
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES"
                        " ('T',1,1,'{}','{}')").lastrowid
        c.execute("INSERT INTO favourites (profile_id, kind, key, label, created_at) VALUES (?,?,?,?,?)",
                  (pid, "company", "rbc", "RBC", now))
        for fid, label in ((1, "Meetup · Toronto tech"), (2, "Eventbrite · Toronto tech")):
            c.execute("INSERT INTO event_feeds (id, url, label, created_at) VALUES (?,?,?,?)", (fid, f"https://x/{fid}", label, now))
        rows = [("a1", 1, "RBC Tech Info Session", now + 86400, "Toronto, ON"),
                ("a2", 2, "RBC Tech Info Session!", now + 86400 + 60, "Toronto, ON"),  # same event, other source
                ("b1", 1, "Passive Income Online Masterclass", now + 2 * 86400, "Online"),
                ("c1", 2, "Women in AI Panel", now + 3 * 86400, "Waterloo, ON")]
        for eid, fid, title, ts, loc in rows:
            c.execute("INSERT INTO events (id, feed_id, title, start_ts, location, url, description, kind, first_seen,"
                      " last_seen, region) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, fid, title, ts, loc, f"https://e/{eid}", "", events.classify(title), now, now,
                       events.event_region(loc)))
        c.execute("INSERT INTO event_marks VALUES (?,?,?,?)", (pid, "c1", "going", now))
    evs = events.for_person(pid)
    titles = [e["title"] for e in evs]
    checks["the same event from two sources shows once, with 'also on'"] = (
        sum("RBC Tech Info Session" in t for t in titles) == 1 and evs[0]["also"] == ["Eventbrite · Toronto tech"])
    checks["an event mentioning a company you follow is flagged"] = evs[0]["company"] == "RBC"
    checks["get-rich-quick noise is filtered out"] = not any("Passive Income" in t for t in titles)
    checks["your ✅ going mark comes back"] = next(e for e in evs if e["id"] == "c1")["mark"] == "going"
    ics = events.ics_for([e for e in evs if e["mark"]])
    checks["calendar file is valid iCalendar with your saved event"] = ics.startswith("BEGIN:VCALENDAR") and \
        "SUMMARY:Women in AI Panel" in ics and ics.count("BEGIN:VEVENT") == 1 and "\r\n" in ics

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
