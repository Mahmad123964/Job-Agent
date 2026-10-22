"""Dedup cases (audit Phase 11)."""
from app.dedup import DedupKey, is_duplicate, normalize_title
from app.normalizer import normalize
from tests.conftest import raw


def keys(*jobs):
    return [DedupKey.from_normalized(normalize(j)) for j in jobs]


def test_exact_source_and_external_id():
    a = raw(source="remoteok", external_id="1", title="QA Engineer", url="https://x.example-corp.io/a")
    b = raw(source="remoteok", external_id="1", title="Totally different title", company="Other", url="https://y.example-corp.io/b")
    assert is_duplicate(normalize(b), keys(a))


def test_same_canonical_url_with_tracking_params_and_trailing_slash():
    a = raw(source="jsearch", external_id="j1", title="Senior QA Engineer", company="Acme Inc",
            url="https://boards.greenhouse.io/acme/jobs/123?gh_src=linkedin&utm_source=x")
    b = raw(source="remoteok", external_id="r1", title="Senior QA Engineer (Remote)", company="",
            url="https://boards.greenhouse.io/acme/jobs/123/")
    assert is_duplicate(normalize(b), keys(a))


def test_fuzzy_title_and_company():
    a = raw(source="remoteok", external_id="1", title="QA Engineer (Remote)", company="Acme Inc.", url="https://a.example-corp.io/1")
    b = raw(source="weworkremotely", external_id="99", title="Remote QA Engineer", company="ACME", url="https://b.example-corp.io/2")
    assert is_duplicate(normalize(b), keys(a))


def test_cross_board_repost_same_ats_domain_different_company_string():
    a = raw(source="remoteok", external_id="1", title="QA Engineer (Remote)", company="Acme Inc.", url="https://boards.greenhouse.io/acme/jobs/1")
    c = raw(source="himalayas", external_id="7", title="Remote QA Engineer", company="Confidential", url="https://boards.greenhouse.io/acme/jobs/1")
    assert is_duplicate(normalize(c), keys(a))


def test_same_board_no_company_near_identical_title():
    # We Work Remotely posting the same listing twice with links differing by "-1"
    a = raw(source="weworkremotely", external_id="e3d6", title="Principal Quality Engineer", company="", url="https://weworkremotely.com/remote-jobs/lawnstarter-principal-quality-engineer-1")
    b = raw(source="weworkremotely", external_id="bdd7", title="Principal Quality Engineer", company="", url="https://weworkremotely.com/remote-jobs/lawnstarter-principal-quality-engineer")
    assert is_duplicate(normalize(b), keys(a))


def test_different_roles_are_not_duplicates():
    a = raw(source="remoteok", external_id="1", title="QA Engineer (Remote)", company="Acme Inc.", url="https://boards.greenhouse.io/acme/jobs/1")
    d = raw(source="jobicy", external_id="8", title="Senior Data Engineer", company="Acme", url="https://boards.greenhouse.io/acme/jobs/2")
    e = raw(source="weworkremotely", external_id="x", title="Senior Backend Engineer", company="", url="https://weworkremotely.com/remote-jobs/acme-backend")
    assert not is_duplicate(normalize(d), keys(a))
    assert not is_duplicate(normalize(e), keys(a))


def test_normalize_title_drops_noise_words():
    assert normalize_title("Remote QA Engineer!") == "qa engineer"
