import calendar
import hashlib
import re
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
import requests

from .base import BaseAdapter, RawJob, strip_html

# Boards like We Work Remotely format titles as "Company: Role". Splitting the
# company out gives dedup a real company to compare (otherwise the same role
# cross-posted on another board can't be recognised).
_COMPANY_PREFIX = re.compile(r"^\s*([^:]{2,60}?)\s*:\s+(.{3,})$")
# Startup Jobs and NODESK format titles the other way round: "Role at Company".
_COMPANY_SUFFIX = re.compile(r"^(.{3,}?)\s+at\s+([^,|]{2,60})$", re.I)


def _entry_datetime(entry) -> Optional[datetime]:
    """feedparser gives `published_parsed`/`updated_parsed` as UTC struct_time."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
            except (OverflowError, ValueError):
                continue
    return None


class RSSAdapter(BaseAdapter):
    """
    Generic adapter for any source that publishes job listings as RSS/Atom.
    Subclass and set `feed_urls` (a list — some boards need multiple category
    feeds to get full coverage).
    """
    feed_urls: List[str] = []
    access_method = "rss"
    timeout = 20  # seconds per feed request
    # True for boards whose titles read "Role at Company" (Startup Jobs, NODESK)
    # rather than "Company: Role" (We Work Remotely).
    company_suffix = False
    default_location = "Remote"

    def fetch(self) -> List[RawJob]:
        jobs: List[RawJob] = []
        for feed_url in self.feed_urls:
            # feedparser.parse(url) has no timeout — a hanging feed used to stall the
            # whole sequential poll. Fetch with a bounded request and parse the body.
            resp = requests.get(feed_url, timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"})
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries:
                guid = entry.get("id") or entry.get("link") or entry.get("title", "")
                external_id = hashlib.sha1(guid.encode("utf-8")).hexdigest()[:16]
                raw_title = entry.get("title", "") or ""
                company = ""
                m = _COMPANY_PREFIX.match(raw_title)
                if m:
                    company, raw_title = m.group(1).strip(), m.group(2).strip()
                elif self.company_suffix:
                    m = _COMPANY_SUFFIX.match(raw_title)
                    if m:
                        raw_title, company = m.group(1).strip(), m.group(2).strip()
                summary = entry.get("summary") or (entry.get("content") or [{}])[0].get("value", "")
                jobs.append(
                    RawJob(
                        source=self.name,
                        external_id=external_id,
                        title=raw_title,
                        company=company,
                        url=entry.get("link", ""),
                        location=self.default_location,
                        tags=[t.term for t in entry.get("tags", [])] if entry.get("tags") else [],
                        posted_at=_entry_datetime(entry),      # <pubDate> — previously ignored
                        description=strip_html(summary),
                    )
                )
        return jobs


class WeWorkRemotelyAdapter(RSSAdapter):
    name = "weworkremotely"
    attribution = "Source: We Work Remotely (weworkremotely.com) — backlink required."
    feed_urls = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/remote-jobs.rss",
    ]


class JobspressoAdapter(RSSAdapter):
    name = "jobspresso"
    attribution = "Source: Jobspresso (jobspresso.co)."
    feed_urls = ["https://jobspresso.co/feed/?post_type=job_listing"]


class PangianAdapter(RSSAdapter):
    name = "pangian"
    attribution = "Source: Pangian (pangian.com)."
    feed_urls = ["https://pangian.com/feed/?post_type=job_listing"]


class AuthenticJobsAdapter(RSSAdapter):
    name = "authenticjobs"
    attribution = "Source: Authentic Jobs (authenticjobs.com)."
    feed_urls = [
        "https://authenticjobs.com/?feed=job_feed&job_types=freelance,full-time,internship,part-time&search_location=remote"
    ]


class RemoteIOAdapter(RSSAdapter):
    name = "remoteio"
    attribution = "Source: remote.io."
    feed_urls = ["https://iloveremote.io/rss/jobs/city/remote.rss"]


class StartupJobsAdapter(RSSAdapter):
    """Startup Jobs (startup.jobs) — free RSS, no key. `workplace=remote` plus
    `role=engineering` narrows to technical roles before our keyword filter.
    Returns the 50 most recent postings from the last 14 days.
    Rate limit is 20 req/min; our 15-minute poll is far below that."""

    name = "startupjobs"
    attribution = "Source: Startup Jobs (startup.jobs) — dofollow backlink to the job page required."
    company_suffix = True
    feed_urls = ["https://startup.jobs/feeds/jobs?workplace=remote&role=engineering"]


class NodeskAdapter(RSSAdapter):
    """NODESK (nodesk.co) — curated remote-jobs feed, ~10 most recent entries."""

    name = "nodesk"
    attribution = "Source: NODESK (nodesk.co)."
    company_suffix = True
    feed_urls = ["https://nodesk.co/remote-jobs/index.xml"]


class RemoteFirstJobsAdapter(RSSAdapter):
    """Remote First Jobs — QA-tagged feed (remotefirstjobs.com/rss/jobs/qa.rss).

    Verified live 2026-09-24: 100 entries, newest minutes old. Note the "qa"
    feed is NOT QA-only (it also carries Customer Success, Deployment Manager
    etc.), so our keyword filter still does the selection.

    Attribution: backlink to the job page + "Remote First Jobs" credit. Their
    terms forbid resubmitting these listings to LinkedIn Jobs, Google Jobs or
    Jooble — this adapter only reads the feed and stores it locally; nothing in
    the pipeline republishes to any third party, so we comply.
    """

    name = "remotefirstjobs"
    attribution = "Source: Remote First Jobs (remotefirstjobs.com) — backlink required; listings not resubmitted to third-party boards."
    company_suffix = True
    feed_urls = ["https://remotefirstjobs.com/rss/jobs/qa.rss"]


class DribbbleAdapter(RSSAdapter):
    name = "dribbble"
    attribution = "Source: Dribbble Jobs (dribbble.com/jobs)."
    feed_urls = ["https://dribbble.com/jobs.rss?anywhere=true&location=Anywhere"]
