import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)


class State:
    """Tracks drafts awaiting owner approval. Keyed by draft id."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"pending": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            self.data.setdefault("pending", {})
        self.data.setdefault("links", {})

    def save(self) -> None:
        """Write atomically (temp file + rename) so a crash or a concurrent reader
        (Mission Control polls this file) never sees a truncated/half-written file."""
        payload = json.dumps(self.data, indent=2)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:  # Windows: target briefly open by another process
                time.sleep(0.05 * (attempt + 1))
        log.warning("Atomic rename of %s failed; falling back to direct write", self.path.name)
        self.path.write_text(payload, encoding="utf-8")
        tmp.unlink(missing_ok=True)

    def add_pending(self, draft_id: str, record: dict) -> None:
        self.data["pending"][draft_id] = record
        self.save()

    def pending(self) -> dict[str, dict]:
        return {k: v for k, v in self.data["pending"].items() if v.get("status") == "pending"}

    def set_status(self, draft_id: str, status: str) -> None:
        self.data["pending"][draft_id]["status"] = status
        self.save()

    # ---- link resolver -------------------------------------------------
    def link_status(self, url: str) -> str | None:
        """Outcome recorded for a job URL, or None if it has never been resolved."""
        rec = self.data["links"].get(url)
        return rec.get("status") if rec else None

    def mark_link(self, url: str, status: str, **extra) -> None:
        self.data["links"][url] = {"status": status, **extra}
        self.save()
