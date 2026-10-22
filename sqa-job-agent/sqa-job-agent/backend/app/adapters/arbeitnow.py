from typing import List

import requests

from .base import BaseAdapter, RawJob, parse_datetime, strip_html


class ArbeitnowAdapter(BaseAdapter):
    name = "arbeitnow"
    attribution = "Source: Arbeitnow (arbeitnow.com)."

    def fetch(self) -> List[RawJob]:
        resp = requests.get("https://www.arbeitnow.com/api/job-board-api", timeout=20)
        resp.raise_for_status()
        data = resp.json().get("data", [])

        jobs: List[RawJob] = []
        for item in data:
            if not item.get("remote"):
                continue
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("slug") or item.get("url")),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    url=item.get("url", ""),
                    location=item.get("location") or "Remote",
                    tags=item.get("tags", []),
                    posted_at=parse_datetime(item.get("created_at")),  # epoch seconds — previously ignored
                    description=strip_html(item.get("description")),
                )
            )
        return jobs
