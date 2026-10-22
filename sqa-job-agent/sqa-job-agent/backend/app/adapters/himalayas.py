from typing import List

import requests

from .base import BaseAdapter, RawJob, parse_datetime, salary_to_annual, strip_html


class HimalayasAdapter(BaseAdapter):
    name = "himalayas"
    attribution = "Source: Himalayas (himalayas.app) — visible backlink + source mention required."

    def fetch(self) -> List[RawJob]:
        resp = requests.get(
            "https://himalayas.app/jobs/api",
            params={"limit": 40, "query": "QA"},
            headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        jobs: List[RawJob] = []
        for item in data.get("jobs", []):
            # `locationRestrictions` is a list of country names; empty means the
            # listing is open worldwide. (The old `worldwide` key doesn't exist
            # in the response, so every job used to be labelled "Remote".)
            restrictions = [r for r in (item.get("locationRestrictions") or []) if r]
            location = ", ".join(restrictions) if restrictions else "Worldwide"

            # Salary comes with a period (yearly/monthly/hourly); only annual-
            # comparable figures are kept.
            salary_min, salary_max = salary_to_annual(
                item.get("minSalary"), item.get("maxSalary"), item.get("salaryPeriod")
            )
            tags = list(item.get("categories") or []) + list(item.get("seniority") or [])
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("guid") or item.get("id")),
                    title=item.get("title", ""),
                    company=(item.get("companyName") or item.get("company", "")),
                    url=item.get("applicationLink") or item.get("url", ""),
                    location=location,
                    tags=tags,
                    posted_at=parse_datetime(item.get("pubDate")),  # epoch seconds
                    description=strip_html(item.get("description") or item.get("excerpt")),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=(item.get("currency") or None) if (salary_min or salary_max) else None,
                )
            )
        return jobs
