"""The passcode guard and the routes always agree on whose data a request touches: a profile id written twice, or
any way but as a plain number ("2.0", "+2", "/api/letters/1.0"), is refused (400) instead of slipping past the
lock, and normal requests still work.

Run: .venv/bin/python -m tests.test_guard
"""

import json
import os
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-guard-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import db  # noqa: E402
from coopcoach.server import app  # noqa: E402

db.init()
results: dict[str, bool] = {}


def check(name: str, ok: bool) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def client() -> TestClient:
    # no `with`: skips the startup sync/scheduler
    return TestClient(app, base_url="http://localhost")


owner, stranger = client(), client()
O = owner.post("/api/profiles", json={"name": "Open"}).json()["id"]  # no passcode
L = owner.post("/api/profiles", json={"name": "Lee", "passcode": "lee-pass-77"}).json()["id"]
with db.tx() as c:
    for pid in (O, L):
        c.execute("INSERT INTO applications (profile_id, job_id, status, updated_at, req_id) VALUES (?,?,?,?,?)",
                  (pid, "j1", "applied", 1, f"R-{pid}"))
    doc = c.execute("INSERT INTO documents (profile_id, kind, job_id, title, content, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)", (L, "cover_letter", "j1", "Lee's letter", "Dear team", 1, 1)).lastrowid
    c.execute("INSERT INTO niches (id, profile_id, name, created_at) VALUES (?,?,?,?)", ("custom-lee", L, "Robots", 1))
    mail = c.execute("INSERT INTO email_accounts (profile_id, kind, address, created_at) VALUES (?,?,?,?)",
                     (L, "imap", "lee@example.com", 1)).lastrowid
    c.execute("INSERT INTO email_events (id, profile_id, subject, created_at) VALUES (?,?,?,?)",
              ("ev-lee", L, "Interview", 1))


def snapshot() -> list:
    return [db.rows(f"SELECT * FROM {t} ORDER BY 1") for t in
            ("profiles", "applications", "documents", "niches", "email_accounts", "email_events", "portal_accounts")]


before = snapshot()
refused = (400, 423)

# the reviewer's exact bypasses
check("profile_id twice (open one first) is refused",
      stranger.get(f"/api/applications?profile_id={O}&profile_id={L}").status_code == 400)
check("profile_id twice (locked one first) is refused",
      stranger.get(f"/api/applications?profile_id={L}&profile_id={O}").status_code == 400)
check("?profile_id=2.0 is refused", stranger.get(f"/api/applications?profile_id={L}.0").status_code == 400)
check("niches with ?profile_id=2.0 is refused", stranger.get(f"/api/niches?profile_id={L}.0").status_code == 400)
check("deleting a niche with ?profile_id=2.0 is refused",
      stranger.delete(f"/api/niches/custom-lee?profile_id={L}.0").status_code == 400)
check("JSON profile_id \"2.0\" is refused", stranger.put("/api/applications/req", json={
    "profile_id": f"{L}.0", "job_id": "j1", "req_id": "PWNED"}).status_code == 400)
check("/api/letters/1.0 is refused", stranger.get(f"/api/letters/{doc}.0").status_code == 400)
check("/api/profiles/2.0/prefs is refused",
      stranger.put(f"/api/profiles/{L}.0/prefs", json={"name": "PWNED"}).status_code == 400)
check("DELETE /api/profiles/2.0 is refused", stranger.delete(f"/api/profiles/{L}.0").status_code == 400)

# other spellings of the same number
odd = ["%2B" + str(L), "-" + str(L), "%20" + str(L), str(L) + "%20", "0" + str(L), f"{L}e0", f"{L}_0", "", "%D9%A2"]
check("+2, -2, ' 2', '2 ', 02, 2e0, 2_0, blank and non-ASCII digits are refused in the query",
      all(stranger.get(f"/api/applications?profile_id={v}").status_code == 400 for v in odd))
check("...and in owner paths", all(stranger.put(f"/api/profiles/{v}/prefs", json={"name": "PWNED"}).status_code
                                   in refused for v in odd if v))
check("JSON profile_id that isn't a plain number (2.0, true, ' 2', [2], {}) is refused", all(
    stranger.put("/api/applications/req", json={"profile_id": v, "job_id": "j1", "req_id": "PWNED"}).status_code == 400
    for v in (float(L), True, f" {L}", [L], {"id": L}, f"+{L}")))
r = stranger.put("/api/applications/req", content=json.dumps({"profile_id": L, "job_id": "j1", "req_id": "PWNED"}),
                 headers={"Content-Type": "Application/JSON"})
check("a JSON body sent as \"Application/JSON\" is still checked (423)", r.status_code == 423)
check("inbox and inbox item paths with 1.0 or a locked owner are refused", all(s in refused for s in (
    stranger.put(f"/api/email/accounts/{mail}.0", json={"enabled": False}).status_code,
    stranger.delete(f"/api/email/accounts/{mail}.0").status_code,
    stranger.put("/api/email/events/ev-lee", json={"ignored": True}).status_code)))
check("none of that changed anything", snapshot() == before)

# normal use is unchanged
check("the open profile still opens", stranger.get(f"/api/applications?profile_id={O}").status_code == 200)
check("the locked profile still says locked", stranger.get(f"/api/applications?profile_id={L}").status_code == 423)
check("its owner still gets in (query, path, letter)", owner.get(f"/api/applications?profile_id={L}").status_code == 200
      and owner.get(f"/api/niches?profile_id={L}").status_code == 200
      and owner.get(f"/api/letters/{doc}").json().get("title") == "Lee's letter")
check("pages under owner paths still work (/api/letters/formats)",
      owner.get(f"/api/letters/formats?profile_id={O}").status_code == 200)
r = owner.put("/api/applications/req", json={"profile_id": str(O), "job_id": "j1", "req_id": "R-77"})
check("a JSON profile_id sent as a digit string still works", r.status_code == 200 and r.json()["req_id"] == "R-77")
check("the owner can still rename their profile",
      owner.put(f"/api/profiles/{L}/prefs", json={"name": "Lee P"}).json().get("name") == "Lee P")
check("the owner can still fix an inbox item",
      owner.put("/api/email/events/ev-lee", json={"ignored": True}).status_code == 200)

failed = [k for k, v in results.items() if not v]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
