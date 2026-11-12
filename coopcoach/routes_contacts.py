"""People: recruiters who wrote to you, your LinkedIn connections and people you added (contacts.py)."""

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from . import contacts, store

router = APIRouter()


def _pid(pid) -> int:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise HTTPException(400, "profile_id is required")
    if not store.get_profile(pid):
        raise HTTPException(404, "no such person")
    return pid


def _ok(out: dict) -> dict:
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


@router.get("/api/recruiters")
def list_recruiters(profile_id: int, q: str = "", industry: str = "", teams: bool = False, starred: bool = False,
                    hidden: bool = False, source: str = "", role: str = "", startups: bool = False,
                    hiring: bool = False, min_funding: int = 0, yc: bool = False, min_team: int = 0):
    return contacts.listing(_pid(profile_id), q, industry, teams, starred, hidden, source, role, startups, hiring,
                            max(0, min_funding), yc, max(0, min_team))


@router.get("/api/export/xlsx")
def export_xlsx(profile_id: int, all: bool = False):
    """Matches, applications, ready to apply and startups in one colour-coded spreadsheet."""
    from datetime import date

    from . import export
    pid = _pid(profile_id)
    data = export.workbook(pid, everything=all)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="co-op-coach-{date.today()}.xlsx"'})


@router.get("/api/recruiters/export")
def export_recruiters(profile_id: int):
    data = contacts.to_csv(_pid(profile_id))
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="people.csv"'})


@router.post("/api/recruiters")
def add_recruiter(body: dict = Body(...)):
    try:
        return _ok(contacts.add(_pid(body.get("profile_id")), body))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/api/recruiters/{cid}")
def update_recruiter(cid: int, body: dict = Body(...)):
    try:
        return _ok(contacts.update(_pid(body.get("profile_id")), cid, body))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/recruiters/{cid}")
def delete_recruiter(cid: int, profile_id: int):
    return _ok(contacts.delete(_pid(profile_id), cid))


@router.get("/api/recruiters/linkedin-export")
def linkedin_export(profile_id: int):
    """LinkedIn's export sitting in Downloads, ready to import (or null)."""
    return {"found": contacts.find_export(_pid(profile_id))}


@router.post("/api/recruiters/linkedin-export")
def linkedin_export_import(body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    if body.get("dismiss"):
        contacts.dismiss_export(pid, str(body.get("key") or ""))
        return {"ok": True}
    return _ok(contacts.import_export(pid, str(body.get("key") or "")))


@router.post("/api/recruiters/import-linkedin")
def import_linkedin(body: dict = Body(...)):
    pid = _pid(body.get("profile_id"))
    text = body.get("csv") or ""
    if not isinstance(text, str) or len(text) > 12_000_000:
        raise HTTPException(400, "send the Connections.csv file's text (up to 12 MB)")
    return _ok(contacts.import_linkedin(pid, text))


@router.post("/api/recruiters/funding")
def check_funding(body: dict = Body(...)):
    """One company now (your AI searches the web, up to a minute), or the founders' companies in the background."""
    pid = _pid(body.get("profile_id"))
    if body.get("founders"):
        import threading
        if (contacts.FUNDING_STATE.get(pid) or {}).get("running"):
            return {"started": False, "running": True}
        threading.Thread(target=contacts.check_founders_funding, args=(pid,), daemon=True).start()
        return {"started": True}
    return _ok(contacts.check_funding(str(body.get("company") or "")))
