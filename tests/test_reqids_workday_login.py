"""Requisition IDs tie emails and Workday rows to the exact posting, and one shared Workday login covers every
company's site. Uses a throwaway database and an in-memory keychain (your real Keychain is never touched).

Run: .venv/bin/python -m tests.test_reqids_workday_login
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-reqtest-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="")  # never call a real AI provider from tests

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.errors import PasswordDeleteError  # noqa: E402


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.d = {}

    def get_password(self, service, user):
        return self.d.get((service, user))

    def set_password(self, service, user, pw):
        self.d[(service, user)] = pw

    def delete_password(self, service, user):
        if (service, user) not in self.d:
            raise PasswordDeleteError()
        del self.d[(service, user)]


KR = MemoryKeyring()
keyring.set_keyring(KR)

from coopcoach import db, mail, server, store, vault, workday  # noqa: E402
from coopcoach.textutil import same_req  # noqa: E402

RBC = "https://rbc.wd3.myworkdayjobs.com/RBCGLOBAL1"


def job(title, path, req_hint="", company="Royal Bank of Canada", desc=""):
    return store.normalize({"source": "workday", "company": company, "title": title, "url": f"{RBC}/job/{path}",
                            "locations": ["Toronto, ON"], "description": desc, "req_id": req_hint})


def email(subject, body, frm="Royal Bank of Canada", addr="noreply@rbc.wd3.myworkday.com", mid="m"):
    return {"message_id": mid, "subject": subject, "body": body, "from_name": frm, "from_addr": addr,
            "received_at": db.now(), "web_link": ""}


def app_row(pid, jid):
    return db.row("SELECT * FROM applications WHERE profile_id=? AND job_id=?", (pid, jid))


def main():
    db.init()
    checks: dict[str, bool] = {}
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json) VALUES"
                        " (?,?,?,?,?)", ("Test", 1, 1, "{}", "{}")).lastrowid

    # two near-identical RBC internships: only the requisition number tells them apart
    a = job("Software Developer Intern", "Toronto/Software-Developer-Intern_R-0000181145", "R-0000181145")
    b = job("Software Developer Intern", "Toronto/Software-Developer-Intern_R-0000181999-1")
    other = job("Data Analyst Co-op", "x/Data-Analyst-Co-op_JR0098765", company="Scotiabank")
    store.upsert_jobs([a, b, other], db.now())
    checks["Workday req ID stored from the board's field and from the job link"] = (
        db.row("SELECT req_id FROM jobs WHERE id=?", (a["id"],))["req_id"] == "R-0000181145"
        and db.row("SELECT req_id FROM jobs WHERE id=?", (b["id"],))["req_id"] == "R-0000181999")

    for j in (a, b):
        server.upsert_application({"profile_id": pid, "job_id": j["id"], "status": "applied"})
    checks["applying copies the req ID onto the application"] = app_row(pid, b["id"])["req_id"] == "R-0000181999"

    mail.process_messages(pid, None, [email("Interview invitation: Software Developer Intern (R-0000181999)",
                                            "Hi, we'd like to invite you to interview for Software Developer Intern.",
                                            mid="i1")])
    ev = db.row("SELECT * FROM email_events WHERE message_id='i1'")
    checks["interview email links to the exact posting by its req ID (not its twin)"] = (
        ev["job_id"] == b["id"] and app_row(pid, b["id"])["status"] == "interviewing"
        and app_row(pid, a["id"])["status"] == "applied")
    checks["the email's req IDs are saved with it"] = ev["req_ids"] == "R-0000181999"

    mail.process_messages(pid, None, [email("Update on your application",
                                            "Thank you for your interest in RBC. Job ID: 181145. Unfortunately, we "
                                            "have decided to move forward with other candidates.", mid="r1")])
    checks["'Job ID: 181145' (no prefix, no zeros) still finds R-0000181145"] = (
        db.row("SELECT job_id FROM email_events WHERE message_id='r1'")["job_id"] == a["id"]
        and app_row(pid, a["id"])["status"] == "rejected")

    mail.process_messages(pid, None, [email("Thank you for applying (Req ID: JR0098765)",
                                            "We received your application for Data Analyst Co-op.",
                                            frm="Scotiabank", addr="careers@scotiabank.com", mid="s1")])
    checks["an email for an untracked posting finds it by req ID and starts tracking it"] = (
        db.row("SELECT job_id FROM email_events WHERE message_id='s1'")["job_id"] == other["id"]
        and app_row(pid, other["id"]) is not None)

    r = server.add_manual_application({"profile_id": pid, "company": "TD", "title": "Analyst Intern",
                                       "url": "https://td.wd3.myworkdayjobs.com/TD_Bank_Careers/job/Toronto/"
                                              "Analyst-Intern_R_1234567"})
    checks["manual application picks the req ID out of a pasted Workday link"] = \
        same_req(app_row(pid, r["job_id"])["req_id"] or "", "R_1234567")
    server.set_application_req({"profile_id": pid, "job_id": r["job_id"], "req_id": " R-77777 "})
    checks["you can type or fix a req ID by hand"] = app_row(pid, r["job_id"])["req_id"] == "R-77777"

    rows = [["Job Title", "Job Req ID", "Status", "Date Submitted"],
            ["Software Developer Intern", "R-0000181145", "Offer", "10/01/2026"]]
    parsed = workday.parse_applications(rows, "")
    checks["Workday's My Applications table: req ID column is captured"] = parsed[0]["req_id"] == "R-0000181145"

    # --- one shared Workday login ---
    checks["no shared login: nothing is added automatically"] = workday.ensure_default_accounts(pid) == 0
    added = server.routes_docs.set_default_login({"profile_id": pid, "username": "me@example.com",
                                                  "password": "Shared-Pass-123!"})["added"]
    accts = db.rows("SELECT * FROM portal_accounts WHERE profile_id=?", (pid,))
    sites = {x["site_url"] for x in accts}
    checks["saving the shared login adds every Workday site you've applied through"] = (
        added == 2 and sites == {RBC, "https://td.wd3.myworkdayjobs.com/TD_Bank_Careers"}
        and all(x["uses_default"] for x in accts))
    checks["each site signs in with the shared password from the Keychain"] = \
        all(workday.password_for(x) == "Shared-Pass-123!" for x in accts)
    checks["the database never holds the password"] = "Shared-Pass-123!" not in (open(db.DB_PATH, "rb").read() if db.DB_PATH.exists() else str([db.rows(f"SELECT * FROM {t}") for t in ("portal_accounts", "profiles", "meta")]).encode()).decode(
        "latin-1") and not any("Shared-Pass" in str(v) for v in db.rows("SELECT * FROM meta"))

    newco = job("ML Intern", "Waterloo/ML-Intern_R-0000200001", company="Royal Bank of Canada")
    newco["url"] = "https://shopify.wd5.myworkdayjobs.com/Careers/job/Waterloo/ML-Intern_R-0000200001"
    newco["id"] = store.job_id(newco["url"])
    store.upsert_jobs([newco], db.now())
    server.upsert_application({"profile_id": pid, "job_id": newco["id"], "status": "applied"})
    checks["marking a new Workday job as applied adds that company automatically"] = bool(db.row(
        "SELECT 1 FROM portal_accounts WHERE profile_id=? AND site_url LIKE 'https://shopify.%' AND uses_default=1",
        (pid,)))

    td = db.row("SELECT id FROM portal_accounts WHERE site_url LIKE 'https://td.%'")["id"]
    server.routes_docs.delete_portal(td)
    workday.ensure_default_accounts(pid)
    checks["a site you remove isn't re-added from your applications"] = not db.row(
        "SELECT 1 FROM portal_accounts WHERE site_url LIKE 'https://td.%'")
    checks["...and removing it keeps the shared password"] = \
        KR.get_password(vault.SERVICE, vault.workday_default_key(pid)) == "Shared-Pass-123!"

    server.routes_docs.add_portal({"profile_id": pid, "url": "https://td.wd3.myworkdayjobs.com/TD_Bank_Careers",
                                   "company": "TD", "username": "other@example.com", "password": "TD-only-pass-9!"})
    tdacct = db.row("SELECT * FROM portal_accounts WHERE site_url LIKE 'https://td.%'")
    checks["a site with its own password can still be added by hand"] = (
        tdacct["uses_default"] == 0 and workday.password_for(tdacct) == "TD-only-pass-9!")

    workday.set_default_login(pid, "new@example.com", None)
    checks["changing the shared email updates every shared site; password kept"] = all(
        x["username"] == "new@example.com" and workday.password_for(x) == "Shared-Pass-123!"
        for x in db.rows("SELECT * FROM portal_accounts WHERE uses_default=1"))

    workday.clear_default_login(pid)
    checks["turning it off removes shared sites and the Keychain entry, keeps the TD login"] = (
        not db.row("SELECT 1 FROM portal_accounts WHERE uses_default=1")
        and KR.get_password(vault.SERVICE, vault.workday_default_key(pid)) is None
        and db.row("SELECT 1 FROM portal_accounts WHERE site_url LIKE 'https://td.%'") is not None)

    # older postings get their req IDs filled in once
    with db.tx() as c:
        c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, first_seen, last_seen) VALUES"
                  " (?,?,?,?,?,?,?,?)", ("old1", "simplify", "BMO", "bmo", "Co-op",
                                         "https://bmo.wd3.myworkdayjobs.com/External/job/X/Co-op_R250012345", 1, 1))
    store.backfill_req_ids()
    checks["postings saved before this update get their req IDs backfilled"] = \
        db.row("SELECT req_id FROM jobs WHERE id='old1'")["req_id"] == "R250012345"

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
