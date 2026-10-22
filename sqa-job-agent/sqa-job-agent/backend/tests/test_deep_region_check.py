"""Deep region check (app/deep_region.py): an Unclear job gets its own posting
page fetched once and re-classified against the fuller text before being
excluded at the notification gate (scheduler.py).
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.deep_region as deep_region
import app.scheduler as sched
from app.deep_region import DeepRegionCache, cache_key, deep_check, is_deep_checkable
from app.geo import Eligibility
from app.scheduler import poll_all_sources
from tests.conftest import raw
from tests.test_pipeline import Stub, stub_classify

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _enable_deep_region_check(monkeypatch):
    """Off by default in tests/conftest.py (so ordinary pipeline tests never make a
    real HTTP call); every test in this file is specifically about this feature."""
    monkeypatch.setattr(sched.get_settings(), "DEEP_REGION_CHECK_ENABLED", True)


def test_looks_like_job_page_matches_company_or_title():
    from app.deep_region import _looks_like_job_page

    assert _looks_like_job_page("Test Co is hiring a QA Engineer, worldwide.", "QA Engineer", "Test Co") is True
    assert _looks_like_job_page("We are hiring a Senior QA Automation Engineer.", "Senior QA Automation Engineer", "") is True
    assert _looks_like_job_page("", "QA Engineer", "Test Co") is False
    # Real WWR failure mode: a busy listing page packed with unrelated job cards.
    # Scattered generic title words ("Engineer", "Quality", "Principal") appear
    # all over such a page from OTHER postings -- word-hit counting alone would
    # false-positive here, which is exactly the bug this stricter check fixes.
    assert _looks_like_job_page(
        "EMEA Only. North America Only. Asia Only. Senior Quality Engineer - Datadog. "
        "Principal Software Engineer - Acme. Quality Assurance Lead - Other Co.",
        "LawnStarter: Principal Quality Engineer", "LawnStarter",
    ) is False


def test_jsearch_is_not_deep_checkable():
    assert is_deep_checkable("jsearch") is False
    assert is_deep_checkable("JSearch") is False  # case-insensitive
    assert is_deep_checkable("jobicy") is True
    assert is_deep_checkable("gh-canonical") is True


def test_jsearch_job_skips_deep_check_entirely(monkeypatch, clean_db):
    """A JSearch-sourced Unclear job must never be fetched -- it stays Unclear
    and excluded, same as before this feature existed."""
    calls = []
    monkeypatch.setattr(deep_region, "fetch_page_text", lambda url, timeout=None: calls.append(url) or "Worldwide, apply now!")
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched, "ADAPTERS", [
        Stub([raw(source="jsearch", external_id="js-1", title="QA Engineer (bare remote)", company="Vague Co",
                   url="https://vague.example-corp.io/1", location="Remote")]),
    ])
    stats = poll_all_sources()
    assert calls == []  # never fetched
    assert stats["deep_region_checked"] == 0
    assert stats["skipped_region_unclear"] == 1
    assert stats["saved"] == 0


def test_unclear_job_reclassified_to_worldwide(monkeypatch, clean_db):
    monkeypatch.setattr(deep_region, "fetch_page_text",
                         lambda url, timeout=None: "Test Co is hiring! This role is fully remote and open worldwide, apply from anywhere.")
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched, "ADAPTERS", [
        Stub([raw(source="gh-testco", external_id="gh-1", title="QA Engineer (bare remote)", company="Test Co",
                   url="https://testco.example-corp.io/1", location="Remote")]),
    ])
    stats = poll_all_sources()
    assert stats["deep_region_checked"] == 1
    assert stats["deep_region_reclassified_worldwide"] == 1
    assert stats["deep_region_confirmed_restricted"] == 0
    assert stats["deep_region_still_unclear"] == 0
    assert stats["saved"] == 1

    from app.models import Job
    saved = clean_db.query(Job).one()
    assert saved.eligible_region == "Worldwide"
    assert saved.deep_region_checked is True


def test_unclear_job_confirmed_restricted_stays_excluded(monkeypatch, clean_db):
    monkeypatch.setattr(deep_region, "fetch_page_text",
                         lambda url, timeout=None: "Test Co: Applicants must be located in Germany for this role.")
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched, "ADAPTERS", [
        Stub([raw(source="gh-testco", external_id="gh-2", title="QA Engineer (bare remote)", company="Test Co",
                   url="https://testco.example-corp.io/2", location="Remote")]),
    ])
    stats = poll_all_sources()
    assert stats["deep_region_checked"] == 1
    assert stats["deep_region_confirmed_restricted"] == 1
    assert stats["deep_region_reclassified_worldwide"] == 0
    assert stats["skipped_region_restricted"] == 1
    assert stats["saved"] == 0

    from app.models import Job
    assert clean_db.query(Job).count() == 0  # confirmed-restricted jobs are still never saved


@pytest.mark.parametrize("page_text", [None, "", "Cookie policy. Privacy policy. Terms of service."])
def test_fetch_failure_or_no_signal_leaves_job_unclear(monkeypatch, clean_db, page_text):
    monkeypatch.setattr(deep_region, "fetch_page_text", lambda url, timeout=None: page_text)
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched, "ADAPTERS", [
        Stub([raw(source="gh-testco", external_id="gh-3", title="QA Engineer (bare remote)", company="Test Co",
                   url="https://testco.example-corp.io/3", location="Remote")]),
    ])
    stats = poll_all_sources()
    assert stats["deep_region_checked"] == 1
    assert stats["deep_region_still_unclear"] == 1
    assert stats["skipped_region_unclear"] == 1
    assert stats["saved"] == 0


def test_generic_listing_page_content_is_not_trusted(monkeypatch, clean_db):
    """Real bug found live on WeWorkRemotely: a plain HTTP GET of a job page can
    return a generic 'browse all jobs' shell (no JS rendering) whose only text
    is OTHER postings' restriction phrases ('EMEA Only', 'North America Only',
    'Asia Only' from unrelated job cards) -- never this job's own content. That
    content must not be trusted just because it contains restriction phrases."""
    monkeypatch.setattr(
        deep_region, "fetch_page_text",
        lambda url, timeout=None: (
            "Datadog: Developer Advocate - Service Management EMEA. North America Only. "
            "EMEA Only. Asia Only. Full-Time. Browse more remote jobs below."
        ),
    )
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched, "ADAPTERS", [
        Stub([raw(source="weworkremotely", external_id="wwr-1", title="QA Engineer",
                   company="LawnStarter", url="https://weworkremotely.com/remote-jobs/lawnstarter-1", location="Remote")]),
    ])
    stats = poll_all_sources()
    assert stats["deep_region_checked"] == 1, stats
    assert stats["deep_region_still_unclear"] == 1
    assert stats["deep_region_reclassified_worldwide"] == 0
    assert stats["deep_region_confirmed_restricted"] == 0  # not misattributed to EMEA/Americas/Asia-Pacific
    assert stats["saved"] == 0


def test_deep_check_runs_only_once_per_job_cache_respected(monkeypatch, clean_db):
    """Same posting (source+external_id) re-encountered on a later poll -- because
    it was excluded, it's never saved, so nothing in jobs.db can dedupe it. The
    cache, not the DB, must stop the second fetch."""
    calls = []

    def fake_fetch(url, timeout=None):
        calls.append(url)
        return "This QA Engineer role at Test Co is open worldwide."

    monkeypatch.setattr(deep_region, "fetch_page_text", fake_fetch)
    cache = DeepRegionCache()

    first = deep_check(source="gh-testco", external_id="gh-4", url="https://testco.example-corp.io/4",
                        title="QA Engineer", location="Remote", cache=cache, company="Test Co")
    second = deep_check(source="gh-testco", external_id="gh-4", url="https://testco.example-corp.io/4",
                         title="QA Engineer", location="Remote", cache=cache, company="Test Co")

    assert first.region == "Worldwide" and second.region == "Worldwide"
    assert len(calls) == 1  # fetched once only

    # A fresh cache instance loaded from the same file (e.g. next poll cycle) also hits the cache.
    reloaded = DeepRegionCache()
    third = deep_check(source="gh-testco", external_id="gh-4", url="https://testco.example-corp.io/4",
                        title="QA Engineer", location="Remote", cache=reloaded, company="Test Co")
    assert third.region == "Worldwide"
    assert len(calls) == 1


def test_deep_region_check_disabled_by_setting(monkeypatch, clean_db):
    monkeypatch.setattr(sched.get_settings(), "DEEP_REGION_CHECK_ENABLED", False)  # overrides the autouse fixture
    calls = []
    monkeypatch.setattr(deep_region, "fetch_page_text", lambda url, timeout=None: calls.append(url) or "Worldwide!")
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched, "ADAPTERS", [
        Stub([raw(source="gh-testco", external_id="gh-5", title="QA Engineer (bare remote)", company="Test Co",
                   url="https://testco.example-corp.io/5", location="Remote")]),
    ])
    stats = poll_all_sources()
    assert calls == []
    assert stats["deep_region_checked"] == 0
    assert stats["skipped_region_unclear"] == 1
