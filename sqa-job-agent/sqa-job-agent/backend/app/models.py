from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from .database import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external_id"),)

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(64), nullable=False, index=True)         # e.g. "remoteok"
    external_id = Column(String(255), nullable=False)                # id/url from the source, used for dedup
    title = Column(String(500), nullable=False)
    company = Column(String(255), default="")
    url = Column(String(1000), nullable=False)
    location = Column(String(255), default="Remote")
    tags = Column(String(1000), default="")                          # comma-separated
    matched_keyword = Column(String(255), default="")
    posted_at = Column(DateTime, nullable=True)
    description = Column(Text, default="")
    salary_min = Column(Float, nullable=True)
    salary_max = Column(Float, nullable=True)
    salary_currency = Column(String(16), nullable=True)
    is_sqa_relevant = Column(Boolean, default=False, index=True)
    seniority = Column(String(32), default="unknown", index=True)
    is_remote = Column(Boolean, default=False)
    is_worldwide = Column(Boolean, default=False)
    pakistan_eligible = Column(Boolean, default=False, index=True)
    # Worldwide | Americas | Europe | Asia-Pacific | Africa | Specific country | Unclear (see geo.py)
    eligible_region = Column(String(32), default="Unclear", index=True)
    eligible_country = Column(String(64), nullable=True)   # only with eligible_region == "Specific country"
    # True when eligible_region above came from a deep region check (app/deep_region.py)
    # re-classifying the job's own posting page, not just the feed/API description.
    deep_region_checked = Column(Boolean, default=False)
    required_skills = Column(JSON, default=list)
    score = Column(Integer, default=0, index=True)
    score_breakdown = Column(JSON, default=dict)
    discovered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    notified = Column(Boolean, default=False)
    notified_at = Column(DateTime, nullable=True, index=True)      # set only when >=1 channel actually sent
    notified_channels = Column(String(128), nullable=True)          # comma-separated, e.g. "email,telegram"
    # Manual application tracking — every transition is a user click in the
    # dashboard; nothing here is automated. not_applied | applying | applied
    application_status = Column(String(16), default="not_applied", index=True)
    applied_at = Column(DateTime, nullable=True, index=True)        # set only on the transition to "applied"
    follow_up_due = Column(DateTime, nullable=True, index=True)     # applied_at + FOLLOW_UP_DAYS; snoozable
    # Moved out of the active views (kept for history). Set in bulk by
    # POST /api/admin/archive-existing; new jobs always start unarchived.
    archived = Column(Boolean, default=False, index=True)
    # Cosine similarity between the user's resume text and the job (0-100).
    # Null when no resume is set (or the embedder is unavailable) — never 0.
    resume_match_score = Column(Integer, nullable=True, index=True)

    notes = relationship("JobNote", back_populates="job", cascade="all, delete-orphan", order_by="JobNote.created_at.desc()")

    @property
    def age_category(self) -> str:
        """'recent' when posted within RECENT_DAYS (or the source gave no date —
        unknown is treated as recent), else 'older'. Computed, not stored, so it
        never goes stale; the /api/jobs `age` filter uses the same rule in SQL."""
        return "recent" if is_recent(self.posted_at) else "older"


RECENT_DAYS = 7


def recent_cutoff(now: datetime | None = None) -> datetime:
    """Naive-UTC cutoff, matching how DateTime columns come back from SQLite/Postgres."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=RECENT_DAYS)).replace(tzinfo=None)


def is_recent(posted_at: datetime | None, now: datetime | None = None) -> bool:
    if posted_at is None:
        return True
    ts = posted_at.replace(tzinfo=None) if posted_at.tzinfo else posted_at  # both sides naive UTC
    return ts >= recent_cutoff(now)


class JobNote(Base):
    """Free-text journal entries the user attaches to a job ("interview
    scheduled", "rejected", ...). Many per job; no status enum on purpose."""
    __tablename__ = "job_notes"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    job = relationship("Job", back_populates="notes")


class PollRun(Base):
    """One row per scheduler poll cycle — the stats dict scheduler.py already
    computes, persisted so history survives restarts (feeds the Excel export)."""
    __tablename__ = "poll_runs"

    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    finished_at = Column(DateTime, nullable=True)
    fetched = Column(Integer, default=0)
    saved = Column(Integer, default=0)
    notified = Column(Integer, default=0)
    duplicate = Column(Integer, default=0)
    no_keyword = Column(Integer, default=0)
    blocked_company = Column(Integer, default=0)
    invalid_url = Column(Integer, default=0)
    too_old = Column(Integer, default=0)
    already_notified = Column(Integer, default=0)
    adapter_errors = Column(String(255), default="")      # comma-separated adapter names that raised
    per_source = Column(JSON, default=dict)               # {"remoteok": 99, "jobicy": 50, ...} jobs returned per adapter
    stats = Column(JSON, default=dict)                    # the full stats dict, for anything not broken out above


class BlockedCompany(Base):
    """Companies whose postings are dropped at ingestion (case-insensitive
    exact match on the normalised company name)."""
    __tablename__ = "blocked_companies"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Preference(Base):
    """
    Single-row table (id=1) holding the dashboard-editable filter settings.
    Kept as a table (not just env vars) so the frontend can update it live
    without restarting the backend.
    """
    __tablename__ = "preferences"

    id = Column(Integer, primary_key=True, default=1)
    keywords = Column(Text, default="")     # comma-separated, overrides DEFAULT_KEYWORDS when non-empty
    locations = Column(Text, default="")    # comma-separated allow-list; empty = any location
    excluded_sources = Column(Text, default="")  # comma-separated source names to temporarily disable
    score_threshold = Column(Integer, default=0)
    seniority = Column(String(32), default="")
    last_digest_sent_at = Column(DateTime, nullable=True)   # daily digest watermark (feature 3)
    resume_text = Column(Text, default="")                   # plain-text resume for resume_match_score (feature 4)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
