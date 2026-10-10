# Co-op Coach roadmap

## Next up
- [ ] **Part-time jobs deep dive.** Part-time roles are hard to find but a big market: work out where they're
      actually posted (campus job boards, Indeed/LinkedIn part-time filters, local businesses, research assistant
      and TA postings, startups hiring part-time during the term), add them as a source, and give them their own
      tab, filters (hours/week, on-campus, remote) and matching. Checked live on 2026-10-08: LinkedIn's guest search
      with the part-time filter works for Waterloo; UWaterloo's own Workday site (uwaterloo.wd3 / uw_careers, 104
      roles) answers; Job Bank Canada's part-time search works; WUSA's jobs page loads.

- [ ] **Full-time job seekers (new grads and beyond).** Extend Co-op Coach beyond internships: full-time/new-grad
      sources and filters, H-1B/OPT/STEM-OPT aware matching, referral and outreach tracking, interview prep and
      offer comparison, and AI help that takes busywork off the hustle (tailored applications at scale, follow-up
      drafts, weekly plans).

## Done recently
- [x] Events from 31 universities in Canada and the US (Localist / LiveWhale / Tribe / iCal readers, a career-only
      filter, each source checked live by tools/discover_event_sources.py), Toronto organizers, and the 40 major
      hackathons with a season view; month view; grouped sources.
- [x] Paste a recruiter email (chat or Applications): tracked, status moved, links and times kept, prep sheet; plus
      chat tools for reminders, company research, drafts, resume fixes, Autopilot controls (42 tools).
- [x] ChatGPT (Codex) as an engine and agent, tested live (JSON answers and an agent run through the MCP tools).
- [x] Simpler UI (list-based Applications, 6 Settings sections), split the large modules, ruff + CI, one-command demo,
      README with screenshots and a walkthrough, "Your numbers".
- [x] Supabase as the database (private schema, Keychain-held password, one-command move with count checks) and a
      calendar link served by a Supabase function (saved events + interview/OA dates, reminders, revocable).
- [x] Resume Studio proofreading (typos, capitals, unfinished sentences, split lines) with one-click fixes, and
      "Check against my file" that keeps your edits.
- [x] Autopilot (daily prepared applications + brief + alerts), your Claude Code as the engine for all AI, the
      Co-op Coach plugin for Claude Code (/coop-coach:autopilot, apply, hunt, followups, interview-prep, events,
      resume) with a Submit guard, "Apply with my Claude Code" in Terminal, and an Activity log with Undo. Black theme.
- [x] Chat agent that acts (search, star, track, letters, kits, events, preferences) with Undo; application kits with
      "fill it in Chrome" (never submits) and hand-off to browser agents; MCP server for your own Claude.

## Later
- [ ] Agent: fill Workday applications too (needs the saved Workday login flow).
- [ ] Readers for Phenom, Avature and Eightfold career sites (OpenText, Deloitte US, Two Sigma, Qualcomm). Their
      internships already arrive via SimplifyJobs/LinkedIn/Indeed, so this is about speed and descriptions.
- [ ] Re-run `tools/discover_boards.py` monthly (or on a schedule) so new companies are picked up automatically.
- [ ] Windows support (start script + a few Mac-only bits), then test on a real Windows laptop.
- [ ] Test Workday status checks against a real account and harden the "My Applications" reader.
- [ ] Test Outlook inbox sync against a real Microsoft account.

## Not planned
- A remote ChatGPT / Atlas connector (an MCP endpoint on Supabase). ChatGPT already works through the local Codex
  plugin with the same tools; a public endpoint would put the tracker behind an internet-facing URL for little gain.
