---
name: doctor
description: Check that Co-op Coach is working - the app, database, last sync, inboxes (Outlook or Gmail that needs signing in again), AI engine - and say exactly what to do about anything that isn't. Use when something looks wrong, tools fail, or postings or emails seem out of date.
allowed-tools: mcp__plugin_coop-coach_coop-coach__coop_status
model: haiku
---

# Is Co-op Coach working?

Call `coop_status` and report one line per thing, only what matters:

- `app_open` false: everything still works here, just slower. To see the pages, start it with `python -m coopcoach serve`
  in the project folder, then open http://localhost:8765.
- `last_sync_minutes_ago` over a day: press Sync in the app, or say so if the app isn't open.
- An inbox with `needs_sign_in`: Settings → Inbox & Workday → Sign in again. Never ask for their password.
- An inbox whose `last_status` starts with `error`: quote it, and suggest signing in again.
- No inboxes: Settings → Inbox & Workday → Connect an inbox.
- `ai_engine` empty: Settings → AI, connect Claude Code.
- `leetcode_linked` empty: nothing's wrong, but `/coop-coach:neetcode` can link their public LeetCode so solves log themselves.
- A tool error about connecting or an expired ticket: in the app, Today → Connect my Claude Code, again.
- A tool error about reaching data: their internet, or Supabase.

If everything's fine, say so in one line. Don't change settings.
