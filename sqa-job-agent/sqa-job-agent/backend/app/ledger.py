"""Notification ledger — the memory of what has already been sent.

Why this exists: `Job.notified` lives in jobs.db, so every time the database
was deleted/recreated (or a fresh test DB was polled) the very same postings
were treated as new and emailed again. The ledger is an append-only JSONL file
OUTSIDE the database (NOTIFY_LEDGER_PATH) holding a fingerprint per notified
posting; the scheduler consults it before every send, so a posting is never
notified twice within NOTIFY_LEDGER_DAYS, whatever happens to jobs.db.

Fingerprints (any match counts as "already notified"):
  * src:<source>:<external_id>           — the exact posting
  * url:<host+path of the apply link>    — the same listing re-indexed elsewhere
  * tc:<normalised title>|<normalised company> — cross-board reposts
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

from .config import get_settings
from .dedup import normalize_company, normalize_title

logger = logging.getLogger("sqa_job_agent.ledger")
_lock = threading.Lock()


def fingerprints(source: str, external_id: str, title: str, company: str, url: str) -> List[str]:
    keys = [f"src:{source}:{external_id}"]
    parsed = urlparse(url or "")
    if parsed.netloc:
        keys.append(f"url:{parsed.netloc.lower().removeprefix('www.')}{parsed.path.rstrip('/').lower()}")
    t, c = normalize_title(title), normalize_company(company)
    if t and c:
        keys.append(f"tc:{t}|{c}")
    return keys


class NotificationLedger:
    def __init__(self, path: Optional[str] = None, days: Optional[int] = None):
        settings = get_settings()
        self.path = path or settings.NOTIFY_LEDGER_PATH
        self.days = days if days is not None else settings.NOTIFY_LEDGER_DAYS
        self._seen: Dict[str, datetime] = {}
        self._load()

    def _load(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days)
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    at = datetime.fromisoformat(rec["at"])
                except Exception:
                    continue
                if at < cutoff:
                    continue
                for key in rec.get("keys", []):
                    self._seen[key] = max(at, self._seen.get(key, at))

    def already_notified(self, keys: Iterable[str]) -> Optional[str]:
        """The matching fingerprint if any key was notified within the window."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.days)
        for key in keys:
            at = self._seen.get(key)
            if at and at >= cutoff:
                return key
        return None

    def record(self, keys: Iterable[str], channels: Iterable[str], title: str = "") -> None:
        keys = list(keys)
        now = datetime.now(timezone.utc)
        rec = {"at": now.isoformat(), "keys": keys, "channels": list(channels), "title": title[:120]}
        with _lock:
            for key in keys:
                self._seen[key] = now
            try:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError as exc:  # ledger must never break notifications
                logger.error("Could not append to notification ledger %s: %s", self.path, exc)

    def __len__(self) -> int:
        return len(self._seen)


def seed_from_db(db, ledger: Optional["NotificationLedger"] = None) -> int:
    """Record every job already marked notified in the DB into the ledger
    (idempotent). Run once when the ledger is introduced so postings notified
    before it existed can't be re-sent after a DB reset."""
    from .models import Job  # local import to avoid a cycle at module load

    ledger = ledger or NotificationLedger()
    added = 0
    for job in db.query(Job).filter(Job.notified.is_(True)).all():
        keys = fingerprints(job.source, job.external_id, job.title, job.company, job.url)
        if ledger.already_notified(keys):
            continue
        ledger.record(keys, [c for c in (job.notified_channels or "").split(",") if c] or ["unknown"], job.title)
        added += 1
    return added
