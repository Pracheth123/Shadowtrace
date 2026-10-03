"""Intake records. JSON on disk; nothing here is a live-bus event."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_path: str
    repo_url: str | None = None
    repo_path: str | None = None
    jd_path: str | None = None
    jd_text: str | None = None
    out_dir: str
    max_steps: int = Field(default=8, ge=1, le=32)


class ResumeProject(BaseModel):
    """Same fields skill-sync extracts for a project, without invented values."""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    description: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    link: str = ""
    duration: str = ""


class ResumeCertification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    issuer: str = ""
    year: str = ""
    score: str = ""
    skills_covered: list[str] = Field(default_factory=list)


class ResumeCourse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    institution: str = ""
    grade: str = ""
    skills_covered: list[str] = Field(default_factory=list)
    type: str = "course"


class ResumeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    email: str = ""
    skills: list[str] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    certifications: list[ResumeCertification] = Field(default_factory=list)
    courses: list[ResumeCourse] = Field(default_factory=list)
    truncated: bool = False


class Claim(BaseModel):
    """An interrogable claim. `text` must appear verbatim in its source."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    competency: str
    status: str = "untested"
    source: str
    source_path: str
    quote: str


class ClaimsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[Claim] = Field(default_factory=list, max_length=8)


class FitGap(BaseModel):
    """
    Skill overlap for the live agent, the guard, and the roadmap.
    Not a hiring score and not an input to the scorer.
    """

    model_config = ConfigDict(extra="forbid")

    required: list[str] = Field(default_factory=list)
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    unsure: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    score_pct: int = 0
    summary: str = ""


class RepoSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    path: str = ""
    commit: str | None = None
    files_listed: int = 0
    bytes_read: int = 0
    truncated: bool = False
    fallback: str | None = None
