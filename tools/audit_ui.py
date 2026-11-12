"""Click through every page of the demo the way a person would, and report what breaks.

    .venv/bin/python -m tools.audit_ui [--out report.json] [--port 8799]

Starts `python -m coopcoach demo --fresh` on a spare port (a fictional student: nothing real is touched), then at desktop
width, phone width and in light mode opens each page, presses each different harmless button on it, and each harmless button
in any window that opens. Reports JavaScript errors,
console errors, failed requests, pages wider than the screen, empty pages, unreadable text and controls too small to tap.
Buttons that would open a window, sign in, change a connection or delete things are left alone.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SKIP = re.compile(r"delete|remove|disconnect|reset|sign ?in|connect|^\s*sync|lock|passcode|forgot|submit|log ?out|revoke|"
                  r"clear|fill|chrome|install|set up|apply session|apply with|run now|run autopilot|forget|deactivate|"
                  r"google|outlook|start over|rebuild|find new boards|turn on|turn off|check inbox|scan|export|download|⬇|"
                  r"\.ics|add to calendar|copy|⏱ start|^start$", re.I)
VIEWS = ["autopilot", "path", "matches", "tracks", "track:role:software", "track:co:quant-firms", "track:co:startups", "tracker",
         "documents", "recruiters", "events", "settings"]
JS_VISIT = """async ([view, sec]) => {
  if (sec) { const id = view.slice(6); LS.set(`tsec:${id}`, sec); }
  document.querySelector('#modal-root').innerHTML = '';
  document.querySelectorAll('.milestone-bg').forEach((e) => e.remove());
  goView(view);
}"""
JS_CHECKS = """() => {
  const out = {wide: document.documentElement.scrollWidth - innerWidth, empty: !document.querySelector('#main').innerText.trim(), offenders: [], tiny: []};
  for (const e of document.querySelectorAll('#main *, #modal-root *')) {
    const r = e.getBoundingClientRect();
    if (r.width && r.right > innerWidth + 2 && getComputedStyle(e).position !== 'fixed' && !e.closest('.trail-map, .table-wrap, .kw, [style*="overflow"]') && out.offenders.length < 4) {
      let p = e.parentElement, scrolls = false;
      while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { scrolls = true; break; } p = p.parentElement; }
      if (!scrolls) out.offenders.push(e.tagName + '.' + String(e.className).slice(0, 30));
    }
    if (innerWidth < 500 && /^(BUTTON|A|SELECT)$/.test(e.tagName) && r.width && (r.height < 24 || r.width < 24) && out.tiny.length < 4 && e.innerText.trim() && getComputedStyle(e).display !== 'inline') out.tiny.push((e.innerText || '').trim().slice(0, 20));
  }
  return out;
}"""
JS_MODAL_BUTTONS = """() => [...document.querySelectorAll('#modal-root button, #modal-root summary, #modal-root [role=tab]')]
  .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !e.disabled; })
  .map((e, i) => { e.setAttribute('data-audit-m', i); return {i, text: (e.innerText || e.title || e.getAttribute('aria-label') || '').trim().slice(0, 50), id: e.id}; })"""
JS_BUTTONS = """() => [...document.querySelectorAll('#main button, #main summary, #main [role=tab], #main a[href="#"]')]
  .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !e.disabled; })
  .map((e, i) => { e.setAttribute('data-audit', i); return {i, text: (e.innerText || e.title || e.getAttribute('aria-label') || '').trim().slice(0, 50), id: e.id}; })"""


def run(port: int) -> dict:
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([sys.executable, "-m", "coopcoach", "demo", "--fresh", "--port", str(port)], cwd=ROOT,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {"problems": [], "pressed": 0, "skipped": [], "pages": 0}
    try:
        end = time.time() + 180
        while time.time() < end:
            try:
                if httpx.get(f"{url}/api/state", timeout=5).status_code == 200:
                    httpx.put(f"{url}/api/features", json={"mode": "all"}, headers={"Origin": url}, timeout=5)  # every page
                    break
            except httpx.HTTPError:
                time.sleep(1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for mode, size, scheme in (("desktop", (1280, 800), "dark"), ("phone", (375, 812), "dark"), ("light", (1280, 800), "light")):
                ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme, accept_downloads=False)
                ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith(url) or route.request.resource_type in ("image", "font", "stylesheet") else route.abort())
                page = ctx.new_page()
                where = {"at": ""}
                def note(kind, text):
                    if len(report["problems"]) < 400:
                        report["problems"].append({"mode": mode, "at": where["at"], "kind": kind, "text": str(text)[:300]})
                page.on("pageerror", lambda e: note("js-error", e))
                page.on("console", lambda m: note("console", m.text) if m.type == "error" and "ERR_FAILED" not in m.text else None)
                page.on("response", lambda r: note("http", f"{r.status} {r.request.method} {r.url.replace(url, '')}") if r.status >= 400 and r.url.startswith(url) else None)
                page.on("popup", lambda p: p.close())
                page.on("dialog", lambda d: d.dismiss())
                seen_in_windows: set = set()
                page.goto(url)
                page.wait_for_function("typeof S !== 'undefined' && S.app && S.booted", timeout=60000)
                if scheme == "light":
                    page.evaluate("document.documentElement.dataset.theme = 'light'")
                pages = [(v, None) for v in VIEWS] + [("track:role:software", s) for s in ("companies", "market")] + [("track:co:quant-firms", s) for s in ("companies", "market")]
                for view, sec in pages:
                    where["at"] = f"{view}{'/' + sec if sec else ''}"
                    page.evaluate(JS_VISIT, [view, sec])
                    page.wait_for_timeout(1600)
                    report["pages"] += 1
                    c = page.evaluate(JS_CHECKS)
                    if c["wide"] > 2:
                        note("too-wide", f"{c['wide']}px wider than the screen: {c['offenders']}")
                    if c["empty"]:
                        note("empty", "nothing on the page")
                    if c["tiny"]:
                        note("tiny", c["tiny"])
                    pressed: set = set()
                    for _ in range(400):  # (a button with the same name on every row is pressed once)
                        btns = page.evaluate(JS_BUTTONS)
                        btn = next((b for b in btns if (b["text"] or b["id"]) and (b["text"], b["id"]) not in pressed), None)
                        if not btn:
                            break
                        pressed.add((btn["text"], btn["id"]))
                        if SKIP.search(btn["text"] or "") or SKIP.search(btn["id"] or ""):
                            report["skipped"].append(f"{where['at']}: {btn['text'] or btn['id']}")
                            continue
                        where["at"] = f"{view}{'/' + sec if sec else ''} › {btn['text'] or btn['id']}"
                        try:
                            page.locator(f'[data-audit="{btn["i"]}"]').first.click(timeout=2500)
                            report["pressed"] += 1
                        except Exception as e:
                            why = [ln.strip() for ln in str(e).split("\n") if any(w in ln for w in ("intercepts", "not visible", "not stable", "not enabled", "outside of the viewport", "detached", "waiting for locator"))]
                            note("unclickable", " / ".join(why[-2:])[:280] or str(e)[:200])
                        page.wait_for_timeout(450)
                        c = page.evaluate(JS_CHECKS)
                        if c["wide"] > 2:
                            note("too-wide", f"{c['wide']}px: {c['offenders']}")
                        inside = f"{where['at']} › window"
                        for _ in range(12):  # what it opened: its own buttons, one level down
                            more = page.evaluate(JS_MODAL_BUTTONS)
                            hit = next((b for b in more if (b["text"] or b["id"]) and (b["text"], b["id"], "window") not in seen_in_windows), None)
                            if not hit:
                                break
                            seen_in_windows.add((hit["text"], hit["id"], "window"))
                            if SKIP.search(hit["text"] or "") or SKIP.search(hit["id"] or ""):
                                report["skipped"].append(f"{inside}: {hit['text'] or hit['id']}")
                                continue
                            where["at"] = f"{inside} › {hit['text'] or hit['id']}"
                            try:
                                page.locator(f'[data-audit-m="{hit["i"]}"]').first.click(timeout=2500)
                                report["pressed"] += 1
                            except Exception as e:
                                why = [ln.strip() for ln in str(e).split("\n") if any(w in ln for w in ("intercepts", "not visible", "not stable", "not enabled", "outside of the viewport", "detached", "waiting for locator"))]
                                note("unclickable", " / ".join(why[-2:])[:280] or str(e)[:200])
                            page.wait_for_timeout(350)
                            page.evaluate("document.querySelectorAll('.milestone-bg').forEach((e) => e.remove())")
                        page.evaluate("document.querySelector('#modal-root').innerHTML = ''; document.querySelectorAll('.milestone-bg, .plan-pop').forEach((e) => e.remove()); document.querySelectorAll('details[open]').forEach((d) => d.open = false)")
                        where["at"] = f"{view}{'/' + sec if sec else ''}"
                        if page.evaluate("() => S.tab") != view.split(":")[0]:
                            page.evaluate(JS_VISIT, [view, sec])
                            page.wait_for_timeout(1000)
                ctx.close()
            browser.close()
    finally:
        server.terminate()
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args()
    r = run(args.port)
    if args.out:
        Path(args.out).write_text(json.dumps(r, indent=1), encoding="utf-8")
    seen: dict = {}
    for p in r["problems"]:
        seen.setdefault((p["kind"], p["text"][:90]), []).append(f"{p['mode']}:{p['at']}")
    print(f"{r['pages']} pages, {r['pressed']} buttons pressed, {len(r['skipped'])} left alone, {len(r['problems'])} problems, {len(seen)} different")
    for (kind, text), where in sorted(seen.items()):
        print(f"[{kind}] {text}\n    x{len(where)}: {where[0]}")
    raise SystemExit(1 if r["problems"] else 0)


if __name__ == "__main__":
    main()
