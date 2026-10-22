"""Turn the agents' raw state into "missions" (one per job) with a stage and progress.

Sources (all read-only):
- SCOUT  (Agent 1): scout_state_file  -> jobs discovered            (stage: discovered)
- CONNECT(Agent 3): state.json["links"]  -> recruiter-email outcomes  (stage: recruiter_found / manual)
- APPLY  (Agent 2): state.json["pending"]-> drafts awaiting approval, sent, rejected

A mission is keyed by job URL when there is one. Drafts that came from an inbound recruiter
email (Agent 2's normal flow, no job URL) are their own missions keyed by draft id.
"""
import hashlib
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

STAGES = ["discovered", "recruiter_found", "application_ready", "sent"]
PROGRESS = {"discovered": 25, "recruiter_found": 50, "application_ready": 75, "sent": 100}


class Mission(BaseModel):
    id: str
    title: Optional[str] = None
    company: Optional[str] = None
    platform: Optional[str] = None
    job_url: Optional[str] = None
    discovered_by: str = "SCOUT"
    discovered_at: Optional[str] = None  # ISO 8601
    stage: str = "discovered"
    progress: int = 25
    recruiter_email_status: str = "pending"  # pending | verified | manual_review | manual_lookup_needed
    recruiter_email: Optional[str] = None
    recruiter_name: Optional[str] = None
    application_status: str = "none"  # none | awaiting_approval | sent | rejected | missing
    draft_id: Optional[str] = None
    draft_url: Optional[str] = None
    notes: Optional[str] = None


class MissionDetail(Mission):
    job_description: Optional[str] = None
    draft_content: Optional[str] = None
    draft_subject: Optional[str] = None
    letter_mode: Optional[str] = None
    cv_files: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    timeline: list[dict] = Field(default_factory=list)


def mission_id(key: str) -> str:
    return "m-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _iso(value: Any) -> Optional[str]:
    """Accept ISO strings, epoch seconds or epoch milliseconds."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    return str(value)


def _platform(url: str | None) -> Optional[str]:
    if not url:
        return None
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for name in ("linkedin", "indeed", "glassdoor", "remotive", "weworkremotely", "himalayas"):
        if name in host:
            return name
    return host or None


def _scout_jobs(scout: Any) -> list[dict]:
    if isinstance(scout, dict):
        scout = scout.get("jobs", [])
    return [j for j in (scout or []) if isinstance(j, dict) and j.get("url")]


def build_missions(scout: Any, apply_state: dict, file_mtime: float = 0.0) -> dict[str, MissionDetail]:
    """Merge the three sources into {mission_id: MissionDetail}."""
    fallback_ts = _iso(file_mtime) if file_mtime else None
    missions: dict[str, MissionDetail] = {}

    # 1) SCOUT discoveries
    for job in _scout_jobs(scout):
        url = job["url"]
        m = MissionDetail(
            id=mission_id(url), job_url=url, title=job.get("title"), company=job.get("company"),
            platform=job.get("platform") or _platform(url), discovered_by="SCOUT",
            discovered_at=_iso(job.get("found_at") or job.get("discovered_at")),
            job_description=job.get("description"),
        )
        missions[m.id] = m

    # 2) CONNECT outcomes (a URL here was discovered by SCOUT even if SCOUT's file is absent)
    for url, rec in (apply_state.get("links") or {}).items():
        m = missions.get(mission_id(url)) or MissionDetail(
            id=mission_id(url), job_url=url, platform=_platform(url), discovered_by="SCOUT", discovered_at=fallback_ts,
        )
        m.title = m.title or rec.get("role")
        m.company = m.company or rec.get("company")
        status = rec.get("status")
        if status == "drafted":
            m.recruiter_email_status, m.recruiter_email = "verified", rec.get("email")
            m.stage = "recruiter_found"
            m.draft_id = rec.get("draft_id")
        elif status == "linkedin_manual_lookup":
            m.recruiter_email_status = "manual_lookup_needed"
            m.notes = "LinkedIn posting: owner must look up the recruiter email manually"
        elif status == "manual_review":
            m.recruiter_email_status = "manual_review"
            m.notes = rec.get("reason")
        missions[m.id] = m

    # 3) APPLY drafts / approvals
    for draft_id, rec in (apply_state.get("pending") or {}).items():
        url = rec.get("job_url")
        if url:
            m = missions.get(mission_id(url)) or MissionDetail(
                id=mission_id(url), job_url=url, platform=rec.get("platform") or _platform(url), discovered_by="SCOUT",
            )
        else:
            # Inbound recruiter email (Agent 2's normal flow): no job URL, company unknown.
            m = MissionDetail(id=mission_id(f"draft:{draft_id}"), discovered_by="APPLY", platform="email")
        m.discovered_at = m.discovered_at or _iso(rec.get("notified_at")) or fallback_ts
        m.title = m.title or rec.get("role")
        m.recruiter_email = rec.get("sender_email") or m.recruiter_email
        m.recruiter_name = m.recruiter_name or _display_name(rec.get("sender"))
        m.recruiter_email_status = "verified" if m.recruiter_email else m.recruiter_email_status
        m.draft_id, m.draft_url = draft_id, rec.get("draft_url")
        m.draft_content, m.draft_subject = rec.get("letter"), rec.get("subject")
        m.letter_mode, m.cv_files, m.signals = rec.get("letter_mode"), rec.get("cv_files", []), rec.get("signals", [])
        status = rec.get("status")
        if status == "sent":
            m.application_status, m.stage = "sent", "sent"
        elif status == "pending":
            m.application_status, m.stage = "awaiting_approval", "application_ready"
        elif status == "rejected":
            m.application_status, m.stage = "rejected", "application_ready"
        else:
            m.application_status, m.stage = status or "none", "application_ready"
        missions[m.id] = m

    for m in missions.values():
        m.progress = PROGRESS[m.stage]
    return missions


def _display_name(sender: str | None) -> Optional[str]:
    if not sender or "<" not in sender:
        return None
    name = sender.split("<", 1)[0].strip().strip('"')
    return name or None
