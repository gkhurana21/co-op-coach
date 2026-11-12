"""Re-sort: today's rules over emails already in the tracker. The three cases the old Outlook reader got wrong on real
data (a LinkedIn News article taken for an interview, five Mercor roles merged into one "Submitted" row, Shopify filed
under its program's name) are rebuilt here on scratch data; the preview lists them, nothing changes until Apply, and
an email the student fixed by hand is never touched.

Run: .venv/bin/python -m tests.test_email_resort
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-resort-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import db, mail  # noqa: E402


def main():
    db.init()
    checks: dict[str, bool] = {}
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Robin', ?, ?)", (now, now)).lastrowid
        apps = [("ext:news", "LinkedIn News", "Role from email", "interviewing"),
                ("ext:submitted", "Submitted", "Role from email", "applied"),
                ("ext:shopprog", "Shopify's Internships program", "Role from email", "applied")]
        for job_id, co, title, st in apps:
            c.execute("INSERT INTO applications (profile_id, job_id, status, company, title, source, updated_at)"
                      " VALUES (?,?,?,?,?,'email',?)", (pid, job_id, st, co, title, now))
        rows = [("e-news", "LinkedIn News", "editors-noreply@linkedin.com", "5 interview questions to prepare for",
                 "LinkedIn News", "", "interviewing", "ext:news", "rules")]
        for n, role in enumerate(["Software Developers (Must own MacBook)", "Python Experts", "Data Annotators",
                                  "Math Tutors", "Technical Writers"]):
            rows.append((f"e-mercor{n}", "Mercor", "noreply@mercor.com", f"Application Submitted - {role} on Mercor",
                         "Submitted", "", "applied", "ext:submitted", "rules"))
        rows.append(("e-shop", "Shopify Early Talent", "earlytalent@shopify.com",
                     "Thank you for applying to Shopify's Internships program", "Shopify's Internships program", "",
                     "applied", "ext:shopprog", "rules"))
        rows.append(("e-mine", "LinkedIn", "editors-noreply@linkedin.com", "Your interview with Acme",
                     "Acme", "", "interviewing", "ext:news2", "you"))
        for i, (eid, fname, faddr, subj, co, role, st, job, by) in enumerate(rows):
            c.execute("INSERT INTO email_events (id, profile_id, message_id, received_at, from_name, from_addr, subject,"
                      " snippet, status, company, role, job_id, classified_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, pid, f"<{eid}@x>", now - 86400 * (30 - i), fname, faddr, subj, "We received your application.",
                       st, co, role, job, by, now))

    before = db.rows("SELECT * FROM applications ORDER BY job_id")
    p = mail.resort(pid)
    kinds = {ch["id"]: ch["kind"] for ch in p["changes"]}
    checks["the LinkedIn News article is flagged as not a job email"] = kinds.get("e-news") == "not_job"
    checks["each Mercor email is filed again under Mercor with its own role"] = \
        all(kinds.get(f"e-mercor{n}") == "relink" for n in range(5)) and \
        all(ch["now"].startswith("Mercor · ") for ch in p["changes"] if ch["id"].startswith("e-mercor"))
    checks["Shopify's email is filed under Shopify"] = kinds.get("e-shop") == "relink" and \
        next(ch["now"] for ch in p["changes"] if ch["id"] == "e-shop").startswith("Shopify")
    checks["an email fixed by hand is left alone"] = "e-mine" not in kinds
    checks["the rows made only from those emails are listed for removal"] = \
        {r["job_id"] for r in p["removals"]} == {"ext:news", "ext:submitted", "ext:shopprog"}
    checks["a preview changes nothing"] = db.rows("SELECT * FROM applications ORDER BY job_id") == before and \
        not db.row("SELECT 1 FROM email_events WHERE ignored=1")

    mail.resort(pid, apply=True)
    apps_now = db.rows("SELECT company, title, status FROM applications WHERE profile_id=?", (pid,))
    mercor = [a for a in apps_now if a["company"] == "Mercor"]
    checks["after Apply: five Mercor applications, one per role"] = len(mercor) == 5 and \
        len({a["title"] for a in mercor}) == 5
    checks["after Apply: Shopify is its own application, the bad rows are gone"] = \
        any(a["company"] == "Shopify" for a in apps_now) and \
        not any(a["company"] in ("LinkedIn News", "Submitted", "Shopify's Internships program") for a in apps_now)
    checks["after Apply: the news email is kept but marked not a job email"] = \
        db.row("SELECT ignored FROM email_events WHERE id='e-news'")["ignored"] == 1
    checks["after Apply: the email fixed by hand is unchanged"] = \
        db.row("SELECT company, ignored FROM email_events WHERE id='e-mine'") == {"company": "Acme", "ignored": 0}
    checks["a second re-sort finds nothing more to change"] = not mail.resort(pid)["changes"]

    # the old Outlook-window reader: emails dated by the scan are dropped, with cards made only from them, and
    # forgotten as read so the next check opens them again; a hand-fixed one and a properly dated one stay
    with db.tx() as c:
        c.execute("INSERT INTO email_accounts (profile_id, kind, address, created_at) VALUES (?,?,?,?)",
                  (pid, "outlook_web", "robin@uni.ca", now))
        acct = c.execute("SELECT id FROM email_accounts WHERE profile_id=?", (pid,)).fetchone()["id"]
        c.execute("INSERT INTO applications (profile_id, job_id, status, company, title, source, updated_at)"
                  " VALUES (?,?,?,?,?,'email',?)", (pid, "ext:scan", "applied", "Scanco", "Intern", now))
        for eid, conv, rec, made, by in (("s1", "owa:A", now - 3600, now - 3590, "rules"),
                                          ("s2", "owa:B", now - 40 * 86400, now - 3590, "rules"),
                                          ("s3", "owa:C", now - 3600, now - 3590, "you")):
            c.execute("INSERT INTO email_events (id, profile_id, account_id, message_id, received_at, subject, status,"
                      " company, job_id, classified_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, pid, acct, conv, rec, "x", "applied", "Scanco", "ext:scan" if eid == "s1" else "ext:other", by, made))
    db.meta_set(f"owa_seen:{acct}", "A B C Z")
    dry = mail.repair_scan_dates(pid, dry_run=True)
    checks["the repair finds only the email dated by the scan"] = dry["emails"] == 1 and dry["applications"] == 1 and \
        db.row("SELECT 1 AS k FROM email_events WHERE id='s1'")
    mail.repair_scan_dates(pid)
    checks["the repair drops it and the card made only from it, keeps the rest"] = \
        not db.row("SELECT 1 AS k FROM email_events WHERE id='s1'") and \
        not db.row("SELECT 1 AS k FROM applications WHERE job_id='ext:scan'") and \
        db.row("SELECT 1 AS k FROM email_events WHERE id='s2'") and db.row("SELECT 1 AS k FROM email_events WHERE id='s3'")
    checks["...and the next check opens it again"] = sorted(db.meta_get(f"owa_seen:{acct}").split()) == ["B", "C", "Z"]
    checks["...and a backup was written first"] = any((mail.config.DATA_DIR / "backups").glob("outlook-repair-*.json"))

    from coopcoach.mail_rules import apply_status
    with db.tx() as c:
        c.execute("INSERT INTO applications (profile_id, job_id, status, company, title, source, applied_at, updated_at)"
                  " VALUES (?,?,?,?,?,'email',?,?)", (pid, "ext:late", "applied", "Late", "Intern", now, now))
    apply_status(pid, "ext:late", "applied", now - 90 * 86400, "Thanks for applying")
    checks["an application's date is its earliest email, even one read late"] = \
        db.row("SELECT applied_at FROM applications WHERE job_id='ext:late'")["applied_at"] == now - 90 * 86400

    from coopcoach.mail_rules import _domain_company
    checks["a sender's domain names the company, but booking tools and reserved test domains don't"] = \
        [_domain_company(a) for a in ("recruiting@google.com", "priya@eulerity.example", "noreply@calendly.com",
                                      "earlytalent@shopify.com")] == ["google", "eulerity", "", "shopify"]

    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print(f"\n{sum(bool(v) for v in checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
