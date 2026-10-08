"""
Signal bus — stage 7.

Claim hits, hedging against the candidate's own first-answer baseline, and
distress. This feeds the live agent and the guard only. A scorer payload is
the transcript text and nothing derived from these keywords (contract 8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

_WORDS = re.compile(r"\b[\w']+\b")
_FILLERS = frozenset({"um", "uh", "hmm", "er", "ah"})
_HEDGE_PHRASES = (
    "i think",
    "i guess",
    "sort of",
    "kind of",
    "not sure",
    "maybe",
)


class SignalReading(BaseModel):
    """Typed snapshot carried on `likely_next.signal_snapshot`."""

    model_config = ConfigDict(extra="forbid")

    claim_hits: list[str]
    hedge_ratio: float
    baseline_hedge: float | None
    pause_count: int
    silence_ms: int
    distress_score: float
    distress_triggers: list[str]
    looks_complete: bool


@dataclass
class ClaimHint:
    id: str
    text: str


def scorer_transcript(text: str) -> dict[str, str]:
    """The only view a scorer may take of a turn. No claim hits, no signal scores."""
    return {"text": text}


def _tokens(text: str) -> list[str]:
    return _WORDS.findall(text.casefold())


def _hedge_ratio(text: str, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    folded = text.casefold()
    hits = sum(1 for phrase in _HEDGE_PHRASES if phrase in folded)
    hits += sum(1 for token in tokens if token in _FILLERS)
    return hits / len(tokens)


def _claim_hits(text: str, claims: list[ClaimHint]) -> list[str]:
    folded = text.casefold()
    hits: list[str] = []
    for claim in claims:
        keys = [w for w in _tokens(claim.text) if len(w) > 3]
        if not keys:
            continue
        matched = sum(1 for key in keys if key in folded)
        if matched >= 1 and matched >= max(1, len(keys) // 4):
            hits.append(claim.id)
    return hits


@dataclass
class SignalExtractor:
    """One extractor per session so the hedge baseline is the candidate's own."""

    claims: list[ClaimHint] = field(default_factory=list)
    _baseline: float | None = field(default=None, init=False, repr=False)

    def update(self, text: str, *, silence_ms: int = 0, looks_complete: bool = False) -> SignalReading:
        tokens = _tokens(text)
        ratio = _hedge_ratio(text, tokens)
        if self._baseline is None and len(tokens) >= 8:
            self._baseline = ratio
        fillers = sum(1 for token in tokens if token in _FILLERS)
        triggers: list[str] = []
        score = 0.0
        if silence_ms >= 2000:
            triggers.append("long_silence")
            score += 0.5
        if fillers >= 3:
            triggers.append("repeated_filler")
            score += 0.3
        baseline = self._baseline
        if (
            baseline is not None
            and len(tokens) >= 8
            and ratio > max(0.2, baseline * 2)
            and ratio > baseline
        ):
            triggers.append("hedge_spike")
            score += 0.4
        return SignalReading(
            claim_hits=_claim_hits(text, self.claims),
            hedge_ratio=round(ratio, 4),
            baseline_hedge=None if baseline is None else round(baseline, 4),
            pause_count=fillers,
            silence_ms=silence_ms,
            distress_score=round(min(1.0, score), 4),
            distress_triggers=triggers,
            looks_complete=looks_complete,
        )
