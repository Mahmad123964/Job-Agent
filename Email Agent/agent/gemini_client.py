import logging
import time
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class GeminiUnavailable(RuntimeError):
    """Raised when a Gemini call still fails after all retries are exhausted."""


def _retryable(err: Exception) -> bool:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    msg = str(err)
    return any(t in msg for t in ("429", "503", "502", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "overloaded"))


class Gemini:
    def __init__(self, api_key: str, model: str, max_retries: int = 3, initial_backoff_seconds: float = 3.0):
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_retries = max(1, int(max_retries))
        self.initial_backoff = float(initial_backoff_seconds)

    def structured(self, prompt: str, schema: type[T], temperature: float = 0.2) -> T:
        """Call Gemini and parse the response into `schema`.

        Retries transient errors (429/5xx) with a short doubling backoff
        (default 3s, 6s, 12s over 3 attempts). Raises GeminiUnavailable once
        retries are exhausted so callers can degrade gracefully.
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
        )
        delay = self.initial_backoff
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
                return schema.model_validate_json(resp.text)
            except Exception as e:  # noqa: BLE001 - SDK raises several error types
                last = e
                if not _retryable(e):
                    raise
                if attempt == self.max_retries:
                    break
                log.warning("Gemini transient error (attempt %d/%d), retrying in %.0fs: %s",
                            attempt, self.max_retries, delay, str(e)[:120])
                time.sleep(delay)
                delay *= 2
        raise GeminiUnavailable(f"Gemini failed after {self.max_retries} attempts: {str(last)[:200]}") from last
