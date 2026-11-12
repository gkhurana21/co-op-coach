"""Narrow API for the unpacked extension; page text can never dispatch arbitrary tools."""

import base64
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import agent, apply, auth, db, extension_auth, routes_docs, store
from .tools import jobs

router = APIRouter()


class ProfileBody(BaseModel):
    profile_id: int = Field(ge=1)


class PairBody(BaseModel):
    code: str = Field(min_length=16, max_length=128)


class PageBody(BaseModel):
    url: str = Field(max_length=4096)


class Question(BaseModel):
    id: str = Field(default="", max_length=100)
    label: str = Field(max_length=1000)
    kind: str = Field(default="text", max_length=30)
    options: list[str] = Field(default_factory=list, max_length=200)


class AnswerBody(PageBody):
    questions: list[Question] = Field(max_length=100)


class InboxRow(BaseModel):
    id: str = Field(min_length=1, max_length=300)
    label: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=1000)
    dated: str = Field(default="", max_length=600)


class InboxRowsBody(BaseModel):
    rows: list[InboxRow] = Field(max_length=200)


class InboxMessage(BaseModel):
    id: str = Field(min_length=1, max_length=300)
    dated: str = Field(default="", max_length=600)   # the row's own date text, which decides when it arrived
    subject: str = Field(default="", max_length=1000)
    sender: str = Field(default="", max_length=600)
    when: str = Field(default="", max_length=300)    # the reading pane's date text: only sharpens the time
    body: str = Field(default="", max_length=8000)
    url: str = Field(default="", max_length=2048)


class InboxBody(BaseModel):
    messages: list[InboxMessage] = Field(max_length=50)
    finished: bool = False


def _url_key(value: str) -> tuple:
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        raise HTTPException(400, "Invalid application URL") from None
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise HTTPException(400, "Open a job application webpage first")
    query = parse_qs(u.query)
    # Tracking parameters and the ATS's final /apply or /application tab don't change the posting.
    path = u.path.rstrip("/")
    for suffix in ("/application", "/apply"):
        path = path.removesuffix(suffix)
    return (u.scheme, u.hostname.lower(), port or (443 if u.scheme == "https" else 80), path,
            tuple((key, query[key][0]) for key in ("gh_jid", "jobId", "job_id", "requisitionId") if key in query))


def _job(pid: int, url: str) -> dict | None:
    want = _url_key(url)
    if urlsplit(url).scheme not in ("http", "https"):
        raise HTTPException(400, "Open a job application webpage first")
    # Resolve URLs already in the database only. Never fetch an arbitrary page supplied by a content script.
    for j in store.scored_jobs(pid):
        if _url_key(j["url"]) == want or _url_key(apply.apply_url(j)) == want:
            return j
        for alt in j.get("alt_urls") or []:
            if isinstance(alt, str) and _url_key(alt) == want:
                return j
    return None


def _kit(pid: int, url: str) -> tuple[dict, dict]:
    p = store.get_profile(pid)
    if not p:
        raise HTTPException(404, "No such profile")
    j = _job(pid, url)
    if j and j.get("eligibility") == "no":
        raise HTTPException(409, "This posting is marked ineligible. Review its requirements in the app.")
    existing = jobs.cached_kit(pid, j["id"]) if j else None
    r = routes_docs._resume_for(pid, j["id"] if j else "") or {}
    basics = apply._basics(p, r)
    job = {**(existing or {}).get("job", {}), **({k: j[k] for k in ("id", "company", "title", "regions", "url")} if j else
                                               {"id": "", "company": "", "title": "", "regions": [], "url": url})}
    return {**(existing or {}), "basics": basics, "job": job,
            "questions": (existing or {}).get("questions", [])}, p


@router.post("/api/extension/code")
def pairing_code(request: Request, body: ProfileBody):
    return extension_auth.new_code(body.profile_id, request.cookies.get(auth.COOKIE))


@router.post("/api/extension/disconnect")
def disconnect(body: ProfileBody):
    extension_auth.disconnect(body.profile_id)
    return {"ok": True}


@router.post("/api/extension/pair")
def pair(request: Request, body: PairBody):
    return extension_auth.pair(body.code, extension_auth.origin(request))


@router.post("/api/extension/disconnect-self")
def disconnect_self(request: Request):
    extension_auth.disconnect(extension_auth.require(request))
    return {"ok": True}


@router.post("/api/extension/today")
def today(request: Request):
    pid = extension_auth.require(request)
    # These are read tools. No general tool dispatch is exposed to webpage scripts.
    out = {name: agent.call(pid, name, {}, source="extension") for name in
           ("daily_brief", "my_calendar", "ready_queue", "my_path", "coding_progress", "search_jobs", "coop_status")}
    for value in out.values():
        value.pop("_card", None)
        value.pop("_undo", None)
    out["profile"] = db.row("SELECT id, name FROM profiles WHERE id=?", (pid,))
    return out


@router.post("/api/extension/context")
def context(request: Request, body: PageBody):
    pid = extension_auth.require(request, touch=True)
    kit, _ = _kit(pid, body.url)
    # Dynamic questions go back through the Python rules; only fixed contact answers travel in bulk.
    return {"job": kit["job"], "answers": apply._basic_answers(kit["basics"]),
            "needs_you": [q["label"] for q in kit["questions"] if q.get("needs_you")],
            "prepared": bool(kit.get("files"))}


@router.post("/api/extension/answers")
def answers(request: Request, body: AnswerBody):
    pid = extension_auth.require(request, touch=True)
    kit, p = _kit(pid, body.url)
    ask = apply.asker(kit, p["prefs"])
    out = []
    for q in body.questions:
        options = [o[:1000] for o in q.options]
        # Unknown postings have no verified job country: don't infer work authorization from the default.
        unknown_work = not kit["job"].get("id") and any(w in q.label.lower() for w in
                                                       ("sponsor", "authoriz", "authoris", "eligible to work", "right to work"))
        answer = "" if unknown_work else ask(q.label, q.kind, options)
        index = apply._pick_option(answer, options) if answer and options else None
        out.append({"id": q.id, "answer": answer, "index": index,
                    "needs_you": not answer or bool(options and index is None)})
    return {"answers": out}


@router.post("/api/extension/files")
def files(request: Request, body: PageBody):
    pid = extension_auth.require(request, touch=True)
    kit, _ = _kit(pid, body.url)
    # Rendering known documents only: never start AI or fetch the posting while a form is being filled.
    if not kit.get("files"):
        return {"files": []}
    written = apply._write_files(pid, kit)
    out = []
    for kind, filename in written.items():
        f = Path(filename)
        if f.stat().st_size <= 10 * 1024 * 1024:
            out.append({"kind": kind, "name": f.name, "type": "application/pdf",
                        "data": base64.b64encode(f.read_bytes()).decode("ascii")})
    return {"files": out}


@router.post("/api/extension/submitted")
def submitted(request: Request, body: PageBody):
    pid = extension_auth.require(request, touch=True)
    j = _job(pid, body.url)
    if not j:
        raise HTTPException(404, "This posting is not in your feed. Add it in the tracker first.")
    out = agent.call(pid, "track", {"job_id": j["id"], "status": "applied"}, source="extension")
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return {"ok": True, "company": j["company"], "title": j["title"]}


# --- reading the Outlook tab the student already has open ------------------------------------------------------
# The extension reads only the page the student is looking at (the list and the reading pane, as text) and sends it
# here; no tokens, cookies or session headers are read or sent. The rules here decide what to open and what it means.

TAB_ADDRESS = "Outlook in Chrome"


def _tab_account(pid: int) -> dict:
    acct = db.row("SELECT * FROM email_accounts WHERE profile_id=? AND kind='outlook_tab'", (pid,))
    if acct:
        return acct
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO email_accounts (profile_id, kind, address, created_at) VALUES (?,?,?,?)",
                  (pid, "outlook_tab", TAB_ADDRESS, db.now()))
    return db.row("SELECT * FROM email_accounts WHERE profile_id=? AND kind='outlook_tab'", (pid,))


@router.post("/api/extension/inbox-plan")
def inbox_plan(request: Request, body: InboxRowsBody):
    """Which of the rows on screen to open: new ones that might be about an application, newer than the lookback."""
    from . import config, outlook_web
    pid = extension_auth.require(request, touch=True)
    acct = _tab_account(pid)
    seen = set((db.meta_get(f"owa_seen:{acct['id']}") or "").split())
    floor = int(db.now() - int(config.get("email.lookback_days", 120)) * 86400)
    open_ids, skipped, stop = [], [], False
    for r in body.rows:
        ts = outlook_web._when(r.dated) or outlook_web._when(r.label) or outlook_web._when(r.text)
        if ts and ts < floor:
            stop = True  # newest first: everything below is older still
            break
        if r.id in seen:
            continue
        (open_ids if outlook_web._worth_opening(r.model_dump()) else skipped).append(r.id)
    if skipped:
        db.meta_set(f"owa_seen:{acct['id']}", " ".join(list(seen | set(skipped))[-5000:]))
    return {"open": open_ids, "stop": stop}


@router.post("/api/extension/inbox")
def inbox(request: Request, body: InboxBody):
    """Emails read from the student's Outlook tab: dated by their own row, sorted, and filed like any other inbox."""
    from . import mail, outlook_web
    pid = extension_auth.require(request, touch=True)
    acct = _tab_account(pid)
    msgs, ids = [], []
    for m in body.messages:
        name, addr = outlook_web._sender(m.sender)
        received = outlook_web._received(outlook_web._when(m.dated), outlook_web._when(m.when))
        msgs.append({"message_id": f"owa:{m.id}", "received_at": received, "from_name": name, "from_addr": addr,
                     "subject": m.subject.strip()[:300], "body": m.body,
                     "web_link": m.url if m.url.startswith("https://") else ""})
        ids.append(m.id)
    stats = mail.process_messages(pid, acct["id"], msgs) if msgs else {"job_emails": 0, "status_changes": 0}
    key = f"owa_seen:{acct['id']}"
    db.meta_set(key, " ".join(list(set((db.meta_get(key) or "").split()) | set(ids))[-5000:]))
    if body.finished or msgs:
        with db.tx() as c:
            c.execute("UPDATE email_accounts SET last_sync=?, last_status=? WHERE id=?",
                      (db.now(), f"ok · read from your Outlook tab · {stats.get('job_emails', 0)} job emails", acct["id"]))
    return {"job_emails": stats.get("job_emails", 0), "updates": stats.get("status_changes", 0)}
