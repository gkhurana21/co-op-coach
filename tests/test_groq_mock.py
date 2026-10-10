"""Groq provider against a local mock of Groq's OpenAI-compatible API (no real key or network needed).

Run: .venv/bin/python -m tests.test_groq_mock
"""

import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

TMP = tempfile.mkdtemp(prefix="coopcoach-groqtest-")
os.environ.update(COOPCOACH_DATA=TMP, GROQ_API_KEY="test-key", GROQ_BASE_URL="http://127.0.0.1:8796/openai/v1")
for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(k, None)

from coopcoach import ai, db, groq_client, routes_docs, store, sync  # noqa: E402

REQUESTS: list[dict] = []
MODE = {"rate_limit_once": False, "daily_limit": False}
PII = ["Alex Chen", "alex.chen@example.com", "416-555-0199", "github.com/alexchen-dev", "linkedin.com/in/alexchen-dev"]


def instance(schema, token):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "object":
        return {k: instance(v, token) for k, v in schema["properties"].items()}
    if t == "array":
        return [instance(schema["items"], token)]
    if t == "integer":
        return 70
    if t == "number":
        return 3.8
    if t == "boolean":
        return True
    return f"mock text signed {token}"


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append({"body": body, "auth": self.headers.get("Authorization")})
        if MODE["daily_limit"]:
            return self._send(429, {"error": {"message": "Rate limit reached: tokens per day (TPD)"}}, {"retry-after": "3600"})
        if MODE["rate_limit_once"]:
            MODE["rate_limit_once"] = False
            return self._send(429, {"error": {"message": "tokens per minute"}}, {"retry-after": "1"})
        prompt = " ".join(m["content"] for m in body["messages"])
        token = (re.findall(r"\[\[P\d+\]\]", prompt) or ["(no token)"])[0]
        rf = body.get("response_format")
        content = json.dumps(instance(rf["json_schema"]["schema"], token)) if rf else f"Hi! (chat reply) {token}"
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})

    def _send(self, code, obj, headers=None):
        data = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    srv = HTTPServer(("127.0.0.1", 8796), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    db.init()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at, prefs_json, profile_json, resume_text) VALUES"
                        " (?,?,?,?,?,?)", ("Alex Chen", 1, 1, "{}", json.dumps({"name": "Alex Chen", "email": PII[1],
                        "links": PII[3:], "skills": ["Python"], "summary": "Year 3 CS"}), "resume")).lastrowid
        resume = {"header": {"name": "Alex Chen", "email": PII[1], "phone": PII[2], "location": "Waterloo, ON",
                             "links": PII[3:]}, "experience": [{"title": "SWE Intern", "company": "Wealthsimple",
                             "dates": "May 2025 – Aug 2025", "bullets": ["Built a React dashboard for 40k users"]}]}
        c.execute("INSERT INTO documents (profile_id, kind, title, content, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                  (pid, "resume_master", "Master resume", json.dumps(resume), 1, 1))
        jid = "job1"
        c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, first_seen, last_seen, description)"
                  " VALUES (?,?,?,?,?,?,?,?,?)", (jid, "simplify", "Acme", "acme", "Software Engineer Intern",
                                                  "https://example.com/j", 1, 1, "Python, React, AWS. " * 400))
    checks = {}
    checks["Groq is picked automatically when only GROQ_API_KEY is set"] = ai.provider() == "groq" and ai.available()

    letter = routes_docs.generate_letter(pid, jid, "classic")
    req = REQUESTS[-1]
    sent = json.dumps(req["body"])
    checks["letter written by Groq"] = letter["generated_by"] == "groq"
    checks["request: gpt-oss-120b, strict JSON schema, reasoning effort, bearer key"] = (
        req["body"]["model"] == "openai/gpt-oss-120b" and req["body"]["response_format"]["json_schema"]["strict"] is True
        and req["body"]["reasoning_effort"] in ("low", "medium", "high") and req["auth"] == "Bearer test-key")
    checks["no name, email, phone or links leave the machine"] = not any(p.lower() in sent.lower() for p in PII)
    checks["name is restored in the reply"] = "Alex Chen" in letter["content"] and "[[P" not in letter["content"]
    checks["fits the free tier: request under ~8k tokens incl. output budget"] = (
        len(sent) / 4 + req["body"]["max_completion_tokens"] < 8000)

    n_before = len(REQUESTS)
    for i in range(30):
        with db.tx() as c:
            c.execute("INSERT INTO jobs (id, source, company, company_key, title, url, first_seen, last_seen, regions,"
                      " category) VALUES (?,?,?,?,?,?,?,?,?,?)", (f"s{i}", "simplify", f"Co{i}", f"co{i}",
                      "Software Engineer Intern", f"https://example.com/{i}", 1, 1, '["Canada"]', "Software"))
    store.invalidate_cache()
    sync.score_with_ai(store.get_profile(pid))
    score_reqs = REQUESTS[n_before:]
    sizes = [len(json.loads(r["body"]["messages"][1]["content"].split("<postings>\n")[1].split("\n</postings>")[0]))
             for r in score_reqs]
    checks["fit scoring uses small batches (<=6) and caps at 18 postings per sync"] = max(sizes) <= 6 and sum(sizes) <= 18

    MODE["rate_limit_once"] = True
    reply = routes_docs.chat({"profile_id": pid, "messages": [{"role": "user", "content": "what's new?"}]})
    checks["a per-minute rate limit is waited out and retried"] = reply["by"] == "groq"

    MODE["daily_limit"] = True
    letter2 = routes_docs.generate_letter(pid, jid, "startup")
    checks["daily limit: pauses Groq and falls back to the built-in templates"] = (
        groq_client.paused() > 0 and not ai.available() and letter2["generated_by"] == "rules")
    srv.shutdown()

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
