"""Check application statuses on Workday candidate portals ("My Applications").

Each company runs its own Workday site with its own candidate account. You save a login once (the password
goes to the OS keychain), and every sync Co-op Coach signs in with your installed Chrome, reads My
Applications, and moves your tracker forward.

Ground rules this module sticks to:
  * It only fills the email + password fields. Workday's hidden "beecatcher" field is a robot trap and is
    never touched.
  * If Workday shows a CAPTCHA, a verification code or anything unexpected, it stops and marks the account
    "needs you". "Sign in yourself" then opens a normal Chrome window where you sign in; the session is kept
    so later checks run on their own again.
  * Each saved login gets its own browser profile, so two people with accounts at the same company never
    share cookies.
"""

import logging
import re
import threading
import time
from pathlib import Path

from . import config, db, mail, store, vault
from .textutil import find_req_ids, title_key

log = logging.getLogger(__name__)
PROFILES = config.DATA_DIR / "browser"

STATUS_RULES = [
    ("withdrawn", re.compile(r"withdr[ae]w", re.I)),
    ("offer", re.compile(r"\boffer\b|hired|accepted", re.I)),
    ("rejected", re.compile(r"no longer (?:under )?consider|not selected|not moving forward|unsuccessful|declined|"
                            r"rejected|position (?:has been )?(?:filled|closed|cancel+ed)|closed|not proceeding|"
                            r"disqualified", re.I)),
    ("interviewing", re.compile(r"interview|screen(?:ing)? call|final round|assessment cent(?:re|er)|superday", re.I)),
    ("oa", re.compile(r"assessment|test|challenge|hirevue|evaluation", re.I)),
    ("applied", re.compile(r"submitted|received|in (?:process|progress|review)|under (?:review|consideration)|"
                           r"considering|reviewing|application complete|\bapplied\b|\bactive\b|pending", re.I)),
]
_HEADER_WORDS = re.compile(r"^(job title|title|status|date submitted|submitted on|job req(?:uisition)?(?: id)?|"
                           r"reference|location|actions?|my applications|active|inactive|candidate home)\b", re.I)
_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b[A-Z][a-z]{2,8} \d{1,2},? \d{4}\b")
_REQ = re.compile(r"^(?:R|JR|REQ|JOB)?[-_ ]?\d{4,}$|^[A-Z]{1,4}-?\d{4,}$", re.I)


def map_status(raw: str) -> str | None:
    for status, rx in STATUS_RULES:
        if rx.search(raw or ""):
            return None if status == "withdrawn" else status
    return None


_ROLE_WORDS = re.compile(r"\b(intern|internship|engineer\w*|developer|analyst|co-?op|scientist|designer|manager|"
                         r"associate|specialist|student|programmer|technician|consultant|automation)\b", re.I)


def _status_cell(cells: list[str]) -> str | None:
    # status columns come after the title, and statuses don't look like job titles
    for c in reversed(cells):
        if len(c) < 60 and len(c.split()) <= 6 and map_status(c) is not None and not _DATE.fullmatch(c.strip()) \
                and not _ROLE_WORDS.search(c):
            return c
    return None


def parse_applications(rows: list[list[str]], text: str) -> list[dict]:
    """Rows come from tables/grids on the page; `text` is the fallback when Workday renders cards."""
    apps, seen = [], set()
    for cells in rows:
        cells = [c.strip() for c in cells if c and c.strip()]
        if len(cells) < 2 or all(_HEADER_WORDS.match(c) for c in cells):
            continue
        status = _status_cell(cells)
        if not status:
            continue
        title = next((c for c in cells if c != status and not _DATE.search(c) and not _REQ.match(c)
                      and not _HEADER_WORDS.match(c) and len(c) > 3), None)
        if title and (title, status) not in seen:
            seen.add((title, status))
            date = next((c for c in cells if _DATE.search(c)), "")
            req = next((c for c in cells if _REQ.match(c)), "") or next(iter(find_req_ids(title)), "")
            apps.append({"title": title, "status_raw": status, "date": date, "req_id": req})
    if apps:
        return apps
    # card layout: a title line followed (within a few lines) by a status line
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if len(ln) < 60 and map_status(ln) is not None and (ln.lower().startswith("status") or i > 0):
            raw = re.sub(r"^status:?\s*", "", ln, flags=re.I)
            for back in range(1, 4):
                if i - back < 0:
                    break
                cand = lines[i - back]
                if not (_DATE.search(cand) or _REQ.match(cand) or _HEADER_WORDS.match(cand) or map_status(cand)):
                    if (cand, raw) not in seen and len(cand) > 3:
                        seen.add((cand, raw))
                        near = lines[max(0, i - back - 2):i + 3]
                        req = next((x for x in near if _REQ.match(x)), "") or next(iter(find_req_ids(cand)), "")
                        apps.append({"title": cand, "status_raw": raw, "date": "", "req_id": req})
                    break
    return apps


# --- browser automation --------------------------------------------------------------------------

_EXTRACT_JS = """() => {
  const rows = [];
  document.querySelectorAll('table tr, [role=row]').forEach(r => {
    const cells = [...r.querySelectorAll('td, th, [role=cell], [role=gridcell], [role=columnheader]')]
      .map(c => c.innerText.trim()).filter(Boolean);
    if (cells.length) rows.push(cells);
  });
  return {rows, text: document.body.innerText};
}"""
CHALLENGE = re.compile(r"verify (?:that )?you(?:'re| are) (?:a )?human|captcha|verification code|one-time (?:pass)?code|"
                       r"two-step|2-step|multi-factor|enter the code", re.I)


def _launch(p, profile_dir: Path, headless: bool):
    profile_dir.mkdir(parents=True, exist_ok=True)
    kw = dict(headless=headless, viewport={"width": 1280, "height": 900})
    try:
        return p.chromium.launch_persistent_context(str(profile_dir), channel="chrome", **kw)
    except Exception:
        return p.chromium.launch_persistent_context(str(profile_dir), **kw)


def _visible(page, selector: str) -> bool:
    try:
        return page.locator(selector).first.is_visible(timeout=500)
    except Exception:
        return False


def _on_login(page) -> bool:
    return "/login" in page.url or _visible(page, '[data-automation-id="password"]')


def _challenge(page) -> bool:
    if _visible(page, 'iframe[src*="captcha"], iframe[title*="captcha" i], [data-automation-id="verificationCode"]'):
        return True
    try:
        return bool(CHALLENGE.search(page.inner_text("body", timeout=2000)[:4000]))
    except Exception:
        return False


def _read_apps(page) -> list[dict]:
    page.wait_for_timeout(1500)
    data = page.evaluate(_EXTRACT_JS)
    apps = parse_applications(data["rows"], data["text"])
    for label in ("Inactive", "Closed", "Past"):  # finished applications usually live on a second tab
        tab = page.get_by_role("tab", name=re.compile(rf"^{label}", re.I))
        if not tab.count():
            tab = page.get_by_role("button", name=re.compile(rf"^{label}", re.I))
        if tab.count():
            try:
                tab.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                more = page.evaluate(_EXTRACT_JS)
                apps += [a for a in parse_applications(more["rows"], more["text"])
                         if (a["title"], a["status_raw"]) not in {(x["title"], x["status_raw"]) for x in apps}]
            except Exception:
                pass
            break
    return apps


def _sign_in(page, username: str, password: str) -> str:
    """Returns 'ok', 'challenge' or 'failed: <reason>'."""
    for sel in ('[data-automation-id="legalNoticeDeclineButton"]', '[data-automation-id="bannerCloseButton"]'):
        if _visible(page, sel):  # decline non-essential cookies
            page.locator(sel).first.click()
    if not _visible(page, '[data-automation-id="email"]'):
        link = page.locator('[data-automation-id="utilityButtonSignIn"], [data-automation-id="auth_signin_link"]')
        if link.count():
            link.first.click()
            page.wait_for_timeout(1000)
    if _challenge(page):
        return "challenge"
    page.locator('input[data-automation-id="email"]').first.fill(username)
    page.locator('input[data-automation-id="password"]').first.fill(password)
    # Workday lays an invisible "click_filter" div over its buttons; clicking it is how a person's click lands
    overlay = page.locator('[data-automation-id="signInSubmitButton"] ~ [data-automation-id="click_filter"], '
                           '[data-automation-id="click_filter"][aria-label*="Sign In" i]')
    if overlay.count():
        overlay.first.click()
    else:
        page.locator('[data-automation-id="signInSubmitButton"]').first.click()
    for _ in range(30):
        page.wait_for_timeout(500)
        if not _on_login(page):
            return "ok"
        if _challenge(page):
            return "challenge"
        err = page.locator('[data-automation-id="errorMessage"], [role="alert"]')
        if err.count() and err.first.is_visible():
            msg = err.first.inner_text().strip()
            if msg:
                return f"failed: {msg[:160]}"
    return "failed: sign-in didn't complete"


# --- one login for every Workday site ------------------------------------------------------------------
# Every company's Workday is a separate account, so people end up re-entering a link, email and password for each
# application. With a saved default login, any Workday job marked as applied becomes a status-checked account with
# no extra typing. The email lives in the database; the password only in the Keychain.

def default_login(pid: int) -> str:
    return db.meta_get(f"workday_default_user:{pid}") or ""


def set_default_login(pid: int, username: str, password: str | None) -> None:
    username = username.strip()
    if password:
        vault.put(vault.workday_default_key(pid), password)
    elif not vault.get(vault.workday_default_key(pid)):
        raise ValueError("password required")
    db.meta_set(f"workday_default_user:{pid}", username)
    with db.tx() as c:  # accounts already on the shared login follow an email change
        c.execute("UPDATE OR IGNORE portal_accounts SET username=? WHERE profile_id=? AND uses_default=1",
                  (username, pid))


def clear_default_login(pid: int) -> None:
    vault.delete(vault.workday_default_key(pid))
    db.meta_set(f"workday_default_user:{pid}", "")
    with db.tx() as c:
        c.execute("DELETE FROM portal_accounts WHERE profile_id=? AND uses_default=1", (pid,))


def password_for(acct: dict) -> str:
    if acct.get("uses_default"):
        return vault.get(vault.workday_default_key(acct["profile_id"])) or ""
    return vault.get(vault.workday_key(acct["profile_id"], acct["site_url"].rstrip("/"), acct["username"])) or ""


def _skipped(pid: int) -> set[str]:
    return set(db.loads(db.meta_get(f"workday_skip:{pid}"), []) or [])


def skip_site(pid: int, site: str, skip: bool = True) -> None:
    """Remember sites someone removed so they aren't re-added from their applications."""
    s = _skipped(pid)
    s = s | {site} if skip else s - {site}
    db.meta_set(f"workday_skip:{pid}", db.dumps(sorted(s)))


def workday_sites_applied(pid: int) -> dict[str, str]:
    """Workday career sites behind applications (applied or further) -> company name."""
    from .sources import ats
    out = {}
    for r in db.rows("SELECT COALESCE(j.url, a.url) AS url, COALESCE(j.company, a.company) AS company"
                     " FROM applications a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.profile_id=? AND a.status!='saved'"
                     " AND COALESCE(j.url, a.url) LIKE '%myworkdayjobs.com%'", (pid,)):
        found = ats.detect(r["url"] or "")
        if found and found[0] == "workday":
            out.setdefault(found[1], r["company"] or "")
    return out


def ensure_default_accounts(pid: int) -> int:
    """Add a status-checked account (on the shared login) for every Workday site you've applied through."""
    user = default_login(pid)
    if not user:
        return 0
    have = {r["site_url"] for r in db.rows("SELECT site_url FROM portal_accounts WHERE profile_id=?", (pid,))}
    skip = _skipped(pid)
    added = 0
    with db.tx() as c:
        for site, company in workday_sites_applied(pid).items():
            if site in have or site in skip:
                continue
            company = company or site.split("//")[1].split(".")[0].upper()
            c.execute("INSERT OR IGNORE INTO portal_accounts (profile_id, kind, site_url, company, username, created_at,"
                      " uses_default) VALUES (?,?,?,?,?,?,1)", (pid, "workday", site, company, user, db.now()))
            added += 1
    return added


def check_account(acct: dict, headless: bool = True, wait_for_user_s: int = 0) -> dict:
    """Sign in (if needed) and read My Applications. Never raises; returns {state, apps, message}."""
    from playwright.sync_api import sync_playwright

    site = acct["site_url"].rstrip("/")
    password = password_for(acct)
    with sync_playwright() as p:
        ctx = _launch(p, PROFILES / f"workday-{acct['id']}", headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(f"{site}/userHome", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            if _on_login(page):
                if wait_for_user_s:  # the person is signing in themselves in a visible window
                    if _visible(page, 'input[data-automation-id="email"]'):
                        page.locator('input[data-automation-id="email"]').first.fill(acct["username"])
                    deadline = time.time() + wait_for_user_s
                    while time.time() < deadline and _on_login(page):
                        page.wait_for_timeout(1000)
                    if _on_login(page):
                        return {"state": "needs_manual", "apps": [], "message": "Sign-in window closed or timed out"}
                    if "userHome" not in page.url:
                        page.goto(f"{site}/userHome", wait_until="domcontentloaded", timeout=45000)
                else:
                    if not password:
                        return {"state": "error", "apps": [], "message": "No saved password in the Keychain"}
                    res = _sign_in(page, acct["username"], password)
                    if res == "challenge":
                        return {"state": "needs_manual", "apps": [],
                                "message": "Workday asked for a CAPTCHA or code. Use “Sign in yourself” once."}
                    if res != "ok":
                        return {"state": "error", "apps": [], "message": res}
                    if "userHome" not in page.url:
                        page.goto(f"{site}/userHome", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            if _on_login(page):
                return {"state": "error", "apps": [], "message": "Still on the sign-in page after signing in"}
            apps = _read_apps(page)
            return {"state": "ok", "apps": apps, "message": f"{len(apps)} application{'s' if len(apps) != 1 else ''} found"}
        except Exception as e:
            return {"state": "error", "apps": [], "message": f"{type(e).__name__}: {str(e)[:160]}"}
        finally:
            ctx.close()


def apply_results(acct: dict, result: dict) -> int:
    """Push statuses into the tracker (forward only) and log each change in the activity feed."""
    changed = 0
    now = db.now()
    for a in result.get("apps", []):
        status = map_status(a["status_raw"])
        if not status:
            continue
        reqs = [a["req_id"]] if a.get("req_id") else []
        job_id = mail.match_by_req(acct["profile_id"], reqs, acct["company"]) or \
            mail.match_application(acct["profile_id"], acct["company"], a["title"])
        if not job_id:
            continue
        mail.remember_req(acct["profile_id"], job_id, reqs)
        did = mail.apply_status(acct["profile_id"], job_id, status, now, f"Workday: {a['status_raw']}")
        changed += int(did)
        eid = mail._event_id(acct["profile_id"], f"workday:{acct['id']}:{title_key(a['title'])}:{a['status_raw'].lower()}")
        with db.tx() as c:
            c.execute("INSERT OR IGNORE INTO email_events (id, profile_id, account_id, message_id, received_at,"
                      " from_name, from_addr, subject, snippet, web_link, status, company, role, job_id,"
                      " classified_by, changed_status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, acct["profile_id"], None, f"workday:{acct['id']}", now, f"{acct['company']} Workday",
                       "candidate portal", f"{a['title']}: {a['status_raw']}", a.get("date", ""),
                       f"{acct['site_url']}/userHome", status, acct["company"], a["title"], job_id, "workday",
                       int(did), now))
    store.refresh_scores()
    return changed


def _record(acct: dict, result: dict, changed: int) -> None:
    msg = result["message"] + (f", {changed} tracker update{'s' if changed != 1 else ''}" if changed else "")
    with db.tx() as c:
        c.execute("UPDATE portal_accounts SET last_check=?, last_status=?, needs_manual=? WHERE id=?",
                  (db.now(), f"{result['state']}: {msg}", int(result["state"] == "needs_manual"), acct["id"]))


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def check_all(profile_id: int | None = None) -> dict:
    if not available():
        return {}
    for pid in [profile_id] if profile_id else [r["id"] for r in db.rows("SELECT id FROM profiles")]:
        ensure_default_accounts(pid)
    sql, params = "SELECT * FROM portal_accounts WHERE needs_manual=0", ()
    if profile_id:
        sql, params = sql + " AND profile_id=?", (profile_id,)
    out = {}
    for acct in db.rows(sql, params):
        result = check_account(acct)
        changed = apply_results(acct, result) if result["state"] == "ok" else 0
        _record(acct, result, changed)
        out[f"{acct['company']} ({acct['username']})"] = f"{result['state']}: {result['message']}"
    return out


_manual: dict[int, str] = {}


def manual_sign_in(acct_id: int) -> None:
    """Open a visible Chrome window so the person can sign in (CAPTCHA, 2FA, first login...)."""
    acct = db.row("SELECT * FROM portal_accounts WHERE id=?", (acct_id,))
    if not acct or _manual.get(acct_id) == "running":
        return
    _manual[acct_id] = "running"

    def run():
        try:
            result = check_account(acct, headless=False, wait_for_user_s=300)
            changed = apply_results(acct, result) if result["state"] == "ok" else 0
            _record(acct, result, changed)
            _manual[acct_id] = result["state"]
        except Exception as e:
            _manual[acct_id] = f"error: {e}"

    threading.Thread(target=run, daemon=True).start()


def manual_state(acct_id: int) -> str:
    return _manual.get(acct_id, "idle")
