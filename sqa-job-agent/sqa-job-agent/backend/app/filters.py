"""Keyword filter: decides which fetched postings are QA / SQA / test roles.

Scope (title-driven, individual contributors only): QA Engineer, SQA Engineer,
Automation Engineer, Software Tester, Test Engineer, SDET, QA Automation
Engineer and close variants — roles centred on *software* testing. Lead /
Principal / Staff / Manager / Head / Director / Analyst titles are out.

Why the rules look the way they do (all from real false positives):
  * Tags are no longer a match on their own. "External Marker - Payroll"
    (exam marking) matched because a Himalayas tag read
    "Quality-Assurance-In-Education"; three RemoteOK jobs matched on a bare
    "quality assurance" tag. In the whole database, tag-only matches produced
    0 true positives. A tag now only helps when it is an unambiguous QA tag
    AND the title itself reads like an engineering/testing role.
  * Generic keywords ("quality assurance", "quality engineer") need a software
    context word in the title: "Supplier Quality Engineer" (manufacturing),
    "Quality Assurance Rater" (search rating) and "Civil Engineering QA Lead"
    are out; "Senior Quality Assurance Engineer" / "Quality Assurance Analyst"
    stay in.
  * Industry exclusions on the title win over everything.
"""
import re
from functools import lru_cache
from typing import List, Optional

from .adapters.base import RawJob

# Titles containing these are never software QA, whatever else they say.
_EXCLUDED_TITLE_TERMS = (
    "supplier", "manufacturing", "mechanical", "electrical", "hardware", "civil", "construction", "welding",
    "food", "pharma", "pharmaceutical", "clinical", "laboratory", "lab technician", "chemist", "gmp",
    "audit", "auditor", "assurance associate", "rater", "annotator", "transcription", "call center", "call centre",
    "customer support", "customer service", "data quality", "payroll", "marker", "proofreader", "warehouse",
    "inspector", "inspection", "quality control", "qc technician", "nurse", "healthcare quality", "compliance officer",
    "ordnance", "aerospace", "automotive", "textile", "packaging",
)
# Seniority / management signals: the agent targets individual-contributor
# roles only, so these exclude a title even when it also has a valid QA term
# ("LawnStarter: Principal Quality Engineer", "QA Lead", "QA Analyst").
_EXCLUDED_LEVEL_TERMS = (
    "lead", "principal", "staff", "manager", "head of", "head", "director", "vp", "vice president", "chief",
    "architect", "analyst", "supervisor", "coordinator", "team lead", "tech lead", "engineering manager",
)
_LEVEL_PATTERN = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in _EXCLUDED_LEVEL_TERMS) + r")(?![a-z0-9])", re.I)

# Words that make a generic "quality ..." keyword read as a software role.
_SOFTWARE_CONTEXT = ("software", "engineer", "engineering", "testing", "tester", "qa", "sqa", "sdet",
                     "automation", "developer", "dev")
# Keywords that are specific enough on their own.
_STRONG_KEYWORDS = {"qa engineer", "sqa", "sdet", "test engineer", "software tester", "automation tester",
                    "qa tester", "qa automation", "sqa engineer", "qa automation engineer",
                    "software quality assurance", "test automation", "automation engineer", "qa specialist"}
# Tags that are unambiguous QA tags (case-insensitive, after normalising separators).
_STRONG_TAGS = {"qa", "sqa", "sdet", "qa engineer", "qa automation", "test automation", "software testing",
                "software qa", "qa testing", "qa & testing", "quality assurance engineer"}


@lru_cache(maxsize=256)
def _keyword_pattern(keyword: str) -> re.Pattern:
    """Whole-word, case-insensitive match for a (possibly multi-word) keyword.
    Internal whitespace matches whitespace, "/" or "-" so "qa engineer" also
    hits "QA/Engineer" and "QA-Engineer"."""
    parts = [re.escape(p) for p in keyword.lower().split()]
    body = r"[\s/\-]+".join(parts)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.IGNORECASE)


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def title_exclusion(title: str) -> Optional[str]:
    """The excluded industry term or seniority/level term found in the title, or None."""
    t = (title or "").lower()
    for term in _EXCLUDED_TITLE_TERMS:
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", t):
            return term
    m = _LEVEL_PATTERN.search(t)
    if m:
        return f"level:{m.group(0)}"
    return None


def _has_software_context(title: str) -> bool:
    return bool(_tokens(title) & set(_SOFTWARE_CONTEXT))


def matches_keywords(job: RawJob, keywords: List[str]) -> Optional[str]:
    """Return the matched keyword, or None.

    1. Title exclusions (manufacturing, audit, raters, payroll, ...) -> None.
    2. Title keyword match: strong keywords match as-is; generic ones
       ("quality assurance", "quality engineer") also need a software-context
       word in the title.
    3. Tag match only for unambiguous QA tags, and only if the title reads
       like an engineering/testing role. Description is never used.
    """
    title = job.title or ""
    if title_exclusion(title):
        return None
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        if _keyword_pattern(kw).search(title):
            if kw.lower() in _STRONG_KEYWORDS or _has_software_context(title):
                return kw
    # tags: unambiguous QA tags with a title that already looks technical
    if _has_software_context(title):
        for tag in job.tags or []:
            norm = " ".join(re.split(r"[\s/_\-]+", (tag or "").strip().lower()))
            if norm in _STRONG_TAGS:
                return f"tag:{norm}"
    return None


_GENERIC_ENGINEERING = re.compile(
    r"(?<![a-z0-9])(?:software|full[\s-]?stack|backend|back-end|frontend|front-end|web|mobile|platform|application|"
    r"applications|python|java|javascript|typescript|\.net|node|react)?\s*(?:engineer|developer|programmer|engineering)"
    r"(?![a-z0-9])",
    re.I,
)


def is_generic_engineering_title(title: str) -> bool:
    """Titles like "Software Engineer", "Backend Developer" — no QA term, but the
    role could still be a QA role that the description reveals. Excluded
    industries/levels stay excluded."""
    if title_exclusion(title):
        return False
    return bool(_GENERIC_ENGINEERING.search(title or ""))


def matches_location(job: RawJob, allowed_locations: List[str]) -> bool:
    if not allowed_locations:
        return True
    loc = (job.location or "").lower()
    return any(al.lower() in loc for al in allowed_locations) or "remote" in loc or "worldwide" in loc
