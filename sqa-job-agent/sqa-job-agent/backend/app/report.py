"""Excel export (GET /api/export/report.xlsx) built with openpyxl.

Every sheet is a plain rectangular table: one header row, one record per row,
one value per column (no key/value lists, no packed strings), so it filters,
sorts and pivots cleanly in Excel.

  Summary       — one row of all-time totals, then a per-source table
  Poll History  — one row per PollRun; per-source counts as one column per adapter
  Saved Jobs    — every row in the jobs table (the "how many entries" proof)
"""
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import func
from sqlalchemy.orm import Session

from .adapters import ADAPTERS
from .models import Job, PollRun

_HEADER_FILL = PatternFill("solid", fgColor="0A1018")
_HEADER_FONT = Font(bold=True, color="EEF2F6")
_DATE_FMT = "yyyy-mm-dd hh:mm"


def _naive(ts: Optional[datetime]) -> Optional[datetime]:
    """openpyxl can't write tz-aware datetimes; DB values are UTC either way."""
    if ts is None:
        return None
    return ts.replace(tzinfo=None) if ts.tzinfo else ts


def _table(ws, headers, rows, start_row=1, widths=None, autofilter=True):
    """Write a rectangular table at `start_row`; returns the next free row."""
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=col, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    for r, row in enumerate(rows, start_row + 1):
        for col, value in enumerate(row, 1):
            cell = ws.cell(row=r, column=col, value=value)
            if isinstance(value, datetime):
                cell.number_format = _DATE_FMT
    end_row = start_row + max(len(rows), 0)
    for col, header in enumerate(headers, 1):
        letter = get_column_letter(col)
        width = (widths or {}).get(header) or max(12, min(48, len(header) + 4))
        ws.column_dimensions[letter].width = max(ws.column_dimensions[letter].width or 0, width)
    if autofilter:
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(headers))}{max(end_row, start_row + 1)}"
    return end_row + 2  # one blank row between tables


def build_report(db: Session) -> BytesIO:
    wb = Workbook()
    runs = db.query(PollRun).order_by(PollRun.started_at.asc()).all()
    jobs = db.query(Job).order_by(Job.discovered_at.desc()).all()
    source_names = [a.name for a in ADAPTERS]

    # ---------------- Summary: ONE row, one column per metric ----------------
    ws = wb.active
    ws.title = "Summary"
    first_job = db.query(func.min(Job.discovered_at)).scalar()
    last_job = db.query(func.max(Job.discovered_at)).scalar()
    notified_jobs = db.query(func.count(Job.id)).filter(Job.notified.is_(True)).scalar() or 0
    applied_jobs = db.query(func.count(Job.id)).filter(Job.application_status == "applied").scalar() or 0
    summary_headers = [
        "Generated at (UTC)", "Poll runs recorded", "Total fetched (all polls)", "Total saved / entries (all polls)",
        "Total notified (all polls)", "Duplicates skipped (all polls)", "No-keyword skips (all polls)",
        "Jobs in database now", "Notified (current table)", "Applied (current table)", "Configured sources",
        "First poll (UTC)", "Last poll (UTC)", "First job discovered (UTC)", "Last job discovered (UTC)",
    ]
    summary_row = [
        _naive(datetime.now(timezone.utc)), len(runs),
        sum(r.fetched or 0 for r in runs), sum(r.saved or 0 for r in runs), sum(r.notified or 0 for r in runs),
        sum(r.duplicate or 0 for r in runs), sum(r.no_keyword or 0 for r in runs),
        len(jobs), notified_jobs, applied_jobs, len(source_names),
        _naive(runs[0].started_at) if runs else None, _naive(runs[-1].started_at) if runs else None,
        _naive(first_job), _naive(last_job),
    ]
    next_row = _table(ws, summary_headers, [summary_row], widths={h: 22 for h in summary_headers}, autofilter=False)

    # per-source table (current jobs table), one row per source
    per_source = {}
    for source, count in db.query(Job.source, func.count(Job.id)).group_by(Job.source).all():
        per_source[source] = per_source.get(source, {}) | {"jobs": count}
    for source, count in db.query(Job.source, func.count(Job.id)).filter(Job.notified.is_(True)).group_by(Job.source).all():
        per_source.setdefault(source, {})["notified"] = count
    for source, count in db.query(Job.source, func.count(Job.id)).filter(Job.application_status == "applied").group_by(Job.source).all():
        per_source.setdefault(source, {})["applied"] = count
    for source, avg in db.query(Job.source, func.avg(Job.score)).group_by(Job.source).all():
        per_source.setdefault(source, {})["avg"] = round(avg or 0, 1)
    rows = [
        (name, per_source.get(name, {}).get("jobs", 0), per_source.get(name, {}).get("notified", 0),
         per_source.get(name, {}).get("applied", 0), per_source.get(name, {}).get("avg", None), "yes" if name in source_names else "no")
        for name in sorted(set(source_names) | set(per_source), key=lambda n: -per_source.get(n, {}).get("jobs", 0))
    ]
    _table(ws, ["Source", "Jobs in database", "Notified", "Applied", "Average score", "Configured adapter"], rows,
           start_row=next_row, widths={"Source": 18}, autofilter=False)
    ws.freeze_panes = "A2"

    # ---------------- Poll History: one column per source ----------------
    ws = wb.create_sheet("Poll History")
    run_sources = sorted({name for r in runs for name in (r.per_source or {})} | set(source_names))
    headers = ["Run #", "Started at (UTC)", "Finished at (UTC)", "Fetched", "Saved", "Notified", "Already notified (ledger)",
               "Duplicates", "Too old", "No keyword match", "Blocked company", "Invalid URL", "Adapter errors"] + [f"fetched: {s}" for s in run_sources]
    rows = [
        [r.id, _naive(r.started_at), _naive(r.finished_at), r.fetched, r.saved, r.notified, r.already_notified or 0, r.duplicate,
         r.too_old or 0, r.no_keyword, r.blocked_company, r.invalid_url, r.adapter_errors or ""] + [(r.per_source or {}).get(s) for s in run_sources]
        for r in runs
    ]
    _table(ws, headers, rows, widths={"Started at (UTC)": 18, "Finished at (UTC)": 18, "Adapter errors": 18})
    ws.freeze_panes = "C2"

    # ---------------- Saved Jobs: one row per job ----------------
    ws = wb.create_sheet("Saved Jobs")
    headers = ["ID", "Title", "Company", "Source", "Score", "Resume match %", "Seniority", "Eligible region",
               "Eligible country", "Location", "Remote", "Worldwide", "Pakistan eligible", "Posted at (UTC)",
               "Discovered at (UTC)", "Age category", "Application status", "Applied at (UTC)", "Follow-up due (UTC)",
               "Notified", "Notified at (UTC)", "Channels", "Matched keyword", "Required skills", "Salary min",
               "Salary max", "Currency", "Archived", "URL"]
    rows = [
        [j.id, j.title, j.company, j.source, j.score, j.resume_match_score, j.seniority, j.eligible_region,
         j.eligible_country, j.location, "yes" if j.is_remote else "no", "yes" if j.is_worldwide else "no",
         "yes" if j.pakistan_eligible else "no", _naive(j.posted_at), _naive(j.discovered_at), j.age_category,
         j.application_status, _naive(j.applied_at), _naive(j.follow_up_due), "yes" if j.notified else "no",
         _naive(j.notified_at), j.notified_channels or "", j.matched_keyword, ", ".join(j.required_skills or []),
         j.salary_min, j.salary_max, j.salary_currency, "yes" if j.archived else "no", j.url]
        for j in jobs
    ]
    _table(ws, headers, rows, widths={"Title": 44, "Company": 24, "Location": 22, "Posted at (UTC)": 18,
                                      "Discovered at (UTC)": 18, "Applied at (UTC)": 18, "Follow-up due (UTC)": 18,
                                      "Notified at (UTC)": 18, "Required skills": 40, "URL": 60})
    ws.freeze_panes = "C2"

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
