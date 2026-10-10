"""The form filler on a local practice form (never a real company's site): fills what it knows, leaves demographic
questions alone, and never submits.

Run: .venv/bin/python -m tests.test_autofill
"""

from coopcoach.apply import _FILL_JS

FORM = """<!doctype html><html><body>
<form id="f" onsubmit="window.SUBMITTED = true; return false;">
  <label for="first_name">First Name *</label><input id="first_name">
  <label for="last_name">Last Name *</label><input id="last_name">
  <label for="email">Email *</label><input id="email" type="email">
  <div class="field"><label>LinkedIn Profile</label><input name="urls[LinkedIn]"></div>
  <label for="auth">Are you legally authorized to work in Canada?</label>
  <select id="auth"><option value="">Select…</option><option value="1">Yes</option><option value="0">No</option></select>
  <label for="why">Why do you want to work at Shopify?</label><textarea id="why"></textarea>
  <label for="gender">Gender</label><select id="gender"><option value="">Select…</option><option>Man</option><option>Woman</option></select>
  <label for="prefilled">Phone</label><input id="prefilled" value="555-0000">
  <label for="uni">Name of your university</label><input id="uni">
  <label for="ph">Phone number</label><input id="ph">
  <button type="submit">Submit application</button>
</form></body></html>"""

ANSWERS = [{"label": "First Name", "answer": "Alex"}, {"label": "Last Name", "answer": "Chen"},
           {"label": "Email", "answer": "alex@example.com"}, {"label": "linkedin profile", "answer": "https://linkedin.com/in/alex"},
           {"label": "Are you legally authorized to work in Canada?", "answer": "Yes"},
           {"label": "Why do you want to work at Shopify?", "answer": "I built payment tools in Go."},
           {"label": "Gender", "answer": ""}, {"label": "Phone", "answer": "416-555-0199"},
           {"label": "name", "answer": "Alex Chen"}, {"label": "phone number", "answer": "416-555-0199"}]


def main():
    from playwright.sync_api import sync_playwright
    checks = {}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(FORM)
        filled = page.evaluate(_FILL_JS, ANSWERS)
        val = lambda sel: page.eval_on_selector(sel, "e => e.value")  # noqa: E731
        checks["fills name and email by their labels"] = val("#first_name") == "Alex" and val("#last_name") == "Chen" \
            and val("#email") == "alex@example.com"
        checks["finds a field whose label sits in a wrapper div"] = val("input[name='urls[LinkedIn]']") == "https://linkedin.com/in/alex"
        checks["picks the matching option in a dropdown"] = val("#auth") == "1"
        checks["fills a free-text answer"] = val("#why") == "I built payment tools in Go."
        checks["leaves demographic questions alone"] = val("#gender") == ""
        checks["doesn't overwrite what's already typed"] = val("#prefilled") == "555-0000"
        checks["'Name of your university' never gets your name"] = val("#uni") == ""
        checks["'Phone number' gets your phone"] = val("#ph") == "416-555-0199"
        checks["never submits the form"] = not page.evaluate("window.SUBMITTED || false")
        checks["reports what it filled"] = {"First Name", "Email"} <= set(filled)
        browser.close()
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
