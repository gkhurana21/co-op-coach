# MATCH in Co-op Coach

MATCH is a five-step way to run a job search: **M**atch the market, **A**ssemble the resume, **T**arget the
ecosystem, **C**ombine networking with AI, **H**andle the interviews. This page covers what the market already offers
for each step, what Co-op Coach had already, and what's built for each.

**Since November 2026 the M, A and T pages are tracks** (Jobs → Matches | your tracks | ＋ Tracks): every kind of role
or employer opens the same page with its postings, its companies and its market, and new kinds of role (Forward
Deployed Engineer, GTM...) are found in the feed. The niches below are the firm lists behind the employer tracks. The
practice features that were H (weak-spots reports, mock interviews, drills, quiz lessons, prep workbooks) were taken
out: Co-op Coach tracks the real work instead of setting homework.

## The idea, step by step

| | Step | What it means for a student |
|---|---|---|
| **M** | Match the market | Don't read one posting at a time. Look at the 20 to 30 postings that fit you best and see what *they* keep asking for, and at what you actually apply to. |
| **A** | Assemble the resume | Check your resume against that market. Find the skills it asks for that your resume doesn't show, and do one thing about each. |
| **T** | Target the ecosystem | Everyone applies to Jane Street and Citadel. Map *every* kind of firm that hires for the role (smaller prop shops, systematic funds, Canadian pension funds, energy traders...), track them, and know when each one opens. |
| **C** | Combine networking with AI | Find people (alumni, past co-op students) at those companies, and see where your school's alumni are building startups. |
| **H** | Handle the interviews | When an interview or assessment lands, it's on your calendar (from your inbox), a prep sheet is written for it, and your LeetCode is tracked on the Path. |

## Is there anything like it? (market research, October 2026)

Every step exists somewhere, but in separate products, and nobody does all five for co-op students.

| Step | Who does something like it | What's missing |
|---|---|---|
| **M** | [Jobright](https://jobright.ai/compare/simplify), Teal and Handshake score you against **one posting at a time**. Waterloo's own AI tool, JADA (beta, [UW Daily Bulletin](https://uwaterloo.ca/daily-bulletin/2025-10-01)), matches jobs and gives resume tips. [Lightcast](https://aiskills.lightcast.io/) publishes skill demand for the whole labour market. | Nobody adds up *your* top 30 matches to show what your kind of role keeps asking for, or compares that with what you actually apply to. |
| **A** | Teal, Rezi and Jobscan stuff a posting's keywords into your resume, per posting ([comparison](https://hirekey.io/best-ai-job-search-tools)). Reviewers report Jobright adding skills people don't have ([Scoutify review](https://scoutify.com/blog/jobright-review)). | A market-level gap list where each gap has an honest action: surface what you already show, confirm what you have, or build it. |
| **T** | Hand-kept lists only. The Northwestern Fintech Club keeps a public list of about 58 quant firms, including smaller ones like Vatic Labs, Headlands, Radix and Voloridge ([NUFT 2027](https://github.com/northwesternfintech/2027QuantInternships)); [Simplify](https://simplify.jobs/l/Top-Quant-Internships) and QuantBlueprint publish lists too. | A personal niche map that's checked live, tracks itself, learns when firms open, and finds firms you're missing. **The most original part of the idea.** |
| **C** | Jobright's [Insider Connections](https://jobright.ai/job-referral) and [Offerloop](https://www.offerloop.ai/blog/alumni-networking-guide) find alumni at a company; [NextCoffee](https://www.nextcoffee.ai/articles/ai-email-generator-for-internship-networking) writes the outreach. | Not a first. The fresh angle is a Waterloo startup feed: which alumni companies are hiring fastest. |
| **H** | Final Round AI ([review](https://www.loopcv.pro/directory/finalround/)), [Exponent](https://www.lodely.com/blog/exponent-pricing) and [interviewing.io](https://www.lodely.com/blog/interviewing-io-pricing) run mock interviews, but generic ones. | (Researched when H meant practice. Co-op Coach now tracks interviews instead: on the calendar from the inbox, with a prep sheet.) |

Handshake ([Sacra](https://sacra.com/c/handshake/)) is the big incumbent for campus recruiting. Waterloo's
[CS class profile](https://csclub.uwaterloo.ca/classprofile/2024/) is a useful picture of where co-op students
actually end up.

**About "Dr Karan Gupta" and MATCH:** we couldn't find him teaching a MATCH framework. INSEAD has a different
[MATCH framework](https://intheknow.insead.edu/podcast/51-navigating-career-uncertainty-introducing-insead-match-framework)
for career changes. One idea is borrowed from it: *hiring windows*, meaning when each firm usually opens applications.

## What Co-op Coach already had

Matching per posting (a 0-100 score with reasons, plus who can apply), Resume Studio (checker, recruiter-style review,
per-posting tailoring and keyword report), company tiers (Big Tech / Quant / Banks / YC / Startup) and *Follow a
company*, interview prep sheets and mock interviews in the chat, company research, drafted networking and referral
messages, and 4,700+ company job boards read on every sync. MATCH builds on all of that rather than starting over.

## What's built: M, A and T

First an **Explore** tab, now **tracks** (Jobs → a track → Market and Companies), and a card in Resume Studio.

### M: A track's market (Jobs → a track → Market)

Each track's Market (`market.track_market`) reads that track's postings that are open to you from the last 120 days,
the full text of each (kept in `job_facts`): the skills it asks for, which sit under its own *Requirements* and which
under *Nice to have*, and the pay it states (as an hourly figure; nothing converted, nothing guessed). Shares are only
over postings with full text, and that number is always shown; when fewer than half have it, up to 30 more are read
in the background. Trends (last 30 days against the 90 before) only show with enough postings on both sides. Every
number opens exactly the postings behind it. The older summary below still answers the chat's `my_market` for your
targets; with a track it answers from the track.

The first version (Explore → Your market):

* Reads your **top 30 postings you can apply to** in your target roles (or any role you pick, or your top 20/50), each
  with a full description where one has been read, and counts the skills they ask for:
  "C++: 18 of 30 · not on your resume".
* Shows how postings actually write each skill ("Kubernetes", not "Docker/Kubernetes"), the kinds of roles and
  companies in your market, and the **gaps**: skills asked by 15% or more of the sample that you don't show.
* **What you actually go for**: after 5+ applications or stars it compares what you pick with what you said you
  want: "You target Software, but 12 of your 20 picks are Quant roles. See what that market asks for."
* Code: `coopcoach/market.py` (`summary`, `pattern`).

### A: Market fit (Resume Studio → 📈 Market fit)

* Each skill the market asks for gets a status and **one action**:
  * *In your Skills*: nothing to do.
  * *In your bullets, not in Skills* (it says which entry): **Add to Skills**, using the word postings use.
  * *Not on your resume*: **I have this** (you type how to list it, with a reminder to back it up with a bullet), or
    **Plan a project**, a 3-5 week project that would let you list it honestly, with a resume line template whose
    results are all `[placeholders]`. With an AI connected the plan is written for your market and your resume;
    without one, built-in plans cover the common gaps.
* A coverage number: how much of what your market asks for your resume shows.
* Nothing is ever added without your click. Code: `market.fit`, `market.add_skill`, `market.project_plan`.

### T: Niches, the employer tracks' firm lists (Jobs → a track → Companies)

* **Quant & Trading** ships with **161 firms in 12 segments**: big market makers, smaller prop shops (22, most of
  them little-known), systematic funds, pod shops, macro funds, **Canadian trading desks and pension
  funds** (RBC, TD, BMO, CIBC, Scotiabank, Ontario Teachers', CPP Investments, OMERS, PSP, CDPQ, AIMCo, BCI...),
  crypto market makers, exchanges and market infrastructure, energy and commodity traders, quant asset managers, bank
  strats desks, and sports betting and prediction markets. Firms with Canadian offices (DRW and Squarepoint in
  Montreal, among others) are flagged.
* For each firm: roles open to you right now (from the postings Co-op Coach already reads), its best match, its
  careers page or job board, and **when it opened internships last season**. Those dates are real: they're the day
  each firm's first Summer 2027 internship appeared on SimplifyJobs' list in 2026. Last season, 23 firms opened in
  July, 23 in August and 15 in September; SIG opened in April and Point72 in March, while Jain Global posted one
  London role in June and its New York roles only in October.
* **Follow** a niche: its firms' job boards join every sync, and their new roles always alert you (marked 🎯), even
  below your usual score threshold. At firms that aren't trading firms through and through (an exchange, a bank, an
  energy company, a betting company), only roles that fit the niche (quant, trading, engineering) do, not every
  internship they post. The track's *Postings* (and Matches' Track picker) show the niche's roles.
* **🔔 Reminders** two weeks before each month firms usually open, on your calendar link and as notifications.
* **🔎 Find more**:
  1. *From your postings*: companies posting this kind of role that aren't on the list (no AI needed).
  2. *From your AI* (Claude Code searches the web first): firms and **whole segments** you may be missing,
     preferring smaller firms. **Every firm is checked live before you see it**: its postings are already in Co-op
     Coach, or its job board or careers page answers, or a Greenhouse board under its exact name answers. Links into
     your computer or home network are refused. Firms that don't check out are listed as left out, never shown as
     suggestions.
  3. You review: **Add** or **Not for me** (it won't come back).
* **Start your own niche** (Robotics, Fintech, Game studios... or anything you type). It fills from postings matching
  its keywords, then from your AI.
* Hide firms that aren't for you, add firms you know (checked the same way), and put hidden ones back.
* **Learning the windows**: every sync records the first internship it sees from each company for each term
  (`firm_openings`), so next season the niche shows when firms actually opened, as seen by your own Co-op Coach.
* Code: `coopcoach/niches.py`, the list in `coopcoach/niches.yaml`, `tools/refresh_niches.py` (re-reads opening dates
  each season and lists quant firms the file is missing), API in `routes_match.py`.

### In the chat and your own Claude Code

`my_market` ("what do my target roles want that I don't have?"), `explore_niche` ("which smaller quant firms are in
Chicago?") and `follow_niche` (with Undo). They're in the MCP server too, so your Claude Code or Codex can use them.

### Honesty rules (same as the rest of the app)

* Never invent: skills are only added when you press the button, and project plans never claim results.
* Nothing an AI names reaches you unchecked.
* Opening dates come from posting dates, not guesses. Careers links in the list come from the Northwestern list or
  from real postings; a firm without one gets a link to search for its careers page.

## More niches (built)

Six more fields ship firm by firm next to Quant & Trading: **AI labs & ML infrastructure**, **Robotics, autonomy &
space**, **Chips & hardware**, **Fintech**, **Developer tools & cloud** and **Waterloo & Toronto startups** (186
firms). Each firm's website was checked live, its job board found from its own careers page or from real postings
(`tools/discover_niches.py`), and its opening date read from SimplifyJobs (`tools/refresh_niches.py --niche <id>`).
In these fields every firm is in the field, so any of its internships counts (`all_roles`), except its sales and
business roles; a `role_only` segment holds big companies with one team in the field (Nokia, TD's Layer 6), where only
the field's own roles count.

## H: Handle the interviews

Practice (weak-spots reports, mock interviews, mental-math drills), the quiz lessons and the prep workbooks were taken
out in November 2026: they made students practise inside a job tracker, and the owner's call was fewer features that
all work. What H is now:

* **On your calendar**: interviews, online assessments (with their deadline), recruiter calls, coffee chats and info
  sessions found in your email or a pasted one (`meetings.py`, `calendar_items`), with reminders; a moved or cancelled
  invite updates it.
* **A prep sheet** when an interview email lands, and `research_company` in the chat.
* **The NeetCode 150 tracked** on the Path: its 18 topics in roadmap order, attempts with time and notes, reviews after 2, 5 and 12 days, and
  problems starred for a job, a track or a company (`path.py`, `coding_progress` and `record_problem` in the chat). Tests prove a topic: real problems on LeetCode against the clock, no hints (`start_path_test`), and a linked public LeetCode profile logs solves by itself (`link_leetcode`, `leetcode.py`).

## C: Who works where (People tab)

The Recruiters tab became **People**: everyone who writes to you about an application, your LinkedIn connections, and
people you add, grouped by kind of company and company.

* **LinkedIn connections**: LinkedIn doesn't let other apps read your connections (its sign-in shares only your own
  profile), and driving your logged-in browser to collect them would break its terms. So you download your own data
  (Me → Settings & Privacy → Data privacy → Get a copy of your data → Connections) and import `Connections.csv`: name,
  company, title, profile link and when you connected. Importing again catches job changes without undoing your
  edits; someone already here from an email gets their LinkedIn link instead of a second entry.
* **🎓 Find alumni** next to every company (and on niche firms and postings) opens LinkedIn's own search for people
  from your school there, signed in as you. Nothing is scraped.
* **Roles** from titles (founders & CEOs, recruiters, leaders, engineers, product) and **company signals**: YC batch,
  team size and hiring (the YC directory), internships open to you now (your postings), and **💰 Check funding**, which
  has your Claude Code or ChatGPT search the web and keep only what a source states (total raised, last round,
  investors, sources). Filters: startups only, hiring now, YC-backed, raised $1M+ / $5M+ / $20M+ / $100M+, team size.
  So "founders I know at startups that actually raised money" is two clicks; a startup that disclosed nothing drops
  out of the funding filters.
* **A track's Companies** show 👥 how many people you know at each firm.
* Chat: "who do I know at Optiver?" or "founders I know" (`recruiters` with `role`), with each company's facts and
  the alumni link. Code: `coopcoach/contacts.py`, `static/recruiters.js`, tests in `tests/test_people.py`.

## Next

* The startup feed is built (the Startups in Canada track), and so is one-click LinkedIn import.

## Paid version: decide later

A paid tier would need hosted accounts and billing, and its own AI keys: Anthropic allows Claude subscription logins
only inside Claude Code and claude.ai, not inside other products
([report](https://winbuzzer.com/2026/02/19/anthropic-bans-claude-subscription-oauth-in-third-party-apps-xcxwbn/)).
Today everything runs on your own Mac with your own AI. The plan is to build and test MATCH first, and decide on
pricing (candidates: AI "Find more", the people list) once people are using it.
