"""Jobicy adapter (https://jobicy.com/jobs-rss-feed / /api/v2/remote-jobs).

Why the old call broke: `?tag=qa` returned
    400 {"error": "The length of the 'tag' value should be between 3 and 50 characters."}
`tag` is a free-text search whose value must be >= 3 chars, so "qa" was
rejected on every poll. Jobicy also exposes a curated industry filter
(`?get=industries` lists the slugs) that includes `qa-testing`, which is a
far better server-side filter than a keyword. We use that, and if Jobicy
ever renames/removes the slug (a 400), fall back to an unfiltered fetch and
let filters.py do the QA matching client-side like the other adapters.
"""
import logging
from typing import List

import requests

from .base import BaseAdapter, RawJob, number_or_none, parse_datetime, strip_html

logger = logging.getLogger("sqa_job_agent.adapters.jobicy")

_API = "https://jobicy.com/api/v2/remote-jobs"
_HEADERS = {"User-Agent": "Mozilla/5.0 (sqa-job-agent)"}

class JobicyAdapter(BaseAdapter):
    name = "jobicy"
    attribution = "Source: Jobicy (jobicy.com) — backlink required. Poll at most once/hour."

    # Curated category slug from https://jobicy.com/api/v2/remote-jobs?get=industries
    industry = "qa-testing"

    def _get(self, params: dict) -> dict:
        resp = requests.get(_API, params=params, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def fetch(self) -> List[RawJob]:
        try:
            data = self._get({"count": 50, "industry": self.industry})
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 400:
                raise
            # Slug rejected -> Jobicy changed its industry list. Fetch unfiltered;
            # the scheduler's keyword filter still picks out the QA roles.
            logger.warning("Jobicy rejected industry=%s (%s); fetching unfiltered", self.industry, exc.response.text[:120])
            data = self._get({"count": 50})

        jobs: List[RawJob] = []
        for item in data.get("jobs", []):
            tags = list(item.get("jobIndustry") or [])
            if item.get("jobLevel") and item["jobLevel"] != "Any":
                tags.append(item["jobLevel"])
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("id")),
                    title=item.get("jobTitle", ""),
                    company=item.get("companyName", ""),
                    url=item.get("url", ""),
                    location=item.get("jobGeo") or "Remote",
                    tags=tags,
                    posted_at=parse_datetime(item.get("pubDate")),
                    description=strip_html(item.get("jobDescription") or item.get("jobExcerpt")),
                    salary_min=number_or_none(item.get("salaryMin") or item.get("annualSalaryMin")),
                    salary_max=number_or_none(item.get("salaryMax") or item.get("annualSalaryMax")),
                    salary_currency=item.get("salaryCurrency") or None,
                )
            )
        return jobs
