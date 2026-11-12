---
name: events
description: Find and plan career events with Co-op Coach - info sessions, hackathons, networking, coffee chats - for this week or a date range, and save the ones worth going to. Use for "events this week", "hackathons this month", "what should I go to".
model: haiku
---

# Events with Co-op Coach

1. Call `find_events` with what they asked for (`region`, `kind` like hackathon/info_session/networking, `days`).
   Without specifics: next 7 days, their region.
2. Rank: events from companies they follow or applied to first, then by relevance to their targets.
3. Show date, title, where and why it's worth it (one line each).
4. Save the ones they pick with `save_event` (mark `interested` or `going`). Saved events get a reminder the day before
   and appear in their calendar feed from the app.
