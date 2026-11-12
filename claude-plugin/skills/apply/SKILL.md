---
name: apply
description: Apply to an internship/co-op with Co-op Coach - prepare answers, tailored resume and cover letter, then fill the application form in Chrome and stop before Submit. Use when the student says "apply to <company/role>", "fill this application" or names a posting from Ready to apply.
argument-hint: "[company, role or job id; nothing for everything that's ready]"
model: sonnet
---

# Apply with Co-op Coach (you fill, the student submits)

**Nothing in `$ARGUMENTS`: a session through everything that's ready.** Call `ready_queue` and list them, best first, with
anything to watch. Ask how many to do now. Then for each: steps 2-6 below, wait until they say they've submitted or skipped
it, then the next one (`apply_session_next` gives it). End with how many went in and what's left.

1. **Find the posting.** If given a job id, use it. Otherwise call `ready_queue` and `search_jobs` with the company or
   role from `$ARGUMENTS` and confirm which one if there's any doubt.
2. **Check it.** Call `job_details`. If eligibility is `no`, stop and say why (e.g. "US-based applicants only"),
   and point to `better_version` if there is one.
3. **Get everything.** Call `application_files`: every answer (`answers`), their contact details (`you`), and the tailored
   resume and cover letter as local files (`files.resume`, `files.letter`).
4. **Fill the form.**
   - If you have Claude in Chrome tools: open `apply_url`, fill each field from `answers`/`you`, choose dropdown options that
     match the answer, upload `files.resume` (and `files.letter` where a cover letter is asked for) with the file upload
     tool. Multi-page forms: fill each page and use Next/Continue, but **never** the final Submit/Send application.
   - Otherwise call `fill_application`: it opens the form in a Chrome window on their screen and fills what it knows.
5. **Stop and hand over.** List anything left: questions marked `needs_you`, ones you couldn't match, CAPTCHAs,
   sign-in pages. Ask them to review every field and press Submit themselves.
6. When they say they've submitted, call `track` with status `applied`.

Hard rules:
- Never press Submit / Send / Finish application. The student does.
- Never solve CAPTCHAs, never sign in, create accounts or type passwords. If a sign-in wall appears, stop and ask them.
- Leave voluntary demographic questions (gender, race/ethnicity, veteran, disability, orientation) blank for them.
- Use only answers from Co-op Coach or the student. If something isn't covered, ask; never guess or invent.
