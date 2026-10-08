"""Canned evaluation report. No tools, no transcript, no model."""

from __future__ import annotations

from interview.evaluation.schema import ClaimJudgement, DimensionScore, Finding, Report


def fake_report(session_id: str = "fake-eval") -> Report:
    findings = [
        Finding(
            agent="substance",
            dimension="technical",
            summary="Canned finding.",
            quote="I built the pipeline and measured the lag.",
            turn_id="turn-canned",
            polarity="support",
        )
    ]
    return Report(
        session_id=session_id,
        claims=[
            ClaimJudgement(
                id="c-canned",
                status="held",
                quote="I built the pipeline and measured the lag.",
                turn_id="turn-canned",
            )
        ],
        findings=findings,
        dimensions=[
            DimensionScore(dimension="technical", score=1.0, finding_count=1),
            DimensionScore(dimension="structure", score=0.5, finding_count=0),
            DimensionScore(dimension="delivery", score=0.5, finding_count=0),
            DimensionScore(dimension="competency", score=0.5, finding_count=0),
        ],
        elapsed_s=0.0,
    )
