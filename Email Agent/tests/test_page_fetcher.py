"""robots.txt precedence: the most specific (longest-path) matching rule must win,
regardless of which order it appears in the file.

Python's stdlib `RobotFileParser.can_fetch()` returns on the *first* matching rule in file
order, so a broad `Allow: /` listed before a narrower `Disallow: /viewjob` — exactly how
Indeed's real robots.txt is written — is misread as permitting /viewjob. These tests pin
that exact rule shape (not a synthetic one) so the bug can't silently reappear.
"""
import urllib.robotparser

from agent.page_fetcher import robots_can_fetch

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobAgentLinkResolver/1.0"

# Trimmed to the shape that actually triggers the bug: a blanket Allow before a more
# specific Disallow, both in the User-agent: * block — verified live against the real
# indeed.com/robots.txt (which lists "Allow: /" first, then "Disallow: /viewjob" and
# "Disallow: /m/viewjob" much further down the same block).
INDEED_LIKE_ROBOTS_TXT = """
User-agent: *
Allow: /
Disallow: /ads/
Disallow: /company/*
Disallow: /m/viewjob
Disallow: /viewjob
"""

# LinkedIn's real robots.txt has no such ordering ambiguity for an unnamed agent — a single
# blanket Disallow: / in the User-agent: * block. The fix must not change this.
LINKEDIN_LIKE_ROBOTS_TXT = """
User-agent: *
Disallow: /
"""


def _parse(text: str) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.strip().splitlines())
    return rp


def test_stdlib_can_fetch_is_wrong_for_this_rule_shape():
    """Documents the bug being fixed: stdlib itself gets this wrong."""
    rp = _parse(INDEED_LIKE_ROBOTS_TXT)
    assert rp.can_fetch(UA, "https://www.indeed.com/viewjob?jk=abc123") is True


def test_longest_match_blocks_viewjob_despite_earlier_broad_allow():
    rp = _parse(INDEED_LIKE_ROBOTS_TXT)
    assert robots_can_fetch(rp, UA, "https://www.indeed.com/viewjob?jk=abc123") is False
    assert robots_can_fetch(rp, UA, "https://www.indeed.com/m/viewjob?jk=abc123") is False
    # country subdomains carry the same robots.txt in practice; the path match doesn't care
    assert robots_can_fetch(rp, UA, "https://pk.indeed.com/viewjob?jk=abc123") is False


def test_longest_match_still_allows_paths_with_no_specific_disallow():
    rp = _parse(INDEED_LIKE_ROBOTS_TXT)
    assert robots_can_fetch(rp, UA, "https://www.indeed.com/jobs?q=qa+engineer") is True
    assert robots_can_fetch(rp, UA, "https://www.indeed.com/") is True


def test_longest_match_still_blocks_disallowed_prefix_not_just_exact_path():
    # Note: stdlib RuleLine matching is a plain startswith(), with no Google-style mid-path
    # `*`/`$` wildcard support (`Disallow: /company/*` only matches a literal "/company/*"
    # path) — that's an existing, separate limitation this fix doesn't change or need to.
    rp = _parse(INDEED_LIKE_ROBOTS_TXT)
    assert robots_can_fetch(rp, UA, "https://www.indeed.com/ads/tracker.gif") is False


def test_linkedins_simple_blanket_disallow_is_unaffected():
    """The one case that already worked correctly must keep working identically."""
    rp = _parse(LINKEDIN_LIKE_ROBOTS_TXT)
    for url in ("https://www.linkedin.com/jobs/view/12345/", "https://www.linkedin.com/"):
        assert rp.can_fetch(UA, url) is False  # stdlib already got this right
        assert robots_can_fetch(rp, UA, url) is False  # and the fix agrees


def test_equal_length_rules_favour_the_least_restrictive_one():
    """Per the documented robots.txt precedence rule: ties go to Allow."""
    rp = _parse("User-agent: *\nDisallow: /x\nAllow: /x\n")
    assert robots_can_fetch(rp, UA, "https://example.com/x") is True


def test_no_matching_rule_defaults_to_allowed():
    rp = _parse(INDEED_LIKE_ROBOTS_TXT)
    assert robots_can_fetch(rp, UA, "https://www.indeed.com/some/unlisted/path") is True
