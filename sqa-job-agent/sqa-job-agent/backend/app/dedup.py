"""Fuzzy duplicate detection for the ingestion pipeline.

Two layers:

1. Exact: the DB already enforces UNIQUE(source, external_id). We re-check it
   here so the scheduler can skip the classifier (the expensive step) without
   round-tripping an IntegrityError.
2. Fuzzy: the same role is frequently cross-posted to several boards (e.g.
   RemoteOK + We Work Remotely + Himalayas) with slightly different titles
   ("QA Engineer (Remote)" vs "Remote QA Engineer"). We normalise the title,
   then compare title + company + apply-URL domain against recent jobs with
   rapidfuzz. A job is a likely duplicate when the title and company both
   match above their thresholds, OR when the title matches and the apply
   domain is identical (many boards link straight to the company ATS, so an
   identical domain + near-identical title is a strong signal even when the
   company string differs).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional
from urllib.parse import urlparse

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Job
from .normalizer import NormalizedJob, apply_domain

_NOISE_WORDS = {"remote", "wfh", "hiring", "urgent", "immediate", "job", "position", "opening"}

# Hosts that are job boards, not company ATSs: an identical board domain says
# nothing about the two postings being the same role, so it isn't a signal.
_BOARD_DOMAINS = {
    "remoteok.com", "remotive.com", "remotive.io", "himalayas.app", "arbeitnow.com",
    "jobicy.com", "weworkremotely.com", "jobspresso.co", "pangian.com",
    "authenticjobs.com", "remote.io", "dribbble.com", "workingnomads.com",
    "linkedin.com", "indeed.com", "glassdoor.com",
}


def normalize_title(title: str) -> str:
    """Lower-case, strip punctuation, drop filler words, collapse whitespace."""
    text = re.sub(r"[^a-z0-9]+", " ", (title or "").lower())
    words = [w for w in text.split() if w not in _NOISE_WORDS]
    return " ".join(words)


def normalize_company(company: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (company or "").lower())
    text = re.sub(r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|plc)\b", " ", text)
    return " ".join(text.split())


def _canonical_url(url: str) -> str:
    """Lower-cased host + path, no scheme/query/fragment/trailing slash."""
    parsed = urlparse((url or "").strip())
    if not parsed.netloc:
        return ""
    return f"{parsed.netloc.lower().removeprefix('www.')}{parsed.path.rstrip('/').lower()}"


@dataclass(frozen=True)
class DedupKey:
    """Everything we need from an existing job to compare against."""
    source: str
    external_id: str
    title: str      # already passed through normalize_title
    company: str    # already passed through normalize_company
    domain: str     # apply-URL host
    url: str = ""   # canonical apply URL (host + path)

    @classmethod
    def from_job(cls, job: Job) -> "DedupKey":
        return cls(
            source=job.source,
            external_id=job.external_id,
            title=normalize_title(job.title),
            company=normalize_company(job.company or ""),
            domain=apply_domain(job.url),
            url=_canonical_url(job.url),
        )

    @classmethod
    def from_normalized(cls, job: NormalizedJob) -> "DedupKey":
        return cls(
            source=job.source,
            external_id=job.external_id,
            title=normalize_title(job.title),
            company=normalize_company(job.company),
            domain=apply_domain(job.apply_url),
            url=_canonical_url(job.apply_url),
        )


def load_recent_keys(db: Session, lookback_days: Optional[int] = None) -> List[DedupKey]:
    """Fetch dedup keys for jobs discovered within the lookback window.

    Called once per poll (not once per job) so a poll over ~13 sources does a
    single lightweight query instead of loading the whole table repeatedly.
    """
    days = lookback_days if lookback_days is not None else get_settings().DEDUP_LOOKBACK_DAYS
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(Job.source, Job.external_id, Job.title, Job.company, Job.url)
        .filter(Job.discovered_at >= since)
        .all()
    )
    return [
        DedupKey(
            source=source,
            external_id=external_id,
            title=normalize_title(title),
            company=normalize_company(company or ""),
            domain=apply_domain(url),
            url=_canonical_url(url),
        )
        for source, external_id, title, company, url in rows
    ]


def is_duplicate(
    job: NormalizedJob,
    existing: Iterable[DedupKey],
    *,
    title_threshold: Optional[int] = None,
    company_threshold: Optional[int] = None,
) -> bool:
    """Return True if `job` is an exact or likely-fuzzy duplicate of any key in `existing`.

    Pure with respect to the DB: pass the output of `load_recent_keys()` (plus
    any keys you've added during the current poll) as `existing`.
    """
    settings = get_settings()
    title_threshold = title_threshold if title_threshold is not None else settings.DEDUP_TITLE_THRESHOLD
    company_threshold = company_threshold if company_threshold is not None else settings.DEDUP_COMPANY_THRESHOLD

    candidate = DedupKey.from_normalized(job)
    if not candidate.title:
        return False

    candidate_url = _canonical_url(job.apply_url)
    for other in existing:
        # Layer 1: exact (source, external_id) — mirrors the DB unique constraint.
        if other.source == candidate.source and other.external_id == candidate.external_id:
            return True
        # Layer 1b: the very same apply link. Catches one listing appearing in
        # two feeds of the same board (e.g. We Work Remotely's category feed and
        # its all-jobs feed give different GUIDs but the same link) — company is
        # empty there, so the fuzzy company check below can't fire.
        if candidate_url and other.url == candidate_url:
            return True

        # Layer 2: fuzzy cross-board match.
        title_ratio = fuzz.token_sort_ratio(candidate.title, other.title)
        if title_ratio < title_threshold:
            continue

        if candidate.company and other.company:
            if fuzz.token_set_ratio(candidate.company, other.company) >= company_threshold:
                return True
        elif not candidate.company and not other.company and candidate.source == other.source and title_ratio >= 95:
            # RSS boards give no company (the title is "Company: Role"); an
            # (almost) identical title on the same board is the same listing —
            # seen live as We Work Remotely posting "LawnStarter: Principal
            # Quality Engineer" twice with links differing only by a "-1" suffix.
            return True

        same_domain = (
            candidate.domain
            and candidate.domain == other.domain
            and candidate.domain not in _BOARD_DOMAINS
        )
        if same_domain:
            return True

    return False
