"""Link Resolver Agent (Agent 3).

Bridges the Job Finder Agent (1) and the Email Agent (2): reads Agent 1's job
notification emails, opens each job link, and either hands the job to Agent 2's
existing draft + approval flow (when a recruiter email is literally present on
the page) or asks the owner to look the contact up manually.

Usage:
    python -m agent.link_resolver once             # process new notifications, then exit
    python -m agent.link_resolver watch            # loop forever (link_resolver.poll_interval_seconds)
    python -m agent.link_resolver resolve <url>    # dry run one link: fetch + classify + extract, no side effects
    python -m agent.link_resolver links <msg_id>   # show which links would be taken from one notification

Hard rules (never relaxed):
- A recruiter email is used only if it appears verbatim in the fetched page. Gemini
  may only *choose* among addresses the regex already found; its answer is checked
  against that list again before use. Nothing is ever inferred or guessed.
- LinkedIn: only the single public posting page is read; no email lookup at all.
- No browser extensions or third-party scraping/lookup tools; robots.txt and a
  per-domain rate limit are respected.
- Every draft goes through Agent 2's APPROVE/REJECT flow. Nothing sends itself.
"""
import logging
import re
import sys
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .approval import notify_link_review, notify_linkedin_lookup, process_approvals
from .config import load_config, load_prompt
from .gemini_client import Gemini, GeminiUnavailable
from .gmail_client import Email, Gmail
from .main import extract_jd_text, is_finder_notification, prepare_application, register_pending
from .models import ContactEmailResult, JDResult, JobPageResult
from .page_fetcher import Page, PageFetcher
from .state import State

log = logging.getLogger("agent.link_resolver")

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


# ---- link extraction ------------------------------------------------------
def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _domain_matches(host: str, domains: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def platform_for(url: str, cfg: dict) -> str:
    """'linkedin', a job-board key from config, or 'other'."""
    host = _host(url)
    platforms = cfg["link_resolver"]["platforms"]
    if _domain_matches(host, platforms.get("linkedin", [])):
        return "linkedin"
    for name, domains in platforms.get("job_boards", {}).items():
        if _domain_matches(host, domains):
            return name
    return "other"


def parse_finder_body(body: str) -> dict:
    """Fields from the Job Finder Agent's fixed body layout:

        New SQA job (87/100): Senior QA Engineer
        Company: Acme
        Location: Remote
        ...
        https://example.com/job          <- bare URL on its own line at the end

    Returns {title, company, location, score, url} with None for anything missing."""
    out: dict = {"title": None, "company": None, "location": None, "score": None, "url": None}
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return out
    m = re.match(r"(?i)new sqa job\s*\((\d+)\s*/\s*100\)\s*:\s*(.+)", lines[0])
    if m:
        out["score"], out["title"] = int(m.group(1)), m.group(2).strip()
    for ln in lines:
        key, _, val = ln.partition(":")
        if key.strip().lower() == "company":
            out["company"] = val.strip() or None
        elif key.strip().lower() == "location":
            out["location"] = val.strip() or None
    if re.fullmatch(r"https?://\S+", lines[-1]):
        out["url"] = lines[-1].rstrip(".,;:!?")
    return out


def extract_links(email: Email, cfg: dict) -> list[str]:
    """Job links from a notification email, de-duplicated.

    The finder's bare URL on the last body line comes first; any other links in the
    text/HTML follow in order of appearance."""
    lr = cfg["link_resolver"]
    found: list[str] = []
    trailing = parse_finder_body(email.body).get("url")
    if trailing:
        found.append(trailing)
    if email.html:
        for a in BeautifulSoup(email.html, "html.parser").find_all("a", href=True):
            found.append(a["href"].strip())
    found += URL_RE.findall(email.body)

    links: list[str] = []
    for raw in found:
        url = raw.rstrip(".,;:!?").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        host = _host(url)
        if not host or _domain_matches(host, lr.get("ignore_domains", [])):
            continue
        if url not in links:
            links.append(url)
    return links[: lr.get("max_links_per_email", 10)]


# ---- page understanding -----------------------------------------------------
def analyse_page(gemini: Gemini, page: Page) -> JobPageResult:
    """Title / company / description / poster name from the fetched page only."""
    if page.ld_description and len(page.ld_description) > 200:
        # Structured data is more reliable than the rendered text; still let Gemini spot a
        # "posted by" name in the visible text, since JSON-LD never carries it.
        base = JobPageResult(is_job_posting=True, role_title=page.ld_title, company=page.ld_company,
                             description=page.ld_description)
        try:
            seen = gemini.structured(load_prompt("extract_job_page").format(url=page.url, page_text=page.text[:15000]), JobPageResult)
            base.poster_name = seen.poster_name
            base.role_title = base.role_title or seen.role_title
            base.company = base.company or seen.company
        except GeminiUnavailable as e:
            log.warning("Gemini unavailable while reading %s; using JSON-LD only: %s", page.url, e)
        return base
    return gemini.structured(load_prompt("extract_job_page").format(url=page.url, page_text=page.text[:15000]), JobPageResult)


def find_contact_email(gemini: Gemini, page: Page, description: str) -> tuple[str | None, str]:
    """Pick the recruiter/HR/application address from the emails literally on the page.

    Returns (email or None, reason). Gemini is only asked to choose among `page.emails`;
    an answer outside that list is discarded. No candidates -> no Gemini call at all.
    """
    if not page.emails:
        return None, "no email address appears anywhere on the page"
    prompt = load_prompt("extract_contact_email").format(
        url=page.url, candidates="\n".join(f"- {e}" for e in page.emails), description=description[:12000]
    )
    result = gemini.structured(prompt, ContactEmailResult)
    chosen = (result.email or "").strip().lower()
    if not chosen:
        return None, result.reason or "addresses on the page are not a recruiter/HR/application contact"
    if chosen not in page.emails:
        log.warning("Discarding %s: not among the addresses literally on %s", chosen, page.url)
        return None, "model returned an address that is not on the page (discarded)"
    return chosen, result.reason


# ---- per-link handling ------------------------------------------------------
def resolve_link(gmail: Gmail, gemini: Gemini, fetcher: PageFetcher, cfg: dict, state: State,
                 url: str, source: Email | None) -> str:
    """Process one job link end-to-end. Returns the outcome recorded in state."""
    lr = cfg["link_resolver"]
    platform = platform_for(url, cfg)
    finder = parse_finder_body(source.body) if source else {}
    item = {"url": url, "platform": platform, "source_subject": source.subject if source else None,
            "source_msg_id": source.id if source else None,
            "role": finder.get("title"), "company": finder.get("company")}  # page data overrides below

    page = fetcher.fetch(url)
    if page.ok:
        try:
            info = analyse_page(gemini, page)
        except GeminiUnavailable as e:
            log.warning("Gemini unavailable for %s; leaving it for the next run: %s", url, e)
            return "retry"
        item.update(role=info.role_title or item["role"], company=info.company or item["company"], poster_name=info.poster_name)
        if not info.is_job_posting:
            item["reason"] = "page does not look like a job posting"
    else:
        info = JobPageResult(is_job_posting=False)
        item["reason"] = f"could not read the page ({page.blocked_reason})"

    # B) LinkedIn: never look for an email. Report what the single posting page showed.
    if platform == "linkedin":
        item.setdefault("reason", "LinkedIn postings always require a manual email lookup")
        if gmail:
            notify_linkedin_lookup(gmail, cfg, item)
        state.mark_link(url, "linkedin_manual_lookup", role=item.get("role"), company=item.get("company"))
        log.info("LinkedIn -> manual lookup: %s (%s @ %s)", url, item.get("role"), item.get("company"))
        return "linkedin_manual_lookup"

    # A) Job boards / other: use an email only if it is literally on the page.
    email, reason = (None, item.get("reason"))
    if page.ok and info.is_job_posting:
        try:
            email, reason = find_contact_email(gemini, page, info.description or page.text)
        except GeminiUnavailable as e:
            log.warning("Gemini unavailable choosing contact for %s; leaving it for the next run: %s", url, e)
            return "retry"

    if not email:
        item["reason"] = reason
        if gmail:
            notify_link_review(gmail, cfg, item)
        state.mark_link(url, "manual_review", role=item.get("role"), company=item.get("company"), reason=reason)
        log.info("No contact email -> manual review: %s (%s)", url, reason)
        return "manual_review"

    # Hand off to Agent 2's existing JD -> letter -> draft -> approval flow.
    log.info("Recruiter email found on page: %s -> %s", url, email)
    if not gmail:
        return "handoff (dry run)"
    sender = f"{info.company or platform} via {platform} <{email}>"
    try:
        jd = extract_jd_text(gemini, sender, info.role_title or "", info.description or page.text)
    except GeminiUnavailable as e:
        log.warning("JD extraction unavailable for %s; using default letter: %s", url, e)
        jd = JDResult(usable=False, role_title=info.role_title, company=info.company)
    jd.role_title = jd.role_title or info.role_title
    jd.company = jd.company or info.company
    letter, mode, attachments = prepare_application(gemini, cfg, jd, info.poster_name or "", info.role_title)
    subject = lr["draft_subject"].format(role=jd.role_title or "the advertised role", company=jd.company or platform)
    draft = gmail.create_draft(email, subject, letter, attachments)
    record = {
        "status": "pending",
        "source_msg_id": source.id if source else "",
        "sender": sender,
        "sender_email": email,
        "subject": subject,
        "job_url": url,
        "platform": platform,
        "role": jd.role_title,
        "letter_mode": mode,
        "letter": letter,
        "cv_files": [p.name for p in attachments],
        "signals": [f"recruiter email found on {platform} posting"],
    }
    register_pending(gmail, cfg, state, draft, record)
    state.mark_link(url, "drafted", draft_id=draft["draft_id"], email=email, role=jd.role_title, company=jd.company)
    log.info("Draft created (%s letter) for %s -> %s", mode, email, draft["url"])
    return "drafted"


def handle_notification(gmail: Gmail, gemini: Gemini, fetcher: PageFetcher, cfg: dict, state: State, email: Email) -> None:
    label = cfg["link_resolver"]["labels"]["processed"]
    links = extract_links(email, cfg)
    if not links:
        log.info("No job links in notification %s (%s)", email.id, email.subject)
        gmail.add_labels(email.id, [label])
        return
    log.info("Notification %s (%s): %d link(s)", email.id, email.subject, len(links))
    pending_retry = False
    for url in links:
        prior = state.link_status(url)
        if prior:
            log.info("Already resolved (%s), skipping: %s", prior, url)
            continue
        try:
            if resolve_link(gmail, gemini, fetcher, cfg, state, url, email) == "retry":
                pending_retry = True
        except Exception:  # noqa: BLE001 - one bad link must not stop the others
            log.exception("Failed resolving %s", url)
            pending_retry = True
    if not pending_retry:
        gmail.add_labels(email.id, [label])


def notification_query(cfg: dict) -> str:
    lr = cfg["link_resolver"]
    n = lr["notification"]
    q = f'{lr["gmail_query"]} subject:"{n["subject_prefix"]}"'
    if n.get("sender"):
        q += f' from:{n["sender"]}'
    return f'{q} -label:"{lr["labels"]["processed"]}"'


def run_once(gmail: Gmail, gemini: Gemini, fetcher: PageFetcher, cfg: dict, state: State) -> None:
    process_approvals(gmail, cfg, state)  # same approval loop as Agent 2
    for msg_id in gmail.search(notification_query(cfg), cfg["link_resolver"]["max_messages_per_run"]):
        try:
            email = gmail.get(msg_id)
            if not is_finder_notification(email.subject, cfg):
                continue  # Gmail subject: search is fuzzy; require the prefix ourselves
            handle_notification(gmail, gemini, fetcher, cfg, state, email)
        except Exception:  # noqa: BLE001
            log.exception("Failed processing notification %s", msg_id)


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cmd = argv[0] if argv else "once"
    cfg = load_config()
    if "link_resolver" not in cfg:
        raise SystemExit("config.yaml has no link_resolver section")
    gemini = Gemini(cfg["gemini_api_key"], cfg["gemini_model"],
                    max_retries=cfg.get("gemini_max_retries", 3),
                    initial_backoff_seconds=cfg.get("gemini_initial_backoff_seconds", 3))
    fetcher = PageFetcher(cfg["link_resolver"]["fetch"])

    if cmd == "resolve":
        # Dry run: no Gmail, no notifications, no drafts, no state writes.
        url = argv[1]
        page = fetcher.fetch(url)
        print(f"platform: {platform_for(url, cfg)}  status: {page.status}  via: {page.fetched_with}  "
              f"blocked: {page.blocked_reason}  text: {len(page.text)} chars  emails on page: {page.emails}")
        if not page.ok:
            return
        try:
            info = analyse_page(gemini, page)
            print(info.model_dump_json(indent=2, exclude={"description"}))
            print(f"description: {len(info.description)} chars")
            if platform_for(url, cfg) != "linkedin" and info.is_job_posting:
                print("contact:", find_contact_email(gemini, page, info.description or page.text))
        except GeminiUnavailable as e:
            print(f"Gemini unavailable, could not analyse the page: {e}")
        return

    gmail = Gmail(cfg["gmail_credentials_file"], cfg["gmail_token_file"])
    state = State(cfg["state_file"])

    if cmd == "links":
        email = gmail.get(argv[1])
        print(f"{email.from_header} | {email.subject}")
        for url in extract_links(email, cfg):
            print(f"  [{platform_for(url, cfg)}] {url}  (state: {state.link_status(url)})")
    elif cmd == "once":
        run_once(gmail, gemini, fetcher, cfg, state)
    elif cmd == "watch":
        interval = cfg["link_resolver"].get("poll_interval_seconds", cfg["poll_interval_seconds"])
        while True:
            try:
                run_once(gmail, gemini, fetcher, cfg, state)
            except Exception:  # noqa: BLE001 - a Gmail/network outage must not kill the watcher
                log.exception("Run failed; retrying after %ss", interval)
            time.sleep(interval)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
