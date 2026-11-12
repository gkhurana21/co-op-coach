"""Fill a Workday application in the person's own Chrome window, step by step, and stop at Review.

Workday applications are a wizard: Apply → (Autofill with Resume | Apply Manually) → sign in → My Information →
My Experience → Application Questions → Voluntary Disclosures → Self Identify → Review. This fills what the kit
knows (name, email, phone, city, the resume upload, the questions it has answers for), moves on with Save and
Continue, and hands over to the person:
- at Review: they check everything and press Submit themselves (this never clicks a button that says Submit);
- at Voluntary Disclosures and Self Identify: those answers are theirs alone;
- at sign-in, a CAPTCHA, a verification code, or a site that needs a new account: the person handles those;
- when Workday flags a field, or a page has questions it has no answer for.
Hidden robot traps ("beecatcher") and anything else not visible are never touched.
"""

import logging
import re

log = logging.getLogger(__name__)

NEXT = '[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"]'
STEPS = ("My Information", "My Experience", "Application Questions", "Voluntary Disclosures", "Self Identify",
         "Review")
YOURS = ("Voluntary Disclosures", "Self Identify")  # demographic questions: never answered for anyone
_SUBMIT = re.compile(r"\bsubmit\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*:?]", " ", (s or "").lower())).strip()


def answer_for(label: str, answers: list[dict]) -> str | None:
    """The kit's answer for a question by its label: short labels ("Email") must match exactly, so "Name of your
    university" never gets your name; longer ones may match the start or be contained."""
    lab = _norm(label)
    if not lab:
        return None
    for a in answers:
        k, v = _norm(a.get("label")), a.get("answer") or ""
        if not v or v.startswith("("):
            continue
        if lab == k or (len(k) >= 8 and (lab.startswith(k) or k.startswith(lab))) or (len(k) > 12 and k in lab):
            return v
    return None


def _visible(loc) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def step_name(page) -> str:
    """Which step of the wizard is showing (by its active progress step, else the page's heading)."""
    for sel in ('[data-automation-id="progressBarActiveStep"]', '[data-automation-id="pageHeader"] h2',
                'h2[data-automation-id]', "h2"):
        loc = page.locator(sel)
        if _visible(loc):
            text = loc.first.inner_text()
            for s in STEPS:
                if s.lower() in text.lower():
                    return s
    return ""


def _pick(page, button, wanted: str) -> bool:
    """A Workday dropdown (a button that opens a listbox): choose the option that reads like the answer."""
    try:
        button.click()
        page.wait_for_timeout(300)
        opts = page.locator('[role="listbox"] [role="option"]')
        w = _norm(wanted)
        for i in range(opts.count()):
            t = _norm(opts.nth(i).inner_text())
            if t == w or (w and (t.startswith(w) or w.startswith(t) and len(t) > 1)):
                opts.nth(i).click()
                return True
        page.keyboard.press("Escape")
    except Exception as e:
        log.info("Workday dropdown: %s", e)
    return False


def fill_page(page, answers: list[dict], files: dict[str, str]) -> tuple[list[str], list[str]]:
    """Fill the visible step: (labels filled, labels left for the person)."""
    filled, left = [], []
    fields = page.locator('[data-automation-id^="formField-"], fieldset, .wd-question')
    for i in range(fields.count()):
        f = fields.nth(i)
        try:
            if not f.is_visible():
                continue
            lab_loc = f.locator("label, legend")
            label = lab_loc.first.inner_text().strip() if lab_loc.count() else ""
            ans = answer_for(label, answers)
            box = f.locator('input[type="text"]:visible, input[type="email"]:visible, input[type="tel"]:visible,'
                            ' input:not([type]):visible, textarea:visible')
            drop = f.locator('button[aria-haspopup="listbox"]:visible')
            radios = f.locator('input[type="radio"]')
            if box.count():
                el = box.first
                if (el.get_attribute("data-automation-id") or "") == "beecatcher":
                    continue  # a robot trap
                if el.input_value():
                    continue  # already there (Workday remembers, or the resume filled it)
                if ans:
                    el.fill(ans)
                    filled.append(label)
                elif label:
                    left.append(label)
            elif drop.count():
                if ans and _pick(page, drop.first, ans):
                    filled.append(label)
                elif label and _norm(drop.first.inner_text()) in ("", "select one"):
                    left.append(label)
            elif radios.count():
                if ans:
                    opt = f.locator("label").filter(has_text=re.compile(rf"^\s*{re.escape(ans)}\s*$", re.I))
                    if opt.count() > 0:
                        opt.first.click()
                        filled.append(label)
                        continue
                if label:
                    left.append(label)
        except Exception as e:
            log.info("Workday field: %s", e)
    up = page.locator('input[type="file"]')
    if up.count() and files.get("resume") and not _visible(page.locator('[data-automation-id="file-upload-successful"]')):
        try:
            up.first.set_input_files(files["resume"])
            filled.append("resume")
        except Exception as e:
            log.info("Workday resume upload: %s", e)
    return filled, left


def run(page, kit: dict, files: dict[str, str], login: tuple[str, str] | None, say=lambda m: None) -> dict:
    """Walk the application as far as it's safe to; returns {"step", "filled", "left", "message"}."""
    from .apply import _basic_answers
    from .workday import _challenge, _on_login
    b = kit.get("basics") or {}
    # Workday's own labels first (its City is the city alone), then the kit's answers, then the usual basics
    answers = [{"label": lab, "answer": v} for lab, v in (
        ("Given Name(s)", b.get("first_name")), ("Family Name", b.get("last_name")), ("Email Address", b.get("email")),
        ("Phone Number", b.get("phone")), ("City", (b.get("location") or "").split(",")[0].strip()),
        ("Phone Device Type", "Mobile")) if v]
    answers += [{"label": q["label"], "answer": q["answer"]} for q in kit.get("questions") or []]
    answers += _basic_answers(b)
    out = {"step": "", "filled": [], "left": [], "message": ""}

    def stop(msg: str) -> dict:
        out["message"] = msg
        return out

    say("Opening the posting…")
    page.goto(kit["job"]["apply_url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    apply_btn = page.locator('[data-automation-id="adventureButton"]')
    if _visible(apply_btn):
        apply_btn.first.click()
        page.wait_for_timeout(1500)
    for how in ('[data-automation-id="autofillWithResume"]', '[data-automation-id="applyManually"]'):
        if _visible(page.locator(how)):
            page.locator(how).first.click()
            page.wait_for_timeout(1500)
            break
    if _on_login(page):
        if _challenge(page):
            return stop("Workday is asking for a CAPTCHA or a code: do that in the window, then continue yourself.")
        return stop("This Workday site wants you signed in. Sign in yourself in the window, then press Fill again.")
    seen = []
    for _ in range(len(STEPS) + 2):
        name = step_name(page)
        out["step"] = name
        if _challenge(page):
            return stop("Workday is asking for a CAPTCHA or a code: finish that in the window yourself.")
        if name == "Review":
            return stop("At Review: check every page, then press Submit yourself.")
        if name in YOURS:
            return stop(f"{name}: those questions are yours to answer. Then continue to Review and press Submit"
                        " yourself.")
        if seen and seen[-1] == name:
            return stop(f"Stopped at {name or 'this page'}: Workday wants something here. Fix what's marked, then carry on.")
        seen.append(name)
        say(f"Filling {name or 'the application'}…")
        filled, left = fill_page(page, answers, files)
        out["filled"] += [x for x in filled if x not in out["filled"]]
        if left:
            out["left"] = left
            return stop(f"{name or 'This page'} has questions only you can answer: " + "; ".join(left[:4])
                        + (" …" if len(left) > 4 else "") + ". Answer them, then continue.")
        nxt = page.locator(NEXT)
        if not _visible(nxt):
            return stop(f"Filled what I could on {name or 'this page'}; take it from here.")
        if _SUBMIT.search(nxt.first.inner_text() or ""):
            return stop("At the last step: check everything, then press Submit yourself.")
        nxt.first.click()
        page.wait_for_timeout(2500)
        if _visible(page.locator('[data-automation-id="errorMessage"], [data-automation-id="errorBanner"]')):
            return stop(f"Workday flagged something on {name or 'this page'}: fix what's marked, then continue.")
    return stop("Filled what I could; take it from here.")
