"""Backwards-compatibility shim — the implementation moved to `dedup.py`
(rapidfuzz-based, queries only recent jobs). Import from `app.dedup` instead.
"""
from .dedup import DedupKey, is_duplicate, load_recent_keys, normalize_title  # noqa: F401
