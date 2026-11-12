---
name: track-email
description: Track a recruiting email the student pastes or forwards (application received, online assessment, interview invite, rejection, offer) in Co-op Coach - find or add the application, move its status, keep booking / assessment links and put any stated time on their calendar. Use when they paste an email or say "I got this email from <company>".
model: haiku
---

# Track a recruiting email with Co-op Coach

1. Call `log_email` with the whole email as `text`. Add `company`, `role`, `status` and `when` (interview time, local
   ISO like 2026-10-14T14:00) or `deadline` if you can read them; the rules fill in the rest.
2. Tell them in a sentence what changed (e.g. "Lumen Robotics · Software/ML Engineering Intern → interviewing").
3. Next step:
   - Interview with a booking link: they book it themselves. When they tell you the time, call `set_dates`.
   - Online assessment: say the deadline (it's on their calendar).
   - Rejection: offer `search_jobs` for similar open roles.
4. Offer `/coop-coach:interview-prep` for interviews and assessments (a prep sheet is also written automatically).

Never reply to the recruiter, book a time, or submit anything on their behalf. If `log_email` can't tell the company,
ask them and call it again with `company`.
