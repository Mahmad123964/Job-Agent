"""Backwards-compatibility shim — the implementation moved to `scoring.py`.
Import `score` from `app.scoring` instead.
"""
from .scoring import score  # noqa: F401
