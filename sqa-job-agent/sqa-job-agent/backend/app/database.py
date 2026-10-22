from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def ensure_runtime_columns() -> None:
    """Add columns introduced after the initial prototype without requiring downtime.

    Deployments should still adopt Alembic for larger schema changes; these
    additive columns keep existing single-user SQLite/Supabase installs usable.
    """
    additions = {
        "description": "TEXT",
        "salary_min": "FLOAT",
        "salary_max": "FLOAT",
        "salary_currency": "VARCHAR(16)",
        "is_sqa_relevant": "BOOLEAN DEFAULT FALSE",
        "seniority": "VARCHAR(32) DEFAULT 'unknown'",
        "is_remote": "BOOLEAN DEFAULT FALSE",
        "is_worldwide": "BOOLEAN DEFAULT FALSE",
        "pakistan_eligible": "BOOLEAN DEFAULT FALSE",
        "eligible_region": "VARCHAR(32) DEFAULT 'Unclear'",
        "eligible_country": "VARCHAR(64)",
        "required_skills": "JSON",
        "score": "INTEGER DEFAULT 0",
        "score_breakdown": "JSON",
        "notified_at": "TIMESTAMP",
        "notified_channels": "VARCHAR(128)",
        "application_status": "VARCHAR(16) DEFAULT 'not_applied'",
        "applied_at": "TIMESTAMP",
        "follow_up_due": "TIMESTAMP",
        "resume_match_score": "INTEGER",
        "archived": "BOOLEAN DEFAULT FALSE",
        "deep_region_checked": "BOOLEAN DEFAULT FALSE",
    }
    poll_run_additions = {
        "too_old": "INTEGER DEFAULT 0",
        "already_notified": "INTEGER DEFAULT 0",
    }
    preference_additions = {
        "score_threshold": "INTEGER DEFAULT 0",
        "seniority": "VARCHAR(32) DEFAULT ''",
        "last_digest_sent_at": "TIMESTAMP",
        "resume_text": "TEXT DEFAULT ''",
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, columns in (("jobs", additions), ("preferences", preference_additions), ("poll_runs", poll_run_additions)):
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, declaration in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"))
        # Indexes declared with index=True on columns that were added via ALTER TABLE
        # are never created by create_all (it only runs for brand-new tables), so
        # create them explicitly. Idempotent on SQLite and Postgres.
        for name, cols in (
            ("ix_jobs_posted_at", "posted_at"),
            ("ix_jobs_application_status", "application_status"),
            ("ix_jobs_follow_up_due", "follow_up_due"),
            ("ix_jobs_resume_match_score", "resume_match_score"),
            ("ix_jobs_archived", "archived"),
            ("ix_jobs_notified", "notified"),
            ("ix_jobs_archived_score", "archived, score"),
        ):
            connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON jobs ({cols})"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
