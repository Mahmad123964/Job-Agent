"""JSearch publisher allowlist (JSEARCH_TRUSTED_PUBLISHERS)."""
from app.adapters.jsearch import JSearchAdapter, is_trusted_publisher

TRUSTED = ["LinkedIn", "Indeed", "Glassdoor", "ZipRecruiter", "Monster", "CareerBuilder", "Dice", "Jobgether"]


def item(publisher, i):
    return {"job_id": f"id{i}", "job_title": f"QA Engineer {i}", "employer_name": "Co", "job_publisher": publisher,
            "job_apply_link": f"https://{publisher.lower().replace(' ', '')}.example-corp.io/{i}", "job_is_remote": True,
            "job_posted_at_datetime_utc": "2026-09-18T00:00:00.000Z"}


def test_allowlist_matching():
    assert is_trusted_publisher("LinkedIn", TRUSTED) and is_trusted_publisher("indeed", TRUSTED)
    assert not is_trusted_publisher("Vacancy Global Pro", TRUSTED)
    assert not is_trusted_publisher("Lensa", TRUSTED) and not is_trusted_publisher("BeBee", TRUSTED) and not is_trusted_publisher("", TRUSTED)
    assert is_trusted_publisher("Anything", [])  # empty allowlist = keep all


def test_parse_drops_untrusted_publishers_and_counts_them():
    payload = {"data": {"jobs": [item("Indeed", 1), item("Vacancy Global Pro", 2), item("LinkedIn", 3), item("Jooble", 4),
                                 item("ZipRecruiter", 5), item("Vacancy Global Pro", 6), item("BeBee", 7)], "cursor": ""}}
    jobs = JSearchAdapter.parse(payload, trusted=TRUSTED)
    assert [j.tags[0] for j in jobs] == ["Indeed", "LinkedIn", "ZipRecruiter"]
    assert dict(JSearchAdapter.last_excluded) == {"Vacancy Global Pro": 2, "Jooble": 1, "BeBee": 1}
