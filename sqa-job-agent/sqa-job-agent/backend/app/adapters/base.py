"""
Every job source is an "adapter": a small class with a fetch() method that
returns a normalized list of RawJob dicts. Adding a new source means adding
one file here and registering it in adapters/__init__.py — nothing else in
the app needs to change.

The helpers at the bottom (strip_html, parse_datetime, parse_salary_text,
salary_to_annual) are shared by the JSON adapters so each one can fill in
description / posted_at / salary_* consistently.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from typing import List, Optional, Tuple


@dataclass
class RawJob:
    source: str
    external_id: str
    title: str
    url: str
    company: str = ""
    location: str = "Remote"
    tags: List[str] = field(default_factory=list)
    posted_at: Optional[datetime] = None
    description: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None


class BaseAdapter:
    name: str = "base"
    # Public docs / attribution requirement, surfaced in the dashboard footer.
    attribution: str = ""
    access_method: str = "api"
    # "aggregator" (default): job boards/RSS feeds that surface genuinely new
    # postings daily (Jobicy, RemoteOK, We Work Remotely, ...).
    # "ats_board": a single employer's own careers page (Greenhouse/Ashby/Lever).
    # These list a job until it's filled — weeks or months, not days — so they
    # get a longer freshness window (MAX_JOB_AGE_DAYS_ATS_BOARDS) in scheduler.py.
    source_category: str = "aggregator"

    def fetch(self) -> List[RawJob]:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Shared field parsers
# --------------------------------------------------------------------------- #

_TAG_RE = re.compile(r"<[^>]+>")


def _fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was decoded as Latin-1 somewhere upstream
    (e.g. "â" -> "—"). RemoteOK's feed does this. Only
    applied when the tell-tale lead bytes are present and the round-trip succeeds."""
    if "â" not in text and "Ã" not in text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def strip_html(text: Optional[str]) -> str:
    """HTML -> plain text. Unescapes twice because some APIs (RemoteOK) ship
    HTML that is itself entity-encoded ("&lt;p&gt;...")."""
    text = _fix_mojibake(unescape(unescape(text or "")))
    return " ".join(_TAG_RE.sub(" ", text).split())


def parse_datetime(value) -> Optional[datetime]:
    """ISO-8601 string, epoch seconds (int/str), or None -> aware UTC datetime."""
    if value in (None, "", 0, "0"):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def number_or_none(value) -> Optional[float]:
    """'0', 0, '', None -> None; otherwise float."""
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "C$": "CAD", "A$": "AUD"}
_SALARY_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?")
_CURRENCY_CODE = re.compile(r"\b(USD|EUR|GBP|CAD|AUD|INR|PKR|CHF|SGD|AED|PLN|BRL|MXN|JPY)\b", re.I)


def parse_salary_text(text: Optional[str]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Free-text salary ("$10K-$20K", "€60k - 80k", "80000-100000 USD") ->
    (min, max, currency). Anything unparseable -> (None, None, None)."""
    if not text:
        return None, None, None
    text = str(text)
    currency = None
    code = _CURRENCY_CODE.search(text)
    if code:
        currency = code.group(1).upper()
    else:
        for symbol, iso in _CURRENCY_SYMBOLS.items():
            if symbol in text:
                currency = iso
                break
    numbers = []
    for raw, suffix in _SALARY_NUMBER.findall(text):
        value = number_or_none(raw)
        if value is None:
            continue
        if suffix:
            value *= 1000
        numbers.append(value)
    numbers = [n for n in numbers if n >= 100]  # drop stray "2+ years"-style digits
    if not numbers:
        return None, None, currency
    return min(numbers), max(numbers), currency


_PERIOD_MULTIPLIER = {"yearly": 1, "annual": 1, "annually": 1, "year": 1, "monthly": 12, "month": 12}


def salary_to_annual(low, high, period: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """Normalise to an annual figure. Only yearly/monthly periods are converted;
    hourly/daily/weekly rates are dropped rather than guessed at."""
    factor = _PERIOD_MULTIPLIER.get((period or "yearly").strip().lower())
    if factor is None:
        return None, None
    low, high = number_or_none(low), number_or_none(high)
    return (low * factor if low else None), (high * factor if high else None)
