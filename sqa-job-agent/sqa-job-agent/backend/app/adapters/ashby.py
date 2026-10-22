"""Ashby Posting API — free, no auth, one board per employer.

    GET https://api.ashbyhq.com/posting-api/job-board/<board>?includeCompensation=true

Adding another company board is one line:

    class AcmeAdapter(AshbyBoardAdapter):
        name = "ashby-acme"
        board_name = "acme"

Single-employer boards mean low QA volume by design.
"""
import re
from typing import List, Optional, Tuple

import requests

from .base import BaseAdapter, RawJob, parse_datetime, parse_salary_text

_API = "https://api.ashbyhq.com/posting-api/job-board/{board}"
# "€30K – €45K • Offers Equity" -> the salary part only
_COMP_SPLIT = re.compile(r"\s*[•·|]\s*")


def _compensation(item: dict) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    summary = ((item.get("compensation") or {}).get("compensationTierSummary")) or ""
    if not summary:
        return None, None, None
    return parse_salary_text(_COMP_SPLIT.split(summary)[0])


class AshbyBoardAdapter(BaseAdapter):
    board_name: str = ""
    company_name: str = ""
    access_method = "api (company job board)"
    source_category = "ats_board"
    timeout = 30

    def fetch(self) -> List[RawJob]:
        if not self.board_name:
            return []
        resp = requests.get(
            _API.format(board=self.board_name),
            params={"includeCompensation": "true"},
            headers={"User-Agent": "Mozilla/5.0 (sqa-job-agent)"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        jobs: List[RawJob] = []
        for item in resp.json().get("jobs", []):
            if not item.get("id") or item.get("isListed") is False:
                continue
            url = item.get("jobUrl") or item.get("applyUrl") or ""
            if not url:
                continue
            secondary = [s.get("location") for s in (item.get("secondaryLocations") or []) if s.get("location")]
            location = ", ".join([item.get("location") or ""] + secondary).strip(", ") or "Remote"
            if item.get("isRemote") and "remote" not in location.lower():
                location = f"Remote - {location}"
            salary_min, salary_max, currency = _compensation(item)
            tags = [t for t in (item.get("department"), item.get("team"), item.get("employmentType")) if t]
            jobs.append(
                RawJob(
                    source=self.name,
                    external_id=str(item["id"]),
                    title=item.get("title", ""),
                    company=self.company_name or self.board_name,
                    url=url,
                    location=location,
                    tags=tags,
                    posted_at=parse_datetime(item.get("publishedAt")),
                    description=(item.get("descriptionPlain") or "").strip(),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency=currency,
                )
            )
        return jobs


class OysterHRAdapter(AshbyBoardAdapter):
    name = "oysterhr"
    board_name = "oyster"
    company_name = "Oyster HR"
    attribution = "Source: Oyster HR careers (jobs.ashbyhq.com/oyster)."


def ashby_board(name: str, board: str, company: str) -> AshbyBoardAdapter:
    """One line per company board; every board below verified live (200 + jobs)."""
    return type(
        f"Ashby_{name}",
        (AshbyBoardAdapter,),
        {
            "name": name,
            "board_name": board,
            "company_name": company,
            "attribution": f"Source: {company} careers (jobs.ashbyhq.com/{board}).",
        },
    )()


# Verified live 2026-09-24 (HTTP 200, non-empty jobs array).
ASHBY_BOARDS = [
    ashby_board("ashby-kraken", "kraken.com", "Kraken"),        # literal dot in the slug
    ashby_board("ashby-colonist", "colonist", "Colonist"),      # 7 jobs, 1 QA
    ashby_board("ashby-injective", "injective", "Injective"),
    ashby_board("ashby-truelogic", "truelogic", "Truelogic"),   # 179 jobs, 9 QA
    ashby_board("ashby-bio", "bio", "Bio Protocol"),            # 11 jobs, 1 QA
    ashby_board("ashby-runwayml", "runway-ml", "Runway"),
    ashby_board("ashby-runlayer", "runlayer", "Runlayer"),
    ashby_board("ashby-govworx", "govworx", "GovWorx"),         # 6 jobs, 1 QA
    ashby_board("ashby-mexdigital", "mexdigital", "MexDigital"),
    ashby_board("ashby-savvymoney", "savvymoney", "SavvyMoney"),
    ashby_board("ashby-crackenagi", "crackenagi", "Cracken AGI"),
    ashby_board("ashby-ironclad", "ironcladhq", "Ironclad"),
    ashby_board("ashby-jump", "jump", "Jump"),
    ashby_board("ashby-optro", "optro", "Optro"),
    ashby_board("ashby-absorblms", "absorblms", "Absorb LMS"),  # 35 jobs, 1 QA
    ashby_board("ashby-delosdata", "delos-data", "Delos Data"), # 10 jobs, 2 QA
    ashby_board("ashby-assured", "assured", "Assured"),
    ashby_board("ashby-reedsy", "reedsy", "Reedsy"),            # 17 jobs, 1 QA
    ashby_board("ashby-everai", "everai", "EverAI"),
]

# Probed and NOT added: "empower finance" (space in the slug, %20-encoded) ->
# HTTP 404. Ashby board names are path segments; this company's board is either
# named differently or not public.


class DeelAdapter(AshbyBoardAdapter):
    """Deel's Ashby board answers 200 but has returned an EMPTY jobs array in
    every probe (2026-09-24) — Deel appears to have moved its postings off this
    board. Kept here, unregistered, so it can be re-enabled if jobs reappear."""

    name = "deel"
    board_name = "deel"
    company_name = "Deel"
    attribution = "Source: Deel careers (jobs.ashbyhq.com/deel)."
