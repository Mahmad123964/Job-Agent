from typing import List

import requests

from .base import BaseAdapter, RawJob, parse_datetime, parse_salary_text, strip_html


class RemotiveAdapter(BaseAdapter):
    name = "remotive"
    attribution = "Source: Remotive (remotive.com) — backlink + source mention required. Max ~4 requests/day."

    def fetch(self) -> List[RawJob]:
        resp = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"search": "QA"},
            headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        jobs: List[RawJob] = []
        for item in data.get("jobs", []):
            # Remotive's salary is free text like "$10K-$20K" or "€60k - 80k".
            salary_min, salary_max, currency = parse_salary_text(item.get("salary"))
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("id")),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    url=item.get("url", ""),
                    location=item.get("candidate_required_location") or "Remote",
                    tags=item.get("tags", []),
                    posted_at=parse_datetime(item.get("publication_date")),  # naive ISO, UTC
                    description=strip_html(item.get("description")),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=currency,
                )
            )
        return jobs
