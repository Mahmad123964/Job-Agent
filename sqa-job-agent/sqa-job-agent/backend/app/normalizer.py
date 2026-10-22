"""Convert source-specific RawJob objects into the pipeline's common schema.

`normalize()` is a pure function (no I/O, no settings) so it can be unit-tested
by constructing a RawJob and asserting on the NormalizedJob fields.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from .adapters.base import RawJob


@dataclass
class NormalizedJob:
    source: str
    external_id: str
    title: str
    company: str
    location: str
    description: str
    posted_at: Optional[datetime]
    apply_url: str
    salary_min: Optional[float]
    salary_max: Optional[float]
    salary_currency: Optional[str]
    tags: list[str] = field(default_factory=list)


def _clean(value: Optional[str]) -> str:
    """Collapse internal whitespace and strip; None becomes ''."""
    return " ".join((value or "").split())


def _clean_datetime(value: Optional[datetime]) -> Optional[datetime]:
    """Adapters return a mix of naive and aware datetimes. Naive is treated as
    UTC; aware values are CONVERTED to UTC (not just tagged) because the DB
    columns are timezone-less and store the wall-clock time — a "+05:00" value
    left as-is would read back 5 hours wrong."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean_salary(low: Optional[float], high: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Drop zero/negative salaries and make sure min <= max."""
    low = low if low and low > 0 else None
    high = high if high and high > 0 else None
    if low is not None and high is not None and low > high:
        low, high = high, low
    return low, high


def normalize(raw: RawJob) -> NormalizedJob:
    salary_min, salary_max = _clean_salary(raw.salary_min, raw.salary_max)
    return NormalizedJob(
        source=_clean(raw.source).lower(),
        external_id=_clean(str(raw.external_id or raw.url)),
        title=_clean(raw.title),
        company=_clean(raw.company),
        location=_clean(raw.location) or "Remote",
        description=(raw.description or "").strip(),
        posted_at=_clean_datetime(raw.posted_at),
        apply_url=(raw.url or "").strip(),
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=(_clean(raw.salary_currency).upper() or None),
        tags=[t for t in (_clean(tag) for tag in raw.tags) if t],
    )


def apply_domain(url: str) -> str:
    """Host part of an apply URL, lower-cased and without a leading 'www.'."""
    return urlparse(url or "").netloc.lower().removeprefix("www.")


# Domains that can never be a real job posting: IANA reserved names (RFC 2606)
# plus local/test hosts. Matched on the host and any subdomain of it.
PLACEHOLDER_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "example.edu",
    "test.com", "localhost", "invalid", "example", "test",
})


def invalid_url_reason(url: str) -> Optional[str]:
    """Why `url` can't be saved as an apply link, or None if it's acceptable.

    Rejects: empty, non-http(s) scheme, missing host, and hosts on/under a
    placeholder domain. Pure function so it can be unit-tested.
    """
    url = (url or "").strip()
    if not url:
        return "empty"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"scheme={parsed.scheme or 'none'}"
    host = parsed.hostname or ""
    if not host or "." not in host and host not in PLACEHOLDER_DOMAINS:
        return "no host"
    labels = host.lower().split(".")
    for i in range(len(labels)):
        if ".".join(labels[i:]) in PLACEHOLDER_DOMAINS:
            return f"placeholder domain {host}"
    return None
