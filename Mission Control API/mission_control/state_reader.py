"""Safe, read-only access to the agents' JSON state files.

The agents write with a plain `write_text` (not atomic), so a read can land mid-write and
see a truncated file, or hit a Windows sharing violation. `SafeJsonFile.read()` retries
briefly and otherwise returns the last good snapshot instead of raising.
"""
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class SafeJsonFile:
    def __init__(self, path: Path, default: Any, retries: int = 5, retry_delay: float = 0.1):
        self.path = path
        self.default = default
        self.retries = retries
        self.retry_delay = retry_delay
        self._last_good: Any = default
        self._last_mtime: float = 0.0

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def mtime(self) -> float:
        """Last modification time (epoch seconds), 0 if missing."""
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def read(self) -> Any:
        """Parsed JSON, or the previous good value if the file is missing/mid-write."""
        if not self.path.exists():
            return self.default
        for attempt in range(self.retries):
            try:
                raw = self.path.read_text(encoding="utf-8")
                if not raw.strip():
                    # write_text truncates before it writes: an empty file is a mid-write, not "no data".
                    raise ValueError("empty file (mid-write)")
                data = json.loads(raw)
                self._last_good, self._last_mtime = data, self.mtime()
                return data
            except (ValueError, OSError, UnicodeDecodeError) as e:  # JSONDecodeError is a ValueError
                if attempt < self.retries - 1:
                    time.sleep(self.retry_delay)
                else:
                    log.warning("Could not read %s after %d attempts (%s); using last good snapshot",
                                self.path.name, self.retries, e.__class__.__name__)
        return self._last_good
