---
name: interview-prep
description: Prepare the student for an online assessment or interview at a company they applied to through Co-op Coach - company research, likely questions, STAR stories from their real resume, and questions to ask. Use for "prep me for my <company> interview" or "I have an OA".
argument-hint: "<company or role>"
model: opus
---

# Interview prep with Co-op Coach

1. Find the application: `my_applications` (status oa or interviewing), then `job_details` for the posting, and `my_calendar`
   for when it is and how to join.
2. Read their resume with `my_resume`.
3. If you can search the web, look up the company and team: what they build, recent news, interview format for
   interns (OA platform, rounds).
4. Produce:
   - **The role in one paragraph** and what they'll likely test.
   - **Likely questions**: technical (topics, not leaked questions), behavioural, and "why us".
   - **3-4 STAR stories** built only from real resume experience. Never invent results or numbers; where a number would
     help, ask them for it.
   - **Questions to ask** the interviewer (specific to this team).
   - **A 3-day plan** if the date is close.
5. Coding rounds: `coding_progress`, then name the NeetCode 150 topics this role leans on and how far along they are. Offer
   `star_problem` to keep a list for this company. Link to problems; never paste their text.
6. If the date isn't in `my_calendar`, add it (`set_dates`), and offer a reminder the day before (`remind_me`).

This is preparation they do themselves: don't run mock interviews or quizzes.
