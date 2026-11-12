---
name: resume
description: Review or tailor the student's resume with Co-op Coach - a recruiter-style review, or the changes that matter for a specific posting, using only real experience. Use for "review my resume", "tailor my resume for <company>".
argument-hint: "[company or role to tailor for]"
model: sonnet
---

# Resume with Co-op Coach

1. Call `my_resume` for the current text, and `review_resume` for Co-op Coach's recruiter-style review.
2. If they named a posting, find it (`search_jobs`, `job_details`) and focus on what that role values.
3. Give the 3-5 changes that matter most, each as exact before → after text. Keep what's already strong.
4. Never add numbers, tools, employers or results that aren't in their resume. Where a number would help, ask
   ("roughly how many users?").
5. Edits are applied in the app's Resume Studio (Documents tab), and `queue_application`/`application_kit` build a tailored
   copy per posting automatically.
