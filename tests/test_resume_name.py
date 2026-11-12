"""An AI reads the resume with the student's name hidden and can hand back only part of it. Scratch data only.

Run: .venv/bin/python -m tests.test_resume_name
"""

import os
import tempfile

os.environ["COOPCOACH_DATA"] = tempfile.mkdtemp(prefix="coopcoach-name-")
os.environ.update(GROQ_API_KEY="", ANTHROPIC_API_KEY="", COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from coopcoach import ai, db, resume, routes_docs, server, store  # noqa: E402

RESUME = "Alex Chen\nalex@example.com | 416-555-0123\n\nEDUCATION\nUniversity of Waterloo\n\nEXPERIENCE\nData Intern | Maple | May 2025 - Aug 2025\n- Built things.\n"
AI_PROFILE = {"name": "Chen", "email": "", "links": [], "university": "University of Waterloo", "program": "CS", "skills": [],
              "target_categories": [], "gpa_normalized": None}


def main():
    db.init()
    now = db.now()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, created_at, updated_at) VALUES ('Alex', ?, ?)", (now, now)).lastrowid
    checks: dict[str, bool] = {}
    w = resume.whole_name
    checks["half a name comes back whole"] = w("Chen", "Alex Chen") == "Alex Chen"
    checks["...so does the first half"] = w("Alex", "Alex Chen") == "Alex Chen"
    checks["a leftover placeholder or nothing at all uses the file"] = w("[[P0]] Chen", "Alex Chen") == "Alex Chen" and w("", "Alex Chen") == "Alex Chen"
    checks["a different full name from the AI stands (the file's top line can be a heading)"] = w("Alex Chen", "Curriculum Vitae") == "Alex Chen"
    checks["no name in the file keeps the AI's"] = w("Alex Chen", "") == "Alex Chen"
    checks["accents and hyphens count as one name"] = w("Núñez-Díaz", "María Núñez-Díaz") == "María Núñez-Díaz"
    checks["the name line skips an email or phone line"] = resume.name_line("a@b.co\n416-555-0123\nAlex Chen\n") == "Alex Chen"

    ai.available = lambda *a, **k: True
    ai.provider = lambda *a, **k: "groq"
    ai.parse_resume = lambda *a, **k: (dict(AI_PROFILE), {"resume_tips": []})
    ai.structure_resume = lambda *a, **k: {"header": {"name": "Chen", "email": "", "phone": "", "location": "", "links": []}, "education": [],
                                           "experience": [], "projects": [], "skills": [], "extras": []}
    server._safe_ai_score = lambda pid: None
    server._ingest_resume(pid, "r.txt", RESUME.encode(), background_ai=False, rebuild_master=False)
    checks["an AI-read resume saves the whole name"] = store.get_profile(pid)["profile"]["name"] == "Alex Chen"
    (routes_docs.UPLOAD_DIR / f"{pid}_r.txt").write_bytes(RESUME.encode())
    r = routes_docs.build_master(pid, use_ai=True)
    checks["Resume Studio's AI copy has the whole name"] = r["header"]["name"] == "Alex Chen"

    # already saved wrong: repaired once, and only that exact damage
    damage = store.get_profile(pid)["profile"]
    damage["name"] = "Chen"
    with db.tx() as c:
        c.execute("UPDATE profiles SET profile_json=? WHERE id=?", (db.dumps(damage), pid))
        c.execute("UPDATE documents SET content=? WHERE profile_id=? AND kind='resume_master'",
                  (db.dumps({"header": {"name": "Chen"}, "experience": []}), pid))
        other = c.execute("INSERT INTO profiles (name, resume_text, profile_json, created_at, updated_at) VALUES ('Sam', ?, ?, ?, ?)",
                          (RESUME, db.dumps({"name": "Chen"}), now, now)).lastrowid
    server.repair_cut_names()
    server.repair_cut_names()
    checks["a name already cut gets fixed on the profile"] = store.get_profile(pid)["profile"]["name"] == "Alex Chen"
    doc = db.loads(db.row("SELECT content FROM documents WHERE profile_id=? AND kind='resume_master'", (pid,))["content"], {})
    checks["...and on the resume"] = doc["header"]["name"] == "Alex Chen"
    checks["a shortened name that isn't this damage is left alone"] = store.get_profile(other)["profile"]["name"] == "Chen"

    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print(f"\n{sum(bool(v) for v in checks.values())}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
