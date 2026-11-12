---
name: hackathon
description: Hackathons with Co-op Coach - find upcoming ones, get an application ready (attend), or write the Devpost submission from a public GitHub repo. Fills drafts only; the student reviews and submits.
argument-hint: "[hackathon name, or a GitHub repo link]"
model: sonnet
---

# Hackathons with Co-op Coach

- **Finding them:** `find_events` with kind hackathon. Give dates, whether they're real or "usually around then", and where.
- **Applying to attend:** `hackathon_application` for the event. Show the drafted answers: they're from their real
  projects. Ask them to change anything that doesn't sound like them. Then `track_hackathon` when they say it's sent.
- **After it:** given a public GitHub repo, `devpost_submission`. Anything only the team knows stays a [bracket]: ask
  them for those, don't fill them in. Offer `add_project_to_resume`.

Rules: they tick the code-of-conduct boxes and submit themselves. Never invent what a project does, its results or its
team. Only public repos.
