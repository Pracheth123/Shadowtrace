"""Scorer. Findings only — no keyword hits, claim-match counts, or fit fields."""

from __future__ import annotations

from interview.evaluation.schema import DimensionName, DimensionScore, Finding

_DIMENSIONS: tuple[DimensionName, ...] = ("technical", "structure", "delivery", "competency")


def score_findings(findings: list[Finding]) -> list[DimensionScore]:
    scored: list[DimensionScore] = []
    for dimension in _DIMENSIONS:
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
