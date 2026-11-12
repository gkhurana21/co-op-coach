"""Everything in one spreadsheet (.xlsx): your matches, applications, what's ready to apply and the startup feed,
colour-coded (match score, who can apply, kind of company, status), with filters on every column for sorting, frozen
headers, real dates and links. Opens in Excel, Numbers or Google Sheets."""

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import companies, db, startups, store

HEAD_FILL = PatternFill("solid", fgColor="1F1D3A")
HEAD_FONT = Font(bold=True, color="FFFFFF")
LINK_FONT = Font(color="4F46E5", underline="single")
THIN = Border(bottom=Side(style="thin", color="D9D9E3"))
WRAP = Alignment(wrap_text=True, vertical="top")

ELIG = {"fit": "Open to you", "ok": "Probably open", "check": "Check who can apply", "no": "Not open to you"}
ELIG_COLOR = {"Open to you": "C6EFCE", "Probably open": "E2F0D9", "Check who can apply": "FFEB9C",
              "Not open to you": "FFC7CE"}
STATUS = {"saved": "Saved", "applied": "Applied", "oa": "Assessment", "interviewing": "Interviewing", "offer": "Offer",
          "rejected": "Rejected", "ready": "Ready to apply", "opened": "Opened", "skipped": "Skipped"}
STATUS_COLOR = {"Saved": "DDEBF7", "Applied": "BDD7EE", "Assessment": "FFE699", "Interviewing": "F8CBAD",
                "Offer": "A9D08E", "Rejected": "D9D9D9", "Ready to apply": "E4DFEC", "Opened": "FFF2CC",
                "Skipped": "EDEDED"}
TIER_COLOR = {"YC Startup": "FCE4D6", "Startup": "EDE7F6", "Big Tech": "DDEBF7", "Quant & Trading": "D9F2EE",
              "Banks & Corporate": "FBE5D6", "Other": "F2F2F2"}
PACE = {"fast": "🔥 Hiring fast", "hiring": "Hiring", "quiet": "Quiet"}


def _date(ts):
    return datetime.fromtimestamp(ts) if ts else None


def _sheet(wb: Workbook, title: str, columns: list[tuple[str, int]], rows: list[list], first: bool = False,
           freeze_cols: int = 0):
    """A table: bold header, widths, frozen header (and first columns), a filter on every column."""
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append([c for c, _ in columns])
    for i, (_, width) in enumerate(columns, 1):
        cell = ws.cell(row=1, column=i)
        cell.fill, cell.font, cell.alignment = HEAD_FILL, HEAD_FONT, Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 28
    for r in rows:
        ws.append(r)
    ws.freeze_panes = ws.cell(row=2, column=freeze_cols + 1)
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
    return ws


def _colour_by_text(ws, col: int, colours: dict[str, str], n: int) -> None:
    """Fill a column's cells by their text (a rule per value: cheap however many rows)."""
    if n < 1:  # an empty sheet: no cells to colour, and "A2:A1" isn't a range
        return
    letter = get_column_letter(col)
    for text, hexcol in colours.items():
        ws.conditional_formatting.add(f"{letter}2:{letter}{n + 1}", FormulaRule(
            formula=[f'{letter}2="{text}"'], fill=PatternFill("solid", fgColor=hexcol, bgColor=hexcol)))


def _score_scale(ws, col: int, n: int) -> None:
    if n < 1:
        return
    letter = get_column_letter(col)
    ws.conditional_formatting.add(f"{letter}2:{letter}{n + 1}", ColorScaleRule(
        start_type="num", start_value=40, start_color="F8696B", mid_type="num", mid_value=65, mid_color="FFEB84",
        end_type="num", end_value=90, end_color="63BE7B"))


def _links(ws, col: int, urls: list[str]) -> None:
    for i, url in enumerate(urls, 2):
        if url and url.startswith("http"):
            c = ws.cell(row=i, column=col)
            c.value, c.hyperlink, c.font = "Open", url, LINK_FONT


def _finish(ws, n: int, dates: tuple[int, ...] = (), wrap: tuple[int, ...] = ()) -> None:
    """Wrap the long-text columns and show dates as dates (only the cells that need it: styling is the slow part)."""
    for col in (*wrap, *dates):
        for (c,) in ws.iter_rows(min_row=2, max_row=n + 1, min_col=col, max_col=col):
            if col in wrap:
                c.alignment = WRAP
            else:
                c.number_format = "yyyy-mm-dd"


def workbook(pid: int, everything: bool = False) -> bytes:
    """The spreadsheet as bytes. `everything` keeps postings they can't apply to and weak matches too."""
    prof = store.get_profile(pid)
    jobs = store.scored_jobs(pid)
    if not everything:
        jobs = [j for j in jobs if j["eligibility"] != "no" and j["score"] >= 40]
    jobs.sort(key=lambda j: (-j["score"], -(j.get("posted_at") or j.get("first_seen") or 0)))
    wb = Workbook()

    # --- Summary -----------------------------------------------------------------------------------------------
    apps = db.rows("SELECT a.*, COALESCE(j.company, a.company) AS co, COALESCE(j.title, a.title) AS ti,"
                   " COALESCE(j.url, a.url) AS link FROM applications a LEFT JOIN jobs j ON j.id=a.job_id"
                   " WHERE a.profile_id=? ORDER BY a.updated_at DESC", (pid,))
    queue = db.rows("SELECT q.*, j.company, j.title, j.url FROM ready_queue q LEFT JOIN jobs j ON j.id=q.job_id"
                    " WHERE q.profile_id=? ORDER BY q.status IN ('ready', 'opened') DESC, q.created_at DESC", (pid,))
    counts = {s: sum(1 for a in apps if a["status"] == s) for s in STATUS}
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = f"Co-op Coach · {prof['name'] if prof else ''}"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A2"] = f"Exported {datetime.now():%A %B %d, %Y at %H:%M}"
    ws["A2"].font = Font(color="808080")
    lines = [("Open matches in this file", len(jobs)), ("Strong matches (75+)", sum(1 for j in jobs if j["score"] >= 75)),
             ("Open to you", sum(1 for j in jobs if j["eligibility"] == "fit")),
             ("Applications tracked", len(apps))] + [(STATUS[s], counts[s]) for s in
                                                       ("applied", "oa", "interviewing", "offer", "rejected") if counts[s]] + [
        ("Ready to apply", sum(1 for q in queue if q["status"] in ("ready", "opened")))]
    for i, (label, v) in enumerate(lines, 4):
        ws.cell(row=i, column=1, value=label)
        ws.cell(row=i, column=2, value=v).font = Font(bold=True)
    r = len(lines) + 6
    ws.cell(row=r, column=1, value="Colours").font = Font(bold=True, size=13)
    for i, (label, hexcol) in enumerate([*ELIG_COLOR.items(), *STATUS_COLOR.items(), *TIER_COLOR.items()], r + 1):
        c = ws.cell(row=i, column=1, value=label)
        c.fill = PatternFill("solid", fgColor=hexcol)
    ws.cell(row=r + 1, column=3, value="Match score: red (40) → yellow (65) → green (90+)")
    ws.cell(row=r + 2, column=3, value="Every sheet: use the ▾ in the header row to sort and filter.")
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 56

    # --- Matches -----------------------------------------------------------------------------------------------
    cols = [("Score", 8), ("AI fit", 7), ("Company", 24), ("Role", 46), ("Kind of company", 17), ("Role type", 14),
            ("Where", 30), ("Term", 16), ("Who can apply", 19), ("Visa / notes", 30), ("Skills it asks for", 34),
            ("Posted", 12), ("First seen", 12), ("Your status", 14), ("Starred", 8), ("Source", 12), ("Link", 8)]
    rows = [[j["score"], (j["parts"] or {}).get("AI fit"), j["company"], j["title"],
             companies.TIERS.get(j.get("tier") or "other", "Other"), j.get("category") or "",
             "; ".join(j.get("locations") or [])[:200], ", ".join(j.get("terms") or []), ELIG.get(j["eligibility"], ""),
             "; ".join(j.get("notes") or [])[:240], ", ".join((j.get("skills") or [])[:10]), _date(j.get("posted_at")),
             _date(j.get("first_seen")), STATUS.get(j.get("app_status") or "", ""), "★" if j.get("favourite") else "",
             (j.get("source") or "").split(":")[0], None] for j in jobs]
    ws = _sheet(wb, "Matches", cols, rows, freeze_cols=4)
    _score_scale(ws, 1, len(rows))
    _colour_by_text(ws, 5, TIER_COLOR, len(rows))
    _colour_by_text(ws, 9, ELIG_COLOR, len(rows))
    _colour_by_text(ws, 14, STATUS_COLOR, len(rows))
    _links(ws, 17, [j["url"] for j in jobs])
    _finish(ws, len(rows), dates=(12, 13), wrap=(4, 10))

    # --- Applications ------------------------------------------------------------------------------------------
    cols = [("Company", 24), ("Role", 44), ("Status", 14), ("Applied", 12), ("Last email", 12), ("Last email subject", 40),
            ("Interview", 17), ("Assessment due", 17), ("Notes", 40), ("Link", 8)]
    rows = [[a["co"] or "", a["ti"] or "", STATUS.get(a["status"], a["status"]), _date(a.get("applied_at")),
             _date(a.get("last_email_at")), a.get("last_email_subject") or "", _date(a.get("interview_at")),
             _date(a.get("oa_due_at")), a.get("notes") or "", None] for a in apps]
    ws = _sheet(wb, "Applications", cols, rows, freeze_cols=2)
    _colour_by_text(ws, 3, STATUS_COLOR, len(rows))
    _links(ws, 10, [a["link"] or "" for a in apps])
    _finish(ws, len(rows), dates=(4, 5), wrap=(2, 6, 9))
    for row in ws.iter_rows(min_row=2, max_row=len(rows) + 1, min_col=7, max_col=8):
        for c in row:
            c.number_format = "yyyy-mm-dd hh:mm"

    # --- Ready to apply ----------------------------------------------------------------------------------------
    by = {i: j for j in store.scored_jobs(pid) for i in j["alt_ids"]}  # (cached: the same list as above)
    cols = [("Company", 24), ("Role", 44), ("Score", 8), ("Status", 15), ("Why Jarvis picked it", 60), ("Prepared", 12),
            ("Link", 8)]
    rows = [[q["company"] or "", q["title"] or "", (by.get(q["job_id"]) or {}).get("score"),
             STATUS.get(q["status"], q["status"]), q.get("why") or "", _date(q["created_at"]), None] for q in queue]
    ws = _sheet(wb, "Ready to apply", cols, rows, freeze_cols=2)
    _score_scale(ws, 3, len(rows))
    _colour_by_text(ws, 4, STATUS_COLOR, len(rows))
    _links(ws, 7, [q["url"] or "" for q in queue])
    _finish(ws, len(rows), dates=(6,), wrap=(2, 5))

    # --- Startups ----------------------------------------------------------------------------------------------
    try:
        feed = startups.feed(pid, "all", limit=500)["startups"]
    except Exception:  # (the startup feed needs the YC directory: a fresh install may not have it yet)
        feed = []
    cols = [("Startup", 24), ("Where", 28), ("Pace", 15), ("New internships (30 days)", 12), ("Open internships", 10),
            ("Open to you", 10), ("Roles on its boards", 11), ("Change (30 days)", 10), ("YC batch", 12), ("Team", 8),
            ("Raised (USD)", 14), ("People you know", 10), ("What it does", 50), ("Link", 8)]
    rows = [[s["name"], s["where"], PACE.get(s["pace"], s["pace"]), s["new30"], s["open"], s["open_to_you"], s["roles"],
             s["growth"], s["yc"], s["team"], ((s.get("funding") or {}).get("total_usd") or None),
             s["people"] + len(s["founders"]), s["one_liner"], None] for s in feed]
    ws = _sheet(wb, "Startups", cols, rows, freeze_cols=1)
    _colour_by_text(ws, 3, {"🔥 Hiring fast": "F8CBAD", "Hiring": "C6EFCE", "Quiet": "EDEDED"}, len(rows))
    _links(ws, 14, [s["yc_url"] or s["website"] or "" for s in feed])
    _finish(ws, len(rows), wrap=(2, 13))
    for row in ws.iter_rows(min_row=2, max_row=len(rows) + 1, min_col=11, max_col=11):
        for c in row:
            c.number_format = '"$"#,##0'

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
