"""Owner notifications over several channels: Gmail (existing), Slack webhook, WhatsApp via CallMeBot.

Usage:
    python -m agent.notifications test   # send a sample message to every enabled channel

Channels are toggled in config.yaml (`notifications.channels`); secrets live in .env:
SLACK_WEBHOOK_URL, CALLMEBOT_PHONE, CALLMEBOT_APIKEY.
Email is the primary channel and behaves exactly as before (its errors propagate and
its thread id is returned so the approval loop can watch for replies). Slack and
WhatsApp are best-effort: a failure is logged as a warning and never blocks the
email or crashes the run.
"""
import logging
import os
import sys
from typing import Optional
from urllib.parse import quote

import requests

from .config import load_config

log = logging.getLogger(__name__)

CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
WHATSAPP_BODY_LIMIT = 1500  # keep the GET URL well under common length limits


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _clip(text: str, limit: int = WHATSAPP_BODY_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 30].rstrip() + "\n...(truncated, see email)"


class Notifier:
    def __init__(self, cfg: dict):
        n = cfg.get("notifications") or {}
        self.owner_email = cfg["owner_email"]
        self.channels = [c.lower() for c in n.get("channels", ["email"])]
        self.timeout = float(n.get("timeout_seconds", 10))

    # ---- public --------------------------------------------------------
    def send(self, subject: str, body: str, gmail=None) -> dict[str, Optional[str]]:
        """Deliver one notification to every enabled channel.

        Returns {channel: id-or-"ok" on success, None on failure}. Email goes first and
        raises on failure like before; the others never raise.
        """
        results: dict[str, Optional[str]] = {}
        if "email" in self.channels:
            if gmail is None:
                raise ValueError("email channel is enabled but no Gmail client was given")
            results["email"] = gmail.send_mail(self.owner_email, subject, body)["threadId"]
        text = f"{subject}\n\n{body}"
        if "slack" in self.channels:
            results["slack"] = self._safe("slack", self._slack, text)
        if "whatsapp" in self.channels:
            results["whatsapp"] = self._safe("whatsapp", self._whatsapp, text)
        return results

    # ---- channels ------------------------------------------------------
    def _safe(self, name: str, fn, *args) -> Optional[str]:
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 - a side channel must never break the run
            log.warning("%s notification failed (continuing): %s", name, str(e)[:200])
            return None

    def _slack(self, text: str) -> str:
        webhook = _env("SLACK_WEBHOOK_URL")
        if not webhook:
            raise RuntimeError("SLACK_WEBHOOK_URL is not set")
        resp = requests.post(webhook, json={"text": text}, timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"webhook returned {resp.status_code}: {resp.text[:100]}")
        return "ok"

    def _whatsapp(self, text: str) -> str:
        """CallMeBot free API: one GET with phone, url-encoded text and the personal apikey."""
        phone, apikey = _env("CALLMEBOT_PHONE"), _env("CALLMEBOT_APIKEY")
        if not (phone and apikey):
            raise RuntimeError("CALLMEBOT_PHONE / CALLMEBOT_APIKEY not set")
        url = f"{CALLMEBOT_URL}?phone={quote(phone, safe='')}&text={quote(_clip(text), safe='')}&apikey={quote(apikey, safe='')}"
        resp = requests.get(url, timeout=self.timeout)
        # CallMeBot answers 200 with an HTML page; it puts error text in the body instead of a status.
        low = resp.text.lower()
        if resp.status_code != 200 or ("error" in low[:400] and "message queued" not in low):
            raise RuntimeError(f"callmebot returned {resp.status_code}: {resp.text[:120]}")
        return "ok"


def notify(gmail, cfg: dict, subject: str, body: str) -> Optional[str]:
    """Send to all enabled channels; returns the Gmail thread id (None if email is disabled)."""
    return Notifier(cfg).send(subject, body, gmail).get("email")


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not argv or argv[0] != "test":
        raise SystemExit(__doc__)
    cfg = load_config()
    notifier = Notifier(cfg)
    print("Enabled channels:", ", ".join(notifier.channels) or "(none)")
    gmail = None
    if "email" in notifier.channels:
        from .gmail_client import Gmail

        gmail = Gmail(cfg["gmail_credentials_file"], cfg["gmail_token_file"])
    results = notifier.send(
        "[JobAgent] Test notification",
        "This is a test message from `python -m agent.notifications test`.\nIf you can read this, the channel works.",
        gmail,
    )
    for channel, result in results.items():
        print(f"  {channel:9s} {'OK (' + result + ')' if result else 'FAILED - see warning above'}")


if __name__ == "__main__":
    main(sys.argv[1:])
