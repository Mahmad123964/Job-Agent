"""Pipeline counter invariant + API smoke (audit Phases 2/3/12).

The classifier is replaced by a cheap deterministic stub so these tests run
without loading any Hugging Face model; region/eligibility still comes from
the real rule engine (geo.py) via the stub.
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.scheduler as sched
from app.adapters.base import BaseAdapter
from app.geo import extract_eligibility
from app.scheduler import NOTIFY_OUTCOMES, OUTCOMES, poll_all_sources
from tests.conftest import raw

NOW = datetime.now(timezone.utc)


def stub_classify(job, resume_text=None):
    where = extract_eligibility(job.location, job.description, job.title)
    return {
        "is_sqa_relevant": True, "relevance_score": 1.0, "seniority": "mid", "is_remote": True,
        "is_worldwide": where.region == "Worldwide", "pakistan_eligible": where.region in ("Worldwide", "Asia-Pacific") or where.country == "Pakistan",
        "eligible_region": where.region, "eligible_country": where.country, "required_skills": [], "resume_match_score": None, "backend": "stub",
    }


class Stub(BaseAdapter):
    name = "stub"

    def __init__(self, jobs):
        self.jobs = jobs

    def fetch(self):
        return list(self.jobs)


@pytest.fixture()
def pipeline(monkeypatch, clean_db):
    sent = []
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: (sent.append(job.title), ["email"])[1])
    return sent


def assert_sums(stats):
    assert stats["fetched"] == sum(stats[k] for k in OUTCOMES), stats
    assert stats["saved"] == sum(stats[k] for k in NOTIFY_OUTCOMES), stats


def test_every_fetched_job_lands_in_exactly_one_bucket(pipeline, monkeypatch):
    jobs = [
        raw(external_id="ok", title="QA Engineer", posted_at=NOW - timedelta(days=2)),                       # saved + notified
        raw(external_id="old", title="QA Engineer (old)", posted_at=NOW - timedelta(days=30)),               # too old
        raw(external_id="undated", title="QA Engineer (undated)", posted_at=None),                           # undated
        raw(external_id="nokw", title="Product Manager"),                                                    # no keyword
        raw(external_id="us", title="SDET (US)", company="Uscorp", url="https://uscorp.example-corp.io/9", location="Remote — United States"),  # region restricted
        raw(external_id="unclear", title="Software Tester (bare remote)", company="Vague Co", url="https://vague.example-corp.io/2", location="Remote"),  # unclear -> excluded
        raw(external_id="badurl", title="QA Engineer (bad url)", url="https://example.com/x"),               # invalid url
        raw(external_id="notitle", title="   "),                                                             # invalid
        raw(external_id="dup", title="QA Engineer", company="Acme", url="https://acme.example-corp.io/1"),   # duplicate of "ok" (same url)
    ]
    monkeypatch.setattr(sched, "ADAPTERS", [Stub(jobs)])
    stats = poll_all_sources()
    assert_sums(stats)
    assert stats["fetched"] == 9
    assert {k: v for k, v in stats.items() if v} == {
        "fetched": 9, "saved": 1, "notified": 1, "skipped_too_old": 1, "skipped_undated": 1, "skipped_no_keyword": 1,
        "skipped_region_restricted": 1, "skipped_region_unclear": 1, "skipped_invalid_url": 1, "skipped_invalid": 1, "skipped_duplicate": 1,
    }
    assert pipeline == ["QA Engineer"]


def test_second_poll_is_all_duplicates_and_sends_nothing(pipeline, monkeypatch):
    titles = ["QA Engineer", "SDET", "Software Tester", "Test Engineer", "QA Automation Engineer"]
    jobs = [raw(external_id=str(i), title=t, company=f"Company {i}", url=f"https://c{i}.example-corp.io/{i}") for i, t in enumerate(titles)]
    monkeypatch.setattr(sched, "ADAPTERS", [Stub(jobs)])
    first = poll_all_sources(); second = poll_all_sources()
    assert_sums(first); assert_sums(second)
    assert first["saved"] == first["notified"] == 5
    assert second["skipped_duplicate"] == 5 and second["saved"] == 0 and len(pipeline) == 5


def test_adapter_exception_is_isolated(pipeline, monkeypatch):
    class Boom(BaseAdapter):
        name = "boom"

        def fetch(self):
            raise RuntimeError("feed down")

    good = Stub([raw(external_id="g", title="SDET")])
    monkeypatch.setattr(sched, "ADAPTERS", [Boom(), good])
    stats = poll_all_sources()
    assert_sums(stats)
    assert stats["saved"] == 1


def test_poll_run_row_is_persisted(pipeline, monkeypatch, db):
    from app.models import PollRun
    monkeypatch.setattr(sched, "ADAPTERS", [Stub([raw(external_id="p", title="Test Engineer")])])
    poll_all_sources()
    run = db.query(PollRun).order_by(PollRun.id.desc()).first()
    assert run.fetched == 1 and run.saved == 1 and run.notified == 1 and run.per_source == {"stub": 1}


# ---------------------------------------------------------------- API smoke
def test_api_smoke(pipeline, monkeypatch, client):
    monkeypatch.setattr(sched, "ADAPTERS", [Stub([raw(external_id="api", title="QA Automation Engineer", description="Playwright")])])
    poll_all_sources()
    h = client.get("/api/health"); assert h.status_code == 200
    assert {"status", "db", "active_jobs", "warming_up", "sources", "classifier_backend"} <= set(h.json())
    jobs = client.get("/api/jobs"); assert jobs.status_code == 200
    row = jobs.json()[0]
    assert {"id", "title", "company", "url", "score", "eligible_region", "application_status", "age_category"} <= set(row)
    assert "description" not in row
    assert "description" in client.get(f"/api/jobs/{row['id']}").json()
    notes = client.get("/api/notifications"); assert notes.status_code == 200 and notes.json()[0]["notified"] is True
    assert client.get("/api/applied").status_code == 200
    assert client.get("/api/analytics").status_code == 200
    assert client.get("/api/archived").status_code == 200
