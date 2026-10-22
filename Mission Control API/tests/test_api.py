import json
import time

from fastapi.testclient import TestClient

from mission_control.app import create_app
from mission_control.events import diff_missions
from mission_control.missions import build_missions, mission_id
from mission_control.state_reader import SafeJsonFile

URL = "https://pk.indeed.com/viewjob?jk=1"
LI = "https://www.linkedin.com/jobs/view/9/"


def _state(status="pending"):
    return {
        "pending": {"d1": {"status": status, "source_msg_id": "x", "sender": "Acme HR <hr@acme.com>", "sender_email": "hr@acme.com",
                           "subject": "Application for QA Lead — Acme", "job_url": URL, "platform": "indeed", "role": "QA Lead",
                           "letter_mode": "tailored", "letter": "Dear...", "cv_files": ["cv.pdf"], "signals": [],
                           "draft_url": "https://mail.google.com/#drafts/1", "notified_at": 1789724988722},
                    "d2": {"status": "pending", "sender": "Bob <bob@x.com>", "sender_email": "bob@x.com", "subject": "Re: SQA",
                           "role": "SQA", "letter_mode": "default", "letter": "Hi", "cv_files": [], "signals": [], "notified_at": 1789724988722}},
        "links": {URL: {"status": "drafted", "email": "hr@acme.com", "draft_id": "d1", "role": "QA Lead", "company": "Acme"},
                  LI: {"status": "linkedin_manual_lookup", "role": "Dev", "company": "LinkedCo"},
                  "https://himalayas.app/j/1": {"status": "manual_review", "reason": "no email on page"}},
    }


def _cfg(tmp_path, scout=None, state=None):
    (tmp_path / "state.json").write_text(json.dumps(state or _state()), encoding="utf-8")
    if scout is not None:
        (tmp_path / "scout_state.json").write_text(json.dumps(scout), encoding="utf-8")
    return {"agents_dir": tmp_path, "apply_state_file": tmp_path / "state.json", "scout_state_file": tmp_path / "scout_state.json",
            "events_log": tmp_path / "events.jsonl", "active_window_seconds": 30, "poll_interval_seconds": 0.05,
            "cors_origins": ["http://localhost:3000"], "owner_email": "owner@example.com"}


def test_build_missions_merges_three_sources():
    scout = {"jobs": [{"url": URL, "title": "QA Lead", "company": "Acme", "found_at": "2026-09-19T09:00:00"},
                      {"url": "https://remotive.com/j/5", "title": "Dev", "company": "R", "found_at": 1789724988722}]}
    ms = build_missions(scout, _state("sent"), file_mtime=time.time())
    assert len(ms) == 5  # 2 scout (one shared with links) + linkedin + himalayas + email-only draft
    m = ms[mission_id(URL)]
    assert (m.stage, m.progress, m.recruiter_email_status, m.application_status) == ("sent", 100, "verified", "sent")
    assert m.discovered_at == "2026-09-19T09:00:00" and m.draft_content == "Dear..."
    assert ms[mission_id(LI)].recruiter_email_status == "manual_lookup_needed"
    assert ms[mission_id("https://himalayas.app/j/1")].notes == "no email on page"
    only_email = ms[mission_id("draft:d2")]
    assert only_email.discovered_by == "APPLY" and only_email.stage == "application_ready" and only_email.recruiter_name == "Bob"


def test_diff_emits_only_transitions():
    old = build_missions(None, {"links": {URL: {"status": "manual_review"}}})
    new = build_missions(None, _state("pending"))
    types = [(e["type"], e["mission_id"]) for e in diff_missions(old, new, "t")]
    assert ("recruiter_verified", mission_id(URL)) in types and ("draft_ready", mission_id(URL)) in types
    assert ("job_found", mission_id(URL)) not in types  # existed before
    assert ("job_found", mission_id(LI)) in types and ("manual_lookup_needed", mission_id(LI)) in types
    sent = build_missions(None, _state("sent"))
    assert [e["type"] for e in diff_missions(new, sent, "t")] == ["sent"]
    assert diff_missions(sent, sent, "t") == []


def test_safe_reader_survives_partial_write(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"pending": {}}', encoding="utf-8")
    r = SafeJsonFile(f, default={}, retries=2, retry_delay=0)
    assert r.read() == {"pending": {}}
    f.write_text('{"pending": {"a": ', encoding="utf-8")  # mid-write
    assert r.read() == {"pending": {}}  # last good snapshot, no exception
    f.unlink()
    assert r.read() == {}


def test_endpoints_and_websocket(tmp_path):
    cfg = _cfg(tmp_path, scout={"jobs": [{"url": "https://remotive.com/j/5", "title": "Dev", "company": "R",
                                          "found_at": time.strftime("%Y-%m-%dT%H:%M:%S")}]})
    with TestClient(create_app(cfg)) as c:
        status = c.get("/api/status").json()
        st = status["agents"]
        assert set(st) == {"SCOUT", "CONNECT", "APPLY"} and st["SCOUT"]["status"] == "scanning"  # file just written
        assert status["owner_email"] == "owner@example.com"

        ms = c.get("/api/missions").json()
        assert len(ms) == 5 and {"title", "company", "platform", "discovered_at", "recruiter_email_status", "application_status", "progress"} <= set(ms[0])
        assert "draft_content" not in ms[0]

        detail = c.get(f"/api/missions/{mission_id(URL)}").json()
        assert detail["draft_content"] == "Dear..." and detail["recruiter_email"] == "hr@acme.com"
        assert {e["type"] for e in detail["timeline"]} >= {"job_found", "recruiter_verified", "draft_ready"}
        assert c.get("/api/missions/nope").status_code == 404

        s = c.get("/api/stats").json()
        assert s["jobs_discovered_today"] >= 1 and s["recruiters_verified"] == 2 and s["awaiting_approval"] == 2

        with c.websocket_connect("/ws/events") as ws:
            assert ws.receive_json()["type"] == "connected"
            (tmp_path / "state.json").write_text(json.dumps(_state("sent")), encoding="utf-8")
            ev = ws.receive_json()
            assert ev["type"] == "sent" and ev["mission_id"] == mission_id(URL) and ev["agent"] == "APPLY"
        assert c.get("/api/stats").json()["applications_sent"] == 1
        assert c.get("/api/events").json()[-1]["type"] == "sent"
        # the agents' files were never written by the service
        assert json.loads((tmp_path / "state.json").read_text()) == _state("sent")


def test_cors_allows_any_localhost_port_by_regex(tmp_path):
    """The frontend's dev port varies (Vite auto-increments), so any localhost/127.0.0.1/[::1]
    origin must be allowed regardless of port, while a non-local origin is rejected."""
    cfg = _cfg(tmp_path)
    cfg["cors_origin_regex"] = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
    with TestClient(create_app(cfg)) as c:
        for origin in ("http://localhost:3000", "http://localhost:3001", "http://localhost:5173",
                      "http://127.0.0.1:4173", "http://[::1]:3000"):
            resp = c.get("/api/stats", headers={"Origin": origin})
            assert resp.headers.get("access-control-allow-origin") == origin, origin

        blocked = c.get("/api/stats", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in blocked.headers


class _FakeApprovalError(Exception):
    """Stands in for agent.approval.ApprovalError without importing the real sibling package —
    HTTP-layer tests only need to prove app.py maps bridge exceptions to the right status code."""


def _mark_sent_like_agent2_would(tmp_path, draft_id="d1", to="hr@acme.com"):
    """What agent.approval.approve_draft actually does to state.json, for the endpoint test to
    assert against (it doesn't call the real function — that's covered by test_agent2_bridge.py)."""
    def fake_approve(agents_dir, apply_state_file, draft_id_):
        data = json.loads(apply_state_file.read_text())
        data["pending"][draft_id_]["status"] = "sent"
        apply_state_file.write_text(json.dumps(data))
        return {"id": "gmail-msg-1"}
    return fake_approve


def test_approve_endpoint_sends_and_updates_immediately(tmp_path, monkeypatch):
    from mission_control import agent2_bridge as bridge

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(bridge, "approval_error_type", lambda agents_dir: _FakeApprovalError)
    monkeypatch.setattr(bridge, "approve", _mark_sent_like_agent2_would(tmp_path))

    with TestClient(create_app(cfg)) as c:
        with c.websocket_connect("/ws/events") as ws:
            assert ws.receive_json()["type"] == "connected"

            resp = c.post(f"/api/missions/{mission_id(URL)}/approve")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "sent" and body["gmail_message_id"] == "gmail-msg-1"
            assert body["mission"]["application_status"] == "sent" and body["mission"]["stage"] == "sent"

            # picked up immediately — no waiting for the next poll tick
            assert c.get("/api/missions").json() != []
            fresh = next(m for m in c.get("/api/missions").json() if m["id"] == mission_id(URL))
            assert fresh["application_status"] == "sent"

            ev = ws.receive_json()
            assert ev["type"] == "sent" and ev["mission_id"] == mission_id(URL)


def test_reject_endpoint_marks_rejected(tmp_path, monkeypatch):
    from mission_control import agent2_bridge as bridge

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(bridge, "approval_error_type", lambda agents_dir: _FakeApprovalError)

    def fake_reject(agents_dir, apply_state_file, draft_id):
        data = json.loads(apply_state_file.read_text())
        data["pending"][draft_id]["status"] = "rejected"
        apply_state_file.write_text(json.dumps(data))

    monkeypatch.setattr(bridge, "reject", fake_reject)

    with TestClient(create_app(cfg)) as c:
        resp = c.post(f"/api/missions/{mission_id(URL)}/reject")
        assert resp.status_code == 200 and resp.json()["status"] == "rejected"
        assert resp.json()["mission"]["application_status"] == "rejected"


def test_approve_endpoint_404_for_unknown_mission(tmp_path):
    with TestClient(create_app(_cfg(tmp_path))) as c:
        assert c.post("/api/missions/does-not-exist/approve").status_code == 404


def test_approve_endpoint_409_when_not_awaiting_approval(tmp_path):
    # LI is a LinkedIn manual-lookup mission with no draft at all.
    with TestClient(create_app(_cfg(tmp_path))) as c:
        resp = c.post(f"/api/missions/{mission_id(LI)}/approve")
        assert resp.status_code == 409


def test_approve_endpoint_409_when_bridge_raises_approval_error(tmp_path, monkeypatch):
    from mission_control import agent2_bridge as bridge

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(bridge, "approval_error_type", lambda agents_dir: _FakeApprovalError)

    def fake_approve(agents_dir, apply_state_file, draft_id):
        raise _FakeApprovalError(f"draft {draft_id} is already sent, not pending")

    monkeypatch.setattr(bridge, "approve", fake_approve)
    with TestClient(create_app(cfg)) as c:
        resp = c.post(f"/api/missions/{mission_id(URL)}/approve")
        assert resp.status_code == 409 and "already sent" in resp.json()["detail"]


def test_approve_endpoint_404_when_draft_missing_from_state(tmp_path, monkeypatch):
    from mission_control import agent2_bridge as bridge

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(bridge, "approval_error_type", lambda agents_dir: _FakeApprovalError)

    def fake_approve(agents_dir, apply_state_file, draft_id):
        raise bridge.DraftNotFound(draft_id)

    monkeypatch.setattr(bridge, "approve", fake_approve)
    with TestClient(create_app(cfg)) as c:
        assert c.post(f"/api/missions/{mission_id(URL)}/approve").status_code == 404


def test_approve_endpoint_502_when_agent2_unavailable(tmp_path, monkeypatch):
    from mission_control import agent2_bridge as bridge

    cfg = _cfg(tmp_path)

    def unavailable(agents_dir):
        raise bridge.Agent2Unavailable("credentials.json missing")

    monkeypatch.setattr(bridge, "approval_error_type", unavailable)
    with TestClient(create_app(cfg)) as c:
        resp = c.post(f"/api/missions/{mission_id(URL)}/approve")
        assert resp.status_code == 502 and "credentials.json" in resp.json()["detail"]
