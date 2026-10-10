"""The chat agent: Co-op Coach's actions as tools an AI can call (search, star, track, write letters, build
application kits, queue applications, events, preferences), plus the loop that runs them.

The same TOOLS power the in-app chat (Groq for free, Claude, or your own Claude Code), Autopilot, the MCP server your
own Claude connects to, and the buttons on chat cards. Every call is written to the Activity log (agent_actions)
with how to undo it. Nothing here ever submits an application: the agent prepares everything and opens the form;
you press Submit.
"""

import contextvars
import json
import logging
import time

from . import ai, db, events, store

log = logging.getLogger(__name__)
_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("agent_ctx", default={})

# the tools live in coopcoach/tools/ by area; they're imported here so TOOLS (and older callers) see them
from .tools.applications import (  # noqa: F401
    _prep_lock,
    _prepping,
    add_application,
    check_inbox,
    discard_draft,
    draft_followup,
    draft_message,
    forget_email,
    log_email,
    my_applications,
    prep_interview,
    prep_pending,
    research_company,
    set_dates,
    start_prep,
    track,
)
from .tools.calendar import (  # noqa: F401
    _event_regions,
    add_event_source,
    cancel_reminder,
    find_events,
    fire_reminders,
    remind_me,
    reminders,
    remove_event_source,
    save_event,
)
from .tools.common import (  # noqa: F401
    STATUSES,
    _app_job,
    _app_row,
    _epoch,
    _find_job,
    _job_brief,
    _new_doc,
    _resume_text,
    _safe,
    _target,
)
from .tools.jobs import (  # noqa: F401
    _mark_opened,
    application_files,
    application_kit,
    apply_session_next,
    cached_kit,
    fill_application,
    job_details,
    not_for_me,
    open_link,
    preview_picks,
    queue_application,
    ready_queue,
    refresh_jobs,
    search_jobs,
    star,
    write_cover_letter,
)
from .tools.profile import (  # noqa: F401
    _AP_KEYS,
    autopilot_settings,
    change_autopilot,
    daily_brief,
    fix_resume,
    my_resume,
    restore_resume,
    review_resume,
    run_autopilot,
    update_preferences,
)

# --- tools ---------------------------------------------------------------------------------------------------


SHORT_LIVED = False  # True inside the MCP helper process, which exits when the chat answers: no background work there


# --- more things the chat can do --------------------------------------------------------------------------------


# name -> (function, description, JSON schema). Descriptions stay short: they're sent with every chat turn.
def _s(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


_STR, _INT, _BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}

TOOLS = {
    "search_jobs": (search_jobs, "Search the student's ranked internship/co-op matches.", _s({
        "query": _STR, "location": {"type": "string", "description": "city, 'Remote', 'Canada'..."},
        "role": {"type": "string", "description": "software, ai, data, quant, hardware, product, business"},
        "company_type": {"type": "string", "description": "yc, startup, big tech, quant, bank"},
        "posted_within_days": _INT, "include_ineligible": _BOOL, "limit": _INT})),
    "job_details": (job_details, "Full details of one posting: who can apply, why it fits, description.",
                    _s({"job_id": _STR}, ["job_id"])),
    "star": (star, "Star a posting, or follow a company (pins its roles + alerts). on=false to undo.",
             _s({"job_id": _STR, "company": _STR, "on": _BOOL})),
    "track": (track, "Set an application's status: saved, applied, oa, interviewing, offer, rejected, or remove.",
              _s({"job_id": _STR, "status": _STR}, ["job_id", "status"])),
    "my_applications": (my_applications, "The student's tracked applications and counts.", _s({"status": _STR})),
    "write_cover_letter": (write_cover_letter, "Write a cover letter for a posting (style: classic, startup, corporate, "
                                               "story, email).", _s({"job_id": _STR, "style": _STR}, ["job_id"])),
    "application_kit": (application_kit, "Prepare everything to apply: answers to the form's questions, tailored "
                                         "resume + letter. The student submits.", _s({"job_id": _STR}, ["job_id"])),
    "find_events": (find_events, "Upcoming career events (info sessions, hackathons, networking).", _s({
        "query": _STR, "region": {"type": "string", "description": "Waterloo, Toronto, Online, SF, New York..."},
        "kind": {"type": "string", "description": "hackathon, info_session, networking, workshop, coffee_chat"},
        "days": _INT, "saved_only": _BOOL, "limit": _INT})),
    "save_event": (save_event, "Save an event (mark: interested, going, or none).",
                   _s({"event_id": _STR, "mark": _STR}, ["event_id"])),
    "update_preferences": (update_preferences, "Change what they're looking for (locations, roles, terms like "
                                               "'Summer 2027', aim: balanced/yc/startups/big corps).", _s({
        "locations": {"type": "array", "items": _STR}, "roles": {"type": "array", "items": _STR},
        "terms": {"type": "array", "items": _STR}, "aim": _STR})),
    "review_resume": (review_resume, "Recruiter-style review of their resume with the few changes that matter.", _s({})),
    "daily_brief": (daily_brief, "What's new and what needs doing: new matches, ready-to-apply queue, inbox updates, "
                                 "quiet applications, interviews to prep, this week's events.", _s({"days": _INT})),
    "queue_application": (queue_application, "Prepare an application now (answers + tailored resume + letter) and add "
                                              "it to Ready to apply for the student to review and submit. remove=true "
                                              "takes it out.", _s({"job_id": _STR, "why": {
                                                  "type": "string", "description": "one line: why this one"},
                                                  "remove": _BOOL}, ["job_id"])),
    "ready_queue": (ready_queue, "Applications already prepared and waiting for the student (status: ready, applied, "
                                 "skipped).", _s({"status": _STR})),
    "fill_application": (fill_application, "Open the application in a Chrome window on the student's screen and fill "
                                           "it in. Never submits: they review and press Submit.",
                         _s({"job_id": _STR}, ["job_id"])),
    "application_files": (application_files, "For browser agents filling a form themselves: every answer, contact "
                                             "details, and the tailored resume + letter as local files to upload.",
                          _s({"job_id": _STR}, ["job_id"])),
    "my_resume": (my_resume, "The student's resume as plain text (for tailoring and interview prep).", _s({})),
    "draft_followup": (draft_followup, "Draft (never send) a follow-up on a quiet application, or kind='thankyou' after "
                                       "an interview. Saved to Documents.", _s({"job_id": _STR, "kind": _STR}, ["job_id"])),
    "prep_interview": (prep_interview, "Write an interview / online-assessment prep sheet from their real resume.",
                       _s({"job_id": _STR}, ["job_id"])),
    "discard_draft": (discard_draft, "Delete a follow-up, thank-you or prep draft.", _s({"doc_id": _INT}, ["doc_id"])),
    "log_email": (log_email, "The student pasted an email about an application (received, assessment, interview, "
                             "rejection, offer): track it on the right application (adds it if untracked), move the "
                             "status, keep its booking/assessment links, calendar any stated time, start a prep sheet. "
                             "Pass the whole email as text; add company/role/status if you can read them.", _s({
        "text": _STR, "company": _STR, "role": _STR, "status": {"type": "string", "description": " | ".join(STATUSES)},
        "when": {"type": "string", "description": "interview time if stated, local ISO e.g. 2026-10-14T14:00"},
        "deadline": {"type": "string", "description": "assessment deadline if stated, local ISO"}}, ["text"])),
    "set_dates": (set_dates, "Put an interview time or assessment deadline on an application and their calendar "
                             "(local ISO time, e.g. 2026-10-14T14:00).", _s({
        "job_id": _STR, "interview_at": _STR, "oa_due_at": _STR, "clear": _BOOL}, ["job_id"])),
    "check_inbox": (check_inbox, "Scan their connected inbox now for application updates.", _s({})),
    "add_application": (add_application, "Track an application they made elsewhere (company, role, link, status).", _s({
        "company": _STR, "title": _STR, "url": _STR, "status": _STR, "req_id": _STR}, ["company"])),
    "research_company": (research_company, "Brief on a company before applying, networking or interviewing: what they "
                                           "do, recent news, intern interviews, talking points from their resume "
                                           "(searches the web on Claude Code). Saved to Documents.", _s({
        "company": _STR, "job_id": _STR})),
    "draft_message": (draft_message, "Draft (never send) a message: " + ", ".join(ai.MESSAGE_KINDS) + ". Saved to "
                                     "Documents.", _s({"kind": _STR, "job_id": _STR, "company": _STR, "person": _STR,
                                                       "notes": {"type": "string", "description": "what to say"}})),
    "fix_resume": (fix_resume, "Apply every safe small fix to their resume (typos in titles, capitals, split lines, "
                               "spaces, dashes) and list what still needs them.", _s({})),
    "restore_resume": (restore_resume, "Undo fix_resume.", _s({"backup_id": _INT}, ["backup_id"])),
    "run_autopilot": (run_autopilot, "Start an Autopilot pass now (prepares the best new matches to apply to).", _s({})),
    "change_autopilot": (change_autopilot, "Change Autopilot settings: on, per_day, min_score, weekly_goal, "
                                           "paused_until, time, what it prepares, fill_with, block/unblock a company.",
                         _s(_AP_KEYS)),
    "refresh_jobs": (refresh_jobs, "Check every job source for new postings now (a few minutes).", _s({})),
    "not_for_me": (not_for_me, "Hide a posting from Autopilot and learn why (reason: location, company, role, senior, "
                               "term).", _s({"job_id": _STR, "reason": _STR}, ["job_id"])),
    "add_event_source": (add_event_source, "Add an events calendar link (.ics/webcal, Luma, Meetup, Eventbrite, a "
                                           "university calendar) to Events.", _s({"url": _STR}, ["url"])),
    "remove_event_source": (remove_event_source, "Remove an events source.", _s({"feed_id": _INT}, ["feed_id"])),
    "open_link": (open_link, "Open a link (posting, form, booking, assessment) in their browser on this Mac.",
                  _s({"url": _STR, "job_id": _STR})),
    "remind_me": (remind_me, "Set a reminder (local ISO time): it pings them and goes on their calendar link.", _s({
        "title": _STR, "when": _STR, "job_id": _STR}, ["title", "when"])),
    "reminders": (reminders, "Their upcoming reminders.", _s({})),
    "cancel_reminder": (cancel_reminder, "Cancel a reminder.", _s({"reminder_id": _INT}, ["reminder_id"])),
    "forget_email": (forget_email, "Undo a pasted email (Undo uses this).", _s({
        "event_id": _STR, "job_id": _STR, "since": _INT, "restore": {"type": ["object", "string", "null"]}}, ["event_id"])),
    "preview_picks": (preview_picks, "What Autopilot would prepare right now and why (prepares nothing).",
                      _s({"n": _INT})),
    "autopilot_settings": (autopilot_settings, "The student's Autopilot settings (schedule, filters, goal).", _s({})),
    "apply_session_next": (apply_session_next, "The next prepared application to submit, best first.", _s({})),
}
WRITES = {"star", "track", "write_cover_letter", "save_event", "update_preferences", "application_kit",
          "queue_application", "fill_application", "draft_followup", "prep_interview", "discard_draft", "log_email",
          "set_dates", "forget_email", "add_application", "research_company", "draft_message", "fix_resume",
          "restore_resume", "run_autopilot", "change_autopilot", "refresh_jobs", "not_for_me", "add_event_source",
          "remove_event_source", "open_link", "remind_me", "cancel_reminder", "check_inbox"}
# which tools each kind of run gets
CHAT_TOOLS = [n for n in TOOLS if n not in ("application_files", "forget_email", "restore_resume")]
# Groq's free tier allows ~8k tokens a minute, and every tool's description rides along on each turn
GROQ_TOOLS = ["search_jobs", "job_details", "star", "track", "my_applications", "write_cover_letter", "find_events",
              "save_event", "update_preferences", "review_resume", "daily_brief", "queue_application", "ready_queue",
              "fill_application", "draft_followup", "prep_interview", "preview_picks", "log_email", "set_dates", "draft_message", "remind_me",
              "research_company", "add_application"]
AUTOPILOT_TOOLS = ["daily_brief", "search_jobs", "job_details", "queue_application", "ready_queue", "my_applications",
                   "find_events", "my_resume", "preview_picks", "autopilot_settings"]


def call(pid: int, name: str, args: dict, source: str = "app", run_id: str | None = None) -> dict:
    """Run one tool and log it. Errors come back as {"error": ...} so the model can recover."""
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    fn, _, schema = TOOLS[name]
    allowed = set(schema["properties"])
    args = {k: v for k, v in (args or {}).items() if k in allowed}
    tok = _ctx.set({"source": source, "run_id": run_id})
    try:
        out = fn(pid, **args)
    except TypeError as e:
        out = {"error": f"bad arguments: {e}"}
    except Exception as e:  # keep the conversation going
        log.warning("agent tool %s failed: %s", name, e)
        out = {"error": str(e)[:200]}
    finally:
        _ctx.reset(tok)
    _log(pid, name, args, out, source, run_id)
    return out


def _slim_card(card: dict | None) -> dict | None:
    """Cards are stored by reference (job / event ids) and filled in again when shown."""
    if not card:
        return None
    if card.get("type") == "jobs":
        return {"type": "jobs", "job_ids": [j["id"] for j in card.get("jobs", [])][:8]}
    if card.get("type") == "events":
        return {"type": "events", "event_ids": [e["id"] for e in card.get("events", [])][:8]}
    return card


def _log(pid: int, name: str, args: dict, out: dict, source: str, run_id: str | None) -> None:
    if source == "card" and name not in WRITES:
        return
    try:
        with db.tx() as c:
            c.execute("INSERT INTO agent_actions (profile_id, ts, source, run_id, tool, args, summary, ok, writes, card,"
                      " undo) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, db.now(), source, run_id, name, db.dumps(args)[:2000], _summary(name, out),
                       int("error" not in out), int(name in WRITES and "error" not in out),
                       db.dumps(_slim_card(out.get("_card"))) if out.get("_card") else None,
                       db.dumps(out["_undo"]) if out.get("_undo") else None))
            c.execute("DELETE FROM agent_actions WHERE ts < ?", (db.now() - 90 * 86400,))
    except Exception as e:  # the log must never break the action itself
        log.warning("couldn't log agent action: %s", e)


def _rehydrate(pid: int, card: dict) -> dict:
    if card.get("type") == "jobs" and "job_ids" in card:
        by = {}
        for j in store.scored_jobs(pid):
            for i in j["alt_ids"]:
                by[i] = j
        return {"type": "jobs", "jobs": [store._slim(by[i]) for i in card["job_ids"] if i in by]}
    if card.get("type") == "events" and "event_ids" in card:
        by = {e["id"]: e for e in events.for_person(pid, 90)}
        return {"type": "events", "events": [by[i] for i in card["event_ids"] if i in by]}
    return card


def run_actions(pid: int, run_id: str) -> list[dict]:
    return db.rows("SELECT * FROM agent_actions WHERE profile_id=? AND run_id=? ORDER BY id", (pid, run_id))


def activity(pid: int, limit: int = 60, writes_only: bool = True) -> list[dict]:
    rows = db.rows("SELECT id, ts, source, run_id, tool, summary, ok, writes, undo, undone FROM agent_actions WHERE"
                   " profile_id=?" + (" AND writes=1" if writes_only else "") + " ORDER BY id DESC LIMIT ?",
                   (pid, limit))
    return [{**{k: r[k] for k in ("id", "ts", "source", "run_id", "tool", "summary")}, "ok": bool(r["ok"]),
             "can_undo": bool(r["undo"]) and not r["undone"], "undone": bool(r["undone"])} for r in rows]


def undo_action(pid: int, action_id: int) -> dict:
    r = db.row("SELECT undo, undone FROM agent_actions WHERE profile_id=? AND id=?", (pid, action_id))
    if not r or not r["undo"]:
        return {"error": "nothing to undo"}
    if r["undone"]:
        return {"ok": True, "already": True}
    u = db.loads(r["undo"], {})
    out = call(pid, u["tool"], u["args"], source="undo")
    if "error" not in out:
        with db.tx() as c:
            c.execute("UPDATE agent_actions SET undone=1 WHERE id=?", (action_id,))
    return out


def tool_specs(fmt: str, names: list[str] | None = None) -> list[dict]:
    pick = [(n, TOOLS[n]) for n in (names or CHAT_TOOLS)]
    if fmt == "claude":
        return [{"name": n, "description": d, "input_schema": s} for n, (_, d, s) in pick]
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": s}} for n, (_, d, s) in pick]


SYSTEM = """You are Co-op Coach's assistant inside a student's internship app. You can ACT using tools: search their
ranked matches, open a posting, star/follow, track applications, write cover letters, prepare application kits, queue
applications for them to submit, fill an application in Chrome, find and save events, change their preferences,
review their resume. Use tools whenever the student asks you to find or do something; don't tell them which button
to press. Rules:
- Never invent postings, facts, numbers or experience. Only use what tools return and what's in their profile.
- You never submit an application. You prepare the kit and the student submits (say so when relevant).
- When results show as cards, don't repeat every detail: give a short takeaway (best pick and why).
- Postings marked eligibility "no" can't be applied to (e.g. US-residents only); steer to better options.
- When they paste an email about an application (or say "I got this email"), call log_email with the whole email
  (plus company/role/status/time if you can read them), then say what changed and the next step. Booking links are
  for them to click; when they tell you the booked time, call set_dates. Never reply to or book on their behalf.
- More you can do: research_company before an interview or coffee chat; draft_message for replies, availability,
  extensions, offers, networking/LinkedIn notes and referral asks (drafts only); remind_me ("remind me Friday to
  follow up"); fix_resume; run or change Autopilot; refresh_jobs; check_inbox; add_application; add_event_source;
  not_for_me; open_link. Times are local ISO: take dates for weekdays from next_14_days in <student>, and repeat
  the date the tool returns when you confirm.
- You can also coach directly: mock interviews (one question at a time, feedback using their real resume), offer
  comparisons, negotiation, and LinkedIn/networking advice. Never claim to have sent or submitted anything.
- Be brief and friendly: 1-4 sentences or a few bullets."""


def _context(pid: int) -> str:
    p = store.get_profile(pid)
    prof, prefs = p["profile"], p["prefs"]
    from . import mail
    return json.dumps({
        "student": prof.get("summary"), "targets": prefs.get("categories") or prof.get("target_categories"),
        "locations": prefs.get("locations"), "terms": prefs.get("terms"), "aim": prefs.get("aim"),
        "work_authorization": prefs.get("citizenship"), "applications": mail.stats(pid),
        "today": time.strftime("%a %b %d %Y"), "now": time.strftime("%Y-%m-%dT%H:%M"),
        # weekday -> date for the next two weeks, so "Monday" or "next Friday" never needs date arithmetic
        "next_14_days": [time.strftime("%a %Y-%m-%d", time.localtime(time.time() + i * 86400)) for i in range(1, 15)]},
        ensure_ascii=False)


def run(pid: int, history: list[dict], max_rounds: int = 5) -> dict:
    """One chat turn: the model may call several tools, then answers. Returns the reply plus what it did."""
    import uuid
    steps, cards, undo = [], [], []
    run_id = uuid.uuid4().hex[:12]

    def execute(name: str, args: dict) -> str:
        res = call(pid, name, args, source="chat", run_id=run_id) if name in CHAT_TOOLS else {"error": f"unknown tool {name}"}
        card = res.pop("_card", None)
        u = res.pop("_undo", None)
        if card:
            cards.append(card)
        if u:
            undo.append(u)
        steps.append({"tool": name, "args": args, "ok": "error" not in res, "summary": _summary(name, res)})
        return json.dumps(res, ensure_ascii=False, default=str)[:1800]

    system = SYSTEM + "\n\n<student>\n" + _context(pid) + "\n</student>"
    turns = [{"role": m["role"], "content": str(m["content"])[:2000]} for m in history[-8:]
             if m.get("role") in ("user", "assistant") and m.get("content")]
    if turns and turns[-1]["role"] == "user":  # a pasted email can be long: keep the whole latest message
        turns[-1]["content"] = str(history[-1]["content"])[:8000]
    by = ai.provider()
    if by in ("claude_code", "codex"):
        try:
            return _run_claude_code(pid, system, turns, by)
        except Exception as e:  # signed out, usage limit...: the configured fallback answers instead
            log.warning("Claude Code chat failed, falling back: %s", e)
            by = ai._fallback()
            if not by:
                raise
    if by == "claude":
        reply = _run_claude(system, turns, execute, max_rounds)
    else:
        reply = _run_groq(system, turns, execute, max_rounds)
    return {"reply": ai._clean(reply), "steps": steps, "cards": cards, "undo": undo, "by": by}


def _run_claude_code(pid: int, system: str, turns: list[dict], engine: str = "claude_code") -> dict:
    """The chat on your own Claude Code or ChatGPT (Codex): same tools, same cards and Undo."""
    from . import claude_code, codex_cli
    convo = "\n\n".join(f"{'Student' if m['role'] == 'user' else 'You (earlier)'}: {m['content']}" for m in turns)
    mod = claude_code if engine == "claude_code" else codex_cli
    out = mod.run_agent(pid, convo + "\n\nReply to the student's last message, using the tools as needed.",
                        system, source="chat", tools=CHAT_TOOLS, timeout=300)
    acts = run_actions(pid, out["run_id"])
    steps = [{"tool": a["tool"], "args": db.loads(a["args"], {}), "ok": bool(a["ok"]), "summary": a["summary"]} for a in acts]
    cards = [_rehydrate(pid, db.loads(a["card"])) for a in acts if a["card"]]
    undo = [db.loads(a["undo"]) for a in acts if a["undo"]]
    return {"reply": ai._clean(out["reply"]), "steps": steps, "cards": cards, "undo": undo, "by": engine}


def _summary(name: str, res: dict) -> str:
    if res.get("error"):
        return f"couldn't: {res['error']}"
    return {"search_jobs": lambda: f"found {res.get('total', 0)} matches",
            "job_details": lambda: f"opened {res.get('company')} · {res.get('title')}",
            "star": lambda: f"starred {res.get('starred')}" if res.get("starred") else f"unstarred {res.get('unstarred')}",
            "track": lambda: f"{res.get('job')} → {res.get('status')}",
            "my_applications": lambda: f"{len(res.get('applications', []))} applications",
            "write_cover_letter": lambda: "wrote a cover letter",
            "application_kit": lambda: f"prepared the application kit ({res.get('questions_answered', 0)} answers)",
            "find_events": lambda: f"found {res.get('total', 0)} events",
            "save_event": lambda: f"event → {res.get('mark')}",
            "update_preferences": lambda: "updated your preferences",
            "review_resume": lambda: f"reviewed your resume: {res.get('verdict')}",
            "daily_brief": lambda: f"checked today: {res.get('new_matches', {}).get('count', 0)} new matches",
            "queue_application": lambda: (f"took {res['removed']} out of Ready to apply" if res.get("removed") else
                                          f"prepared {res.get('job')} → Ready to apply"),
            "ready_queue": lambda: f"{res.get('count', 0)} ready to apply",
            "fill_application": lambda: f"opened and filled {res.get('job')} in Chrome",
            "application_files": lambda: f"got the files for {res.get('company')}",
            "my_resume": lambda: "read your resume",
            "draft_followup": lambda: f"drafted a {'thank-you' if res.get('kind') == 'thankyou' else 'follow-up'} for {res.get('company')}",
            "prep_interview": lambda: f"wrote an interview prep sheet for {res.get('company')}",
            "discard_draft": lambda: "discarded a draft",
            "log_email": lambda: (f"{res.get('application')} → {res.get('status')}" + (
                f" (was {res.get('was')})" if res.get("status_changed") else "")),
            "set_dates": lambda: f"put {res.get('company')} on your calendar",
            "forget_email": lambda: "removed the pasted email",
            "check_inbox": lambda: f"checked your inbox: {len(res.get('new_emails', []))} new recruiting emails",
            "add_application": lambda: f"tracked {res.get('job')} → {res.get('status')}",
            "research_company": lambda: f"researched {res.get('company')}" + (" (searched the web)" if res.get("searched_web") else ""),
            "draft_message": lambda: f"drafted: {res.get('subject')}",
            "fix_resume": lambda: (f"fixed {res.get('fixed')} things on your resume ({res.get('score_before')} → {res.get('score')})"
                                   if res.get("fixed") else "nothing small left to fix on your resume"),
            "restore_resume": lambda: "put your resume back",
            "run_autopilot": lambda: "Autopilot is already running" if res.get("already_running") else "started Autopilot",
            "change_autopilot": lambda: "updated Autopilot: " + ", ".join(f"{k} → {v}" for k, v in (res.get("changed") or {}).items()),
            "refresh_jobs": lambda: "already checking for new postings" if res.get("already_running") else "checking every job source for new postings",
            "not_for_me": lambda: f"hid {res.get('job')}",
            "add_event_source": lambda: f"added {res.get('source')} ({res.get('found')} events)",
            "remove_event_source": lambda: "removed an events source",
            "open_link": lambda: "opened it in your browser",
            "remind_me": lambda: f"reminder set: {res.get('title')} ({res.get('at')})",
            "reminders": lambda: f"{len(res.get('reminders', []))} reminders",
            "cancel_reminder": lambda: "cancelled a reminder",
            "preview_picks": lambda: f"previewed {len(res.get('picks', []))} picks",
            "autopilot_settings": lambda: "read your Autopilot settings",
            "apply_session_next": lambda: "found the next application to submit"}.get(name, lambda: "done")()


def _run_groq(system: str, turns: list[dict], execute, max_rounds: int) -> str:
    from . import groq_client
    from .privacy import Redactor
    red = Redactor()
    msgs = [{"role": "system", "content": red.redact(system)}] + \
        [{"role": m["role"], "content": red.redact(m["content"])} for m in turns]
    specs = tool_specs("openai", GROQ_TOOLS)
    for _ in range(max_rounds):
        data = groq_client._post({"model": groq_client.model(), "messages": msgs, "tools": specs, "tool_choice": "auto",
                                  "reasoning_effort": "low", "max_completion_tokens": 1500, "temperature": 0.3})
        m = data["choices"][0]["message"]
        calls = m.get("tool_calls") or []
        if not calls:
            return red.restore(m.get("content") or "").strip()
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        for tc in calls:
            try:
                args = red.restore(json.loads(tc["function"].get("arguments") or "{}"))
            except ValueError:
                args = {}
            out = execute(tc["function"]["name"], args)
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": red.redact(out)})
    return "I did what I could in a few steps. Ask me to keep going if something's missing."


def _run_claude(system: str, turns: list[dict], execute, max_rounds: int) -> str:
    client = ai._get_client()
    msgs = list(turns)
    for _ in range(max_rounds):
        resp = client.beta.messages.create(model=ai._model(), max_tokens=2000, betas=[ai._FALLBACK_BETA],
                                           fallbacks="default", system=system, tools=tool_specs("claude"),
                                           output_config={"effort": "low"}, messages=msgs)
        if resp.stop_reason != "tool_use":
            return next((b.text for b in resp.content if b.type == "text"), "").strip()
        msgs.append({"role": "assistant", "content": resp.content})
        results = [{"type": "tool_result", "tool_use_id": b.id, "content": execute(b.name, b.input)}
                   for b in resp.content if b.type == "tool_use"]
        msgs.append({"role": "user", "content": results})
    return "I did what I could in a few steps. Ask me to keep going if something's missing."
