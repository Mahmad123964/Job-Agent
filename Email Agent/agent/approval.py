"""Owner approval loop.

Every HIGH match produces a Gmail draft plus a notification email to the owner.
The owner replies to that notification with APPROVE (send the draft) or REJECT
(leave it alone). Nothing is ever sent without an APPROVE reply.
"""
import logging

from .gmail_client import Gmail
from .notifications import notify
from .state import State

log = logging.getLogger(__name__)


def notify_owner(gmail: Gmail, cfg: dict, record: dict) -> str:
    subject = f"{cfg['approval']['subject_prefix']}: {record['role'] or 'role unknown'} — {record['sender_email']}"
    body = (
        f"A new job email was matched with HIGH confidence.\n\n"
        f"From:       {record['sender']}\n"
        f"Subject:    {record['subject']}\n"
        + (f"Job link:   {record['job_url']}\n" if record.get("job_url") else "")
        + f"Role:       {record['role'] or '(not stated)'}\n"
        f"Letter:     {record['letter_mode']}\n"
        f"CV files:   {', '.join(record['cv_files'])}\n"
        f"Signals:    {', '.join(record['signals']) or '-'}\n\n"
        f"Review the draft here:\n{record['draft_url']}\n\n"
        f"Reply to this email with {cfg['approval']['approve_word']} to send it to {record['sender_email']}, "
        f"or {cfg['approval']['reject_word']} to leave it unsent.\n\n"
        f"--- Draft body ---\n{record['letter']}"
    )
    return notify(gmail, cfg, subject, body)


def notify_review(gmail: Gmail, cfg: dict, email, score) -> None:
    subject = f"[JobAgent] Manual review: {email.subject}"
    body = (
        f"This email looked job-related but was ambiguous (MEDIUM confidence). No draft was created.\n\n"
        f"From:    {email.from_header}\n"
        f"Subject: {email.subject}\n"
        f"Reason:  {score.reason}\n"
        f"Positive: {', '.join(score.positive_signals) or '-'}\n"
        f"Negative: {', '.join(score.negative_signals) or '-'}\n\n"
        f"Open: https://mail.google.com/mail/u/0/#inbox/{email.id}\n"
    )
    notify(gmail, cfg, subject, body)


def notify_link_review(gmail: Gmail, cfg: dict, item: dict) -> None:
    """Link resolver: job-board page had no explicit contact email. No draft was created."""
    subject = f"[JobAgent] Manual review needed: {item.get('role') or 'role unknown'} — {item.get('company') or item['platform']}"
    body = (
        f"The Link Resolver fetched this job posting but found no recruiter/HR email literally present on the page, "
        f"so no draft was created. Please find the contact yourself if you want to apply.\n\n"
        f"Job link:  {item['url']}\n"
        f"Platform:  {item['platform']}\n"
        f"Role:      {item.get('role') or '(not found)'}\n"
        f"Company:   {item.get('company') or '(not found)'}\n"
        f"Reason:    {item.get('reason') or '-'}\n"
        f"Source:    {item.get('source_subject') or '-'}\n"
    )
    notify(gmail, cfg, subject, body)


def notify_linkedin_lookup(gmail: Gmail, cfg: dict, item: dict) -> None:
    """Link resolver: LinkedIn posting. The agent never looks up LinkedIn emails; the owner must."""
    subject = f"[JobAgent] Manual lookup needed (LinkedIn): {item.get('role') or 'role unknown'} — {item.get('company') or 'company unknown'}"
    body = (
        f"This is a LinkedIn posting. The agent only reads the public posting page and never looks up or guesses "
        f"recruiter emails for LinkedIn, so this one needs you to find the contact email manually.\n\n"
        f"Job link:   {item['url']}\n"
        f"Role:       {item.get('role') or '(not found)'}\n"
        f"Company:    {item.get('company') or '(not found)'}\n"
        f"Posted by:  {item.get('poster_name') or '(not shown on posting)'}\n"
        f"Note:       {item.get('reason') or '-'}\n"
        f"Source:     {item.get('source_subject') or '-'}\n"
    )
    notify(gmail, cfg, subject, body)


class ApprovalError(Exception):
    """Raised when an approve/reject action can't be completed (draft missing, already
    resolved, ...). Callers (the email-reply loop, the Mission Control API) decide how to
    surface this — it is never swallowed silently."""


def approve_draft(gmail: Gmail, cfg: dict, state: State, draft_id: str, rec: dict) -> dict:
    """Send the Gmail draft for `draft_id` and mark it sent.

    This is the single place that actually sends an application — both the email-reply
    APPROVE path (`process_approvals` below) and Mission Control API's manual
    `POST /api/missions/{id}/approve` call this exact function, so "nothing sends without
    an explicit APPROVE" holds regardless of which surface triggered it.
    """
    if rec.get("status") != "pending":
        raise ApprovalError(f"draft {draft_id} is already {rec.get('status')}, not pending")
    if not gmail.draft_exists(draft_id):
        state.set_status(draft_id, "missing")
        raise ApprovalError(f"draft {draft_id} no longer exists in Gmail (deleted or already sent)")
    sent = gmail.send_draft(draft_id)
    gmail.add_labels(rec["source_msg_id"], [cfg["labels"]["sent"]])
    state.set_status(draft_id, "sent")
    log.info("APPROVED — sent draft %s to %s", draft_id, rec["sender_email"])
    notify(gmail, cfg, f"[JobAgent] Sent: {rec['role'] or rec['subject']}",
           f"Your application was sent to {rec['sender_email']} (message id {sent.get('id')}).")
    return sent


def reject_draft(gmail: Gmail, cfg: dict, state: State, draft_id: str, rec: dict) -> None:
    """Leave the draft unsent. Same function for the email-reply REJECT path and the
    Mission Control API's manual `POST /api/missions/{id}/reject`."""
    if rec.get("status") != "pending":
        raise ApprovalError(f"draft {draft_id} is already {rec.get('status')}, not pending")
    state.set_status(draft_id, "rejected")
    log.info("REJECTED — draft %s left in Gmail unsent", draft_id)


def _decision(body: str, approve: str, reject: str) -> str | None:
    # Only look at the owner's own text, above any quoted reply.
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(">"):
            continue
        if line.startswith("On ") and line.endswith(":"):
            break
        word = line.split()[0].strip(".,!").upper()
        if word == approve.upper():
            return "approve"
        if word == reject.upper():
            return "reject"
        break  # first non-empty line decides
    return None


def process_approvals(gmail: Gmail, cfg: dict, state: State) -> None:
    approve, reject = cfg["approval"]["approve_word"], cfg["approval"]["reject_word"]
    for draft_id, rec in state.pending().items():
        replies = [
            m for m in gmail.thread_messages(rec["notify_thread_id"])
            if m.sender_email.lower() == cfg["owner_email"].lower() and m.internal_date > rec["notified_at"]
        ]
        if not replies:
            continue
        decision = _decision(replies[-1].body, approve, reject)
        try:
            if decision == "approve":
                approve_draft(gmail, cfg, state, draft_id, rec)
            elif decision == "reject":
                reject_draft(gmail, cfg, state, draft_id, rec)
        except ApprovalError as e:
            log.warning("Email-reply %s for draft %s could not be applied: %s", decision, draft_id, e)
