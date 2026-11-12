"""C in MATCH (who works where): importing your LinkedIn connections (the real export's format, notes preamble and
all), job changes on a re-import without undoing your edits, no duplicate of a recruiter who's also a connection,
unsafe links refused, the People tab's source filter, "people you know" on niche firms, the alumni search link, and
the API, chat tool and passcode lock around them.

Run: .venv/bin/python -m tests.test_people
"""

import os
import tempfile

os.environ.update(COOPCOACH_DATA=tempfile.mkdtemp(prefix="coopcoach-people-"), GROQ_API_KEY="", ANTHROPIC_API_KEY="",
                  COOPCOACH_CLAUDE_BIN="/nonexistent", COOPCOACH_CODEX_BIN="/nonexistent")

from fastapi.testclient import TestClient  # noqa: E402

from coopcoach import agent, ai, auth, companies, contacts, db, niches  # noqa: E402
from coopcoach.server import app  # noqa: E402

results: dict[str, bool] = {}

EXPORT = '''Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing. You will only see email addresses for connections who have allowed their connections to see or download their email address using this setting https://www.linkedin.com/psettings/privacy/email. You can learn more here https://www.linkedin.com/help/linkedin/answer/261"

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Jordan,Lee,https://www.linkedin.com/in/jordan-lee-123,,Optiver,Software Engineer,05 Oct 2025
Priya,Shah,https://www.linkedin.com/in/priyashah,priya@example.com,Northwind Robotics,University Recruiter,12 Jan 2026
Sam,Okafor,javascript:alert(1),,Five Rings,Quant Trader,03 Mar 2024
=HYPERLINK(""http://x.example""),Smith,,,"Acme, Inc.",Analyst,11/2/23
,,,,,,
'''


def check(name: str, ok) -> None:
    results[name] = bool(ok)
    print(("PASS " if ok else "FAIL ") + name)


def person(pid, name):
    return db.row("SELECT * FROM contacts WHERE profile_id=? AND name=?", (pid, name))


def main():
    db.init()
    with db.tx() as c:
        pid = c.execute("INSERT INTO profiles (name, profile_json, created_at, updated_at) VALUES (?,?,?,?)",
                        ("Alex", db.dumps({"university": "University of Waterloo"}), db.now(), db.now())).lastrowid
        other = c.execute("INSERT INTO profiles (name, profile_json, created_at, updated_at) VALUES (?,?,?,?)",
                          ("Sam", db.dumps({"university": "McGill University"}), db.now(), db.now())).lastrowid
    # a recruiter who emailed first, and is also a LinkedIn connection
    contacts.record(pid, from_name="Priya Shah", from_addr="priya@example.com", company="Northwind Robotics",
                    body="Hi Alex,\n\nThanks for applying.\n\nBest,\nPriya Shah\nUniversity Recruiter",
                    subject="Your application", status="applied", job_id=None, when=db.now(), source="inbox")

    out = contacts.import_linkedin(pid, EXPORT)
    jordan = person(pid, "Jordan Lee")
    check("LinkedIn's export reads past its notes: name, company, title, profile link, when you connected",
          out["added"] == 3 and jordan and jordan["company"] == "Optiver" and jordan["title"] == "Software Engineer"
          and jordan["linkedin"] == "https://www.linkedin.com/in/jordan-lee-123" and jordan["source"] == "linkedin"
          and jordan["emails"] == 0 and jordan["last_seen"] < db.now() - 300 * 86400 // 2)
    priya = db.rows("SELECT * FROM contacts WHERE profile_id=? AND name='Priya Shah'", (pid,))
    check("a recruiter who's also a connection stays one person and gets their LinkedIn link",
          len(priya) == 1 and priya[0]["linkedin"] == "https://www.linkedin.com/in/priyashah"
          and priya[0]["source"] == "inbox" and out["updated"] == 1)
    sam = person(pid, "Sam Okafor")
    check("a link that isn't LinkedIn's is dropped", sam and sam["linkedin"] is None)
    check("empty rows are skipped, and quoted company names with commas survive",
          out["skipped"] == 1 and db.row("SELECT 1 FROM contacts WHERE company='Acme, Inc.'"))
    csv_out = contacts.to_csv(pid)
    check("the spreadsheet export never lets an imported name run as a formula", "=HYPERLINK" not in
          csv_out.replace("'=HYPERLINK", "") and "'=HYPERLINK" in csv_out)
    check("a file that isn't LinkedIn's connections export is refused",
          "error" in contacts.import_linkedin(pid, "name,email\nA,a@b.c\n"))

    # people change jobs: the new export wins, your edits stay
    contacts.update(pid, sam["id"], {"title": "Trader (my note)"})
    again = EXPORT.replace("Optiver,Software Engineer", "Jane Street,Quant Developer").replace(
        "Five Rings,Quant Trader", "Five Rings,Senior Trader")
    out2 = contacts.import_linkedin(pid, again)
    jordan, sam = person(pid, "Jordan Lee"), person(pid, "Sam Okafor")
    check("importing again updates someone who changed jobs", jordan["company"] == "Jane Street" and
          jordan["title"] == "Quant Developer" and out2["added"] == 0)
    check("...but never undoes your own edit", sam["title"] == "Trader (my note)")

    lst = contacts.listing(pid, source="linkedin")
    names = {p["name"] for g in lst["groups"] for c in g["companies"] for p in c["people"]}
    check("the People tab filters by source and counts each",
          "Jordan Lee" in names and "Priya Shah" not in names and lst["sources"]["linkedin"] == 3
          and lst["sources"]["recruiters"] == 1)
    check("someone else's connections stay theirs", not contacts.listing(other)["people"])

    d = niches.detail(pid, "quant")
    js = next(f for s in d["segments"] for f in s["firms"] if f["name"] == "Jane Street")
    check("a niche shows how many people you know at each firm", js["people"] == 1 and d["stats"]["people"] >= 2)

    url = contacts.alumni_search(pid, "Five Rings")
    check("alumni search: Waterloo's own LinkedIn alumni page, filtered to the company",
          url == "https://www.linkedin.com/school/uwaterloo/people/?keywords=Five%20Rings")
    check("...and another school's alumni through LinkedIn's people search",
          contacts.alumni_search(other, "Optiver").startswith("https://www.linkedin.com/search/results/people/")
          and "McGill" in contacts.alumni_search(other, "Optiver"))
    r = agent.call(pid, "recruiters", {"company": "Jane Street"}, source="chat")
    check("the chat finds who you know at a company, with the alumni link to open",
          r["count"] == 1 and r["people"][0]["name"] == "Jordan Lee" and r["people"][0]["source"] == "linkedin"
          and r["find_alumni"].startswith("https://www.linkedin.com/school/uwaterloo/"))

    with TestClient(app, base_url="http://localhost") as cl:
        res = cl.post("/api/recruiters/import-linkedin", json={"profile_id": other, "csv": EXPORT})
        check("import through the API", res.status_code == 200 and res.json()["added"] == 4)
        bad = cl.post("/api/recruiters/import-linkedin", json={"profile_id": other, "csv": "hello"})
        check("...which explains a wrong file", bad.status_code == 400 and "Connections.csv" in bad.text)
        auth.set_passcode(pid, "maple-1234")
        check("a locked profile can't be imported into", cl.post("/api/recruiters/import-linkedin", json={
            "profile_id": pid, "csv": EXPORT}).status_code == 423)
        auth.clear_passcode(pid)

    founders_and_funding(pid)
    export_in_downloads(other)
    print(f"\n{sum(results.values())}/{len(results)} passed")
    raise SystemExit(0 if all(results.values()) else 1)


FOUNDERS = """First Name,Last Name,URL,Email Address,Company,Position,Connected On
Nia,Patel,https://www.linkedin.com/in/nia-p,,Lumen Robotics,Co-Founder & CEO,01 Feb 2026
Ravi,Kumar,https://www.linkedin.com/in/ravi-k,,Tiny Idea Labs,Founder,01 Feb 2026
Ana,Silva,https://www.linkedin.com/in/ana-s,,Lumen Robotics,Talent Partner,01 Feb 2026
Leo,Martin,https://www.linkedin.com/in/leo-m,,Big Bank,VP Engineering,01 Feb 2026
"""


def founders_and_funding(pid):
    check("roles read from titles: founders and CEOs, recruiters, leaders, engineers",
          [contacts.role_of(t) for t in ("Co-Founder & CEO", "Talent Partner", "VP Engineering", "Quant Researcher",
                                         "Product Designer", "Barista")]
          == ["founder", "recruiter", "leader", "engineer", "product", "other"])
    contacts.import_linkedin(pid, FOUNDERS)
    fake_yc = {"lumen robotics": {"name": "Lumen Robotics", "batch": "W25", "team_size": 14, "status": "Active",
                                  "is_hiring": True, "one_liner": "Robots for warehouses", "website": ""}}
    real_yc, real_ai = companies.yc_lookup, (ai.company_funding, ai.provider)
    companies.yc_lookup = lambda name: fake_yc.get(name.lower())
    asked = []

    def funding(name, website=""):
        asked.append(name)
        return {"found": name == "Lumen Robotics", "total_raised_usd": 3_500_000 if name == "Lumen Robotics" else 0,
                "last_round": "Seed" if name == "Lumen Robotics" else "", "last_round_date": "2025-06",
                "investors": ["Y Combinator"], "sources": ["https://example.com/lumen-seed"], "note": ""}
    ai.company_funding, ai.provider = funding, (lambda: "claude_code")
    try:
        lst = contacts.listing(pid, role="founder")
        names = {p["name"] for g in lst["groups"] for c in g["companies"] for p in c["people"]}
        check("the Founders filter lists founders only", names == {"Nia Patel", "Ravi Kumar"}
              and lst["roles"]["founder"] == 2)
        out = contacts.check_founders_funding(pid, limit=5)
        check("checking founders' funding looks up each founder's company once, and only those",
              sorted(asked) == ["Lumen Robotics", "Tiny Idea Labs"] and out["done"] == 2 and out["found"] == 1)
        rich = contacts.listing(pid, role="founder", min_funding=1_000_000)
        rnames = {p["name"] for g in rich["groups"] for c in g["companies"] for p in c["people"]}
        check("'raised $1M+' keeps the funded startup's founder and drops the one with nothing disclosed",
              rnames == {"Nia Patel"})
        co = next(c for g in rich["groups"] for c in g["companies"] if c["company"] == "Lumen Robotics")
        check("the company shows its YC batch, team size, hiring and what it raised (with sources)",
              co["signals"]["yc"] == "W25" and co["signals"]["team"] == 14 and co["signals"]["yc_hiring"]
              and co["signals"]["funding"]["total_usd"] == 3_500_000
              and co["signals"]["funding"]["sources"] == ["https://example.com/lumen-seed"])
        ycs = {p["name"] for g in contacts.listing(pid, yc=True, min_team=10)["groups"] for c in g["companies"]
               for p in c["people"]}
        check("YC-backed and team-size filters", ycs == {"Nia Patel", "Ana Silva"})
        st = {c["company"] for g in contacts.listing(pid, startups=True)["groups"] for c in g["companies"]}
        check("'Startups' means YC, startups from your postings, or a checked raise (not a big bank)",
              "Lumen Robotics" in st and "Big Bank" not in st)
        r = agent.call(pid, "recruiters", {"role": "founder"}, source="chat")
        lumen = next(p for p in r["people"] if p["company"] == "Lumen Robotics")
        check("the chat lists founders you know with their company's facts",
              {p["name"] for p in r["people"]} == {"Nia Patel", "Ravi Kumar"}
              and lumen["company_facts"]["raised_usd"] == 3_500_000 and lumen["company_facts"]["yc_batch"] == "W25")
        ai.provider = lambda: "groq"
        ai.company_funding = real_ai[0]
        check("without Claude Code or ChatGPT, funding isn't guessed", "needs your" in
              contacts.check_funding("Another Startup").get("error", ""))
    finally:
        companies.yc_lookup = real_yc
        ai.company_funding, ai.provider = real_ai


def export_in_downloads(pid):
    """LinkedIn's export zip in Downloads: offered once, imported in one click, never asked about again."""
    import zipfile
    folder = tempfile.mkdtemp(prefix="downloads-")
    os.environ["COOPCOACH_DOWNLOADS"] = folder
    with zipfile.ZipFile(os.path.join(folder, "holiday-photos.zip"), "w") as z:  # not LinkedIn's
        z.writestr("Connections.csv", EXPORT)
    check("nothing's offered when Downloads has no LinkedIn export", contacts.find_export(pid) is None)
    with zipfile.ZipFile(os.path.join(folder, "Basic_LinkedInDataExport_10-20-2026.zip"), "w") as z:
        z.writestr("Connections.csv", FOUNDERS)
        z.writestr("Messages.csv", "From,To,Content\nprivate,stuff,never read\n")
    found = contacts.find_export(pid)
    check("LinkedIn's export zip in Downloads is found, with how many connections it has",
          found and found["name"] == "Basic_LinkedInDataExport_10-20-2026.zip" and found["rows"] == 4)
    with TestClient(app, base_url="http://localhost") as cl:
        got = cl.get(f"/api/recruiters/linkedin-export?profile_id={pid}").json()["found"]
        res = cl.post("/api/recruiters/linkedin-export", json={"profile_id": pid, "key": got["key"]})
        check("one click imports it", res.status_code == 200 and res.json()["added"] >= 3
              and person(pid, "Nia Patel"))
        check("...and it isn't offered again", cl.get(f"/api/recruiters/linkedin-export?profile_id={pid}").json()["found"]
              is None)
    zpath = os.path.join(folder, "Basic_LinkedInDataExport_10-20-2026.zip")
    os.utime(zpath, (os.path.getmtime(zpath) + 60,) * 2)  # downloaded again later: offered again
    again = contacts.find_export(pid)
    contacts.dismiss_export(pid, again["key"])
    check("'Not now' stops the offer too", contacts.find_export(pid) is None)


if __name__ == "__main__":
    main()
