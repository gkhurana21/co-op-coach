"""Tracks (Jobs → Matches | your tracks | ＋ Tracks): the catalog validates; which postings are in which track (an "AI
Sales Intern" isn't ML & AI, banks apart from other large employers, a niche's own firm list and a firm taken off it,
senior roles never); default pins and edited pins; follows alerting; every header and ＋ Tracks count equal to the list
it opens; your own tracks and found ones (folded into a shipped one, they still open); older niche links; the API, the
lock and another person's tracks. Then tracks.js in a fake DOM: a slow answer can't overwrite the track someone moved to,
and the Market's words match its numbers.

Run: .venv/bin/python -m tests.test_tracks
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-tracks-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent", COOPCOACH_SYNC_PROCESSES="1")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, auth, companies, db, market, niches, server, store, track_discovery, tracks  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}
DAY = 86400
DESC = ("About the role\nYou'll build our product.\nRequirements:\n- Python and SQL\n- Git\nNice to have:\n- Docker\n"
        "Pay: $30 - $40 per hour\n") * 3


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def posting(company, title, n=0, locations=("Toronto, ON, Canada",), desc=DESC):
    raw = {"source": "greenhouse:x", "company": company, "title": title, "url": f"https://x.test/{company}/{title}/{n}",
           "locations": list(locations), "description": desc}
    j = store.normalize(raw)
    store.upsert_jobs([j], db.now())
    return j["id"]


def new_profile(name, prefs=None, profile=None):
    now = db.now()
    with db.tx() as c:
        return c.execute("INSERT INTO profiles (name, profile_json, prefs_json, created_at, updated_at) VALUES"
                         " (?,?,?,?,?)", (name, db.dumps(profile or {"skills": ["Python"]}), db.dumps(prefs or {}),
                                          now, now)).lastrowid


def in_track(pid, tid, jid):
    return jid in (tracks.ids(pid, tid) or set())


def main():
    db.init()
    server._sync_in_background = lambda *a, **k: None
    real = (companies.yc_all, companies.yc_lookup)
    companies.yc_all = lambda: []
    companies.yc_lookup = lambda name: None
    try:
        run()
    finally:
        companies.yc_all, companies.yc_lookup = real
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


def run():
    catalog()
    pid = new_profile("Sam", {"categories": ["Software", "Quant"]})
    ids = {
        "swe": posting("Acme Analytics", "Software Engineering Intern"),
        "ml": posting("Acme Analytics", "Machine Learning Intern"),
        "ai_sales": posting("Acme Analytics", "AI Sales Intern"),
        "data": posting("Acme Analytics", "Data Scientist Intern"),
        "rbc": posting("Royal Bank of Canada", "Business Analyst Co-op"),
        "walmart": posting("Walmart", "Supply Chain Analyst Intern"),
        "js": posting("Jane Street", "Quantitative Trader Intern"),
        "senior": posting("Acme Analytics", "Senior Software Engineer"),
        "google": posting("Google", "Software Engineer Intern", locations=("Mountain View, CA, USA",)),
    }
    for i in range(3):
        posting("Lumen Robotics", f"Robotics Software Intern {i}", i)
    store.invalidate_cache()
    membership(pid, ids)
    pins(pid)
    follows(pid, ids)
    counts_match(pid)
    yours_and_found(pid, ids)
    api(pid)
    fake_dom(pid)


def catalog():
    shipped = tracks._shipped()
    check("tracks.yaml: every track has a name, and its patterns compile", shipped and all(t["name"] for t in shipped.values()))
    check("...each employer niche is a niche that ships", all(
        niches._shipped(t["niche"]) for t in shipped.values() if t.get("niche")))
    check("...role tracks cover each role category", set(tracks.CATEGORY_TRACKS.values()) <= set(shipped))
    check("...banks and other large employers split the one big kind of company",
          shipped["co:banks"]["names"] is not None and shipped["co:corporate"]["not_names"] is not None)


def membership(pid, ids):
    check("a software internship is in Software engineering", in_track(pid, "role:software", ids["swe"]))
    check("a machine learning internship is in ML & AI", in_track(pid, "role:ml", ids["ml"]))
    check("an AI Sales Intern isn't ML & AI", not in_track(pid, "role:ml", ids["ai_sales"])
          and in_track(pid, "role:business", ids["ai_sales"]))
    check("a data scientist internship is in Data", in_track(pid, "role:data", ids["data"]))
    check("a bank's co-op is in Banks & financial firms, not Other large employers",
          in_track(pid, "co:banks", ids["rbc"]) and not in_track(pid, "co:corporate", ids["rbc"]))
    check("Walmart is in Other large employers, not Banks", in_track(pid, "co:corporate", ids["walmart"])
          and not in_track(pid, "co:banks", ids["walmart"]))
    check("a trading firm's internship is in Trading firms & funds (the quant niche's firm list) and Quant roles",
          in_track(pid, "co:quant-firms", ids["js"]) and in_track(pid, "role:quant", ids["js"]))
    check("Big Tech is by kind of company", in_track(pid, "co:bigtech", ids["google"]))
    check("a senior role is in no track", not any(ids["senior"] in v for v in tracks.index(pid).values()))
    niches.set_firm(pid, "quant", niches._norm_key("Jane Street"), "hidden")
    check("a firm taken off the niche's list drops out of its track", not in_track(pid, "co:quant-firms", ids["js"])
          and in_track(pid, "role:quant", ids["js"]))
    niches.set_firm(pid, "quant", niches._norm_key("Jane Street"), "shown")
    check("...and comes back", in_track(pid, "co:quant-firms", ids["js"]))
    check("an older niche link opens its track", tracks.get(pid, "niche:quant")["id"] == "co:quant-firms"
          and tracks.get(pid, "niche:waterloo-toronto")["id"] == "co:startups")


def pins(pid):
    d = tracks.pins(pid)
    check("until someone edits them, pins come from their targets (Software, Quant) and add trading firms and startups",
          d[:2] == ["role:software", "role:quant"] and "co:quant-firms" in d and "co:startups" in d
          and len(d) <= tracks.MAX_DEFAULTS)
    other = new_profile("Robin")
    check("someone with no targets still gets two", len(tracks.pins(other)) >= 2)
    r = tracks.set_pins(pid, ["co:bigtech", "role:data", "co:bigtech"])
    check("edited pins are kept in order, once each", r["ok"] and tracks.pins(pid) == ["co:bigtech", "role:data"])
    with db.tx() as c:
        c.execute("UPDATE profiles SET prefs_json=? WHERE id=?", (db.dumps({"categories": ["Hardware"]}), pid))
    check("...and stop following the defaults", tracks.pins(pid) == ["co:bigtech", "role:data"])
    check("a track that doesn't exist can't be pinned", tracks.set_pins(pid, ["role:nope"]).get("error"))
    check("at most six", tracks.set_pins(pid, [f"role:{k}" for k in ("software", "ml", "data", "quant", "hardware",
                                                                     "embedded", "infra")]).get("error"))
    check("pin and unpin one", tracks.pin(pid, "role:quant")["pins"] == ["co:bigtech", "role:data", "role:quant"]
          and tracks.pin(pid, "co:bigtech", on=False)["pins"] == ["role:data", "role:quant"])
    check("an empty bar stays empty", tracks.set_pins(pid, [])["ok"] and tracks.pins(pid) == [])
    tracks.set_pins(pid, ["role:software", "co:quant-firms"])


def follows(pid, ids):
    check("nothing followed, no matcher", tracks.follow_matcher(pid) is None)
    tracks.follow(pid, "role:data")
    jobs = {j["id"]: j for j in store.scored_jobs(pid)}
    fm = tracks.follow_matcher(pid)
    check("following a role track alerts on its postings, and only those", fm.firm(jobs[ids["data"]])
          and not fm.firm(jobs[ids["walmart"]]))
    r = tracks.follow(pid, "co:quant-firms")
    check("following a niche track follows the niche (its firms' boards join the sync)",
          r["following"] and "quant" in niches.followed(pid) and "co:quant-firms" in tracks.follows(pid))
    check("...and its firms alert", tracks.follow_matcher(pid).firm(jobs[ids["js"]]))
    tracks.follow(pid, "role:data", on=False)
    tracks.follow(pid, "co:quant-firms", on=False)
    check("unfollowing stops both", tracks.follow_matcher(pid) is None and not niches.followed(pid))


def counts_match(pid):
    for tid in ("role:software", "co:quant-firms", "co:banks"):
        h = tracks.header(pid, tid)
        check(f"{tid}: the header's counts are the lists they open",
              h["open"] == store.list_jobs(pid, track=tid, show_ineligible=True)["total"]
              and h["open_to_you"] == store.list_jobs(pid, track=tid)["total"]
              and h["new_week"] == store.list_jobs(pid, track=tid, since_days=7)["total"] and h["open"] > 0)
    b = tracks.browse(pid)
    card = next(c for c in b["roles"] if c["id"] == "role:software")
    check("＋ Tracks: a card's count is its list", card["open_to_you"] == store.list_jobs(pid, track="role:software")["total"])
    check("＋ Tracks groups the pinned ones first", [c["id"] for c in b["pins"]] == ["role:software", "co:quant-firms"]
          and b["roles"] and b["employers"])
    co = tracks.companies(pid, "role:software")
    acme = next(r for r in co["rows"] if r["name"] == "Acme Analytics")
    check("Companies: a company's 'open to you' is the postings its Postings → opens",
          acme["open_to_you"] == store.list_jobs(pid, track="role:software", facet=acme["facet"])["total"])
    q = tracks.companies(pid, "co:quant-firms", chip="open")
    js = next(r for r in q["rows"] if r["name"] == "Jane Street")
    check("...the same for a niche firm, under every name it posts as", js["open_to_you"] == store.list_jobs(
        pid, track="co:quant-firms", facet=js["facet"])["total"] and js["facet"].startswith("firm:"))
    check("...hiring first, with segments to pick from", q["rows"][0]["open"] > 0 and len(q["segments"]) >= 1)
    check("an unknown track says so instead of listing everything", store.list_jobs(pid, track="role:nope").get(
        "track_unknown"))


def yours_and_found(pid, ids):
    made = tracks.create(pid, "Robotics startups")
    check("starting your own track makes a niche and pins it", made["ok"] and made["id"].startswith("co:custom-")
          and made["id"] in tracks.pins(pid))
    niches.add_firm(pid, made["niche"], "Lumen Robotics", segment="yours")
    check("...its firms' postings are in it", len(tracks.ids(pid, made["id"])) == 3)
    check("...and its own segment shows in Companies", tracks.companies(pid, made["id"])["rows"][0]["name"]
          == "Lumen Robotics")
    other = new_profile("Kai")
    check("another person can't open it", tracks.get(other, made["id"]) is None
          and store.list_jobs(other, track=made["id"]).get("track_unknown"))
    check("a shipped track can't be deleted", tracks.delete(pid, "role:software").get("error"))
    check("yours can, and it leaves the bar", tracks.delete(pid, made["id"]).get("ok")
          and made["id"] not in tracks.pins(pid))
    now = db.now()
    with db.tx() as c:
        c.execute("INSERT INTO tracks_discovered (id, label, phrases, status, trend, stats, first_seen, last_seen)"
                  " VALUES (?,?,?,?,?,?,?,?)", ("quantitative", "Quantitative Trader", db.dumps(["quantitative"]),
                                                 "active", "rising", db.dumps({"open": 9, "companies": 6, "pace": 3}),
                                                 now, now))
    track_discovery.REVISION += 1
    check("a found track is a track, with the postings its phrases name (a company's own name never counts)",
          "auto:quantitative" in {t["id"] for t in tracks.all_tracks(pid)}
          and tracks.ids(pid, "auto:quantitative") == {ids["js"]})
    check("...listed under New & rising, with its card", any(c["id"] == "auto:quantitative" for c in tracks.browse(pid)["rising"])
          and tracks.get(pid, "auto:quantitative")["blurb"].startswith("Rising · Quantitative Trader · 9 open at 6 companies"))
    tracks.pin(pid, "auto:quantitative")
    with db.tx() as c:
        c.execute("UPDATE tracks_discovered SET folded_into='role:quant', status='faded' WHERE id='quantitative'")
    track_discovery.REVISION += 1
    check("folded into a shipped track, it opens that one and its pin moves there",
          tracks.get(pid, "auto:quantitative")["id"] == "role:quant" and "role:quant" in tracks.pins(pid)
          and "auto:quantitative" not in {t["id"] for t in tracks.all_tracks(pid)})


def api(pid):
    with TestClient(app, base_url="http://localhost") as cl:
        page = cl.get("/")
        srcs = re.findall(r'<script src="/static/([^"]+)"', page.text)
        check("the page loads, every script on it versioned (tracks.js and fit.js, no explore.js)",
              page.status_code == 200 and srcs and all("?v=" in x for x in srcs)
              and any(x.startswith("tracks.js?v=") for x in srcs) and not any("explore.js" in x for x in srcs))
        b = cl.get(f"/api/tracks?profile_id={pid}").json()
        check("GET /api/tracks: the groups", {"pins", "suggested", "rising", "roles", "employers", "yours",
                                              "more_roles", "starters"} <= set(b))
        h = cl.get(f"/api/tracks/{'role:software'}?profile_id={pid}").json()
        check("GET a track: header, counts and what it can do", h["name"] == "Software engineering"
              and {"open", "open_to_you", "new_week", "pinned", "following", "can"} <= set(h))
        check("...an old niche link answers with the track it is", cl.get(
            f"/api/tracks/niche:quant?profile_id={pid}").json()["id"] == "co:quant-firms")
        check("...an unknown one is a 404", cl.get(f"/api/tracks/role:nope?profile_id={pid}").status_code == 404)
        r = cl.put("/api/tracks/pins", json={"profile_id": pid, "pins": ["role:data", "co:banks"]})
        check("PUT pins, then the bar shows them", r.status_code == 200 and [t["id"] for t in cl.get(
            f"/api/tracks/bar?profile_id={pid}").json()["pins"]] == ["role:data", "co:banks"])
        check("...pins that aren't a list are a 400", cl.put("/api/tracks/pins", json={"profile_id": pid, "pins": "x"})
              .status_code == 400)
        f = cl.put("/api/tracks/role:data/follow", json={"profile_id": pid, "on": True}).json()
        check("PUT follow", f["following"] and "role:data" in tracks.follows(pid))
        co = cl.get(f"/api/tracks/co:banks/companies?profile_id={pid}").json()
        check("GET companies", co["rows"] and co["rows"][0]["name"] == "Royal Bank of Canada")
        m = cl.get(f"/api/tracks/role:software/market?profile_id={pid}").json()
        check("GET market: at a glance, skills, where & when, pay, fit", {"glance", "skills", "where", "history", "fit"}
              <= set(m) and m["described"] >= 1)
        j = cl.get(f"/api/jobs?profile_id={pid}&track=role:software&facet=skill:Python&population=market").json()
        check("GET /api/jobs with a track and a facet", j["total"] == next(
            s["count"] for s in m["skills"] if s["skill"] == "Python"))
        mk = agent.call(pid, "my_market", {"track": "banks"}, source="test")
        check("chat: my_market answers for a track by name", mk.get("market") == "Banks & financial firms"
              and mk["open_to_you"] >= 1 and "top_skills" in mk and "pay" in mk)
        check("...and says which tracks exist when it can't find one", "tracks" in agent.call(
            pid, "my_market", {"track": "zzz"}, source="test"))
        auth.set_passcode(pid, "maple-1234")
        try:
            check("a locked person's tracks stay locked", cl.get(f"/api/tracks?profile_id={pid}").status_code == 423
                  and cl.get(f"/api/tracks/role:software/market?profile_id={pid}").status_code == 423)
        finally:
            auth.clear_passcode(pid)


# tracks.js in a tiny fake DOM (tests/dom_harness.js): the API answers in an order the test picks
TRACKS_DOM_JS = r"""
const { load, answer, settle, html } = require(process.argv[2]);
const MOCKS = JSON.parse(require("fs").readFileSync(process.argv[4], "utf8"));
const ctx = load(process.argv[3], { tab: "track" });
const out = {};
(async () => {
  const run = (code) => ctx.run(code);
  // 1. a track is slow; they open another before it answers
  ctx.store.track = "role:software"; ctx.store["tsec:role:software"] = "market"; ctx.store["tsec:co:banks"] = "market";
  const slow = run("renderTrack()");
  ctx.store.track = "co:banks";
  const fast = run("renderTrack()");
  answer(ctx, "/api/tracks/co%3Abanks", MOCKS.banks, true); await settle();
  answer(ctx, "/api/tracks/co%3Abanks/market", MOCKS.market); await fast; await settle();
  answer(ctx, "/api/tracks/role%3Asoftware", MOCKS.software); await slow; await settle();
  out.late_track_leaves_the_new_one_alone = html(ctx, "#t-head").includes(ctx.esc(MOCKS.banks.name)) && !html(ctx, "#t-head").includes(ctx.esc(MOCKS.software.name));
  // 2. the Market's words match its numbers
  const body = html(ctx, "#t-body");
  out.market_says_how_many_have_full_text = body.includes(`<b>${MOCKS.market.described.toLocaleString()}</b> have a full description`);
  out.pay_copy_is_honest = MOCKS.market.glance.pay.n ? body.includes("Nothing converted, nothing guessed") : body.includes("state pay yet");
  out.every_skill_opens_its_postings = MOCKS.market.skills.slice(0, 18).every((s) => body.includes(`data-facet="skill:${s.skill}"`));
  // 3. ＋ Tracks: the groups, and Pin is off when the bar is full
  ctx.S.tab = "tracks"; ctx.S.trackBar = MOCKS.full_bar; ctx.S.trackBarPid = 1; ctx.S.trackMax = 6;
  const list = run("renderTracks()");
  answer(ctx, "/api/tracks", MOCKS.browse); await list; await settle();
  const main = html(ctx, "#main");
  out.tracks_list_has_its_groups = main.includes("Roles") && main.includes("Employers") && main.includes("Start your own");
  out.pin_is_off_when_the_bar_is_full = main.includes("Your Jobs bar is full");
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ ...out, harness_error: String(e && e.stack || e) })); });
"""


def fake_dom(pid):
    """Skipped when node isn't installed."""
    node = shutil.which("node")
    if not node:
        print("SKIP tracks.js in a fake DOM: node isn't installed")
        return
    tracks.set_pins(pid, ["role:software"])
    mocks = {"software": tracks.header(pid, "role:software"), "banks": tracks.header(pid, "co:banks"),
             "market": market.track_market(pid, "co:banks"), "browse": tracks.browse(pid),
             "full_bar": [{"id": f"role:{i}", "name": str(i), "icon": ""} for i in range(6)]}
    here = os.path.dirname(os.path.abspath(__file__))
    tracks_js = os.path.join(os.path.dirname(tracks.__file__), "static", "tracks.js")
    with tempfile.TemporaryDirectory() as tmp:
        script, data = os.path.join(tmp, "tracks_dom.js"), os.path.join(tmp, "mocks.json")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(TRACKS_DOM_JS)
        with open(data, "w", encoding="utf-8") as fh:
            json.dump(mocks, fh)
        run = subprocess.run([node, script, os.path.join(here, "dom_harness.js"), tracks_js, data],
                             capture_output=True, text=True, timeout=120)
    try:
        got = json.loads(run.stdout)
    except ValueError:
        got = {"harness_error": (run.stdout + run.stderr)[-800:]}
    if got.get("harness_error"):
        print(got["harness_error"])
    check("tracks.js in a fake DOM: every step ran", len(got) == 6 and "harness_error" not in got)
    for k, v in got.items():
        check("tracks.js in a fake DOM: " + re.sub("_", " ", k), v is True)


if __name__ == "__main__":
    main()
