"""Spec guarantees that must never regress: explicit APPROVE gate, literal recruiter
address, graceful Gemini degradation, dynamic CV, resilient state + watch loop."""
import base64
import json
import os

import pytest

from agent import approval as approval_module
from agent import main as agent_main
from agent.approval import process_approvals
from agent.cv_reader import active_cv_files
from agent.gemini_client import GeminiUnavailable
from agent.gmail_client import Email
from agent.models import CoverLetterResult, CVProfile, JDResult, ScoreResult
from agent.state import State

OWNER = "owner@example.com"
CFG = {
    "owner_email": OWNER,
    "labels": {"processed": "JobAgent/Processed", "high": "JobAgent/High", "review": "JobAgent/Review",
               "low": "JobAgent/Low", "sent": "JobAgent/Sent"},
    "approval": {"subject_prefix": "[JobAgent] Approval needed", "approve_word": "APPROVE", "reject_word": "REJECT"},
    "notifications": {"channels": ["email"]},
    "link_resolver": {"notification": {"subject_prefix": "New SQA job:"}},
    "gmail_query": "in:inbox", "max_messages_per_run": 25, "poll_interval_seconds": 0, "cv_dir": "cv",
}


def _b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode()


def _email(id_, frm, subject, body, internal_date=1_000, thread="t1"):
    return Email({"id": id_, "threadId": thread, "internalDate": internal_date, "payload": {
        "headers": [{"name": "From", "value": frm}, {"name": "Subject", "value": subject}, {"name": "Message-ID", "value": f"<{id_}@x>"}],
        "parts": [{"mimeType": "text/plain", "body": {"data": _b64(body)}}]}})


class _Gmail:
    """Records everything; only `thread` is fed back to the approval loop."""

    def __init__(self, thread=None, draft_exists=True):
        self.thread = thread or []
        self._draft_exists = draft_exists
        self.reply_drafts, self.drafts, self.sent_drafts, self.mail, self.labels = [], [], [], [], []

    def create_reply_draft(self, original, body, attachments):
        self.reply_drafts.append({"to": original.sender_email, "body": body, "attachments": attachments})
        return {"draft_id": "d1", "message_id": "m9", "url": "https://mail.google.com/#drafts/m9"}

    def create_draft(self, to, subject, body, attachments):
        self.drafts.append({"to": to, "subject": subject})
        return {"draft_id": "d2", "message_id": "m10", "url": "u"}

    def send_mail(self, to, subject, body):
        self.mail.append({"to": to, "subject": subject, "body": body})
        return {"threadId": "nt1"}

    def thread_messages(self, thread_id):
        return self.thread

    def draft_exists(self, draft_id):
        return self._draft_exists

    def send_draft(self, draft_id):
        self.sent_drafts.append(draft_id)
        return {"id": "sent-1"}

    def add_labels(self, msg_id, names):
        self.labels.append((msg_id, names))


class _Gemini:
    def __init__(self, confidence="HIGH", fail=()):
        self.confidence, self.fail = confidence, set(fail)

    def structured(self, prompt, schema, temperature=0.2):
        if schema in self.fail:
            raise GeminiUnavailable("quota")
        if schema is ScoreResult:
            return ScoreResult(confidence=self.confidence, role_title="QA Engineer", positive_signals=["jd"], reason="r")
        if schema is JDResult:
            return JDResult(usable=True, role_title="QA Engineer", company="Acme", summary="s", requirements=["Selenium"])
        if schema is CoverLetterResult:
            return CoverLetterResult(confident=True, letter="Dear Recruiter,\n" + "tailored " * 30)
        raise AssertionError(schema)


def _pending_state(path, notified_at=1_000):
    state = State(path)
    state.add_pending("d1", {"status": "pending", "source_msg_id": "src", "sender_email": "hr@acme.com",
                             "role": "QA", "subject": "s", "notify_thread_id": "nt1", "notified_at": notified_at})
    return state


@pytest.fixture
def cv(monkeypatch, tmp_path):
    profile = CVProfile(name="Jane Doe", current_role="SQA Engineer", core_skill_areas=["testing"], key_skills=["Selenium"])
    monkeypatch.setattr(agent_main, "load_profile", lambda g, d: (profile, {"pdf": tmp_path / "cv.pdf"}))
    return profile


# ---- 2b. explicit APPROVE gate -------------------------------------------------------------
def test_nothing_sent_without_owner_approve_reply(tmp_path):
    # Thread holds: the approval notification itself (sent from the owner's own account), a
    # stranger replying APPROVE, and an owner APPROVE that predates the notification. None may send.
    thread = [
        _email("n1", f"Me <{OWNER}>", "[JobAgent] Approval needed", "A new job email was matched...", internal_date=1_001),
        _email("x1", "Stranger <evil@example.com>", "Re: approval", "APPROVE", internal_date=2_000),
        _email("o0", f"Me <{OWNER}>", "Re: approval", "APPROVE", internal_date=500),
    ]
    gmail, state = _Gmail(thread), _pending_state(tmp_path / "s.json")
    process_approvals(gmail, CFG, state)
    assert gmail.sent_drafts == [] and state.pending()["d1"]["status"] == "pending"


def test_owner_approve_sends_and_reject_does_not(tmp_path):
    gmail = _Gmail([_email("o1", f"Me <{OWNER}>", "Re: approval", "APPROVE\n\nOn Tue, JobAgent wrote:\n> ...", internal_date=2_000)])
    state = _pending_state(tmp_path / "a.json")
    process_approvals(gmail, CFG, state)
    assert gmail.sent_drafts == ["d1"] and state.data["pending"]["d1"]["status"] == "sent"
    assert ("src", ["JobAgent/Sent"]) in gmail.labels and gmail.mail[-1]["to"] == OWNER

    gmail = _Gmail([_email("o1", f"Me <{OWNER}>", "Re: approval", "REJECT", internal_date=2_000)])
    state = _pending_state(tmp_path / "b.json")
    process_approvals(gmail, CFG, state)
    assert gmail.sent_drafts == [] and state.data["pending"]["d1"]["status"] == "rejected"


def test_approve_on_missing_draft_never_sends(tmp_path):
    gmail = _Gmail([_email("o1", f"Me <{OWNER}>", "Re", "APPROVE", internal_date=2_000)], draft_exists=False)
    state = _pending_state(tmp_path / "s.json")
    process_approvals(gmail, CFG, state)
    assert gmail.sent_drafts == [] and state.data["pending"]["d1"]["status"] == "missing"


def test_approved_draft_is_not_sent_twice(tmp_path):
    gmail = _Gmail([_email("o1", f"Me <{OWNER}>", "Re", "APPROVE", internal_date=2_000)])
    state = _pending_state(tmp_path / "s.json")
    process_approvals(gmail, CFG, state)
    process_approvals(gmail, CFG, state)  # second poll: status is no longer "pending"
    assert gmail.sent_drafts == ["d1"]


def test_email_reply_path_calls_the_shared_approve_reject_functions(monkeypatch, tmp_path):
    """process_approvals must delegate to approval.approve_draft/reject_draft rather than
    sending/rejecting inline — these are the same functions Mission Control API's manual
    approve/reject endpoints call, so there is exactly one place that ever sends a draft."""
    calls = []
    real_approve, real_reject = approval_module.approve_draft, approval_module.reject_draft
    monkeypatch.setattr(approval_module, "approve_draft", lambda *a: (calls.append(("approve", a)), real_approve(*a))[1])
    monkeypatch.setattr(approval_module, "reject_draft", lambda *a: (calls.append(("reject", a)), real_reject(*a))[1])

    gmail = _Gmail([_email("o1", f"Me <{OWNER}>", "Re: approval", "APPROVE", internal_date=2_000)])
    state = _pending_state(tmp_path / "a.json")
    process_approvals(gmail, CFG, state)
    assert [c[0] for c in calls] == ["approve"] and calls[0][1][:2] == (gmail, CFG) and calls[0][1][3] == "d1"
    assert gmail.sent_drafts == ["d1"]

    calls.clear()
    gmail = _Gmail([_email("o1", f"Me <{OWNER}>", "Re: approval", "REJECT", internal_date=2_000)])
    state = _pending_state(tmp_path / "b.json")
    process_approvals(gmail, CFG, state)
    assert [c[0] for c in calls] == ["reject"] and state.data["pending"]["d1"]["status"] == "rejected"


# ---- 2a. recruiter address is always the literal From: address --------------------------
def test_high_match_drafts_reply_to_literal_sender_and_only_notifies_owner(cv, tmp_path):
    gmail, state = _Gmail(), State(tmp_path / "state.json")
    email = _email("e1", "Acme Recruiting <talent@acme.com>", "QA Engineer opening", "We are hiring a QA engineer. Selenium required.")
    agent_main.handle_email(gmail, _Gemini("HIGH"), CFG, state, email)
    assert gmail.reply_drafts[0]["to"] == "talent@acme.com"      # literal From:, never derived
    assert gmail.reply_drafts[0]["body"].startswith("Dear Recruiter")
    assert gmail.sent_drafts == [] and gmail.drafts == []          # nothing sent, no fresh-address draft
    assert [m["to"] for m in gmail.mail] == [OWNER]
    rec = state.pending()["d1"]
    assert rec["sender_email"] == "talent@acme.com" and rec["letter_mode"] == "tailored"
    assert ("e1", ["JobAgent/Processed", "JobAgent/High"]) in gmail.labels


def test_medium_and_low_never_create_drafts(cv, tmp_path):
    for conf, extra in (("MEDIUM", "JobAgent/Review"), ("LOW", "JobAgent/Low")):
        gmail, state = _Gmail(), State(tmp_path / f"{conf}.json")
        agent_main.handle_email(gmail, _Gemini(conf), CFG, state, _email("e1", "R <r@x.com>", "s", "b"))
        assert gmail.reply_drafts == [] and state.pending() == {}
        assert ("e1", ["JobAgent/Processed", extra]) in gmail.labels
        assert len(gmail.mail) == (1 if conf == "MEDIUM" else 0)


def test_owner_and_agent_mail_is_skipped_without_scoring(cv, tmp_path):
    gmail, state = _Gmail(), State(tmp_path / "state.json")
    for frm, subj in ((f"Me <{OWNER}>", "anything"), ("Bot <b@x.com>", "[JobAgent] Approval needed: x"), ("Job Finder <haalim406@gmail.com>", "New SQA job: Senior QA Engineer")):
        agent_main.handle_email(gmail, _Gemini("HIGH"), CFG, state, _email("e1", frm, subj, "body"))
    assert gmail.reply_drafts == [] and all(names == ["JobAgent/Processed"] for _, names in gmail.labels)


# ---- 3a. Gemini outages degrade, never crash -------------------------------------------
def test_gemini_down_during_scoring_leaves_email_for_next_run(cv, tmp_path):
    gmail, state = _Gmail(), State(tmp_path / "state.json")
    agent_main.handle_email(gmail, _Gemini(fail=[ScoreResult]), CFG, state, _email("e1", "R <r@x.com>", "s", "b"))
    assert gmail.labels == [] and gmail.reply_drafts == []  # unlabelled -> retried next poll


def test_gemini_down_after_scoring_falls_back_to_default_letter(cv, tmp_path):
    gmail, state = _Gmail(), State(tmp_path / "state.json")
    agent_main.handle_email(gmail, _Gemini("HIGH", fail=[JDResult, CoverLetterResult]), CFG, state,
                            _email("e1", "R <r@x.com>", "s", "b"))
    rec = state.pending()["d1"]
    assert rec["letter_mode"] == "default" and "Jane Doe" in rec["letter"] and "QA Engineer position" in rec["letter"]
    assert gmail.reply_drafts[0]["to"] == "r@x.com"


def test_run_once_survives_one_bad_email(cv, tmp_path):
    class G(_Gmail):
        def search(self, q, n):
            return ["bad", "good"]

        def get(self, msg_id):
            if msg_id == "bad":
                raise RuntimeError("boom")
            return _email("good", "R <r@x.com>", "s", "b")

    gmail, state = G(), State(tmp_path / "state.json")
    agent_main.run_once(gmail, _Gemini("HIGH"), CFG, state)
    assert [d["to"] for d in gmail.reply_drafts] == ["r@x.com"]


# ---- 3c. watch loop survives Gmail/network failures ---------------------------------------
def test_watch_loop_logs_and_continues_when_gmail_fails(monkeypatch, tmp_path, caplog):
    calls = []

    def run_once(*a):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("gmail down")
        raise KeyboardInterrupt  # end the loop after the second iteration

    monkeypatch.setattr(agent_main, "run_once", run_once)
    monkeypatch.setattr(agent_main, "load_config", lambda: dict(CFG, gemini_api_key="k", gemini_model="m",
                                                                gmail_credentials_file=tmp_path, gmail_token_file=tmp_path,
                                                                state_file=tmp_path / "s.json"))
    monkeypatch.setattr(agent_main, "Gemini", lambda *a, **k: object())
    monkeypatch.setattr(agent_main, "Gmail", lambda *a, **k: object())
    monkeypatch.setattr(agent_main.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        agent_main.main(["watch"])
    assert len(calls) == 2 and "Run failed" in caplog.text


# ---- 2c. CV is read dynamically ---------------------------------------------------------------
def test_swapping_cv_file_changes_profile_without_code_changes(monkeypatch, tmp_path):
    from agent import cv_reader

    cv_dir = tmp_path / "cv"
    cv_dir.mkdir()
    monkeypatch.setattr(cv_reader, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(cv_reader, "extract_text", lambda p: p.read_text() * 20)

    class G:
        def structured(self, prompt, schema, temperature=0.2):
            return CVProfile(name="Jane Doe" if "JANE" in prompt else "John Roe", current_role="SQA")

    (cv_dir / "a.docx").write_text("JANE cv text")
    p1, files = cv_reader.load_profile(G(), cv_dir)
    assert p1.name == "Jane Doe" and files["docx"].name == "a.docx"
    assert cv_reader.load_profile(G(), cv_dir)[0].name == "Jane Doe"  # cached by content digest

    (cv_dir / "a.docx").write_text("JOHN cv text")  # replace the active CV in place
    assert cv_reader.load_profile(G(), cv_dir)[0].name == "John Roe"  # digest changed -> re-extracted


def test_active_cv_is_newest_file_of_each_type(tmp_path):
    (tmp_path / "old.pdf").write_bytes(b"x")
    (tmp_path / "new.pdf").write_bytes(b"y")
    os.utime(tmp_path / "old.pdf", (1, 1))
    (tmp_path / "~$lock.docx").write_bytes(b"z")  # Word lock file is ignored
    assert {k: v.name for k, v in active_cv_files(tmp_path).items()} == {"pdf": "new.pdf"}
    with pytest.raises(FileNotFoundError):
        active_cv_files(tmp_path / "empty")


# ---- 3b / 6a. state file integrity --------------------------------------------------------------
def test_state_save_is_atomic_and_reloads(tmp_path):
    path = tmp_path / "state.json"
    s = State(path)
    s.add_pending("d1", {"status": "pending"})
    s.mark_link("https://x/1", "drafted", draft_id="d1")
    assert not path.with_suffix(".json.tmp").exists()
    again = State(path)
    assert again.pending() == {"d1": {"status": "pending"}} and again.link_status("https://x/1") == "drafted"
    assert json.loads(path.read_text())["links"]["https://x/1"]["draft_id"] == "d1"
