---
name: autopilot
description: Run the student's job-hunt routine with Co-op Coach - check what's new, prepare the best applications into Ready to apply, flag follow-ups, interviews and this week's events. Use when they say "run my autopilot", "what should I do today", "morning routine" or "prep my applications".
model: sonnet
---

# Co-op Coach autopilot

Work through the Co-op Coach tools (MCP server `coop-coach`). Default: prepare up to **3** applications unless the
student gives a number.

1. Call `daily_brief` (days=1, or more if they ask about a longer stretch).
2. Pick the applications worth preparing:
   - Start from `preview_picks`: their Autopilot settings already applied, one per employer, roles where they want to be first,
     each with why. Add from `new_matches.top`, or `search_jobs` with `posted_within_days: 7`, if that's thin.
   - Call `job_details` on each serious candidate before choosing. Skip anything with eligibility `no`, anything already
     applied to or in `ready_to_apply`, and a posting whose `better_version` (same role in a better city) exists.
   - Prefer: high score, eligibility `fit`, posted recently, a mix of companies (one per company).
3. For each pick, call `queue_application` with `job_id` and a one-line `why` grounded in the posting and their
   resume. It writes the answers, tailored resume and cover letter.
4. Note follow-ups: `quiet_3_weeks` (suggest a short follow-up or moving on), `inbox_updates` and `prep_for`
   (offer `/coop-coach:interview-prep`).
5. Check `my_calendar` (days=7): flag anything soon, and anything still to book.
6. Mention 1-3 events from `events_this_week` worth going to (company events first). Don't save them unless asked.

Finish with a short brief:
- **Ready to apply** (what you prepared and why, one line each). They review and submit from the app's Today tab
  (http://localhost:8765) or with `/coop-coach:apply <company>`.
- **Needs you** (follow-ups, interview prep, anything to book, anything blocked).
- **This week** (events).

Rules: never submit an application, never invent experience or numbers, never change their preferences unless asked.
