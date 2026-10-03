"""Scorer. Findings only — no keyword hits, claim-match counts, or fit fields."""

from __future__ import annotations

from interview.evaluation.schema import DimensionName, DimensionScore, Finding, Lane

_DIMENSIONS: tuple[DimensionName, ...] = ("technical", "structure", "delivery", "competency")

# Dimensions a lane cannot assess. The text lane has no spoken answer, so
# delivery is marked not-assessed instead of being scored from silence —
# scoring it would punish the candidate for choosing the lane (contract 9).
_NOT_ASSESSED: dict[Lane, frozenset[DimensionName]] = {
    "voice": frozenset(),
    "text": frozenset({"delivery"}),
}


def score_findings(findings: list[Finding], lane: Lane = "voice") -> list[DimensionScore]:
    unassessable = _NOT_ASSESSED.get(lane, frozenset())
    scored: list[DimensionScore] = []
    for dimension in _DIMENSIONS:
        if dimension in unassessable:
            scored.append(
                DimensionScore(
                    dimension=dimension,
                    score=0.0,
                    finding_count=0,
                    assessed=False,
                )
            )
            continue
        related = [item for item in findings if item.dimension == dimension]
        if not related:
            value = 0.5
        else:
            supports = sum(1 for item in related if item.polarity == "support")
            value = supports / len(related)
        scored.append(
            DimensionScore(
                dimension=dimension,
                score=round(value, 4),
                finding_count=len(related),
            )
        )
    return scored
