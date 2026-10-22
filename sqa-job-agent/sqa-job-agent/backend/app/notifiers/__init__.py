import logging

from .discord import DiscordNotifier
from .email import EmailNotifier
from .telegram import TelegramNotifier
from .whatsapp import WhatsAppNotifier
from ..config import get_settings
from ..models import Job

logger = logging.getLogger("sqa_job_agent.notifiers")

_REGISTRY = {
    "telegram": TelegramNotifier(),
    "discord": DiscordNotifier(),
    "email": EmailNotifier(),
    "whatsapp": WhatsAppNotifier(),
}


def email_deferred_to_digest() -> bool:
    """True when per-job emails are replaced by the daily digest (DIGEST_MODE=daily).
    Other channels are never affected; 'both' keeps per-job emails too."""
    settings = get_settings()
    return settings.DIGEST_MODE == "daily" and "email" in settings.PREFERRED_NOTIFIER


def notify_all(job: Job) -> list:
    """Send `job` through every channel listed in PREFERRED_NOTIFIER.

    A failure in one channel doesn't block the others. Returns the names of
    the channels that sent without raising (empty list = every channel
    failed), so the caller can record exactly where a job went out and
    never mark a job notified when nothing was delivered.
    """
    settings = get_settings()
    sent = []
    defer_email = email_deferred_to_digest()
    for channel in settings.PREFERRED_NOTIFIER:
        if channel == "email" and defer_email:
            logger.debug("DIGEST_MODE=daily: job %s queued for the daily digest instead of a per-job email", job.id)
            continue
        notifier = _REGISTRY.get(channel)
        if not notifier:
            logger.warning("Unknown notifier channel in PREFERRED_NOTIFIER: %s", channel)
            continue
        try:
            # A notifier may return False to say "skipped, not delivered" (WhatsApp no-op).
            if notifier.send(job) is not False:
                sent.append(channel)
        except Exception as e:
            logger.error("Notification via %s failed for job %s: %s", channel, job.id, e)
    return sent
