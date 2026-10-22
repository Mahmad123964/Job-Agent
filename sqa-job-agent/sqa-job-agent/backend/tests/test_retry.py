"""Roadmap item 6: failed notifications are retried on the next poll (idempotently)."""
import app.scheduler as sched
from app.models import Job
from app.scheduler import poll_all_sources
from tests.conftest import raw
from tests.test_pipeline import Stub, stub_classify


def test_failed_send_is_retried_next_poll(monkeypatch, clean_db):
    monkeypatch.setattr(sched, "classify", stub_classify)
    calls = {"n": 0}

    def flaky(job):  # every channel fails on the first cycle, succeeds afterwards
        calls["n"] += 1
        return [] if calls["n"] == 1 else ["email"]

    monkeypatch.setattr(sched, "notify_all", flaky)
    monkeypatch.setattr(sched, "ADAPTERS", [Stub([raw(external_id="r1", title="QA Engineer")])])

    first = poll_all_sources()
    assert first["saved"] == 1 and first["notify_failed"] == 1 and first["notified"] == 0
    job = clean_db.query(Job).one(); clean_db.refresh(job)
    assert job.notified is False and job.notified_at is None

    second = poll_all_sources()   # job is now a duplicate for ingestion, but the retry pass delivers it
    assert second["retried_ok"] == 1 and second["skipped_duplicate"] == 1 and second["saved"] == 0
    clean_db.refresh(job)
    assert job.notified is True and job.notified_at is not None and job.notified_channels == "email"
    assert calls["n"] == 2

    third = poll_all_sources()    # nothing left to retry; ledger prevents any re-send
    assert third["retried_ok"] == 0 and third["retry_failed"] == 0 and calls["n"] == 2


def test_retry_respects_ledger(monkeypatch, clean_db):
    """A job already delivered (ledger says so) but flagged notified=False is repaired without re-sending."""
    from app.ledger import NotificationLedger, fingerprints
    monkeypatch.setattr(sched, "classify", stub_classify)
    sends = []
    monkeypatch.setattr(sched, "notify_all", lambda job: (sends.append(job.title), [])[1])   # always fails
    monkeypatch.setattr(sched, "ADAPTERS", [Stub([raw(external_id="l1", title="SDET")])])
    poll_all_sources()
    job = clean_db.query(Job).one()
    NotificationLedger().record(fingerprints(job.source, job.external_id, job.title, job.company, job.url), ["email"], job.title)
    stats = poll_all_sources()
    clean_db.refresh(job)
    assert job.notified is True and job.notified_channels == "ledger:recovered"
    assert stats["retried_ok"] == 0 and len(sends) == 1  # no second send attempt
