"""Per-person passcode lock, exercised through the real app + middleware on a throwaway database.

Run: .venv/bin/python -m tests.test_lock
"""

import os
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="coopcoach-locktest-")
os.environ["COOPCOACH_DATA"] = TMP  # must be set before the app is imported

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import auth, db  # noqa: E402
from coopcoach.server import app  # noqa: E402

db.init()
results: dict[str, bool] = {}


def check(name: str, ok: bool) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def client(ip: str = "127.0.0.1") -> TestClient:
    # no `with`: skips the startup sync/scheduler
    return TestClient(app, base_url="http://localhost", client=(ip, 50000))


alex, sam, stranger = client(), client(), client()
A = alex.post("/api/profiles", json={"name": "Alex", "passcode": "maple-1234"}).json()["id"]
B = sam.post("/api/profiles", json={"name": "Sam", "passcode": "sam-pass-99"}).json()["id"]

check("owner can open their own matches", alex.get(f"/api/jobs?profile_id={A}").status_code == 200)
check("someone else's browser is locked out (query)", stranger.get(f"/api/jobs?profile_id={A}").status_code == 423)
check("locked out (JSON body)", stranger.post("/api/chat", json={"profile_id": A, "messages": [{"role": "user", "content": "hi"}]}).status_code == 423)
check("locked out (the chat as it's written)", stranger.post("/api/chat/stream", json={"profile_id": A, "messages": [{"role": "user", "content": "hi"}]}).status_code == 423)
check("locked out (starting the chat ahead)", stranger.post("/api/chat/warm", json={"profile_id": A}).status_code == 423)
check("locked out (path)", stranger.put(f"/api/profiles/{A}/prefs", json={"aim": "yc"}).status_code == 423)
check("locked out (resume upload)", stranger.post(f"/api/profiles/{A}/resume", files={"file": ("r.txt", b"x" * 200)}).status_code == 423)

with db.tx() as c:
    doc = c.execute("INSERT INTO documents (profile_id, kind, job_id, title, content, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)", (A, "cover_letter", "j1", "Secret letter", "Dear...", 1, 1)).lastrowid
check("locked out (letter owned by Alex)", stranger.get(f"/api/letters/{doc}").status_code == 423)

state = stranger.get("/api/state").json()["profiles"]
alex_view = next(p for p in state if p["id"] == A)
check("state hides a locked person's data", alex_view.get("locked") and set(alex_view) == {"id", "name", "locked", "has_passcode"})
check("passcode is not stored in plain text",
      b"maple-1234" not in (open(os.path.join(TMP, "coopcoach.db"), "rb").read() if os.path.exists(os.path.join(TMP, "coopcoach.db"))
                            else str(db.rows("SELECT * FROM profiles")).encode()))

r = stranger.post("/api/auth/unlock", json={"profile_id": A, "passcode": "wrong"})
check("wrong passcode rejected", r.status_code == 403)
for _ in range(4):
    stranger.post("/api/auth/unlock", json={"profile_id": A, "passcode": "wrong"})
r = stranger.post("/api/auth/unlock", json={"profile_id": A, "passcode": "maple-1234"})
check("guessing gets throttled (even the right code waits)", r.status_code == 429)
auth._failures.clear()

check("Sam can unlock Sam", stranger.post("/api/auth/unlock", json={"profile_id": B, "passcode": "sam-pass-99"}).status_code == 200)
check("...and still can't open Alex", stranger.get(f"/api/jobs?profile_id={A}").status_code == 423)
stranger.post("/api/auth/unlock", json={"profile_id": A, "passcode": "maple-1234"})
check("unlocking Alex locks Sam again (one person at a time)", stranger.get(f"/api/jobs?profile_id={B}").status_code == 423)

check("removed Workday credentials cannot be saved or retrieved", all(r.status_code == 404 for r in (
    alex.post("/api/portals", json={"profile_id": A, "username": "alex@example.com", "password": "fictional"}),
    alex.get(f"/api/portals/default/password?profile_id={A}"))))

alex.post("/api/auth/lock")
check("Lock button locks immediately", alex.get(f"/api/jobs?profile_id={A}").status_code == 423)

alex.post("/api/auth/unlock", json={"profile_id": A, "passcode": "maple-1234"})
real_idle = auth._idle_limit
auth._idle_limit = lambda: 1.0
time.sleep(1.3)
check("auto-locks after inactivity", alex.get(f"/api/jobs?profile_id={A}").status_code == 423)
auth._idle_limit = real_idle

r = client().get("/api/state", headers={"Host": "evil.example"})
check("requests for other host names are refused (DNS rebinding)", r.status_code == 400)

alex.post("/api/auth/unlock", json={"profile_id": A, "passcode": "maple-1234"})
r = alex.post(f"/api/profiles/{A}/passcode", json={"current": "nope", "new": "abcd"})
check("changing a passcode needs the current one", r.status_code == 403)
auth._failures.clear()
r = alex.post(f"/api/profiles/{A}/passcode", json={"current": "maple-1234", "new": "new-code-5678"})
check("changing it with the current one works", r.status_code == 200 and
      stranger.post("/api/auth/unlock", json={"profile_id": A, "passcode": "new-code-5678"}).status_code == 200)

failed = [k for k, v in results.items() if not v]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
