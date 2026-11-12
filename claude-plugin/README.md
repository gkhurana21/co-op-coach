# Co-op Coach for Claude Code

Your internship hunt inside any Claude Code session. In the app: Today → Connect my Claude Code. It installs this plugin
for this computer and keeps it up to date whenever the app updates.

| Command | What it does |
|---|---|
| `/coop-coach:today` | One screen: what's coming up, ready to apply, new, your Path, and the one thing to do first |
| `/coop-coach:autopilot` | Check what's new and prepare the best applications |
| `/coop-coach:hunt <what>` | Find and vet roles that match a request |
| `/coop-coach:apply [company]` | Prepare an application and fill the form, or with nothing given, a session through everything ready. **You press Submit** |
| `/coop-coach:tracks [track]` | A market: required vs nice-to-have skills, stated pay, your gaps, who's hiring, when firms open |
| `/coop-coach:neetcode [what you did]` | The NeetCode 150: what's next, reviews, logging a try, tests against the clock, linking your LeetCode |
| `/coop-coach:followups` | Inbox updates and quiet applications, with follow-ups drafted, never sent |
| `/coop-coach:interview-prep <company>` | Company, likely questions, STAR stories from your real resume, what to review |
| `/coop-coach:hackathon [name or repo]` | Apply to attend, or write the Devpost submission from a public repo |
| `/coop-coach:events` | Career events worth going to |
| `/coop-coach:resume [company]` | A recruiter-style review, or tailoring for one posting |
| `/coop-coach:track-email` | Track a recruiting email you paste |
| `/coop-coach:doctor` | What's connected and working, and what to do about what isn't |

The `scout` agent vets a batch of postings in the background and returns a shortlist.

**Other clients:** in Codex and other MCP clients, the same commands are prompts on the server.

**Fast:** the tools list as soon as Claude Code starts. While the app is open, it answers them with everything already
loaded; when it's closed, they still work, a little slower.

**Safe by design:** it fills forms but never submits (a hook blocks the click in the browsers it can read), never solves
CAPTCHAs or signs in for you, never answers voluntary demographic questions, drafts messages but never sends them, and
never invents experience or numbers. Every change is in the app's Activity log, with Undo.
