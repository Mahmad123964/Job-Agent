"""Weighted 0-100 job score computed from classifier output.

Default weights (all overridable via env — see config.py):

    SCORE_SQA_RELEVANCE    40  scaled by relevance_score (0-1)
    SCORE_REMOTE           20
    SCORE_JUNIOR_FRIENDLY  15  full for junior, half for unknown, 0 for mid+/senior/lead
    SCORE_PAKISTAN_ELIGIBLE 15
    SCORE_FRESHNESS        10  posted within the last 24 h

The earned points are normalised by the sum of the configured weights, so the
result is always 0-100 even if someone sets weights that don't add to 100.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from .config import get_settings

FRESHNESS_WINDOW_SECONDS = 24 * 60 * 60


def _is_fresh(posted_at: datetime | None) -> bool:
    if not posted_at:
        return False
    timestamp = posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=timezone.utc)
    return 0 <= (datetime.now(timezone.utc) - timestamp).total_seconds() <= FRESHNESS_WINDOW_SECONDS


def _junior_factor(seniority: str | None) -> float:
    if seniority == "junior":
        return 1.0
    if seniority in (None, "", "unknown"):
        return 0.5  # unstated level: might be open to juniors, might not
    return 0.0


def score(classification: Dict[str, Any], posted_at: datetime | None) -> Tuple[int, Dict[str, int]]:
    """Return (score 0-100, per-component breakdown in points)."""
    s = get_settings()
    weights = {
        "sqa_relevance": s.SCORE_SQA_RELEVANCE,
        "remote": s.SCORE_REMOTE,
        "junior_friendly": s.SCORE_JUNIOR_FRIENDLY,
        "pakistan_eligible": s.SCORE_PAKISTAN_ELIGIBLE,
        "freshness": s.SCORE_FRESHNESS,
    }

    relevance = float(classification.get("relevance_score", 0.0) or 0.0)
    if not classification.get("is_sqa_relevant"):
        relevance = 0.0

    earned = {
        "sqa_relevance": weights["sqa_relevance"] * max(0.0, min(1.0, relevance)),
        "remote": weights["remote"] if classification.get("is_remote") else 0.0,
        "junior_friendly": weights["junior_friendly"] * _junior_factor(classification.get("seniority")),
        "pakistan_eligible": weights["pakistan_eligible"] if classification.get("pakistan_eligible") else 0.0,
        "freshness": weights["freshness"] if _is_fresh(posted_at) else 0.0,
    }

    total_weight = sum(weights.values()) or 1
    final = round(100 * sum(earned.values()) / total_weight)
    breakdown = {name: round(points) for name, points in earned.items()}
    return max(0, min(100, final)), breakdown
