"""Outlook without anyone's approval: the student signs in to Outlook on the web once, in Co-op Coach's own browser
window (a school Microsoft 365 account or Outlook.com), and each inbox check reads the recruiting emails from there.

Schools (Waterloo included) don't let students register apps, and most need IT to approve any app that reads mail;
forwarding a school inbox to Gmail is unreliable too. This needs none of that: it's the student's own Outlook, in a
window that keeps them signed in. Co-op Coach never types their password, never
sends, moves or deletes anything, and only opens the emails Outlook's own search finds about applications.
"""

import logging
import re
import secrets
import threading
import time
from datetime import datetime

from . import config, db

log = logging.getLogger(__name__)
SCHOOL = "https://outlook.office.com/mail/"
PERSONAL = "https://outlook.live.com/mail/0/"
_PERSONAL_DOMAINS = ("outlook.com", "hotmail.com", "live.com", "msn.com", "outlook.ca", "hotmail.ca", "live.ca",
                     "hotmail.co.uk", "outlook.co.uk", "passport.com")
_MS_HOSTS = ("outlook.office.com", "outlook.office365.com", "outlook.live.com", "outlook.cloud.microsoft")
_HOSTS = _MS_HOSTS
# the mailbox's tab title: "Mail - Their Name - Outlook", "Inbox - them@school.ca - Outlook"; while it's still signing
# in the title is "Outlook", "Sign in to your account" or "Microsoft Authentication"
_MAILBOX_TITLE = re.compile(r"\S.* - .*Outlook\s*$", re.I)
# any of them on screen (Outlook keeps hidden listboxes around, so the first match alone says nothing)
_MAILBOX_JS = """() => [...document.querySelectorAll('[role="listbox"], [data-convid], [aria-label="Message list" i], #MailList')]
  .some((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; })"""
KEEP_DAYS = 14
# what Outlook's own search is asked for: the words recruiting emails use (the same as the Gmail search)
QUERY = ("application OR applying OR applied OR interview OR assessment OR offer OR candidate OR candidacy OR "
         "unfortunately OR hackerrank OR codesignal OR hirevue OR \"next steps\" OR \"coffee chat\" OR invitation OR "
         "\"info session\" OR RSVP OR \"career fair\" OR \"phone screen\"")
_flows: dict[str, dict] = {}
_busy: dict[int, threading.Lock] = {}  # per person: their Outlook window's profile can only be open once


class SignInNeeded(RuntimeError):
    """Outlook asked to sign in again (the remembered sign-in ran out)."""


def home(address: str) -> str:
    domain = (address or "").rsplit("@", 1)[-1].lower()
    return PERSONAL if domain in _PERSONAL_DOMAINS else SCHOOL


def _profile(pid: int):
    from .workday import PROFILES
    return PROFILES / f"outlook-{pid}"


def _host(url: str) -> str:
    return re.sub(r"^https?://([^/]+).*", r"\1", url or "").lower()


def _open_url(acct: dict) -> str:
    """Where this inbox's mailbox is: the Outlook it landed on when they signed in, whatever kind of address it is."""
    host = (acct.get("host") or "").lower()
    return f"https://{host}/mail/" if host in _MS_HOSTS else home(acct["address"])


def _in_mailbox(page) -> bool:
    """Outlook's mailbox has loaded (not a sign-in, MFA or "stay signed in?" page). Its address and tab title say so
    as soon as it's there; the message list may take a while longer to draw."""
    try:
        if _host(page.url) not in _HOSTS or not re.match(r"https?://[^/]+/(mail|owa)\b", page.url, re.I):
            return False
        return bool(_MAILBOX_TITLE.match(page.title() or "")) or bool(page.evaluate(_MAILBOX_JS))
    except Exception:  # the page is moving on to the next one
        return False


def _keep_session(ctx) -> int:
    """Schools' sign-ins (Waterloo's ADFS and Duo) use cookies that end when the browser closes, which would sign the
    student out of this window every time. They're kept in the window's own profile, next to its "stay signed in"
    cookies; Microsoft still decides how long the sign-in lasts. Returns how many were kept."""
    kept = 0
    until = time.time() + KEEP_DAYS * 86400
    for k in ctx.cookies():
        if k.get("expires", -1) == -1:
            try:
                ctx.add_cookies([{**k, "expires": until}])
                kept += 1
            except Exception as e:  # one odd cookie mustn't cost the others
                log.info("couldn't keep a cookie: %s", e)
    return kept


def _probe(page) -> dict:
    """What the page is made of, when reading it didn't go as expected: how many of each thing, never any text."""
    sel = ['[role="listbox"]', '[role="option"]', "[data-convid]", '[aria-label="Message list" i]', "#MailList",
           "#topSearchInput", 'input[aria-label*="Search" i]', '[role="search"] input', '[role="combobox"]',
           '[role="main"]', '[aria-label="Reading Pane" i]', '[aria-label="Message body" i]', '[role="document"]']
    try:
        counts = page.evaluate("(s) => Object.fromEntries(s.map((x) => [x, document.querySelectorAll(x).length]))", sel)
    except Exception as e:
        counts = {"error": str(e)[:80]}
    return {"host": _host(page.url), "counts": counts, "at": int(time.time())}


# --- connecting: the student signs in, in a window of its own ------------------------------------------------

def connect(pid: int, address: str) -> str:
    """Open Outlook on the web in Co-op Coach's own window and wait (up to 15 minutes) for the student to sign in.
    Returns a flow id to ask about (see flow)."""
    fid = secrets.token_urlsafe(8)
    _flows[fid] = {"status": "waiting", "address": address, "error": None}

    def run():
        try:
            from playwright.sync_api import sync_playwright

            from .workday import _launch
            with sync_playwright() as p:
                ctx = _launch(p, _profile(pid), headless=False)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(home(address), wait_until="domcontentloaded", timeout=60000)
                deadline = time.time() + 15 * 60
                while time.time() < deadline:
                    if not ctx.pages:
                        _flows[fid].update(status="error", error="The Outlook window was closed before you signed in")
                        return
                    page = next((x for x in ctx.pages if _in_mailbox(x)), None)
                    if page:
                        _signed_in(pid, address, ctx, page, fid)
                        return
                    ctx.pages[-1].wait_for_timeout(1500)
                _flows[fid].update(status="error", error="Sign-in took longer than 15 minutes: try again")
                ctx.close()
        except Exception as e:
            log.warning("Outlook sign-in window failed: %s", e)
            _flows[fid].update(status="error", error=f"Couldn't open Outlook: {str(e)[:160]}")

    threading.Thread(target=run, daemon=True, name="outlook-sign-in").start()
    return fid


def _signed_in(pid: int, address: str, ctx, page, fid: str) -> None:
    """They're in. Save it straight away (closing the window now can't lose it), then read the first emails here, in
    the window that's certainly signed in, and only then close it."""
    _save(pid, address, _host(page.url))
    _keep_session(ctx)
    acct = db.row("SELECT * FROM email_accounts WHERE profile_id=? AND address=?", (pid, address.lower()))
    _flows[fid].update(status="reading")
    msgs: list[dict] = []
    note = ""
    floor = int(time.time() - int(config.get("email.lookback_days", 120)) * 86400)
    closed = False
    try:
        msgs = _read(page, acct, floor, FIRST_BUDGET, progress=lambda n, oldest: _flows[fid].update(
            found=n, back_to=oldest))
    except Exception as e:  # signed in all the same
        closed = "closed" in str(e).lower()
        log.warning("Outlook: signed in, first read %s: %s", "stopped (window closed)" if closed else "failed", e)
        if not closed:
            note = "You're connected, but the first read of your emails didn't finish. It tries again at the next check."
            with db.tx() as c:
                c.execute("UPDATE email_accounts SET last_status=? WHERE id=?", (f"error: {str(e)[:160]}", acct["id"]))
    try:
        _keep_session(ctx)
        page.wait_for_timeout(400)
        ctx.close()
    except Exception:  # they closed the window themselves
        pass
    if msgs:
        _process_first(acct, msgs)
    if closed or not state(acct["id"]).get("finished", True):  # the rest, without the window: the sign-in is remembered
        _read_rest(acct)
        note = note or "You're connected. The rest of your emails are being read in the background."
    _flows[fid].update(status="done", found=len(msgs), note=note)


def _process_first(acct: dict, msgs: list[dict]) -> None:
    """Sort what the first read found, behind the page: with an AI connected this can take a minute."""
    from . import mail

    def run():
        try:
            stats = mail.process_messages(acct["profile_id"], acct["id"], msgs)
            status = f"ok · {stats['job_emails']} job emails, {stats['status_changes']} updates"
            with db.tx() as c:
                c.execute("UPDATE email_accounts SET last_sync=?, last_status=? WHERE id=?", (db.now(), status, acct["id"]))
        except Exception as e:
            log.warning("Outlook: sorting the first emails failed: %s", e)
    threading.Thread(target=run, daemon=True, name="outlook-first-read").start()


def _read_rest(acct: dict) -> None:
    from . import mail

    def run():
        try:
            mail.sync_account(db.row("SELECT * FROM email_accounts WHERE id=?", (acct["id"],)), budget=FIRST_BUDGET)
        except Exception as e:
            log.warning("Outlook: reading the rest failed: %s", e)
    threading.Thread(target=run, daemon=True, name="outlook-rest").start()


def flow(fid: str) -> dict | None:
    return _flows.get(fid)


def _save(pid: int, address: str, host: str = "") -> None:
    host = host or _host(home(address))
    with db.tx() as c:
        c.execute("INSERT INTO email_accounts (profile_id, kind, address, host, secret, created_at) VALUES"
                  " (?, 'outlook_web', ?, ?, '', ?) ON CONFLICT(profile_id, address) DO UPDATE SET kind='outlook_web',"
                  " host=excluded.host, secret='', last_status=NULL, enabled=1", (pid, address.lower(), host, db.now()))


# --- reading: Outlook's own search, then each email it finds -------------------------------------------------
#
# Outlook's list is newest first and draws only the rows on screen, so a check walks down it: what it read before is passed
# over (remembered by id), new mail at the top is read first, then it carries on down to older mail it hasn't read yet, as
# far back as the lookback window. A check that runs out of time stops there, and the next one carries on from where it was.

BUDGET = 90          # seconds of reading per check, at most
FIRST_BUDGET = 300   # right after signing in: the whole window back, while the student watches it happen
MAX_OPEN = 120       # emails opened per check, at most
SIGN_IN_HOSTS = re.compile(r"(^|\.)(login\.microsoftonline\.com|login\.live\.com|login\.microsoft\.com|account\.live\.com|"
                           r"adfs\.[\w.-]+|sso\.[\w.-]+|[\w.-]*duosecurity\.com|[\w.-]*okta\.com|idp\.[\w.-]+)$", re.I)

_LIST_JS = """() => {
  const rows = [...document.querySelectorAll('[role="listbox"] [role="option"], [data-convid]')]
    .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const seen = new Set(), out = [];
  for (const r of rows) {
    const id = r.getAttribute('data-convid') || r.querySelector('[data-convid]')?.getAttribute('data-convid') || r.id;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const dated = [...r.querySelectorAll('[title]')].map((e) => e.getAttribute('title')).filter((t) => /\\d/.test(t)).join(' | ');
    out.push({id, label: (r.getAttribute('aria-label') || '').slice(0, 600), text: (r.innerText || '').slice(0, 600), dated});
  }
  return out;
}"""

# down the list: the scrolling box the rows are in, by most of a screen
_SCROLL_JS = """() => {
  let e = document.querySelector('[role="listbox"] [role="option"], [data-convid]');
  while (e && e !== document.body) {
    const s = getComputedStyle(e);
    if (/(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 5) {
      const before = e.scrollTop; e.scrollTop = before + e.clientHeight * 0.85; return e.scrollTop - before;
    }
    e = e.parentElement;
  }
  return 0;
}"""

_READ_JS = """() => {
  const pane = document.querySelector('[aria-label="Reading Pane" i], [role="main"] [aria-label*="Reading" i], #ReadingPaneContainerId')
    || document.querySelector('[role="main"]') || document.body;
  const body = pane.querySelector('[aria-label="Message body" i], [role="document"]') || pane;
  const subject = (pane.querySelector('[role="heading"][aria-level="2"], [role="heading"]') || {}).innerText || '';
  const from = [...pane.querySelectorAll('[aria-label^="From" i], [title*="@"], span')].map((e) => e.getAttribute('title') || e.innerText || '')
    .find((t) => /@/.test(t)) || '';
  const when = [...pane.querySelectorAll('[aria-label*="Date" i], [title]')].map((e) => e.getAttribute('title') || '')
    .find((t) => /\\d{1,2}:\\d{2}/.test(t)) || '';
  return {subject, from, when, body: (body.innerText || '').slice(0, 8000), url: location.href};
}"""

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_WDAYS = {d: i for i, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
_TIME = r"(\d{1,2}):(\d{2})(?:\s*([AaPp])\.?\s*[Mm]\.?)?"


def _hm(m) -> tuple[int, int]:
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ap == "p" and h < 12:
        h += 12
    elif ap == "a" and h == 12:
        h = 0
    return h, mi


def _received(row_ts: int | None, pane_ts: int | None) -> int:
    """When an email arrived. Its own row in the list is the source: the reading pane's time-only text can belong to
    another message or default to today. The pane only sharpens the time when it agrees on the day."""
    if row_ts and pane_ts and datetime.fromtimestamp(row_ts).date() == datetime.fromtimestamp(pane_ts).date():
        return pane_ts
    return row_ts or pane_ts or int(time.time())


def _when(text: str, now: float | None = None) -> int | None:
    """A date as Outlook shows it, in the list or the open email, as a timestamp; None when there's no date in it.
    "Tue 2026-10-28 3:14 PM", "Tue, Oct 28, 2026 3:14 PM", "10/28/2026 3:14 PM", "28/10/2026 15:14", "October 28, 2026",
    "Tue 10/28", "Oct 28", "Yesterday", "Tue 3:14 PM", "3:14 PM"."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return None
    now_dt = datetime.fromtimestamp(now or time.time())
    tm = re.search(_TIME, t)
    h, mi = _hm(tm) if tm else (12, 0)

    def at(y, mo, d):
        try:
            return datetime(y, mo, d, h, mi)
        except ValueError:
            return None

    def past(dt):  # no year shown: this year's, or last year's if that would be in the future
        if dt and dt > now_dt.replace(hour=23, minute=59):
            dt = at(dt.year - 1, dt.month, dt.day)
        return dt

    dt = None
    if m := re.search(r"\b(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})\b", t):
        dt = at(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    elif m := re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d\d|\d\d)\b", t):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += 2000 if y < 100 else 0
        dt = at(y, b, a) if a > 12 else at(y, a, b)  # month first unless it can't be
    elif m := re.search(r"\b([A-Za-z]{3})[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?,? (20\d\d)\b", t):
        if m.group(1).lower() in _MONTHS:
            dt = at(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
    elif m := re.search(r"\b(\d{1,2}) ([A-Za-z]{3})[a-z]*\.? (20\d\d)\b", t):
        if m.group(2).lower() in _MONTHS:
            dt = at(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    if dt is None:
        if m := re.search(r"\b([A-Za-z]{3})[a-z]*\.? (\d{1,2})\b(?!:)", t):
            if m.group(1).lower() in _MONTHS:
                dt = past(at(now_dt.year, _MONTHS[m.group(1).lower()], int(m.group(2))))
        if dt is None and (m := re.search(r"\b(\d{1,2})/(\d{1,2})\b(?![/:])", t)):
            dt = past(at(now_dt.year, int(m.group(1)), int(m.group(2))))
    if dt is None:
        low = t.lower()
        base = None
        if "yesterday" in low:
            base = now_dt.date().toordinal() - 1
        elif "today" in low:
            base = now_dt.date().toordinal()
        elif m := re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", low):
            back = (now_dt.weekday() - _WDAYS[m.group(1)]) % 7 or 7  # within the last week (today shows a bare time)
            base = now_dt.date().toordinal() - back
        elif tm:
            base = now_dt.date().toordinal()
        if base is not None:
            d = datetime.fromordinal(base)
            dt = at(d.year, d.month, d.day)
    return int(dt.timestamp()) if dt else None


def _sender(text: str) -> tuple[str, str]:
    m = re.search(r"([^<>\n]*?)\s*<?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)>?", text or "")
    return ((m.group(1) or "").strip(' "'), m.group(2).lower()) if m else ("", "")


def _worth_opening(row: dict) -> bool:
    """From the list alone: job alerts, newsletters and the like aren't opened. Anything that might be about an
    application is, since the date, the links and the details are inside."""
    from .mail_rules import NOT_JOB
    return not NOT_JOB.search(f"{row['label']} {row['text']}")


def _on_sign_in(page) -> bool:
    return bool(SIGN_IN_HOSTS.search(_host(page.url)))


def _search(page, acct: dict) -> None:
    try:
        box = page.locator('#topSearchInput, input[aria-label*="Search" i], [role="search"] input, '
                           '[role="combobox"][aria-label*="Search" i]').first
        box.click(timeout=20000)
        box.fill(QUERY)
        box.press("Enter")
    except Exception as e:
        db.meta_set(f"owa_probe:{acct['id']}", db.dumps(_probe(page)))
        raise RuntimeError("signed in, but couldn't find Outlook's search box") from e


def _rows_settled(page, before: set[str], wait: float) -> list[dict]:
    """The rows on screen, once something new has drawn (or after `wait` seconds of nothing new)."""
    end = time.time() + wait
    while True:
        rows = page.evaluate(_LIST_JS)
        if {r["id"] for r in rows} - before or time.time() >= end:
            return rows
        page.wait_for_timeout(150)


def _open(page, row: dict, last_subject: str) -> dict:
    page.locator(f'[data-convid="{row["id"]}"]').first.click(timeout=5000)
    end = time.time() + 6
    got = page.evaluate(_READ_JS)
    while time.time() < end and (not got["subject"] or got["subject"] == last_subject or len(got["body"]) < 20):
        page.wait_for_timeout(120)  # until the reading pane shows this one (no fixed wait: it's often there at once)
        got = page.evaluate(_READ_JS)
    return got


def _read(page, acct: dict, floor_ts: int, budget: float = BUDGET, max_open: int = MAX_OPEN, progress=None) -> list[dict]:
    """Search this signed-in mailbox and read what's new and not yet read, back to floor_ts, within the time budget."""
    started = time.time()
    seen_key = f"owa_seen:{acct['id']}"
    seen = set((db.meta_get(seen_key) or "").split())
    out: list[dict] = []
    _search(page, acct)
    page.wait_for_timeout(300)
    rows = _rows_settled(page, set(), 10)
    handled: set[str] = set()
    oldest, done, ended, last_subject, opened, skipped, stalls = None, False, False, "", 0, 0, 0  # ended: the window's end reached
    try:
        while not done:
            fresh = [r for r in rows if r["id"] not in handled]
            for r in fresh:
                handled.add(r["id"])
                row_ts = _when(r["dated"]) or _when(r["label"]) or _when(r["text"])
                if row_ts:
                    oldest = min(oldest or row_ts, row_ts)
                    if row_ts < floor_ts:
                        done = ended = True  # newest first: everything below this is older still
                        break
                if r["id"] in seen:
                    continue
                if not _worth_opening(r):
                    seen.add(r["id"])
                    skipped += 1
                    continue
                if opened >= max_open or time.time() - started > budget:
                    done = True
                    break
                try:
                    got = _open(page, r, last_subject)
                except Exception as e:
                    log.info("couldn't open an Outlook email: %s", e)
                    continue
                opened += 1
                last_subject = got["subject"]
                seen.add(r["id"])  # read once: not opened again on the next check
                name, addr = _sender(got["from"])
                if addr == acct["address"].lower():
                    continue  # one they sent
                received = _received(row_ts, _when(got["when"]))
                if received < floor_ts:
                    continue
                out.append({"message_id": f"owa:{r['id']}", "received_at": received, "from_name": name,
                            "from_addr": addr, "subject": got["subject"].strip()[:300], "body": got["body"],
                            "web_link": got["url"] if got["url"].startswith("https://") else ""})
                if progress:
                    progress(len(out), oldest)
            if done or time.time() - started > budget:
                break
            moved = page.evaluate(_SCROLL_JS)
            rows = _rows_settled(page, handled, 1.5 if moved else 2.5)
            if not {r["id"] for r in rows} - handled:
                stalls += 1
                if stalls >= 2:  # the end of the results
                    done = ended = True
            else:
                stalls = 0
    finally:
        db.meta_set(seen_key, " ".join(list(seen)[-5000:]))
        reached = floor_ts if ended else (oldest or int(time.time()))
        db.meta_set(f"owa_state:{acct['id']}", db.dumps({"at": int(time.time()), "finished": ended, "opened": opened,
                                                         "skipped": skipped, "rows": len(handled), "back_to": reached,
                                                         "seconds": round(time.time() - started, 1)}))
    if not handled:  # nothing to read, or a page we don't understand: this tells which next time
        db.meta_set(f"owa_probe:{acct['id']}", db.dumps(_probe(page)))
    return out


def state(acct_id: int) -> dict:
    """How far back the last check got, and whether it reached the end of the window."""
    return db.loads(db.meta_get(f"owa_state:{acct_id}"), {}) or {}


def _wait_for_mailbox(page, give_up: float = 45) -> None:
    """Until the mailbox shows. A sign-in page that stays put means the remembered sign-in has run out: say so in
    seconds rather than waiting the whole time (a school's sign-in page may pass straight back by itself first)."""
    end, on_sign_in_since = time.time() + give_up, None
    while time.time() < end:
        if _in_mailbox(page):
            return
        if _on_sign_in(page):
            on_sign_in_since = on_sign_in_since or time.time()
            if time.time() - on_sign_in_since > 6:
                break
        else:
            on_sign_in_since = None
        page.wait_for_timeout(300)
    raise SignInNeeded("Outlook wants you to sign in again: press Sign in again")


def fetch(acct: dict, floor_ts: int, budget: float = BUDGET, max_open: int = MAX_OPEN) -> list[dict]:
    """The recruiting emails Outlook's search finds that weren't read here before, back to floor_ts, in the same shape
    the inbox reader uses for Gmail and IMAP. Raises SignInNeeded when Outlook wants the student to sign in again."""
    from playwright.sync_api import sync_playwright

    from .workday import _launch
    lock = _busy.setdefault(acct["profile_id"], threading.Lock())
    if not lock.acquire(timeout=budget + 120):  # one window on this sign-in at a time (a check, the rest of a first read)
        raise RuntimeError("Outlook is still being read from the last check")
    try:
        return _fetch(sync_playwright, _launch, acct, floor_ts, budget, max_open)
    finally:
        lock.release()


def _fetch(sync_playwright, _launch, acct: dict, floor_ts: int, budget: float, max_open: int) -> list[dict]:
    with sync_playwright() as p:
        ctx = _launch(p, _profile(acct["profile_id"]), headless=True)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(_open_url(acct), wait_until="domcontentloaded", timeout=60000)
            _wait_for_mailbox(page)
            out = _read(page, acct, floor_ts, budget, max_open)
            _keep_session(ctx)  # a sign-in Microsoft renewed along the way
            page.wait_for_timeout(300)
            return out
        finally:
            ctx.close()
