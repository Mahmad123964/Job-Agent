import requests

from .base import BaseNotifier
from ..config import get_settings
from ..models import Job


class TelegramNotifier(BaseNotifier):
    name = "telegram"

    def send(self, job: Job) -> None:
        settings = get_settings()
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured")

        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": settings.TELEGRAM_CHAT_ID,
                "text": self.format_message(job),
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        resp.raise_for_status()
