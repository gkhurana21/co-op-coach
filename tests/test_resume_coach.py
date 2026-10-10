"""🧠 Resume Coach (against a local mock of Groq) and the calmer checker.

Run: .venv/bin/python -m tests.test_resume_coach
"""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-coachtest-"), GROQ_API_KEY="test-key",
                  GROQ_BASE_URL="http://127.0.0.1:8797/openai/v1", ANTHROPIC_API_KEY="")

from coopcoach import ai, db  # noqa: E402
from coopcoach import resume_studio as rs  # noqa: E402

RESUME = rs.normalize({
    "header": {"name": "Alex Chen", "email": "alex@example.com"},
    "experience": [{"title": "Software Engineer Intern", "company": "Shopify", "dates": "May 2025 – Aug 2025",
                    "bullets": ["Built a GraphQL API in Go for merchant payouts used by the checkout team",
                                "Worked on dashboards for the support team",
                                "Designed a caching layer with Redis that cut API latency by 40%"]}],
    "projects": [{"name": "Tradebot", "tech": "Python, Go",
                  "bullets": ["Built a trading system with a Go risk engine and a Python strategy layer",
                              "Wrote backtests for momentum strategies"]}],
    "section_order": ["experience", "projects", "education", "skills", "extras"],
})
REVIEW = {
    "verdict": "Almost there", "first_impression": "Solid backend intern with real systems work.",
    "strengths": ["The Redis caching bullet shows clear impact"],
    "changes": [
        {"title": "Sharpen the dashboards bullet", "why": "Vague opener", "path": "experience.0.bullets.1",
         "before": "Worked on dashboards", "after": "Built support dashboards in React that show queue health per team"},
        {"title": "Add scale", "why": "Numbers", "path": "projects.0.bullets.1", "before": "Wrote backtests",
         "after": "Wrote backtests across 500 tickers improving returns by 12%"},  # invented numbers: must be dropped
        {"title": "Reorder", "why": "Best work first", "path": "[experience.7.bullets.0]", "before": "", "after": ""},
    ],
    "questions": ["Roughly how many merchants used the payouts API?"],
}
SEEN: list[dict] = []


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        data = json.dumps({"choices": [{"message": {"role": "assistant", "content": json.dumps(REVIEW)},
                                        "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    srv = HTTPServer(("127.0.0.1", 8797), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    db.init()
    checks = {}
    out = ai.review_resume(RESUME, {"program": "Computer Science", "year_of_study": 3}, {"categories": ["Software"]})
    titles = [c["title"] for c in out["changes"]]
    checks["a suggestion that invents numbers is dropped"] = "Add scale" not in titles
    keep = next((c for c in out["changes"] if c["title"].startswith("Sharpen")), None)
    checks["a real edit keeps its path and gets the line's exact current text"] = bool(keep) and \
        keep["path"] == "experience.0.bullets.1" and keep["before"] == "Worked on dashboards for the support team"
    adv = next((c for c in out["changes"] if c["title"] == "Reorder"), None)
    checks["a path that doesn't exist becomes advice, not a one-click edit"] = bool(adv) and adv["path"] == ""
    sent = json.dumps(SEEN[-1])
    checks["the AI sees every line with its [path]"] = "[experience.0.bullets.2]" in sent and "[projects.0.bullets.1]" in sent
    checks["contact details never leave the machine"] = "alex@example.com" not in sent
    checks["the prompt forbids adding numbers"] = "never add a number" in sent

    srv.shutdown()

    L = rs.lint(RESUME)
    per_bullet_numbers = [i for i in L["issues"] if "number" in i["message"].lower() and i.get("path")]
    checks["no 'add a number' nag on individual bullets"] = not per_bullet_numbers
    crisp = rs.normalize({"experience": [{"title": "SWE Intern", "company": "Shopify", "bullets": [
        "Built a GraphQL API in Go for merchant payouts used by the checkout team",
        "Designed a Redis caching layer for the storefront API and rolled it out behind feature flags",
        "Automated release checks with GitHub Actions so every service ships with the same test gates"]}]})
    checks["strong, specific bullets with no numbers still score high on impact (was 16/40)"] = \
        rs.lint(crisp)["scores"]["impact"] >= 33
    checks["rule rewrites never insert [X%] placeholders"] = not any(
        "%" in s or "N users" in s for s in rs.suggest_rewrites("Worked on the backend for the trading app"))
    checks["rule-based review has the same shape as the AI one"] = set(rs.rule_review(RESUME)) >= {
        "verdict", "first_impression", "strengths", "changes", "questions"}

    from coopcoach.privacy import Redactor
    red = Redactor(["Demo", "Alex Chen"])
    checks["hiding a name never eats other words ('Demo' vs 'demographics')"] = red.redact(
        "Demo surveyed demographics with AlexNet") == "[[P0]] surveyed demographics with AlexNet"
    typo = rs.normalize({"header": {"name": "A", "email": "me@uwaterlo..ca", "location": "Toronto , ON"}})
    msgs = [i["message"] for i in rs.lint(typo)["issues"]]
    checks["checker catches a mistyped email and 'Toronto , ON'"] = any("mistyped" in m for m in msgs) and \
        any("Stray space" in m for m in msgs)

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
