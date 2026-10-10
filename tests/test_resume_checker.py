"""Resume Studio's checker: real mistakes caught with exact fixes (typos, capitals, unfinished sentences, lines split
when the file was read, stray spaces/commas, mixed dashes, a project name glued to its tech), no false nags (real
verbs, modern words, compounds), "Fix all small things" is safe, and "Check against my file" keeps your edits.

Run: .venv/bin/python -m tests.test_resume_checker
"""

import copy
import os
import tempfile

os.environ.setdefault("COOPCOACH_DATA", tempfile.mkdtemp(prefix="coopcoach-checker-"))

from coopcoach import resume_studio as rs  # noqa: E402

MINE = {
    "header": {"name": "Alex Chen", "email": "alex@example.com", "phone": "4165550199", "location": "Toronto , ON",
               "links": ["github.com/alexchen"]},
    "education": [{"school": "University of Waterloo", "degree": "Bachelor of Computer Science", "location": "Waterloo, ON",
                   "dates": "Sept 2024 – Apr 2029", "details": ["Honors & Awards:", "Dean's List;", "Euclid Contest Distinction"]}],
    "experience": [
        {"title": "Software Engineer Inter", "company": "Acme Analytics", "location": "Seattle, WA", "dates": "Sept 2025 – Dec 2025",
         "bullets": ["Provided hands-on technical support across Microsoft 365 and ServiceNow, documenting escalation patterns.",
                     "Built backend orchestration (batch runs, caching, retries) controlling multi-strategy evaluation jobs.",
                     "Analyzed ticket routing data to find bottlenecks."]},
        {"title": "Quantative Trading Intern", "company": "Northwind Capital", "location": "Remote", "dates": "MAy 2025 - Aug 2025",
         "bullets": ["Built a portfolio analytics platform (FastAPI, Next.js) consolidating multi-strategy portfolios.",
                     "Designed risk dashboards for the trading desk."]},
    ],
    "projects": [
        {"name": "PriceCore: Derivatives Pricing EngineC++, Python, FastAPI", "tech": "", "bullets": [
            "Engineered a C++ options pricing engine with pybind11 bindings, computing Black-Scholes valuations",
            "with validated Greeks at 4x throughput over vectorized NumPy.",
            "Implemented VaR modules validated against 851 days of market data with a 4.5% breach rate versus the"]},
    ],
    "skills": [{"label": "Languages", "items": "Python, C++, Java, SQL,"}, {"label": "Tools", "items": "Docker, Git"}],
}


def main():
    checks = {}
    L = rs.lint(MINE)
    msgs = [i["message"] for i in L["issues"]]
    has = lambda txt: any(txt in m for m in msgs)  # noqa: E731
    checks["catches a typo in a job title (Inter → Intern)"] = has("“Inter” → “Intern”")
    checks["catches 'Quantative' → 'Quantitative'"] = has("“Quantative” → “Quantitative”")
    checks["catches wrong capitals (MAy → May)"] = has("“MAy” → “May”")
    checks["catches a sentence that stops mid-way"] = has("Stops mid-sentence")
    checks["catches a line split from the bullet above"] = has("end of the line above")
    checks["catches a heading split from its list"] = has("heading split from its line")
    checks["catches stray spaces and a trailing comma"] = has("Stray spaces in “Toronto , ON”") and has("stray comma")
    checks["catches mixed date dashes"] = has("Dash:")
    checks["catches a project name glued to its tech"] = has("got stuck together")
    checks["no 'start with an action verb' for real verbs like Provided"] = not has("Start with an action verb")
    checks["no false spelling flags (escalation, controlling, multi-)"] = not any(
        w in m for m in msgs for w in ("escalation", "controlling", "“multi”"))
    checks["no number nags"] = not any("number" in m.lower() and "optional" not in m.lower() for m in msgs)
    checks["2-line bullets aren't called too long"] = not has("lines long")
    checks["every mistake comes first"] = [i["kind"] for i in L["issues"]][:len([i for i in L["issues"] if i["kind"] == "mistake"])] == \
        ["mistake"] * len([i for i in L["issues"] if i["kind"] == "mistake"])

    fixed, n = rs.apply_fixes(copy.deepcopy(MINE), "auto")
    e0, e1, p0 = fixed["experience"][0], fixed["experience"][1], fixed["projects"][0]
    checks["fix-all corrects titles, capitals, dashes and spaces together"] = e0["title"] == "Software Engineer Intern" \
        and e1["title"] == "Quantitative Trading Intern" and e1["dates"] == "May 2025 – Aug 2025" \
        and fixed["header"]["location"] == "Toronto, ON" and fixed["skills"][0]["items"] == "Python, C++, Java, SQL"
    checks["...joins split lines without losing words"] = len(p0["bullets"]) == 2 and \
        p0["bullets"][0].endswith("Black-Scholes valuations with validated Greeks at 4x throughput over vectorized NumPy.")
    checks["...joins the awards list into one line"] = fixed["education"][0]["details"] == [
        "Honors & Awards: Dean's List; Euclid Contest Distinction"]
    checks["...splits the glued project name"] = p0["name"] == "PriceCore: Derivatives Pricing Engine" and \
        p0["tech"] == "C++, Python, FastAPI"
    checks["...leaves things only you can decide (the unfinished sentence)"] = p0["bullets"][1].endswith("versus the")
    after = rs.lint(fixed)
    checks["...and the score goes up"] = after["score"] > L["score"]

    review = rs.rule_review(MINE)
    checks["the coach's changes are exact before/after fixes"] = review["changes"] and all(
        c["after"] and c["fix_id"] for c in review["changes"] if c["title"] != "Needs you")
    checks["...and it says what needs you"] = any(c["title"] == "Needs you" for c in review["changes"])

    from_file = copy.deepcopy(MINE)
    from_file["experience"][0]["title"] = "Software Engineer Intern"
    from_file["experience"][0]["link"] = "acme.com"
    from_file["header"]["links"] = ["github.com/alexchen", "linkedin.com/in/alexchen"]
    from_file["projects"].append({"name": "tradebot: Trading System", "tech": "Go, Python", "bullets": ["Built a bot."]})
    mine = copy.deepcopy(MINE)
    mine["experience"][1]["bullets"].append("Presented weekly risk reviews to the desk.")  # their own addition
    items = rs.compare(mine, from_file)
    kinds = {(i["kind"], i["label"].split(" · ")[0]) for i in items}
    checks["compare finds a project that's only in the file"] = ("missing", "Projects") in kinds
    checks["...a missing link and a fixed title"] = any(i["file"] == "linkedin.com/in/alexchen" for i in items) and \
        any(i["file"] == "Software Engineer Intern" and i["suggested"] for i in items)
    checks["...and marks your own additions as yours (kept)"] = any(i["kind"] == "only_mine" and "Presented" in i["mine"]
                                                                    for i in items)
    picked = [i["id"] for i in items if i["suggested"]]
    merged, k = rs.apply_compare(mine, from_file, picked)
    checks["taking the file's version keeps your additions"] = "Presented weekly risk reviews to the desk." in \
        merged["experience"][1]["bullets"] and merged["experience"][0]["title"] == "Software Engineer Intern"
    checks["...and adds what was missing"] = len(merged["projects"]) == 2 and "linkedin.com/in/alexchen" in merged["header"]["links"]

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
