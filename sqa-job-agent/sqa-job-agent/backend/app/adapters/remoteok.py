from typing import List

import requests

from .base import BaseAdapter, RawJob, number_or_none, parse_datetime, strip_html


class RemoteOKAdapter(BaseAdapter):
    name = "remoteok"
    attribution = "Source: RemoteOK (remoteok.com) — direct link back required per their API terms."

    def fetch(self) -> List[RawJob]:
        resp = requests.get(
            "https://remoteok.com/api",
            headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        jobs: List[RawJob] = []
        for item in data:
            if not isinstance(item, dict) or "id" not in item:
                continue  # first element is a legal/metadata blob, not a job
            # RemoteOK publishes USD figures; "0" means not disclosed.
            salary_min = number_or_none(item.get("salary_min"))
            salary_max = number_or_none(item.get("salary_max"))
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item["id"]),
                    title=item.get("position", ""),
                    company=item.get("company", ""),
                    # `url` is the canonical listing page; fall back to the id-based
                    # deep link on remoteok.com (a real page, not a placeholder).
                    url=item.get("url") or f"https://remoteok.com/remote-jobs/{item['id']}",
                    location=item.get("location") or "Remote",
                    tags=item.get("tags", []),
                    posted_at=parse_datetime(item.get("date") or item.get("epoch")),
                    description=strip_html(item.get("description")),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency="USD" if (salary_min or salary_max) else None,
                )
            )
        return jobs
