"""Who a posting is open to, and how that changes ranking.

Run: .venv/bin/python -m tests.test_eligibility
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-eligtest-")

from coopcoach import eligibility as el  # noqa: E402
from coopcoach import matching  # noqa: E402

US_POSTING = """Software Engineer Intern 2027. • Currently enrolled in a Bachelor's or Master's in Computer Science
• On track to graduate by June 2029. Some interns will work fully remote within approved locations.
US based applicants only. All qualified applicants will receive consideration..."""
CA_POSTING = """Software Engineering Intern 2027 (Toronto). • Obtain Bachelor's in Computer Science by June 2029.
Relocation Statement: This position may be eligible for relocation assistance. Canada based applicants only."""
FORM = ["First Name", "What U.S State do you currently reside in?",
        "Will you now or in the future require employer sponsorship or other employer assistance to obtain, extend, "
        "or maintain authorization to work in the country?"]
STUDENT = {"degree_level": "Bachelor's", "grad_date": "Aug 2029", "grad_year": 2029, "skills": ["Python", "Java"],
           "target_categories": ["Software"], "year_of_study": 3}
CANADIAN = {"citizenship": "Canadian", "locations": ["Canada", "Remote"], "us_open": True}


def job(title, desc, regions, questions=None, flags=None):
    return {"title": title, "company": "Pinterest", "category": "Software", "skills": ["Python", "Java"],
            "regions": regions, "locations": [], "terms": [], "flags": flags or [], "tier": "bigtech",
            "intl_friendly": "US" in regions, "requirements": el.extract(title, desc, questions)}


def verdict(reasons):
    return next(r["v"] for r in reasons if r["k"] == "elig")


def main():
    checks = {}
    us = el.extract("SWE Intern (USA)", US_POSTING, FORM)
    checks["reads 'US based applicants only' from the end of the posting"] = any(
        r["kind"] == "residency" and r["value"] == "US" for r in us)
    checks["reads the sponsorship question from the application form"] = any(r["kind"] == "sponsorship_question" for r in us)
    checks["reads the graduation window"] = any(r["kind"] == "grad_window" and "June 2029" in r["value"] for r in us)
    form_only = el.extract("SWE Intern", "Build things.", ["What U.S. State do you currently reside in?"])
    checks["a form asking which US state you live in counts as US-residents only"] = any(
        r["kind"] == "residency" and r["source"] == "form" for r in form_only)

    e_us = el.evaluate(us, set(), STUDENT, CANADIAN)
    checks["Canada-based student: US-residents-only posting is 'no'"] = e_us["verdict"] == "no"
    e_ca = el.evaluate(el.extract("SWE Intern (Toronto)", CA_POSTING), set(), STUDENT, CANADIAN)
    checks["Canada-based student: 'Canada based applicants only' is a 'fit'"] = e_ca["verdict"] == "fit"
    e_f1 = el.evaluate(us, set(), STUDENT, {"citizenship": "F1"})
    checks["an F-1 student already in the US can apply to the US posting"] = e_f1["verdict"] in ("fit", "ok")
    checks["US citizens / clearance only: 'no' for a Canadian, fine for a US citizen"] = (
        el.evaluate([], {"us_citizen"}, STUDENT, CANADIAN)["verdict"] == "no"
        and el.evaluate([], {"us_citizen"}, STUDENT, {"citizenship": "US"})["verdict"] != "no")
    checks["PhD-only roles are 'no' for a Bachelor's student"] = el.evaluate([], {"phd"}, STUDENT, CANADIAN)["verdict"] == "no"
    checks["graduation after the window is only a note"] = (
        any("June 2029" in n and "Aug 2029" in n for n in e_ca["notes"]) and e_ca["verdict"] == "fit")

    # ranking: grad window never moves the score
    a = job("SWE Intern (Toronto)", CA_POSTING, ["Canada"])
    b = job("SWE Intern (Toronto)", CA_POSTING.replace("by June 2029", "in 2030"), ["Canada"])
    sa, ra = matching.score(a, STUDENT, CANADIAN)
    sb, _ = matching.score(b, STUDENT, CANADIAN)
    checks["a stated graduation window doesn't change the score"] = sa == sb
    su, ru = matching.score(job("SWE Intern (USA)", US_POSTING, ["US", "Remote"], FORM), STUDENT, CANADIAN)
    checks["US-residents-only role is capped low with a clear reason"] = (
        su <= 20 and verdict(ru) == "no" and any("based in the US" in r["t"] for r in ru))
    checks["...and no 'Often hires international interns' on it"] = not any("international interns" in r["t"] for r in ru)
    checks["the Toronto version outranks it"] = sa > su + 40 and verdict(ra) == "fit"

    checks["Toronto/USA/Dublin copies of one internship share a core title"] = len({
        el.core_title(t) for t in ("Software Engineer Intern 2027 (USA)", "Software Engineering Intern 2027 (Toronto)",
                                   "Software Engineering Intern 2027 (Dublin)")}) == 1
    checks["different roles at the same company don't"] = (
        el.core_title("Machine Learning Intern 2027 (Toronto)") != el.core_title("Software Engineering Intern 2027"))

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
