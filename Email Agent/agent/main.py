"""Job Application Email Agent.

Usage:
    python -m agent.main once            # process new mail + approvals, then exit
    python -m agent.main watch           # loop forever (poll_interval_seconds)
    python -m agent.main score <msg_id>  # dry-run: score one message, no side effects
    python -m agent.main profile         # show what the agent reads from the active CV
"""
import logging
import sys
import time

from .approval import notify_owner, notify_review, process_approvals
from .config import load_config, load_prompt
from .cover_letter import build_letter
from .cv_reader import load_profile
from .gemini_client import Gemini, GeminiUnavailable
from .gmail_client import Email, Gmail
from .models import JDResult, ScoreResult
from .state import State

log = logging.getLogger("agent")


def score_email(gemini: Gemini, email: Email) -> ScoreResult:
    prompt = load_prompt("score").format(
        keywords=load_prompt("keywords"), sender=email.from_header, subject=email.subject, body=email.body[:12000]
    )
    return gemini.structured(prompt, ScoreResult)


def extract_jd_text(gemini: Gemini, sender: str, subject: str, body: str) -> JDResult:
    prompt = load_prompt("extract_jd").format(sender=sender, subject=subject, body=body[:12000])
    return gemini.structured(prompt, JDResult)


def extract_jd(gemini: Gemini, email: Email) -> JDResult:
    return extract_jd_text(gemini, email.from_header, email.subject, email.body)


def prepare_application(gemini: Gemini, cfg: dict, jd: JDResult, recruiter_name: str, scored_role: str | None):
    """Letter + CV attachments for a JD. Returns (letter, mode, attachments).

    Always reads the CV fresh so a replaced file is picked up immediately."""
    profile, cv_files = load_profile(gemini, cfg["cv_dir"])
    letter, mode = build_letter(gemini, jd, profile, recruiter_name, scored_role)
    attachments = [cv_files[k] for k in ("pdf", "docx") if k in cv_files]
    return letter, mode, attachments


def register_pending(gmail: Gmail, cfg: dict, state: State, draft: dict, record: dict) -> None:
    """Notify the owner about a draft and track it until they APPROVE/REJECT."""
    record["draft_url"] = draft["url"]
    record["notified_at"] = int(time.time() * 1000)
    record["notify_thread_id"] = notify_owner(gmail, cfg, record)
    state.add_pending(draft["draft_id"], record)


def is_finder_notification(subject: str, cfg: dict) -> bool:
    """True if `subject` is one of the Job Finder Agent's notifications.

    Case-insensitive substring match on link_resolver.notification.subject_prefix, since the
    rest of the subject ("New SQA job: <title>") varies per job."""
    prefix = (cfg.get("link_resolver", {}).get("notification", {}).get("subject_prefix") or "").strip().lower()
    return bool(prefix) and prefix in (subject or "").lower()


def _skip(email: Email, cfg: dict) -> str | None:
    if email.sender_email.lower() == cfg["owner_email"].lower():
        return "sent by owner/agent"
    if email.subject.startswith("[JobAgent]"):
        return "agent notification"
    if is_finder_notification(email.subject, cfg):
        return "job finder notification (handled by agent.link_resolver)"
    if not email.sender_email:
        return "no sender address"
    if not email.body.strip():
        return "empty body"
    return None


def handle_email(gmail: Gmail, gemini: Gemini, cfg: dict, state: State, email: Email) -> None:
    labels = cfg["labels"]
    reason = _skip(email, cfg)
    if reason:
        log.debug("skip %s (%s)", email.id, reason)
        gmail.add_labels(email.id, [labels["processed"]])
        return

    try:
        score = score_email(gemini, email)
    except GeminiUnavailable as e:
        # Leave the email unlabelled so it is retried on the next run.
        log.warning("Scoring unavailable for %s (%s); skipping this run: %s", email.id, email.subject, e)
        return
    log.info("[%s] %s | %s — %s", score.confidence, email.sender_email, email.subject, score.reason)

    if score.confidence == "LOW":
        gmail.add_labels(email.id, [labels["processed"], labels["low"]])
        return

    if score.confidence == "MEDIUM":
        notify_review(gmail, cfg, email, score)
        gmail.add_labels(email.id, [labels["processed"], labels["review"]])
        return

    # HIGH
    try:
        jd = extract_jd(gemini, email)
    except GeminiUnavailable as e:
        log.warning("JD extraction unavailable for %s; using default letter: %s", email.id, e)
        jd = JDResult(usable=False, role_title=score.role_title)
    letter, mode, attachments = prepare_application(gemini, cfg, jd, email.sender_name, score.role_title)
    draft = gmail.create_reply_draft(email, letter, attachments)

    record = {
        "status": "pending",
        "source_msg_id": email.id,
        "sender": email.from_header,
        "sender_email": email.sender_email,
        "subject": email.subject,
        "role": jd.role_title or score.role_title,
        "letter_mode": mode,
        "letter": letter,
        "cv_files": [p.name for p in attachments],
        "signals": score.positive_signals,
    }
    register_pending(gmail, cfg, state, draft, record)
    gmail.add_labels(email.id, [labels["processed"], labels["high"]])
    log.info("Draft created (%s letter) -> %s", mode, draft["url"])


def run_once(gmail: Gmail, gemini: Gemini, cfg: dict, state: State) -> None:
    process_approvals(gmail, cfg, state)
    query = f'{cfg["gmail_query"]} -label:"{cfg["labels"]["processed"]}"'
    for msg_id in gmail.search(query, cfg["max_messages_per_run"]):
        try:
            handle_email(gmail, gemini, cfg, state, gmail.get(msg_id))
        except Exception:  # noqa: BLE001 - one bad email must not stop the run
            log.exception("Failed processing message %s", msg_id)


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cmd = argv[0] if argv else "once"
    cfg = load_config()
    gemini = Gemini(cfg["gemini_api_key"], cfg["gemini_model"],
                    max_retries=cfg.get("gemini_max_retries", 3),
                    initial_backoff_seconds=cfg.get("gemini_initial_backoff_seconds", 3))

    if cmd == "profile":
        profile, files = load_profile(gemini, cfg["cv_dir"])
        print("Active CV files:", {k: v.name for k, v in files.items()})
        print(profile.model_dump_json(indent=2))
        return

    gmail = Gmail(cfg["gmail_credentials_file"], cfg["gmail_token_file"])
    state = State(cfg["state_file"])

    if cmd == "score":
        email = gmail.get(argv[1])
        print(score_email(gemini, email).model_dump_json(indent=2))
        print(extract_jd(gemini, email).model_dump_json(indent=2))
    elif cmd == "once":
        run_once(gmail, gemini, cfg, state)
    elif cmd == "watch":
        while True:
            try:
                run_once(gmail, gemini, cfg, state)
            except Exception:  # noqa: BLE001 - a Gmail/network outage must not kill the watcher
                log.exception("Run failed; retrying after %ss", cfg["poll_interval_seconds"])
            time.sleep(cfg["poll_interval_seconds"])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
