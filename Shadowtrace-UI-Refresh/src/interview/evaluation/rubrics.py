"""
Rubric-based assessment, replacing the support-count ratio.

The old scorer computed `supports / total_findings` per dimension and returned
0.5 when there were no findings at all. Three problems with that, all of which
this module exists to fix:

  1. **0.5 for no evidence** is a claim about the candidate drawn from the
     absence of data. "We did not establish this" and "this was middling" are
     different results and must not share a number.
  2. **Counting findings** rewards verbosity. More answers meant more findings
     meant a more confident score, regardless of what was in them.
  3. **One dimension set for every interview.** A sales candidate received a
     "technical substance" score because the shape was fixed.

Instead: each round declares its own versioned rubric in its pack, each
dimension is assessed at an explicit *level* with cited evidence, and an
unassessed dimension is excluded from the aggregate with the weights
renormalised over what remains.

These are coaching indicators, not hiring predictions. `AGGREGATE_DISCLAIMER`
travels with every aggregate so a number cannot be lifted out of context, and
nothing here has been validated against human review.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from interview.packs.model import Pack, PackRubric

AGGREGATE_DISCLAIMER = (
    "Experimental coaching indicator. Not validated against human review, and "
    "not a hiring prediction."
)


class Level(str, Enum):
    """
    Assessment levels, with `INSUFFICIENT_EVIDENCE` as a first-class outcome.

    It carries no score and is excluded from the aggregate, because a dimension
    the interview never reached should not drag a candidate down or prop them
    up.
    """

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    DEVELOPING = "developing"
    SOLID = "solid"
    STRONG = "strong"

    @property
    def score(self) -> float | None:
        return {
            Level.INSUFFICIENT_EVIDENCE: None,
            Level.DEVELOPING: 0.35,
            Level.SOLID: 0.65,
            Level.STRONG: 0.9,
        }[self]

    @property
    def is_assessed(self) -> bool:
        return self is not Level.INSUFFICIENT_EVIDENCE


class EvidenceCitation(BaseModel):
    """
    A quote with the turn it came from.

    `verified` records whether the quote was actually found in that turn. An
    unverified citation is kept but marked, never silently dropped — a finding
    whose evidence does not check out is itself a finding about the evaluator.
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    quote: str
    verified: bool = False


class DimensionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_id: str
    label: str
    kind: Literal["reasoning", "knowledge", "evidence", "communication"]
    level: Level
    # One line naming what the level rests on. Not the evaluator's reasoning
    # process.
    rationale: str = ""
    citations: list[EvidenceCitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _evidence_required_for_a_judgement(self) -> "DimensionAssessment":
        """
        A scored dimension must cite something.

        Without this the module would permit exactly what it replaced: a number
        with nothing behind it. An unevidenced judgement is downgraded to
        insufficient evidence rather than accepted.
        """
        if self.level.is_assessed and not self.citations:
            raise ValueError(
                f"dimension {self.dimension_id!r} is scored {self.level.value} "
                "but cites no transcript evidence; use INSUFFICIENT_EVIDENCE "
                "when the interview did not establish it"
            )
        return self

    @property
    def score(self) -> float | None:
        return self.level.score

    @property
    def verified_citations(self) -> list[EvidenceCitation]:
        return [citation for citation in self.citations if citation.verified]


class RoundScore(BaseModel):
    """One round's assessment against its own versioned rubric."""

    model_config = ConfigDict(extra="forbid")

    round: str
    pack_id: str
    rubric_version: str
    dimensions: list[DimensionAssessment] = Field(default_factory=list)
    # Set when the round ended before covering enough of its spine to support
    # conclusions; the report says so beside the score.
    minimum_coverage_met: bool = True

    @property
    def assessed(self) -> list[DimensionAssessment]:
        return [item for item in self.dimensions if item.level.is_assessed]

    @property
    def unassessed(self) -> list[DimensionAssessment]:
        return [item for item in self.dimensions if not item.level.is_assessed]


class RoundAggregate(BaseModel):
    """
    A round's weighted score, with the weight arithmetic made explicit.

    `weights_used` is reported because renormalising over assessed dimensions
    changes what the number means. Showing a 0.70 without saying it came from
    three of five dimensions re-weighted to sum to 1 would overstate it.
    """

    model_config = ConfigDict(extra="forbid")

    round: str
    rubric_version: str
    score: float | None
    assessed_dimensions: list[str] = Field(default_factory=list)
    excluded_dimensions: list[str] = Field(default_factory=list)
    weights_used: dict[str, float] = Field(default_factory=dict)
    note: str = ""
    disclaimer: str = AGGREGATE_DISCLAIMER


def rubric_for(pack: Pack) -> PackRubric | None:
    return pack.rubric


def blank_round_score(pack: Pack, round_: str) -> RoundScore:
    """
    A round score with every dimension at insufficient evidence.

    The honest starting point: before anything is assessed, nothing is
    established. Evaluators raise levels from here.
    """
    rubric = pack.rubric
    if rubric is None:
        return RoundScore(
            round=round_,
            pack_id=pack.pack_id,
            rubric_version="legacy.v0",
            dimensions=[],
        )
    return RoundScore(
        round=round_,
        pack_id=pack.pack_id,
        rubric_version=rubric.version,
        dimensions=[
            DimensionAssessment(
                dimension_id=dimension.id,
                label=dimension.label,
                kind=dimension.kind,
                level=Level.INSUFFICIENT_EVIDENCE,
                rationale="The interview did not establish this.",
            )
            for dimension in rubric.dimensions
        ],
    )


def aggregate_round(score: RoundScore, pack: Pack) -> RoundAggregate:
    """
    Weighted score over the dimensions actually assessed.

    Weights come from the pack rubric and are renormalised over the assessed
    subset. When nothing was assessed the score is None — not zero, which would
    read as the worst possible performance.
    """
    rubric = pack.rubric
    if rubric is None:
        return RoundAggregate(
            round=score.round,
            rubric_version=score.rubric_version,
            score=None,
            note=(
                "This round used the legacy four-dimension weights and has no "
                "round rubric, so no weighted score is produced."
            ),
        )

    weights = {dimension.id: dimension.weight for dimension in rubric.dimensions}
    assessed = score.assessed
    excluded = [item.dimension_id for item in score.unassessed]

    if not assessed:
        return RoundAggregate(
            round=score.round,
            rubric_version=rubric.version,
            score=None,
            excluded_dimensions=excluded,
            note=(
                "Nothing in this round was established well enough to score. "
                "That is a gap in the interview, not a low result."
            ),
        )

    total_weight = sum(weights.get(item.dimension_id, 0.0) for item in assessed)
    if total_weight <= 0:
        return RoundAggregate(
            round=score.round,
            rubric_version=rubric.version,
            score=None,
            excluded_dimensions=excluded,
            note="Assessed dimensions carry no weight in this rubric.",
        )

    normalised = {
        item.dimension_id: weights.get(item.dimension_id, 0.0) / total_weight
        for item in assessed
    }
    value = sum(
        (item.score or 0.0) * normalised[item.dimension_id] for item in assessed
    )

    note_parts = [
        f"Weighted over the {len(assessed)} of {len(rubric.dimensions)} "
        "dimensions this interview established, renormalised to sum to 1."
    ]
    if excluded:
        note_parts.append(
            "Excluded as not established: " + ", ".join(excluded) + "."
        )
    if not score.minimum_coverage_met:
        note_parts.append(
            "This round ended before covering enough of its questions, so read "
            "the score as provisional."
        )

    return RoundAggregate(
        round=score.round,
        rubric_version=rubric.version,
        score=round(value, 4),
        assessed_dimensions=[item.dimension_id for item in assessed],
        excluded_dimensions=excluded,
        weights_used={key: round(val, 4) for key, val in normalised.items()},
        note=" ".join(note_parts),
    )


def comparable(left: RoundScore, right: RoundScore) -> bool:
    """
    Whether two round scores belong on the same trend line.

    Same round and same rubric version, or they are not one series. Charting a
    hr.v1 score beside a hr.v2 score would invent improvement that is really a
    change of ruler.
    """
    return left.round == right.round and left.rubric_version == right.rubric_version


def verify_citations(
    assessment: DimensionAssessment, turns: dict[str, str]
) -> DimensionAssessment:
    """
    Check each quote against the transcript turn it claims to come from.

    Comparison is on collapsed whitespace and case, because a quote repunctuated
    by a model is still the candidate's sentence. A quote that is not in the
    named turn is marked unverified; if that leaves a scored dimension with no
    verified evidence, it is downgraded to insufficient evidence.
    """

    def normalise(text: str) -> str:
        return " ".join(text.split()).casefold()

    checked = [
        citation.model_copy(
            update={
                "verified": normalise(citation.quote)
                in normalise(turns.get(citation.turn_id, ""))
                and bool(citation.quote.strip())
            }
        )
        for citation in assessment.citations
    ]
    updated = assessment.model_copy(update={"citations": checked})
    if updated.level.is_assessed and not updated.verified_citations:
        return updated.model_copy(
            update={
                "level": Level.INSUFFICIENT_EVIDENCE,
                "rationale": (
                    "Downgraded: the cited quotes were not found in the "
                    "transcript turns given, so the judgement is unsupported."
                ),
            }
        )
    return updated


__all__ = [
    "AGGREGATE_DISCLAIMER",
    "DimensionAssessment",
    "EvidenceCitation",
    "Level",
    "RoundAggregate",
    "RoundScore",
    "aggregate_round",
    "blank_round_score",
    "comparable",
    "rubric_for",
    "verify_citations",
]
