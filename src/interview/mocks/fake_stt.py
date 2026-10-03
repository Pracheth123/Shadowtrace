"""
Fake STT — Stage 2 mock.

Replays a transcript JSON fixture as a stream of `partial` events with realistic
revisions, followed by a `final_transcript`. No audio required; no Deepgram API key.

This is what turns transport testable with zero API calls and zero audio hardware.
The turn detector must run correctly against this mock — if it can't, its interface
is wrong (CLAUDE.md contract §5).

Transcript file format  (fixtures/transcripts/*.json):
{
  "utterances": [
    {
      "id": "utt-001",
      "duration_ms": 4200,
      "words": [
        {"word": "I",        "start_ms": 100, "end_ms": 160},
        {"word": "built",    "start_ms": 165, "end_ms": 240},
        ...
      ],
      "revisions": [
        {"at_ms": 300,  "text": "I"},
        {"at_ms": 500,  "text": "I built the"},
        {"at_ms": 700,  "text": "I built the pipeline"}
      ]
    }
  ]
}

revisions: ordered by at_ms.  Text changes simulate in-flight STT corrections.
The last revision becomes the final_transcript text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from interview.events.bus import EventBus

from interview.events.schema import FinalTranscript, Partial, SpeechStart, WordTiming

log = logging.getLogger(__name__)


class FakeStt:
    """
    Fake STT that replays a transcript fixture file.

    Usage::

        stt = FakeStt(bus, "fixtures/transcripts/normal_answer.json",
                      session_id="sess-test")
        await stt.run(speed=1.0)          # 1.0 = real-time, 0.0 = instant

    Events emitted:
        speech_start, partial (×N per utterance), final_transcript

    Callbacks:
        on_partial_text(text): called synchronously with each partial — lets
                               TurnDetector update without knowing about events.
        on_silence(t_audio_in): called after the last word of each utterance —
                                triggers the turn detector's silence clock.
    """

    def __init__(
        self,
        bus: "EventBus",
        transcript_path: Path | str,
        session_id: str,
        *,
        producer: str = "fake_stt",
        on_partial_text: Callable[[str], None] | None = None,
        on_silence: Callable[[float], None] | None = None,
    ) -> None:
        self._bus = bus
        self._path = Path(transcript_path)
        self._session_id = session_id
        self._producer = producer
        self.on_partial_text = on_partial_text
        self.on_silence = on_silence

    async def run(self, speed: float = 1.0) -> None:
        """
        Replay all utterances in the transcript file.
        speed=1.0 → real-time, speed=0.0 → instant (for tests).
        """
        data = json.loads(self._path.read_text(encoding="utf-8"))
        utterances = data.get("utterances", [])
        t_audio_in = 0.0  # running audio stream position in seconds

        for utt in utterances:
            t_audio_in = await self._replay_utterance(utt, t_audio_in, speed)

    async def _replay_utterance(
        self, utt: dict, t_offset: float, speed: float
    ) -> float:
        """Replay one utterance. Returns updated t_audio_in after utterance."""
        turn_id = str(uuid.uuid4())
        duration_ms: int = utt.get("duration_ms", 1000)
        words: list[dict] = utt.get("words", [])
        revisions: list[dict] = utt.get("revisions", [])

        # Emit speech_start
        await self._bus.emit(
            SpeechStart(
                session_id=self._session_id,
                turn_id=turn_id,
                producer=self._producer,
                t_audio_in=t_offset,
            )
        )

        prev_at_ms = 0

        # Emit partials with realistic timing
        for i, rev in enumerate(revisions):
            at_ms: int = rev["at_ms"]
            text: str = rev["text"]
            wait_ms = at_ms - prev_at_ms
            if speed > 0 and wait_ms > 0:
                await asyncio.sleep((wait_ms / 1000.0) / speed)
            prev_at_ms = at_ms

            t_now = t_offset + at_ms / 1000.0

            partial = Partial(
                session_id=self._session_id,
                turn_id=turn_id,
                producer=self._producer,
                t_audio_in=t_now,
                text=text,
                stable_until_ms=at_ms,
                revision=i,
            )
            await self._bus.emit(partial)
            if self.on_partial_text:
                self.on_partial_text(text)

        # Wait until utterance is "done" (last word end)
        end_ms = max((w["end_ms"] for w in words), default=duration_ms)
        remaining_ms = end_ms - prev_at_ms
        if speed > 0 and remaining_ms > 0:
            await asyncio.sleep((remaining_ms / 1000.0) / speed)

        t_end = t_offset + end_ms / 1000.0

        # Emit final_transcript
        final_text = revisions[-1]["text"] if revisions else ""
        word_timings = [
            WordTiming(word=w["word"], start_ms=w["start_ms"], end_ms=w["end_ms"])
            for w in words
        ]
        await self._bus.emit(
            FinalTranscript(
                session_id=self._session_id,
                turn_id=turn_id,
                producer=self._producer,
                t_audio_in=t_end,
                text=final_text,
                word_timings=word_timings,
            )
        )

        # Yield to the event loop so all bus subscriber tasks (including speech_start
        # and partial handlers) have a chance to run before we signal silence.
        # Without this yield, on_silence fires before the turn_detector has seen
        # speech_start and cannot schedule its endpoint timer.
        await asyncio.sleep(0)

        # Signal silence so the turn detector starts its clock
        if self.on_silence:
            self.on_silence(t_end)

        # Inter-utterance gap (300 ms default)
        gap_ms = utt.get("gap_after_ms", 300)
        if speed > 0:
            await asyncio.sleep((gap_ms / 1000.0) / speed)

        return t_end + gap_ms / 1000.0
