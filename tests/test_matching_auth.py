"""US roles scored for different work-authorization statuses. Run: .venv/bin/python -m tests.test_matching_auth"""

import time

from coopcoach import matching

PROFILE = {"skills": ["Python", "React"], "target_categories": ["Software"], "degree_level": "Bachelor's",
           "year_of_study": 3}


def job(**kw):
    base = {"title": "Software Engineer Intern", "company": "Acme", "category": "Software", "regions": ["US"],
            "locations": ["San Francisco, CA"], "terms": [], "skills": ["Python"], "flags": [], "tier": "yc",
            "posted_at": time.time() - 3 * 86400}
    return {**base, **kw}


def run(auth, j, locations=("Canada", "Remote")):
    prefs = matching.merged_prefs({"citizenship": auth, "locations": list(locations)})
    s, reasons = matching.score(j, PROFILE, prefs)
    return s, [r["t"] for r in reasons]


checks = {}
plain = job()
s_us, _ = run("US", plain, ("US",))
s_ca, r_ca = run("Canadian", plain)
s_f1, r_f1 = run("F1", plain)
s_other, _ = run("Other", plain)
checks["US startup role isn't 'outside your locations' for a Canadian"] = "Outside your locations" not in r_ca and s_ca >= s_us - 6
checks["F-1 student in the US gets full location credit"] = any("CPT/OPT" in r for r in r_f1) and s_f1 >= s_ca
checks["international (other) students also see US roles as reachable"] = s_other >= s_ca - 2

no = job(flags=["no_sponsor"])
s_ca_no, r_ca_no = run("Canadian", no)
s_f1_no, r_f1_no = run("F1", no)
checks["'no sponsorship' hurts a J-1 student more than an F-1 (CPT/OPT) student"] = (s_ca - s_ca_no) > (s_f1 - s_f1_no) > 0

yes = job(flags=["sponsor_yes"])
checks["'sponsors visas' boosts international students"] = run("Canadian", yes)[0] > s_ca and run("F1", yes)[0] > s_f1

citizen = job(flags=["us_citizen"])
checks["US-citizens-only is a hard blocker for every international status"] = all(run(a, citizen)[0] < 40 for a in ("Canadian", "F1", "Intl-CA", "Other"))
checks["...but not for US citizens"] = run("US", citizen, ("US",))[0] >= 70

opt_out = matching.merged_prefs({"citizenship": "Canadian", "locations": ["Canada"], "us_open": False})
checks["turning 'open to US roles' off restores the old behaviour"] = any(
    r["t"] == "Outside your locations" for r in matching.score(plain, PROFILE, opt_out)[1])

for name, ok in checks.items():
    print(("PASS " if ok else "FAIL ") + name)
print(f"\nscores for the same US startup role: US citizen {s_us}, Canadian {s_ca}, F-1 {s_f1}, other intl {s_other}")
print(f"{sum(map(bool, checks.values()))}/{len(checks)} passed")
raise SystemExit(0 if all(checks.values()) else 1)
