import logging
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .adapters import ADAPTERS
from .classifier import classifier_backend, preload_models
from .config import get_settings
from .database import Base, SessionLocal, engine, get_db, ensure_runtime_columns
from .classifier import resume_match_score
from .report import build_report
from .ledger import seed_from_db
from .models import BlockedCompany, Job, JobNote, Preference, recent_cutoff
from .scheduler import normalize_company_name, poll_all_sources, start_scheduler
from .notifiers.email import EmailNotifier
from .schemas import (
    BackfillEmailIn,
    BackfillEmailOut,
    BlockedCompanyIn,
    BlockedCompanyOut,
    JobOut,
    JobStatusIn,
    JobSummaryOut,
    NoteIn,
    NoteOut,
    PreferenceIn,
    PreferenceOut,
    ResumeIn,
    ResumeOut,
    SnoozeIn,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sqa_job_agent")

Base.metadata.create_all(bind=engine)
ensure_runtime_columns()
settings = get_settings()

app = FastAPI(title="SQA Job Agent")


def require_api_key(x_api_key: str = Header(default="", alias="X-Api-Key")):
    """Guard for every mutating route. GET routes stay open (read-only dashboard).
    With ADMIN_API_KEY unset the check is disabled — acceptable only when the
    server is bound to 127.0.0.1."""
    expected = settings.ADMIN_API_KEY
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid X-Api-Key")


MUTATING = [Depends(require_api_key)]

# CORS: explicit origins from FRONTEND_ORIGINS/FRONTEND_ORIGIN plus any localhost /
# 127.0.0.1 origin on any port (Vite falls back to 5174, 5175, ... when 5173 is
# busy, and a port mismatch used to fail silently as "Failed to fetch").
# Scoped to loopback hosts only — never a wildcard.
_ALLOWED_ORIGINS = sorted({settings.FRONTEND_ORIGIN, *settings.FRONTEND_ORIGINS} - {""})
_LOCALHOST_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=_LOCALHOST_ORIGIN_REGEX,
    allow_methods=["*"],
    allow_headers=["*"],
)

_scheduler = None


_warmup = {"state": "pending", "detail": "", "started_at": None, "finished_at": None}


def _warm_up():
    """Model preload + first poll, off the event loop. Until this finishes the
    server already answers /api/* (health reports warming_up=true) — before,
    the whole thing ran inside the startup hook, so the port stayed closed for
    ~50 s (model load + 13 adapters + classification) and the dashboard's
    probe reported the backend as "disconnected"."""
    _warmup.update(state="running", started_at=datetime.now(timezone.utc))
    try:
        if settings.HF_PRELOAD:
            logger.info("Classifier backend: %s", preload_models())  # first run downloads ~1.7 GB
        poll_all_sources()  # so the dashboard isn't empty on first load
        _warmup.update(state="ready")
    except Exception as exc:  # keep serving; the scheduler will poll again anyway
        logger.exception("Warm-up failed: %s", exc)
        _warmup.update(state="error", detail=str(exc))
    finally:
        _warmup["finished_at"] = datetime.now(timezone.utc)


@app.on_event("startup")
def on_startup():
    global _scheduler
    if not settings.ADMIN_API_KEY:
        logger.warning("ADMIN_API_KEY is not set: mutating endpoints are UNAUTHENTICATED. Set it in .env unless bound to 127.0.0.1 only.")
    logger.info("CORS allowed origins: %s (plus any localhost/127.0.0.1 origin via regex %s)", ", ".join(_ALLOWED_ORIGINS), _LOCALHOST_ORIGIN_REGEX)
    _scheduler = start_scheduler()
    import threading

    threading.Thread(target=_warm_up, name="warm-up", daemon=True).start()


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    """Cheap liveness + DB check; the dashboard polls this for its connection indicator."""
    try:
        active_jobs = db.query(func.count(Job.id)).filter(Job.archived.is_(False)).scalar() or 0
        db_ok = True
    except Exception as exc:  # pragma: no cover
        logger.error("health: DB check failed: %s", exc)
        active_jobs, db_ok = None, False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "warming_up": _warmup["state"] in ("pending", "running"),
        "warmup": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in _warmup.items()},
        "active_jobs": active_jobs,
        "sources": [a.name for a in ADAPTERS],
        "classifier_backend": classifier_backend(),  # "huggingface" or "rules" (fallback)
        "notify_score_threshold": settings.NOTIFY_SCORE_THRESHOLD,
        "max_job_age_days": settings.MAX_JOB_AGE_DAYS,
        "allowed_origins": _ALLOWED_ORIGINS,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/jobs", response_model=List[JobSummaryOut])
def list_jobs(
    keyword: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    seniority: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None, ge=0, le=100),
    sort: str = Query("score"),  # score | newest | resume
    age: Optional[str] = Query(None, pattern="^(recent|older)$"),  # recent = posted <=7d ago or unknown; older = the rest
    limit: int = Query(100, le=500),
    include_blocked: bool = Query(False),
    archived: bool = Query(False),  # False = active feed only; True = archived only
    db: Session = Depends(get_db),
):
    q = db.query(Job).filter(Job.archived.is_(True) if archived else Job.archived.isnot(True))
    if age == "recent":
        q = q.filter(or_(Job.posted_at.is_(None), Job.posted_at >= recent_cutoff()))
    elif age == "older":
        q = q.filter(Job.posted_at < recent_cutoff())
    if keyword:
        q = q.filter(
            Job.title.ilike(f"%{keyword}%")
            | Job.company.ilike(f"%{keyword}%")
            | Job.description.ilike(f"%{keyword}%")
        )
    if location:
        q = q.filter(Job.location.ilike(f"%{location}%"))
    if source:
        q = q.filter(Job.source == source)
    if seniority:
        q = q.filter(Job.seniority == seniority)
    if min_score is not None:
        q = q.filter(Job.score >= min_score)
    if not include_blocked:
        # Companies blocked after their jobs were saved disappear from the feed too.
        blocked = [normalize_company_name(n) for (n,) in db.query(BlockedCompany.company_name).all()]
        if blocked:
            q = q.filter(func.lower(Job.company).notin_(blocked))
    if sort == "newest":
        ordering = Job.discovered_at.desc()
    elif sort == "resume":
        ordering = Job.resume_match_score.desc().nullslast()
    else:
        ordering = Job.score.desc()
    return q.order_by(ordering, Job.discovered_at.desc()).limit(limit).all()


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    """Full record including the description (list endpoints omit it)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/archived", response_model=List[JobSummaryOut])
def list_archived(limit: int = Query(200, le=1000), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    """Read-only historical view of archived jobs, newest discovered first."""
    return db.query(Job).filter(Job.archived.is_(True)).order_by(Job.discovered_at.desc()).offset(offset).limit(limit).all()


@app.post("/api/admin/archive-existing", dependencies=MUTATING)
def archive_existing(db: Session = Depends(get_db)):
    """One-time: move every job currently in the database out of the active
    views (archived=true). Nothing is deleted; new jobs saved after this start
    unarchived. Safe to call again (already-archived rows are untouched)."""
    n = db.query(Job).filter(Job.archived.isnot(True)).update({Job.archived: True}, synchronize_session=False)
    db.commit()
    # Also make sure everything already notified is in the ledger, so a later
    # DB reset can't re-send it (the ledger lives outside the database).
    seeded = seed_from_db(db)
    logger.info("Archived %d job(s); %d notified job(s) added to the ledger", n, seeded)
    return {"archived": n, "ledger_seeded": seeded, "total_archived": db.query(func.count(Job.id)).filter(Job.archived.is_(True)).scalar()}


@app.get("/api/notifications", response_model=List[JobSummaryOut])
def list_notifications(
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Persistent log of every job that actually went out on >=1 channel,
    newest notification first. Independent of the live feed's filters."""
    return (
        db.query(Job)
        .filter(Job.notified.is_(True))
        .order_by(Job.notified_at.desc().nullslast(), Job.discovered_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@app.patch("/api/jobs/{job_id}/status", response_model=JobOut, dependencies=MUTATING)
def update_job_status(job_id: int, payload: JobStatusIn, db: Session = Depends(get_db)):
    """Manual application tracking. Called only from explicit user clicks in
    the dashboard — the app never applies on the user's behalf.

    applied_at is stamped only on the transition *into* "applied" (re-marking
    an already-applied job keeps the original timestamp) and cleared when the
    job is moved back to "not_applied".
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    previous = job.application_status or "not_applied"
    job.application_status = payload.status
    if payload.status == "applied" and previous != "applied":
        job.applied_at = datetime.now(timezone.utc)
        job.follow_up_due = job.applied_at + timedelta(days=settings.FOLLOW_UP_DAYS)
    elif payload.status == "not_applied":
        job.applied_at = None
        job.follow_up_due = None
    db.commit()
    db.refresh(job)
    return job


@app.get("/api/applied", response_model=List[JobSummaryOut])
def list_applied(
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Permanent record of jobs the user marked as applied, most recent first."""
    return (
        db.query(Job)
        .filter(Job.application_status == "applied")
        .order_by(Job.applied_at.desc().nullslast(), Job.discovered_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def _get_or_create_preference(db: Session) -> Preference:
    pref = db.query(Preference).filter(Preference.id == 1).first()
    if not pref:
        pref = Preference(id=1, keywords="", locations="", excluded_sources="")
        db.add(pref)
        db.commit()
        db.refresh(pref)
    return pref


def _utcnow_naive() -> datetime:
    """DB timestamps are stored naive-UTC (SQLite/Postgres TIMESTAMP); compare like-for-like."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- feature 1: follow-ups
@app.get("/api/applied/due-followups", response_model=List[JobSummaryOut])
def list_due_followups(limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    """Applied jobs whose follow-up date has passed and that are still just 'applied'."""
    return (
        db.query(Job)
        .filter(Job.application_status == "applied", Job.follow_up_due.isnot(None), Job.follow_up_due <= _utcnow_naive())
        .order_by(Job.follow_up_due.asc())
        .limit(limit)
        .all()
    )


@app.post("/api/jobs/{job_id}/snooze", response_model=JobOut, dependencies=MUTATING)
def snooze_follow_up(job_id: int, payload: SnoozeIn = SnoozeIn(), db: Session = Depends(get_db)):
    """Push follow_up_due forward by `days` (default 3) from now or from the
    current due date, whichever is later."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.application_status != "applied":
        raise HTTPException(status_code=400, detail="Only applied jobs have a follow-up date")
    base = max(_utcnow_naive(), job.follow_up_due) if job.follow_up_due else _utcnow_naive()
    job.follow_up_due = base + timedelta(days=max(1, payload.days))
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------- feature 2: notes
@app.get("/api/jobs/{job_id}/notes", response_model=List[NoteOut])
def list_notes(job_id: int, db: Session = Depends(get_db)):
    if not db.query(Job.id).filter(Job.id == job_id).first():
        raise HTTPException(status_code=404, detail="Job not found")
    return db.query(JobNote).filter(JobNote.job_id == job_id).order_by(JobNote.created_at.desc(), JobNote.id.desc()).all()


@app.post("/api/jobs/{job_id}/notes", response_model=NoteOut, status_code=201, dependencies=MUTATING)
def add_note(job_id: int, payload: NoteIn, db: Session = Depends(get_db)):
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Note text is empty")
    if not db.query(Job.id).filter(Job.id == job_id).first():
        raise HTTPException(status_code=404, detail="Job not found")
    note = JobNote(job_id=job_id, text=text[:5000])
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


# ---------------------------------------------------------------- feature 4: resume
def _rescore_jobs_for_resume(resume_text: str, limit: int = 500) -> int:
    """Recompute resume_match_score for the most recent jobs after the resume
    changes (new jobs get it at classify-time). Runs in a background task."""
    db = SessionLocal()
    try:
        jobs = db.query(Job).order_by(Job.discovered_at.desc()).limit(limit).all()
        for job in jobs:
            job.resume_match_score = resume_match_score(resume_text, job.title, job.description or "", list(job.required_skills or []))
        db.commit()
        logger.info("Resume match rescored for %d job(s)", len(jobs))
        return len(jobs)
    finally:
        db.close()


@app.get("/api/resume", response_model=ResumeOut)
def get_resume(db: Session = Depends(get_db)):
    pref = _get_or_create_preference(db)
    text = pref.resume_text or ""
    return ResumeOut(text=text, chars=len(text))


@app.put("/api/resume", response_model=ResumeOut, dependencies=MUTATING)
def put_resume(payload: ResumeIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    pref = _get_or_create_preference(db)
    pref.resume_text = payload.text.strip()
    pref.updated_at = datetime.now(timezone.utc)
    db.commit()
    text = pref.resume_text
    # Existing jobs are rescored in the background; an empty resume clears the scores.
    count = db.query(func.count(Job.id)).scalar() or 0
    background.add_task(_rescore_jobs_for_resume, text)
    return ResumeOut(text=text, chars=len(text), rescored_jobs=min(count, 500))


# ---------------------------------------------------------------- feature 5: analytics
@app.get("/api/analytics")
def analytics(db: Session = Depends(get_db)):
    """Aggregates over the existing jobs table — no extra tracking tables.
    Bucketing is done in Python so it behaves identically on SQLite and Postgres."""
    now = _utcnow_naive()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    per_source = Counter()
    for source, in db.query(Job.source).filter(Job.discovered_at >= week_ago).all():
        per_source[source] += 1

    buckets: dict = defaultdict(lambda: [0, 0])  # day -> [sum, count]
    for discovered_at, score in db.query(Job.discovered_at, Job.score).filter(Job.discovered_at >= two_weeks_ago).all():
        if discovered_at is None:
            continue
        day = discovered_at.date().isoformat()
        buckets[day][0] += score or 0
        buckets[day][1] += 1
    trend = []
    for offset in range(13, -1, -1):
        day = (now - timedelta(days=offset)).date().isoformat()
        total, count = buckets.get(day, [0, 0])
        trend.append({"day": day, "avg_score": round(total / count, 1) if count else None, "count": count})

    status_counts = dict(db.query(Job.application_status, func.count(Job.id)).group_by(Job.application_status).all())
    saved_total = sum(status_counts.values())
    active_total = db.query(func.count(Job.id)).filter(Job.archived.isnot(True)).scalar() or 0
    archived_total = saved_total - active_total
    applied_total = status_counts.get("applied", 0)
    return {
        "jobs_per_source": [{"source": name, "count": count} for name, count in per_source.most_common()],
        "score_trend": trend,
        "conversion": {
            "saved_total": saved_total,
            "active_total": active_total,
            "archived_total": archived_total,
            "applying": status_counts.get("applying", 0),
            "applied": applied_total,
            "rate": round(applied_total / saved_total, 3) if saved_total else 0.0,
        },
        "generated_at": now.isoformat(),
    }


# ---------------------------------------------------------------- feature 6: blocklist
@app.get("/api/blocklist", response_model=List[BlockedCompanyOut])
def list_blocklist(db: Session = Depends(get_db)):
    return db.query(BlockedCompany).order_by(BlockedCompany.created_at.desc()).all()


@app.post("/api/blocklist", response_model=BlockedCompanyOut, status_code=201, dependencies=MUTATING)
def block_company(payload: BlockedCompanyIn, db: Session = Depends(get_db)):
    name = " ".join(payload.company_name.split())
    if not name:
        raise HTTPException(status_code=422, detail="company_name is empty")
    existing = db.query(BlockedCompany).filter(func.lower(BlockedCompany.company_name) == name.lower()).first()
    if existing:
        return existing  # idempotent
    entry = BlockedCompany(company_name=name)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/api/blocklist/{entry_id}", status_code=204, dependencies=MUTATING)
def unblock_company(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(BlockedCompany).filter(BlockedCompany.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Blocklist entry not found")
    db.delete(entry)
    db.commit()
    return None


# ---------------------------------------------------------------- admin: history backfill
_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


@app.post("/api/admin/backfill-email", response_model=BackfillEmailOut, dependencies=MUTATING)
def backfill_email(payload: BackfillEmailIn, db: Session = Depends(get_db)):
    """One-off: email every job ever notified to ONE explicit recipient (for
    onboarding a new stakeholder). Email only — never PREFERRED_NOTIFIER — and
    strictly read-only: no notified_at / status / dedup state is touched."""
    import re

    recipient = payload.recipient.strip()
    if not re.match(_EMAIL_RE, recipient):
        raise HTTPException(status_code=422, detail="recipient must be a valid email address")
    jobs = (
        db.query(Job)
        .filter(Job.notified.is_(True))
        .order_by(Job.notified_at.asc().nullsfirst(), Job.id.asc())
        .all()
    )
    if not jobs:
        return BackfillEmailOut(recipient=recipient, jobs=0, emails_sent=0, parts=[])
    try:
        parts = EmailNotifier().send_backfill_summary(recipient, jobs)
    except RuntimeError as exc:  # SMTP not configured
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("Backfill email to %s failed: %s", recipient, exc)
        raise HTTPException(status_code=502, detail=f"SMTP send failed: {exc}")
    logger.info("Backfill: %d job(s) sent to %s in %d email(s)", len(jobs), recipient, len(parts))
    return BackfillEmailOut(recipient=recipient, jobs=len(jobs), emails_sent=len(parts), parts=parts)


# ---------------------------------------------------------------- Excel export
@app.get("/api/export/report.xlsx")
def export_report(db: Session = Depends(get_db)):
    """Summary + Poll History + Saved Jobs workbook, generated on demand."""
    buffer = build_report(db)
    filename = f"sqa-job-agent-report-{datetime.now(timezone.utc):%Y%m%d-%H%M}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/sources")
def list_sources():
    return [
        {"name": a.name, "attribution": a.attribution, "access_method": a.access_method}
        for a in ADAPTERS
    ]


@app.get("/api/preferences", response_model=PreferenceOut)
def get_preferences(db: Session = Depends(get_db)):
    pref = db.query(Preference).filter(Preference.id == 1).first()
    if not pref:
        pref = Preference(id=1, keywords="", locations="", excluded_sources="")
        db.add(pref)
        db.commit()
        db.refresh(pref)
    return PreferenceOut(
        keywords=[k for k in pref.keywords.split(",") if k] or settings.DEFAULT_KEYWORDS,
        locations=[l for l in pref.locations.split(",") if l],
        excluded_sources=[s for s in pref.excluded_sources.split(",") if s],
        score_threshold=pref.score_threshold or 0,
        seniority=pref.seniority or "",
        updated_at=pref.updated_at,
    )


@app.put("/api/preferences", response_model=PreferenceOut, dependencies=MUTATING)
def update_preferences(payload: PreferenceIn, db: Session = Depends(get_db)):
    pref = db.query(Preference).filter(Preference.id == 1).first()
    if not pref:
        pref = Preference(id=1)
        db.add(pref)

    if payload.keywords is not None:
        pref.keywords = ",".join(payload.keywords)
    if payload.locations is not None:
        pref.locations = ",".join(payload.locations)
    if payload.excluded_sources is not None:
        pref.excluded_sources = ",".join(payload.excluded_sources)
    if payload.score_threshold is not None:
        pref.score_threshold = max(0, min(100, payload.score_threshold))
    if payload.seniority is not None:
        pref.seniority = payload.seniority

    db.commit()
    db.refresh(pref)
    return PreferenceOut(
        keywords=[k for k in pref.keywords.split(",") if k] or settings.DEFAULT_KEYWORDS,
        locations=[l for l in pref.locations.split(",") if l],
        excluded_sources=[s for s in pref.excluded_sources.split(",") if s],
        score_threshold=pref.score_threshold or 0,
        seniority=pref.seniority or "",
        updated_at=pref.updated_at,
    )


@app.post("/api/poll-now", dependencies=MUTATING)
def poll_now():
    """Manual trigger — lets the dashboard offer a 'Check now' button."""
    stats = poll_all_sources()
    return {"status": "polled", **stats}
