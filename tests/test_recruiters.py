"""Recruiters: everyone who writes to you about an application is kept (name, email, title, phone, LinkedIn from
their sign-off), grouped by kind of company and company. Automated senders aren't people; a hiring-system email
signed by someone keeps that someone; shared inboxes are marked; your own address and quoted replies never leak in;
hand edits stay; a locked profile's contacts stay locked. No AI (built-in rules).

Run: .venv/bin/python -m tests.test_recruiters
"""

import csv
import io
import os
import tempfile
import time

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-recruiters-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, contacts, db, jobdetail, mail  # noqa: E402
from coopcoach.server import app  # noqa: E402

jobdetail.ensure_description = lambda *a, **k: ""  # offline: a prep sheet doesn't wait on the posting's web page
db.init()
results: dict[str, bool] = {}


def check(name: str, ok: bool) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def email(subject, body, frm, addr, mid, when=None):
    return {"message_id": mid, "subject": subject, "body": body, "from_name": frm, "from_addr": addr,
            "received_at": when or db.now(), "web_link": ""}


def contact(pid, key):
    return db.row("SELECT * FROM contacts WHERE profile_id=? AND key=?", (pid, key))


PRIYA = """Hi Alex,

Thanks for applying to the Software Engineering Intern role at Northwind Robotics. We'd like to invite you to an
interview.

Best,
Priya Shah
University Recruiter | Northwind Robotics
+1 (416) 555-0123
linkedin.com/in/priya-shah-12/

On Mon, Oct 12, 2026 at 9:00 AM Alex Chen <alex@gmail.com> wrote:
> Thanks,
> Alex Chen
> 647-555-0199
> linkedin.com/in/alex-chen
"""

# --- reading a sign-off ---
sig = contacts.signature(PRIYA)
check("sign-off: name, title, phone, LinkedIn and company", sig == {
    "name": "Priya Shah", "title": "University Recruiter", "phone": "+1 (416) 555-0123",
    "linkedin": "https://linkedin.com/in/priya-shah-12", "company": "Northwind Robotics"})
check("a reply's quoted history (the student's own sign-off) is ignored",
      contacts.signature("Sounds good!\n\nOn Tue, Alex Chen <alex@gmail.com> wrote:\n> Best,\n> Alex Chen\n> "
                         "linkedin.com/in/alex-chen")["name"] == "" and
      "alex-chen" not in contacts.signature(PRIYA)["linkedin"])
one = contacts.signature("Please complete the CodeSignal assessment within 7 days.\nThanks, Priya | Recruiting, Ramp")
check("one-line sign-off 'Thanks, Priya | Recruiting, Ramp'", one["name"] == "Priya" and "Recruiting" in one["title"])
team = contacts.signature("All the best,\n\nSam\n\nLumen Robotics Hiring Team")
check("'Lumen Robotics Hiring Team' is the company, not Sam's title",
      team["name"] == "Sam" and team["title"] == "" and team["company"] == "Lumen Robotics")
check("a team sign-off isn't a person", contacts.signature("Best, The Shopify Recruiting Team")["name"] == "")
check("a requisition number isn't a phone number",
      contacts.signature("Your application (R-0000181999) was received.\nBest,\nSam\nRecruiter\nReq 0000181999")["phone"] == "")

# --- who wrote it ---
S = contacts._sender
check("a person", S("Priya Shah (she/her)", "Priya@Northwind.com") == ("person", "Priya Shah", "priya@northwind.com"))
check("no-reply and hiring systems are automated",
      S("RBC", "noreply@rbc.wd3.myworkday.com")[0] == S("Priya via Greenhouse", "no-reply@greenhouse.io")[0] == "automated")
check("careers@ / university-recruiting@ / talent.acquisition.ca@ are shared inboxes",
      {S("", a)[0] for a in ("careers@x.com", "university-recruiting@x.com", "talent.acquisition.ca@x.com")} == {"team"})
check("ta.smith@ is a person, not a shared inbox", S("Ta Smith", "ta.smith@x.com")[0] == "person")

# --- the inbox scan ---
owner, stranger = TestClient(app, base_url="http://localhost"), TestClient(app, base_url="http://localhost")
pid = owner.post("/api/profiles", json={"name": "Alex Chen"}).json()["id"]
with db.tx() as c:
    c.execute("UPDATE profiles SET profile_json=? WHERE id=?", ('{"name": "Alex Chen", "email": "alex@gmail.com"}', pid))
T0 = db.now() - 5 * 86400
stats = mail.process_messages(pid, None, [
    email("Interview invitation: Software Engineering Intern", PRIYA, "Priya Shah", "priya@northwindrobotics.com",
          "a", T0),
    email("Thank you for applying to Jane Street", "Hi Alex,\n\nWe received your application for the Software "
          "Engineer Intern role. We will review it shortly.\n\nBest,\nSam Lee\nCampus Recruiting, Jane Street",
          "Jane Street", "noreply@janestreet.wd1.myworkdayjobs.com", "b", T0 + 10),
    email("Your application to Scotiabank", "Thank you for your interest in Scotiabank. We received your application "
          "for Data Analyst Co-op.\n\nRegards,\nScotiabank Talent Acquisition", "Scotiabank Careers",
          "careers@scotiabank.com", "c", T0 + 20),
    email("Your weekly job alerts", "Here are 10 new jobs for you", "LinkedIn", "jobs-noreply@linkedin.com", "d"),
    email("Update on your application", "Hi Alex, unfortunately we have decided to move forward with other candidates"
          " for the Quant Developer Intern role.\n\nKind regards,\nMaria Gomez\nTalent Acquisition Partner",
          "Maria Gomez via Greenhouse", "no-reply@greenhouse.io", "e", T0 + 30),
    email("Re: Interview invitation", "Thanks so much, see you then!\n\nBest,\nAlex Chen", "Alex Chen",
          "alex@gmail.com", "f", T0 + 40),
])
p = contact(pid, "priya@northwindrobotics.com")
check("a recruiter's email saves name, address, title, phone, LinkedIn, company", p and (
    p["name"], p["title"], p["phone"], p["linkedin"], p["company"], p["kind"]) == (
    "Priya Shah", "University Recruiter", "+1 (416) 555-0123", "https://linkedin.com/in/priya-shah-12",
    "Northwind Robotics", "person"))
check("…with the email's status and the application it's about",
      p["last_status"] == "interviewing" and p["emails"] == 1 and len(db.loads(p["job_ids"], [])) == 1)
sam = contact(pid, "name:jane street:sam lee")
check("a hiring-system email signed by a person keeps that person (no address to write back to)",
      sam and sam["name"] == "Sam Lee" and sam["email"] is None and sam["company"] == "Jane Street")
sc = contact(pid, "careers@scotiabank.com")
check("careers@ is kept as a shared inbox", sc and sc["kind"] == "team" and sc["company"] == "Scotiabank")
maria = db.row("SELECT * FROM contacts WHERE profile_id=? AND name='Maria Gomez'", (pid,))
check("'Maria Gomez via Greenhouse' is a person, never her own company",
      maria and maria["kind"] == "person" and maria["company"] != "Maria Gomez" and maria["title"] == "Talent Acquisition Partner")
check("job alerts and your own replies add no one",
      not db.row("SELECT 1 FROM contacts WHERE profile_id=? AND (email LIKE '%linkedin%' OR email='alex@gmail.com'"
                 " OR name='Alex Chen')", (pid,)))
n_before = db.row("SELECT COUNT(*) AS n FROM contacts WHERE profile_id=?", (pid,))["n"]

# a later email from the same person: one row, refreshed
mail.process_messages(pid, None, [email("Your interview is confirmed", "Hi Alex, confirming your interview on Friday"
                                        " at 2 PM.\n\nBest,\nPriya", "Priya Shah", "priya@northwindrobotics.com", "g",
                                        T0 + 100)])
p2 = contact(pid, "priya@northwindrobotics.com")
check("the same recruiter again: one row, two emails, newest subject",
      db.row("SELECT COUNT(*) AS n FROM contacts WHERE profile_id=?", (pid,))["n"] == n_before
      and p2["emails"] == 2 and p2["last_subject"] == "Your interview is confirmed" and p2["first_seen"] == T0
      and p2["title"] == "University Recruiter")
mail.process_messages(pid, None, [email("Interview invitation: Software Engineering Intern", PRIYA, "Priya Shah",
                                        "priya@northwindrobotics.com", "a", T0)])
check("the same email read twice counts once", contact(pid, "priya@northwindrobotics.com")["emails"] == 2)

# --- the tab ---
d = owner.get(f"/api/recruiters?profile_id={pid}").json()
labels = [g["label"] for g in d["groups"]]
check("grouped by kind of company (Quant before Other)", labels.index("Quant & Trading") < labels.index("Other"))
north = next(c for g in d["groups"] for c in g["companies"] if c["company"] == "Northwind Robotics")
check("then by company, with each person's application", north["people"][0]["name"] == "Priya Shah"
      and north["people"][0]["applications"] and north["people"][0]["applications"][0]["status"] == "interviewing")
everyone = [x for g in d["groups"] for c in g["companies"] for x in c["people"]]
check("shared inboxes are left out unless asked for", all(x["kind"] == "person" for x in everyone)
      and d["teams"] == 1 and any(x["kind"] == "team" for g in owner.get(
          f"/api/recruiters?profile_id={pid}&teams=true").json()["groups"] for c in g["companies"] for x in c["people"]))
q = owner.get(f"/api/recruiters?profile_id={pid}&industry=quant").json()
check("industry filter", [c["company"] for g in q["groups"] for c in g["companies"]] == ["Jane Street"]
      and any(i["industry"] == "other" for i in q["industries"]))
check("search by name", [x["name"] for g in owner.get(f"/api/recruiters?profile_id={pid}&q=priya").json()["groups"]
                         for c in g["companies"] for x in c["people"]] == ["Priya Shah"])

# --- hand edits ---
pid_c = p2["id"]
r = owner.put(f"/api/recruiters/{pid_c}", json={"profile_id": pid, "title": "Senior Campus Recruiter",
                                                 "notes": "Met at the career fair", "starred": True})
check("edit, note and star", r.status_code == 200 and contact(pid, "priya@northwindrobotics.com")["notes"] ==
      "Met at the career fair" and contact(pid, "priya@northwindrobotics.com")["starred"] == 1)
with db.tx() as c:
    c.execute("UPDATE contacts SET phone=NULL WHERE id=?", (pid_c,))
mail.process_messages(pid, None, [email("Next steps", PRIYA.replace("University Recruiter", "Recruiter"), "Priya Shah",
                                        "priya@northwindrobotics.com", "h", T0 + 200)])
p3 = contact(pid, "priya@northwindrobotics.com")
check("a later email never undoes a hand edit, but fills what's missing",
      p3["title"] == "Senior Campus Recruiter" and p3["notes"] == "Met at the career fair" and p3["phone"])
check("starred filter", [x["name"] for g in owner.get(f"/api/recruiters?profile_id={pid}&starred=true").json()["groups"]
                         for c in g["companies"] for x in c["people"]] == ["Priya Shah"])
check("bad email or LinkedIn is refused", owner.put(f"/api/recruiters/{pid_c}", json={
    "profile_id": pid, "email": "not-an-email"}).status_code == 400 and owner.put(f"/api/recruiters/{pid_c}", json={
        "profile_id": pid, "linkedin": "https://evil.example/x"}).status_code == 400)
owner.put(f"/api/recruiters/{sam['id']}", json={"profile_id": pid, "hidden": True})
h = owner.get(f"/api/recruiters?profile_id={pid}").json()
check("hide: gone from the list, still counted, back with hidden=true", h["hidden"] == 1
      and "Sam Lee" not in [x["name"] for g in h["groups"] for c in g["companies"] for x in c["people"]]
      and [x["name"] for g in owner.get(f"/api/recruiters?profile_id={pid}&hidden=true").json()["groups"]
           for c in g["companies"] for x in c["people"]] == ["Sam Lee"])

# --- adding someone yourself, export, delete ---
r = owner.post("/api/recruiters", json={"profile_id": pid, "name": "=HYPERLINK(\"http://x\")", "company": "Citadel",
                                        "email": "Dana@Citadel.com", "linkedin": "linkedin.com/in/dana"})
check("add someone you met", r.status_code == 200 and contact(pid, "dana@citadel.com")["linkedin"] ==
      "https://linkedin.com/in/dana" and contact(pid, "dana@citadel.com")["source"] == "you")
check("…once", owner.post("/api/recruiters", json={"profile_id": pid, "email": "dana@citadel.com"}).status_code == 400)
check("…with at least a name or address", owner.post("/api/recruiters", json={"profile_id": pid,
                                                                               "company": "X"}).status_code == 400)
mail.process_messages(pid, None, [email("Interview with Citadel", "Hi Alex, we'd like to invite you to interview at "
                                        "Citadel.\n\nBest,\nDana Park\nRecruiter", "Dana Park", "dana@citadel.com",
                                        "i")])
check("an email from someone you added keeps what you typed",
      contact(pid, "dana@citadel.com")["name"] == "=HYPERLINK(\"http://x\")" and contact(pid, "dana@citadel.com")["emails"] == 1)
x = owner.get(f"/api/recruiters/export?profile_id={pid}")
rows = list(csv.reader(io.StringIO(x.text)))
check("spreadsheet export", x.status_code == 200 and "attachment" in x.headers["content-disposition"]
      and rows[0][:3] == ["Name", "Email", "Title"] and any(r[0] == "Priya Shah" for r in rows))
check("…where a cell can't run as a formula", any(r[0] == "'=HYPERLINK(\"http://x\")" for r in rows)
      and not any(r[0].startswith("=") for r in rows))
dana = contact(pid, "dana@citadel.com")["id"]
check("delete", owner.delete(f"/api/recruiters/{dana}?profile_id={pid}").status_code == 200
      and not contact(pid, "dana@citadel.com"))

# --- the chat ---
out = agent.call(pid, "recruiters", {"company": "Northwind"}, source="chat")
check("chat: 'who was the recruiter from Northwind?'", out.get("count") == 1 and out["people"][0]["email"] ==
      "priya@northwindrobotics.com" and out["people"][0]["last_contact"])
check("chat tool is offered to every model", "recruiters" in agent.CHAT_TOOLS and "recruiters" in agent.GROQ_TOOLS)

# --- pasting an email ---
OA = """From: Jordan Lee <jordan@ramp.com>
Subject: Ramp - Software Engineer Intern - Online Assessment

Hi Alex! Please complete the CodeSignal assessment within 7 days: https://app.codesignal.com/test/abc123
Thanks, Jordan | Recruiting, Ramp"""
out = agent.call(pid, "log_email", {"text": OA}, source="chat")
j = contact(pid, "jordan@ramp.com")
check("a pasted email saves its sender too", out.get("ok") is not False and j and j["source"] == "pasted"
      and j["name"] == "Jordan Lee" and j["last_status"] == "oa")
agent.call(pid, "log_email", {"text": OA})
check("…once", contact(pid, "jordan@ramp.com")["emails"] == 1)

# --- earlier emails (from before this tab existed) fill it in once ---
old = owner.post("/api/profiles", json={"name": "Old"}).json()["id"]
with db.tx() as c:
    for i, (frm, addr) in enumerate([("Kim Park", "kim@shopify.com"), ("Kim Park", "kim@shopify.com")]):
        c.execute("INSERT INTO email_events (id, profile_id, received_at, from_name, from_addr, subject, snippet, status,"
                  " company, classified_by, created_at, ignored) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                  (f"old{i}", old, T0 + i, frm, addr, f"Interview {i}", "Hi, we'd like to interview you.", "interviewing",
                   "Shopify", "rules", T0))
mail.process_messages(old, None, [email("Offer", "Congratulations! We're pleased to offer you the role.\n\nBest,\nKim",
                                        "Kim Park", "kim@shopify.com", "new1", T0 + 50)])
k = contact(old, "kim@shopify.com")
check("earlier emails are added before new ones, each counted once", k and k["emails"] == 3 and k["first_seen"] == T0)
owner.get(f"/api/recruiters?profile_id={old}")
check("…and only once", contact(old, "kim@shopify.com")["emails"] == 3)

# --- a locked profile's contacts stay locked ---
L = owner.post("/api/profiles", json={"name": "Lee", "passcode": "lee-pass-77"}).json()["id"]
mail.process_messages(L, None, [email("Interview invitation", PRIYA, "Priya Shah", "priya@northwindrobotics.com", "L1")])
lc = contact(L, "priya@northwindrobotics.com")["id"]
before = db.rows("SELECT * FROM contacts WHERE profile_id=?", (L,))
refused = (400, 403, 404, 423)
check("locked: can't list them", stranger.get(f"/api/recruiters?profile_id={L}").status_code in refused)
check("locked: can't export them", stranger.get(f"/api/recruiters/export?profile_id={L}").status_code in refused)
check("locked: can't change one through another profile's id",
      stranger.put(f"/api/recruiters/{lc}", json={"profile_id": pid, "notes": "pwned"}).status_code in refused)
check("locked: can't delete one through another profile's id",
      stranger.delete(f"/api/recruiters/{lc}?profile_id={pid}").status_code in refused)
check("locked: ?profile_id=2.0 is refused", stranger.get(f"/api/recruiters?profile_id={L}.0").status_code == 400)
check("nothing changed", db.rows("SELECT * FROM contacts WHERE profile_id=?", (L,)) == before)
check("another site can't add to your list", owner.post("/api/recruiters", json={"profile_id": pid, "name": "Evil"},
                                                         headers={"Origin": "https://evil.example",
                                                                  "Sec-Fetch-Site": "cross-site"}).status_code == 403)
check("one profile can't touch another's contact", owner.put(f"/api/recruiters/{lc}", json={
    "profile_id": pid, "notes": "x"}).status_code in refused and contact(L, "priya@northwindrobotics.com")["notes"] is None)

# --- deleting a profile deletes their contacts ---
check("deleting a profile deletes their recruiters", owner.delete(f"/api/profiles/{old}").status_code == 200
      and not db.rows("SELECT 1 FROM contacts WHERE profile_id=?", (old,)))

end = time.time() + 20  # pasted emails start prep sheets in the background
while agent._prepping and time.time() < end:
    time.sleep(0.2)
failed = [k for k, v in results.items() if not v]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
