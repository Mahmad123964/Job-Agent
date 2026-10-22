"""Roadmap item 4: description-based recall for generic titles (loads MiniLM)."""
import pytest

import app.scheduler as sched
from app.scheduler import description_recall_match, poll_all_sources
from app.normalizer import normalize
from tests.conftest import raw
from tests.test_pipeline import Stub, stub_classify

QA_DESC = ("You will own test automation with Playwright and Selenium, build and maintain regression suites, "
           "write test plans and test cases, triage defects in JIRA, run API tests with Postman and drive the QA "
           "process across product squads. Experience with CI pipelines for automated testing required.")
NON_QA = {
    "DevOps Engineer": "Kubernetes, Terraform, CI/CD pipelines, observability with Prometheus and Grafana, on-call rotation, cost optimisation on AWS. " * 2,
    "Frontend Engineer": "React, TypeScript, design systems, accessibility, performance budgets, component libraries and Storybook. " * 2,
    "Software Engineer": "Build React features and Node services. Write unit tests, participate in code review, ship weekly, mentor juniors. " * 2,
    "Data Scientist": "Build ML models in Python and pandas, feature engineering, A/B testing analysis, dashboards for stakeholders. " * 2,
}


@pytest.fixture(scope="module")
def models():
    from app.classifier import _get_models
    m = _get_models()
    if m is None:
        pytest.skip("Hugging Face models unavailable")
    return m


def test_generic_title_with_qa_description_is_admitted(models):
    job = normalize(raw(title="Software Engineer", description=QA_DESC))
    m = description_recall_match(job)
    assert m and m.startswith("desc:") and float(m[5:]) >= 0.70


@pytest.mark.parametrize("title,desc", list(NON_QA.items()))
def test_non_qa_descriptions_are_not_admitted(models, title, desc):
    assert description_recall_match(normalize(raw(title=title, description=desc))) is None


def test_non_engineering_titles_never_use_the_path(models):
    assert description_recall_match(normalize(raw(title="Product Manager", description=QA_DESC))) is None
    assert description_recall_match(normalize(raw(title="Supplier Quality Engineer", description=QA_DESC))) is None


def test_recall_path_end_to_end(models, monkeypatch, clean_db):
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched, "ADAPTERS", [Stub([
        raw(external_id="se", title="Software Engineer", company="Acme", url="https://acme.example-corp.io/se", description=QA_DESC),
        raw(external_id="devops", title="DevOps Engineer", company="Beta", url="https://beta.example-corp.io/do", description=NON_QA["DevOps Engineer"]),
    ])])
    stats = poll_all_sources()
    assert stats["saved"] == 1 and stats["skipped_no_keyword"] == 1
    from app.models import Job
    job = clean_db.query(Job).one()
    assert job.title == "Software Engineer" and job.matched_keyword.startswith("desc:")
