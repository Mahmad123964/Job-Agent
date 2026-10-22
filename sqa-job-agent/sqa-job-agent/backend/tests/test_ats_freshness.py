"""Per-source-category freshness window: ATS company boards (Greenhouse/Ashby/
Lever, source_category="ats_board") get MAX_JOB_AGE_DAYS_ATS_BOARDS (30d
default) instead of the aggregator default MAX_JOB_AGE_DAYS (14d) — a company
careers page lists a job until it's filled, not daily like an aggregator feed.
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.scheduler as sched
from app.adapters.base import BaseAdapter
from app.scheduler import OUTCOMES, is_too_old, max_age_days_for, poll_all_sources
from tests.conftest import raw
from tests.test_pipeline import Stub, stub_classify

NOW = datetime.now(timezone.utc)


class AtsStub(BaseAdapter):
    """Stands in for a Greenhouse/Ashby/Lever adapter: same base class, just
    tagged ats_board, so this doesn't depend on any live network call."""

    name = "ats-stub"
    source_category = "ats_board"

    def __init__(self, jobs):
        self.jobs = jobs

    def fetch(self):
        return list(self.jobs)


def test_max_age_days_for_defaults():
    assert max_age_days_for("aggregator") == 14
    assert max_age_days_for("ats_board") == 30
    assert max_age_days_for("unknown-category") == 14  # unrecognised -> aggregator default


@pytest.mark.parametrize("age_days,category,expected_too_old", [
    (13, "aggregator", False),
    (15, "aggregator", True),
    (20, "aggregator", True),    # audit case: 20d aggregator job stays excluded
    (20, "ats_board", False),    # audit case: 20d ATS-board job now passes
    (29, "ats_board", False),
    (31, "ats_board", True),
])
def test_is_too_old_respects_category(age_days, category, expected_too_old):
    posted = NOW - timedelta(days=age_days)
    assert is_too_old(posted, now=NOW, source_category=category) is expected_too_old


def test_pipeline_20_day_old_jobs_ats_board_passes_aggregator_excluded(monkeypatch, clean_db):
    """The exact audit scenario end-to-end: a 20-day-old Canonical-style ATS
    posting is saved; a 20-day-old Jobicy-style aggregator posting is not."""
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    old = NOW - timedelta(days=20)
    monkeypatch.setattr(sched, "ADAPTERS", [
        Stub([raw(external_id="agg-old", title="QA Engineer (aggregator, 20d)", company="Agg Co",
                   url="https://agg.example-corp.io/1", posted_at=old)]),
        AtsStub([raw(external_id="ats-old", title="QA Engineer (ATS board, 20d)", company="ATS Co",
                     url="https://ats.example-corp.io/1", posted_at=old)]),
    ])
    stats = poll_all_sources()
    assert stats["fetched"] == sum(stats[k] for k in OUTCOMES), stats
    assert stats["skipped_too_old"] == 1 and stats["saved"] == 1

    from app.models import Job
    saved = clean_db.query(Job).one()
    assert saved.title == "QA Engineer (ATS board, 20d)"


def test_ats_board_31_days_is_excluded_too(monkeypatch, clean_db):
    """The longer window is not unlimited — 31d ATS-board jobs still get filtered."""
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched, "ADAPTERS", [
        AtsStub([raw(external_id="ats-too-old", title="QA Engineer (ATS board, 31d)", company="ATS Co",
                     url="https://ats.example-corp.io/2", posted_at=NOW - timedelta(days=31))]),
    ])
    stats = poll_all_sources()
    assert stats["skipped_too_old"] == 1 and stats["saved"] == 0
