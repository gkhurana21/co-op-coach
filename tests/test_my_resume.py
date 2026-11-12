"""The repo owner's own uploaded resume, parsed end to end. Opt-in (it checks facts about one specific resume) and
local only: it never prints resume contents.

Run: MY_RESUME=1 .venv/bin/python -m tests.test_my_resume
"""

import logging
import os

from coopcoach import resume
from coopcoach import resume_studio as rs
from coopcoach.config import UPLOAD_DIR


def main():
    if not os.environ.get("MY_RESUME"):
        print("SKIP set MY_RESUME=1 to check the owner's own resume")
        return
    files = sorted(UPLOAD_DIR.glob("1_*.pdf")) if UPLOAD_DIR.exists() else []
    if not files:
        print("SKIP no stored resume for profile 1")
        return
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    data = files[0].read_bytes()
    text = resume.extract_text(files[0].name, data)
    links = resume.extract_links(files[0].name, data)
    p = resume.merge_file_links(resume.heuristic_parse(text), links)
    r = rs.normalize(rs.attach_file_links(rs.build_from_text(text, p), p))
    checks = {
        "year from the start date (Sept 2024 -> year 3 in fall 2026)": p["year_of_study"] == 3,
        "co-op program detected": p["coop_program"] is True,
        "graduation month read": p["grad_date"].startswith("Aug"),
        "targets include Quant and Software": {"Quant", "Software"} <= set(p["target_categories"]),
        "4 jobs, each with a real company (no city as company)": len(p["experiences"]) == 4 and
            all(e["company"] and "," not in e["company"] for e in p["experiences"]),
        "projects are QuantCore and tradebot only (no contact line or 'Live Demo')": [x["name"] for x in p["projects"]]
            == ["QuantCore", "tradebot"],
        "company websites and repo/demo links attached": all(e["link"] for e in r["experience"]) and
            all(x["link"] for x in r["projects"]) and r["projects"][0]["demo"],
        "no wrapped-line fragments as bullets": not any(b[:1].islower() for e in r["experience"] + r["projects"]
                                                       for b in e["bullets"]),
    }
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
