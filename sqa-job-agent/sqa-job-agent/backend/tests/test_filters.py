"""Keyword filter battery (audit Phase 6). Title-driven, IC roles only."""
import pytest

from app.config import get_settings
from app.filters import matches_keywords, title_exclusion
from tests.conftest import raw

KWS = get_settings().DEFAULT_KEYWORDS


def match(title, tags=(), description=""):
    return matches_keywords(raw(title=title, tags=list(tags), description=description), KWS)


# The 20 canonical SQA titles from the audit. Two are excluded by policy
# (analyst-level roles are out of scope) and are asserted as non-matches below.
SHOULD_MATCH = [
    "QA Engineer", "Software QA Engineer", "Quality Assurance Engineer", "Software Tester", "Test Engineer",
    "QA Automation Engineer", "SDET", "Test Automation Engineer", "Manual QA Tester", "Automation Tester",
    "Playwright QA Engineer", "Selenium Automation Engineer", "API Test Engineer", "Quality Engineer (Software)",
]
# Audit-identified keyword-list gaps, closed by roadmap item 4 (DEFAULT_KEYWORDS additions).
KEYWORD_GAPS = ["Junior QA", "QA Specialist", "Automation QA", "Tester"]
EXCLUDED_BY_POLICY = ["QA Analyst", "Software Quality Analyst"]

SHOULD_NOT_MATCH = [
    "Product Manager", "Sales Manager", "Marketing Manager", "HR Manager", "Data Scientist", "DevOps Engineer",
    "Network Engineer", "Customer Support Specialist", "Hardware Test Technician", "Quality Assurance Rater",
    "Supplier Quality Engineer", "Data Quality Analyst", "QA Lead", "Principal Quality Engineer",
    "External Marker - Payroll", "Test Kitchen Manager", "Software Engineer",
]


@pytest.mark.parametrize("title", SHOULD_MATCH)
def test_canonical_titles_match(title):
    assert match(title), f"{title!r} should match"


@pytest.mark.parametrize("title", KEYWORD_GAPS)
def test_keyword_gap_titles(title):
    assert match(title)


def test_generic_engineering_title_detector():
    from app.filters import is_generic_engineering_title as g
    assert g("Software Engineer") and g("Backend Developer") and g("Full-Stack Engineer") and g("Engineer")
    assert not g("Product Manager") and not g("Supplier Quality Engineer") and not g("Engineering Manager")


@pytest.mark.parametrize("title", EXCLUDED_BY_POLICY)
def test_analyst_titles_excluded_by_policy(title):
    assert match(title) is None
    assert title_exclusion(title) == "level:analyst"


@pytest.mark.parametrize("title", SHOULD_NOT_MATCH)
def test_negatives_do_not_match(title):
    assert match(title) is None, f"{title!r} must not match"


def test_level_words_exclude_even_with_qa_term():
    for title in ("LawnStarter: Principal Quality Engineer", "SQA Lead", "Staff Quality Engineer", "Head of QA", "QA Engineering Manager"):
        assert match(title) is None


def test_tags_only_help_technical_titles():
    assert match("External Marker - Payroll", tags=["Education", "Quality-Assurance-In-Education"]) is None
    assert match("AI Response Analyst", tags=["quality assurance"]) is None
    assert match("Marketing Coordinator", tags=["qa automation"]) is None
    assert match("Backend Engineer", tags=["qa automation"]) == "tag:qa automation"


def test_word_boundaries():
    assert match("Sales Manager, Qatar") is None      # "qa" must not match inside "Qatar"
    assert match("Contest Marketing Lead") is None    # "test" must not match inside "Contest"
    assert match("QA/Engineer (Remote)") == "qa engineer"
