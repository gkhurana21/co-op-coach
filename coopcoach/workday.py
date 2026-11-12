"""Browser helpers for form filling and owner-driven sign-in.

Workday account monitoring has been removed. Old account records are inert;
only explicit profile deletion cleans up their legacy credentials and sessions.
"""
import re
import shutil
from pathlib import Path

from . import config, db, vault

PROFILES = config.DATA_DIR / "browser"

CHALLENGE = re.compile(r"verify (?:that )?you(?:'re| are) (?:a )?human|captcha|verification code|one-time (?:pass)?code|"
                       r"two-step|2-step|multi-factor|enter the code", re.I)


def _launch(p, profile_dir: Path, headless: bool):
    profile_dir.mkdir(parents=True, exist_ok=True)
    kw = dict(headless=headless, viewport={"width": 1280, "height": 900})
    for channel in ("chrome", "msedge"):  # your Chrome, or Edge (every Windows 11 laptop has it)
        try:
            return p.chromium.launch_persistent_context(str(profile_dir), channel=channel, **kw)
        except Exception:
            continue
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


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False



def forget_legacy_accounts(pid: int) -> None:
    """Called only when the person explicitly deletes their Co-op Coach profile."""
    for account in db.rows("SELECT id, site_url, username FROM portal_accounts WHERE profile_id=?", (pid,)):
        vault.delete(vault.workday_key(pid, account["site_url"], account["username"]))
        shutil.rmtree(PROFILES / f"workday-{account['id']}", ignore_errors=True)
    vault.delete(vault.workday_default_key(pid))
