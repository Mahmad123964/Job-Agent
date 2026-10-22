"""Lever Postings API — free, no auth, one board ("site") per employer.

    GET https://api.lever.co/v0/postings/<site>?mode=json
    GET https://api.eu.lever.co/v0/postings/<site>?mode=json   (EU-hosted accounts)

Site slugs are CASE-SENSITIVE ("SatelliteInnovations" != "satelliteinnovations").
The primary host is tried first; a 404 there falls back to the EU host, and the
working host is remembered for subsequent polls.

Server-side filters (`team`, `location`, `commitment`) are supported the same way
the Greenhouse/Ashby helpers take their filters — set `query` on the subclass:

    lever_board("lever-acme", "acme", "Acme", query={"team": "Engineering"})

Adding a company is one line (see LEVER_BOARDS below).
"""
import logging
from typing import Dict, List, Optional

import requests

from .base import BaseAdapter, RawJob, parse_datetime, parse_salary_text, strip_html

logger = logging.getLogger("sqa_job_agent.adapters.lever")

_HOSTS = ("https://api.lever.co", "https://api.eu.lever.co")


def _lever_datetime(value):
    """Lever timestamps are epoch MILLISECONDS; parse_datetime expects seconds
    (a raw millis value lands in the year 57000 and is rejected as invalid)."""
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return parse_datetime(value)
    return parse_datetime(millis // 1000 if millis > 10**11 else millis)


class LeverBoardAdapter(BaseAdapter):
    site: str = ""
    company_name: str = ""
    access_method = "api (company job board)"
    source_category = "ats_board"
    timeout = 25
    # Optional server-side filters, e.g. {"team": "Engineering", "location": "Remote",
    # "commitment": "Full-time"}. Lever accepts repeated values for OR semantics.
    query: Optional[Dict[str, str]] = None

    def __init__(self):
        self._host: Optional[str] = None   # remembered after the first successful call

    def _get(self) -> list:
        params = {"mode": "json", **(self.query or {})}
        hosts = [self._host] if self._host else list(_HOSTS)
        last_status = None
        for host in hosts:
            resp = requests.get(
                f"{host}/v0/postings/{self.site}",
                params=params,
                headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"},
                timeout=self.timeout,
            )
            if resp.status_code == 404:
                last_status = 404
                continue
            resp.raise_for_status()
            self._host = host
            return resp.json()
        logger.warning("Lever board %s not found on either host (last status %s)", self.site, last_status)
        return []

    @staticmethod
    def _location(item: dict) -> str:
        categories = item.get("categories") or {}
        parts = [categories.get("location"), categories.get("allLocations") and ", ".join(categories["allLocations"])]
        location = next((p for p in parts if p), "") or ""
        workplace = (item.get("workplaceType") or "").lower()
        if workplace == "remote" and "remote" not in location.lower():
            return f"Remote - {location}" if location else "Remote"
        return location or "Remote"

    def fetch(self) -> List[RawJob]:
        if not self.site:
            return []
        jobs: List[RawJob] = []
        for item in self._get():
            if not isinstance(item, dict) or not item.get("id"):
                continue
            url = item.get("hostedUrl") or item.get("applyUrl") or ""
            if not url:
                continue
            categories = item.get("categories") or {}
            salary_min, salary_max, currency = parse_salary_text(item.get("salaryRange") and str(item["salaryRange"]))
            if item.get("salaryRange"):
                sr = item["salaryRange"]
                salary_min = sr.get("min") or salary_min
                salary_max = sr.get("max") or salary_max
                currency = sr.get("currency") or currency
            tags = [t for t in (categories.get("team"), categories.get("department"), categories.get("commitment")) if t]
            description = strip_html(item.get("descriptionPlain") or item.get("description") or "")
            for section in item.get("lists") or []:
                description += " " + strip_html(f"{section.get('text','')} {section.get('content','')}")
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item["id"]),
                    title=item.get("text", ""),
                    company=self.company_name or self.site,
                    url=url,
                    location=self._location(item),
                    tags=tags,
                    posted_at=_lever_datetime(item.get("createdAt")),  # epoch millis -> seconds
                    description=description.strip(),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=currency,
                )
            )
        return jobs


def lever_board(name: str, site: str, company: str, query: Optional[Dict[str, str]] = None) -> LeverBoardAdapter:
    """One line per company board; every site below verified live (200 + postings)."""
    return type(
        f"Lever_{name}",
        (LeverBoardAdapter,),
        {
            "name": name,
            "site": site,
            "company_name": company,
            "query": query,
            "attribution": f"Source: {company} careers (jobs.lever.co/{site}).",
        },
    )()


# Verified live 2026-09-24 on api.lever.co (none needed the EU fallback).
LEVER_BOARDS = [
    lever_board("lever-smartworking", "smart-working-solutions", "Smart Working Solutions"),  # 16 jobs
    lever_board("lever-oowlish", "oowlish", "Oowlish"),                                       # 15 jobs, 4 QA
    lever_board("lever-satelliteinnovations", "SatelliteInnovations", "Satellite Innovations"),  # 1 job
    lever_board("lever-resilientco", "resilientco", "Resilient"),                             # 23 jobs, 1 QA
    lever_board("lever-drivetrain", "drivetrain", "Drivetrain"),                              # 33 jobs, 4 QA
    lever_board("lever-sprinto", "Sprinto", "Sprinto"),                                       # 34 jobs, 1 QA
]

# Verified live but deliberately NOT registered (one-line add when they post QA roles,
# same precedent as Deel):
#   netomi              -> 200, 23 postings, 0 QA
#   sphere-laboratories -> 200,  1 posting,  0 QA
#   gohighlevel         -> 200, 96 postings; QA titles are Lead/Penetration-testing roles
#                          that the existing level rule excludes anyway, and the board mixes
#                          India/US-restricted postings. Low priority, left off.
# Excluded by instruction: jobgether (already covered via JSearch — would duplicate).
UNREGISTERED_LEVER_BOARDS = [
    lever_board("lever-netomi", "netomi", "Netomi"),
    lever_board("lever-spherelabs", "sphere-laboratories", "Sphere Laboratories"),
    lever_board("lever-gohighlevel", "gohighlevel", "HighLevel"),
]
