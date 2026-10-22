import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Iterable, List, Optional

from .base import BaseNotifier
from ..config import get_settings
from ..models import Job


class EmailNotifier(BaseNotifier):
    name = "email"

    @staticmethod
    def recipients() -> list:
        """EMAIL_TO may be one address or a comma-separated list."""
        return get_settings().EMAIL_RECIPIENTS

    def _deliver(self, subject: str, body: str, recipients: Optional[List[str]] = None) -> None:
        """Send one message. `recipients` defaults to EMAIL_TO; an explicit list
        (backfill) bypasses it but still needs the SMTP credentials."""
        settings = get_settings()
        recipients = recipients or self.recipients()
        if not settings.SMTP_USER or not settings.SMTP_PASS or not recipients:
            raise RuntimeError("SMTP_USER / SMTP_PASS / EMAIL_TO not configured")

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_USER
        msg["To"] = ", ".join(recipients)  # display header; the envelope list below is what actually delivers

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.sendmail(settings.SMTP_USER, recipients, msg.as_string())

    def send(self, job: Job) -> None:
        self._deliver(f"New SQA job: {job.title}", self.format_message(job))

    # --- daily digest (DIGEST_MODE=daily|both) ---
    def format_digest_message(self, jobs: Iterable[Job]) -> str:
        jobs = list(jobs)
        lines = [f"{len(jobs)} new SQA job(s) since your last digest:", ""]
        for i, job in enumerate(sorted(jobs, key=lambda j: (j.score or 0), reverse=True), 1):
            lines.append(f"{i}. [{job.score or 0}/100] {job.title} — {job.company or 'Unknown'} ({job.source})")
            extras = [job.location or ""]
            if job.eligible_region and job.eligible_region != "Unclear":
                extras.append(job.eligible_region if job.eligible_region != "Specific country" else f"{job.eligible_country} only")
            if job.resume_match_score is not None:
                extras.append(f"resume match {job.resume_match_score}%")
            lines.append("   " + " · ".join(e for e in extras if e))
            lines.append(f"   {job.url}")
            lines.append("")
        return "\n".join(lines).rstrip()

    # --- one-off history backfill for a new recipient (POST /api/admin/backfill-email) ---
    BACKFILL_BATCH = 100      # jobs per email once the list is long enough to split
    BACKFILL_SPLIT_ABOVE = 150

    def format_backfill_message(self, jobs: List[Job], part: int, parts: int, total: int) -> str:
        first, last = (part - 1) * self.BACKFILL_BATCH + 1, (part - 1) * self.BACKFILL_BATCH + len(jobs)
        header = f"SQA Job Agent — full notification history: {total} job(s)"
        if parts > 1:
            header += f" (part {part} of {parts}, jobs {first}-{last})"
        lines = [header, "Every job the agent has notified so far, oldest first.", ""]
        for i, job in enumerate(jobs, first):
            when = job.notified_at.strftime("%Y-%m-%d %H:%M UTC") if job.notified_at else "unknown time"
            lines.append(f"{i}. [{job.score or 0}/100] {job.title}")
            lines.append(f"   Company: {job.company or 'Unknown'} · Source: {job.source} · Notified: {when}")
            extras = [job.location or ""]
            if job.seniority and job.seniority != "unknown":
                extras.append(job.seniority)
            if job.eligible_region and job.eligible_region != "Unclear":
                extras.append(job.eligible_region if job.eligible_region != "Specific country" else f"{job.eligible_country} only")
            if job.application_status and job.application_status != "not_applied":
                extras.append(f"status: {job.application_status}")
            lines.append("   " + " · ".join(e for e in extras if e))
            lines.append(f"   {job.url}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def send_backfill_summary(self, recipient: str, jobs: List[Job]) -> List[dict]:
        """Email the whole history to ONE explicit recipient, split into parts
        of BACKFILL_BATCH when there are more than BACKFILL_SPLIT_ABOVE jobs.
        Read-only: touches no job rows. Returns one dict per email sent."""
        jobs = list(jobs)
        total = len(jobs)
        if total == 0:
            return []
        batches = [jobs[i:i + self.BACKFILL_BATCH] for i in range(0, total, self.BACKFILL_BATCH)] if total > self.BACKFILL_SPLIT_ABOVE else [jobs]
        parts = len(batches)
        sent = []
        for part, batch in enumerate(batches, 1):
            subject = f"SQA Job Agent — full history ({total} jobs)"
            if parts > 1:
                subject += f" — Part {part} of {parts}"
            self._deliver(subject, self.format_backfill_message(batch, part, parts, total), recipients=[recipient])
            sent.append({"part": part, "parts": parts, "jobs": len(batch), "subject": subject})
        return sent

    def send_digest(self, jobs: Iterable[Job]) -> int:
        """Send ONE email listing `jobs`. Returns the number of jobs included (0 = nothing sent)."""
        jobs = list(jobs)
        if not jobs:
            return 0
        self._deliver(f"SQA job digest: {len(jobs)} new job(s)", self.format_digest_message(jobs))
        return len(jobs)
