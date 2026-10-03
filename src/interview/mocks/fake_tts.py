"""
Fake TTS — Stage 3 mock.

Emits silence of the right duration with estimated word timestamps.
No audio hardware, no TTS API key required. Lets the inference path be tested
end-to-end with zero audio I/O.

Duration is estimated at 2.8 words/second (typical English speech rate).
Word timestamps are uniformly spaced within the silence chunk.

Usage::

    tts = FakeTts(bus, session_id, turn_id, utterance_id)
    await tts.synthesise("How did you handle back-pressure?")
    # bus receives a tts_chunk with audio_ref pointing to a fake ref
    # and word_timestamps matching the text
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from interview.events.bus import EventBus

from interview.events.schema import TtsChunk, WordTimestamp

_WORDS_PER_SECOND = 2.8   # typical English TTS rate
_SAMPLE_RATE = 16000       # Hz
_BYTES_PER_MS = 32         # 16 kHz, 16-bit, mono: 16000 * 2 / 1000


class FakeTts:
    """
    Fake TTS that emits silence of the correct duration with word timestamps.

    Identical interface to TtsAdapter.synthesise(). Drop-in replacement for tests.
    """

    def __init__(
        self,
        bus: "EventBus",
        session_id: str,
        turn_id: str,
        utterance_id: str,
        *,
        producer: str = "fake_tts",
        stream_start_s: float | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._turn_id = turn_id
        self._utterance_id = utterance_id
        self._producer = producer
        self._stream_start = stream_start_s or time.monotonic()
        self._chunk_index = 0
        self._audio_out_cursor_ms: int = 0

    async def synthesise(self, text: str) -> None:
        """Emit a tts_chunk with silence audio and estimated word timestamps."""
        words = text.split()
        if not words:
            return

        duration_ms = max(100, int(len(words) * 1000 / _WORDS_PER_SECOND))
        silence = b"\x00" * (duration_ms * _BYTES_PER_MS)

        ms_per_word = duration_ms // max(len(words), 1)
        word_timestamps = [
            WordTimestamp(
                word=w,
                offset_ms=self._audio_out_cursor_ms + i * ms_per_word,
            )
            for i, w in enumerate(words)
        ]

        t_audio_out = time.monotonic() - self._stream_start
        ref = f"fake_tts/{self._utterance_id}/chunk{self._chunk_index}.pcm"

        await self._bus.emit(
            TtsChunk(
                session_id=self._session_id,
                turn_id=self._turn_id,
                producer=self._producer,
                t_audio_out=t_audio_out,
                audio_ref=ref,
                word_timestamps=word_timestamps,
                utterance_id=self._utterance_id,
            )
        )

        self._audio_out_cursor_ms += duration_ms
        self._chunk_index += 1
