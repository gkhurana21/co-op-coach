---
name: hunt
description: Find internship/co-op roles with Co-op Coach that match a request ("quant internships in NYC for summer", "YC startups hiring in Waterloo"), vet who can apply, and star or prepare the best ones. Use for any job search request.
argument-hint: "<what you're looking for>"
model: sonnet
---

# Hunt with Co-op Coach

1. Turn `$ARGUMENTS` into `search_jobs` filters (`role`, `location`, `company_type`, `posted_within_days`, `query`), or a
   `track` for a kind of role or employer (`list_tracks` shows them; new roles like Forward Deployed Engineer are there). Run
   a couple of variations if the first is thin. For a big batch, hand the vetting to the `scout` agent.
2. Call `job_details` on the most promising 3-6. Read `who_can_apply`, `notes` and `reasons`; drop anything they can't
   apply to.
3. Present a short ranked list: company · role · score · why it fits · anything to watch (eligibility, term,
   graduation window).
4. Offer next steps and do them on request: `star` (or follow the company), `queue_application` to prepare it,
   `/coop-coach:apply` to fill it, `follow_track` if it's a whole kind of role they want.

Never invent postings or details: only what the tools return.
