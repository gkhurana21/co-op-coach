"""Rule-based recruiting-email classifier checks. Run: .venv/bin/python -m tests.test_email_rules"""

from coopcoach.mail import classify

CASES = [
    # (subject, from_name, from_addr, body, expected_status, expected_company, role_contains)
    ("Thank you for applying to Stripe!", "Stripe", "no-reply@us.greenhouse-mail.io",
     "Hi Alex, Thanks for applying to the Software Engineering Intern (Summer 2027) role. Our team will review "
     "your application and if your background is a fit we'll reach out about an interview.",
     "applied", "Stripe", "Software Engineering Intern"),
    ("Thank you for your application", "RBC", "rbc@myworkday.com",
     "Thank you for your interest in RBC. We have received your application for Developer Co-op (Winter 2027).",
     "applied", "RBC", "Developer Co-op"),
    ("Your application was sent to Shopify", "LinkedIn", "jobs-noreply@linkedin.com",
     "Your application was sent to Shopify. Dev Degree Intern · Toronto, ON", "applied", "Shopify", ""),
    ("Thank you for applying to the Software Engineering Intern position at Wealthsimple", "Wealthsimple Talent",
     "no-reply@ashbyhq.com", "We received your application.", "applied", "Wealthsimple", "Software Engineering Intern"),
    ("Datadog Online Assessment Invitation", "Datadog Recruiting", "noreply@hackerrank.com",
     "You have been invited to complete the Datadog Software Engineering Intern assessment. Please complete it "
     "within 7 days: https://www.hackerrank.com/test/abc", "oa", "Datadog", ""),
    ("Next steps for your application", "Coinbase Recruiting", "no-reply@greenhouse.io",
     "Thank you for applying to Coinbase! As a next step, please complete the CodeSignal assessment by Friday: "
     "https://app.codesignal.com/x", "oa", "Coinbase", ""),
    ("Interview invitation - Software Developer Intern", "Jordan from Cohere", "jordan@cohere.com",
     "Hi Alex, thanks for your interest in Cohere! We'd like to invite you to an interview with our team. "
     "Please share your availability for next week.", "interviewing", "Cohere", "Software Developer Intern"),
    ("Cohere - next steps", "Jordan", "jordan@cohere.com",
     "Thanks for your interest in Cohere! We'd love to schedule a call to chat more about the role.",
     "interviewing", "Cohere", ""),
    ("Update on your application to Palantir", "Palantir", "no-reply@hire.lever.co",
     "Thank you for your interest in Palantir. Unfortunately, we have decided to move forward with other "
     "candidates for the Software Engineer Intern role.", "rejected", "Palantir", "Software Engineer Intern"),
    ("Your application with TD", "TD Bank Group", "td@myworkday.com",
     "Thank you for applying. After careful consideration, we regret to inform you that you have not been "
     "selected for the Data Analytics Co-op position.", "rejected", "TD", "Data Analytics Co-op"),
    ("Thanks for applying to Ramp", "Ramp", "no-reply@ashbyhq.com",
     "Thank you for applying. Unfortunately we won't be moving forward at this time.", "rejected", "Ramp", ""),
    ("Congratulations! Offer from Databricks", "Databricks Recruiting", "recruiting@databricks.com",
     "We're thrilled to extend you an offer for the Software Engineering Intern position.", "offer",
     "Databricks", "Software Engineering Intern"),
    ("Interview reschedule", "Sam (Notion)", "sam@makenotion.com",
     "Unfortunately I need to move our interview to Thursday. Does 2pm work?", "interviewing", "Notion", ""),
    ("Update on your application", "Bombardier", "bombardier@myworkday.com",
     "Thank you for your interest in Bombardier. Unfortunately, we have decided to move forward with other "
     "candidates for the Intern, Developer (Winter 2027) position.", "rejected", "Bombardier", "Intern, Developer"),
    # things that must be ignored
    ("Software Engineer Intern at Google and 12 more jobs for you", "LinkedIn Job Alerts",
     "jobalerts-noreply@linkedin.com", "New jobs match your preferences", None, "", ""),
    ("Your credit card application", "RBC Royal Bank", "noreply@rbc.com",
     "Thank you for your application. We have received your application for the Avion card.", None, "", ""),
    ("Weekly digest: your study group", "StudySync", "digest@studysync.app", "3 new posts", None, "", ""),
    ("Lunch tomorrow?", "Mom", "mom@gmail.com", "Want to grab lunch?", None, "", ""),
]


def main() -> None:
    failures = 0
    for subj, name, addr, body, want_status, want_company, role_has in CASES:
        got = classify(subj, name, addr, body)
        status = got["status"] if got else None
        company = got["company"] if got else ""
        role = got["role"] if got else ""
        ok = status == want_status and (not want_company or company.lower() == want_company.lower()) and \
            (not role_has or role_has.lower() in role.lower())
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {subj[:55]:<55} -> {status!s:<12} {company!r:<16} {role[:40]!r}"
              + ("" if ok else f"   (wanted {want_status}, {want_company!r}, role~{role_has!r})"))
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
