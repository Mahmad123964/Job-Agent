"""Scheduled ingestion pipeline.

Per poll, every adapter result goes through:

    normalize -> keyword/location filter -> dedup check -> classify -> score
             -> save -> notify (only if score >= threshold; default threshold 0)

Cheap stages run first so the classifier (the only expensive step — local
Hugging Face inference on CPU) only sees jobs that can actually be stored.
"""
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .adapters import ADAPTERS
from .classifier import classify, description_relevance
from .config import get_settings
from .database import SessionLocal
from .dedup import DedupKey, is_duplicate, load_recent_keys
from .deep_region import DeepRegionCache, deep_check, is_deep_checkable
from .filters import is_generic_engineering_title, matches_keywords, matches_location
from .ledger import NotificationLedger, fingerprints
from .models import BlockedCompany, Job, PollRun, Preference
from .normalizer import invalid_url_reason, normalize
from .notifiers import email_deferred_to_digest, notify_all
from .notifiers.email import EmailNotifier
from .scoring import score

logger = logging.getLogger("sqa_job_agent.scheduler")


def _get_preference(db: Session) -> Preference:
    pref = db.query(Preference).filter(Preference.id == 1).first()
    if not pref:
        pref = Preference(id=1, keywords="", locations="", excluded_sources="")
        db.add(pref)
        db.commit()
        db.refresh(pref)
    return pref


def _notify_threshold(pref: Preference) -> int:
    """Dashboard preference (when non-zero) overrides the NOTIFY_SCORE_THRESHOLD env var.

    Both default to 0, so out of the box every saved job is notified.
    """
    return pref.score_threshold or get_settings().NOTIFY_SCORE_THRESHOLD


# Every fetched job ends in exactly one of these buckets, so
#   fetched == sum(OUTCOMES)   always holds for the poll log line.
OUTCOMES = (
    "skipped_invalid",         # normalize() left no title
    "skipped_invalid_url",     # apply URL empty, not http(s), no host, or a placeholder domain (example.com)
    "skipped_blocked_company", # company is on the BlockedCompany list (case-insensitive)
    "skipped_too_old",         # posted_at older than MAX_JOB_AGE_DAYS
    "skipped_undated",         # no parseable posted_at and ALLOW_UNDATED_JOBS=false (can't be proven fresh)
    "skipped_region_restricted",  # classified as a specific country/region that isn't Pakistan-eligible or Worldwide
    "skipped_region_unclear",     # no evidence either way (e.g. bare "Remote") — not confirmed eligible, so not saved
    "skipped_no_keyword",      # no DEFAULT_KEYWORDS / preference keyword in title+tags
    "skipped_location",        # keyword matched but the location allow-list didn't
    "skipped_duplicate",       # exact or fuzzy duplicate of a recent job (dedup.py)
    "skipped_classify_error",  # classifier raised; job dropped, error logged
    "skipped_save_conflict",   # UNIQUE(source, external_id) hit on commit (concurrent poll)
    "saved",                   # stored in the jobs table
)
# Post-save outcomes: every saved job ends in exactly one of these.
NOTIFY_OUTCOMES = ("notified", "notify_failed", "below_threshold", "seniority_mismatch", "queued_for_digest", "already_notified")

# Deep region check (app/deep_region.py) stats — a secondary enrichment applied only
# to jobs that would otherwise land in skipped_region_unclear, so these are NOT part
# of OUTCOMES (they don't change the fetched == sum(OUTCOMES) invariant; every one of
# these jobs still ends up in exactly one OUTCOMES bucket, same as before).
DEEP_REGION_OUTCOMES = (
    "deep_region_checked",                 # Unclear job actually went through the deep check (fresh fetch or cache hit)
    "deep_region_reclassified_worldwide",  # deep check found explicit Worldwide/Anywhere phrasing -> now passes the gate
    "deep_region_confirmed_restricted",    # deep check found a specific country/region -> correctly stays excluded
    "deep_region_still_unclear",           # fetch failed/timed out, or the fuller text still had no clear signal
)


def is_notification_eligible(classification: dict) -> bool:
    """Strict rule: only an explicitly Worldwide posting, or one whose region
    includes Pakistan, is saved/notified. "Unclear" (bare "Remote", no other
    evidence) is NOT enough — the posting must be positively confirmed."""
    region = classification.get("eligible_region") or "Unclear"
    if region == "Worldwide":
        return True
    if region == "Unclear":
        return False
    return bool(classification.get("pakistan_eligible"))


def max_age_days_for(source_category: str, settings=None) -> int:
    """The freshness window for a given adapter's source_category. ATS company
    boards (Greenhouse/Ashby/Lever) list a job until it's filled, so they get
    MAX_JOB_AGE_DAYS_ATS_BOARDS instead of the aggregator default."""
    settings = settings or get_settings()
    if source_category == "ats_board":
        return settings.MAX_JOB_AGE_DAYS_ATS_BOARDS
    return settings.MAX_JOB_AGE_DAYS


def is_too_old(posted_at, now=None, max_days=None, source_category="aggregator") -> bool:
    """True when posted_at is known and older than the category's freshness
    window. None (undated) -> False; that's handled separately."""
    if posted_at is None:
        return False
    max_days = max_days if max_days is not None else max_age_days_for(source_category)
    now = now or datetime.now(timezone.utc)
    ts = posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=timezone.utc)
    return (now - ts) > timedelta(days=max_days)


RETRY_WINDOW_HOURS = 24


def retry_failed_notifications(db: Session, ledger: NotificationLedger, threshold: int, preferred_seniority: str) -> dict:
    """Re-attempt notify_all() for jobs saved in the last RETRY_WINDOW_HOURS whose
    every channel failed (notified=False). Idempotent: the ledger is consulted
    first, so a job that was actually delivered (e.g. by the daily digest or a
    previous process) is never re-sent. Digest-queued jobs are left alone."""
    result = {"retried_ok": 0, "retry_failed": 0}
    if email_deferred_to_digest() and get_settings().PREFERRED_NOTIFIER == ["email"]:
        return result  # nothing to retry: email goes out in the digest
    since = (datetime.now(timezone.utc) - timedelta(hours=RETRY_WINDOW_HOURS)).replace(tzinfo=None)
    candidates = (
        db.query(Job)
        .filter(Job.notified.is_(False), Job.archived.isnot(True), Job.discovered_at >= since, Job.score >= threshold)
        .order_by(Job.discovered_at.asc())
        .all()
    )
    for job in candidates:
        if job.notified_channels and job.notified_channels.startswith("ledger:"):
            continue  # deliberately not sent (already notified before a DB reset)
        if preferred_seniority and job.seniority != preferred_seniority:
            continue
        keys = fingerprints(job.source, job.external_id, job.title, job.company, job.url)
        if ledger.already_notified(keys):
            job.notified = True
            job.notified_at = job.notified_at or datetime.now(timezone.utc)
            job.notified_channels = "ledger:recovered"
            db.commit()
            continue
        sent_via = notify_all(job)
        if sent_via:
            job.notified = True
            job.notified_at = datetime.now(timezone.utc)
            job.notified_channels = ",".join(sent_via)
            db.commit()
            ledger.record(keys, sent_via, job.title)
            result["retried_ok"] += 1
            logger.info("Retry delivered %s/%s (%r) via %s", job.source, job.external_id, job.title, sent_via)
        else:
            result["retry_failed"] += 1
    if result["retried_ok"] or result["retry_failed"]:
        logger.info("Notification retry: delivered=%d still_failing=%d", result["retried_ok"], result["retry_failed"])
    return result


def description_recall_match(normalized) -> Optional[str]:
    """Return a synthetic keyword like "desc:0.83" when a generic engineering
    title has a description that embeds as QA-focused; else None. One cheap
    MiniLM embedding; never runs for titles the keyword filter already accepted."""
    settings = get_settings()
    threshold = settings.DESCRIPTION_RECALL_THRESHOLD
    if threshold <= 0 or not is_generic_engineering_title(normalized.title):
        return None
    if len(normalized.description or "") < settings.DESCRIPTION_RECALL_MIN_CHARS:
        return None
    score = description_relevance(normalized.description)
    if score is not None and score >= threshold:
        return f"desc:{score:.2f}"
    return None


def fetch_with_deadline(adapter, timeout: float):
    """Run adapter.fetch() in a worker thread with a hard deadline. On timeout the
    caller moves on; the worker is left to finish (daemon pool) so a stuck socket
    can't block the scheduler. Raises concurrent.futures.TimeoutError."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"fetch-{adapter.name}")
    future = pool.submit(adapter.fetch)
    pool.shutdown(wait=False)
    return future.result(timeout=timeout)


def normalize_company_name(name: str) -> str:
    return " ".join((name or "").lower().split())


def load_blocked_companies(db: Session) -> set:
    """Lower-cased, whitespace-normalised names — loaded once per poll."""
    return {normalize_company_name(name) for (name,) in db.query(BlockedCompany.company_name).all() if name}


def poll_all_sources() -> dict:
    """Run one full poll. Returns the stats dict (also logged) for tests/manual runs."""
    settings = get_settings()
    db = SessionLocal()
    try:
        pref = _get_preference(db)
        keywords = [k.strip() for k in pref.keywords.split(",") if k.strip()] or settings.DEFAULT_KEYWORDS
        locations = [l.strip() for l in pref.locations.split(",") if l.strip()]
        excluded = {s.strip() for s in pref.excluded_sources.split(",") if s.strip()}
        threshold = _notify_threshold(pref)
        preferred_seniority = (pref.seniority or "").strip().lower()

        # One query per poll; keys for jobs saved during this poll are appended
        # so the same role cross-posted on two boards is caught within a poll too.
        recent_keys = load_recent_keys(db)
        blocked = load_blocked_companies(db)
        ledger = NotificationLedger()
        resume_text = (pref.resume_text or "").strip() or None

        stats = {
            "fetched": 0,
            **{name: 0 for name in OUTCOMES},
            **{name: 0 for name in NOTIFY_OUTCOMES},
            **{name: 0 for name in DEEP_REGION_OUTCOMES},
        }
        adapter_errors = []
        deep_region_cache = DeepRegionCache() if settings.DEEP_REGION_CHECK_ENABLED else None

        # 0. Retry deliveries that failed on an earlier cycle (roadmap item 6).
        #    Counted separately ("retried_ok"/"retry_failed") so the per-poll
        #    fetched/saved invariants are untouched.
        stats.update(retry_failed_notifications(db, ledger, threshold, preferred_seniority))
        per_source = {}
        run = PollRun(started_at=datetime.now(timezone.utc))

        for adapter in ADAPTERS:
            if adapter.name in excluded:
                continue
            try:
                raw_jobs = fetch_with_deadline(adapter, settings.ADAPTER_TIMEOUT_SECONDS)
            except FutureTimeout:
                logger.error("Adapter %s timed out after %ss; skipping this poll", adapter.name, settings.ADAPTER_TIMEOUT_SECONDS)
                adapter_errors.append(adapter.name)
                continue
            except Exception as e:
                logger.error("Adapter %s failed: %s", adapter.name, e)
                adapter_errors.append(adapter.name)
                continue
            logger.info("Adapter %s returned %d job(s)", adapter.name, len(raw_jobs))
            per_source[adapter.name] = len(raw_jobs)

            for raw in raw_jobs:
                stats["fetched"] += 1

                # 1. normalize
                normalized = normalize(raw)
                if not normalized.title:
                    stats["skipped_invalid"] += 1
                    continue
                url_problem = invalid_url_reason(normalized.apply_url)
                if url_problem:
                    stats["skipped_invalid_url"] += 1
                    logger.warning(
                        "Rejected %s/%s (%r): apply URL %r is invalid (%s)",
                        normalized.source, normalized.external_id, normalized.title, normalized.apply_url, url_problem,
                    )
                    continue

                # 2. cheap filters before anything expensive
                if is_too_old(normalized.posted_at, source_category=adapter.source_category):
                    stats["skipped_too_old"] += 1
                    continue
                if normalized.posted_at is None and not settings.ALLOW_UNDATED_JOBS:
                    stats["skipped_undated"] += 1
                    continue
                if normalize_company_name(normalized.company) in blocked:
                    stats["skipped_blocked_company"] += 1
                    continue
                matched = matches_keywords(raw, keywords)
                if not matched:
                    # Secondary recall: a generic engineering title whose description
                    # is clearly about software QA (roadmap item 4).
                    matched = description_recall_match(normalized)
                if not matched:
                    stats["skipped_no_keyword"] += 1
                    continue
                if not matches_location(raw, locations):
                    stats["skipped_location"] += 1
                    continue

                # 3. dedup (exact + fuzzy cross-board)
                if is_duplicate(normalized, recent_keys):
                    stats["skipped_duplicate"] += 1
                    continue

                # 4. classify (local HF models, or rule fallback)
                try:
                    classification = classify(normalized, resume_text=resume_text)
                except Exception as exc:
                    stats["skipped_classify_error"] += 1
                    logger.error(
                        "Classification failed for %s/%s (%r): %s",
                        normalized.source, normalized.external_id, normalized.title, exc,
                    )
                    continue

                # 4b. region gate: explicit single-country/region restrictions that
                #     exclude Pakistan are neither saved nor notified.
                job_deep_checked = False
                if not is_notification_eligible(classification):
                    region = classification.get("eligible_region") or "Unclear"
                    # 4c. deep region check: the short feed/API description was Unclear —
                    # before excluding, fetch the job's own posting page once and re-run
                    # geo.py against the fuller text (skipped for JSearch/LinkedIn etc.).
                    if (
                        region == "Unclear"
                        and deep_region_cache is not None
                        and is_deep_checkable(normalized.source, settings)
                    ):
                        deep = deep_check(
                            source=normalized.source,
                            external_id=normalized.external_id,
                            url=normalized.apply_url,
                            title=normalized.title,
                            location=normalized.location,
                            cache=deep_region_cache,
                            company=normalized.company,
                        )
                        stats["deep_region_checked"] += 1
                        job_deep_checked = True
                        if deep.region != "Unclear":
                            classification["eligible_region"] = deep.region
                            classification["eligible_country"] = deep.country
                            classification["is_worldwide"] = deep.region == "Worldwide"
                            if deep.region == "Specific country":
                                classification["pakistan_eligible"] = deep.country == "Pakistan"
                            elif deep.region in ("Americas", "Europe", "Africa"):
                                classification["pakistan_eligible"] = False
                            region = deep.region
                            if deep.region == "Worldwide":
                                stats["deep_region_reclassified_worldwide"] += 1
                            else:
                                stats["deep_region_confirmed_restricted"] += 1
                            logger.info(
                                "Deep region check reclassified %s/%s (%r): Unclear -> %s (%s)",
                                normalized.source, normalized.external_id, normalized.title, deep.region, deep.evidence,
                            )
                        else:
                            stats["deep_region_still_unclear"] += 1

                if not is_notification_eligible(classification):
                    if (classification.get("eligible_region") or "Unclear") == "Unclear":
                        stats["skipped_region_unclear"] += 1
                    else:
                        stats["skipped_region_restricted"] += 1
                    logger.info("Skipping %s/%s (%r): restricted to %s", normalized.source, normalized.external_id, normalized.title,
                                classification.get("eligible_country") or classification.get("eligible_region"))
                    continue

                # 5. score — always computed and stored (dashboard sorting), even
                #    though the default threshold of 0 doesn't gate notifications on it
                job_score, breakdown = score(classification, normalized.posted_at)

                # 6. save every job that passed the filters
                job = Job(
                    source=normalized.source,
                    external_id=normalized.external_id,
                    title=normalized.title,
                    company=normalized.company,
                    url=normalized.apply_url,
                    location=normalized.location,
                    tags=",".join(normalized.tags),
                    matched_keyword=matched,
                    posted_at=normalized.posted_at,
                    description=normalized.description,
                    salary_min=normalized.salary_min,
                    salary_max=normalized.salary_max,
                    salary_currency=normalized.salary_currency,
                    is_sqa_relevant=classification.get("is_sqa_relevant", False),
                    seniority=classification.get("seniority", "unknown"),
                    is_remote=classification.get("is_remote", False),
                    is_worldwide=classification.get("is_worldwide", False),
                    pakistan_eligible=classification.get("pakistan_eligible", False),
                    eligible_region=classification.get("eligible_region", "Unclear"),
                    eligible_country=classification.get("eligible_country"),
                    deep_region_checked=job_deep_checked,
                    required_skills=classification.get("required_skills", []),
                    resume_match_score=classification.get("resume_match_score"),
                    score=job_score,
                    score_breakdown=breakdown,
                )
                db.add(job)
                try:
                    db.commit()
                except IntegrityError:
                    # Race with a concurrent poll-now on the (source, external_id) constraint.
                    db.rollback()
                    stats["skipped_save_conflict"] += 1
                    continue
                db.refresh(job)
                recent_keys.append(DedupKey.from_job(job))
                stats["saved"] += 1

                # 7. notify. With threshold 0 (default) every saved job is sent.
                if job_score < threshold:
                    stats["below_threshold"] += 1
                    continue
                if preferred_seniority and job.seniority != preferred_seniority:
                    stats["seniority_mismatch"] += 1
                    continue
                keys = fingerprints(job.source, job.external_id, job.title, job.company, job.url)
                seen_as = ledger.already_notified(keys)
                if seen_as:
                    # Same posting (or the same listing on another board) was already
                    # sent within NOTIFY_LEDGER_DAYS — e.g. before a DB recreation.
                    stats["already_notified"] += 1
                    job.notified_channels = "ledger:" + seen_as.split(":")[0]
                    db.commit()
                    logger.info("Not re-notifying %s/%s (%r): already sent as %s", job.source, job.external_id, job.title, seen_as)
                    continue
                sent_via = notify_all(job)
                if sent_via:
                    # Stamped only on a real delivery (>=1 channel returned without raising).
                    job.notified = True
                    job.notified_at = datetime.now(timezone.utc)
                    job.notified_channels = ",".join(sent_via)
                    db.commit()
                    ledger.record(keys, sent_via, job.title)
                    stats["notified"] += 1
                elif email_deferred_to_digest():
                    # DIGEST_MODE=daily with email as the (only) channel: the job
                    # goes out in the next daily digest; notified is stamped then.
                    stats["queued_for_digest"] += 1
                else:
                    # Every configured channel raised (see notifier error logs).
                    # The job stays notified=False so it's visible as undelivered.
                    stats["notify_failed"] += 1

        logger.info(
            "Poll complete: fetched=%d | skipped_invalid=%d skipped_invalid_url=%d skipped_blocked_company=%d "
            "skipped_too_old=%d skipped_undated=%d skipped_no_keyword=%d skipped_location=%d skipped_duplicate=%d "
            "skipped_classify_error=%d skipped_region_restricted=%d skipped_region_unclear=%d skipped_save_conflict=%d saved=%d | notified=%d notify_failed=%d "
            "below_threshold=%d seniority_mismatch=%d queued_for_digest=%d already_notified=%d retried_ok=%d retry_failed=%d "
            "| deep_region_checked=%d deep_region_reclassified_worldwide=%d deep_region_confirmed_restricted=%d deep_region_still_unclear=%d "
            "(threshold=%d, max_age_days=%d, adapter_errors=%s)",
            stats["fetched"], stats["skipped_invalid"], stats["skipped_invalid_url"], stats["skipped_blocked_company"],
            stats["skipped_too_old"], stats["skipped_undated"], stats["skipped_no_keyword"], stats["skipped_location"], stats["skipped_duplicate"],
            stats["skipped_classify_error"], stats["skipped_region_restricted"], stats["skipped_region_unclear"], stats["skipped_save_conflict"], stats["saved"], stats["notified"],
            stats["notify_failed"], stats["below_threshold"], stats["seniority_mismatch"], stats["queued_for_digest"],
            stats["already_notified"], stats["retried_ok"], stats["retry_failed"],
            stats["deep_region_checked"], stats["deep_region_reclassified_worldwide"], stats["deep_region_confirmed_restricted"], stats["deep_region_still_unclear"],
            threshold, settings.MAX_JOB_AGE_DAYS, ",".join(adapter_errors) or "none",
        )
        accounted = sum(stats[name] for name in OUTCOMES)
        if accounted != stats["fetched"]:
            logger.error("Poll accounting mismatch: fetched=%d but outcomes sum to %d", stats["fetched"], accounted)
        notify_accounted = sum(stats[name] for name in NOTIFY_OUTCOMES)
        if notify_accounted != stats["saved"]:
            logger.error("Poll accounting mismatch: saved=%d but notify outcomes sum to %d", stats["saved"], notify_accounted)

        # Persist this cycle (the numbers above) so history survives restarts.
        try:
            run.finished_at = datetime.now(timezone.utc)
            run.fetched = stats["fetched"]
            run.saved = stats["saved"]
            run.notified = stats["notified"]
            run.duplicate = stats["skipped_duplicate"]
            run.no_keyword = stats["skipped_no_keyword"]
            run.blocked_company = stats["skipped_blocked_company"]
            run.invalid_url = stats["skipped_invalid_url"]
            run.too_old = stats["skipped_too_old"]
            run.already_notified = stats["already_notified"]
            run.adapter_errors = ",".join(adapter_errors)
            run.per_source = per_source
            run.stats = dict(stats)
            db.add(run)
            db.commit()
        except Exception as exc:  # never let bookkeeping break the poll
            db.rollback()
            logger.error("Could not persist PollRun: %s", exc)
        return stats
    finally:
        db.close()


def send_daily_digest(now: datetime | None = None) -> dict:
    """Send ONE email listing every job saved since the last digest (DIGEST_MODE
    daily|both). The watermark lives in Preference.last_digest_sent_at so a
    restart never re-sends a stale digest. Jobs below the notify threshold are
    left out, matching the per-job rule. Returns a small stats dict."""
    now = now or datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        pref = _get_preference(db)
        since = pref.last_digest_sent_at or (now - timedelta(days=1))
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        threshold = _notify_threshold(pref)
        ledger = NotificationLedger()
        jobs = [
            j for j in db.query(Job).filter(Job.discovered_at > since, Job.score >= threshold).order_by(Job.score.desc()).all()
            if not ledger.already_notified(fingerprints(j.source, j.external_id, j.title, j.company, j.url))
        ]
        result = {"since": since.isoformat(), "jobs": len(jobs), "sent": False}
        if not jobs:
            pref.last_digest_sent_at = now
            db.commit()
            logger.info("Daily digest: nothing new since %s", since.isoformat())
            return result
        try:
            EmailNotifier().send_digest(jobs)
        except Exception as exc:
            logger.error("Daily digest failed (%d job(s) kept for the next run): %s", len(jobs), exc)
            return result
        # Mark the batch as delivered so the notification log stays truthful.
        for job in jobs:
            channels = [c for c in (job.notified_channels or "").split(",") if c]
            if "email-digest" not in channels:
                channels.append("email-digest")
            job.notified_channels = ",".join(channels)
            job.notified = True
            job.notified_at = job.notified_at or now
            ledger.record(fingerprints(job.source, job.external_id, job.title, job.company, job.url), ["email-digest"], job.title)
        pref.last_digest_sent_at = now
        db.commit()
        result["sent"] = True
        logger.info("Daily digest sent: %d job(s) since %s", len(jobs), since.isoformat())
        return result
    finally:
        db.close()


def _digest_trigger(send_time: str) -> CronTrigger:
    hour, minute = (int(part) for part in send_time.split(":")[:2])
    return CronTrigger(hour=hour, minute=minute)  # server local time


def start_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        poll_all_sources,
        "interval",
        minutes=settings.POLL_INTERVAL_MINUTES,
        id="poll_all_sources",
        max_instances=1,
        coalesce=True,
    )
    if settings.DIGEST_MODE in ("daily", "both"):
        scheduler.add_job(
            send_daily_digest,
            _digest_trigger(settings.DIGEST_SEND_TIME),
            id="send_daily_digest",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        logger.info("Daily digest enabled (DIGEST_MODE=%s) at %s local time", settings.DIGEST_MODE, settings.DIGEST_SEND_TIME)
    scheduler.start()
    return scheduler
