"""University and hackathon events, offline (recorded-style responses through a fake network): the Localist, LiveWhale
and The Events Calendar readers, finding the feed behind a pasted calendar page, the campus filter (career / startup /
hackathon yes; concerts, staff and grad-program sessions no), daily drop-ins capped, the new regions, the major
hackathons and their season list, and the bundled source lists.

Run: .venv/bin/python -m tests.test_event_sources
"""

import os
import tempfile
import time

import httpx

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-evsrc-")

from coopcoach import db, events, store  # noqa: E402

NOW = int(time.time())
iso = lambda ts, tz="-04:00": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts - 4 * 3600)) + tz  # noqa: E731

LOCALIST = {"events": [
    {"event": {"id": 1, "title": "Cardinal Careers: Meet the Employers", "localist_url": "https://events.example.edu/e/1",
               "location_name": "Tresidder Union", "experience": "inperson", "description_text": "Recruiters from 40 firms.",
               "filters": {"event_subject": [{"name": "Career Development", "id": 9}]}, "departments": [],
               "event_instances": [{"event_instance": {"id": 11, "start": iso(NOW + 3 * 86400), "end": None, "all_day": False}}]}},
    {"event": {"id": 2, "title": "Online MBA Program Overview", "localist_url": "https://events.example.edu/e/2",
               "location_name": "", "experience": "virtual", "description_text": "", "filters": {}, "departments": [],
               "event_instances": [{"event_instance": {"id": 21, "start": iso(NOW + 4 * 86400), "end": None, "all_day": False}}]}},
], "page": {"current": 1, "total": 1}}

LIVEWHALE = [
    {"id": 7, "title": "Startup Founders Panel &amp; Mixer", "url": "https://events.brown.example/e/7", "date_iso": iso(NOW + 2 * 86400),
     "date2_iso": iso(NOW + 2 * 86400 + 3600), "is_all_day": None, "location": "Friedman Hall", "summary": "<p>Meet founders.</p>",
     "event_types": ["Entrepreneurship"], "tags": [], "group": "Nelson Center"},
    {"id": 8, "title": "Chamber Orchestra Concert", "url": "https://events.brown.example/e/8", "date_iso": iso(NOW + 2 * 86400),
     "date2_iso": None, "is_all_day": None, "location": "Grant Center", "summary": "", "event_types": ["Performances"],
     "tags": [], "group": "Music"},
] + [{"id": 100 + i, "title": "Career Drop-in Hours", "url": "https://events.brown.example/e/d", "date_iso": iso(NOW + (i + 1) * 86400),
      "date2_iso": None, "is_all_day": None, "location": "CareerLAB", "summary": "", "event_types": ["Careers, Recruiting, Internships"],
      "tags": [], "group": "CareerLAB"} for i in range(10)]

TRIBE = {"events": [
    {"id": 5, "title": "Engineering Career Fair", "url": "https://events.example.ca/e/5", "timezone": "America/Vancouver",
     "start_date": time.strftime("%Y-%m-%d 10:00:00", time.localtime(NOW + 5 * 86400)),
     "end_date": time.strftime("%Y-%m-%d 15:00:00", time.localtime(NOW + 5 * 86400)), "all_day": False,
     "venue": {"venue": "Life Building", "city": "Vancouver", "province": "BC"}, "description": "<p>Bring your resume.</p>",
     "categories": [{"name": "Careers"}], "tags": []},
    {"id": 6, "title": "Faculty Meeting: Budget", "url": "https://events.example.ca/e/6", "timezone": "America/Vancouver",
     "start_date": time.strftime("%Y-%m-%d 10:00:00", time.localtime(NOW + 6 * 86400)),
     "end_date": time.strftime("%Y-%m-%d 11:00:00", time.localtime(NOW + 6 * 86400)), "all_day": False, "venue": [],
     "description": "", "categories": [{"name": "Faculty and Staff"}], "tags": []},
], "total_pages": 1}


def handler(req: httpx.Request) -> httpx.Response:
    u = str(req.url)
    if "/api/2/events" in u and req.url.host == "events.example.edu":
        return httpx.Response(200, json=LOCALIST)
    if "/live/json/events" in u and req.url.host == "events.brown.example":
        return httpx.Response(200, json=LIVEWHALE)
    if "/wp-json/tribe/events/v1/events" in u:
        return httpx.Response(200, json=TRIBE) if "school-tribe" in u else httpx.Response(404, text="no")
    if u.startswith("https://ics-only.example.edu"):
        if u.rstrip("/").endswith("ics-only.example.edu/events") or u.endswith("/events/"):
            return httpx.Response(200, text='<a href="webcal://ics-only.example.edu/calendar.ics">Subscribe</a>')
        return httpx.Response(404, text="no")
    return httpx.Response(404, text="nothing here")


def fake_client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def main():
    events.client = fake_client
    checks = {}
    db.init()

    loc = events.fetch_feed("https://events.example.edu/api/2/events?type[]=9")[0]
    checks["Localist: reads title, time, place and filters"] = loc[0]["title"] == "Cardinal Careers: Meet the Employers" \
        and abs(loc[0]["start_ts"] - (NOW + 3 * 86400)) < 5 and "Career Development" in loc[0]["tags"]
    lw = events.fetch_feed("https://events.brown.example/live/json/events/max/400")[0]
    checks["LiveWhale: unescapes titles and keeps event types as tags"] = lw[0]["title"] == "Startup Founders Panel & Mixer" \
        and "Entrepreneurship" in lw[0]["tags"]
    tr = events.fetch_feed("https://school-tribe.example.ca/wp-json/tribe/events/v1/events")[0]
    checks["The Events Calendar: local times and the venue's city"] = tr[0]["title"] == "Engineering Career Fair" \
        and "Vancouver" in tr[0]["location"]

    campus = {"grp": "US universities", "career_only": 1}
    keep = lambda e, typed=False: events._keep({**e, "kind": events.classify(e["title"], e["description"])}, campus, typed)  # noqa: E731
    checks["campus filter keeps career fairs and founder panels"] = keep(tr[0]) and keep(lw[0])
    checks["...drops concerts, staff meetings and MBA info sessions"] = not keep(lw[1]) and not keep(tr[1]) \
        and not keep(loc[1], typed=True)
    checks["...but a school's own career event type is trusted"] = keep(loc[0], typed=True)
    checks["'International' no longer counts as 'intern'"] = not events.CAREER_RE.search("International Food Fair") \
        and events.CAREER_RE.search("Summer internships")

    checks["a pasted Localist calendar page resolves to its API"] = events.resolve_feed(
        "https://events.example.edu/calendar/day") == "https://events.example.edu/api/2/events"
    checks["...an .ics link on the page is found"] = events.resolve_feed(
        "https://ics-only.example.edu/events") == "https://ics-only.example.edu/calendar.ics"
    checks["...and readable links are left alone"] = events.resolve_feed("https://lu.ma/toronto") == "https://lu.ma/toronto"

    now = db.now()
    with db.tx() as c:
        c.execute("INSERT INTO event_feeds (id, url, label, career_only, created_at, grp, region) VALUES"
                  " (1, 'https://events.brown.example/live/json/events/max/400', 'Brown', 1, ?, 'US universities', 'Providence')", (now,))
        c.execute("INSERT INTO event_feeds (id, url, label, career_only, created_at, grp) VALUES"
                  " (2, 'https://mlh.example/seasons', 'MLH hackathons', 1, ?, 'Hackathons')", (now,))
    events._SITE_FEEDS.append((__import__("re").compile(r"https://mlh\.example/"), lambda c, u: [
        {"uid": "m1", "title": "DeltaHacks", "start_ts": NOW + 100 * 86400, "end_ts": NOW + 101 * 86400, "all_day": True,
         "location": "Hamilton, ON, CA", "url": "https://www.deltahacks.com/", "description": "MLH", "kind": "hackathon"},
        {"uid": "m2", "title": "Cal Hacks 13.0", "start_ts": NOW + 14 * 86400, "end_ts": NOW + 16 * 86400, "all_day": True,
         "location": "San Francisco, CA, US", "url": "https://calhacks.io/", "description": "MLH", "kind": "hackathon"},
        {"uid": "m3", "title": "Tiny Hacks", "start_ts": NOW + 20 * 86400, "end_ts": None, "all_day": True,
         "location": "Online", "url": "", "description": "", "kind": "hackathon"}], "MLH hackathons"))
    new, status = events.sync_feeds()
    stored = db.rows("SELECT title, region, feed_id FROM events ORDER BY start_ts")
    titles = [r["title"] for r in stored]
    checks["sync keeps the founder panel and drops the concert"] = "Startup Founders Panel & Mixer" in titles \
        and "Chamber Orchestra Concert" not in titles
    checks["a daily drop-in shows its next two dates, not ten"] = titles.count("Career Drop-in Hours") == 2
    checks["campus events take the school's city when the room says nothing"] = all(
        r["region"] == "Providence" for r in stored if r["feed_id"] == 1)
    checks["hackathons announced 3+ months out are kept"] = "DeltaHacks" in titles
    checks["the campus calendar isn't re-read within 12 hours"] = "https://events.brown.example/live/json/events/max/400" \
        not in events.sync_feeds()[1] and "https://events.brown.example/live/json/events/max/400" in events.sync_feeds(force=True)[1]

    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES"
                        " ('T',1,1,'{}','{}')").lastrowid
    mine = events.for_person(pid, 120)
    checks["major hackathons get a Major badge"] = any(e["title"] == "Cal Hacks 13.0" and e["major"] == "Cal Hacks" for e in mine) \
        and any(e["title"] == "Tiny Hacks" and not e["major"] for e in mine)
    season = {h["name"]: h for h in events.hackathon_season(pid)}
    checks["the season list has dates for announced ones"] = season["DeltaHacks"]["event"] and \
        season["Cal Hacks"]["event"]["url"] == "https://calhacks.io/"
    checks["...and 'usually <month>' for the rest"] = season["Hack the North"]["event"] is None and season["Hack the North"]["when"] == "September"
    order = [h["name"] for h in events.hackathon_season(pid)]
    checks["...soonest announced first"] = order.index("Cal Hacks") < order.index("DeltaHacks") < order.index("Hack the North")

    checks["new regions: Ithaca, Research Triangle, London ON, Hamilton, Victoria"] = [
        events.event_region(x) for x in ("Ithaca, NY", "Durham, NC", "London, ON", "Hamilton, ON", "Victoria, BC")] == [
        "Ithaca", "Research Triangle", "London ON", "Hamilton", "Victoria"]
    checks["Durham Region (near Toronto) isn't Durham, NC"] = events.event_region("Oshawa, Durham Region, ON") == "Toronto / GTA"
    checks["near Waterloo includes Toronto, Hamilton and London"] = {"Toronto / GTA", "Hamilton", "London ON"} <= events.NEARBY["Waterloo Region"]

    defaults = store.default_event_feeds()
    groups = {g for _, _, g, _ in defaults}
    checks["everyone gets universities in Canada and the US, Toronto and hackathons"] = {
        "Canadian universities", "US universities", "Toronto & Waterloo", "Hackathons", "Cities"} <= groups
    checks["30+ universities, all with a region"] = sum(g.endswith("universities") for _, _, g, _ in defaults) >= 30 and all(
        r for _, _, g, r in defaults if g.endswith("universities"))
    checks["40 major hackathons, each with a site and a usual month"] = len(events.major_hackathons()) >= 40 and all(
        h["site"].startswith("https://") and h["when"] for h in events.major_hackathons())

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
