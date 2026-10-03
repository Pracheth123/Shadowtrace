"""
Deepgram streaming text-to-speech — a real `SpeakPort`.

Protocol (`/v1/speak` WebSocket), as documented:

  - client → `{"type":"Speak","text":"..."}` buffers text for synthesis
  - client → `{"type":"Flush"}` forces synthesis of whatever is buffered
  - client → `{"type":"Clear"}` drops the provider's text and audio buffers
  - client → `{"type":"Close"}` ends the socket
  - server → binary frames of audio in the requested encoding
  - server → `{"type":"Flushed","sequence_id":N}` / `{"type":"Cleared",...}`

Three constraints shape this adapter, and each one is a real documented limit
rather than a guess:

1. **Flush is rate limited to 20 per 60 seconds.** So this does *not* flush per
   phrase. It sends the sentences of one agent line as separate `Speak`
   messages — which lets the provider begin synthesising the first sentence
   immediately — and flushes **once** at the end of the line. A flush budget
   guards the limit, and running out degrades to "wait for the provider's own
   buffering" rather than erroring.

2. **`Clear` does not recall audio already in flight.** It stops the provider
   generating more. Anything already pushed to the browser is still in the
   browser's queue, so barge-in has to stop playback locally *as well*. That
   client half lives in the Waveform/playback code; this adapter's job is the
   provider half plus refusing to emit further chunks for a cancelled
   utterance.

3. **No documented word alignment.** The Speak API returns audio, not word
   timings. So word timestamps here are *estimated* from a speaking rate and
   every chunk is marked `timings_estimated=True`. Truncation on barge-in is
   therefore word-approximate; the event log says so, and the decision document
   says so. This is the one place the previous implementation claimed more
   precision than the provider gives.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from interview.events.schema import TtsChunk, WordTimestamp
from interview.transport.audio import pcm16_duration_ms

if TYPE_CHECKING:
    from interview.events.bus import EventBus

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10.0
FLUSH_WINDOW_S = 60.0
# Typical English TTS pace, used only to place estimated word boundaries.
WORDS_PER_SECOND = 2.8
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class TtsUnavailable(RuntimeError):
    """Synthesis could not be reached. Surfaced, never replaced with silence."""


@dataclass
class TtsStats:
    utterances: int = 0
    chunks: int = 0
    audio_bytes: int = 0
    flushes: int = 0
    clears: int = 0
    dropped_late_chunks: int = 0
    errors: list[str] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    """
    Split a line into sentences for incremental `Speak` messages.

    Sending sentence by sentence is what lets synthesis start on the first
    clause while the rest is still being written, without spending a flush per
    clause.
    """
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def estimate_word_timestamps(text: str, offset_ms: int = 0) -> list[WordTimestamp]:
    """Estimated, evenly spaced word offsets. Not provider-reported."""
    words = text.split()
    if not words:
        return []
    step = int(1000 / WORDS_PER_SECOND)
    return [
        WordTimestamp(word=word, offset_ms=offset_ms + index * step)
        for index, word in enumerate(words)
    ]


class FlushBudget:
    """Sliding-window guard for the documented 20-flush-per-minute limit."""

    def __init__(self, limit: int, window_s: float = FLUSH_WINDOW_S, clock=time.monotonic):
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._stamps: list[float] = []

    def allow(self) -> bool:
        now = self._clock()
        self._stamps = [t for t in self._stamps if now - t < self._window]
        if len(self._stamps) >= self._limit:
            return False
        self._stamps.append(now)
        return True

    @property
    def used(self) -> int:
        now = self._clock()
        return len([t for t in self._stamps if now - t < self._window])


class DeepgramTts:
    """
    Streaming TTS for one session. Satisfies the runtime's `SpeakPort`.

    `synthesise()` returns when the provider has finished *sending* audio for
    the line. That is not the same as the candidate having *heard* it — the
    browser reports that separately via `playback_ack`, and the session uses
    those acks, not this return, to know what was audible.
    """

    def __init__(
        self,
        bus: "EventBus",
        session_id: str,
        settings,
        *,
        producer: str = "deepgram_tts",
        url_override: str | None = None,
        voice_for_persona: dict[str, str] | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._settings = settings
        self._producer = producer
        self._url_override = url_override
        self._voices = dict(voice_for_persona or {})

        self._ws = None
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._stream_start: float | None = None
        self._audio_cursor_ms = 0
        self._chunk_index = 0

        # Utterance currently being synthesised. A chunk whose utterance no
        # longer matches is late audio from an interrupted line and is dropped.
        self._active_utterance: str | None = None
        self._active_turn: str | None = None
        self._active_text: str = ""
        self._cancelled: set[str] = set()
        self._flushed = asyncio.Event()

        self._flush_budget = FlushBudget(settings.deepgram_tts_max_flushes_per_minute)
        self.stats = TtsStats()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def query_params(self, voice: str | None = None) -> list[tuple[str, str]]:
        s = self._settings
        return [
            ("model", voice or s.deepgram_tts_model),
            ("encoding", s.deepgram_tts_encoding),
            ("sample_rate", str(s.deepgram_tts_sample_rate)),
        ]

    def url(self, voice: str | None = None) -> str:
        base = self._url_override or self._settings.deepgram_tts_url
        return f"{base}?{urlencode(self.query_params(voice))}"

    async def start(self, voice: str | None = None) -> None:
        import websockets

        key = self._settings.deepgram_api_key.get_secret_value().strip()
        if not key and not self._url_override:
            raise TtsUnavailable(
                "DEEPGRAM_API_KEY is not set, so speech synthesis cannot start. "
                "Set it in the server environment, or use the text lane."
            )
        headers = [("Authorization", f"Token {key}")] if key else []
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.url(voice), additional_headers=headers),
                timeout=CONNECT_TIMEOUT_S,
            )
        except asyncio.TimeoutError as exc:
            raise TtsUnavailable(
                f"Speech synthesis did not connect within {CONNECT_TIMEOUT_S:.0f}s."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise TtsUnavailable(f"Speech synthesis could not connect: {exc}") from exc

        self._closed = False
        self._stream_start = time.monotonic()
        self._recv_task = asyncio.create_task(self._receive_loop())

    # ------------------------------------------------------------------
    # SpeakPort
    # ------------------------------------------------------------------

    async def synthesise(
        self,
        text: str,
        utterance_id: str,
        turn_id: str,
        voice: str = "",
    ) -> None:
        if self._ws is None or self._closed:
            raise TtsUnavailable("Speech synthesis is not connected.")
        if not text.strip():
            return

        self._active_utterance = utterance_id
        self._active_turn = turn_id
        self._active_text = text
        self._audio_cursor_ms = 0
        self._chunk_index = 0
        self._flushed.clear()
        self.stats.utterances += 1

        # One Speak per sentence: synthesis of sentence one can begin while the
        # rest is still arriving. One Flush for the whole line, because the
        # provider allows only 20 per minute.
        for sentence in split_sentences(text):
            await self._send_json({"type": "Speak", "text": sentence})

        if self._flush_budget.allow():
            await self._send_json({"type": "Flush"})
            self.stats.flushes += 1
            # Bounded: a provider that never answers must not hang the turn.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._flushed.wait(), timeout=20.0)
        else:
            log.warning(
                "TTS flush budget exhausted (%d in the last minute); relying on "
                "provider buffering for this line",
                self._flush_budget.used,
            )

    async def cancel(self, utterance_id: str | None = None) -> None:
        """
        Barge-in, provider half.

        `Clear` stops the provider generating more audio for the buffered text.
        It does **not** unsend audio already delivered, so the browser must also
        stop and discard its own queue; this marks the utterance cancelled so
        any chunk still arriving for it is dropped rather than forwarded.
        """
        target = utterance_id or self._active_utterance
        if target:
            self._cancelled.add(target)
        if self._active_utterance == target:
            self._active_utterance = None
        self._flushed.set()
        if self._ws is None or self._closed:
            return
        await self._send_json({"type": "Clear"})
        self.stats.clears += 1

    async def close(self) -> None:
        if self._ws is None and self._closed:
            return
        # Send Close *before* flipping the flag: `_send_json` is a no-op once
        # `_closed` is set, so setting it first silently skipped the handshake
        # and left the provider socket to time out instead of closing cleanly.
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._send_json({"type": "Close"})
        self._closed = True
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
        self._recv_task = None
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _send_json(self, payload: dict) -> None:
        if self._ws is None or self._closed:
            return
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            self.stats.errors.append(f"send {payload.get('type')}: {exc}")
            self._closed = True

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        try:
            async for frame in self._ws:
                if isinstance(frame, (bytes, bytearray)):
                    await self._on_audio(bytes(frame))
                    continue
                try:
                    message = json.loads(frame)
                except json.JSONDecodeError:
                    continue
                kind = message.get("type")
                if kind in ("Flushed", "Cleared"):
                    self._flushed.set()
                elif kind in ("Error", "Warning"):
                    detail = message.get("description") or str(message)
                    self.stats.errors.append(f"provider: {detail}")
                    log.error("Deepgram TTS %s: %s", kind, detail)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.stats.errors.append(f"recv: {exc}")

    async def _on_audio(self, audio: bytes) -> None:
        utterance = self._active_utterance
        if utterance is None or utterance in self._cancelled:
            # Late audio for an interrupted (or finished) line.
            self.stats.dropped_late_chunks += 1
            return
        if not audio:
            return

        rate = self._settings.deepgram_tts_sample_rate
        chunk_ms = pcm16_duration_ms(audio, rate)
        t_audio_out = self._audio_cursor_ms / 1000.0

        # The estimated word offsets for this chunk cover the slice of the line
        # that this chunk's duration corresponds to. Approximate by design.
        words = estimate_word_timestamps(self._active_text, self._audio_cursor_ms)

        await self._bus.emit(
            TtsChunk(
                session_id=self._session_id,
                turn_id=self._active_turn,
                producer=self._producer,
                t_audio_out=t_audio_out,
                audio_ref=f"deepgram/{utterance}/chunk{self._chunk_index}.pcm",
                word_timestamps=words if self._chunk_index == 0 else [],
                utterance_id=utterance,
                sample_rate=rate,
                encoding=self._settings.deepgram_tts_encoding,
                timings_estimated=True,
            )
        )
        self._audio_cursor_ms += int(chunk_ms)
        self._chunk_index += 1
        self.stats.chunks += 1
        self.stats.audio_bytes += len(audio)

        # The raw bytes go to the browser out-of-band; the bus carries the
        # event, never the audio (contract: audio_ref, never inline bytes).
        if self.on_audio is not None:
            result = self.on_audio(audio, utterance, rate)
            if inspect.isawaitable(result):
                await result

    # Composition root sets this to the websocket forwarder.
    on_audio = None  # type: ignore[assignment]

    def voice_for(self, persona: str | None) -> str:
        """Persona → distinct Aura voice, falling back to the configured default."""
        if persona and persona in self._voices:
            return self._voices[persona]
        return self._settings.deepgram_tts_model
