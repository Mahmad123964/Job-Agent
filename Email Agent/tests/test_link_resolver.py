import base64

from bs4 import BeautifulSoup

from agent.gmail_client import Email
from agent.link_resolver import extract_links, find_contact_email, notification_query, platform_for
from agent.models import ContactEmailResult
from agent.page_fetcher import Page, find_emails, parse_job_posting_ld

CFG = {
    "link_resolver": {
        "notification": {"subject_prefix": "New SQA job:", "sender": ""},
        "gmail_query": "in:inbox",
        "max_links_per_email": 10,
        "ignore_domains": ["google.com", "mail.google.com"],
        "platforms": {
            "linkedin": ["linkedin.com", "lnkd.in"],
            "job_boards": {"indeed": ["indeed.com"], "himalayas": ["himalayas.app"]},
        },
        "labels": {"processed": "JobAgent/LinkResolved"},
    }
}


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def _email(text: str, html: str = "") -> Email:
    parts = [{"mimeType": "text/plain", "body": {"data": _b64(text)}}]
    if html:
        parts.append({"mimeType": "text/html", "body": {"data": _b64(html)}})
    return Email({"id": "m1", "threadId": "t1", "payload": {
        "headers": [{"name": "From", "value": "haalim406@gmail.com"}, {"name": "Subject", "value": "New SQA job: Senior QA Automation Engineer"}],
        "parts": parts}})


def test_platform_classification():
    assert platform_for("https://www.linkedin.com/jobs/view/123", CFG) == "linkedin"
    assert platform_for("https://lnkd.in/abc", CFG) == "linkedin"
    assert platform_for("https://pk.indeed.com/viewjob?jk=1", CFG) == "indeed"
    assert platform_for("https://himalayas.app/companies/x/jobs/y", CFG) == "himalayas"
    assert platform_for("https://jobs.example.com/123", CFG) == "other"
    assert platform_for("https://notlinkedin.com/x", CFG) == "other"


def test_extract_links_from_html_and_text():
    e = _email(
        "Jobs:\nhttps://pk.indeed.com/viewjob?jk=1.\nOpen in Gmail: https://mail.google.com/mail/u/0/#inbox/x",
        '<p><a href="https://www.linkedin.com/jobs/view/999/">Apply</a> and <a href="mailto:a@b.com">mail</a></p>',
    )
    assert extract_links(e, CFG) == ["https://www.linkedin.com/jobs/view/999/", "https://pk.indeed.com/viewjob?jk=1"]


def test_notification_query_requires_prefix_and_excludes_processed():
    q = notification_query(CFG)
    assert 'subject:"New SQA job:"' in q and '-label:"JobAgent/LinkResolved"' in q and "from:" not in q


FINDER_BODY = """New SQA job (87/100): Senior QA Automation Engineer
Company: Acme Robotics
Location: Remote
Seniority: Senior · Worldwide
Eligible region: Worldwide
Skills: Selenium, Playwright, Python, API testing
Source: WeWorkRemotely
https://weworkremotely.com/remote-jobs/acme-senior-qa-automation-engineer"""


def test_finder_body_parsed_and_trailing_url_extracted():
    from agent.link_resolver import parse_finder_body
    f = parse_finder_body(FINDER_BODY)
    assert f == {"title": "Senior QA Automation Engineer", "company": "Acme Robotics", "location": "Remote", "score": 87,
                 "url": "https://weworkremotely.com/remote-jobs/acme-senior-qa-automation-engineer"}
    assert extract_links(_email(FINDER_BODY), CFG) == [f["url"]]
    # Gmail also carries an HTML part with the same link as an <a href>; must not duplicate
    html = "<p>" + FINDER_BODY.replace("\n", "<br>") + '<br><a href="' + f["url"] + '">' + f["url"] + "</a></p>"
    assert extract_links(_email(FINDER_BODY, html), CFG) == [f["url"]]


def test_subject_match_is_case_insensitive_substring():
    from agent.main import is_finder_notification
    assert is_finder_notification("New SQA job: Senior QA Engineer", CFG)
    assert is_finder_notification("Fwd: new sqa JOB: QA Lead", CFG)
    assert not is_finder_notification("[JobAgent] Approval needed: x", CFG)
    assert not is_finder_notification("New job: QA", CFG)


def test_find_emails_filters_noise_and_dedupes():
    html = '<a href="mailto:Jobs@Acme.com">apply</a> logo@2x.png privacy@acme.com'
    text = "Send your CV to jobs@acme.com or hr@acme.com"
    assert find_emails(html, text, ["privacy", "noreply"]) == ["jobs@acme.com", "hr@acme.com"]


def test_parse_job_posting_ld():
    soup = BeautifulSoup('<script type="application/ld+json">{"@type":"JobPosting","title":"QA Lead",'
                         '"hiringOrganization":{"@type":"Organization","name":"Acme"},"description":"<p>Test<br>things</p>"}</script>',
                         "html.parser")
    assert parse_job_posting_ld(soup) == {"title": "QA Lead", "company": "Acme", "description": "Test\nthings"}


class _FakeGemini:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def structured(self, prompt, schema, temperature=0.2):
        self.calls += 1
        return self.answer


def test_contact_email_never_invented():
    page = Page(url="u", text="body", emails=[])
    g = _FakeGemini(ContactEmailResult(email="made.up@acme.com"))
    assert find_contact_email(g, page, "jd")[0] is None
    assert g.calls == 0  # no candidates -> Gemini is not even asked

    page.emails = ["hr@acme.com"]
    g = _FakeGemini(ContactEmailResult(email="recruiter@acme.com", reason="looks right"))
    assert find_contact_email(g, page, "jd")[0] is None  # answer not on page -> discarded

    g = _FakeGemini(ContactEmailResult(email="HR@acme.com", reason="apply address"))
    assert find_contact_email(g, page, "jd") == ("hr@acme.com", "apply address")

    g = _FakeGemini(ContactEmailResult(email=None, reason="only a privacy address"))
    assert find_contact_email(g, page, "jd") == (None, "only a privacy address")


# ---- end-to-end branch behaviour with fakes (no network, no Gmail, no Gemini) ----
import agent.main as agent_main
from agent import link_resolver
from agent.models import CoverLetterResult, CVProfile, JDResult, JobPageResult
from agent.state import State

FULL_CFG = {
    "owner_email": "owner@example.com",
    "cv_dir": None,
    "approval": {"subject_prefix": "[JobAgent] Approval needed", "approve_word": "APPROVE", "reject_word": "REJECT"},
    "labels": {"processed": "JobAgent/Processed"},
    **CFG,
}
FULL_CFG["link_resolver"]["draft_subject"] = "Application for {role} — {company}"


class _SchemaGemini:
    """Answers by schema type; refuses anything unexpected."""

    def __init__(self, contact=None):
        self.contact = contact

    def structured(self, prompt, schema, temperature=0.2):
        if schema is JobPageResult:
            return JobPageResult(is_job_posting=True, role_title="QA Lead", company="Acme", description="Test all the things " * 20, poster_name="Sam Poster")
        if schema is ContactEmailResult:
            return ContactEmailResult(email=self.contact, reason="apply address")
        if schema is JDResult:
            return JDResult(usable=True, role_title="QA Lead", company="Acme", summary="s", requirements=["Selenium"])
        if schema is CoverLetterResult:
            return CoverLetterResult(confident=True, letter="Dear Sam Poster,\n" + "x" * 200)
        raise AssertionError(schema)


class _FakeGmail:
    def __init__(self):
        self.drafts, self.sent, self.labels = [], [], []

    def create_draft(self, to, subject, body, attachments):
        self.drafts.append({"to": to, "subject": subject, "body": body, "attachments": attachments})
        return {"draft_id": "d1", "message_id": "m9", "url": "https://mail.google.com/#drafts/m9"}

    def send_mail(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})
        return {"threadId": "nt1"}

    def add_labels(self, msg_id, names):
        self.labels.append((msg_id, names))


class _FakeFetcher:
    def __init__(self, page):
        self.page = page

    def fetch(self, url):
        self.page.url = url
        return self.page


def _run(monkeypatch, tmp_path, url, page, contact):
    monkeypatch.setattr(agent_main, "load_profile", lambda g, d: (CVProfile(name="Jane", current_role="SQA"), {"pdf": tmp_path / "cv.pdf"}))
    gmail, state = _FakeGmail(), State(tmp_path / "state.json")
    outcome = link_resolver.resolve_link(gmail, _SchemaGemini(contact), _FakeFetcher(page), FULL_CFG, state, url, _email("x"))
    return outcome, gmail, state


def test_job_board_with_email_hands_off_to_agent2(monkeypatch, tmp_path):
    page = Page(url="", text="jd " * 200, emails=["hr@acme.com"])
    outcome, gmail, state = _run(monkeypatch, tmp_path, "https://pk.indeed.com/viewjob?jk=1", page, "hr@acme.com")
    assert outcome == "drafted"
    assert gmail.drafts[0]["to"] == "hr@acme.com"
    assert gmail.drafts[0]["subject"] == "Application for QA Lead — Acme"
    assert gmail.drafts[0]["body"].startswith("Dear Sam Poster")
    # Owner gets the same approval email as Agent 2's HIGH flow; nothing was sent to the recruiter.
    assert [m["to"] for m in gmail.sent] == ["owner@example.com"]
    assert gmail.sent[0]["subject"].startswith("[JobAgent] Approval needed") and "Job link:" in gmail.sent[0]["body"]
    rec = state.pending()["d1"]
    assert rec["sender_email"] == "hr@acme.com" and rec["job_url"].startswith("https://pk.indeed.com")
    assert state.link_status("https://pk.indeed.com/viewjob?jk=1") == "drafted"


def test_job_board_without_email_asks_for_manual_review(monkeypatch, tmp_path):
    page = Page(url="", text="jd " * 200, emails=[])
    outcome, gmail, state = _run(monkeypatch, tmp_path, "https://himalayas.app/x/jobs/y", page, "fake@acme.com")
    assert outcome == "manual_review" and gmail.drafts == []
    assert gmail.sent[0]["subject"].startswith("[JobAgent] Manual review needed: QA Lead — Acme")
    assert "no email address appears" in gmail.sent[0]["body"]
    assert state.pending() == {}


def test_linkedin_never_looks_up_email(monkeypatch, tmp_path):
    page = Page(url="", text="jd " * 200, emails=["hr@acme.com"])  # even if one is on the page
    outcome, gmail, state = _run(monkeypatch, tmp_path, "https://www.linkedin.com/jobs/view/1/", page, "hr@acme.com")
    assert outcome == "linkedin_manual_lookup" and gmail.drafts == []
    assert gmail.sent[0]["subject"].startswith("[JobAgent] Manual lookup needed (LinkedIn): QA Lead — Acme")
    assert "Posted by:  Sam Poster" in gmail.sent[0]["body"] and "hr@acme.com" not in gmail.sent[0]["body"]


def test_blocked_page_goes_to_manual_review(monkeypatch, tmp_path):
    page = Page(url="", blocked_reason="disallowed by robots.txt")
    outcome, gmail, _ = _run(monkeypatch, tmp_path, "https://jobs.example.com/1", page, None)
    assert outcome == "manual_review"
    assert "robots.txt" in gmail.sent[0]["body"]


def test_indeed_viewjob_falls_back_to_manual_review_like_linkedin(monkeypatch, tmp_path):
    """End-to-end with the real PageFetcher (not the fake): Indeed's actual robots.txt shape
    (a blanket Allow: / before the more specific Disallow: /viewjob, in the same block) must
    correctly block the fetch — proving robots_can_fetch's longest-match fix, not just a
    hand-built blocked Page — and resolve_link must then fall back to manual_review exactly
    as it already does for LinkedIn: no page read, no email search, owner notified."""
    from agent.page_fetcher import PageFetcher

    indeed_robots_txt = "User-agent: *\nAllow: /\nDisallow: /m/viewjob\nDisallow: /viewjob\n"

    class _FakeResponse:
        def __init__(self, text, status_code=200):
            self.text, self.status_code = text, status_code

    class _FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, timeout=None, allow_redirects=None):
            if url.endswith("/robots.txt"):
                return _FakeResponse(indeed_robots_txt)
            raise AssertionError(f"page itself must never be fetched once robots.txt disallows it: {url}")

    fetcher = PageFetcher({"respect_robots_txt": True})
    fetcher.session = _FakeSession()
    monkeypatch.setattr(agent_main, "load_profile", lambda g, d: (CVProfile(name="Jane", current_role="SQA"), {}))
    gmail, state = _FakeGmail(), State(tmp_path / "state.json")

    outcome = link_resolver.resolve_link(gmail, _SchemaGemini(None), fetcher, FULL_CFG, state,
                                         "https://pk.indeed.com/viewjob?jk=999", _email("x"))

    assert outcome == "manual_review"
    assert gmail.drafts == []  # never attempted a draft
    assert "robots.txt" in gmail.sent[0]["body"]
    assert state.link_status("https://pk.indeed.com/viewjob?jk=999") == "manual_review"


def test_notification_is_labelled_and_links_deduped(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_main, "load_profile", lambda g, d: (CVProfile(name="Jane", current_role="SQA"), {}))
    gmail, state = _FakeGmail(), State(tmp_path / "state.json")
    state.mark_link("https://pk.indeed.com/viewjob?jk=1", "drafted")
    email = _email("https://pk.indeed.com/viewjob?jk=1\nhttps://himalayas.app/x/jobs/y")
    page = Page(url="", text="jd " * 200, emails=[])
    link_resolver.handle_notification(gmail, _SchemaGemini(None), _FakeFetcher(page), FULL_CFG, state, email)
    assert len(gmail.sent) == 1  # only the un-seen himalayas link was processed
    assert gmail.labels == [("m1", ["JobAgent/LinkResolved"])]
