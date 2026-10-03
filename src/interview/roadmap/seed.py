"""Four comparable sessions in one pack, plus one session in another pack."""

from __future__ import annotations

from interview.evaluation.schema import ClaimJudgement, DimensionScore, Finding, Report
from interview.roadmap.store import LongitudinalStore

_DIMS = ("technical", "structure", "delivery", "competency")


def _scores(values: dict[str, float]) -> list[DimensionScore]:
    return [
        DimensionScore(dimension=name, score=values[name], finding_count=1)  # type: ignore[arg-type]
        for name in _DIMS
    ]


def seed_candidate(store: LongitudinalStore, candidate_id: str = "ada") -> None:
    sessions = [
        (
            "2026-09-01T10:00:00Z",
            Report(
                session_id="ada-1",
                claims=[
                    ClaimJudgement(
                        id="c-kafka",
                        status="collapsed",
                        quote="I didn't build the Kafka pipeline.",
                        turn_id="t1",
                    )
                ],
                findings=[
                    Finding(
                        agent="substance",
                        dimension="technical",
                        summary="The Kafka answer was retracted.",
                        quote="I didn't build the Kafka pipeline.",
                        turn_id="t1",
                        polarity="gap",
                    )
                ],
                dimensions=_scores(
                    {"technical": 0.25, "structure": 0.40, "delivery": 0.50, "competency": 0.30}
                ),
                elapsed_s=0.0,
            ),
        ),
        (
            "2026-09-08T10:00:00Z",
            Report(
                session_id="ada-2",
                claims=[
                    ClaimJudgement(
                        id="c-kafka",
                        status="collapsed",
                        quote="I still have not walked through the consumer lag.",
                        turn_id="t2",
                    )
                ],
                findings=[
                    Finding(
                        agent="structure",
                        dimension="structure",
                        summary="The answer is too short to show how the decision was made.",
                        quote="We shipped it.",
                        turn_id="t2",
                        polarity="gap",
                    )
                ],
                dimensions=_scores(
                    {"technical": 0.40, "structure": 0.45, "delivery": 0.55, "competency": 0.40}
                ),
                elapsed_s=0.0,
            ),
        ),
        (
            "2026-09-15T10:00:00Z",
            Report(
                session_id="ada-3",
                claims=[
                    ClaimJudgement(
                        id="c-kafka",
                        status="held",
                        quote="I built the Kafka pipeline and measured consumer lag.",
                        turn_id="t3",
                    )
                ],
                findings=[
                    Finding(
                        agent="delivery",
                        dimension="delivery",
                        summary="This answer hedges more than the first.",
                        quote="I think maybe the lag was fine.",
                        turn_id="t3",
                        polarity="gap",
                    )
                ],
                dimensions=_scores(
                    {"technical": 0.60, "structure": 0.55, "delivery": 0.50, "competency": 0.60}
                ),
                elapsed_s=0.0,
            ),
        ),
        (
            "2026-09-22T10:00:00Z",
            Report(
                session_id="ada-4",
                claims=[
                    ClaimJudgement(
                        id="c-kafka",
                        status="held",
                        quote="I built the Kafka pipeline and we measured the lag before cutover.",
                        turn_id="t4",
                    )
                ],
                findings=[
                    Finding(
                        agent="substance",
                        dimension="technical",
                        summary="The answer states what was built.",
                        quote="I built the Kafka pipeline and we measured the lag before cutover.",
                        turn_id="t4",
                        polarity="support",
                    )
                ],
                dimensions=_scores(
                    {"technical": 0.80, "structure": 0.70, "delivery": 0.65, "competency": 0.75}
                ),
                elapsed_s=0.0,
            ),
        ),
    ]
    for started_at, report in sessions:
        store.record(candidate_id, "behavioral-core", started_at, report)
    store.record(
        candidate_id,
        "public-company",
        "2026-09-22T12:00:00Z",
        Report(
            session_id="ada-public-1",
            claims=[],
            findings=[],
            dimensions=_scores(
                {"technical": 0.10, "structure": 0.10, "delivery": 0.10, "competency": 0.10}
            ),
            elapsed_s=0.0,
        ),
    )
