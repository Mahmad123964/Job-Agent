"""Greenhouse Job Board API — free, no auth, one board per employer.

    GET https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true

Adding another company board is one line:

    class AcmeAdapter(GreenhouseBoardAdapter):
        name = "gh-acme"
        board_token = "acme"

Single-employer boards mean low QA volume by design — most polls will
contribute nothing, which is expected rather than a failure.
"""
from html import unescape
from typing import List

import requests

from .base import BaseAdapter, RawJob, parse_datetime, strip_html

_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseBoardAdapter(BaseAdapter):
    board_token: str = ""
    company_name: str = ""          # falls back to the API's company_name
    access_method = "api (company job board)"
    source_category = "ats_board"
    timeout = 30

    def fetch(self) -> List[RawJob]:
        if not self.board_token:
            return []
        resp = requests.get(
            _API.format(token=self.board_token),
            params={"content": "true"},
            headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        jobs: List[RawJob] = []
        for item in resp.json().get("jobs", []):
            if not item.get("id") or not item.get("absolute_url"):
                continue
            # `content` is HTML-escaped HTML; strip_html unescapes twice.
            description = strip_html(unescape(item.get("content") or ""))
            location = ((item.get("location") or {}).get("name") or "").strip() or "Remote"
            departments = [d.get("name") for d in (item.get("departments") or []) if d.get("name")]
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item["id"]),
                    title=item.get("title", ""),
                    company=self.company_name or item.get("company_name", "") or self.board_token,
                    url=item["absolute_url"],
                    location=location,
                    tags=departments,
                    posted_at=parse_datetime(item.get("first_published") or item.get("updated_at")),
                    description=description,
                )
            )
        return jobs


def greenhouse_board(name: str, token: str, company: str) -> GreenhouseBoardAdapter:
    """One line per company board. Every token below was verified live
    (HTTP 200 + non-empty jobs array) before being added."""
    return type(
        f"Greenhouse_{name}",
        (GreenhouseBoardAdapter,),
        {
            "name": name,
            "board_token": token,
            "company_name": company,
            "attribution": f"Source: {company} careers (job-boards.greenhouse.io/{token}).",
        },
    )()


class RemoteComAdapter(GreenhouseBoardAdapter):
    """Remote.com's own careers board (board token "remotecom", not "remote")."""

    name = "remotecom"
    board_token = "remotecom"
    company_name = "Remote"
    attribution = "Source: Remote.com careers (job-boards.greenhouse.io/remotecom)."


# Verified live 2026-09-24 (HTTP 200, non-empty). Tier A = best worldwide/regional
# eligibility signal; Tier B = mostly US/LATAM-restricted, so most of their postings
# are expected to stop at the region gate — that is the gate working, not a failure.
GREENHOUSE_BOARDS = [
    # --- Tier A ---
    greenhouse_board("gh-testlio", "testlio", "Testlio"),                    # 23 jobs, 16 QA
    greenhouse_board("gh-canonical", "canonical", "Canonical"),              # 306 jobs, 4 QA
    greenhouse_board("gh-remotepeople", "remotepeople", "Remote People"),    # 13 jobs, 1 QA
    greenhouse_board("gh-trivelta", "trivelta", "Trivelta"),                 # 12 jobs, 2 QA
    greenhouse_board("gh-xapo", "xapo61", "Xapo Bank"),                      # 3 jobs
    # --- Tier B ---
    greenhouse_board("gh-backblaze", "backblaze", "Backblaze"),
    greenhouse_board("gh-upgrade", "upgrade", "Upgrade"),                    # 34 jobs, 5 QA
    greenhouse_board("gh-praxent", "praxent", "Praxent"),
    greenhouse_board("gh-nimblegravity", "nimblegravity", "Nimble Gravity"),
    greenhouse_board("gh-nortal", "nortal", "Nortal"),                       # 35 jobs, 2 QA
    greenhouse_board("gh-vonage", "vonage", "Vonage"),
    greenhouse_board("gh-keepersecurity", "keepersecurity", "Keeper Security"),  # 77 jobs, 5 QA
    greenhouse_board("gh-logicgate", "logicgate", "LogicGate"),
    greenhouse_board("gh-techholding", "techholding", "TechHolding"),
    greenhouse_board("gh-quanata", "quanata", "Quanata"),
    greenhouse_board("gh-perfectserve", "perfectserve", "PerfectServe"),
    greenhouse_board("gh-verantos", "verantos", "Verantos"),
    greenhouse_board("gh-flex", "flex", "Flex"),                             # 39 jobs, 2 QA
    greenhouse_board("gh-canopycare", "canopycare", "Canopy Care"),
    greenhouse_board("gh-gitlab", "gitlab", "GitLab"),                       # 207 jobs
    greenhouse_board("gh-onevest", "onevest", "OneVest"),
]

# Probed 2026-09-24 and NOT added:
#   clickhouse, justmarkets_tech, nearsure, mediacurrent -> HTTP 404 on
#     boards-api.greenhouse.io AND on the EU host (job-boards.eu.greenhouse.io).
#   builder -> HTTP 200 with an EMPTY jobs array (Deel precedent).
#   remotepeople worked on the STANDARD host; the EU fallback was not needed.
# Excluded by instruction: alphafmcroles (contract-only), redcellpartners / defcon
#   (clearance-gated, can never pass the region gate), onetrust (Staff-level title).
