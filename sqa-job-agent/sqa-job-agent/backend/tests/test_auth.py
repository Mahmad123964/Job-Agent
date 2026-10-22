"""ADMIN_API_KEY guard (roadmap item 2). conftest sets ADMIN_API_KEY=test-admin-key."""
import pytest

MUTATING = [
    ("post", "/api/admin/archive-existing", None),
    ("post", "/api/poll-now", None),
    ("put", "/api/resume", {"text": "x"}),
    ("post", "/api/blocklist", {"company_name": "Spam Co"}),
    ("delete", "/api/blocklist/1", None),
    ("post", "/api/admin/backfill-email", {"recipient": "a@b.co"}),
    ("put", "/api/preferences", {"score_threshold": 0}),
    ("patch", "/api/jobs/1/status", {"status": "applying"}),
    ("post", "/api/jobs/1/notes", {"text": "hi"}),
    ("post", "/api/jobs/1/snooze", {"days": 3}),
]


@pytest.mark.parametrize("method,path,body", MUTATING)
def test_mutating_routes_require_key(client, method, path, body):
    kw = {"json": body} if body is not None else {}
    r = client.request(method.upper(), path, **kw)
    assert r.status_code == 401, (path, r.status_code)
    r = client.request(method.upper(), path, headers={"X-Api-Key": "wrong"}, **kw)
    assert r.status_code == 401


def test_archive_existing_with_key_succeeds(client, clean_db, monkeypatch):
    r = client.post("/api/admin/archive-existing", headers={"X-Api-Key": "test-admin-key"})
    assert r.status_code == 200 and "archived" in r.json()


def test_get_routes_stay_open(client):
    for path in ("/api/health", "/api/jobs", "/api/notifications", "/api/applied", "/api/analytics", "/api/blocklist", "/api/resume", "/api/preferences"):
        assert client.get(path).status_code == 200, path
