from agent.approval import _decision
from agent.cover_letter import fallback_letter
from agent.gmail_client import html_to_text
from agent.models import CVProfile


def test_fallback_letter_uses_cv_values():
    p = CVProfile(name="Jane Doe", current_role="SQA Engineer",
                  core_skill_areas=["manual testing", "test automation"],
                  key_skills=["Selenium", "Postman", "JIRA", "Cypress"])
    letter = fallback_letter(p, "QA Engineer")
    assert "QA Engineer position" in letter
    assert "manual testing and test automation" in letter
    assert "Selenium, Postman and JIRA" in letter
    assert letter.rstrip().endswith("Jane Doe\nSQA Engineer")


def test_fallback_role_defaults_to_cv_role():
    p = CVProfile(name="A", current_role="Test Engineer")
    assert "Test Engineer position" in fallback_letter(p, None)


def test_decision_parsing():
    assert _decision("APPROVE\n\nOn Tue, X wrote:\n> ...", "APPROVE", "REJECT") == "approve"
    assert _decision("approve.", "APPROVE", "REJECT") == "approve"
    assert _decision("Reject this one", "APPROVE", "REJECT") == "reject"
    assert _decision("Looks good, approve", "APPROVE", "REJECT") is None
    assert _decision("> APPROVE", "APPROVE", "REJECT") is None


def test_html_to_text():
    assert html_to_text("<p>Hi<br>there</p><style>x{}</style>&amp;") == "Hi\nthere\n&"


def test_gemini_retries_then_raises_unavailable(monkeypatch):
    from agent import gemini_client
    from agent.gemini_client import Gemini, GeminiUnavailable

    sleeps = []
    monkeypatch.setattr(gemini_client.time, "sleep", sleeps.append)
    g = Gemini.__new__(Gemini)
    g.model, g.max_retries, g.initial_backoff = "m", 3, 3.0

    class Boom(Exception):
        code = 503

    class FakeModels:
        calls = 0

        def generate_content(self, **kw):
            FakeModels.calls += 1
            raise Boom("503 UNAVAILABLE")

    g.client = type("C", (), {"models": FakeModels()})()
    import pytest
    with pytest.raises(GeminiUnavailable):
        g.structured("p", CVProfile)
    assert FakeModels.calls == 3
    assert sleeps == [3.0, 6.0]  # no sleep after the final attempt


def test_build_letter_falls_back_when_gemini_unavailable(monkeypatch):
    from agent import cover_letter
    from agent.gemini_client import GeminiUnavailable
    from agent.models import JDResult

    def boom(*a, **k):
        raise GeminiUnavailable("down")

    monkeypatch.setattr(cover_letter, "tailored_letter", boom)
    p = CVProfile(name="Jane Doe", current_role="SQA Engineer", core_skill_areas=["testing"], key_skills=["Selenium"])
    jd = JDResult(usable=True, role_title="QA Lead", requirements=["x"])
    letter, mode = cover_letter.build_letter(None, jd, p, "Bob", None)
    assert mode == "default"
    assert "QA Lead position" in letter and "Jane Doe" in letter
