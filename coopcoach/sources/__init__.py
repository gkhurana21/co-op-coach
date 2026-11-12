"""Job sources. Each module exposes fetch(...) -> list[dict] of raw postings with keys:

source, company, title, url, locations (list[str]), terms (list[str]), category (optional),
description (optional), posted_at (unix seconds, optional), sponsorship (optional), tier_hint (optional)
"""

import contextlib
import ipaddress
import socket
import threading

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


class BlockedURL(ValueError):
    """A link that points at this computer or a private network."""


def check_public(url) -> None:
    """Links someone (or an AI reading an email or a web page) hands the app are only fetched from the public
    internet: never this computer (localhost, this app's own API), the home network, or cloud metadata addresses.
    Raises BlockedURL. A host that doesn't resolve is let through: the request itself fails."""
    u = httpx.URL(str(url))
    if u.scheme not in ("http", "https"):
        raise BlockedURL(f"only http(s) links can be read, not {u.scheme or 'that'}")
    host = (u.host or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
        raise BlockedURL("links to this computer or a private network aren't read")
    try:
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise BlockedURL("links to this computer or a private network aren't read")


def client(timeout: float = 30, public_only: bool = False) -> httpx.Client:
    """public_only checks every request, redirects included, with check_public (for links that came from a person
    or an AI rather than from Co-op Coach's own source lists)."""
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"},
        event_hooks={"request": [lambda req: check_public(req.url)]} if public_only else None,
    )


_shared: httpx.Client | None = None
_shared_lock = threading.Lock()


def pooled():
    """For Co-op Coach's own board lists (never links from a person or an AI): one client shared by every board
    read in a sync, so the connections to Greenhouse, Lever, Ashby and YC stay open from one board to the next
    instead of a new TLS handshake each time. Used as `with pooled() as c:`; it isn't closed after."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = httpx.Client(timeout=30, follow_redirects=True, headers={
                "User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"},
                limits=httpx.Limits(max_connections=160, max_keepalive_connections=80))
    return contextlib.nullcontext(_shared)
