"""Notification channels: Discord webhook, ntfy.sh phone push, macOS banner, SMTP email."""

import html
import logging
import os
import platform
import shutil
import smtplib
import subprocess
from email.message import EmailMessage

import httpx

from . import config
from .textutil import when_text

log = logging.getLogger(__name__)


def _mark(j: dict) -> str:
    """⭐ a favourite, 🎯 a role in a track they follow."""
    return "⭐ " if j.get("favourite") else "🎯 " if j.get("niche_hit") else ""


def _fmt_job(j: dict) -> str:
    star = _mark(j)
    loc = ", ".join((j.get("locations") or [])[:2])
    return f"{star}{j['company']} · {j['title']} ({j['score']}%)" + (f" · {loc}" if loc else "")


UPDATE_LABEL = {"oa": "📝 Online assessment", "interviewing": "🎤 Interview", "offer": "🎉 Offer",
                "rejected": "❌ Rejected", "applied": "📨 Application received"}


def _fmt_update(u: dict) -> str:
    who = u.get("company") or "A company"
    role = f" ({u['role']})" if u.get("role") else ""
    return f"{UPDATE_LABEL.get(u['status'], u['status'])}: {who}{role}"


def _fmt_event(e: dict) -> str:
    when = when_text(e["start_ts"])
    return f"{e['title']} · {when}"


DISCORD_ROOM = 5800


def discord(webhook: str, title: str, jobs: list[dict], events: list[dict], updates: list[dict] = ()) -> None:
    embeds = []
    if updates:
        embeds.append({"title": "Application updates from your inbox",
                       "description": "\n".join(_fmt_update(u) for u in updates[:10])[:3500], "color": 0x7C3AED})
    for j in jobs[:9 - bool(updates)]:
        embeds.append({
            "title": f"{_mark(j)}{j['company']}: {j['title']}"[:256],
            "url": j["url"],
            "description": (f"**{j['score']}% match** · " + ", ".join((j.get("locations") or [])[:2])
                            + (f"\n{j['why']}" if j.get("why") else ""))[:1000],
            "color": 0xF5B301 if _mark(j) else 0x4F7DF3,
        })
    if events:
        embeds.append({
            "title": "Upcoming career events",
            "description": "\n".join(f"• [{e['title']}]({e['url']})" if e.get("url") else f"• {_fmt_event(e)}"
                                     for e in events[:8])[:3500],
            "color": 0x22A06B,
        })
    # Discord refuses a whole message over 6,000 characters of embeds: what comes first is what matters most
    room, kept = DISCORD_ROOM, []
    for e in embeds[:10]:
        room -= len(e["title"])
        if room < 80:
            break
        e["description"] = e["description"][:room]
        room -= len(e["description"])
        kept.append(e)
    r = httpx.post(webhook, json={"username": "Co-op Coach", "content": f"**{title}**"[:1900], "embeds": kept}, timeout=20)
    r.raise_for_status()


def ntfy(topic: str, title: str, jobs: list[dict], events: list[dict], updates: list[dict] = ()) -> None:
    # JSON publishing (POST to the server root) keeps titles/messages UTF-8 safe
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    lines = [_fmt_update(u) for u in updates[:5]] + [_fmt_job(j) for j in jobs[:8]] + \
        [f"📅 {_fmt_event(e)}" for e in events[:4]]
    payload = {"topic": topic, "title": title, "message": "\n".join(lines), "tags": ["briefcase"],
               "priority": 4 if updates or any(_mark(j) for j in jobs) else 3}
    click = jobs[0]["url"] if jobs else (events[0].get("url") if events else "")
    if click:
        payload["click"] = click
    r = httpx.post(server, json=payload, timeout=20)
    r.raise_for_status()


def macos(title: str, jobs: list[dict], events: list[dict], updates: list[dict] = ()) -> None:
    if platform.system() != "Darwin" or not shutil.which("osascript"):
        return
    body = "; ".join(_fmt_update(u) for u in updates[:2]) or \
        "; ".join(f"{j['company']}: {j['title']}" for j in jobs[:3]) or "; ".join(e["title"] for e in events[:3])

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    subprocess.run(["osascript", "-e", f'display notification "{esc(body[:220])}" with title "{esc(title[:80])}" '
                                       f'sound name "Glass"'], check=False, timeout=10)


def email(to: str, title: str, jobs: list[dict], events: list[dict], updates: list[dict] = ()) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST is not set in .env")
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "")
    msg["To"] = to
    text = "".join(f"{_fmt_update(u)}\n" for u in updates) + ("\n" if updates else "")
    text += "\n".join(f"- {_fmt_job(j)}\n  {j['url']}" for j in jobs)
    if events:
        text += "\n\nUpcoming career events:\n" + "\n".join(f"- {_fmt_event(e)} {e.get('url') or ''}" for e in events)
    msg.set_content(text + "\n\n— Co-op Coach")
    h = html.escape
    rows = "".join(
        f"<tr><td style='padding:6px 0'><b>{_mark(j)}{h(j['company'])}</b><br>"
        f"<a href='{h(j['url'], quote=True)}'>{h(j['title'])}</a><br><small>{j['score']}% match · "
        f"{h(', '.join((j.get('locations') or [])[:2]))}</small></td></tr>" for j in jobs)
    ev_items = []
    for e in events:
        link = f" <a href='{h(e['url'], quote=True)}'>details</a>" if e.get("url") else ""
        ev_items.append(f"<li>{h(_fmt_event(e))}{link}</li>")
    ev = "".join(ev_items)
    upd = "".join(f"<li>{h(_fmt_update(u))}</li>" for u in updates)
    msg.add_alternative(f"<h3>{h(title)}</h3>" + (f"<ul>{upd}</ul>" if upd else "") + f"<table>{rows}</table>"
                        + (f"<h4>Upcoming career events</h4><ul>{ev}</ul>" if ev else ""), subtype="html")
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def send(profile_name: str, notify_prefs: dict, jobs: list[dict], events: list[dict],
         updates: list[dict] = (), title: str = "") -> list[str]:
    """Fan out to every configured channel. Returns a list of channel results for logging."""
    if not jobs and not events and not updates:
        return []
    fav = sum(1 for j in jobs if j.get("favourite"))
    parts = []
    if updates:
        parts.append(f"{len(updates)} application update{'s' if len(updates) != 1 else ''}")
    if jobs:
        parts.append(f"{len(jobs)} new match{'es' if len(jobs) != 1 else ''}")
    if fav:
        parts.append(f"{fav} from favourites")
    in_niches = sum(1 for j in jobs if j.get("niche_hit") and not j.get("favourite"))
    if in_niches:
        parts.append(f"{in_niches} from tracks you follow")
    if events:
        parts.append(f"{len(events)} new event{'s' if len(events) != 1 else ''}")
    title = title or f"Co-op Coach · {profile_name}: " + ", ".join(parts)
    results = []
    channels = [
        ("discord", notify_prefs.get("discord_webhook"), discord),
        ("ntfy", notify_prefs.get("ntfy_topic"), ntfy),
        ("email", notify_prefs.get("email"), email),
    ]
    for name, target, fn in channels:
        if not target:
            continue
        try:
            fn(target, title, jobs, events, updates)
            results.append(f"{name}: sent")
        except Exception as e:  # one broken channel shouldn't block the others
            log.warning("%s notification failed: %s", name, e)
            results.append(f"{name}: failed ({e})")
    if config.get("notifications.macos", True):
        try:
            macos(title, jobs, events, updates)
            results.append("macos: sent")
        except Exception as e:
            results.append(f"macos: failed ({e})")
    return results


def send_text(notify_prefs: dict, title: str, lines: list[str], click: str = "") -> list[str]:
    """A short plain-text alert (Autopilot's weekly recap, follow-up nudges) to every configured channel."""
    body = "\n".join(lines)
    results = []
    if notify_prefs.get("discord_webhook"):
        try:
            httpx.post(notify_prefs["discord_webhook"], json={"username": "Co-op Coach", "content": f"**{title}**\n{body}"[:1900]},
                       timeout=20).raise_for_status()
            results.append("discord: sent")
        except Exception as e:
            results.append(f"discord: failed ({e})")
    if notify_prefs.get("ntfy_topic"):
        try:
            server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
            payload = {"topic": notify_prefs["ntfy_topic"], "title": title, "message": body or title, "tags": ["robot"]}
            if click:
                payload["click"] = click
            httpx.post(server, json=payload, timeout=20).raise_for_status()
            results.append("ntfy: sent")
        except Exception as e:
            results.append(f"ntfy: failed ({e})")
    if notify_prefs.get("email") and os.environ.get("SMTP_HOST"):
        try:
            msg = EmailMessage()
            msg["Subject"], msg["To"] = title, notify_prefs["email"]
            msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "")
            msg.set_content(body + "\n\n— Co-op Coach")
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
            results.append("email: sent")
        except Exception as e:
            results.append(f"email: failed ({e})")
    if config.get("notifications.macos", True) and platform.system() == "Darwin" and shutil.which("osascript"):
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{esc((lines[0] if lines else "")[:220])}" with title '
                                           f'"{esc(title[:80])}" sound name "Glass"'], check=False, timeout=10)
        results.append("macos: sent")
    return results
