from typing import List

import requests

from .base import BaseAdapter, RawJob, parse_datetime, strip_html


class WorkingNomadsAdapter(BaseAdapter):
    """
    NOTE: Working Nomads does not publish official API docs. Research found a
    community-reported JSON endpoint below. If it 404s or changes shape,
    check the site for a current `/api/...` or `?format=rss` path and update
    this adapter — the rest of the app is unaffected either way.
    """
    name = "workingnomads"
    attribution = "Source: Working Nomads (workingnomads.co)."
    access_method = "json feed"

    def fetch(self) -> List[RawJob]:
        try:
            resp = requests.get(
                "https://www.workingnomads.co/api/exposed_jobs/",
                params={"category": "development"},
                headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        jobs: List[RawJob] = []
        for item in data:
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item.get("id") or item.get("url")),
                    title=item.get("title", ""),
                    company=item.get("company_name", ""),
                    url=item.get("url", ""),
                    location=item.get("location") or "Remote",
                    tags=item.get("tags", []),
                    posted_at=parse_datetime(item.get("pub_date")),  # ISO with offset — previously ignored
                    description=strip_html(item.get("description")),
                )
            )
        return jobs
