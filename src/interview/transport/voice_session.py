"""
The per-session bridge between the browser, Deepgram, and the interview runtime.

This is the piece that was missing. The adapters existed and were tested, but
nothing connected them: the server injected `FakeSpeakPort`, answered every
`tts_chunk` with a burst of silence, and counted incoming microphone frames as
"unsolicited binary" before discarding them. So a voice session played nothing
and heard nothing while still completing successfully.

`VoiceSession` owns both provider connections for one interview and is itself
the runtime's `SpeakPort`, so the runtime speaks through the same object that is
listening. Three things it is responsible for:

1. **Audio in.** Browser PCM16 frames arrive at whatever rate the device runs.
   `PcmStreamConverter` converts to the rate declared on the Deepgram socket,
   holding phase and partial frames across chunk seams. Mute and push-to-talk
   drop frames *here*, so they control what actually leaves the machine rather
   than only affecting interruption handling.

2. **Audio out.** Each synthesised chunk is sent to the browser as a JSON
   metadata frame immediately followed by its binary frame. The metadata carries
   `utterance_id`, `encoding`, `sample_rate`, `seq` and `bytes`, because a
   browser cannot decode PCM without the rate and the previous client hard-coded
   16 kHz while Deepgram produces 24 kHz. Ordering is safe: a WebSocket
   delivers frames in send order, so the pairing is unambiguous.

3. **Barge-in.** Both halves. `Clear` stops the provider generating, and a
   `stop_playback` control frame tells the browser to drop what it has already
   buffered — provider-side cancellation alone cannot reach audio that has left
   the server. Chunks still arriving for a cancelled utterance are dropped
   rather than forwarded.

Backpressure is explicit: audio is forwarded through a bounded queue, and when
the provider cannot keep up the oldest frames are dropped and counted. Dropping
old audio is the right failure for live speech — buffering it unboundedly would
trade a transcription gap for ever-growing latency.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from interview.transport.audio import PcmStreamConverter, pcm16_duration_ms
from interview.transport.deepgram_stt import DeepgramStt, SttUnavailable
from interview.transport.deepgram_tts import DeepgramTts, TtsUnavailable

log = logging.getLogger(__name__)

# ~2 seconds of 16 kHz mono PCM16 in flight. Beyond this the provider is not
# keeping up and the audio is stale anyway.
AUDIO_QUEUE_FRAMES = 64

SendText = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


class VoiceUnavailable(RuntimeError):
    """
    The voice path could not be established.

    Raised so the session surfaces an honest unavailable state and can offer the
    text lane, rather than running a silent interview that reports success.
    """


@dataclass
class VoiceStats:
    frames_in: int = 0
    bytes_in: int = 0
    frames_dropped_backpressure: int = 0
    frames_dropped_muted: int = 0
    chunks_out: int = 0
    bytes_out: int = 0
    barge_ins: int = 0
    errors: list[str] = field(default_factory=list)


class VoiceSession:
    """
    One live voice session. Also the runtime's `SpeakPort`.

    Usage (composition root)::

        voice = VoiceSession(bus, session_id, settings, send_text, send_bytes)
        await voice.start(source_sample_rate=48000, channels=1)
        ...
        await voice.push_audio(pcm_frame)      # from the WS binary frames
        await voice.synthesise(text, utt, turn)  # from the runtime
        await voice.barge_in(utterance_id)
        await voice.close()
    """

    def __init__(
        self,
        bus,
        session_id: str,
        settings,
        send_text: SendText,
        send_bytes: SendBytes,
        *,
        keyterms: list[str] | None = None,
        voice_for_persona: dict[str, str] | None = None,
        get_turn_id: Callable[[], str | None] | None = None,
        stt_url_override: str | None = None,
        tts_url_override: str | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._settings = settings
        self._send_text = send_text
        self._send_bytes = send_bytes
        self._get_turn_id = get_turn_id or (lambda: None)

        self.stt = DeepgramStt(
            bus,
            session_id,
            settings,
            keyterms=keyterms or [],
            get_turn_id=self._get_turn_id,
            url_override=stt_url_override,
            on_speech_started=self._on_speech_started,
        )
        self.tts = DeepgramTts(
            bus,
            session_id,
            settings,
            url_override=tts_url_override,
            voice_for_persona=voice_for_persona,
        )
        self.tts.on_audio = self._forward_audio

        self._converter: PcmStreamConverter | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=AUDIO_QUEUE_FRAMES)
        self._pump_task: asyncio.Task | None = None
        self._closed = False
        self._muted = False
        self._seq = 0
        # Utterances the candidate interrupted. A late chunk for one of these is
        # dropped rather than forwarded.
        self._cancelled: set[str] = set()
        self.stats = VoiceStats()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, *, source_sample_rate: int, channels: int = 1) -> None:
        """
        Open both provider connections.

        `source_sample_rate` is what the browser reported for its own
        `AudioContext`, not an assumption. The converter is built from it, and
        the Deepgram socket is told the rate we actually produce.
        """
        if source_sample_rate <= 0:
            raise VoiceUnavailable(
                "The browser did not report a microphone sample rate."
            )
        self._converter = PcmStreamConverter(
            source_rate=source_sample_rate,
            target_rate=self._settings.deepgram_sample_rate,
            channels=channels,
        )
        try:
            await self.stt.start()
        except SttUnavailable as exc:
            raise VoiceUnavailable(str(exc)) from exc
        try:
            await self.tts.start()
        except TtsUnavailable as exc:
            # Leave nothing half-open: a live STT socket with no way to reply is
            # worse than a clean failure.
            with contextlib.suppress(Exception):
                await self.stt.close()
            raise VoiceUnavailable(str(exc)) from exc

        self._pump_task = asyncio.create_task(self._pump())
        await self._send_text(
            {
                "type": "voice_ready",
                "stt_model": self._settings.deepgram_stt_model,
                "stt_sample_rate": self._settings.deepgram_sample_rate,
                "tts_model": self._settings.deepgram_tts_model,
                "tts_sample_rate": self._settings.deepgram_tts_sample_rate,
                "capture": self._converter.describe(),
            }
        )
        log.info(
            "voice session %s open: capture %s", self._session_id,
            self._converter.describe(),
        )

    async def close(self) -> None:
        """Release both sockets, the pump task and the queue."""
        if self._closed:
            return
        self._closed = True
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump_task
        self._pump_task = None
        with contextlib.suppress(Exception):
            await self.stt.close()
        with contextlib.suppress(Exception):
            await self.tts.close()
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()

    # ------------------------------------------------------------------
    # Audio in
    # ------------------------------------------------------------------

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        """
        Mute / push-to-talk release.

        Gates the real outgoing stream. The previous push-to-talk button only
        changed how barge-in was interpreted, so the candidate's audio kept
        being transcribed while the UI said otherwise.
        """
        self._muted = bool(muted)

    async def push_audio(self, pcm: bytes) -> None:
        """Queue one browser PCM16 frame for conversion and forwarding."""
        if self._closed or not pcm:
            return
        if self._muted:
            self.stats.frames_dropped_muted += 1
            return
        self.stats.frames_in += 1
        self.stats.bytes_in += len(pcm)
        try:
            self._queue.put_nowait(pcm)
        except asyncio.QueueFull:
            # Drop the oldest: for live speech, stale audio is worth less than
            # low latency, and unbounded buffering would grow the delay forever.
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            self.stats.frames_dropped_backpressure += 1
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(pcm)
            if self.stats.frames_dropped_backpressure in (1, 25, 100):
                await self._send_text(
                    {
                        "type": "voice_warning",
                        "reason": "backpressure",
                        "detail": (
                            "Audio is arriving faster than it can be "
                            "transcribed; some frames were dropped."
                        ),
                        "frames_dropped": self.stats.frames_dropped_backpressure,
                    }
                )

    async def _pump(self) -> None:
        """Convert and forward queued audio. One task, so order is preserved."""
        assert self._converter is not None
        while not self._closed:
            try:
                frame = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                converted = self._converter.push_pcm16(frame)
                if converted:
                    await self.stt.send_audio(converted)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.stats.errors.append(f"pump: {exc}")
                await self._send_text(
                    {
                        "type": "voice_error",
                        "reason": "transcription_failed",
                        "detail": str(exc)[:200],
                    }
                )

    async def finalise_turn(self, boundary: str = "client") -> None:
        """End the current answer from our side (push-to-talk release, timeout)."""
        await self.stt.finalise_turn(boundary)

    async def _on_speech_started(self) -> None:
        """Provider detected speech. Surface it so the UI can show listening."""
        await self._send_text({"type": "speech_started"})

    # ------------------------------------------------------------------
    # Audio out — the SpeakPort
    # ------------------------------------------------------------------

    async def synthesise(
        self,
        text: str,
        utterance_id: str,
        turn_id: str,
        voice: str = "",
    ) -> None:
        """
        Speak one guard-approved line.

        Only text the guard has already accepted reaches here, so nothing
        unvetted is ever synthesised. Returns when the provider has finished
        *sending*; the browser reports what was actually heard separately, and
        the session uses those acknowledgements rather than this return.
        """
        if self._closed:
            return
        self._cancelled.discard(utterance_id)
        await self._send_text(
            {
                "type": "utterance_begin",
                "utterance_id": utterance_id,
                "turn_id": turn_id,
                "encoding": self._settings.deepgram_tts_encoding,
                "sample_rate": self._settings.deepgram_tts_sample_rate,
                "text": text,
            }
        )
        try:
            await self.tts.synthesise(text, utterance_id, turn_id, voice)
        except TtsUnavailable as exc:
            self.stats.errors.append(f"tts: {exc}")
            await self._send_text(
                {
                    "type": "voice_error",
                    "reason": "synthesis_failed",
                    "detail": str(exc)[:200],
                    "utterance_id": utterance_id,
                }
            )
            return
        await self._send_text(
            {"type": "utterance_end", "utterance_id": utterance_id}
        )

    async def _forward_audio(self, audio: bytes, utterance_id: str, rate: int) -> None:
        """
        Send one synthesised chunk: metadata frame, then the audio frame.

        A chunk belonging to a cancelled utterance is dropped here — the last
        gate before audio the candidate interrupted would reach their speakers.
        """
        if self._closed or not audio:
            return
        if utterance_id in self._cancelled:
            return
        self._seq += 1
        await self._send_text(
            {
                "type": "audio_chunk",
                "utterance_id": utterance_id,
                "encoding": self._settings.deepgram_tts_encoding,
                "sample_rate": rate,
                "seq": self._seq,
                "bytes": len(audio),
                "duration_ms": round(pcm16_duration_ms(audio, rate), 1),
                # Deepgram's Speak API does not document word alignment, so any
                # word timing downstream is estimated. Said once, here, rather
                # than implied by silence.
                "timings_estimated": True,
            }
        )
        await self._send_bytes(audio)
        self.stats.chunks_out += 1
        self.stats.bytes_out += len(audio)

    # ------------------------------------------------------------------
    # Barge-in
    # ------------------------------------------------------------------

    async def barge_in(self, utterance_id: str | None = None) -> None:
        """
        Both halves of an interruption.

        Provider `Clear` stops further generation; `stop_playback` tells the
        browser to drop what it has already buffered. Neither alone is enough:
        `Clear` cannot reach audio that has left the server, and stopping only
        the browser leaves the provider burning quota on speech nobody hears.

        Capture is deliberately untouched — the candidate is mid-sentence and
        that sentence is the next answer.
        """
        self.stats.barge_ins += 1
        target = utterance_id or ""
        if target:
            self._cancelled.add(target)
        with contextlib.suppress(Exception):
            await self.tts.cancel(utterance_id)
        await self._send_text(
            {
                "type": "stop_playback",
                "utterance_id": target,
                "reason": "barge_in",
            }
        )

    def is_cancelled(self, utterance_id: str) -> bool:
        return utterance_id in self._cancelled

    def describe(self) -> dict:
        """Operational summary for the log and for `/healthz`-style reporting."""
        return {
            "capture": self._converter.describe() if self._converter else None,
            "frames_in": self.stats.frames_in,
            "frames_dropped_backpressure": self.stats.frames_dropped_backpressure,
            "frames_dropped_muted": self.stats.frames_dropped_muted,
            "chunks_out": self.stats.chunks_out,
            "bytes_out": self.stats.bytes_out,
            "barge_ins": self.stats.barge_ins,
            "stt": {
                "partials": self.stt.stats.partials,
                "segments_finalised": self.stt.stats.segments_finalised,
                "turns_completed": self.stt.stats.turns_completed,
                "boundaries": dict(self.stt.stats.boundary_counts),
                "keyterms": len(self.stt.keyterms),
            },
            "tts": {
                "utterances": self.tts.stats.utterances,
                "chunks": self.tts.stats.chunks,
                "flushes": self.tts.stats.flushes,
                "clears": self.tts.stats.clears,
                "dropped_late_chunks": self.tts.stats.dropped_late_chunks,
            },
            "errors": list(self.stats.errors)[:5],
        }


__all__ = ["AUDIO_QUEUE_FRAMES", "VoiceSession", "VoiceStats", "VoiceUnavailable"]
