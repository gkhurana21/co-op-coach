"""Check (and find) the event sources everyone gets: universities, Toronto/Waterloo organizers and major hackathons.

    .venv/bin/python -m tools.discover_event_sources --check            # every source + hackathon site, live
    .venv/bin/python -m tools.discover_event_sources --probe mcgill.ca  # look for a readable calendar at a school

--check reads every source in coopcoach/event_sources.yaml the way the app does and counts the career / startup /
hackathon events it would keep, then opens every site in coopcoach/major_hackathons.yaml. A source that errors or a
site that doesn't answer is reported (exit code 1) so it can be fixed or dropped.

--probe tries the calendar systems universities use: Localist (/api/2/events, and it lists the school's career and
entrepreneurship event types to filter on), LiveWhale (/live/json/events), WordPress The Events Calendar
(/wp-json/tribe/events/v1/events), and .ics / webcal links on events.<domain> or calendar.<domain>.
"""

import argparse
import concurrent.futures as cf
import re
import sys
import time

import httpx
import yaml

from coopcoach import config, events

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/128"}
CAREER_TYPE = re.compile(r"career|recruit|employ|professional dev|internship|entrepreneur|startup|hackathon|networking|"
                         r"innovation", re.I)


def check_source(src: dict) -> tuple[dict, int, int, str]:
    try:
        evs, _ = events.fetch_feed(src["url"])
    except Exception as e:  # noqa: BLE001 (report anything)
        return src, 0, 0, f"{type(e).__name__}: {e}"[:120]
    now = time.time()
    feed = {"grp": src["group"], "career_only": 1}
    typed = "type[]=" in src["url"]
    kept = [e for e in evs if now - 86400 < e["start_ts"] < now + 120 * 86400
            and events._keep({**e, "kind": e.get("kind") or events.classify(e["title"], e["description"])}, feed, typed)]
    return src, len(evs), len(kept), ""


def check_site(h: dict) -> tuple[dict, int, str]:
    try:
        r = httpx.get(h["site"], headers=UA, follow_redirects=True, timeout=20)
        return h, r.status_code, str(r.url)
    except httpx.HTTPError as e:
        return h, 0, str(e)[:80]


def check() -> int:
    sources = yaml.safe_load((config.PKG / "event_sources.yaml").read_text(encoding="utf-8"))["sources"]
    majors = yaml.safe_load((config.PKG / "major_hackathons.yaml").read_text(encoding="utf-8"))["hackathons"]
    bad = 0
    with cf.ThreadPoolExecutor(8) as ex:
        for src, n, kept, err in ex.map(check_source, sources):
            bad += bool(err)
            print(f"{'FAIL' if err else 'ok  '} {src['name'][:34]:34} {src['group'][:22]:22} "
                  f"{err or f'{n} listed, {kept} kept'}")
        print()
        for h, code, where in ex.map(check_site, majors):
            ok = 200 <= code < 400
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {h['name'][:24]:24} {code or ''} {where}")
    print(f"\n{bad} problem(s)")
    return 1 if bad else 0


def probe(domain: str) -> None:
    with httpx.Client(headers=UA, follow_redirects=True, timeout=20) as c:
        for host in (f"events.{domain}", f"calendar.{domain}", f"www.{domain}"):
            base = f"https://{host}"
            try:
                r = c.get(f"{base}/api/2/events", params={"pp": 1, "days": 90})
                if r.status_code == 200 and '"events"' in r.text[:300]:
                    total = (r.json().get("page") or {}).get("total_items")
                    print(f"Localist at {base}: {total} events in 90 days")
                    fl = c.get(f"{base}/api/2/events/filters").json()
                    for cat, items in fl.items():
                        for it in items if isinstance(items, list) else []:
                            if CAREER_TYPE.search(it.get("name", "")):
                                print(f"   type[]={it['id']}  ({cat}: {it['name']})")
                    return
                r = c.get(f"{base}/live/json/events/max/3")
                if r.status_code == 200 and r.text.lstrip()[:1] in "[{" and "title" in r.text:
                    print(f"LiveWhale at {base}: use {base}/live/json/events/max/400")
                    return
                r = c.get(f"{base}/wp-json/tribe/events/v1/events", params={"per_page": 1})
                if r.status_code == 200 and '"events"' in r.text[:500]:
                    print(f"The Events Calendar at {base}: {r.json().get('total')} events; use {base}/wp-json/tribe/events/v1/events")
                    return
            except (httpx.HTTPError, ValueError):
                continue
        for page in (f"https://events.{domain}/", f"https://calendar.{domain}/", f"https://www.{domain}/events"):
            found = events.resolve_feed(page)
            if found != page:
                print(f"Feed found from {page}: {found}")
                return
    print(f"Nothing readable found for {domain}: its calendar may need a sign-in or a custom reader.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--probe", nargs="*", default=[])
    a = ap.parse_args()
    if a.probe:
        for d in a.probe:
            probe(d)
    if a.check or not a.probe:
        sys.exit(check())


if __name__ == "__main__":
    main()
