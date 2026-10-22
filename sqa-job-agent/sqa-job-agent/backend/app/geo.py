"""Rule-based extraction of the region / country a remote job is open to.

Follows the same pattern as `classifier.pakistan_eligible`: explicit
statements in the location field win, then restriction phrases in the
description ("must be based in Germany", "US residents only", "EST timezone"),
then broad "anywhere" language. Pure functions, no models — the zero-shot
fallback lives in classifier.py.

    eligible_region  : Worldwide | Americas | Europe | Asia-Pacific | Africa
                       | Specific country | Unclear
    eligible_country : canonical country name when exactly one country is
                       named as the requirement, else None
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

REGIONS = ("Worldwide", "Americas", "Europe", "Asia-Pacific", "Africa", "Specific country", "Unclear")

# canonical name -> (region, aliases). Aliases are matched case-insensitively
# as whole tokens; dots are literal so "u.s." works.
_COUNTRIES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "United States": ("Americas", ("usa", "us", "u.s.", "u.s.a.", "united states", "united states of america",
                                   "us time zone", "us time zones", "us timezone", "us timezones", "u.s. time zones",
                                   "u.s. timezone", "us hours", "us business hours", "us work authorization",
                                   "us work authorisation", "u.s. work authorization", "us citizen", "us citizens",
                                   "u.s. citizen", "us person", "w-2", "w2 employee")),
    "Canada": ("Americas", ("canada", "canadian")),
    "Mexico": ("Americas", ("mexico",)),
    "Brazil": ("Americas", ("brazil", "brasil")),
    "Argentina": ("Americas", ("argentina",)),
    "Colombia": ("Americas", ("colombia",)),
    "Chile": ("Americas", ("chile",)),
    "Peru": ("Americas", ("peru",)),
    "Uruguay": ("Americas", ("uruguay",)),
    "Costa Rica": ("Americas", ("costa rica",)),
    "United Kingdom": ("Europe", ("uk", "u.k.", "united kingdom", "great britain", "britain", "england", "scotland", "wales")),
    "Ireland": ("Europe", ("ireland",)),
    "Germany": ("Europe", ("germany", "deutschland")),
    "France": ("Europe", ("france",)),
    "Spain": ("Europe", ("spain",)),
    "Portugal": ("Europe", ("portugal",)),
    "Italy": ("Europe", ("italy",)),
    "Netherlands": ("Europe", ("netherlands", "the netherlands", "holland")),
    "Belgium": ("Europe", ("belgium",)),
    "Switzerland": ("Europe", ("switzerland",)),
    "Austria": ("Europe", ("austria",)),
    "Poland": ("Europe", ("poland",)),
    "Czechia": ("Europe", ("czechia", "czech republic")),
    "Slovakia": ("Europe", ("slovakia",)),
    "Hungary": ("Europe", ("hungary",)),
    "Romania": ("Europe", ("romania",)),
    "Bulgaria": ("Europe", ("bulgaria",)),
    "Greece": ("Europe", ("greece",)),
    "Croatia": ("Europe", ("croatia",)),
    "Serbia": ("Europe", ("serbia",)),
    "Slovenia": ("Europe", ("slovenia",)),
    "Ukraine": ("Europe", ("ukraine",)),
    "Sweden": ("Europe", ("sweden",)),
    "Norway": ("Europe", ("norway",)),
    "Denmark": ("Europe", ("denmark",)),
    "Finland": ("Europe", ("finland",)),
    "Estonia": ("Europe", ("estonia",)),
    "Latvia": ("Europe", ("latvia",)),
    "Lithuania": ("Europe", ("lithuania",)),
    "Turkey": ("Europe", ("turkey", "türkiye")),
    "Cyprus": ("Europe", ("cyprus",)),
    "Malta": ("Europe", ("malta",)),
    "Pakistan": ("Asia-Pacific", ("pakistan",)),
    "India": ("Asia-Pacific", ("india",)),
    "Bangladesh": ("Asia-Pacific", ("bangladesh",)),
    "Sri Lanka": ("Asia-Pacific", ("sri lanka",)),
    "Nepal": ("Asia-Pacific", ("nepal",)),
    "Philippines": ("Asia-Pacific", ("philippines",)),
    "Indonesia": ("Asia-Pacific", ("indonesia",)),
    "Vietnam": ("Asia-Pacific", ("vietnam",)),
    "Thailand": ("Asia-Pacific", ("thailand",)),
    "Malaysia": ("Asia-Pacific", ("malaysia",)),
    "Singapore": ("Asia-Pacific", ("singapore",)),
    "Japan": ("Asia-Pacific", ("japan",)),
    "South Korea": ("Asia-Pacific", ("south korea", "korea")),
    "China": ("Asia-Pacific", ("china",)),
    "Taiwan": ("Asia-Pacific", ("taiwan",)),
    "Hong Kong": ("Asia-Pacific", ("hong kong",)),
    "Australia": ("Asia-Pacific", ("australia",)),
    "New Zealand": ("Asia-Pacific", ("new zealand",)),
    "United Arab Emirates": ("Asia-Pacific", ("uae", "united arab emirates", "dubai")),
    "Saudi Arabia": ("Asia-Pacific", ("saudi arabia", "ksa")),
    "Israel": ("Asia-Pacific", ("israel",)),
    "Kazakhstan": ("Asia-Pacific", ("kazakhstan",)),
    "Armenia": ("Asia-Pacific", ("armenia",)),
    "South Africa": ("Africa", ("south africa",)),
    "Nigeria": ("Africa", ("nigeria",)),
    "Kenya": ("Africa", ("kenya",)),
    "Egypt": ("Africa", ("egypt",)),
    "Ghana": ("Africa", ("ghana",)),
    "Morocco": ("Africa", ("morocco",)),
    "Tunisia": ("Africa", ("tunisia",)),
    "Ethiopia": ("Africa", ("ethiopia",)),
    "Rwanda": ("Africa", ("rwanda",)),
    "Uganda": ("Africa", ("uganda",)),
}

_US_STATES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
    "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
    "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "west virginia", "wisconsin", "wyoming", "puerto rico", "washington dc", "washington, dc",
    "southern california", "northern california", "bay area", "nyc", "new york city",
)
_COUNTRIES["United States"] = ("Americas", _COUNTRIES["United States"][1] + _US_STATES)

# Region-level words / timezone abbreviations. Middle East is mapped to
# Asia-Pacific because the fixed region set has no MENA bucket.
_REGION_TERMS: Dict[str, Tuple[str, ...]] = {
    "Worldwide": ("worldwide", "anywhere", "anywhere in the world", "work from anywhere", "global remote",
                  "remote global", "remote - global", "remote (global)", "no location restriction",
                  "no location restrictions", "any country", "all countries", "fully remote worldwide"),
    "Europe": ("europe", "european", "eu", "e.u.", "eea", "emea", "cet", "cest", "gmt", "bst", "wet", "eet",
               "european time zone", "european timezone", "european timezones", "european time zones", "uk/eu", "eu/uk"),
    "Americas": ("americas", "north america", "south america", "latin america", "latam", "central america",
                 "est", "edt", "pst", "pdt", "cst", "cdt", "mst", "mdt", "eastern time", "pacific time", "central time",
                 "mountain time", "us/canada", "usa/canada", "north american time zones"),
    "Asia-Pacific": ("asia", "apac", "asia pacific", "asia-pacific", "south asia", "southeast asia", "south east asia",
                     "east asia", "oceania", "anz", "australia/new zealand", "middle east", "mena", "gcc", "asean"),
    "Africa": ("africa", "african", "sub-saharan africa"),
}

# Restriction phrases in free text. `{X}` is filled with the country/region alternation.
_RESTRICTION_TEMPLATES = (
    r"(?:must|need to|needs to|required to|should|have to|has to)\s+(?:be\s+)?(?:physically\s+|currently\s+|legally\s+)?"
    r"(?:located|based|residing|living|reside|live|situated|domiciled)\s+(?:in|within)\s+{X}",
    r"(?:only|exclusively)\s+(?:for\s+|open\s+to\s+|available\s+to\s+|hiring\s+(?:in|from)\s+|accepting\s+)?"
    r"(?:candidates|applicants|people|residents|citizens|those)?\s*(?:in|from|within|based\s+in|located\s+in)?\s*{X}",
    r"{X}[\s-]*(?:only|based|residents?|citizens?|based\s+only|candidates\s+only|applicants\s+only|residents?\s+only)",
    r"(?:authori[sz]ed|eligible|legally\s+(?:able|allowed|permitted|entitled)|right|permission|permit)\s+to\s+work\s+(?:in|within)\s+{X}",
    r"(?:candidates|applicants|hiring|open\s+to\s+candidates|we\s+hire|we\s+are\s+hiring)\s+(?:in|from|within|across)\s+{X}",
    r"{X}\s+(?:time\s*zones?|timezones?|working\s+hours|business\s+hours)",
    r"(?:within|overlap(?:ping)?\s+with)\s+{X}\s+(?:time\s*zones?|timezones?|hours)",
    r"(?:work\s+)?(?:authori[sz]ation|permit|visa)\s+(?:for|in)\s+{X}",
    r"(?:location|region|country|geo|eligible\s+(?:countries|locations|regions))\s*:\s*{X}",
    r"{X}\s+work\s+(?:authori[sz]ation|permit|eligibility)",
    r"{X}\s+(?:time\s*zones?|timezones?)\s+(?:required|preferred|only|strongly\s+preferred)",
    r"(?:remote|work)\s*(?:in|within|from)\s+{X}\s+only",
    # "work from anywhere IN THE U.S." is a country restriction, not "anywhere"
    r"anywhere\s+(?:in|within|across)\s+{X}",
)

# Benefits/employment terms that only exist for US-employed staff. A posting
# that advertises them is hiring in the US even if a perks bullet says
# "work from anywhere".
# Citizenship / clearance requirements are de-facto US-only (ITAR, federal
# contracts, DoD work) even when no country is named next to them.
_US_EMPLOYMENT_MARKERS = re.compile(
    r"(?<![a-z0-9])(?:401\(?k\)?|w-?2\s+(?:employee|employment|position)|us\s+payroll|"
    r"(?:us|u\.s\.|united states)\s+citizenship|citizenship\s+(?:is\s+)?required|"
    r"(?:active\s+|current\s+)?(?:security|secret|top\s+secret|ts/sci|dod|government)\s+clearance|"
    r"clearance\s+(?:is\s+)?required|public\s+trust|itar)(?![a-z0-9])",
    re.I,
)


def _alt(terms) -> str:
    # Longest first so "united states" wins over "us"; dots escaped.
    return "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))


def _bounded(body: str) -> str:
    return rf"(?<![a-z0-9]){body}(?![a-z0-9])"


_COUNTRY_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (name, re.compile(_bounded(_alt(aliases)), re.I)) for name, (_, aliases) in _COUNTRIES.items()
]
_REGION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (region, re.compile(_bounded(_alt(terms)), re.I)) for region, terms in _REGION_TERMS.items()
]
_ALL_COUNTRY_ALT = _alt([a for _, (_, aliases) in _COUNTRIES.items() for a in aliases])
_ALL_REGION_ALT = _alt([t for terms in _REGION_TERMS.values() for t in terms])
_ANY_PLACE = rf"(?:the\s+)?(?P<place>{_ALL_COUNTRY_ALT}|{_ALL_REGION_ALT})"
_RESTRICTION_PATTERNS = [re.compile(_bounded(t.replace("{X}", _ANY_PLACE)), re.I) for t in _RESTRICTION_TEMPLATES]

# Strong "hire from anywhere" phrases for description text. Deliberately
# excludes bare "global"/"worldwide"/"international" — nearly every job ad
# says "global team" or "worldwide leader" without meaning it hires anywhere.
_WORLDWIDE_TEXT_PHRASES = (
    "anywhere in the world", "from anywhere", "work from anywhere", "any country", "all countries",
    "no location restriction", "no location restrictions", "hire globally", "hire worldwide", "hiring worldwide",
    "hiring globally", "open worldwide", "remote worldwide", "worldwide remote", "candidates worldwide",
    "applicants worldwide", "location: worldwide", "location: anywhere", "regardless of location",
    "regardless of where you live", "wherever you are",
)

# Location-field values that carry no geographic information at all.
_LOCATION_NOISE = re.compile(r"^(remote|fully remote|100% remote|work from home|wfh|home|n/a|tbd|flexible|-)?[\s,./()-]*$", re.I)


@dataclass(frozen=True)
class Eligibility:
    region: str                    # one of REGIONS
    country: Optional[str] = None  # canonical name, only with region == "Specific country"
    evidence: str = ""             # what matched, for logs/debugging


def country_region(name: str) -> Optional[str]:
    entry = _COUNTRIES.get(name)
    return entry[0] if entry else None


def find_countries(text: str) -> List[str]:
    """Canonical names of every country mentioned in `text`, in table order."""
    return [name for name, pattern in _COUNTRY_PATTERNS if pattern.search(text)]


def find_regions(text: str) -> List[str]:
    return [region for region, pattern in _REGION_PATTERNS if pattern.search(text)]


def _resolve_places(countries: List[str], regions: List[str], evidence: str) -> Optional[Eligibility]:
    """Turn a set of mentioned places into one Eligibility, or None if ambiguous/empty."""
    if "Worldwide" in regions:
        return Eligibility("Worldwide", None, evidence)
    if len(countries) == 1 and not regions:
        return Eligibility("Specific country", countries[0], evidence)
    spanned = {country_region(c) for c in countries} | {r for r in regions if r != "Worldwide"}
    spanned.discard(None)
    if len(spanned) == 1:
        return Eligibility(spanned.pop(), None, evidence)
    return None


def from_location(location: str) -> Optional[Eligibility]:
    """Structured guess from the location field alone ("USA", "Canada, USA", "CET +/- 3")."""
    loc = (location or "").strip()
    if not loc or _LOCATION_NOISE.match(loc):
        return None
    countries = find_countries(loc)
    regions = find_regions(loc)
    if not countries and not regions:
        return None
    resolved = _resolve_places(countries, regions, f"location={loc!r}")
    if resolved:
        return resolved
    # Several countries across regions, e.g. "USA, UK, India" -> not one region, not one country.
    return Eligibility("Unclear", None, f"location={loc!r} spans {sorted({country_region(c) for c in countries} | set(regions))}")


def from_text(description: str) -> Optional[Eligibility]:
    """Restriction phrases in the description ("must be based in Germany", "EST time zone")."""
    text = " ".join((description or "").split())
    if not text:
        return None
    hits: List[Tuple[str, str]] = []  # (place string, matched snippet)
    for pattern in _RESTRICTION_PATTERNS:
        for match in pattern.finditer(text):
            hits.append((match.group("place"), match.group(0)))
    marker = _US_EMPLOYMENT_MARKERS.search(text)
    if marker:
        hits.append(("united states", marker.group(0)))
    if not hits:
        # No restriction language; broad "anywhere" language still counts as a positive signal.
        for term in _WORLDWIDE_TEXT_PHRASES:
            if re.search(_bounded(re.escape(term)), text, re.I):
                return Eligibility("Worldwide", None, f"text={term!r}")
        return None

    places = " , ".join(place for place, _ in hits)
    resolved = _resolve_places(find_countries(places), find_regions(places), f"text={hits[0][1][:80]!r}")
    if resolved:
        return resolved
    return Eligibility("Unclear", None, f"text mentions several regions: {hits[0][1][:80]!r}")


_TITLE_SEGMENT = re.compile(r"\(([^)]*)\)|(?:\s[-–—|:/]\s|\bremote\b[\s:/-]*)([^()|]*)", re.I)


def from_title(title: str) -> Optional[Eligibility]:
    """Titles often carry the restriction: "QA Engineer (Remote, Illinois & Utah)",
    "Remote/Southern California", "... - Colombia". Only parenthesised parts and
    the text after a separator / the word "remote" are scanned, so a plain
    "QA Engineer" title yields nothing."""
    segments = [g for m in _TITLE_SEGMENT.finditer(title or "") for g in m.groups() if g]
    text = " ; ".join(segments).strip()
    if not text:
        return None
    countries, regions = find_countries(text), find_regions(text)
    if not countries and not regions:
        return None
    resolved = _resolve_places(countries, regions, f"title={text[:60]!r}")
    return resolved or Eligibility("Unclear", None, f"title={text[:60]!r} spans regions")


def extract_eligibility(location: str, description: str, title: str = "") -> Eligibility:
    """Rule-based pass, strict by design:

      Worldwide         only for explicit unrestricted phrasing (location or text)
      Specific country  any single-country/state restriction (location, title or text)
      <region>          multi-country lists within one region, or region words
      Unclear           anything ambiguous — never defaulted to Worldwide

    Order: location field, then title segments, then description phrases.
    """
    return (
        from_location(location)
        or from_title(title)
        or from_text(description)
        or Eligibility("Unclear", None, "no signal")
    )
