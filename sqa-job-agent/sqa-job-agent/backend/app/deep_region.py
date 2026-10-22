"""Deep region check — a second, more expensive pass at geo eligibility for
jobs the cheap pass left "Unclear".

Why this exists: most feeds/APIs hand us a short description that often omits
eligibility details a full job posting page states plainly ("This role is
open worldwide" / "Must be authorized to work in the US"). Before excluding
an Unclear job at the notification gate, fetch its own posting page once and
re-run geo.py's rules against the fuller text.

Guardrails:
  * Only ever invoked for jobs already "Unclear" — confirmed-restricted jobs
    are left alone (they're already confidently excluded), and Worldwide/PK
    -eligible jobs never reach this path (they already pass the gate).
  * Never fetched for sources this project doesn't scrape individual pages
    for (JSearch, which aggregates LinkedIn/Indeed/Glassdoor — link-only,
    same policy as everywhere else in this project).
  * Cached outside the DB (DEEP_REGION_CACHE_PATH), like the notification
    ledger, so an Unclear job that keeps reappearing every poll (it's never
    saved, so nothing in jobs.db can remember it) is only ever fetched once.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict
from typing import Dict, Optional

import requests

from .adapters.base import strip_html
from .config import get_settings
from .geo import Eligibility, extract_eligibility

logger = logging.getLogger("sqa_job_agent.deep_region")
_lock = threading.Lock()

_BOILERPLATE_BLOCK = re.compile(
    r"<(script|style|noscript|nav|header|footer|svg)\b[^>]*>.*?</\1>", re.I | re.S
)


def excluded_sources(settings=None) -> set:
    settings = settings or get_settings()
    return {s.strip().lower() for s in settings.DEEP_REGION_EXCLUDED_SOURCES.split(",") if s.strip()}


def is_deep_checkable(source: str, settings=None) -> bool:
    return (source or "").strip().lower() not in excluded_sources(settings)


def extract_main_text(html: str) -> str:
    """Strip script/style/nav/header/footer blocks, then tags -- a rough but
    cheap approximation of "main content" good enough for keyword/phrase
    matching (geo.py's rules don't need real layout, just the words)."""
    return strip_html(_BOILERPLATE_BLOCK.sub(" ", html or ""))


def fetch_page_text(url: str, timeout: Optional[int] = None) -> Optional[str]:
    settings = get_settings()
    timeout = timeout if timeout is not None else settings.DEEP_REGION_FETCH_TIMEOUT_SECONDS
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"})
        resp.raise_for_status()
    except Exception as exc:
        logger.info("Deep region check: fetch failed for %s: %s", url, exc)
        return None
    return extract_main_text(resp.text)


class DeepRegionCache:
    """Append-only JSONL cache of deep-check outcomes, keyed by "source:external_id".

    Same shape as NotificationLedger (app/ledger.py): a record is appended
    per check, the in-memory map is replayed on load and keeps the latest
    write for each key, and a missing/unreadable file just means an empty
    cache (never fatal)."""

    def __init__(self, path: Optional[str] = None):
        settings = get_settings()
        self.path = path or settings.DEEP_REGION_CACHE_PATH
        self._seen: Dict[str, Eligibility] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    self._seen[rec["key"]] = Eligibility(rec["region"], rec.get("country"), rec.get("evidence", ""))
                except Exception:
                    continue

    def get(self, key: str) -> Optional[Eligibility]:
        return self._seen.get(key)

    def record(self, key: str, eligibility: Eligibility) -> None:
        rec = {"key": key, **asdict(eligibility)}
        with _lock:
            self._seen[key] = eligibility
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError as exc:  # cache must never break the poll
                logger.error("Could not append to deep region cache %s: %s", self.path, exc)

    def __len__(self) -> int:
        return len(self._seen)


def cache_key(source: str, external_id: str) -> str:
    return f"{source}:{external_id}"


def _looks_like_job_page(text: str, title: str, company: str) -> bool:
    """Sanity check before trusting anything extracted from the fetched page.

    Some sources (seen live on WeWorkRemotely) serve a generic shell for a
    plain HTTP GET -- a "browse all jobs" / related-listings page packed with
    OTHER postings, not the specific job -- when the real content only exists
    client-side rendered. That page can easily contain other postings'
    restriction phrases ("EMEA Only", "North America Only") AND scattered
    occurrences of common title words ("Engineer", "Quality", "Principal")
    from those unrelated cards, so counting individual word hits is not
    reliable on a busy listing page. Require the company name, or the job's
    full title as a contiguous phrase, to actually appear in the text --
    a miss just falls back to Unclear (safe: never a false confirmation)."""
    text_l = " ".join((text or "").lower().split())
    company_l = (company or "").strip().lower()
    if len(company_l) >= 3 and company_l in text_l:
        return True
    title_l = " ".join((title or "").strip().lower().split())
    return bool(title_l) and title_l in text_l


def deep_check(
    source: str,
    external_id: str,
    url: str,
    title: str,
    location: str,
    cache: DeepRegionCache,
    company: str = "",
) -> Eligibility:
    """Always called only for source-eligible, currently-Unclear jobs (caller's
    responsibility -- see is_deep_checkable). Returns the cached result if this
    exact posting was already checked; otherwise fetches the page once, re-runs
    extract_eligibility against the fuller text, caches, and returns it. On any
    failure (fetch error, timeout, content that doesn't look like this job's own
    page, or still no signal) the result is Unclear -- strictly additive, never
    a new guess-if-uncertain policy."""
    key = cache_key(source, external_id)
    cached = cache.get(key)
    if cached is not None:
        return cached

    page_text = fetch_page_text(url) if url else None
    if not page_text:
        result = Eligibility("Unclear", None, "deep check: fetch failed or empty")
    elif not _looks_like_job_page(page_text, title, company):
        logger.info(
            "Deep region check: fetched page for %s/%s doesn't look like this job's own posting "
            "(title/company not found in the text) -- treating as unreliable, not extracting from it",
            source, external_id,
        )
        result = Eligibility("Unclear", None, "deep check: fetched content didn't match this job (likely a generic/listing page)")
    else:
        result = extract_eligibility(location, page_text, title)

    cache.record(key, result)
    return result
