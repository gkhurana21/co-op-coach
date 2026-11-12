"""Do what docs/FEATURES.md says to try, in a real browser, on a private copy of the fictional demo, and check what comes back.

    .venv/bin/python -m tools.walkthrough [--only tracker,calendar] [--out report.json]

Nothing real is touched: a copy of data-demo/ on its own port, and the browser can't reach anything else. Whatever would open a
window, send something or sign in stays out (the apply session and inboxes have their own suites).
"""

import argparse
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8796
URL = f"http://127.0.0.1:{PORT}"
results = []


def step(name: str):
    def wrap(fn):
        STEPS.append((name, fn))
        return fn
    return wrap


STEPS: list = []


def clear(p):
    p.evaluate("document.querySelectorAll('.milestone-bg').forEach((e) => e.remove()); document.querySelector('#modal-root').innerHTML = ''")


def go(p, view: str, wait: str = "#main *"):
    clear(p)
    p.evaluate("(v) => goView(v)", view)
    p.wait_for_selector(wait, timeout=20000)
    p.wait_for_timeout(500)
    clear(p)


def need(ok, msg: str):
    if not ok:
        raise AssertionError(msg)


def api(p, path: str, method: str = "GET", body=None):
    return p.evaluate("""async ([path, method, body]) => { const r = await fetch(path, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
      return {status: r.status, json: await r.json().catch(() => null)}; }""", [path, method, body])


@step("Lean (what a new person sees): four tabs, Jobs is Matches, Calendar is yours, and Settings brings the rest back")
def lean(p, pid):
    api(p, "/api/features", "PUT", {"mode": "lean"})
    p.evaluate("loadState().then(() => render())")
    p.wait_for_timeout(800)
    tabs = p.evaluate("[...document.querySelectorAll('#tabs [data-group]')].filter((b) => !b.classList.contains('hidden')).map((b) => b.textContent.trim().split(' ')[0])")
    need(tabs == ["Today", "Jobs", "Applications", "Calendar"], f"lean tabs: {tabs}")
    go(p, "matches", ".job, .empty")
    need(p.evaluate("!document.querySelector('a[href*=\"export/xlsx\"]') && !document.querySelector('#subnav button')"), "Excel or track tabs showing in lean")
    go(p, "events", "#ev-list *")
    need(p.evaluate("document.querySelector('#main h2').textContent") == "Your calendar", "Calendar title")
    p.evaluate("openSettings('account')")
    p.wait_for_selector("[data-features='all']")
    p.click("[data-features='all']")
    p.wait_for_timeout(1000)
    tabs = p.evaluate("[...document.querySelectorAll('#tabs [data-group]')].filter((b) => !b.classList.contains('hidden')).map((b) => b.textContent.trim().split(' ')[0])")
    need(tabs == ["Today", "Jobs", "Applications", "People", "Events"], f"everything tabs: {tabs}")


@step("Matches: a posting's details say why and who can apply")
def matches(p, pid):
    go(p, "matches", "text=Details")
    p.get_by_text("Details", exact=True).first.click()
    p.wait_for_timeout(600)
    text = p.locator("#main").inner_text()
    need("%" in text or "match" in text.lower(), "no score on the page")
    need(any(w in text for w in ("Why", "Skills", "role", "Open", "open to")), "no reasons shown after opening Details")


@step("Matches: filters narrow the list, and search finds by company")
def filters(p, pid):
    all_ = api(p, f"/api/jobs?profile_id={pid}&limit=200")["json"]
    canada = api(p, f"/api/jobs?profile_id={pid}&limit=200&region=Canada")["json"]
    need(0 < canada["total"] < all_["total"], f"Canada filter: {canada['total']} of {all_['total']}")
    one = all_["jobs"][0]["company"]
    found = api(p, f"/api/jobs?profile_id={pid}&limit=50&q={one}")["json"]
    need(found["jobs"] and all(one.lower() in (j["company"] + j["title"]).lower() for j in found["jobs"][:5]), f"search for {one}")


@step("Tracks: a pin shows in the Jobs bar; unpin takes it out")
def pin(p, pid):
    r = api(p, f"/api/tracks?profile_id={pid}")["json"]
    pinned = [x["id"] for x in r["pins"]]
    pick = next(c["id"] for c in r["employers"] if c["id"] not in pinned)
    freed = pinned[-1] if len(pinned) >= r.get("max_pins", 6) else None  # the demo copy may already be full: make room
    if freed:
        api(p, f"/api/tracks/{freed}/pin", "PUT", {"profile_id": pid, "on": False})
    api(p, f"/api/tracks/{pick}/pin", "PUT", {"profile_id": pid, "on": True})
    need(pick in {x["id"] for x in api(p, f"/api/tracks/bar?profile_id={pid}")["json"]["pins"]}, f"{pick} not in the bar")
    api(p, f"/api/tracks/{pick}/pin", "PUT", {"profile_id": pid, "on": False})
    need(pick not in {x["id"] for x in api(p, f"/api/tracks/bar?profile_id={pid}")["json"]["pins"]}, f"{pick} still in the bar")
    if freed:
        api(p, f"/api/tracks/{freed}/pin", "PUT", {"profile_id": pid, "on": True})


@step("Tracks: every number on the Market opens a list of that size")
def market(p, pid):
    tid = "role:software"
    m = api(p, f"/api/tracks/{tid}/market?profile_id={pid}")["json"]
    bad = []
    open_to_you = api(p, f"/api/jobs?profile_id={pid}&track={tid}&population=market&limit=200")["json"]["total"]
    if open_to_you != m["glance"]["open_to_you"]:
        bad.append(("open to you", m["glance"]["open_to_you"], open_to_you))
    for s in m["skills"][:5]:
        facet = f"skill:{s['skill']}"
        got = api(p, f"/api/jobs?profile_id={pid}&track={tid}&facet={facet}&population=market&limit=200")["json"]
        if got["total"] != s["count"]:
            bad.append((s["skill"], s["count"], got["total"]))
    need(not bad, f"said vs listed: {bad}")


@step("Tracks: the page's own tabs draw")
def track_page(p, pid):
    go(p, "track:role:software", "#t-pin")
    for tab in ("Companies", "Market", "Postings"):
        p.get_by_text(tab, exact=True).first.click()
        p.wait_for_timeout(700)
        need(len(p.locator("#main").inner_text()) > 200, f"{tab} is empty")


@step("Autopilot: Prepare now puts applications in Ready to apply")
def autopilot(p, pid):
    before = api(p, f"/api/ready?profile_id={pid}")
    before_n = len((before["json"] or {}).get("items") or (before["json"] or {}).get("ready") or []) if before["status"] == 200 else None
    go(p, "autopilot", "#ap-run")
    p.click("#ap-run")
    end = time.time() + 150
    while time.time() < end:
        st = api(p, f"/api/autopilot?profile_id={pid}")["json"]
        if not (st.get("state") or st.get("status") or {}).get("running") and time.time() > end - 145:
            break
        p.wait_for_timeout(1500)
    st = api(p, f"/api/autopilot?profile_id={pid}")["json"]
    last = st.get("last_run") or st.get("last") or {}
    need(not last.get("error"), f"the run failed: {last.get('error')}")
    need(last.get("queued", 0) >= 1, f"nothing prepared: {str(last)[:160]} (before: {before_n})")


@step("Autopilot: Preview picks explains itself")
def preview(p, pid):
    r = api(p, f"/api/autopilot/preview?profile_id={pid}")["json"]
    need(r["picks"] and all(x["why"] for x in r["picks"]), "picks without reasons")
    need(len({x["company"] for x in r["picks"]}) == len(r["picks"]), "two picks at one company")


def app_by_status(p, pid, company: str):
    return next((a for a in api(p, f"/api/applications?profile_id={pid}")["json"]["applications"] if a["company"] == company), None)


@step("Tracker: a pasted interview email moves the application and goes on the calendar")
def paste_email(p, pid):
    app = next(a for a in api(p, f"/api/applications?profile_id={pid}")["json"]["applications"] if a["status"] == "applied" and not a["job_id"].startswith("hack:"))
    when = (datetime.now() + timedelta(days=4)).replace(hour=14, minute=0)
    text = (f"From: Jamie Lee <jamie@{app['company'].lower().replace(' ', '')}.example>\nSubject: Interview invitation: {app['company']} {app['title']}\n\n"
            f"Hi Alex, thanks for applying to {app['company']} for the {app['title']} role. We'd like to invite you to an interview on "
            f"{when:%B %d, %Y} at 2:00 PM ET. Join here: https://zoom.us/j/123456789\n\nBest,\nJamie")
    go(p, "tracker", "#paste-email")
    p.click("#paste-email")
    p.fill("#e-text", text)
    p.locator("#email-form button.btn").first.click()
    p.wait_for_selector("#e-out b", timeout=15000)
    out = p.locator("#e-out").inner_text()
    now = app_by_status(p, pid, app["company"])
    need(now["status"] == "interviewing", f"status is {now['status']}: {out[:160]}")
    ev = api(p, f"/api/events?profile_id={pid}&days=90")["json"]
    mine = [e for e in ev["mine"] if app["company"] in (e.get("title") or "") or app["company"] in str(e.get("company"))]
    need(mine, f"nothing on the calendar for {app['company']}: {[e.get('title') for e in ev['mine']][:6]}")
    go(p, "events", "text=Month")
    p.get_by_text("Month", exact=True).first.click()
    p.wait_for_timeout(600)
    need(p.locator("#main").inner_text().strip(), "the month view is empty")


@step("Tracker: a rejection email closes it, and paste junk says so")
def rejection(p, pid):
    app = next(a for a in api(p, f"/api/applications?profile_id={pid}")["json"]["applications"] if a["status"] == "applied" and not a["job_id"].startswith("hack:"))
    r = api(p, "/api/applications/from-email", "POST", {"profile_id": pid, "text": f"Subject: Your application to {app['company']}\n\nThank you for your interest in {app['company']} for the {app['title']} role. Unfortunately we have decided not to move forward with your application."})
    need(r["status"] == 200, f"paste: {r}")
    need(app_by_status(p, pid, app["company"])["status"] == "rejected", "status didn't move")
    junk = api(p, "/api/applications/from-email", "POST", {"profile_id": pid, "text": "lunch tomorrow?"})
    need(junk["status"] in (200, 400), f"junk: {junk['status']}")


def path_page(p):
    go(p, "path", ".trail-sec")
    clear(p)


def test_node(p, scope: str):
    i = next(i for i, n in enumerate(n for x in p.evaluate("PATH.data.sections") for n in x["nodes"] if n["kind"] == "test") if n["scope"] == scope)
    node = p.locator(".node.test").nth(i)
    node.scroll_into_view_if_needed()
    node.click()


def clear_running_test(p, pid):
    t = api(p, f"/api/path/test?profile_id={pid}")["json"]["test"]
    if t:
        api(p, "/api/path/test/give-up", "POST", {"profile_id": pid, "test_id": t["id"]})


@step("Path: logging a try on the trail moves XP and the quests")
def path_log(p, pid):
    before = api(p, f"/api/path?profile_id={pid}")["json"]
    path_page(p)
    node = p.locator(".node.prob.here").first
    node.scroll_into_view_if_needed()
    node.click()
    p.click("#st-log")
    p.wait_for_selector("#ld-save")
    p.fill("#ld-min", "14")
    p.fill("#ld-notes", "hash map, then one pass")
    p.click("#ld-save")
    p.wait_for_timeout(900)
    after = api(p, f"/api/path?profile_id={pid}")["json"]
    need(after["level"]["xp"] > before["level"]["xp"], f"XP stayed at {after['level']['xp']}")
    need(after["coding"]["solved"] == before["coding"]["solved"] + 1, "the solve wasn't counted")
    need(any(q["done"] for q in after["quests"]), "no quest done")
    clear(p)


@step("Path: a miss comes back on the Review tab")
def path_miss(p, pid):
    path_page(p)
    p.click("#open-ladder")
    p.wait_for_selector("[data-try]")
    row = p.locator(".ladder-row:not(.solved)").first
    title = row.locator(".lr-main a").first.inner_text()
    row.locator("[data-try]").click()
    p.check('input[name="ld-res"][value="unsolved"]')
    p.click("#ld-save")
    p.wait_for_timeout(800)
    lad = api(p, f"/api/path/ladder?profile_id={pid}")["json"]
    tried = next(x for x in lad["problems"] if x["title"] == title.strip())
    need(tried["review_at"], f"{title} has no review date")
    clear(p)


@step("Path test: failing one says what to review, and the retake waits")
def path_test_fail(p, pid):
    clear_running_test(p, pid)
    path_page(p)
    test_node(p, "two-pointers")
    p.click("#tc-go")
    p.wait_for_selector(".test-row")
    rows = p.locator(".test-row")
    slugs = [rows.nth(i).get_attribute("data-p") for i in range(rows.count())]
    need(len(slugs) == 3, f"{len(slugs)} problems")
    need(not p.locator(".ok-tag:visible").count(), "LeetCode confirms showing with nothing linked")
    p.check(f'input[name="tr-{slugs[0]}"][value="hints"]')
    for s in slugs[1:]:
        p.check(f'input[name="tr-{s}"][value="solved"]')
    p.click("#tt-done")
    p.wait_for_selector("#tr-ok")
    text = p.locator("#modal-root").inner_text()
    need("Not this time" in text and "Review:" in text and "3 days" in text, f"result said: {text[:160]}")
    p.click("#tr-ok")
    p.wait_for_selector(".trail-sec")
    p.wait_for_timeout(400)
    clear(p)
    test_node(p, "two-pointers")
    p.wait_for_selector("#tc-no")
    need(not p.locator("#tc-go").count(), "it offered a retake straight away")
    need("retake" in p.locator("#modal-root").inner_text().lower(), "no retake date")
    clear(p)


@step("Path test: stopping early ends it, and the page is free again")
def path_test_stop(p, pid):
    clear_running_test(p, pid)
    path_page(p)
    test_node(p, "sliding-window")
    p.click("#tc-go")
    p.wait_for_selector(".test-row")
    p.on("dialog", lambda d: d.accept())
    p.click("#tt-stop")
    p.wait_for_timeout(900)
    need(api(p, f"/api/path/test?profile_id={pid}")["json"]["test"] is None, "still running")
    need(not p.locator(".test-banner").count(), "the banner's still there")
    clear(p)


@step("Path test: closing it keeps the clock, and the banner reopens it")
def path_test_close(p, pid):
    clear_running_test(p, pid)
    path_page(p)
    test_node(p, "stack")
    p.click("#tc-go")
    p.wait_for_selector(".test-row")
    p.click("#tt-x")
    p.wait_for_selector(".test-banner", timeout=8000)
    clear(p)
    p.click("#test-open")
    p.wait_for_selector(".test-row")
    clear(p)
    clear_running_test(p, pid)


@step("Path test: a checkpoint and the final start with the right size and clock")
def path_test_big(p, pid):
    for scope, n, least in (("c3", 5, 100 * 60), ("final", 10, 240 * 60)):
        clear_running_test(p, pid)
        path_page(p)
        test_node(p, scope)
        p.click("#tc-go")
        p.wait_for_selector(".test-row")
        need(p.locator(".test-row").count() == n, f"{scope}: {p.locator('.test-row').count()} problems")
        t = api(p, f"/api/path/test?profile_id={pid}")["json"]["test"]
        need(t["seconds_left"] >= least - 120, f"{scope}: {t['seconds_left']} s on the clock")
        clear(p)
    clear_running_test(p, pid)


@step("Path: linking a real public LeetCode profile logs only the 150, then unlinks")
def leetcode_real(p, pid):
    path_page(p)
    before = api(p, f"/api/path/ladder?profile_id={pid}")["json"]["solved"]
    p.locator(".year").scroll_into_view_if_needed()
    p.fill("#lc-name", "hiepit")
    p.click("#lc-go")
    p.wait_for_selector("#lc-off", timeout=30000)
    lad = api(p, f"/api/path/ladder?profile_id={pid}")["json"]
    known = {x["slug"] for x in lad["problems"]}
    got = api(p, "/api/path/leetcode", "POST", {"profile_id": pid, "username": "hiepit"})["json"]
    need(got["linked"] == "hiepit", f"link said: {got}")
    need(got["seen"] > 0, "LeetCode showed no solves for a profile that has them")
    print(f"     (LeetCode showed {got['seen']} solves; {got['new']} new ones from the 150 on this pass, {lad['solved'] - before} logged on linking)")
    need(lad["solved"] >= before and all(s in known for s in [x["slug"] for x in lad["problems"]]), "something outside the 150 got in")
    p.wait_for_timeout(600)
    clear(p)  # (new solves can bring a milestone card up)
    p.click("#lc-off")
    p.wait_for_selector("#lc-name", timeout=8000)
    need(api(p, f"/api/path?profile_id={pid}")["json"]["leetcode_user"] == "", "still linked")
    bad = api(p, "/api/path/leetcode", "POST", {"profile_id": pid, "username": "no_such_user_zz91"})
    need(bad["status"] == 400 or "error" in str(bad["json"]), f"an unknown user: {bad}")


@step("Path: a problem can be starred for a company and shows on the list")
def path_star(p, pid):
    lad = api(p, f"/api/path/ladder?profile_id={pid}")["json"]
    slug = lad["problems"][20]["slug"]
    r = api(p, "/api/path/star", "POST", {"profile_id": pid, "slug": slug, "kind": "company", "tag": "Jane Street"})
    need(r["status"] == 200, f"star: {r}")
    lists = api(p, f"/api/path/ladder?profile_id={pid}")["json"]["lists"]
    need(any("Jane Street" in x["label"] and slug in x["slugs"] for x in lists), f"lists: {lists[:2]}")


@step("A new student: add yourself, upload a resume, and matches are yours")
def new_student(p, pid):
    resume = httpx.get(f"{URL}/api/resume/export?profile_id={pid}&fmt=pdf", headers={"Host": "localhost"}, timeout=60).content
    need(resume[:4] == b"%PDF", "no PDF to upload")
    clear(p)
    p.click("#add-profile")
    p.wait_for_selector("#modal-root input", timeout=5000)
    p.fill('#modal-root input[name="name"]', "Jamie Newstart")
    p.fill('#modal-root input[name="pc"]', "jamie-1234")
    p.fill('#modal-root input[name="pc2"]', "jamie-1234")
    p.press('#modal-root input[name="pc2"]', "Enter")
    p.wait_for_function("S.app && S.app.profiles.some((x) => x.name === 'Jamie Newstart')", timeout=10000)
    new = p.evaluate("S.app.profiles.find((x) => x.name === 'Jamie Newstart').id")
    p.wait_for_function("(id) => S.pid === id", arg=new, timeout=10000)
    empty = api(p, f"/api/path?profile_id={new}")["json"]
    need(empty["coding"]["solved"] == 0 and empty["level"]["xp"] == 0, "a new person isn't starting from nothing")
    before = api(p, f"/api/jobs?profile_id={new}&limit=3")["json"]
    p.evaluate("openSettings && openSettings()")
    p.wait_for_selector("#file", state="attached", timeout=8000)
    p.set_input_files("#file", {"name": "Jamie-Resume.pdf", "mimeType": "application/pdf", "buffer": resume})
    p.wait_for_function("(id) => { const x = S.app.profiles.find((y) => y.id === id); return x && x.resume_filename; }", arg=new, timeout=30000)
    got = api(p, f"/api/profiles/{new}")
    prof = (got["json"] or {}).get("profile") or {} if got["status"] == 200 else {}
    after = api(p, f"/api/jobs?profile_id={new}&limit=3")["json"]
    need(after["total"] > 0 and after["jobs"][0]["score"] > 0, "no matches after the resume")
    need(not prof or prof.get("skills"), f"no skills read from the resume: {str(prof)[:120]}")
    need(api(p, f"/api/path?profile_id={new}")["json"]["counts"]["resume"] == 1, "the Path doesn't know about the resume")
    need(before["total"] == after["total"] or after["jobs"][0]["id"], "matches went missing")
    other = api(p, f"/api/applications?profile_id={new}")["json"]
    need(not other.get("applications"), "someone else's applications showed up")
    clear(p)
    p.evaluate("(id) => { S.pid = id; }", pid)


@step("Settings: changing where you want to work changes your matches")
def settings_change(p, pid):
    ask = lambda: api(p, f"/api/jobs?profile_id={pid}&limit=5")["json"]["jobs"]  # noqa: E731
    state = api(p, "/api/state")["json"]
    prefs = next(x for x in state["profiles"] if x["id"] == pid).get("prefs") or {}
    keep = {k: prefs.get(k) for k in ("locations", "us_open")}
    r = api(p, f"/api/profiles/{pid}/prefs", "PUT", {"locations": ["Toronto"], "us_open": False})
    need(r["status"] == 200, f"saving: {r['status']}")
    top = ask()
    need(sum("Toronto" in " ".join(j["locations"]) for j in top) >= 3, f"Toronto isn't on top: {[j['locations'][:1] for j in top]}")
    api(p, f"/api/profiles/{pid}/prefs", "PUT", keep)
    back = api(p, "/api/state")["json"]
    need(next(x for x in back["profiles"] if x["id"] == pid)["prefs"].get("locations") == keep["locations"], "it didn't save back")


@step("Hackathon packages: Apply opens a draft to review")
def hackathon(p, pid):
    go(p, "events", "text=Hackathons")
    p.get_by_text("Hackathons", exact=True).first.click()
    p.wait_for_selector("[data-hack-apply]", timeout=10000)
    p.locator("[data-hack-apply]").first.click(timeout=5000)
    p.wait_for_timeout(1500)
    text = p.locator("#modal-root").inner_text()
    need(len(text) > 40, f"no draft: {text[:120]}")


@step("People: importing LinkedIn connections adds them, and search finds them")
def people(p, pid):
    before = api(p, f"/api/recruiters?profile_id={pid}")["json"]
    csv = "Notes:\n\nFirst Name,Last Name,URL,Email Address,Company,Position,Connected On\nTaylor,Quill,https://linkedin.com/in/tq,,Zentry Robotics,Recruiter,01 Jan 2026\n"
    go(p, "recruiters", "#rc-li")
    p.set_input_files("#rc-li-file", {"name": "Connections.csv", "mimeType": "text/csv", "buffer": csv.encode()})
    p.wait_for_timeout(1500)
    after = api(p, f"/api/recruiters?profile_id={pid}&q=Zentry")["json"]
    need("Zentry" in json.dumps(after) or "Quill" in json.dumps(after), f"not found after import: {str(after)[:160]} (before {len(json.dumps(before))})")


@step("Resume Studio: draws, exports, and Market fit agrees with the track's Market")
def resume(p, pid):
    go(p, "documents", "text=Resume Studio")
    need("Experience" in p.locator("#main").inner_text(), "no resume on the page")
    for fmt in ("pdf", "docx"):
        r = httpx.get(f"{URL}/api/resume/export?profile_id={pid}&fmt={fmt}", headers={"Host": "localhost"}, timeout=60)
        need(r.status_code == 200 and len(r.content) > 1000, f"{fmt}: status {r.status_code}, {len(r.content)} bytes")
    m = api(p, f"/api/tracks/role:software/market?profile_id={pid}")["json"]
    f = api(p, "/api/market/fit", "POST", {"profile_id": pid, "track": "role:software"})
    need(f["status"] == 200, f"fit: {f['status']}")
    need(f["json"]["coverage"] == m["fit"]["coverage"] and [x["skill"] for x in f["json"]["skills"]] == [x["skill"] for x in m["fit"]["skills"]],
         f"fit disagrees: {f['json']['coverage']} vs {m['fit']['coverage']}")


@step("Excel: the workbook opens with its sheets and rows")
def excel(p, pid):
    r = httpx.get(f"{URL}/api/export/xlsx?profile_id={pid}", headers={"Host": "localhost"}, timeout=60)
    need(r.status_code == 200, f"status {r.status_code}")
    wb = load_workbook(io.BytesIO(r.content))
    rows = {ws.title: ws.max_row for ws in wb}
    need(len(rows) >= 3 and sum(rows.values()) > 20, f"sheets: {rows}")


@step("Jarvis: the chat opens, and without an AI engine says how to get one")
def jarvis(p, pid):
    clear(p)
    p.click("#chat-fab")
    p.wait_for_selector("#chat-panel:not(.hidden)")
    box = p.locator("#chat-panel textarea, #chat-panel input[type=text]").first
    box.fill("How's my NeetCode?")
    box.press("Enter")
    p.wait_for_timeout(2500)
    text = p.locator("#chat-panel").inner_text()
    need(len(text) > 30, "no reply")
    p.click("#chat-fab") if p.locator("#chat-fab").is_visible() else None
    p.evaluate("document.querySelector('#chat-panel').classList.add('hidden')")


@step("Following a track sticks")
def follow(p, pid):
    r = api(p, "/api/tracks/co:quant-firms/follow", "PUT", {"profile_id": pid, "on": True})
    need(r["status"] == 200, f"follow: {r}")
    h = api(p, f"/api/tracks/co:quant-firms?profile_id={pid}")["json"]
    need(h.get("following"), f"not following: {str(h)[:120]}")
    api(p, "/api/tracks/co:quant-firms/follow", "PUT", {"profile_id": pid, "on": False})


@step("Passcode: locked means locked, on every page and API; the code opens it")
def passcode(p, pid):
    r = api(p, f"/api/profiles/{pid}/passcode", "POST", {"new": "test-code-1234"})
    need(r["status"] == 200, f"set: {r}")
    api(p, "/api/auth/lock", "POST", {"profile_id": pid})
    for path in (f"/api/jobs?profile_id={pid}", f"/api/path?profile_id={pid}", f"/api/applications?profile_id={pid}", f"/api/recruiters?profile_id={pid}"):
        got = api(p, path)["status"]
        need(got in (401, 403, 423), f"{path}: {got} while locked")
    wrong = api(p, "/api/auth/unlock", "POST", {"profile_id": pid, "passcode": "wrong-code-9"})
    need(wrong["status"] != 200, "a wrong code opened it")
    ok = api(p, "/api/auth/unlock", "POST", {"profile_id": pid, "passcode": "test-code-1234"})
    need(ok["status"] == 200, f"right code: {ok}")
    need(api(p, f"/api/jobs?profile_id={pid}")["status"] == 200, "still locked after the right code")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    src = ROOT / "data-demo" / "coopcoach.db"
    if not src.exists():
        raise SystemExit("No demo yet: run `python -m coopcoach demo` once first.")
    d = tempfile.mkdtemp(prefix="coopcoach-walk-")
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as a, sqlite3.connect(f"{d}/coopcoach.db") as b:
        a.backup(b)
    env = {**os.environ, "COOPCOACH_DATA": d, "COOPCOACH_DEMO": "1", "GROQ_API_KEY": "", "ANTHROPIC_API_KEY": "", "COOPCOACH_CLAUDE_BIN": "/nonexistent", "COOPCOACH_CODEX_BIN": "/nonexistent"}
    srv = subprocess.Popen([sys.executable, "-m", "coopcoach", "serve", "--port", str(PORT)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    only = [o for o in args.only.split(",") if o]
    try:
        for _ in range(300):
            try:
                if httpx.get(URL + "/api/state", timeout=3).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            ctx = br.new_context(viewport={"width": 1280, "height": 900})
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(URL) or r.request.resource_type in ("image", "font", "stylesheet") else r.abort())
            p = ctx.new_page()
            errors: list[str] = []
            p.on("pageerror", lambda e: errors.append(str(e)))
            p.goto(URL)
            p.wait_for_function("typeof S !== 'undefined' && S.app && S.booted", timeout=60000)
            pid = p.evaluate("S.pid")
            api(p, "/api/features", "PUT", {"mode": "all"})  # every page (the lean step checks lean on its own)
            p.evaluate("loadState().then(() => render())")
            for name, fn in STEPS:
                if only and not any(o.lower() in name.lower() for o in only):
                    continue
                t, errors[:] = time.time(), []
                try:
                    fn(p, pid)
                    ok, why = not errors, "; ".join(errors)[:200]
                except Exception as e:
                    ok, why = False, str(e).split("\n")[0][:260]
                results.append({"step": name, "ok": ok, "why": why, "seconds": round(time.time() - t, 1)})
                print(f"{'ok  ' if ok else 'FAIL'} {name:72} {time.time() - t:5.1f}s {why}", flush=True)
            br.close()
    finally:
        srv.terminate()
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} passed")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
