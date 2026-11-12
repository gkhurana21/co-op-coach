# What Co-op Coach does, and the proof each part works

The rule (November 2026): fewer features that all work. Every feature here has a suite that checks it on every push
(`.venv/bin/python -m tests.run_all`, on SQLite and on Postgres with `--postgres`) and one thing to try by hand. A
feature without a passing check is fixed or taken out. Taken out so far: interview practice (weak spots, mock
interviews, drills), quiz lessons and prep workbooks, and the separate Startups, Niches and Your market pages (now
tracks).

**Lean by default** (`coopcoach/features.py`, `test_features`, the walkthrough's lean step). A new install shows Today, Jobs
(Matches), Applications and a Calendar of your own interviews, assessments and coffee chats, with the Path and Jarvis. People,
the Tracks browser (2), public events and hackathons (6, 7), and the Excel export (11) are tucked away, Jarvis doesn't offer
them, and syncs don't read event sources. Settings → Account & data → Features → Everything brings them all back; nothing
is deleted either way.

| # | Feature | What it does | Proved by | Try it |
|---|---|---|---|---|
| 1 | **Matches** | ~27,000 open internships from the shared feed and your own boards, merged, scored 0-100 with reasons, and read for who can apply. | `test_matching_auth`, `test_eligibility`, `test_picks`, `tools/feed_check.py` (the live feed, by hand), `test_visa_flags`, `test_central`, `test_sync_speed`, `test_discover` | Jobs → Matches: open *Details* on a posting; the score's reasons and "Can't apply" (when it is) are there. |
| 2 | **Tracks** | Kinds of role and of employer (shipped, yours, found in the feed), pinned to the Jobs bar; each with Postings, Companies and Market; following alerts. | `test_tracks`, `test_track_discovery`, `test_market_v2`, `test_match`, `test_niche_data`, `test_startups` | Jobs → ＋ Tracks → pin *Quant roles*; open it → Market → click a skill: the list has as many postings as the number said. |
| 3 | **Autopilot** | Every day, your best new matches prepared into Ready to apply at once (the AI letter, answers and tailored resume finish behind it, and survive a restart), with a brief; a plan in your own words. | `test_autopilot_plus`, `test_picks`, `test_agent_mcp` | Today → *Run now*; the prepared ones appear under Ready to apply. |
| 4 | **Applying** | An apply session fills each form in Chrome (Workday's multi-page ones too), choosing in real dropdowns and answering what it can from your facts, and stops before Submit; never answers demographic questions or touches CAPTCHAs. | `test_autofill`, `tools/live_forms.py` (real forms, by hand), `test_workday_apply`, `test_reqids` | Ready to apply → *Apply session* → the form fills and stops at Review. |
| 5 | **Tracker and inbox** | Applications move by themselves from Gmail, IMAP, Outlook (signed in, no IT approval: each check reads back the whole window, carries on where the last stopped, and dates each email from Outlook) or a pasted email. | `test_email_connect`, `test_email_paste`, `test_email_rules`, `test_outlook_web`, `test_outlook_backfill`, `test_fixes_misc` | Applications → *Paste an email* with an interview invite: the application moves to Interviewing. |
| 6 | **Calendar and events** | Interviews, assessments and coffee chats from email, saved events, reminders: in Events and on one private calendar link (Supabase function). Events from 31 universities, organizers and hackathons. | `test_calendar_items`, `test_calendar`, `test_events`, `test_event_sources` | Paste an invite, then Events → Month: it's there, marked yours, with *Open the email*. |
| 7 | **Hackathon packages** | The application for a hackathon drafted from your resume, and the Devpost write-up from a public GitHub repo. | `test_hackathon_packages` | Events → Hackathon season → *📝 Apply* on one. |
| 8 | **People** | Recruiters from your email, LinkedIn connections, people you add, by company; alumni search; startup signals. | `test_people`, `test_recruiters` | People → import LinkedIn's Connections.csv; search a company. |
| 9 | **Resume Studio and packages** | The resume as an editable document with a checker, PDF / Word / LaTeX; cover letters and tailored resumes; market fit for any track. | `test_resume_checker`, `test_resume_coach`, `test_resume_links` | Documents → Resume Studio → Market fit → pick a track: same numbers as its Market. |
| 10 | **The Path** | XP, streaks with shields and levels from the real work; a 231-stop trail through the NeetCode 150 with real work mixed in, chests, crowns, daily quests, reviews, stars and tests (real problems on LeetCode against the clock); a linked public LeetCode profile logs solves by itself. | `test_path` | Today → Path → tap the problem Jarvis stands at → Log a try (Solved, 14 min): XP, quests and the trail update. |
| 11 | **Excel export** | Matches, applications, ready to apply and startups in one workbook. | `test_export` | Matches → ⬇ Excel. |
| 12 | **Jarvis** | The chat that acts: 65 tools (search, prepare, track, remind, research, NeetCode, markets...), each step shown, with Undo; replies appear as they're written, and exact everyday requests are answered with no AI at all. | `test_chat_tools`, `test_fast_chat`, `test_instant_prepare`, `test_claude_code`, `test_codex`, `test_groq_mock` | Ask "How's my NeetCode?" then "I solved Two Sum in 12 minutes". |
| 13 | **Reminders and alerts** | New matches, followed tracks and companies, saved events and reminders, to Discord, ntfy or email. | `test_notify`, `test_match` (alerts), `test_chat_tools` (reminders), `test_calendar_items` | Follow a track; after the next sync its new roles alert you. |
| 14 | **Settings and safety** | Sources, the shared feed, inboxes, AI engine, passcode lock, Supabase; the local guard. | `test_lock`, `test_security`, `test_guard`, `test_portability`, `test_central` | Settings → Passcode → lock: every page and API answers "locked". |
| 15 | **MCP plugin** | Co-op Coach's tools and 13 commands in your own Claude Code or Codex: starts at once, uses the open app, updates itself, with a Submit guard. | `test_plugin`, `test_agent_mcp`, `test_claude_code`, `tools/plugin_e2e.py` (real Claude Code, by hand) | `/coop-coach:today` in Claude Code, then `/coop-coach:doctor`. |
| | *Demo* | A fictional student and a snapshot of real postings, one command. | `test_demo` | `python -m coopcoach demo` |

And it's quick: `test_read_cache` holds the budget (once warm no page goes to the database), and `tools/bench.py` and `tools/bench_ui.py` time every page. `tools/walkthrough.py` does the try-it steps above in a real browser on a copy of the demo; `tools/audit_ui.py` presses each different safe button on every page, and in the windows they open, at desktop, phone and light.

Checked on real accounts only with the owner present: Outlook sign-in, Workday form filling, a timed Jarvis reply.
