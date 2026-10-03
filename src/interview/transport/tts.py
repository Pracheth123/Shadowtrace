"""
Streaming TTS adapter — Stage 3.

Emits `tts_chunk` events with t_audio_out, utterance_id, audio_ref, and per-word
word_timestamps. TTS starts on the FIRST SENTENCE (from draft_ready), not the whole
reply — this is what keeps endpoint→first_audio under 450 ms.

Vendor is read from config/inference.yaml (tts.vendor). Only this file knows the vendor.
Everything above it sees bus events.

Supported vendors: elevenlabs, openai-tts, google-tts, mock
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

import yaml

if TYPE_CHECKING:
    from interview.events.bus import EventBus

from interview.events.schema import TtsChunk, WordTimestamp

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "inference.yaml"

# Average speaking rate for word-timestamp estimation when the vendor
# doesn't provide them natively.
_WORDS_PER_SECOND = 2.8


def _load_tts_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("tts", {})
    return {}


def _estimate_word_timestamps(text: str, offset_ms: int = 0) -> list[WordTimestamp]:
    """
    Estimate per-word timestamps at a fixed speaking rate.
    Used when the TTS vendor doesn't return real timestamps.
    """
    words = text.split()
    ms_per_word = int(1000 / _WORDS_PER_SECOND)
    return [
        WordTimestamp(word=w, offset_ms=offset_ms + i * ms_per_word)
        for i, w in enumerate(words)
    ]


class TtsAdapter:
    """
    Streaming TTS adapter. Feed it one sentence at a time; it emits tts_chunk events.

    Usage::

        tts = TtsAdapter(bus, session_id, turn_id, utterance_id)
        await tts.synthesise("How did you handle back-pressure?")
        # bus receives tts_chunk with audio_ref and word_timestamps

    Standalone (no audio input): works with fake_llm — just call synthesise() with
    any text string. No microphone, no audio hardware required.
    """

    def __init__(
        self,
        bus: "EventBus",
        session_id: str,
        turn_id: str,
        utterance_id: str,
        *,
        producer: str = "tts",
        stream_start_s: float | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._turn_id = turn_id
        self._utterance_id = utterance_id
        self._producer = producer
        self._stream_start = stream_start_s or time.monotonic()

        cfg = _load_tts_config()
        self._vendor = cfg.get("vendor", "mock")
        self._voice = cfg.get("voice", "")
        self._api_key = cfg.get("api_key", "")
        self._model = cfg.get("model", "")
        self._chunk_index = 0
        self._audio_out_cursor_ms: int = 0  # running ms of audio emitted so far

    async def synthesise(self, text: str) -> None:
        """Synthesise `text` and emit one or more tts_chunk events."""
        if self._vendor == "elevenlabs":
            await self._elevenlabs(text)
        elif self._vendor == "openai-tts":
            await self._openai_tts(text)
        elif self._vendor == "google-tts":
            await self._google_tts(text)
        else:
            await self._mock_tts(text)

    # ------------------------------------------------------------------
    # ElevenLabs streaming
    # ------------------------------------------------------------------

    async def _elevenlabs(self, text: str) -> None:
        try:
            from elevenlabs import AsyncElevenLabs, VoiceSettings  # type: ignore
        except ImportError:
            log.error("elevenlabs package not installed; falling back to mock")
            await self._mock_tts(text)
            return

        client = AsyncElevenLabs(api_key=self._api_key)
        voice_id = self._voice or "21m00Tcm4TlvDq8ikWAM"  # Rachel

        audio_chunks = []
        async for chunk in await client.generate(
            text=text,
            voice=voice_id,
            model="eleven_turbo_v2",
            stream=True,
        ):
            audio_chunks.append(chunk)

        audio_bytes = b"".join(audio_chunks)
        await self._emit_chunk(audio_bytes, text)

    # ------------------------------------------------------------------
    # OpenAI TTS streaming
    # ------------------------------------------------------------------

    async def _openai_tts(self, text: str) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ImportError:
            log.error("openai package not installed; falling back to mock")
            await self._mock_tts(text)
            return

        client = AsyncOpenAI(api_key=self._api_key)
        audio_bytes = b""
        async with client.audio.speech.with_streaming_response.create(
            model=self._model or "tts-1",
            voice=self._voice or "nova",
            input=text,
            response_format="pcm",
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                audio_bytes += chunk

        await self._emit_chunk(audio_bytes, text)

    # ------------------------------------------------------------------
    # Google Cloud TTS
    # ------------------------------------------------------------------

    async def _google_tts(self, text: str) -> None:
        try:
            from google.cloud import texttospeech_v1 as tts  # type: ignore
        except ImportError:
            log.error("google-cloud-texttospeech not installed; falling back to mock")
            await self._mock_tts(text)
            return

        client = tts.TextToSpeechAsyncClient()
        synthesis_input = tts.SynthesisInput(text=text)
        voice = tts.VoiceSelectionParams(
            language_code="en-US",
            name=self._voice or "en-US-Neural2-D",
        )
        audio_config = tts.AudioConfig(
            audio_encoding=tts.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
        )
        response = await client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        # Strip WAV header (44 bytes)
        await self._emit_chunk(response.audio_content[44:], text)

    # ------------------------------------------------------------------
    # Mock TTS — silence of the right duration + estimated word timestamps
    # ------------------------------------------------------------------

    async def _mock_tts(self, text: str) -> None:
        word_count = len(text.split())
        duration_ms = int(word_count * 1000 / _WORDS_PER_SECOND)
        # Silence: 16 kHz, 16-bit, mono → 2 bytes/sample × 16 samples/ms
        silence = b"\x00" * (duration_ms * 32)
        await self._emit_chunk(silence, text)

    # ------------------------------------------------------------------
    # Shared emit
    # ------------------------------------------------------------------

    async def _emit_chunk(self, audio_bytes: bytes, text: str) -> None:
        ref = f"tts/{self._utterance_id}/chunk{self._chunk_index}.pcm"
        t_audio_out = time.monotonic() - self._stream_start

        # Duration of this chunk in ms (16 kHz, 16-bit, mono = 2 bytes/sample)
        chunk_duration_ms = len(audio_bytes) // 32

        word_timestamps = _estimate_word_timestamps(text, self._audio_out_cursor_ms)
        self._audio_out_cursor_ms += chunk_duration_ms

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
        self._chunk_index += 1
