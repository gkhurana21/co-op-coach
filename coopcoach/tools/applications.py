"""Tools about the tracker: statuses, pasted recruiter emails, interview dates, prep sheets, follow-ups and
other drafts, company research."""

import hashlib
import logging
import re
import threading
import time

from .. import ai, contacts, db, store
from ..textutil import company_key, when_text
from .common import (
    STATUSES,
    _app_job,
    _app_row,
    _epoch,
    _find_job,
    _new_doc,
    _resume_text,
    _target,
)

log = logging.getLogger(__name__)


def track(pid: int, job_id: str, status: str) -> dict:
    from .. import server
    j = _find_job(pid, job_id)
    if not j:
        return {"error": "no such posting"}
    status = status.lower().strip()
    status = {"applied to": "applied", "interview": "interviewing", "assessment": "oa", "remove": "", "none": ""}.get(status, status)
    if status and status not in STATUSES:
        return {"error": f"status must be one of {STATUSES} or 'remove'"}
    before = j.get("app_status") or ""
    server.upsert_application({"profile_id": pid, "job_id": j.get("app_id") or j["id"], "status": status or None})
    store.refresh_scores()
    if status == "applied":
        with db.tx() as c:
            c.execute("UPDATE ready_queue SET status='applied', updated_at=? WHERE profile_id=? AND job_id=?",
                      (db.now(), pid, j["id"]))
    return {"ok": True, "job": f"{j['company']} · {j['title']}", "status": status or "removed",
            "_undo": {"tool": "track", "args": {"job_id": j["id"], "status": before or "remove"}}}


def my_applications(pid: int, status: str = "") -> dict:
    rows = db.rows("SELECT a.job_id, a.status, COALESCE(j.company, a.company) AS company, COALESCE(j.title, a.title)"
                   " AS title, a.updated_at FROM applications a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.profile_id=?"
                   + (" AND a.status=?" if status else "") + " ORDER BY a.updated_at DESC LIMIT 25",
                   (pid, status) if status else (pid,))
    from .. import mail
    return {"stats": mail.stats(pid), "applications": [{k: r[k] for k in ("job_id", "company", "title", "status")}
                                                       for r in rows]}


def draft_followup(pid: int, job_id: str, kind: str = "followup") -> dict:
    """Draft (never send) a follow-up on a quiet application, or a thank-you after an interview."""
    from .. import jobdetail, routes_docs
    kind = "thankyou" if kind in ("thankyou", "thank_you", "thanks") else "followup"
    a = _app_job(pid, job_id)
    if not a:
        return {"error": "no such application"}
    days = int((time.time() - (a["since"] or time.time())) / 86400)
    desc = jobdetail.ensure_description(a["description_id"]) if a["description_id"] else ""
    name = (store.get_profile(pid)["profile"].get("name") or "").split(" ")[0]
    by = "rules"
    if ai.available():
        try:
            out = ai.draft_followup(kind, _resume_text(pid), a, desc or "", days)
            subject, body, by = out["subject"], out["body"], ai.provider()
        except Exception as e:
            log.warning("follow-up draft failed, using a template: %s", e)
    if by == "rules":
        if kind == "thankyou":
            subject = f"Thank you: {a['title']} interview"
            body = (f"Hi [Interviewer name],\n\nThank you for taking the time to talk with me about the {a['title']} role "
                    f"at {a['company']}. I enjoyed learning more about the team and the work, and I'm even more excited "
                    f"about the opportunity.\n\nPlease let me know if there's anything else I can share.\n\nBest,\n{name}")
        else:
            subject = f"Following up: {a['title']} application"
            body = (f"Hi [Recruiter name],\n\nI applied for the {a['title']} role at {a['company']} about {days} days ago "
                    f"and wanted to follow up. I'm still very interested and would be glad to share anything else that "
                    f"would help.\n\nThank you for your time,\n{name}")
    doc_id = routes_docs._save_doc(pid, kind, f"Subject: {subject}\n\n{body}", f"{a['company']} · {a['title']}",
                                   job_id=a["job_id"], by=by)
    return {"ok": True, "doc_id": doc_id, "kind": kind, "subject": subject, "preview": body[:300], "company": a["company"],
            "_undo": {"tool": "discard_draft", "args": {"doc_id": doc_id}}}


def prep_interview(pid: int, job_id: str) -> dict:
    """An interview / online-assessment prep sheet built from their real resume (saved to Documents)."""
    from .. import jobdetail, routes_docs
    a = _app_job(pid, job_id)
    if not a:
        return {"error": "no such application"}
    stage = "oa" if a["status"] == "oa" else "interview"
    desc = jobdetail.ensure_description(a["description_id"]) if a["description_id"] else ""
    by, sheet = "rules", None
    if ai.available():
        try:
            sheet, by = ai.interview_prep(stage, _resume_text(pid), a, desc or "", research=True), ai.provider()
        except Exception as e:
            log.warning("prep sheet failed, using a template: %s", e)
    if not sheet:
        sheet = {"role_summary": f"{a['title']} at {a['company']}.", "format": "Check the invite for the format.",
                 "likely_questions": [{"question": q, "why": ""} for q in (
                     "Tell me about yourself.", f"Why {a['company']}?", "Tell me about a project you're proud of.",
                     "Tell me about a time you worked through a hard bug or problem.")],
                 "stories": [], "questions_to_ask": ["What does a great intern accomplish on this team?",
                                                     "How is the team's work measured?"],
                 "plan": ["Day 1: re-read the posting and your resume", "Day 2: practise 2 stories out loud",
                          "Day 3: research the team and prepare questions"]}
    lines = [f"# {a['company']} · {a['title']} ({'online assessment' if stage == 'oa' else 'interview'})", "",
             sheet["role_summary"], "", f"**Format:** {sheet['format']}", "", "## Likely questions"]
    lines += [f"- {q['question']}" + (f" ({q['why']})" if q.get("why") else "") for q in sheet["likely_questions"]]
    if sheet["stories"]:
        lines += ["", "## Your stories (from your resume)"]
        for st in sheet["stories"]:
            lines += [f"### {st['title']}", f"- Situation: {st['situation']}", f"- Action: {st['action']}",
                      f"- Result: {st['result']}", f"- Use for: {st['use_for']}"]
    lines += ["", "## Questions to ask"] + [f"- {q}" for q in sheet["questions_to_ask"]]
    lines += ["", "## Plan"] + [f"- {x}" for x in sheet["plan"]]
    doc_id = routes_docs._save_doc(pid, "prep", "\n".join(lines), f"{a['company']} · {a['title']}", job_id=a["job_id"], by=by)
    return {"ok": True, "doc_id": doc_id, "company": a["company"], "stage": stage,
            "likely_questions": [q["question"] for q in sheet["likely_questions"]][:5],
            "_undo": {"tool": "discard_draft", "args": {"doc_id": doc_id}}}


def _contacts_from_history(pid: int) -> None:
    """Recruiters from emails read before that tab existed, before this email adds its own (so none counts twice)."""
    try:
        contacts.backfill(pid)
    except Exception as e:
        log.warning("couldn't add recruiters from earlier emails: %s", e)


def log_email(pid: int, text: str, company: str = "", role: str = "", status: str = "", when: str = "",
              deadline: str = "") -> dict:
    """A recruiting email the student pasted: put it on the right application (adding one if they never tracked it),
    move the status forward, keep its booking / assessment / call links, put a stated interview time or deadline on
    their calendar, and line up the next steps (prep sheet now, thank-you after the interview)."""
    from .. import autopilot, mail
    text = (text or "").strip()[:12000]
    if len(text) < 40:
        return {"error": "paste the whole email (at least a few lines)"}
    _contacts_from_history(pid)
    r = mail.read_pasted(text)
    from .. import meetings
    kind = meetings.kind_of(r["subject"], text, r.get("status"))
    if kind in meetings.NETWORKING and not status:  # a coffee chat or an info session: the calendar, not the tracker
        return _log_meeting(pid, text, r, kind)
    company = (company or r["company"] or "").strip()
    role = (role or r["role"] or "").strip()
    status = {"interview": "interviewing", "assessment": "oa", "online assessment": "oa", "rejection": "rejected",
              "received": "applied", "confirmation": "applied"}.get((status or "").lower().strip(), (status or "").lower().strip())
    status = status if status in STATUSES else (r["status"] or "applied")
    if not company:
        return {"error": "couldn't tell which company this email is from: ask the student, then call log_email again "
                         "with company=", "status": status, "role": role}
    eid = mail._event_id(pid, "pasted:" + hashlib.sha1(text.encode()).hexdigest()[:16])
    before = {a["job_id"]: a for a in db.rows("SELECT * FROM applications WHERE profile_id=?", (pid,))}
    job_id = mail.match_by_req(pid, r["req_ids"], company) or mail.match_application(pid, company, role)
    if not job_id:
        return {"error": "couldn't match or add this application"}
    prev = before.get(job_id)
    changed = mail.apply_status(pid, job_id, status, db.now(), r["subject"])
    mail.remember_req(pid, job_id, r["req_ids"])
    links, now = r["links"], db.now()
    at = _epoch(when) or r["when"]["at"]
    due = _epoch(deadline) or r["when"]["due"]
    a = _app_row(pid, job_id) or {}
    notes = a.get("notes") or ""
    add = [f"{label}: {links[k]}" for k, label in (("book", "Book a time"), ("assessment", "Assessment"),
                                                  ("video", "Call link")) if links.get(k) and links[k] not in notes]
    sets = {"notes": "\n".join(x for x in [notes.strip(), *add] if x) or None}
    if at and at > now - 3600 and status in ("interviewing", "oa", "applied"):
        sets["interview_at"] = at
    if due and due > now and (status == "oa" or links.get("assessment")):
        sets["oa_due_at"] = due
    with db.tx() as c:
        c.execute("UPDATE applications SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE profile_id=? AND job_id=?",
                  (*sets.values(), pid, job_id))
        dup = c.execute("SELECT 1 FROM email_events WHERE id=?", (eid,)).fetchone()
        c.execute("INSERT OR IGNORE INTO email_events (id, profile_id, account_id, message_id, received_at, from_name,"
                  " from_addr, subject, snippet, web_link, status, company, role, job_id, classified_by, changed_status,"
                  " created_at, req_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (eid, pid, None, f"pasted:{eid}", now, r["from_name"] or company, r["from_addr"], r["subject"],
                   re.sub(r"\s+", " ", text)[:400], links.get("book") or links.get("assessment") or None, status,
                   company, role, job_id, "pasted", int(changed), now, ", ".join(r["req_ids"][:3]) or None))
    try:  # its interview time, assessment deadline or booking link, on the calendar
        items = meetings.extract(r["subject"], text, "", now, status)
        if at and at > now - 3600 and not any(i.get("start_ts") for i in items) and status in ("interviewing", "oa", "applied"):
            items = [{**i, "start_ts": at, "end_ts": at + 3600, "status": "confirmed"} for i in items if i["kind"] != "oa"] \
                or [{"kind": "interview", "start_ts": at, "end_ts": at + 3600, "status": "confirmed"}]
        if items:
            meetings.save(pid, items, job_id, company, f"{r['from_name']} <{r['from_addr']}>" if r["from_addr"] else "",
                          eid, r["subject"], "", "pasted")
    except Exception as e:
        log.warning("couldn't put a pasted email on the calendar: %s", e)
    if not dup:  # who wrote it goes to Recruiters (once per email)
        try:
            contacts.record(pid, from_name=r["from_name"], from_addr=r["from_addr"], company=company, body=text,
                            subject=r["subject"], status=status, job_id=job_id, when=now, source="pasted")
        except Exception as e:
            log.warning("couldn't save the sender of a pasted email as a contact: %s", e)
    store.refresh_scores()
    a = _app_row(pid, job_id) or {}
    j = _find_job(pid, job_id)
    name = f"{(j or a).get('company') or company} · {(j or a).get('title') or role or 'role from email'}"
    # a prep sheet takes a minute with your own AI: it's written in the background and lands in Documents/Autopilot
    prep = status in ("oa", "interviewing") and autopilot.settings(pid).get("prep", True) and not db.row(
        "SELECT 1 FROM documents WHERE profile_id=? AND job_id=? AND kind='prep'", (pid, job_id))
    from .. import agent  # SHORT_LIVED is set on agent by the MCP helper
    if prep and not agent.SHORT_LIVED:
        start_prep(pid, job_id)  # in the MCP helper (Claude Code / Codex chats) the app's 5-minute tick writes it
    nxt = []
    if status == "interviewing":
        if links.get("book") and not a.get("interview_at"):
            nxt.append("Book a time with the link, then tell me the day and time: I'll put it on your calendar "
                       "with a reminder.")
        elif a.get("interview_at"):
            nxt.append(f"Interview {when_text(a['interview_at'])} is on your "
                       "calendar (reminder an hour before).")
        nxt.append("A thank-you note gets drafted for after the interview (never sent).")
    elif status == "oa":
        nxt.append("Do the assessment before the deadline" + (f" ({time.strftime('%a %b %d', time.localtime(a['oa_due_at']))}, "
                   "on your calendar)" if a.get("oa_due_at") else "") + ".")
    elif status == "rejected":
        nxt.append("Marked rejected. Ask me for similar open roles to keep momentum.")
    elif status == "offer":
        nxt.append("Congrats! Note the reply deadline; I can help compare offers.")
    if prep:
        nxt.append("Your prep sheet is being written (Autopilot → This week, " +
                   ("within about 5 minutes)." if agent.SHORT_LIVED else "in about a minute)."))
    return {"ok": True, "job_id": job_id, "application": name, "status": a.get("status") or status,
            "was": (prev or {}).get("status") or "not tracked", "status_changed": changed, "already_logged": bool(dup),
            "links": {k: v for k, v in links.items() if v}, "interview_at": a.get("interview_at"),
            "oa_due_at": a.get("oa_due_at"), "next_steps": nxt,
            "_card": {"type": "email", "job_id": job_id, "title": name, "status": a.get("status") or status,
                      "was": (prev or {}).get("status") or "", "book": links.get("book", ""),
                      "assessment": links.get("assessment", ""), "video": links.get("video", ""),
                      "interview_at": a.get("interview_at"), "oa_due_at": a.get("oa_due_at"), "prep": bool(prep)},
            "_undo": {"tool": "forget_email", "args": {
                "event_id": "" if dup else eid, "job_id": job_id, "since": now, "restore": None if dup else {
                    k: (prev or {}).get(k) for k in ("status", "notes", "interview_at", "oa_due_at", "updated_at")}
                if prev else "remove"}}}


_prepping: set = set()


_prep_lock = threading.Lock()


def start_prep(pid: int, job_id: str) -> bool:
    """Write a prep sheet in the background (a minute or two with your own AI), once per application at a time."""
    with _prep_lock:
        if (pid, job_id) in _prepping:
            return False
        _prepping.add((pid, job_id))

    def work():
        from ..agent import call  # logged like any tool call (agent imports this module)
        try:
            for attempt in range(2):  # one retry: a busy database or a slow AI shouldn't cost the student a prep sheet
                if db.row("SELECT 1 FROM documents WHERE profile_id=? AND job_id=? AND kind='prep'", (pid, job_id)):
                    return
                out = call(pid, "prep_interview", {"job_id": job_id}, source="autopilot")
                if "error" not in out or out["error"] == "no such application":  # done, or undone meanwhile
                    return
                log.warning("prep sheet for %s failed: %s%s", job_id, out["error"], " (retrying)" if not attempt else "")
                time.sleep(2)
        finally:
            with _prep_lock:
                _prepping.discard((pid, job_id))

    threading.Thread(target=work, daemon=True, name=f"prep-{job_id}").start()
    return True


def prep_pending(pid: int) -> int:
    """Applications that just reached an assessment or interview (from a pasted email, the inbox or the tracker) and
    have no prep sheet yet: start one. Called every few minutes by the app."""
    n = 0
    for r in db.rows("SELECT job_id FROM applications WHERE profile_id=? AND status IN ('oa', 'interviewing') AND"
                     " updated_at > ? AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.profile_id=applications.profile_id"
                     " AND d.job_id=applications.job_id AND d.kind='prep') LIMIT 2", (pid, db.now() - 3 * 86400)):
        n += start_prep(pid, r["job_id"])
    return n


def _log_meeting(pid: int, text: str, r: dict, kind: str) -> dict:
    """A pasted coffee chat, info session or career fair: it goes on the calendar (and its sender on People), and no
    application is made for it."""
    from .. import mail, meetings
    eid = mail._event_id(pid, "pasted:" + hashlib.sha1(text.encode()).hexdigest()[:16])
    items = meetings.extract(r["subject"], text, "", db.now())
    who = f"{r['from_name']} <{r['from_addr']}>" if r["from_addr"] else (r["from_name"] or "")
    company = mail._sender_company({"body": text, "from_addr": r["from_addr"]}) or r.get("company") or ""
    ids = meetings.save(pid, items, None, company, who, eid, r["subject"], "", "pasted") if items else []
    if r["from_addr"] or r["from_name"]:
        try:
            contacts.record(pid, from_name=r["from_name"], from_addr=r["from_addr"], company=company, body=text,
                            subject=r["subject"], status="", job_id=None, when=db.now(), source="pasted")
        except Exception as e:
            log.warning("couldn't save the sender of a pasted email as a contact: %s", e)
    it = items[0] if items else {}
    label = meetings.KINDS[kind]
    when = when_text(it["start_ts"]) if it.get("start_ts") else ""
    nxt = ([f"{label} {when} is on your calendar (reminder {meetings.ALARM[kind]} minutes before)."] if when else
           ["Book a time with the link; tell me the day and time and I'll put it on your calendar."] if it.get("book_url")
           else ["No time in it yet: tell me when, and I'll put it on your calendar."])
    return {"ok": True, "meeting": label, "with": who or company, "company": company, "start_ts": it.get("start_ts"),
            "book": it.get("book_url") or "", "join": it.get("join_url") or "", "next_steps": nxt, "calendar_items": ids,
            "_undo": {"tool": "forget_email", "args": {"event_id": eid}}}


def forget_email(pid: int, event_id: str, job_id: str = "", since: int = 0, restore=None) -> dict:
    """Undo a pasted email: remove it from the inbox list, take off the calendar what it put there, and put the
    application back the way it was."""
    from .. import meetings
    meetings.forget(pid, event_id)
    with db.tx() as c:
        c.execute("DELETE FROM email_events WHERE profile_id=? AND id=?", (pid, event_id))
        if job_id and restore == "remove":
            c.execute("DELETE FROM applications WHERE profile_id=? AND job_id=?", (pid, job_id))
        elif job_id and isinstance(restore, dict) and restore.get("status"):
            c.execute("UPDATE applications SET status=?, notes=?, interview_at=?, oa_due_at=?, updated_at=? WHERE"
                      " profile_id=? AND job_id=?", (restore["status"], restore.get("notes"), restore.get("interview_at"),
                                                     restore.get("oa_due_at"), restore.get("updated_at") or db.now(),
                                                     pid, job_id))
        if job_id and since:
            c.execute("DELETE FROM documents WHERE profile_id=? AND job_id=? AND kind='prep' AND updated_at >= ?",
                      (pid, job_id, since))
    store.refresh_scores()
    return {"ok": True}


def set_dates(pid: int, job_id: str, interview_at: str | None = None, oa_due_at: str | None = None,
              clear: bool = False) -> dict:
    """Put an interview time or an assessment deadline on an application (and so on their calendar feed).
    clear=True lets an empty value remove a date (Undo uses it)."""
    j = _find_job(pid, job_id)
    a = _app_row(pid, job_id) or (_app_row(pid, j.get("app_id") or j["id"]) if j else None)
    if not a:
        return {"error": "that application isn't tracked yet (track it or paste its email first)"}
    vals = {k: _epoch(v) for k, v in (("interview_at", interview_at), ("oa_due_at", oa_due_at)) if v is not None}
    vals = {k: v for k, v in vals.items() if v or clear}
    if not vals:
        return {"error": "give interview_at or oa_due_at as a local time like 2026-10-14T14:00"}
    with db.tx() as c:
        c.execute("UPDATE applications SET " + ", ".join(f"{k}=?" for k in vals) + " WHERE profile_id=? AND job_id=?",
                  (*vals.values(), pid, a["job_id"]))
    when = {k: when_text(v) for k, v in vals.items() if v}
    return {"ok": True, "job_id": a["job_id"], "company": a.get("company") or (j or {}).get("company"), "set": when,
            "_undo": {"tool": "set_dates", "args": {"job_id": a["job_id"], "clear": True, **{
                k: a.get(k) or "" for k in vals}}}}


def check_inbox(pid: int) -> dict:
    """Scan their connected inbox now and report recruiting emails it found."""
    from .. import mail
    if not db.row("SELECT 1 FROM email_accounts WHERE profile_id=? AND enabled=1", (pid,)):
        return {"error": "no inbox connected (Settings → Inbox). They can also paste an email here and you'll track it."}
    since = db.now()
    res = mail.sync_all(pid)
    rows = db.rows("SELECT subject, status, company, changed_status FROM email_events WHERE profile_id=? AND"
                   " created_at >= ? ORDER BY received_at DESC LIMIT 10", (pid, since))
    return {"ok": True, "inboxes": {k: v.get("error") or "checked" for k, v in res.items()}, "new_emails": rows,
            "status_changes": sum(1 for r in rows if r["changed_status"])}


def add_application(pid: int, company: str, title: str = "", url: str = "", status: str = "applied",
                    req_id: str = "") -> dict:
    """Track an application they made somewhere Co-op Coach doesn't list (or by hand)."""
    from .. import server
    status = status if status in STATUSES else "applied"
    if not (company or "").strip():
        return {"error": "company is required"}
    from .. import mail

    hit = None  # a posting Co-op Coach lists comes first, so the application keeps its details
    ck = company_key(company)
    if title:
        cands = db.rows("SELECT id, title FROM jobs WHERE company_key=? AND active=1", (ck,))
        best = max(cands, key=lambda j: mail._rank(title, j["title"]), default=None)
        if best and mail._sim(title, best["title"]) >= 0.5:
            hit = best["id"]
    if hit:
        out = track(pid, hit, status)
        return {**out, "matched_posting": True}
    before = {a["job_id"]: a for a in db.rows("SELECT * FROM applications WHERE profile_id=?", (pid,))}
    server.add_manual_application({"profile_id": pid, "company": company, "title": title, "url": url,
                                   "req_id": req_id, "status": status})
    new = next((a for a in db.rows("SELECT * FROM applications WHERE profile_id=? ORDER BY updated_at DESC LIMIT 3",
                                   (pid,)) if a["job_id"].startswith("ext:") and a["company"] == company.strip()), None)
    if not new:
        return {"error": "couldn't add it"}
    prev = before.get(new["job_id"])
    store.refresh_scores()
    return {"ok": True, "job": f"{company} · {title or 'Role'}", "job_id": new["job_id"], "status": status,
            "_undo": {"tool": "forget_email", "args": {"event_id": "", "job_id": new["job_id"], "restore": {
                k: prev.get(k) for k in ("status", "notes", "interview_at", "oa_due_at", "updated_at")} if prev else "remove"}}}


def research_company(pid: int, company: str = "", job_id: str = "") -> dict:
    """A company brief (what they do, recent news, intern interviews, talking points from their resume). Saved to
    Documents. Your own Claude Code searches the web for it."""
    from .. import jobdetail
    t = _target(pid, job_id, company)
    if not t["company"]:
        return {"error": "which company?"}
    if not ai.available():
        return {"error": "company research needs an AI engine (Settings → AI & assistants)"}
    desc = jobdetail.ensure_description(t["description_id"]) if t["description_id"] else ""
    brief, web = ai.research_company(t["company"], _resume_text(pid), t if t["title"] else None, desc or "")
    sec = lambda h, xs: ["", f"## {h}"] + [f"- {x}" for x in xs] if xs else []  # noqa: E731
    lines = [f"# {t['company']}" + (f" · {t['title']}" if t["title"] else ""), "", brief.get("what_they_do") or ""]
    lines += sec("Products", brief.get("products"))
    lines += sec("Recent news", [f"{n['headline']} ({n['when']}, {n['source']})".replace(" (, )", "")
                                 for n in brief.get("recent_news") or []])
    lines += sec("Tech", brief.get("tech_stack"))
    for h, k in (("Intern program", "intern_program"), ("Interview process", "interview_process"), ("Culture", "culture")):
        if brief.get(k):
            lines += ["", f"## {h}", brief[k]]
    lines += sec("Talking points (from your resume)", brief.get("talking_points"))
    lines += sec("Questions to ask", brief.get("questions_to_ask"))
    lines += sec("Sources", brief.get("sources"))
    if not web:
        lines += ["", "_Written without a web search: check anything time-sensitive._"]
    doc_id = _new_doc(pid, "research", "\n".join(lines), f"{t['company']} research", t["job_id"], ai.provider())
    return {"ok": True, "doc_id": doc_id, "company": t["company"], "searched_web": web,
            **{k: brief.get(k) for k in ("what_they_do", "recent_news", "interview_process", "talking_points")},
            "_card": {"type": "doc", "doc_id": doc_id, "title": f"{t['company']} research"},
            "_undo": {"tool": "discard_draft", "args": {"doc_id": doc_id}}}


def draft_message(pid: int, kind: str = "other", job_id: str = "", company: str = "", person: str = "",
                  notes: str = "") -> dict:
    """Draft (never send) a job-search message: reply, availability, extension, accept/decline offer, withdraw,
    networking / LinkedIn note, referral ask, recruiter intro, thank-you. Saved to Documents."""
    from .. import jobdetail
    kind = kind if kind in ai.MESSAGE_KINDS else "other"
    t = _target(pid, job_id, company)
    if not t["company"] and kind != "other":
        return {"error": "which company (or job_id) is it for?"}
    desc = jobdetail.ensure_description(t["description_id"]) if t["description_id"] else ""
    first = (store.get_profile(pid)["profile"].get("name") or "").split(" ")[0]
    by = "rules"
    if ai.available():
        try:
            out = ai.draft_message(kind, notes, _resume_text(pid), {**t, "person": person}, desc or "")
            subject, body, by = out["subject"], out["body"], ai.provider()
        except Exception as e:
            log.warning("message draft failed, using a template: %s", e)
    if by == "rules":
        role = t["title"] or "the role"
        subject = {"extension": f"Request for a little more time: {role}", "accept_offer": f"Accepting the {role} offer",
                   "decline_offer": f"{role} offer", "withdraw": f"Withdrawing my application: {role}"}.get(
            kind, f"{t['company'] or 'Hello'}: {role}")
        body = (f"Hi {person or '[Name]'},\n\n[{ai.MESSAGE_KINDS[kind]}{': ' + notes if notes else ''}]\n\n"
                f"Thank you,\n{first}")
    doc_id = _new_doc(pid, "message", f"Subject: {subject}\n\n{body}",
                      f"{kind.replace('_', ' ').title()}: {t['company'] or person or 'message'}", t["job_id"], by)
    return {"ok": True, "doc_id": doc_id, "kind": kind, "subject": subject, "body": body,
            "_card": {"type": "doc", "doc_id": doc_id, "title": f"Draft: {subject}"},
            "_undo": {"tool": "discard_draft", "args": {"doc_id": doc_id}}}


def discard_draft(pid: int, doc_id: int) -> dict:
    with db.tx() as c:
        n = c.execute("DELETE FROM documents WHERE profile_id=? AND id=? AND kind IN ('followup', 'thankyou', 'prep',"
                      " 'research', 'message')", (pid, doc_id)).rowcount
    return {"ok": bool(n), "removed": n}


def recruiters(pid: int, query: str = "", company: str = "", role: str = "") -> dict:
    """People they know: recruiters who wrote about applications (inbox and pasted emails), their LinkedIn
    connections and people they added: name, title, company, email, phone, LinkedIn, last contact. With a company,
    also a link to LinkedIn's own search for alumni of their school there (they open it, signed in as themselves)."""
    people = contacts.search(pid, query, company, limit=40 if role else 12)
    if role:  # founder | recruiter | leader | engineer | product
        people = [p for p in people if contacts.role_of(p.get("title") or "", p.get("kind") or "person") == role][:12]
    sig = contacts.signals(pid, {company_key(p["company"]): p["company"] for p in people if p.get("company")})
    for p in people:
        g = sig.get(company_key(p.get("company") or ""))
        if g:
            f = g["funding"]
            p["company_facts"] = {"yc_batch": g["yc"] or None, "team_size": g["team"], "internships_open": g["open"],
                                  "raised_usd": f["total_usd"] if f and f["found"] else None,
                                  "last_round": (f or {}).get("last_round")}
        p["last_contact"] = when_text(p.pop("last_seen"), with_time=False) if p.get("last_seen") else None
    out = {"count": len(people), "people": people,
           "note": "" if people else "No one yet: connect an inbox (Applications), paste a recruiter's email, or "
                                     "import LinkedIn connections (People tab)."}
    if company:
        out["find_alumni"] = contacts.alumni_search(pid, company)
    return out
