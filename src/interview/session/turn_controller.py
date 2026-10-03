"""
Turn controller — stage 7.

Stable, complete partials emit `likely_next` so the agent can draft while the
candidate is still talking. `floor_granted` fires at endpoint. Barge-in is
signalled to the caller, which cancels the in-flight agent step.

Stage 11: in panel mode `persona` names the voice that is about to hold the
floor. The controller does not pick it — it asks the injected `persona` callback,
which is the panel's deterministic floor policy. Outside panel mode the callback
returns None and these events are unchanged.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Awaitable, Callable

from interview.events.schema import (
    DistressSignal,
    Endpoint,
    FloorGranted,
    LikelyNext,
    Partial,
    PauseStats,
    Signals,
)
from interview.session.signals import SignalExtractor, SignalReading

if TYPE_CHECKING:
    from interview.events.bus import EventBus

_WORDS = re.compile(r"\b[\w']+\b")
_TRAILING = frozenset(
    {
        "and",
        "so",
        "because",
        "but",
        "or",
        "that",
        "which",
        "if",
        "then",
        "when",
        "although",
        "however",
    }
)

OnLikely = Callable[[str, str, SignalReading], Awaitable[None]]
OnFloor = Callable[[str], Awaitable[None]]
OnBarge = Callable[[str], Awaitable[None]]
# Returns the persona about to speak, or None outside panel mode.
PersonaFn = Callable[[], "str | None"]


def looks_complete(text: str) -> bool:
    words = _WORDS.findall(text.casefold())
    if len(words) < 8:
        return False
    return words[-1] not in _TRAILING


class TurnController:
    def __init__(
        self,
        bus: "EventBus",
        session_id: str,
        extractor: SignalExtractor,
        *,
        on_likely: OnLikely,
        on_floor: OnFloor | None = None,
        on_barge: OnBarge | None = None,
        persona: PersonaFn | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._extractor = extractor
        self._on_likely = on_likely
        self._on_floor = on_floor
        self._on_barge = on_barge
        self._persona = persona or (lambda: None)
        self._last_text: dict[str, str] = {}
        self._fired: set[str] = set()
        self._floor: set[str] = set()

    def attach(self) -> None:
        self._bus.subscribe("partial", self._on_partial)
        self._bus.subscribe("endpoint", self._on_endpoint)
        self._bus.subscribe("barge_in", self._on_barge_event)

    async def _on_partial(self, event: Partial) -> None:
        turn_id = event.turn_id or ""
        text = event.text.strip()
        previous = self._last_text.get(turn_id, "")
        stable = not previous or text.startswith(previous) or previous.startswith(text)
        self._last_text[turn_id] = text
        complete = looks_complete(text)
        reading = self._extractor.update(text, looks_complete=complete and stable)
        distress = None
        if reading.distress_triggers:
            distress = DistressSignal(
                score=reading.distress_score,
                triggers=list(reading.distress_triggers),
            )
        await self._bus.emit(
            Signals(
                session_id=self._session_id,
                turn_id=turn_id,
                producer="signal_bus",
                claim_hits=list(reading.claim_hits),
                pause_stats=PauseStats(
                    count=reading.pause_count,
                    max_ms=200 if reading.pause_count else 0,
                    total_ms=200 * reading.pause_count,
                ),
                silence_ms=reading.silence_ms,
                distress=distress,
            )
        )
        if not stable or not complete or turn_id in self._fired:
            return
        self._fired.add(turn_id)
        await self._bus.emit(
            LikelyNext(
                session_id=self._session_id,
                turn_id=turn_id,
                producer="turn_controller",
                signal_snapshot=reading.model_dump(),
                persona=self._persona(),
            )
        )
        await self._on_likely(turn_id, text, reading)

    async def _on_endpoint(self, event: Endpoint) -> None:
        turn_id = event.turn_id or ""
        if turn_id in self._floor:
            return
        self._floor.add(turn_id)
        await self._bus.emit(
            FloorGranted(
                session_id=self._session_id,
                turn_id=turn_id,
                producer="turn_controller",
                persona=self._persona(),
            )
        )
        if self._on_floor:
            await self._on_floor(turn_id)

    async def _on_barge_event(self, event) -> None:
        if self._on_barge and event.turn_id:
            await self._on_barge(event.turn_id)
