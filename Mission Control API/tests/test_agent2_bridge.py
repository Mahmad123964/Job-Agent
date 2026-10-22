"""Proves the bridge calls Agent 2's *real* approve_draft/reject_draft — not a reimplementation.

Uses the actual `agent.approval` module from the sibling Email Agent project (so the exact
function object exercised by process_approvals is the one under test here too), but replaces
the Gmail client and config with fakes so no real OAuth/network/Gmail call ever happens.
"""
import json
from pathlib import Path

import pytest

from mission_control import agent2_bridge

AGENTS_DIR = Path(__file__).resolve().parents[2] / "Email Agent"


class FakeGmail:
    def __init__(self, draft_exists=True):
        self._draft_exists = draft_exists
        self.sent_drafts, self.labels, self.mail = [], [], []

    def draft_exists(self, draft_id):
        return self._draft_exists

    def send_draft(self, draft_id):
        self.sent_drafts.append(draft_id)
        return {"id": "sent-123"}

    def add_labels(self, msg_id, names):
        self.labels.append((msg_id, names))

    def send_mail(self, to, subject, body):
        self.mail.append((to, subject, body))
        return {"threadId": "t1"}


FAKE_CFG = {"labels": {"sent": "JobAgent/Sent"}, "owner_email": "owner@example.com",
           "notifications": {"channels": []}}  # no channels -> notify() is a safe no-op


def _rec(status="pending"):
    return {"status": status, "source_msg_id": "src", "sender": "R <r@x.com>", "sender_email": "r@x.com",
            "subject": "Re: role", "role": "QA Lead", "letter": "Dear...", "cv_files": []}


def _state_file(tmp_path, pending=None):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"pending": pending or {}, "links": {}}), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _isolated_runtime_cache(monkeypatch):
    """Never let this test's fake Gmail/cfg leak into another test via the module cache.

    Also makes sure AGENTS_DIR is on sys.path / agent.* is imported before each test, so a
    bare `import agent.approval` inside a test resolves to the real module."""
    agent2_bridge.reset_cache_for_tests()
    agent2_bridge._import_agent_modules(AGENTS_DIR)
    yield
    agent2_bridge.reset_cache_for_tests()


def _fake_runtime(monkeypatch, gmail: FakeGmail):
    monkeypatch.setattr(agent2_bridge, "_runtime", lambda agents_dir: {"cfg": FAKE_CFG, "gmail": gmail})


def test_approval_error_type_is_agents_own_exception_class():
    from agent.approval import ApprovalError

    assert agent2_bridge.approval_error_type(AGENTS_DIR) is ApprovalError


def test_approve_calls_the_real_approve_draft_function(monkeypatch, tmp_path):
    import agent.approval as real_approval

    calls = []
    real_fn = real_approval.approve_draft
    monkeypatch.setattr(real_approval, "approve_draft", lambda *a: (calls.append(a), real_fn(*a))[1])

    gmail = FakeGmail()
    _fake_runtime(monkeypatch, gmail)
    state_file = _state_file(tmp_path, {"d1": _rec()})

    result = agent2_bridge.approve(AGENTS_DIR, state_file, "d1")

    assert len(calls) == 1 and calls[0][0] is gmail and calls[0][2].__class__.__name__ == "State" and calls[0][3] == "d1"
    assert result == {"id": "sent-123"} and gmail.sent_drafts == ["d1"]
    assert ("src", ["JobAgent/Sent"]) in gmail.labels
    assert json.loads(state_file.read_text())["pending"]["d1"]["status"] == "sent"


def test_reject_calls_the_real_reject_draft_function(monkeypatch, tmp_path):
    import agent.approval as real_approval

    calls = []
    real_fn = real_approval.reject_draft
    monkeypatch.setattr(real_approval, "reject_draft", lambda *a: (calls.append(a), real_fn(*a))[1])

    gmail = FakeGmail()
    _fake_runtime(monkeypatch, gmail)
    state_file = _state_file(tmp_path, {"d1": _rec()})

    agent2_bridge.reject(AGENTS_DIR, state_file, "d1")

    assert len(calls) == 1 and gmail.sent_drafts == []
    assert json.loads(state_file.read_text())["pending"]["d1"]["status"] == "rejected"


def test_approve_raises_approval_error_when_not_pending(monkeypatch, tmp_path):
    from agent.approval import ApprovalError

    gmail = FakeGmail()
    _fake_runtime(monkeypatch, gmail)
    state_file = _state_file(tmp_path, {"d1": _rec(status="sent")})

    with pytest.raises(ApprovalError):
        agent2_bridge.approve(AGENTS_DIR, state_file, "d1")
    assert gmail.sent_drafts == []  # never sent twice


def test_approve_raises_approval_error_when_draft_missing_from_gmail(monkeypatch, tmp_path):
    from agent.approval import ApprovalError

    gmail = FakeGmail(draft_exists=False)
    _fake_runtime(monkeypatch, gmail)
    state_file = _state_file(tmp_path, {"d1": _rec()})

    with pytest.raises(ApprovalError):
        agent2_bridge.approve(AGENTS_DIR, state_file, "d1")
    assert json.loads(state_file.read_text())["pending"]["d1"]["status"] == "missing"


def test_unknown_draft_id_raises_draft_not_found(monkeypatch, tmp_path):
    _fake_runtime(monkeypatch, FakeGmail())
    state_file = _state_file(tmp_path, {})
    with pytest.raises(agent2_bridge.DraftNotFound):
        agent2_bridge.approve(AGENTS_DIR, state_file, "nope")


def test_runtime_failure_surfaces_as_agent2_unavailable(monkeypatch, tmp_path):
    """_runtime() itself wraps any load_config()/Gmail() failure into Agent2Unavailable —
    simulate that failure at its source (missing OAuth credentials) rather than bypassing it."""
    mods = agent2_bridge._import_agent_modules(AGENTS_DIR)

    def boom_load_config():
        raise SystemExit("credentials.json missing")

    monkeypatch.setattr(mods.config, "load_config", boom_load_config)
    state_file = _state_file(tmp_path, {"d1": _rec()})
    with pytest.raises(agent2_bridge.Agent2Unavailable):
        agent2_bridge.approve(AGENTS_DIR, state_file, "d1")
