"""Pattern detectors. No model calls."""

from __future__ import annotations

import re

from interview.evaluation.schema import Observation, Turn

_WORDS = re.compile(r"[a-z0-9']+")
_HEDGE = ("i think", "i guess", "sort of", "kind of", "not sure", "maybe")
_NEGATION = (
    "didn't",
    "did not",
    "didnt",
    "never",
    "i don't",
    "i dont",
    "i was wrong",
    "not mine",
    "wasn't",
    "was not",
)


def words(text: str) -> list[str]:
    return _WORDS.findall(text.casefold())


def hedge_ratio(text: str) -> float:
    tokens = words(text)
    if not tokens:
        return 0.0
    folded = text.casefold()
    hits = sum(1 for phrase in _HEDGE if phrase in folded)
    return hits / len(tokens)


def detect(turns: list[Turn]) -> list[Observation]:
    found: list[Observation] = []
    for turn in turns:
        if turn.speaker != "candidate":
            continue
        tokens = words(turn.text)
        if 0 < len(tokens) < 6:
            found.append(
                Observation(
                    name="short_answer",
                    turn_id=turn.turn_id,
                    detail=f"{len(tokens)} words",
                )
            )
        if hedge_ratio(turn.text) >= 0.15 and len(tokens) >= 8:
            found.append(
                Observation(name="hedge_cluster", turn_id=turn.turn_id, detail="hedges in this answer")
            )
        folded = turn.text.casefold()
        if any(phrase in folded for phrase in _NEGATION):
            found.append(
                Observation(name="negation", turn_id=turn.turn_id, detail="negation phrase in the answer")
            )
    return found
