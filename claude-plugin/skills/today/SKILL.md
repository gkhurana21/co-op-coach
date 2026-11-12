---
name: today
description: Start the day with Co-op Coach - what's ready to apply, what's coming up (interviews, assessments, coffee chats), what's new, your NeetCode next step and streak, in one short briefing. Use for "what should I do today", "what's up", "morning brief".
allowed-tools: mcp__plugin_coop-coach_coop-coach__daily_brief, mcp__plugin_coop-coach_coop-coach__my_calendar, mcp__plugin_coop-coach_coop-coach__my_path, mcp__plugin_coop-coach_coop-coach__coding_progress, mcp__plugin_coop-coach_coop-coach__ready_queue, mcp__plugin_coop-coach_coop-coach__coop_status
model: haiku
---

# Today with Co-op Coach

Call these together: `daily_brief` (days=1), `my_calendar` (days=7), `my_path`, `ready_queue`, `coding_progress`.

Answer in this order, leaving out any part with nothing in it. Keep it to what fits on one screen.

1. **Don't miss:** anything in the next 48 hours from `my_calendar`, with its join link; deadlines say "due". Anything
   with `to_book` needs them to pick a time: give the booking link.
2. **Ready to apply:** how many, and the best two by name.
3. **New:** matches worth a look (`new_matches`) and inbox updates.
4. **Your Path:** streak, XP against today's goal, today's quests left, and the next NeetCode problem with its link. A test
   running (`test_running`) comes first, with the time left.
5. **One thing to do first:** a single recommendation, with why.

If `daily_brief` shows no new postings or an inbox needs signing in again, call `coop_status` and say what to do.
Offer: `/coop-coach:apply <company>`, `/coop-coach:autopilot`, `/coop-coach:neetcode`.
