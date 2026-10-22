from typing import Literal, Optional

from pydantic import BaseModel, Field


class ScoreResult(BaseModel):
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    role_title: Optional[str] = None
    positive_signals: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    reason: str = ""


class JDResult(BaseModel):
    usable: bool
    role_title: Optional[str] = None
    company: Optional[str] = None
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    summary: str = ""


class CVProfile(BaseModel):
    name: str
    current_role: str
    core_skill_areas: list[str] = Field(default_factory=list)
    key_skills: list[str] = Field(default_factory=list)
    experience_summary: str = ""


class CoverLetterResult(BaseModel):
    confident: bool
    letter: str = ""


class JobPageResult(BaseModel):
    """What the link resolver reads from a single job posting page."""
    is_job_posting: bool
    role_title: Optional[str] = None
    company: Optional[str] = None
    description: str = ""
    poster_name: Optional[str] = None


class ContactEmailResult(BaseModel):
    email: Optional[str] = None
    reason: str = ""
