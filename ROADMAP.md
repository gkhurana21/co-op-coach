# Co-op Coach roadmap

## Later
- [ ] Re-run `tools/refresh_niches.py --niche <id> --write` each September to date the new season's openings.

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
- [ ] Test Workday status checks and Workday filling against a real account (needs your saved Workday login), and
      inbox sync against a real Gmail/Outlook account.
- [ ] Readers for Phenom, Avature and Eightfold career sites (OpenText, Deloitte US, Two Sigma, Qualcomm). Their
      internships already arrive via SimplifyJobs/LinkedIn/Indeed, so this is about speed and descriptions.
- [ ] Windows support (start script + a few Mac-only bits), then test on a real Windows laptop.
- [ ] **Path rewards** (for a paid plan, later): badges worth sharing, a weekly league with friends.
- [ ] **Shared feed for everyone**: run `central.publish_sql()` once the owner decides to open the feed (read-only
      postings and boards), ship the project URL and publishable key in config, and push boards people add to the
      feed so the crawler reads them for everyone. Events and hackathons could move to the feed too.
- [ ] Hackathon application deadlines: MLH and Devpost don't publish them; learn them from each site, or let the
      student set one (with a reminder).

## Done recently
- [x] **Fast for everyone** (November 2026). On Supabase each query is an internet round trip, and a page made up to 35 of them.
      Reads are kept in memory now, ended at once by a write here or in another process on the computer, refilled behind it, and
      asked for again in parallel on a start: a warm page makes none. Postings come from a copy on disk plus what changed. **The
      Path has tests** (real problems on LeetCode against the clock), a linked LeetCode profile that logs solves by itself, shields
      and a year grid. **Picks are better:** titles read for what the job is (a manufacturing engineer isn't a software role), BS/MS
      isn't PhD-only, remote from another country isn't remote for you, roles where you want to be come first, one per employer.
- [x] **Outlook sign-in really works** with school accounts (Duo, ADFS): the app recognises the mailbox, keeps the sign-in
      when the window closes, and reads the first emails while it's open. **The Path is a 207-stop trail** through the
      NeetCode 150 with real work mixed in, chests, crowns, daily quests and 15 levels. **The Claude Code plugin** starts
      at once, uses the open app, updates itself, and has 13 commands (today, tracks, neetcode, hackathon, doctor and
      more) and a scout agent. The page says so when the internet is down, and keeps trying.
- [x] **Fewer features, all working** (November 2026). Practice, quiz lessons and prep workbooks are gone; Coach tracks
      the real work. **Tracks** replace Startups, Niches and Your market: Jobs is Matches, your pinned tracks and
      ＋ Tracks; 12 role tracks, 11 employer tracks, your own, and new kinds of role found in the feed every day
      (Forward Deployed Engineer, GTM...), each with its postings, companies and market (required vs nice-to-have
      skills, stated pay, trends, where and when; every number opens its postings). **Inbox → calendar**: interviews,
      assessments, recruiter calls and coffee chats from email, with reminders, on the calendar link too. **The
      Path** mixes the work in every unit, built on the NeetCode 150 and nothing else (roadmap order, attempts, reviews, stars for a job, track or
      company). **Coach** answers in seconds while the app is open; **Outlook** connects by signing in, no IT
      approval. Feature list and proof: docs/FEATURES.md.
- [x] **One shared job feed** on Supabase: a crawler (GitHub Actions, `crawl.yml`) reads every board at 7:30, 12:30
      and 17:30 Eastern with quick checks in between, and each app pulls only what changed (about 10 s, inside the
      database) instead of reading ~9,500 boards itself. Real new openings are marked so alerts stay right; boards
      someone added are still read by their own app; if the crawler stops for 12 hours, apps read every board again.
      Seeded from the existing 35,685 postings with their history. Opening it to other people's apps (REST,
      read-only) is written (`central.publish_sql`) but not switched on.
- [x] **Hackathon packages.** 📝 Apply: details and MLH's level of study from the resume, the usual short answers
      drafted from real projects (an AI draft with an invented number is dropped), t-shirt/dietary/travel
      remembered, the form filled in Chrome or a zip; MLH's boxes and demographics are always the student's. 🏁 Submit
      project: the Devpost write-up from a public GitHub repo (pitch, Devpost's sections, Built with from the repo's
      languages and dependency files, a demo script) and the project onto the resume with Undo. Hackathon
      applications have their own tracker statuses and Path milestones, and stay out of the internship numbers.
- [x] **Internship form answers** no longer land in the wrong field ("programming languages" got your program,
      "open-source" was read as "how did you hear about us", "mobile development" got your phone, any label with
      "age" in it). The filler fills each new page too (after a sign-in, or the next step). Autopilot's letter,
      letter style and drafted-answers settings now apply, and an email saying you applied clears the item from
      Ready to apply.
- [x] **The Path**, Duolingo-style: XP from the real work against a daily goal (50 XP by default), streaks, levels
      from Egg to Goose Legend, milestones Coach jumps for, and a path of Get set → Apply → Code → Interview. 85
      classic coding problems in 14 topics by kind of role (LeetCode links), 8 quiz lessons, and a prep workbook per
      posting (with your AI: questions people report being asked there, with sources) ending in a mock interview.
- [x] **Today with Coach**: Autopilot's plan as one clickable sentence, presets, "Tell Coach" in your own words, up to
      50 a run (four at a time), five tabs with sections under them.
- [x] **Excel export**: matches, applications, ready to apply and startups in one colour-coded, filterable workbook.
- [x] **Workday descriptions**: read in the background after each sync, best matches first (~700 a sync).
- [x] **Explore → Startups**: Waterloo, Toronto and Canadian startups ranked by how fast they're hiring (new
      internships in 30 days, role counts each sync notes), with YC facts, funding, people you know and Watch.
- [x] **Thousands more boards**: Common Crawl + SimplifyJobs links, each checked live, about once a month (Settings →
      Job sources → Find new boards); every hiring YC company's jobs page read.
- [x] **Coach**, the chat's goose (designed on a Claude Design canvas): moods, thinking, cheering, 🎤 voice input.
- [x] **Practice extras**: questions read aloud, answers by voice, Market fit flags skills practised but not on the
      resume.
- [x] **Autopilot**: applications prepared side by side (a run of three in ~10 s instead of ~22 s), instant progress,
      a clear finish. **Workday applications filled** step by step (saved login; stops at voluntary questions and
      Review; never presses Submit or touches the robot trap).
- [x] **One-click LinkedIn import** from the export zip in Downloads.
- [x] **A much faster sync: 7-17 minutes down to under a minute** (postings show after ~40 s). Worker processes;
      batched Supabase writes; unchanged postings neither re-read nor re-sent (fingerprints); Workday read through
      each board's own internship filter where it proves complete (about 1,300 internships the keyword search had
      missed), with only the searches a board needs in between full reads; big Lever boards through their board page;
      saves every 15 seconds; AI scoring and alerts after the sync shows done; a quick check (~10-20 s) every 20
      minutes of the boards that matter to you.
- [x] **MATCH: H and C.** Practice (weak spots for a posting, mock interviews graded against a rubric, mental-math
      drills) and People (LinkedIn connections import, Waterloo alumni search, founders with minimum-funding, YC and
      team-size filters, Check funding), plus six more niches.
- [x] **Recruiters tab (MATCH, C, first step).** Everyone who writes to you about an application, from your inbox and
      pasted emails, with their address, title, phone and LinkedIn from the sign-off, grouped by kind of company and
      company. Notes, stars, drafts, spreadsheet export, and a `recruiters` chat tool. See docs/MATCH.md.
- [x] **MATCH: M, A and T** (Explore tab). Your market: the skills your top 30 matches ask for, against your resume
      and against what you actually apply to. Resume Studio's market-fit card: one action per gap (surface it,
      confirm it, plan a project). Niches: Quant & Trading with 161 firms in 12 segments, last season's real opening
      dates, follow (alerts + boards), reminders before opening months, Find more (from postings and your AI, each
      firm checked live), your own niches, and opening dates learned from every sync. Market research in
      docs/MATCH.md.
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

## Not planned
- A remote ChatGPT / Atlas connector (an MCP endpoint on Supabase). ChatGPT already works through the local Codex
  plugin with the same tools; a public endpoint would put the tracker behind an internet-facing URL for little gain.
