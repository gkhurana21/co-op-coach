"""Secrets live in the operating system's credential store (macOS Keychain, Windows Credential Manager,
Linux Secret Service) via `keyring`, never in the SQLite database. The database only keeps a reference.

Keys look like "workday:<profile_id>:<site url>:<username>" or "imap:<profile_id>:<address>".
"""

import logging
import secrets
import string

log = logging.getLogger(__name__)
SERVICE = "Co-op Coach"
REF = "keychain"  # what we store in the DB instead of the secret


def available() -> bool:
    try:
        import keyring
        from keyring.backends import fail
        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


def backend_name() -> str:
    try:
        import keyring
        return type(keyring.get_keyring()).__module__.split(".")[-1]
    except Exception:
        return "none"


def put(key: str, secret: str) -> None:
    import keyring
    keyring.set_password(SERVICE, key, secret)


def get(key: str) -> str | None:
    import keyring
    try:
        return keyring.get_password(SERVICE, key)
    except Exception as e:  # locked keychain, denied access prompt, etc.
        log.warning("keychain read failed for %s: %s", key.split(":")[0], e)
        return None


def delete(key: str) -> None:
    import keyring
    from keyring.errors import PasswordDeleteError
    try:
        keyring.delete_password(SERVICE, key)
    except PasswordDeleteError:
        pass


def generate_password(length: int = 18) -> str:
    """Strong password that satisfies typical Workday/ATS rules (upper, lower, digit, symbol)."""
    symbols = "!@#$%^&*-_=+?"
    alphabet = string.ascii_letters + string.digits + symbols
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
                and any(c in symbols for c in pw)):
            return pw


def email_key(profile_id: int, address: str) -> str:
    return f"mail:{profile_id}:{address.lower()}"


def workday_key(profile_id: int, site_url: str, username: str) -> str:
    return f"workday:{profile_id}:{site_url}:{username.lower()}"


def workday_default_key(profile_id: int) -> str:
    """The one login someone uses on every company's Workday site."""
    return f"workday-default:{profile_id}"
