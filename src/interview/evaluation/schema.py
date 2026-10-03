"""Evaluation records. These are files on disk, not live-bus events."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClaimStatus = Literal["held", "collapsed", "untested"]
DimensionName = Literal["technical", "structure", "delivery", "competency"]
Polarity = Literal["support", "gap"]


class EvalClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    competency: str


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    speaker: Literal["candidate", "agent"]
    text: str


class Observation(BaseModel):
    """A pattern detector hit. Not a score."""

    model_config = ConfigDict(extra="forbid")

    name: str
    turn_id: str
    detail: str


class ClaimJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: ClaimStatus
    quote: str
    turn_id: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["substance", "structure", "delivery"]
    dimension: DimensionName
    summary: str
    quote: str
    turn_id: str
    polarity: Polarity


class DimensionScore(BaseModel):
    """Share of support findings in one dimension. Findings are the only input."""

    model_config = ConfigDict(extra="forbid")

    dimension: DimensionName
    score: float = Field(ge=0.0, le=1.0)
    finding_count: int


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    claims: list[ClaimJudgement]
    findings: list[Finding]
    dimensions: list[DimensionScore]
    elapsed_s: float
    transcript_pdf: str = "transcript.pdf"
    # Neutral record of hardness. Dimension scores do not read this field.
    intensity_note: str = ""
