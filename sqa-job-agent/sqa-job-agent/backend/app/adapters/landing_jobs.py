"""Landing.jobs — free public JSON API, no auth.

    GET https://landing.jobs/api/v1/jobs

Verified live 2026-09-24: HTTP 200, 50 current postings with real data
(id, title, url, remote, locations, tags, salary band, published_at).
The API has no remote-only query parameter, so the `remote` flag is filtered
client-side; the RSS fallback (https://landing.jobs/feed?remote=true) carries
fewer fields and the same listings, so the API is preferred.

Freshness caveat: the board is Portugal/EU-centric and slow-moving — measured
published-age median was 58 days, with only 3 of 50 inside our 14-day window.
Most polls will therefore contribute nothing after the freshness filter. That
is the source's own cadence, not an adapter fault.
"""
import re
from typing import List

import requests

from .base import BaseAdapter, RawJob, number_or_none, parse_datetime, strip_html

_API = "https://landing.jobs/api/v1/jobs"
# The API carries no company field; the public URL does: /at/<company>/<slug>
_COMPANY_FROM_URL = re.compile(r"/at/([^/]+)/")


class LandingJobsAdapter(BaseAdapter):
    name = "landingjobs"
    access_method = "api"
    attribution = "Source: Landing.jobs (landing.jobs)."
    timeout = 25
    remote_only = True

    @staticmethod
    def _company(item: dict) -> str:
        explicit = item.get("company_name") or (item.get("company") or {}).get("name")
        if explicit:
            return explicit
        m = _COMPANY_FROM_URL.search(item.get("url") or "")
        return m.group(1).replace("-", " ").title() if m else ""

    @staticmethod
    def _location(item: dict) -> str:
        parts = []
        for loc in item.get("locations") or []:
            city, country = (loc.get("city") or "").strip(), (loc.get("country_code") or "").strip()
            parts.append(", ".join(p for p in (city, country) if p))
        joined = " / ".join(p for p in parts if p)
        if item.get("remote"):
            return f"Remote - {joined}" if joined else "Remote"
        return joined or "Remote"

    def fetch(self) -> List[RawJob]:
        resp = requests.get(_API, headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"}, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        items = payload if isinstance(payload, list) else payload.get("jobs", [])
        jobs: List[RawJob] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("id") or not item.get("url"):
                continue
            if self.remote_only and not item.get("remote"):
                continue
            description = " ".join(
                strip_html(item.get(field) or "")
                for field in ("role_description", "main_requirements", "nice_to_have")
            ).strip()
            tags = [t for t in (item.get("tags") or []) if t]
            if item.get("type"):
                tags.append(item["type"])
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item["id"]),
                    title=item.get("title", ""),
                    company=self._company(item),
                    url=item["url"],
                    location=self._location(item),
                    tags=tags,
                    posted_at=parse_datetime(item.get("published_at") or item.get("created_at")),
                    description=description,
                    salary_min=number_or_none(item.get("gross_salary_low")),
                    salary_max=number_or_none(item.get("gross_salary_high")),
                    salary_currency=(item.get("currency_code") or None),
                )
            )
        return jobs
