"""The form filler on real, live application pages, picked fresh from each company's public board.

    .venv/bin/python -m tools.live_forms [out_dir] [greenhouse,boards] [lever,boards] [ashby,boards]

A fictional student (a copy of the demo). Every request that could send anything is blocked, and no button is pressed, so nothing
typed or attached can leave the browser. Reports what was answered, what's left, and that no demographic question was touched.
Run it by hand: real forms change, and that's what the practice forms in tests/test_autofill.py can't show.
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import httpx

d = tempfile.mkdtemp(prefix="coopcoach-forms-")
with sqlite3.connect("file:data-demo/coopcoach.db?mode=ro", uri=True) as a, sqlite3.connect(d + "/coopcoach.db") as b:
    a.backup(b)
os.environ.update(COOPCOACH_DATA=d, COOPCOACH_DEMO="1", GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")
from playwright.sync_api import sync_playwright

from coopcoach import apply, db, store

db.init()
pid = db.row("SELECT id FROM profiles ORDER BY id LIMIT 1")["id"]
picks = []
def add(ats, slug, title, url, loc=""):
    picks.append({"source": f"{ats}:{slug}", "company": slug.title(), "title": title, "url": url, "locations": [loc or "Toronto, ON"], "description": "Internship."})
def pick(jobs, get_title):
    interns = [j for j in jobs if re.search(r"intern|co-?op", get_title(j), re.I)]
    return (interns or jobs)[:1]
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="coopcoach-forms-out-")
ARGS = (sys.argv[2:] + ["imc,figma,robinhood", "solopulseco,standtogether", "ramp,notion,replit"][len(sys.argv[2:]):])[:3]
for slug in ARGS[0].split(","):
    try:
        for j in pick(httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=20).json().get("jobs", []), lambda j: j["title"]):
            add("greenhouse", slug, j["title"], j["absolute_url"] if "greenhouse" in j["absolute_url"] else f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}")
    except Exception as e: print("gh", slug, e)
for slug in ARGS[1].split(","):
    try:
        for j in pick(httpx.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=20).json(), lambda j: j["text"]):
            add("lever", slug, j["text"], j["hostedUrl"])
    except Exception as e: print("lever", slug, e)
for slug in ARGS[2].split(","):
    try:
        for j in pick(httpx.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=20).json().get("jobs", []), lambda j: j["title"]):
            add("ashby", slug, j["title"], j["jobUrl"])
    except Exception as e: print("ashby", slug, e)
now = db.now()
store.upsert_jobs([store.normalize(p) for p in picks], now)
store.invalidate_cache()
AUDIT = r"""() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2 && getComputedStyle(e).visibility !== 'hidden'; };
  const labelOf = (e) => { const id = e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`); return ((id && id.innerText) || e.getAttribute('aria-label') || e.name || (e.closest('li,fieldset') && e.closest('li,fieldset').querySelector('.application-label,legend') || {}).innerText || '').trim().slice(0, 60).replace(/\s+/g, ' '); };
  const out = {values: [], typedInBox: [], emptyRequired: [], emptyOther: [], demographic: [], selected: [...document.querySelectorAll('[class*=single-value]')].map((v) => { const b = v.closest('.select') || v.parentElement.parentElement.parentElement; const l = b && b.querySelector('label'); return (l ? l.innerText.trim().slice(0, 40) : '?') + ' = ' + v.innerText.trim().slice(0, 30); })};
  for (const e of document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio]):not([class*=requiredInput]), textarea, select')) {
    if (!vis(e) && e.type !== 'file') continue;
    const lab = labelOf(e), combo = e.getAttribute('role') === 'combobox', box = e.closest('[class*=select__control]');
    const chosen = box && box.querySelector('[class*=single-value]');
    if (combo && e.value) out.typedInBox.push(lab);
    const has = e.type === 'file' ? e.files.length > 0 : e.tagName === 'SELECT' ? e.selectedIndex > 0 : !!(e.value && e.value.trim()) || !!chosen;
    if (has && !combo) out.values.push(lab + ' = ' + String(e.type === 'file' ? 'file' : e.tagName === 'SELECT' ? e.options[e.selectedIndex].text : e.value).slice(0, 30));
    if (!has) ((e.required || e.getAttribute('aria-required') === 'true') ? out.emptyRequired : out.emptyOther).push(lab);
    if (has && /gender|race|ethnic|veteran|disabilit|orientation|pronoun|transgender/i.test(lab)) out.demographic.push(lab);
  }
  for (const e of document.querySelectorAll('input[type=radio]:checked')) { out.values.push('radio: ' + (e.closest('label') || e.parentElement).innerText.trim().slice(0, 30)); if (/he\/him|she\/her|they\/them|man|woman|hispanic|veteran/i.test((e.closest('label') || e.parentElement).innerText)) out.demographic.push('radio ' + e.value); }
  return out;
}"""
report = []
with sync_playwright() as pw:
    br = pw.chromium.launch(headless=True)
    for pk in picks:
        ctx = br.new_context(viewport={"width": 1280, "height": 1000}, user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
        blocked = []
        def route(rt):
            q = rt.request
            if q.method != "GET":
                if "non-user-graphql" in q.url and re.search(r"op=(ApiJobPosting|ApiOrganization|ApiJobBoard|ApiAutocomplete)", q.url):
                    return rt.continue_()
                blocked.append(q.url.split("?op=")[-1][:40] if "?op=" in q.url else q.url[:50])
                return rt.abort()
            return rt.continue_()
        ctx.route("**/*", route)
        page = ctx.new_page()
        row = {"source": pk["source"], "title": pk["title"][:44]}
        try:
            job = next(x for x in store.scored_jobs(pid) if x["title"] == pk["title"])
            kit = apply.build_kit(pid, job["id"])
            files = apply._write_files(pid, kit) if kit.get("files") else {}
            todo = [{"label": q["label"], "answer": q["answer"]} for q in kit["questions"]] + apply._basic_answers(kit["basics"])
            prefs = store.get_profile(pid)["prefs"]
            row["url"] = kit["job"]["apply_url"]
            page.goto(row["url"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            t = time.time()
            a1 = apply._fill_page(page, todo, files)
            a2 = apply._fill_choices(page, apply.asker(kit, prefs))
            row["seconds"] = round(time.time() - t, 1)
            page.wait_for_timeout(600)
            a = {"values": [], "typedInBox": [], "emptyRequired": [], "emptyOther": [], "demographic": [], "selected": []}
            for f in page.frames:
                try:
                    x = f.evaluate(AUDIT)
                    for k in a: a[k] += x[k]
                except Exception: pass
            row.update(a, typed=len(a1), choices=len(a2), choice_labels=a2, blocked=sorted(set(blocked))[:6])
            page.screenshot(path=f"{OUT}/live_{pk['source'].replace(':','_')}.png", full_page=True)
        except Exception as e:
            row["error"] = str(e).split("\n")[0][:200]
        report.append(row)
        print(f"{pk['source']:26} text={len(row.get('values',[]))} chosen={len(row.get('selected',[]))} typed-not-chosen={len(row.get('typedInBox',[]))} required-empty={len(row.get('emptyRequired',[]))} other-empty={len(row.get('emptyOther',[]))} demographic={row.get('demographic')} {row.get('seconds')}s {row.get('error','')[:100]}", flush=True)
        ctx.close()
    br.close()
Path(OUT + "/live_forms.json").write_text(json.dumps(report, indent=1))
shutil.rmtree(d, ignore_errors=True)
