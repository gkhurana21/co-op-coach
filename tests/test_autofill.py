"""The form filler on local practice forms (never a real company's site), one built like Greenhouse, Lever and Ashby's real
pages: fills what it knows, really chooses in dropdowns, leaves demographic questions alone, and never presses a button.

Run: .venv/bin/python -m tests.test_autofill
"""

from coopcoach.apply import _EEO, _FILL_JS, _basic_answers, _fill_choices, _fill_page, _pick_option, asker

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


# Built like the real thing: Greenhouse's searchable dropdowns (an answer only counts once an option is chosen), Lever's labels
# with a mark after them and its radio cards, Ashby's labels, plus the traps.
LIKE_REAL = """<!doctype html><html><body>
<script>
function dropdown(id, label, options, search) {
  document.write(`<div class="f"><label for="${id}">${label}<span>*</span></label><div class="select__control" id="box-${id}">
    <input id="${id}" role="combobox" aria-autocomplete="list" class="select__input" autocomplete="off"><div class="menu" id="menu-${id}"></div></div></div>`);
  const input = document.getElementById(id), menu = document.getElementById('menu-' + id);
  const show = () => { const q = input.value.toLowerCase();
    const list = search && !q ? [] : options.filter((o) => !q || o.toLowerCase().includes(q));
    menu.innerHTML = list.map((o) => `<div role="option">${o}</div>`).join('');
    menu.querySelectorAll('[role=option]').forEach((d) => d.addEventListener('mousedown', (e) => { e.preventDefault(); window.PICKED[id] = d.innerText;
      input.value = ''; menu.innerHTML = ''; const v = document.createElement('div'); v.className = 'select__single-value'; v.innerText = d.innerText; input.parentElement.prepend(v); })); };
  input.addEventListener('focus', show); input.addEventListener('input', show);
  input.addEventListener('blur', () => { input.value = ''; menu.innerHTML = ''; });
}
window.PICKED = {};
</script>
<form onsubmit="window.SUBMITTED = true; return false;">
<script>
dropdown('country', 'Country', ['Afghanistan', 'Canada', 'United States']);
dropdown('school', 'School', ['University of Toronto', 'University of Waterloo'], true);
dropdown('degree', 'Degree', ["High School", "Bachelor's Degree", "Master's Degree"]);
dropdown('auth', 'Are you legally authorized to work in Canada?', ['Yes', 'No']);
dropdown('sponsor', 'Will you now or in the future require visa sponsorship?', ['Yes', 'No']);
dropdown('gender', 'Gender', ['Man', 'Woman', 'Decline to self-identify']);
dropdown('race', 'Race / Ethnicity', ['A', 'B']);
dropdown('veteran', 'Veteran status', ['Yes', 'No']);
dropdown('odd', 'Have you applied to this role before?', ['Yes', 'No']);
</script>
<ul>
 <li class="application-question"><label><div class="application-label">Email<span class="required">✱</span></div><div class="application-field"><input id="l-email" type="email" name="email"></div></label></li>
 <li class="application-question"><label><div class="application-label">Phone<span class="required">✱</span></div><div class="application-field"><input id="l-phone" name="phone"></div></label></li>
 <li class="application-question"><label><div class="application-label">Github Profile (required)</div><div class="application-field"><input id="l-gh" name="urls[GitHub]"></div></label></li>
 <li class="application-question"><div class="application-label">Are you authorized to work in Canada?<span class="required">✱</span></div>
   <label><input type="radio" name="card1" value="y" id="r-yes"> Yes</label><label><input type="radio" name="card1" value="n" id="r-no"> No</label></li>
 <li class="application-question"><div class="application-label">Pronouns</div>
   <label><input type="radio" name="card2" value="a" id="r-he"> He/Him</label><label><input type="radio" name="card2" value="b"> She/Her</label></li>
 <li class="application-question"><label><div class="application-label">Will you require sponsorship?</div><div class="application-field"><select id="l-sp"><option value="">Select...</option><option>Yes</option><option>No</option></select></div></label></li>
 <li class="application-question"><label><div class="application-label">What is your favourite colour?</div><div class="application-field"><select id="l-odd"><option value="">Select...</option><option>Red</option></select></div></label></li>
</ul>
<input id="trap" name="beecatcher" style="position:absolute;left:-9999px" aria-label="Email">
<label for="why2">Why did you pick your college major?</label><textarea id="why2"></textarea>
<button type="submit" id="send" onclick="window.PRESSED = true">Submit application</button>
</form></body></html>"""

BASICS = {"first_name": "Alex", "last_name": "Chen", "full_name": "Alex Chen", "email": "alex@example.com", "phone": "416-555-0199", "location": "Toronto, ON", "country": "Canada",
          "linkedin": "https://linkedin.com/in/alex", "github": "https://github.com/alex", "website": "", "school": "University of Waterloo",
          "degree": "Bachelor's in Computer Science", "discipline": "Computer Science", "grad_date": "Apr 2028", "gpa": "", "current_company": "", "current_title": ""}


def like_real(page, checks):
    page.set_content(LIKE_REAL)
    kit = {"job": {"regions": ["Canada"], "terms": []}, "basics": BASICS}
    ask = asker(kit, {"citizenship": "Canadian"})
    typed = _fill_page(page, _basic_answers(BASICS), {})
    picked = _fill_choices(page, ask)
    val = lambda sel: page.eval_on_selector(sel, "e => e.value")  # noqa: E731
    choice = page.evaluate("window.PICKED")
    checks["a searchable dropdown gets an option really chosen, not text typed in it"] = choice.get("country") == "Canada" and choice.get("auth") == "Yes" and choice.get("sponsor") == "No"
    checks["...one that lists only what's searched for is searched"] = choice.get("school") == "University of Waterloo"
    checks["...and a degree finds its kind in the list"] = choice.get("degree") == "Bachelor's Degree"
    checks["...nothing is left typed in the box"] = val("#school") == "" and val("#country") == ""
    checks["demographic dropdowns are never opened or answered"] = not ({"gender", "race", "veteran"} & set(choice))
    checks["a question it has no rule for is left for the student"] = "odd" not in choice and val("#l-odd") == ""
    checks["a mark after the label doesn't hide it: Email ✱, Phone ✱, (required)"] = val("#l-email") == "alex@example.com" and val("#l-phone") == "416-555-0199" and val("#l-gh") == "https://github.com/alex"
    checks["a radio question gets the right button"] = page.is_checked("#r-yes") and not page.is_checked("#r-no")
    checks["pronouns stay blank"] = not page.is_checked("#r-he")
    checks["a plain dropdown asked on the page is answered"] = page.eval_on_selector("#l-sp", "e => e.options[e.selectedIndex].text") == "No"
    checks["the robot trap stays empty"] = val("#trap") == ""
    checks["a long question isn't answered by a stray word in it"] = val("#why2") == ""
    checks["no button is ever pressed, and nothing is submitted"] = not page.evaluate("window.SUBMITTED || window.PRESSED || false")
    checks["it says what it answered"] = "Country" in picked and any("Email" in x or "email" in x for x in typed)
    us = asker({"job": {"regions": ["US"], "terms": []}, "basics": BASICS}, {"citizenship": "Canadian"})
    checks["a US role gets a Canadian's honest answers"] = us("Are you legally authorized to work in the United States?", "select", ["Yes", "No"]) == "No" and us("Will you require sponsorship?", "select", ["Yes", "No"]) == "Yes"
    checks["no way to ask a demographic question"] = all(ask(q, "select", ["Yes", "No"]) == "" for q in ("Gender", "Are you Hispanic or Latino?", "Veteran status", "Pronouns", "Do you have a disability?")) and _EEO.search("Race")
    checks["'No' is the option No, not 'Not sure' or 'None' that happen to contain it"] = asker({"job": {"regions": ["Canada"], "terms": []}, "basics": BASICS}, {"citizenship": "Canadian"})("Will you require sponsorship?", "select", ["Yes", "Not sure", "No"]) == "No"
    checks["a doctoral program isn't the one they're enrolled in"] = ask("Are you currently enrolled in a doctoral (PhD) program?", "select", ["Yes", "No"]) == "" and ask("Are you currently enrolled in a degree?", "select", ["Yes", "No"]) == "Yes"
    checks["high school dates don't get the university's"] = ask("When did you graduate from High School?", "text", []) == ""
    checks["'preferred' isn't 'referred'"] = ask("Your preferred sponsorship type: will you require sponsorship?", "select", ["Yes", "No"]) == "No"
    checks["someone else's details stay empty"] = ask("Referrer's email", "text", []) == "" and ask("Emergency contact phone", "text", []) == ""
    checks["a guess is left for them: relocating, a start date"] = ask("Are you willing to relocate?", "select", ["Yes", "No"]) == "" and ask("Available to start?", "text", []) == ""
    checks["No never matches Not sure when No is missing"] = _pick_option("No", ["Yes", "Not sure"]) is None
    checks["country of birth and citizenship are unknown"] = all(not asker(kit, {"citizenship": "Intl-CA"})(label, "select", ["Canada", "India"]) for label in ["Country of birth", "Country of citizenship"])
    page.set_content('<form><label for="login">Login email</label><input id="login" autocomplete="username"><label for="trap">Email</label><input id="trap" style="opacity:0"><label for="valid">Email</label><input id="valid"></form>')
    _fill_choices(page, asker(kit, {"citizenship": "Canadian"}))
    checks["dynamic pass skips login and invisible fields"] = not page.locator("#login").input_value() and not page.locator("#trap").input_value() and page.locator("#valid").input_value() == BASICS["email"]
    checks["options match by meaning, not luck"] = _pick_option("Canada", ["Canada (+1)", "Cameroon"]) == 0 and _pick_option("No", ["Yes", "No"]) == 1 and _pick_option("Master's in CS", ["Bachelor", "Masters Degree"]) == 1 and _pick_option("", ["Yes"]) is None and _pick_option("Yes", ["Red"]) is None


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
        like_real(page, checks)
        browser.close()
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
