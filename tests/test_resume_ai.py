"""Design → "Describe your look": what the AI sends back is made safe, and only the request and the look go out.

Run: .venv/bin/python -m tests.test_resume_ai
"""

import json
import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-resumeai-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import ai, db, server  # noqa: E402
from coopcoach import resume_theme as t


def main():
    db.init()
    server._sync_in_background = lambda: None
    checks: dict[str, bool] = {}
    cur = {**t.THEME_DEFAULTS, "accent": "#1f3a68", "link_color": "#000000", "contact": "icons", "link_icons": True, "secret": "x"}
    got = {"accent": "#ffee00", "link_color": "red", "contact": "carrier pigeon", "heading_font": "comic-sans", "body_font": "inter",
           "body_size": 40, "name_size": 2, "line_height": 1.3, "margin": 5, "section_space": 1.0, "rule_tint": 0.5,
           "heading_style": "caps", "bullet": "dash", "date_align": "inline", "density": "airy", "header_align": "left",
           "columns": 7, "link_icons": False, "company_first": True, "one_page": False}
    look = t.from_ai(got, cur)
    checks["a colour too pale to read is darkened, keeping its hue"] = t.contrast(look["accent"]) >= 4.5 and look["accent"] != "#ffee00"
    checks["a colour that isn't one keeps the current one"] = look["link_color"] == "#000000"
    checks["choices that don't exist fall back"] = look["contact"] == "icons" and look["heading_font"] == t.THEME_DEFAULTS["heading_font"] and look["columns"] == 1
    checks["numbers stay in range"] = look["body_size"] == 12.5 and look["name_size"] == 14.0 and look["margin"] == 1.0
    checks["valid choices are kept"] = look["body_font"] == "inter" and look["heading_style"] == "caps" and look["link_icons"] is False
    checks["it can't touch settings that aren't the look"] = "company_first" not in look and "one_page" not in look

    sent = {}
    def fake(system, content, schema, effort, max_tokens=16000, fast=False, job=""):
        sent.update(text=content[0]["text"], job=job, schema=schema)
        return {**got, "accent": "#123456", "note": "Went bold."}
    ai._call_json = fake
    out = ai.design_resume("bold for startups", cur)
    payload = json.loads(sent["text"])
    checks["only the request and the look are sent"] = set(payload) == {"request", "current_look", "number_ranges", "rule_tint_choices", "fonts"} and "secret" not in payload["current_look"]
    checks["it answers with the look and a sentence"] = out["look"]["accent"] == "#123456" and out["note"] == "Went bold."
    with TestClient(server.app, base_url="http://localhost", headers={"Origin": "http://localhost"}) as web:
        ai.available = lambda: False
        r = web.post("/api/resume/theme/ai", json={"ask": "x", "style": cur})
        checks["no AI connected: says so"] = r.status_code == 400 and "Settings" in r.json()["detail"]
        ai.available = lambda: True
        checks["an empty request is refused"] = web.post("/api/resume/theme/ai", json={"ask": " "}).status_code == 400
        r = web.post("/api/resume/theme/ai", json={"ask": "navy", "style": cur})
        checks["the route returns the safe look"] = r.status_code == 200 and r.json()["look"]["accent"] == "#123456"
        def boom(*a, **k):
            raise RuntimeError("rate limited: secret detail")
        ai._call_json = boom
        r = web.post("/api/resume/theme/ai", json={"ask": "navy", "style": cur})
        checks["an AI failure is a plain message"] = r.status_code == 502 and "secret" not in r.text
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print(f"\n{sum(bool(v) for v in checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
