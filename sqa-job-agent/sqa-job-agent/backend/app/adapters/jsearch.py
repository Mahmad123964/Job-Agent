"""
Why this file exists instead of direct LinkedIn/Indeed/Glassdoor scrapers:

Those platforms publish no public job-search API and their Terms of Service
prohibit automated scraping/bots. This adapter instead calls JSearch (via
RapidAPI), a licensed service that re-indexes listings from those platforms
(and others) through data-sharing agreements — so you get that coverage
without touching linkedin.com/indeed.com/glassdoor.com directly.

Disabled by default. To enable: set JSEARCH_ENABLED=true and RAPIDAPI_KEY in
your .env (free tier available at rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch).

Endpoint: GET /search-v2 (the older /search now returns 404). The v2 response
nests results as {"data": {"jobs": [...], "cursor": "..."}} — the v1 shape was
{"data": [...]} — while the per-job field names are unchanged.
"""
import logging
from collections import Counter
from typing import List, Optional

import requests

from .base import BaseAdapter, RawJob, parse_datetime, salary_to_annual, strip_html
from ..config import get_settings

_API = "https://jsearch.p.rapidapi.com/search-v2"
logger = logging.getLogger("sqa_job_agent.adapters.jsearch")


def is_trusted_publisher(publisher: Optional[str], allowlist: List[str]) -> bool:
    """JSearch's `job_publisher` names the site the listing was indexed from
    (also mirrored in apply_options[].publisher and the apply-link host). It is
    the only reliable provenance field — `employer_website` is the hiring
    company, not the board. Empty allowlist keeps everything."""
    if not allowlist:
        return True
    name = " ".join((publisher or "").lower().split())
    return any(name == a.lower() or name.startswith(a.lower() + " ") for a in allowlist)


class JSearchAdapter(BaseAdapter):
    name = "jsearch"
    access_method = "api (licensed aggregator)"
    attribution = "Source: JSearch (RapidAPI) — aggregated listings, licensed re-index of Indeed/LinkedIn/Glassdoor/etc."

    @staticmethod
    def _location(item: dict) -> str:
        """Prefer concrete city/state/country; JSearch's 'Anywhere' just means
        remote, so it's left as plain 'Remote' and the description's own
        restriction text (e.g. 'US only') decides eligibility in geo.py."""
        parts = [item.get("job_city"), item.get("job_state"), item.get("job_country")]
        concrete = ", ".join(str(p) for p in parts if p)
        if concrete:
            return f"Remote - {concrete}" if item.get("job_is_remote") else concrete
        loc = (item.get("job_location") or "").strip()
        if loc and loc.lower() not in ("anywhere", "remote"):
            return loc
        return "Remote"

    # Filled by parse(): how many listings the publisher allowlist dropped, by publisher.
    last_excluded: Counter = Counter()

    @staticmethod
    def parse(payload: dict, source: str = "jsearch", trusted: Optional[List[str]] = None) -> List[RawJob]:
        """Map a /search-v2 payload to RawJobs. Separate from fetch() so the
        mapping can be tested against a saved response without spending quota.
        Listings from publishers outside JSEARCH_TRUSTED_PUBLISHERS are dropped
        and counted in JSearchAdapter.last_excluded."""
        trusted = get_settings().JSEARCH_TRUSTED_PUBLISHERS if trusted is None else trusted
        data = payload.get("data") or {}
        items = data.get("jobs", []) if isinstance(data, dict) else data  # v2 dict / v1 list
        jobs: List[RawJob] = []
        excluded: Counter = Counter()
        for item in items:
            if not isinstance(item, dict) or not item.get("job_id"):
                continue
            publisher = item.get("job_publisher") or ""
            if not is_trusted_publisher(publisher, trusted):
                excluded[publisher or "(none)"] += 1
                continue
            salary_min, salary_max = salary_to_annual(
                item.get("job_min_salary"), item.get("job_max_salary"), item.get("job_salary_period")
            )
            tags = [t for t in (item.get("job_publisher"), item.get("job_employment_type")) if t]
            jobs.append(
                RawJob(
                    source=source,
                    external_id=str(item["job_id"]),
                    title=item.get("job_title", ""),
                    company=item.get("employer_name", ""),
                    url=item.get("job_apply_link") or item.get("job_google_link") or "",
                    location=JSearchAdapter._location(item),
                    tags=tags,
                    posted_at=parse_datetime(item.get("job_posted_at_datetime_utc") or item.get("job_posted_at_timestamp")),
                    description=strip_html(item.get("job_description")),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    # v2 carries no currency field; figures are in the search country's currency.
                    salary_currency=("USD" if get_settings().JSEARCH_COUNTRY.lower() == "us" else None)
                    if (salary_min or salary_max) else None,
                )
            )
        JSearchAdapter.last_excluded = excluded
        if excluded:
            logger.info("JSearch: dropped %d listing(s) from untrusted publishers: %s",
                        sum(excluded.values()), ", ".join(f"{k}={v}" for k, v in excluded.most_common()))
        return jobs

    def fetch(self) -> List[RawJob]:
        settings = get_settings()
        if not settings.JSEARCH_ENABLED or not settings.RAPIDAPI_KEY:
            return []

        resp = requests.get(
            _API,
            params={
                "query": settings.JSEARCH_QUERY,
                "num_pages": "1",
                "country": settings.JSEARCH_COUNTRY,
                "date_posted": settings.JSEARCH_DATE_POSTED,  # all|month|week|3days|today
            },
            headers={
                "X-RapidAPI-Key": settings.RAPIDAPI_KEY,  # [API_KEYS]
                "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
            },
            timeout=20,
        )
        resp.raise_for_status()
        return self.parse(resp.json(), self.name)
