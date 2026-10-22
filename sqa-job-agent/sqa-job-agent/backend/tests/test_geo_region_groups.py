"""Region-group acronyms (EMEA/APAC/LATAM) map to the fixed region set geo.py
already uses elsewhere. There's no MENA/EMEA bucket in REGIONS, so each acronym
resolves to its nearest existing category -- same precedent as "Middle East"
already mapping to Asia-Pacific (see the comment above _REGION_TERMS).
"""
import pytest

from app.geo import extract_eligibility


@pytest.mark.parametrize("location,description,title,expected_region", [
    ("EMEA", "", "", "Europe"),
    ("Remote", "Only EMEA candidates will be considered.", "", "Europe"),
    ("Remote", "This role is open to EMEA applicants only.", "", "Europe"),
    ("APAC", "", "", "Asia-Pacific"),
    ("Remote", "This role is open to APAC only.", "", "Asia-Pacific"),
    ("Remote", "Hiring across the APAC region.", "", "Asia-Pacific"),
    ("LATAM", "", "", "Americas"),
    ("Remote", "Hiring exclusively from LATAM.", "", "Americas"),
    ("Remote", "Open to candidates in LATAM only.", "", "Americas"),
])
def test_region_group_acronyms_resolve(location, description, title, expected_region):
    result = extract_eligibility(location, description, title)
    assert result.region == expected_region, result
