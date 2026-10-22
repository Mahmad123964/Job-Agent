"""Read-only + never-crash guarantees for the state reader and poller."""
import json
import time

from fastapi.testclient import TestClient

from mission_control.app import create_app
from mission_control.events import Poller
from mission_control.state_reader import SafeJsonFile

from .test_api import URL, _cfg, _state


def test_empty_file_is_treated_as_mid_write_not_as_no_data(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(_state()), encoding="utf-8")
    r = SafeJsonFile(f, default={}, retries=2, retry_delay=0)
    good = r.read()
    f.write_text("", encoding="utf-8")  # write_text truncates first: this is what a poll can see mid-write
    assert r.read() == good              # last good snapshot, not the default
    f.write_text("   \n", encoding="utf-8")
    assert r.read() == good


def test_corrupt_bytes_and_garbage_json_keep_last_good(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps(_state()), encoding="utf-8")
    r = SafeJsonFile(f, default={}, retries=2, retry_delay=0)
    good = r.read()
    f.write_bytes(b"\xff\xfe\x00garbage")   # undecodable
    assert r.read() == good
    f.write_text('{"pending": [1, 2', encoding="utf-8")
    assert r.read() == good
    f.write_text(json.dumps(_state("sent")), encoding="utf-8")  # recovers as soon as the file is whole
    assert r.read()["pending"]["d1"]["status"] == "sent"


def test_poller_refresh_survives_corrupt_state_and_emits_no_phantom_events(tmp_path):
    cfg = _cfg(tmp_path)
    p = Poller(cfg)
    n = len(p.missions)
    (tmp_path / "state.json").write_text("", encoding="utf-8")
    assert p.refresh() == [] and len(p.missions) == n     # nothing vanished, nothing re-appeared
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    assert p.refresh() == [] and len(p.missions) == n
    (tmp_path / "state.json").write_text(json.dumps(_state("sent")), encoding="utf-8")
    assert [e["type"] for e in p.refresh()] == ["sent"]   # only the real transition


def test_api_stays_up_while_state_file_is_corrupt(tmp_path):
    cfg = _cfg(tmp_path)
    with TestClient(create_app(cfg)) as c:
        before = c.get("/api/missions").json()
        (tmp_path / "state.json").write_text('{"pending": {"a": ', encoding="utf-8")
        time.sleep(0.3)  # several poll cycles at 0.05s
        assert c.get("/api/status").status_code == 200
        assert c.get("/api/missions").json() == before
        assert c.get("/api/stats").status_code == 200
        # still read-only: the corrupt file was left exactly as the agent wrote it
        assert (tmp_path / "state.json").read_text(encoding="utf-8") == '{"pending": {"a": '


def test_service_never_writes_agent_files(tmp_path):
    cfg = _cfg(tmp_path, scout={"jobs": [{"url": URL, "title": "QA Lead", "company": "Acme", "found_at": "2026-09-19T09:00:00"}]})
    state_raw = (tmp_path / "state.json").read_bytes()
    scout_raw = (tmp_path / "scout_state.json").read_bytes()
    state_m, scout_m = (tmp_path / "state.json").stat().st_mtime_ns, (tmp_path / "scout_state.json").stat().st_mtime_ns
    with TestClient(create_app(cfg)) as c:
        c.get("/api/missions"); c.get("/api/stats"); c.get("/api/events")
        c.get(f"/api/missions/{list(c.get('/api/missions').json())[0]['id']}")
        time.sleep(0.2)
    assert (tmp_path / "state.json").read_bytes() == state_raw and (tmp_path / "state.json").stat().st_mtime_ns == state_m
    assert (tmp_path / "scout_state.json").read_bytes() == scout_raw and (tmp_path / "scout_state.json").stat().st_mtime_ns == scout_m
    # the only file the service writes is its own events log
    assert {p.name for p in tmp_path.iterdir()} == {"state.json", "scout_state.json", "events.jsonl"}
