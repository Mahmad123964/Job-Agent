from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

ApplicationStatus = Literal["not_applied", "applying", "applied"]


class JobOut(BaseModel):
    """API shape of a Job row.

    Tolerant of NULLs: columns added by the additive migration (JSON ones have
    no DEFAULT) or rows written by older code can hold NULL where the model
    expects bool/list/dict/str. A single such row used to make /api/jobs and
    /api/notifications 500 — and the dashboard's silent auto-refresh with it —
    so NULLs are coerced to the field defaults instead of failing validation.
    """

    @model_validator(mode="before")
    @classmethod
    def _null_to_default(cls, data):
        obj = data if isinstance(data, dict) else None
        get = (lambda k: obj.get(k)) if obj is not None else (lambda k: getattr(data, k, None))
        fixed = {}
        for name, field in cls.model_fields.items():
            if get(name) is not None:
                continue
            if field.is_required():
                if field.annotation is str:
                    fixed[name] = ""          # NULL text column -> empty string
            elif field.default is not None:
                fixed[name] = field.default_factory() if field.default_factory else field.default
        if not fixed:
            return data
        if obj is not None:
            return {**obj, **fixed}
        merged = {name: get(name) for name in cls.model_fields}
        merged.update(fixed)
        return merged

    id: int
    source: str
    title: str
    company: str
    url: str
    location: str
    tags: str
    matched_keyword: str
    posted_at: Optional[datetime] = None
    discovered_at: datetime
    description: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    is_sqa_relevant: bool = False
    seniority: str = "unknown"
    is_remote: bool = False
    is_worldwide: bool = False
    pakistan_eligible: bool = False
    eligible_region: str = "Unclear"
    eligible_country: Optional[str] = None
    required_skills: List[str] = []
    score: int = 0
    score_breakdown: dict = {}
    notified: bool = False
    notified_at: Optional[datetime] = None
    notified_channels: Optional[str] = None
    application_status: ApplicationStatus = "not_applied"
    applied_at: Optional[datetime] = None
    follow_up_due: Optional[datetime] = None
    resume_match_score: Optional[int] = None
    age_category: Literal["recent", "older"] = "recent"   # computed from posted_at (Job.age_category)
    archived: bool = False

    class Config:
        from_attributes = True


class JobSummaryOut(JobOut):
    """List shape: identical to JobOut but the description is left out.
    Descriptions are 2-10 KB each and made every list response 300+ KB;
    GET /api/jobs/{id} returns the full JobOut for the detail view."""
    description: str = Field(default="", exclude=True)
    archived: bool = False


class SnoozeIn(BaseModel):
    days: int = 3


class NoteIn(BaseModel):
    text: str


class NoteOut(BaseModel):
    id: int
    job_id: int
    text: str
    created_at: datetime

    class Config:
        from_attributes = True


class ResumeIn(BaseModel):
    text: str


class ResumeOut(BaseModel):
    text: str
    chars: int
    rescored_jobs: int = 0


class BlockedCompanyIn(BaseModel):
    company_name: str


class BackfillEmailIn(BaseModel):
    recipient: str


class BackfillEmailOut(BaseModel):
    recipient: str
    jobs: int
    emails_sent: int
    parts: List[dict] = []


class BlockedCompanyOut(BaseModel):
    id: int
    company_name: str
    created_at: datetime

    class Config:
        from_attributes = True


class JobStatusIn(BaseModel):
    status: ApplicationStatus


class PreferenceIn(BaseModel):
    keywords: Optional[List[str]] = None
    locations: Optional[List[str]] = None
    excluded_sources: Optional[List[str]] = None
    score_threshold: Optional[int] = None
    seniority: Optional[str] = None


class PreferenceOut(BaseModel):
    keywords: List[str]
    locations: List[str]
    excluded_sources: List[str]
    score_threshold: int
    seniority: str
    updated_at: datetime
