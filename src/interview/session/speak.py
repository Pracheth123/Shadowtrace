"""
SpeakPort adapters — Stage 4 composition helpers.

These wrap mocks / TTS without letting session.runtime import transport.
server.py (composition root) may import transport and pass BusSpeak.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from interview.mocks.fake_tts import FakeTts

if TYPE_CHECKING:
    from interview.events.bus import EventBus


class FakeSpeakPort:
    """SpeakPort backed by FakeTts (silence + word timestamps on the bus)."""

    def __init__(self, bus: "EventBus", session_id: str) -> None:
        self._bus = bus
        self._session_id = session_id

    async def synthesise(self, text: str, utterance_id: str, turn_id: str) -> None:
        tts = FakeTts(self._bus, self._session_id, turn_id, utterance_id)
        await tts.synthesise(text)
