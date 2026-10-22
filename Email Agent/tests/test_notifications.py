import logging
from urllib.parse import parse_qs, urlparse

from agent import notifications
from agent.notifications import Notifier, _clip


class _Gmail:
    def __init__(self):
        self.sent = []

    def send_mail(self, to, subject, body):
        self.sent.append((to, subject, body))
        return {"threadId": "t42"}


def _cfg(channels):
    return {"owner_email": "owner@example.com", "notifications": {"channels": channels}}


class _Resp:
    def __init__(self, status=200, text="ok"):
        self.status_code, self.text = status, text


def test_email_only_default_keeps_thread_id():
    g = _Gmail()
    assert Notifier({"owner_email": "o@x.com"}).send("s", "b", g) == {"email": "t42"}
    assert g.sent == [("o@x.com", "s", "b")]


def test_side_channels_fail_independently_without_blocking(monkeypatch, caplog):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
    monkeypatch.setenv("CALLMEBOT_PHONE", "+920000000000")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "123")

    def boom(*a, **k):
        raise ConnectionError("dns")

    monkeypatch.setattr(notifications.requests, "post", boom)
    monkeypatch.setattr(notifications.requests, "get", boom)
    g = _Gmail()
    with caplog.at_level(logging.WARNING):
        res = Notifier(_cfg(["slack", "whatsapp", "email"])).send("s", "b", g)
    assert res == {"email": "t42", "slack": None, "whatsapp": None}
    assert g.sent  # email still went out
    assert "slack notification failed" in caplog.text and "whatsapp notification failed" in caplog.text


def test_missing_secrets_are_a_warning_not_a_crash(monkeypatch, caplog):
    for k in ("SLACK_WEBHOOK_URL", "CALLMEBOT_PHONE", "CALLMEBOT_APIKEY"):
        monkeypatch.delenv(k, raising=False)
    with caplog.at_level(logging.WARNING):
        assert Notifier(_cfg(["slack", "whatsapp"])).send("s", "b") == {"slack": None, "whatsapp": None}
    assert "SLACK_WEBHOOK_URL is not set" in caplog.text and "CALLMEBOT_PHONE" in caplog.text


def test_slack_and_callmebot_success_with_url_encoding(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
    monkeypatch.setenv("CALLMEBOT_PHONE", "+920000000000")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "abc&123")
    posted, got = [], []
    monkeypatch.setattr(notifications.requests, "post", lambda url, json, timeout: posted.append((url, json)) or _Resp())
    monkeypatch.setattr(notifications.requests, "get", lambda url, timeout: got.append(url) or _Resp(text="<p>Message queued.</p>"))

    res = Notifier(_cfg(["slack", "whatsapp"])).send("[JobAgent] Approval needed: QA — hr@acme.com", "Line 1\nA & B = 100%")
    assert res == {"slack": "ok", "whatsapp": "ok"}
    assert posted == [("https://hooks.slack.com/x", {"text": "[JobAgent] Approval needed: QA — hr@acme.com\n\nLine 1\nA & B = 100%"})]
    u = urlparse(got[0])
    assert u.scheme == "https" and u.netloc == "api.callmebot.com" and u.path == "/whatsapp.php"
    q = parse_qs(u.query)  # decodes; proves the raw URL was encoded correctly
    assert q["phone"] == ["+920000000000"] and q["apikey"] == ["abc&123"]
    assert q["text"] == ["[JobAgent] Approval needed: QA — hr@acme.com\n\nLine 1\nA & B = 100%"]
    assert "&text=%5BJobAgent%5D" in got[0] and "%26" in got[0]  # brackets and & are percent-encoded


def test_callmebot_error_body_is_reported(monkeypatch, caplog):
    monkeypatch.setenv("CALLMEBOT_PHONE", "+920000000000")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "k")
    monkeypatch.setattr(notifications.requests, "get", lambda url, timeout: _Resp(text="<b>ERROR: APIKey is invalid</b>"))
    with caplog.at_level(logging.WARNING):
        assert Notifier(_cfg(["whatsapp"])).send("s", "b") == {"whatsapp": None}
    assert "APIKey is invalid" in caplog.text


def test_clip_respects_whatsapp_limit():
    assert len(_clip("x" * 5000)) <= 1500 and _clip("short") == "short"
