"""Visa / sponsorship signals in postings. Run: .venv/bin/python -m tests.test_visa_flags"""

from coopcoach.textutil import detect_flags

CASES = [
    ("We sponsor J-1 visas for international interns, including Canadian co-op students.", {"sponsor_yes"}),
    ("Visa sponsorship is available for this role.", {"sponsor_yes"}),
    ("• We do sponsor and take over sponsorship of employment visas for this role", {"sponsor_yes"}),
    ("Open to students located in Canada; remote work from Canada is possible.", {"sponsor_yes"}),
    ("We are unable to sponsor visas for this position.", {"no_sponsor"}),
    ("Candidates must be authorized to work in the US without current or future sponsorship.", {"no_sponsor"}),
    ("This company does not sponsor.", {"no_sponsor"}),
    ("We do not sponsor work visas.", {"no_sponsor"}),
    ("We can't offer H-1B sponsorship, but Canadian co-op students can join on a J-1.", {"sponsor_yes", "no_sponsor"}),
    ("International students on F-1 visas are eligible through CPT or OPT.", {"sponsor_yes"}),
    ("We do not sponsor H-1B visas; students on CPT/OPT are welcome to apply.", {"sponsor_yes", "no_sponsor"}),
    ("Users can opt in to notifications.", set()),
    ("Must be a U.S. citizen due to ITAR requirements.", {"us_citizen"}),
    ("Any offer may be conditioned on your authorization to receive technology controlled under these U.S. export "
     "laws without sponsorship for an export license.", set()),
    ("Company-sponsored sports teams and fitness events", set()),
    ("Build delightful products with React and Python.", set()),
]
KEYS = {"sponsor_yes", "no_sponsor", "us_citizen"}


def main() -> None:
    fails = 0
    for text, want in CASES:
        got = set(detect_flags("Software Intern", text)) & KEYS
        fails += got != want
        print(("PASS " if got == want else "FAIL ") + f"{sorted(got) or '-'} | {text[:80]}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
