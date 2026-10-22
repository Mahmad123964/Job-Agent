"""Geo / eligibility battery (audit Phase 7). Rules only — no model involved."""
import pytest

from app.geo import extract_eligibility

# (location, description, expected "Region" or "Region/Country")
LOCATION_CASES = [
    # worldwide — explicit phrasing only
    ("Worldwide", "Worldwide"), ("Anywhere", "Worldwide"), ("Work from anywhere", "Worldwide"),
    ("Any country", "Worldwide"), ("Remote worldwide", "Worldwide"), ("Remote — Worldwide", "Worldwide"),
    ("Remote - Global", "Worldwide"),
    # ambiguous — must NOT become Worldwide
    ("Global", "Unclear"), ("Fully remote", "Unclear"), ("Remote", "Unclear"), ("International", "Unclear"),
    # Pakistan / Asia
    ("Pakistan", "Specific country/Pakistan"), ("Remote - Pakistan", "Specific country/Pakistan"),
    ("Asia", "Asia-Pacific"), ("South Asia", "Asia-Pacific"), ("APAC", "Asia-Pacific"), ("Middle East", "Asia-Pacific"),
    ("Remote Asia", "Asia-Pacific"),
    # country / region restricted
    ("US only", "Specific country/United States"), ("United States only", "Specific country/United States"),
    ("Remote — United States", "Specific country/United States"), ("UK only", "Specific country/United Kingdom"),
    ("EU only", "Europe"), ("Canada only", "Specific country/Canada"), ("Remote — Brazil", "Specific country/Brazil"),
    ("Remote EMEA", "Europe"), ("Remote Americas", "Americas"), ("Remote Europe", "Europe"),
    ("Remote (US timezone preferred)", "Specific country/United States"),
    ("Canada, USA", "Americas"), ("France, Germany, Spain", "Europe"), ("USA, UK, India", "Unclear"),
]

TEXT_CASES = [
    ("US work authorization required", "Specific country/United States"),
    ("Must be eligible to work in the US", "Specific country/United States"),
    ("Applicants must be authorized to work in the United States without sponsorship.", "Specific country/United States"),
    ("Must reside in Brazil.", "Specific country/Brazil"),
    ("Must be based in the EU.", "Europe"),
    ("US timezone required for this role.", "Specific country/United States"),
    ("Work from anywhere in the U.S. with hybrid work in LA.", "Specific country/United States"),
    ("Benefits: 401(k) match. Work from anywhere.", "Specific country/United States"),
    ("We hire from anywhere in the world.", "Worldwide"),
    ("Remote (no location restriction).", "Worldwide"),
    ("We are a global company with offices worldwide. Great team.", "Unclear"),
    ("Only candidates in Europe will be considered.", "Europe"),
    ("Must be a U.S. citizen.", "Specific country/United States"),
]

# Roadmap item 7: citizenship / clearance requirements map to United States.
CITIZENSHIP_CASES = [
    ("US citizenship required.", "Specific country/United States"),
    ("Active security clearance required.", "Specific country/United States"),
    ("Candidates must be able to obtain a Secret clearance.", "Specific country/United States"),
    ("This position is subject to ITAR regulations.", "Specific country/United States"),
    ("Citizenship required for this federal contract.", "Specific country/United States"),
]

TITLE_CASES = [
    ("Software QA Engineer (Remote, Illinois & Utah)", "Specific country/United States"),
    ("Data Quality / QA Engineer Remote (Ohio, USA) Contract", "Specific country/United States"),
    ("QA Manual Test Engineer - AA, Remote: Colombia - Costa Rica", "Americas"),
    ("QA Engineer", "Unclear"),
]


def fmt(e):
    return e.region + (f"/{e.country}" if e.country else "")


@pytest.mark.parametrize("location,expected", LOCATION_CASES)
def test_location_field(location, expected):
    assert fmt(extract_eligibility(location, "", "QA Engineer")) == expected


@pytest.mark.parametrize("text,expected", TEXT_CASES)
def test_description_phrases(text, expected):
    assert fmt(extract_eligibility("Remote", text, "QA Engineer")) == expected


@pytest.mark.parametrize("text,expected", CITIZENSHIP_CASES)
def test_citizenship_and_clearance(text, expected):
    assert fmt(extract_eligibility("Remote", text, "QA Engineer")) == expected


@pytest.mark.parametrize("title,expected", TITLE_CASES)
def test_title_segments(title, expected):
    assert fmt(extract_eligibility("Remote", "", title)) == expected


def test_clearance_job_is_not_notification_eligible():
    from app.scheduler import is_notification_eligible
    e = extract_eligibility("Remote", "Active security clearance required.", "QA Engineer")
    assert not is_notification_eligible({"eligible_region": e.region, "eligible_country": e.country, "pakistan_eligible": False})


@pytest.mark.parametrize("cls,expected", [
    ({"eligible_region": "Worldwide", "pakistan_eligible": True}, True),
    ({"eligible_region": "Specific country", "eligible_country": "Pakistan", "pakistan_eligible": True}, True),
    ({"eligible_region": "Asia-Pacific", "pakistan_eligible": True}, True),
    ({"eligible_region": "Unclear", "pakistan_eligible": False}, False),   # bare "Remote": no positive evidence
    ({"eligible_region": "Unclear", "pakistan_eligible": True}, False),    # even a stale True flag can't rescue Unclear
    ({"eligible_region": "Specific country", "eligible_country": "United States", "pakistan_eligible": False}, False),
    ({"eligible_region": "Europe", "pakistan_eligible": False}, False),
])
def test_notification_gate_requires_positive_evidence(cls, expected):
    from app.scheduler import is_notification_eligible
    assert is_notification_eligible(cls) is expected


def test_explicit_location_wins_over_description():
    assert fmt(extract_eligibility("Worldwide", "Must be located in the US.", "QA")) == "Worldwide"
