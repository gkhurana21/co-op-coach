---
name: tracks
description: Explore the job market by track with Co-op Coach - a kind of role (Software, ML, Quant, Forward Deployed Engineer, GTM) or employer (startups, trading firms, banks) - what it asks for, required vs nice-to-have skills, stated pay, who's hiring, resume gaps, and when firms open. Use for "what does this market want", "which firms am I missing", "what's new out there".
argument-hint: "[a track, e.g. quant roles, forward deployed, trading firms]"
model: haiku
---

# Tracks with Co-op Coach

1. No track named? Call `list_tracks`. Show their pinned ones, what's `rising`, and two or three worth a look. Stop and
   ask which to open.
2. With a track: call `my_market` with `track`. Report:
   - **The market:** how many are open to them, and how many have full text (shares are only over those).
   - **Skills:** the top ones with their share, how many require them vs call them nice to have, and any trend.
   - **Pay:** only what postings state: median and middle half, in its currency. If nothing's stated, say so.
   - **Your gaps:** skills asked by many that aren't on their resume, each with one action: it's in their bullets but not
     Skills (add it), they have it (add it and back it up), or they don't (a small project). Never add one they
     haven't confirmed.
3. An employer track: call `explore_niche` too. Point out smaller firms, Canadian offices and when firms open.
4. `search_jobs` with `track` for its best postings.
5. Offer: `pin_track`, `follow_track` (new roles always alert them), `queue_application`, `/coop-coach:apply`.

Only what the tools return: no invented firms, skills, pay or dates.
