"""
Central configuration. Every credential/URL is read from the environment —
nothing is hardcoded. Copy backend/.env.example to backend/.env and fill in
real values before running.
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Load backend/.env (if present) before Settings reads os.environ. Real
# environment variables always win over values in the file.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings:
    # --- Database ---
    # e.g. postgresql+psycopg2://user:pass@host:5432/dbname  OR  sqlite:///./jobs.db
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///./jobs.db")  # [DATABASE_URL]

    # --- Polling ---
    POLL_INTERVAL_MINUTES: int = int(os.environ.get("POLL_INTERVAL_MINUTES", "15"))

    # --- Keyword / role filter (comma-separated, case-insensitive substring match) ---
    DEFAULT_KEYWORDS: List[str] = _split_csv(
        os.environ.get(
            "DEFAULT_KEYWORDS",
            "qa engineer,sqa,software quality assurance,quality engineer,"
            "test engineer,software tester,automation tester,automation engineer,"
            "sdet,quality assurance,qa tester,qa automation,sqa engineer,qa automation engineer,"
            "qa specialist,junior qa,tester,automation qa",
        )
    )

    # --- Notification channel selection ---
    # One or more of: telegram, discord, email, whatsapp
    PREFERRED_NOTIFIER: List[str] = _split_csv(os.environ.get("PREFERRED_NOTIFIER", "email"))  # [PREFERRED_NOTIFIER]

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")  # [API_KEYS] telegram bot token
    TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")

    # Discord
    DISCORD_WEBHOOK_URL: str = os.environ.get("DISCORD_WEBHOOK_URL", "")  # [API_KEYS] discord webhook

    # Email (SMTP)
    SMTP_HOST: str = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "465"))
    SMTP_USER: str = os.environ.get("SMTP_USER", "")
    SMTP_PASS: str = os.environ.get("SMTP_PASS", "")  # [API_KEYS] app password, never a raw account password
    EMAIL_TO: str = os.environ.get("EMAIL_TO", "")  # one address or a comma-separated list
    EMAIL_RECIPIENTS: List[str] = _split_csv(os.environ.get("EMAIL_TO", ""))

    # --- Optional licensed aggregator for Indeed/LinkedIn/Glassdoor coverage ---
    # Direct scraping of these platforms is not implemented — see adapters/jsearch.py
    # and README for why. This key is for a licensed re-indexing API (e.g. JSearch on RapidAPI).
    RAPIDAPI_KEY: str = os.environ.get("RAPIDAPI_KEY", "")  # [API_KEYS]
    JSEARCH_ENABLED: bool = os.environ.get("JSEARCH_ENABLED", "false").lower() == "true"
    JSEARCH_QUERY: str = os.environ.get("JSEARCH_QUERY", "QA engineer remote")
    JSEARCH_COUNTRY: str = os.environ.get("JSEARCH_COUNTRY", "us")  # ISO-3166 alpha-2 market to search, e.g. us, gb, pk
    JSEARCH_DATE_POSTED: str = os.environ.get("JSEARCH_DATE_POSTED", "month")  # all | month | week | 3days | today
    # Only keep JSearch results whose `job_publisher` is one of these (case-insensitive,
    # comma-separated). Empty = keep everything. Values confirmed against live responses:
    # LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster, CareerBuilder, Dice, Jobgether.
    JSEARCH_TRUSTED_PUBLISHERS: List[str] = _split_csv(os.environ.get(
        "JSEARCH_TRUSTED_PUBLISHERS", "LinkedIn,Indeed,Glassdoor,ZipRecruiter,Monster,CareerBuilder,Dice,Jobgether"
    ))

    # --- Local Hugging Face classifier (free, offline-capable; no paid LLM API) ---
    # Models are downloaded once from the HF Hub and cached under ~/.cache/huggingface.
    HF_RELEVANCE_MODEL: str = os.environ.get(
        "HF_RELEVANCE_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )  # [HF_RELEVANCE_MODEL]
    HF_ZEROSHOT_MODEL: str = os.environ.get("HF_ZEROSHOT_MODEL", "facebook/bart-large-mnli")  # [HF_ZEROSHOT_MODEL]
    HF_DEVICE: str = os.environ.get("HF_DEVICE", "cpu").lower()  # [HF_DEVICE] cpu | cuda | cuda:0 ...
    # Cosine similarity (0-1) against the SQA reference phrases at/above which a job is is_sqa_relevant.
    HF_RELEVANCE_THRESHOLD: float = float(os.environ.get("HF_RELEVANCE_THRESHOLD", "0.55"))  # calibrated: non-QA titles+text score 0.41-0.44
    # Description text is truncated to this many characters before inference (bart-large-mnli is slow on CPU).
    HF_MAX_TEXT_CHARS: int = int(os.environ.get("HF_MAX_TEXT_CHARS", "1500"))
    # Load models during FastAPI startup (true) or lazily on the first classification (false).
    HF_PRELOAD: bool = os.environ.get("HF_PRELOAD", "true").lower() == "true"

    # --- Fuzzy duplicate detection (dedup.py) ---
    DEDUP_LOOKBACK_DAYS: int = int(os.environ.get("DEDUP_LOOKBACK_DAYS", "30"))
    DEDUP_TITLE_THRESHOLD: int = int(os.environ.get("DEDUP_TITLE_THRESHOLD", "90"))    # rapidfuzz 0-100
    DEDUP_COMPANY_THRESHOLD: int = int(os.environ.get("DEDUP_COMPANY_THRESHOLD", "85"))  # rapidfuzz 0-100

    # Scores can be tuned without changing code.
    SCORE_SQA_RELEVANCE: int = int(os.environ.get("SCORE_SQA_RELEVANCE", "40"))
    SCORE_REMOTE: int = int(os.environ.get("SCORE_REMOTE", "20"))
    SCORE_JUNIOR_FRIENDLY: int = int(os.environ.get("SCORE_JUNIOR_FRIENDLY", "15"))
    SCORE_PAKISTAN_ELIGIBLE: int = int(os.environ.get("SCORE_PAKISTAN_ELIGIBLE", "15"))
    SCORE_FRESHNESS: int = int(os.environ.get("SCORE_FRESHNESS", "10"))

    # Only notify for jobs scoring at or above this (0-100). Default 0 = notify
    # for EVERY job that passes the keyword filter; the score is still computed
    # and stored for dashboard sorting. The dashboard's "score_threshold"
    # preference overrides this when set to a non-zero value.
    NOTIFY_SCORE_THRESHOLD: int = int(os.environ.get("NOTIFY_SCORE_THRESHOLD", "0"))  # [NOTIFY_SCORE_THRESHOLD]

    # Optional WhatsApp via Twilio's WhatsApp API (paid / approved sender required).
    # The notifier is a no-op unless all four values are set.
    TWILIO_ACCOUNT_SID: str = os.environ.get("TWILIO_ACCOUNT_SID", "")  # [TWILIO_ACCOUNT_SID]
    TWILIO_AUTH_TOKEN: str = os.environ.get("TWILIO_AUTH_TOKEN", "")  # [TWILIO_AUTH_TOKEN] [API_KEYS]
    TWILIO_WHATSAPP_FROM: str = os.environ.get("TWILIO_WHATSAPP_FROM", "")  # [TWILIO_WHATSAPP_FROM] e.g. whatsapp:+14155238886
    WHATSAPP_TO: str = os.environ.get("WHATSAPP_TO", "")  # [WHATSAPP_TO] e.g. whatsapp:+92XXXXXXXXXX

    # --- Hard freshness filter --- postings older than this are not saved or notified
    # (unknown posted_at is allowed through: missing data isn't penalised).
    MAX_JOB_AGE_DAYS: int = int(os.environ.get("MAX_JOB_AGE_DAYS", "14"))  # [MAX_JOB_AGE_DAYS]
    # ATS company-board sources (Greenhouse/Ashby/Lever, source_category="ats_board")
    # keep a posting listed until it's filled — weeks/months, not days — so they get a
    # longer freshness window than aggregator/RSS feeds, which surface new postings daily.
    MAX_JOB_AGE_DAYS_ATS_BOARDS: int = int(os.environ.get("MAX_JOB_AGE_DAYS_ATS_BOARDS", "30"))  # [MAX_JOB_AGE_DAYS_ATS_BOARDS]
    # Postings with no parseable date can't be proven fresh — a 30+ day old undated
    # JSearch listing got notified under the old "allow through" rule. Default: exclude.
    ALLOW_UNDATED_JOBS: bool = os.environ.get("ALLOW_UNDATED_JOBS", "false").lower() == "true"

    # --- Notification ledger --- fingerprints of everything ever notified, kept in a
    # file OUTSIDE the database so a recreated jobs.db can't re-notify the same postings.
    NOTIFY_LEDGER_PATH: str = os.environ.get("NOTIFY_LEDGER_PATH", "./notified_ledger.jsonl")
    NOTIFY_LEDGER_DAYS: int = int(os.environ.get("NOTIFY_LEDGER_DAYS", "45"))

    # --- Deep region check --- a job classified eligible_region="Unclear" from its
    # feed/API description often states eligibility clearly on its own posting page.
    # Fetch that page once and re-run geo.py against the fuller text before excluding
    # the job at the notification gate. Result is cached (outside the DB, like the
    # notification ledger) so the same posting is never fetched twice.
    DEEP_REGION_CHECK_ENABLED: bool = os.environ.get("DEEP_REGION_CHECK_ENABLED", "true").lower() == "true"
    DEEP_REGION_FETCH_TIMEOUT_SECONDS: int = int(os.environ.get("DEEP_REGION_FETCH_TIMEOUT_SECONDS", "15"))
    DEEP_REGION_CACHE_PATH: str = os.environ.get("DEEP_REGION_CACHE_PATH", "./deep_region_cache.jsonl")
    # Sources never fetched for a deep check: JSearch aggregates LinkedIn/Indeed/Glassdoor
    # (source="jsearch" regardless of the underlying publisher), which this project never
    # scrapes directly — link-only, same as elsewhere. Comma-separated, extendable per source.
    DEEP_REGION_EXCLUDED_SOURCES: str = os.environ.get("DEEP_REGION_EXCLUDED_SOURCES", "jsearch")

    # --- Follow-up reminders (feature 1) ---
    FOLLOW_UP_DAYS: int = int(os.environ.get("FOLLOW_UP_DAYS", "7"))  # [FOLLOW_UP_DAYS]

    # --- Daily digest email (feature 3) ---
    # off  = per-job emails as before | daily = ONE summary email per day instead of
    # per-job emails | both = per-job emails AND the daily summary.
    DIGEST_MODE: str = os.environ.get("DIGEST_MODE", "off").strip().lower()  # [DIGEST_MODE]
    DIGEST_SEND_TIME: str = os.environ.get("DIGEST_SEND_TIME", "08:00").strip()  # [DIGEST_SEND_TIME] HH:MM, server local time

    # --- Description-based recall (secondary path). A generic engineering title that
    # fails the keyword filter is still admitted when the DESCRIPTION alone embeds at or
    # above this calibrated relevance (0-1) against the SQA reference phrases. 0 disables.
    DESCRIPTION_RECALL_THRESHOLD: float = float(os.environ.get("DESCRIPTION_RECALL_THRESHOLD", "0.70"))
    DESCRIPTION_RECALL_MIN_CHARS: int = int(os.environ.get("DESCRIPTION_RECALL_MIN_CHARS", "200"))

    # --- Per-adapter deadline (seconds). A source that hasn't returned by then is logged
    # as a failed adapter for this poll and the others continue.
    ADAPTER_TIMEOUT_SECONDS: int = int(os.environ.get("ADAPTER_TIMEOUT_SECONDS", "60"))

    # --- Admin API key --- required (X-Api-Key header) on every non-GET route. Empty
    # disables the check (local dev only); the app logs a warning at startup in that case.
    ADMIN_API_KEY: str = os.environ.get("ADMIN_API_KEY", "").strip()  # [ADMIN_API_KEY] [API_KEYS]

    # --- CORS --- FRONTEND_ORIGINS (plural, comma-separated) plus the legacy FRONTEND_ORIGIN.
    # Any localhost / 127.0.0.1 origin on any port is also allowed (Vite falls back to
    # 5174, 5175, ... when 5173 is busy); never a wildcard.
    FRONTEND_ORIGIN: str = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")
    FRONTEND_ORIGINS: List[str] = _split_csv(os.environ.get("FRONTEND_ORIGINS", "")) or [
        f"http://localhost:{p}" for p in range(5173, 5180)
    ] + ["http://localhost:4173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
