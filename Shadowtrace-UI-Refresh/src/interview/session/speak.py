"""
SpeakPort adapters — stage 4 composition helpers, stage 11 lanes and voices.

These wrap mocks / TTS without letting session.runtime import transport.
server.py (the composition root) may import transport and pass a real port.

`synthesise` takes an optional `voice`: panel mode passes the persona's voice
so one voice speaks at a time and the log says which. A port that does not care
about voices ignores it.
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

    async def synthesise(
        self,
        text: str,
        utterance_id: str,
        turn_id: str,
        voice: str = "",
    ) -> None:
        tts = FakeTts(
            self._bus, self._session_id, turn_id, utterance_id, voice=voice
        )
        await tts.synthesise(text)


class TextLaneSpeakPort:
    """
    SpeakPort for the text lane — stage 11.

    The text lane runs the same bus, the same agent and the same guard; it just
    has no audio. So this port emits nothing: no `tts_chunk`, which means no
    `playback_ack` and no truncation to record, because nothing was heard. The
    agent line still reaches the candidate as a caption via `on_wire`, and the
    transcript is written by the runtime exactly as in the voice lane.

    Delivery is therefore not assessable from a text-lane session, and the
    evaluation pass marks that dimension "not assessed" rather than scoring
    silence as a weakness (contract 9).
    """

    def __init__(self, bus: "EventBus", session_id: str) -> None:
        self._bus = bus
        self._session_id = session_id
        self.spoken: list[str] = []

    async def synthesise(
        self,
        text: str,
        utterance_id: str,
        turn_id: str,
        voice: str = "",
    ) -> None:
        self.spoken.append(text)
