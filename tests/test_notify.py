"""Alerts really leave in the shape each service takes: Discord webhooks, ntfy and email, against local stand-ins. Discord refuses
a message over its limits, so long ones have to be cut to fit; one broken channel doesn't stop the others; what a posting says
can't break out into an email's HTML.

Run: .venv/bin/python -m tests.test_notify
"""

import json
import os
import smtplib
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-notify-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="")

from coopcoach import notify  # noqa: E402

results: dict[str, bool] = {}
GOT: list[dict] = []


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        GOT.append({"path": self.path, "json": json.loads(body or b"{}")})
        code = 500 if "broken" in self.path else 204
        if GOT[-1]["json"].get("embeds") is not None and discord_too_big(GOT[-1]["json"]):
            code = 400  # what Discord does
        self.send_response(code)
        self.end_headers()

    def log_message(self, *a):
        pass


def discord_too_big(p: dict) -> bool:
    e = p["embeds"]
    return len(e) > 10 or len(p.get("content") or "") > 2000 or any(len(x.get("title") or "") > 256 or len(x.get("description") or "") > 4096 for x in e) \
        or sum(len(x.get("title") or "") + len(x.get("description") or "") for x in e) > 6000


def job(i: int, why: str = "") -> dict:
    return {"company": f"Company {i}", "title": f"Software Intern {i}", "url": f"https://x.test/{i}", "score": 80 + i % 10, "locations": ["Toronto, ON"], "why": why, "favourite": i == 0}


EVENT = {"title": "Career fair", "start_ts": 1893456000, "url": "https://x.test/e"}
UPDATE = {"status": "interviewing", "company": "Acme", "role": "SWE Intern"}


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    notify.macos = lambda *a, **k: None
    os.environ["NTFY_SERVER"] = url + "/ntfy"

    notify.discord(url + "/d", "New matches", [job(i) for i in range(3)], [EVENT], [UPDATE])
    d = GOT[-1]["json"]
    check("Discord gets its shape: a title, an embed for each, with links", d["content"] == "**New matches**" and len(d["embeds"]) == 5
          and d["embeds"][1]["url"] == "https://x.test/0" and "⭐" in d["embeds"][1]["title"] and "Interview" in d["embeds"][0]["description"])
    long = "This posting fits because of many things you have done. " * 40
    r = notify.send("Sam", {"discord_webhook": url + "/d"}, [job(i, long) for i in range(20)], [dict(EVENT, title="An event with a long name " * 20) for _ in range(20)], [UPDATE] * 20)
    check(f"a very long alert is cut to what Discord accepts, not refused {r}", r == ["discord: sent", "macos: sent"] and not discord_too_big(GOT[-1]["json"]))

    notify.ntfy("my-topic", "New matches · été", [job(1)], [EVENT], [UPDATE])
    n = GOT[-1]
    check("ntfy gets its shape, with accents intact and the first link", n["path"] == "/ntfy" and n["json"]["topic"] == "my-topic" and "été" in n["json"]["title"]
          and n["json"]["click"] == "https://x.test/1" and n["json"]["priority"] == 4)

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=0):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def starttls(self):
            sent.append("tls")

        def login(self, u, p):
            sent.append(("login", u))

        def send_message(self, m):
            sent.append(m)
    real, smtplib.SMTP = smtplib.SMTP, FakeSMTP
    os.environ.update(SMTP_HOST="smtp.test", SMTP_FROM="coach@example.com", SMTP_USER="u", SMTP_PASSWORD="p")
    try:
        nasty = {"company": "<script>alert(1)</script>", "title": "Intern <b>x</b>", "url": "https://x.test/'><img src=x>", "score": 90, "locations": ["<i>Toronto</i>"]}
        notify.email("sam@example.com", "New matches", [nasty], [EVENT], [UPDATE])
        m = sent[-1]
        html = m.get_body(("html",)).get_content()
        check("email goes over TLS with a plain and an HTML part", sent[0] == "tls" and m["To"] == "sam@example.com" and m.get_body(("plain",)) is not None)
        check("what a posting says can't break out into the email's HTML", "<script>" not in html and "<b>x</b>" not in html and "<img" not in html and "&lt;script&gt;" in html)
        os.environ.pop("SMTP_HOST")
        r = notify.send("Sam", {"email": "sam@example.com", "ntfy_topic": "t"}, [job(1)], [], [])
        check("email not set up is reported, and doesn't stop the other channels", any(x.startswith("email: failed") for x in r) and "ntfy: sent" in r)
    finally:
        smtplib.SMTP = real
    r = notify.send("Sam", {"discord_webhook": url + "/broken", "ntfy_topic": "t"}, [job(1)], [], [])
    check("a broken channel says so, and the next still goes", any(x.startswith("discord: failed") for x in r) and "ntfy: sent" in r)
    before = len(GOT)
    check("nothing new: nothing sent", notify.send("Sam", {"discord_webhook": url + "/d"}, [], [], []) == [] and len(GOT) == before)
    r = notify.send_text({"discord_webhook": url + "/d", "ntfy_topic": "t"}, "Weekly recap", ["line one", "x" * 5000], click="https://x.test/r")
    check("a plain alert (the weekly recap) goes to both, within Discord's limit", "discord: sent" in r and "ntfy: sent" in r and len(GOT[-2]["json"]["content"]) <= 2000)
    srv.shutdown()
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
