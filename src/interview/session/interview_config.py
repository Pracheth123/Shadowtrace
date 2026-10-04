"""
The validated configuration for one interview.

This is what makes the application role-aware. It is built at setup, persisted
with the session, and read by intake, the interviewer, evaluation and the
report — so a sales candidate is never scored against a software rubric because
a default leaked through.

GitHub is optional by construction. The required context is a resume or written
background plus a target role; a repository is one *optional* evidence source
for roles where code exists. Nothing infers ability from its presence, and
nothing infers a deficiency from its absence: a missing evidence source makes a
claim "not established", which is not the same as false.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from interview.session.roles import (
    InterviewRound,
    RoleFamily,
    Seniority,
    pack_id_for,
    rounds_for,
    time_allocation,
)

# Upload types we actually read. One list, shared with intake/documents.py and
# mirrored by the browser, so the two sides cannot disagree about what works.
SUPPORTED_RESUME_SUFFIXES = (".pdf", ".docx", ".txt", ".md")
SUPPORTED_RESUME_LABEL = "PDF, DOCX, plain text or Markdown"

MAX_BACKGROUND_CHARS = 20_000
MAX_JOB_DESCRIPTION_CHARS = 20_000
MAX_COMPANY_CONTEXT_CHARS = 4_000

_GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")


class UnsupportedUpload(ValueError):
    """An upload we cannot read. Carries advice, not just a refusal."""

    def __init__(self, message: str, *, recovery: str) -> None:
        super().__init__(message)
        self.recovery = recovery


def check_resume_upload(filename: str) -> str:
    """
    Accept only types we can actually extract, with a useful message otherwise.

    The name check is the cheap first gate; `intake.documents.extract_upload`
    then checks the content agrees with the name.
    """
    name = (filename or "").strip()
    if not name:
        raise UnsupportedUpload(
            "No file was received.",
            recovery=f"Choose a {SUPPORTED_RESUME_LABEL} file and try again.",
        )
    lowered = name.casefold()
    for suffix in SUPPORTED_RESUME_SUFFIXES:
        if lowered.endswith(suffix):
            return suffix
    if lowered.endswith((".doc", ".pages", ".odt", ".rtf")):
        raise UnsupportedUpload(
            f"{name} is a word-processor format this build does not read.",
            recovery=(
                "Save it as DOCX or PDF (File → Save as / Export) and upload "
                "that, or paste your background as text instead."
            ),
        )
    if lowered.endswith((".png", ".jpg", ".jpeg", ".heic")):
        raise UnsupportedUpload(
            f"{name} is an image, and this build does not run OCR.",
            recovery=(
                "Upload a PDF exported from a word processor, a DOCX, or paste "
                "your background as text."
            ),
        )
    raise UnsupportedUpload(
        f"{name} is not a file type this build can read.",
        recovery=f"Upload a {SUPPORTED_RESUME_LABEL} file, or paste your background as text.",
    )


def validate_repo_url(url: str) -> str:
    """
    Accept an https URL on a known code host, and nothing else.

    Rejects ssh, git and file schemes, and anything with credentials embedded.
    This is an optional evidence source, so refusing an odd URL costs the
    candidate nothing — the interview runs without it.
    """
    candidate = (url or "").strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme != "https":
        raise ValueError(
            f"repository URL must be https (got {parsed.scheme or 'no scheme'!r})"
        )
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ValueError("repository URL must not contain credentials")
    host = parsed.hostname or ""
    if host.casefold() not in _GIT_HOSTS:
        raise ValueError(
            f"{host or 'that host'} is not a supported code host "
            f"({', '.join(_GIT_HOSTS)})"
        )
    if not re.match(r"^/[^/]+/[^/]+/?$", parsed.path):
        raise ValueError("repository URL should look like https://host/owner/name")
    return candidate


class InterviewConfig(BaseModel):
    """
    Everything chosen at setup. Persisted with the session.

    Two deliberate properties:

    - `role_family` can be GENERIC, and that is a real, labelled choice rather
      than a silent fallback to the software pack.
    - `has_background` is required: an interview with no candidate context has
      nothing to interrogate, and would produce a transcript of generic
      questions that still looked like a session.
    """

    model_config = ConfigDict(extra="forbid")

    target_role: str = Field(min_length=1, max_length=120)
    role_family: RoleFamily = RoleFamily.GENERIC
    seniority: Seniority = Seniority.MID
    round: InterviewRound = InterviewRound.FULL
    intensity: str = "realistic"
    lane: str = "voice"

    # Candidate context. One of these must be present.
    background_text: str = Field(default="", max_length=MAX_BACKGROUND_CHARS)
    resume_filename: str = ""

    # Optional context.
    job_description: str = Field(default="", max_length=MAX_JOB_DESCRIPTION_CHARS)
    company_context: str = Field(default="", max_length=MAX_COMPANY_CONTEXT_CHARS)
    # Optional evidence source. Never required, for any round or family.
    repo_url: str = ""
    work_sample_filenames: list[str] = Field(default_factory=list)

    # Whole-interview budget, split across the selected rounds.
    total_seconds: float = Field(default=1800.0, gt=0)

    @model_validator(mode="after")
    def _validate(self) -> "InterviewConfig":
        if self.intensity not in ("coach", "realistic", "panel"):
            raise ValueError(f"unknown intensity {self.intensity!r}")
        if self.lane not in ("voice", "text"):
            raise ValueError(f"unknown lane {self.lane!r}")
        if not self.background_text.strip() and not self.resume_filename.strip():
            raise ValueError(
                "an interview needs the candidate's background: upload a resume "
                "or paste a written background"
            )
        if self.resume_filename:
            check_resume_upload(self.resume_filename)
        if self.repo_url:
            object.__setattr__(self, "repo_url", validate_repo_url(self.repo_url))
        return self

    # ------------------------------------------------------------------
    # Derived
    # ------------------------------------------------------------------

    @property
    def has_background(self) -> bool:
        return bool(self.background_text.strip() or self.resume_filename.strip())

    @property
    def uses_repository(self) -> bool:
        return bool(self.repo_url)

    @property
    def is_generic_coverage(self) -> bool:
        """True when no dedicated specialist pack exists for this profession."""
        return not self.role_family.has_dedicated_pack

    def selected_rounds(self) -> tuple[InterviewRound, ...]:
        return rounds_for(self.round)

    def pack_ids(self) -> dict[InterviewRound, str]:
        return {
            item: pack_id_for(item, self.role_family)
            for item in self.selected_rounds()
        }

    def time_budget(self) -> dict[InterviewRound, float]:
        return time_allocation(self.round, self.total_seconds)

    def coverage_note(self) -> str:
        """
        One honest line for the report about how specific this interview was.

        Says plainly when coverage was generic, rather than letting a generic
        round read as expert assessment of a profession.
        """
        if self.is_generic_coverage:
            return (
                f"This interview used general coverage for '{self.target_role}'. "
                "No specialist pack exists for that profession yet, so the "
                "domain round asked role-agnostic questions and its findings "
                "should be read as general rather than expert."
            )
        return (
            f"This interview used the {self.role_family.label.lower()} specialist "
            f"pack for '{self.target_role}'."
        )

    def evidence_note(self) -> str:
        """What the findings rest on, including what was absent."""
        if self.uses_repository:
            return (
                "Claims were checked against the resume and the supplied "
                "repository. Repository content shows that code exists, not who "
                "wrote it, so ownership was established in conversation."
            )
        return (
            "No repository or work sample was supplied, so every claim rests on "
            "the candidate's own account. Unsupported claims are recorded as "
            "not established — which is not the same as contradicted."
        )


__all__ = [
    "InterviewConfig",
    "MAX_BACKGROUND_CHARS",
    "SUPPORTED_RESUME_LABEL",
    "SUPPORTED_RESUME_SUFFIXES",
    "UnsupportedUpload",
    "check_resume_upload",
    "validate_repo_url",
]
