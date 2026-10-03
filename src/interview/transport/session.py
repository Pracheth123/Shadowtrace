"""
Interview session — Stage 2.

One FastAPI WebSocket per candidate. Translates WebSocket frames into bus events and
bus events into WebSocket frames. No turn logic lives here — only I/O bridging.

Protocol (binary WebSocket frames):
  Client → Server:  raw 16-bit little-endian PCM, 16 kHz, mono, 20 ms per frame
  Server → Client:  audio/* binary payload of the cached clip chunk

JSON WebSocket frames (text):
  Client → Server:  {"type": "playback_ack", "played_ms": 1200, "utterance_id": "utt-x"}
  Server → Client:  {"type": "session_ready"} | {"type": "turn_end"}

The session never blocks. Every callback schedules a bus.emit() as a task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from interview.events.bus import EventBus
from interview.events.log import EventLogger
from interview.events.schema import (
    BargeIn,
    Endpoint,
    PlaybackAck,
    SpeechStart,
    Truncate,
)
from interview.transport.turn_detector import TurnDetector, TurnDetectorConfig
from interview.transport.vad import EnergyVad, VadConfig

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "transport.yaml"


def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


class InterviewSession:
    """
    One session per candidate WebSocket connection.

    Usage::

        session = InterviewSession(websocket, log_path, session_id)
        await session.run()

    The session runs until the WebSocket closes or session_complete is emitted.
    """

    def __init__(
        self,
        websocket,  # fastapi.WebSocket
        log_path: Path,
        session_id: str | None = None,
        *,
        stt_api_key: str = "",
        keywords: list[str] | None = None,
    ) -> None:
        self._ws = websocket
        self._log_path = log_path
        self._session_id = session_id or str(uuid.uuid4())
        self._stt_api_key = stt_api_key
        self._keywords = keywords or []

        cfg = _load_config()
        vad_cfg_dict = cfg.get("vad", {})
        td_cfg_dict = cfg.get("turn_detector", {})
        bi_cfg = cfg.get("barge_in", {})
        audio_cfg = cfg.get("audio", {})

        self._vad = EnergyVad(VadConfig(**{k: v for k, v in vad_cfg_dict.items()
                                           if k in VadConfig.__dataclass_fields__}))
        self._td_cfg = TurnDetectorConfig.from_dict(td_cfg_dict)
        self._barge_in_threshold: float = bi_cfg.get("confidence_threshold", 0.80)
        self._barge_in_min_ms: int = bi_cfg.get("min_duration_ms", 300)
        self._barge_in_cooldown_ms: int = bi_cfg.get("cooldown_ms", 500)
        self._cached_clip_path = Path(
            audio_cfg.get("cached_clip_path", "fixtures/audio/cached_response.wav")
        )
        self._ack_interval_ms: int = audio_cfg.get("playback_ack_interval_ms", 100)

        self._bus = EventBus()
        self._logger: EventLogger | None = None

        # State
        self._stream_start: float = 0.0  # monotonic, set on first audio frame
        self._current_turn_id: str | None = None
        self._current_utterance_id: str | None = None
        self._playing: bool = False
        self._barge_in_cooldown_until: float = 0.0
        self._last_playback_ack_ms: int = 0

        # Turn detector — one per turn, recreated
        self._td: TurnDetector | None = None

        # Wire VAD callbacks
        self._vad.on_speech_start = self._handle_speech_start
        self._vad.on_silence = self._handle_vad_silence

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._logger = await EventLogger.open(
            self._bus, self._log_path, self._session_id
        )
        self._stream_start = time.monotonic()

        # Send ready signal
        await self._ws.send_text(json.dumps({"type": "session_ready",
                                              "session_id": self._session_id}))

        try:
            async for message in self._ws.iter_bytes():
                t_audio_in = time.monotonic() - self._stream_start

                # Check for barge-in while clip is playing
                if self._playing:
                    await self._handle_possible_barge_in(message, t_audio_in)
                else:
                    self._vad.push_frame(message, t_audio_in)
        except Exception as exc:
            log.info("Session WebSocket closed: %s", exc)
        finally:
            await self._bus.drain()
            if self._logger:
                await self._logger.close()

    # ------------------------------------------------------------------
    # VAD callbacks
    # ------------------------------------------------------------------

    def _handle_speech_start(self, turn_id: str, t_audio_in: float) -> None:
        self._current_turn_id = turn_id
        self._td = TurnDetector(
            config=self._td_cfg,
            on_endpoint=lambda conf, t: asyncio.ensure_future(
                self._handle_endpoint(conf, t)
            ),
        )
        self._td.notify_speech_start()
        asyncio.ensure_future(
            self._bus.emit(
                SpeechStart(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    producer="vad",
                    t_audio_in=t_audio_in,
                )
            )
        )

    def _handle_vad_silence(self, t_audio_in: float) -> None:
        if self._td:
            self._td.push_silence(t_audio_in)

    # ------------------------------------------------------------------
    # Endpoint handling
    # ------------------------------------------------------------------

    async def _handle_endpoint(self, confidence: float, t_audio_in: float) -> None:
        event = Endpoint(
            session_id=self._session_id,
            turn_id=self._current_turn_id,
            producer="turn_detector",
            t_audio_in=t_audio_in,
            confidence=confidence,
        )
        await self._bus.emit(event)

        # In stage 2: play the cached clip unconditionally (no LLM)
        await self._play_cached_clip()

    # ------------------------------------------------------------------
    # Cached clip playback
    # ------------------------------------------------------------------

    async def _play_cached_clip(self) -> None:
        """Read cached clip WAV, send as binary frames, with barge-in detection."""
        self._current_utterance_id = str(uuid.uuid4())
        self._playing = True
        self._last_playback_ack_ms = 0

        clip_data = self._read_clip()
        if not clip_data:
            self._playing = False
            return

        # Emit a minimal tts_chunk so the log has a first-audio timestamp
        from interview.events.schema import TtsChunk, WordTimestamp
        t_audio_out = time.monotonic() - self._stream_start
        chunk_event = TtsChunk(
            session_id=self._session_id,
            turn_id=self._current_turn_id,
            producer="session",
            t_audio_out=t_audio_out,
            audio_ref=str(self._cached_clip_path),
            word_timestamps=self._clip_word_timestamps(),
            utterance_id=self._current_utterance_id,
        )
        await self._bus.emit(chunk_event)

        # Send audio in ~20 ms chunks
        frame_bytes = 640  # 20 ms at 16 kHz, 16-bit
        offset = 0
        t_played_ms = 0
        last_ack_at = 0

        while offset < len(clip_data) and self._playing:
            chunk = clip_data[offset: offset + frame_bytes]
            offset += frame_bytes
            try:
                await self._ws.send_bytes(chunk)
            except Exception:
                break

            t_played_ms += 20
            # Emit playback_ack every ack_interval_ms
            if t_played_ms - last_ack_at >= self._ack_interval_ms:
                last_ack_at = t_played_ms
                self._last_playback_ack_ms = t_played_ms
                ack = PlaybackAck(
                    session_id=self._session_id,
                    turn_id=self._current_turn_id,
                    producer="session",
                    t_audio_out=time.monotonic() - self._stream_start,
                    played_ms=t_played_ms,
                    utterance_id=self._current_utterance_id,
                )
                await self._bus.emit(ack)
            await asyncio.sleep(0.018)  # pace to ~real-time

        self._playing = False
        self._vad.reset()

    def _read_clip(self) -> bytes:
        """Read cached clip, strip 44-byte WAV header if present."""
        try:
            data = self._cached_clip_path.read_bytes()
            if data[:4] == b"RIFF":
                return data[44:]  # strip WAV header
            return data
        except FileNotFoundError:
            log.warning("Cached clip not found: %s — sending silence", self._cached_clip_path)
            return b"\x00" * 3200  # 100 ms of silence as fallback

    def _clip_word_timestamps(self) -> list:
        """Return word timestamps from a companion .json file, or empty list."""
        from interview.events.schema import WordTimestamp
        json_path = self._cached_clip_path.with_suffix(".json")
        if json_path.exists():
            import json as _json
            data = _json.loads(json_path.read_text())
            return [
                WordTimestamp(word=w["word"], offset_ms=w["offset_ms"])
                for w in data.get("word_timestamps", [])
            ]
        return [WordTimestamp(word="[cached]", offset_ms=0)]

    # ------------------------------------------------------------------
    # Barge-in detection
    # ------------------------------------------------------------------

    async def _handle_possible_barge_in(self, pcm_bytes: bytes, t_audio_in: float) -> None:
        """Feed frame to VAD while clip is playing; fire barge_in if threshold met."""
        now = time.monotonic() * 1000.0
        if now < self._barge_in_cooldown_until:
            return

        self._vad.push_frame(pcm_bytes, t_audio_in)
        conf = self._vad.confidence()

        if conf >= self._barge_in_threshold:
            self._playing = False
            self._barge_in_cooldown_until = now + self._barge_in_cooldown_ms

            # Emit barge_in
            await self._bus.emit(
                BargeIn(
                    session_id=self._session_id,
                    turn_id=self._current_turn_id,
                    producer="vad",
                    t_audio_in=t_audio_in,
                    confidence=conf,
                )
            )

            # Emit truncate at the last acked word
            word_idx, word = self._last_acked_word()
            await self._bus.emit(
                Truncate(
                    session_id=self._session_id,
                    turn_id=self._current_turn_id,
                    producer="session",
                    t_audio_out=time.monotonic() - self._stream_start,
                    last_heard_word=word,
                    word_index=word_idx,
                    utterance_id=self._current_utterance_id or "",
                )
            )

    def _last_acked_word(self) -> tuple[int, str]:
        """
        Find the last word in the cached clip's word_timestamps that falls within
        played_ms. Returns (word_index, word_text).
        The word_index indexes tts_chunk.word_timestamps, not the candidate transcript.
        """
        json_path = self._cached_clip_path.with_suffix(".json")
        if json_path.exists():
            import json as _json
            data = _json.loads(json_path.read_text())
            wts = data.get("word_timestamps", [])
            played = self._last_playback_ack_ms
            last_idx = 0
            last_word = wts[0]["word"] if wts else "[unknown]"
            for i, w in enumerate(wts):
                if w["offset_ms"] <= played:
                    last_idx = i
                    last_word = w["word"]
            return last_idx, last_word
        return 0, "[unknown]"

    # ------------------------------------------------------------------
    # JSON text frame from client (playback_ack etc.)
    # ------------------------------------------------------------------

    async def handle_text_message(self, data: str) -> None:
        msg = json.loads(data)
        if msg.get("type") == "playback_ack":
            self._last_playback_ack_ms = msg.get("played_ms", self._last_playback_ack_ms)
