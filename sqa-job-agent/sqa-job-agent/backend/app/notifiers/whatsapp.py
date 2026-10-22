"""WhatsApp notifier via Twilio's WhatsApp API.

Twilio requires a paid account and an approved WhatsApp sender (or the
sandbox number for testing), so this notifier is a deliberate no-op unless
TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM and WHATSAPP_TO
are all set. Numbers must be in Twilio's "whatsapp:+E164" form, e.g.
    TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
    WHATSAPP_TO=whatsapp:+923001234567

Note: outside a 24-hour customer-initiated session WhatsApp only delivers
pre-approved template messages; free-form alerts like this one require the
recipient to have messaged the sender first (or to use the sandbox).
"""
import logging

from .base import BaseNotifier
from ..config import get_settings
from ..models import Job

logger = logging.getLogger("sqa_job_agent.notifiers.whatsapp")


class WhatsAppNotifier(BaseNotifier):
    name = "whatsapp"

    def __init__(self) -> None:
        self._client = None  # created lazily so importing twilio isn't required unless configured

    def _configured(self) -> bool:
        s = get_settings()
        return all((s.TWILIO_ACCOUNT_SID, s.TWILIO_AUTH_TOKEN, s.TWILIO_WHATSAPP_FROM, s.WHATSAPP_TO))

    def _get_client(self):
        if self._client is None:
            from twilio.rest import Client  # [TWILIO_ACCOUNT_SID] / [TWILIO_AUTH_TOKEN]

            s = get_settings()
            self._client = Client(s.TWILIO_ACCOUNT_SID, s.TWILIO_AUTH_TOKEN)
        return self._client

    @staticmethod
    def _whatsapp_address(value: str) -> str:
        value = value.strip()
        return value if value.startswith("whatsapp:") else f"whatsapp:{value}"

    def send(self, job: Job) -> bool:
        if not self._configured():
            logger.debug("WhatsApp notifier not configured; skipping job %s", job.id)
            return False  # Deliberate no-op until Twilio is configured; False = "not delivered" for the log.

        s = get_settings()
        message = self._get_client().messages.create(
            from_=self._whatsapp_address(s.TWILIO_WHATSAPP_FROM),
            to=self._whatsapp_address(s.WHATSAPP_TO),
            body=self.format_message(job),
        )
        logger.info("WhatsApp message %s queued for job %s", message.sid, job.id)
        return True
