"""
STT adapter — Stage 2.

Wraps Deepgram Nova-3 streaming WebSocket.
Emits `partial` and `final_transcript` events onto the bus.

The adapter accepts a `keywords: list[str]` boost list at construction time.
In stage 2 this list is empty or a test list; stage 6 will supply the Claims File terms.

Contract: only this file knows about Deepgram. Everything above it sees bus events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from interview.events.bus import EventBus
    from interview.events.schema import Event

from interview.events.schema import (
    FinalTranscript,
    Partial,
    WordTiming,
)

log = logging.getLogger(__name__)


class SttAdapter:
    """
    Streaming STT adapter.

    Usage::

        adapter = SttAdapter(bus, api_key, session_id, keywords=["Kafka", "Pydantic"])
        await adapter.start()
        adapter.push_audio(pcm_bytes, t_audio_in)    # call from VAD/session loop
        await adapter.stop()

    Events emitted onto bus:
        - partial (text, stable_until_ms, revision)
        - final_transcript (text, word_timings)
    """

    def __init__(
        self,
        bus: "EventBus",
        api_key: str,
        session_id: str,
        *,
        keywords: list[str] | None = None,
        get_turn_id: Callable[[], str | None] | None = None,
        producer: str = "stt",
        model: str = "nova-3",
        language: str = "en-US",
    ) -> None:
        self._bus = bus
        self._api_key = api_key
        self._session_id = session_id
        self._keywords = keywords or []
        self._get_turn_id = get_turn_id or (lambda: None)
        self._producer = producer
        self._model = model
        self._language = language

        self._ws = None
        self._running = False
        self._revision_counter: int = 0
        self._last_partial: str = ""
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None

        # on_partial_text is called synchronously so the turn_detector can update
        self.on_partial_text: Callable[[str], None] | None = None

    async def start(self) -> None:
        """Connect to Deepgram and start streaming."""
        try:
            from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions  # type: ignore
        except ImportError:
            log.warning("deepgram-sdk not installed; STT adapter in no-op mode")
            return

        client = DeepgramClient(self._api_key)
        options = LiveOptions(
            model=self._model,
            language=self._language,
            smart_format=True,
            interim_results=True,
            keywords=self._keywords,
        )
        self._dg_connection = client.listen.asyncwebsocket.v("1")
        self._dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
        self._dg_connection.on(LiveTranscriptionEvents.Error, self._on_error)

        self._running = True
        await self._dg_connection.start(options)
        self._send_task = asyncio.create_task(self._send_loop())

    async def stop(self) -> None:
        """Flush and disconnect."""
        self._running = False
        await self._audio_queue.put(None)  # sentinel
        if self._send_task:
            await self._send_task
        if hasattr(self, "_dg_connection"):
            await self._dg_connection.finish()

    def push_audio(self, pcm_bytes: bytes, _t_audio_in: float) -> None:
        """Non-blocking: queue audio for the send loop."""
        if self._running:
            self._audio_queue.put_nowait(pcm_bytes)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _send_loop(self) -> None:
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                break
            if hasattr(self, "_dg_connection"):
                await self._dg_connection.send(chunk)

    async def _on_transcript(self, _client, result, **_kw) -> None:
        try:
            alt = result.channel.alternatives[0]
            text = alt.transcript.strip()
            if not text:
                return
            is_final = result.is_final
            turn_id = self._get_turn_id()
            t_audio_in = result.start  # seconds from stream start

            if is_final:
                word_timings = [
                    WordTiming(
                        word=w.word,
                        start_ms=int(w.start * 1000),
                        end_ms=int(w.end * 1000),
                    )
                    for w in alt.words
                ]
                event = FinalTranscript(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    producer=self._producer,
                    t_audio_in=t_audio_in,
                    text=text,
                    word_timings=word_timings,
                )
                self._revision_counter = 0
                self._last_partial = ""
            else:
                event = Partial(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    producer=self._producer,
                    t_audio_in=t_audio_in,
                    text=text,
                    stable_until_ms=int(result.start * 1000),
                    revision=self._revision_counter,
                )
                self._revision_counter += 1
                self._last_partial = text
                if self.on_partial_text:
                    self.on_partial_text(text)

            await self._bus.emit(event)
        except Exception as exc:  # noqa: BLE001
            log.error("STT transcript handler error: %s", exc)

    async def _on_error(self, _client, error, **_kw) -> None:
        log.error("Deepgram error: %s", error)
