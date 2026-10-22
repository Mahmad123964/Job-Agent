import requests

from .base import BaseNotifier
from ..config import get_settings
from ..models import Job


class DiscordNotifier(BaseNotifier):
    name = "discord"

    def send(self, job: Job) -> None:
        settings = get_settings()
        if not settings.DISCORD_WEBHOOK_URL:
            raise RuntimeError("DISCORD_WEBHOOK_URL not configured")

        resp = requests.post(
            settings.DISCORD_WEBHOOK_URL,
            json={"content": self.format_message(job)},
            timeout=10,
        )
        resp.raise_for_status()
