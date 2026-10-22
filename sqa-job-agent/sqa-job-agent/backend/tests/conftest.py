"""Test environment. Settings are read from os.environ at import time (backend/.env
is loaded with override=False), so everything the suite depends on is pinned
here BEFORE any `app.*` import: an isolated SQLite file, an isolated ledger,
the code-default keyword list (not the developer's .env), no model preload,
unconfigured notifiers, and the small MNLI model for the zero-shot fallback.

Run from backend/:  python -m pytest tests -q
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sqa-tests-"))
os.environ.update({
    "DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "NOTIFY_LEDGER_PATH": str(_TMP / "ledger.jsonl"),
    "DEEP_REGION_CACHE_PATH": str(_TMP / "deep_region_cache.jsonl"),
    "HF_PRELOAD": "false",
    "HF_HUB_OFFLINE": "1",              # use the local model cache; no network round-trips per test run
    "HF_ZEROSHOT_MODEL": "typeform/distilbert-base-uncased-mnli",  # ~260 MB instead of BART's 1.6 GB
    "PREFERRED_NOTIFIER": "telegram",   # unconfigured -> every real send fails deterministically
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "DISCORD_WEBHOOK_URL": "",
    "SMTP_USER": "", "SMTP_PASS": "", "EMAIL_TO": "",
    "DIGEST_MODE": "off",
    "NOTIFY_SCORE_THRESHOLD": "0",
    "MAX_JOB_AGE_DAYS": "14",
    "ALLOW_UNDATED_JOBS": "false",
    "ADMIN_API_KEY": "test-admin-key",
    "DEFAULT_KEYWORDS": (
        "qa engineer,sqa,software quality assurance,quality engineer,test engineer,software tester,"
        "automation tester,automation engineer,sdet,quality assurance,qa tester,qa automation,"
        "sqa engineer,qa automation engineer,qa specialist,junior qa,tester,automation qa"
    ),
    "FRONTEND_ORIGIN": "http://localhost:5173",
    "JSEARCH_ENABLED": "false",
    # Off by default so ordinary pipeline tests with an Unclear job never make a real
    # HTTP call; tests/test_deep_region_check.py turns it on explicitly per test.
    "DEEP_REGION_CHECK_ENABLED": "false",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app  # creates tables + runtime columns on the temp DB
    return TestClient(app)


@pytest.fixture()
def db(client):  # `client` imports app.main, which creates the tables
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def clean_db(db):
    """Empty the mutable tables so each pipeline test starts from zero."""
    from app.models import BlockedCompany, Job, JobNote, PollRun
    for model in (JobNote, Job, PollRun, BlockedCompany):
        db.query(model).delete()
    db.commit()
    ledger = Path(os.environ["NOTIFY_LEDGER_PATH"])
    if ledger.exists():
        ledger.unlink()
    deep_cache = Path(os.environ["DEEP_REGION_CACHE_PATH"])
    if deep_cache.exists():
        deep_cache.unlink()
    yield db


def raw(**kw):
    """RawJob factory with sane defaults."""
    from datetime import datetime, timezone
    from app.adapters.base import RawJob
    base = dict(source="stub", external_id="x", title="QA Engineer", url="https://acme.example-corp.io/1",
                company="Acme", location="Remote — Worldwide", posted_at=datetime.now(timezone.utc), description="")
    base.update(kw)
    return RawJob(**base)
