"""Roadmap item 3: a hanging source must not stall the poll."""
import threading
import time

import pytest
import requests

import app.scheduler as sched
from app.adapters.base import BaseAdapter
from app.adapters.rss_sources import RSSAdapter
from app.scheduler import OUTCOMES, poll_all_sources
from tests.conftest import raw
from tests.test_pipeline import Stub, stub_classify


class Hang(BaseAdapter):
    name = "hang"

    def fetch(self):
        time.sleep(30)  # far beyond the test deadline
        return []


def test_hanging_adapter_is_skipped_and_others_run(monkeypatch, clean_db):
    monkeypatch.setattr(sched, "classify", stub_classify)
    monkeypatch.setattr(sched, "notify_all", lambda job: ["email"])
    monkeypatch.setattr(sched.get_settings(), "ADAPTER_TIMEOUT_SECONDS", 2, raising=False)
    monkeypatch.setattr(sched, "ADAPTERS", [Hang(), Stub([raw(external_id="ok", title="SDET")])])
    t0 = time.time()
    stats = poll_all_sources()
    assert time.time() - t0 < 10, "poll must not wait for the hanging adapter"
    assert stats["saved"] == 1 and stats["fetched"] == sum(stats[k] for k in OUTCOMES)


def test_rss_fetch_uses_bounded_request(monkeypatch):
    """The RSS adapter must go through requests.get(timeout=...) rather than feedparser's own fetch."""
    seen = {}

    def fake_get(url, timeout=None, headers=None):
        seen["timeout"] = timeout
        raise requests.ReadTimeout("simulated slow feed")

    monkeypatch.setattr(requests, "get", fake_get)

    class Feed(RSSAdapter):
        name = "slowfeed"
        feed_urls = ["https://slow.example-corp.io/jobs.rss"]

    with pytest.raises(requests.ReadTimeout):
        Feed().fetch()
    assert seen["timeout"] == 20
