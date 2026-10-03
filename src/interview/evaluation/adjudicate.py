"""Final claim status. A negation in front of the claim collapses it."""

from __future__ import annotations

from interview.evaluation.detectors import words
from interview.evaluation.schema import ClaimJudgement, EvalClaim, Turn

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


def _keys(text: str) -> list[str]:
    return [token for token in words(text) if len(token) > 3]


def _negated_before(text: str, keys: list[str]) -> bool:
    folded = text.casefold()
    positions = [folded.find(key) for key in keys if key in folded]
    positions = [pos for pos in positions if pos >= 0]
    if not positions:
        return False
    before = folded[: min(positions)]
    tail = before[-48:]
    return any(phrase in tail for phrase in _NEGATION)


def adjudicate(claims: list[EvalClaim], turns: list[Turn]) -> list[ClaimJudgement]:
    candidate = [turn for turn in turns if turn.speaker == "candidate"]
    judgements: list[ClaimJudgement] = []
    for claim in claims:
        keys = _keys(claim.text)
        chosen: ClaimJudgement | None = None
        for turn in candidate:
            if not keys:
                continue
            matched = sum(1 for key in keys if key in turn.text.casefold())
            if matched < max(1, len(keys) // 4):
                continue
            status = "collapsed" if _negated_before(turn.text, keys) else "held"
            chosen = ClaimJudgement(
                id=claim.id,
                status=status,
                quote=turn.text.strip(),
                turn_id=turn.turn_id,
            )
            if status == "collapsed":
                break
        if chosen is None:
            chosen = ClaimJudgement(id=claim.id, status="untested", quote="", turn_id=None)
        judgements.append(chosen)
    return judgements
