"""
VAD (Voice Activity Detector) — Stage 2.

Implementation: energy-threshold VAD using numpy RMS per frame.
Interface: identical to what a Silero or WebRTC VAD would expose, so swapping the
implementation is a one-function change inside this file.

Why this instead of Silero/WebRTC:
- Silero requires PyTorch (≈600 MB); no Python 3.14 ONNX-runtime wheels at time of
  stage 2.
- webrtcvad's C extension does not build on Python 3.14 without patching.
- This runs at <0.1 ms per 20 ms frame, which is negligible in the event loop.
See docs/decisions/transport.md for the full rationale.

Contract:
- Call push_frame(pcm_bytes) for each incoming audio frame (20 ms @ 16 kHz = 320 samples).
- The VAD emits speech_start (with a fresh turn_id) or signals silence internally.
- The session layer polls is_speech or registers a callback via on_speech_start /
  on_silence.
"""

from __future__ import annotations

import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class VadConfig:
    sample_rate: int = 16000
    frame_ms: int = 20
    energy_threshold: float = 0.015   # RMS in [-1, 1] normalised range
    speech_pad_ms: int = 100          # hang-over: keep VAD active after loud frame
    min_speech_frames: int = 3        # consecutive frames before speech_start fires


class EnergyVad:
    """
    Frame-by-frame energy VAD.

    Usage::

        vad = EnergyVad(config)
        vad.on_speech_start = lambda turn_id, t_audio_in: ...
        vad.on_silence     = lambda t_audio_in: ...
        for frame in audio_frames:
            vad.push_frame(frame, t_audio_in)
    """

    def __init__(self, config: VadConfig | None = None) -> None:
        self.cfg = config or VadConfig()
        self._speaking: bool = False
        self._consecutive_loud: int = 0
        self._hang_over_frames: int = 0
        self._current_turn_id: str | None = None

        # Callbacks — set by session layer
        self.on_speech_start: Callable[[str, float], None] | None = None
        self.on_silence: Callable[[float], None] | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def push_frame(self, pcm_bytes: bytes, t_audio_in: float) -> None:
        """
        Process one PCM frame (16-bit little-endian samples).
        t_audio_in is the position in the candidate audio stream in seconds.
        """
        rms = self._rms(pcm_bytes)
        is_loud = rms >= self.cfg.energy_threshold

        pad_frames = self.cfg.speech_pad_ms // self.cfg.frame_ms

        if is_loud:
            self._consecutive_loud += 1
            self._hang_over_frames = pad_frames
        else:
            if self._hang_over_frames > 0:
                self._hang_over_frames -= 1
                is_loud = True  # treat as speech during hang-over
            else:
                self._consecutive_loud = 0

        if not self._speaking and self._consecutive_loud >= self.cfg.min_speech_frames:
            self._speaking = True
            self._current_turn_id = str(uuid.uuid4())
            if self.on_speech_start:
                self.on_speech_start(self._current_turn_id, t_audio_in)

        elif self._speaking and not is_loud and self._hang_over_frames == 0:
            self._speaking = False
            self._consecutive_loud = 0
            if self.on_silence:
                self.on_silence(t_audio_in)

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def current_turn_id(self) -> str | None:
        return self._current_turn_id

    def confidence(self) -> float:
        """
        Heuristic confidence in [0, 1] based on consecutive loud frames.
        Used for barge-in confidence field.
        """
        return min(1.0, self._consecutive_loud / max(1, self.cfg.min_speech_frames * 3))

    def reset(self) -> None:
        """Reset after a turn ends."""
        self._speaking = False
        self._consecutive_loud = 0
        self._hang_over_frames = 0
        self._current_turn_id = None

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _rms(pcm_bytes: bytes) -> float:
        """Compute RMS of 16-bit little-endian PCM, normalised to [-1, 1]."""
        n = len(pcm_bytes) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack(f"<{n}h", pcm_bytes[:n * 2])
        rms_int = (sum(s * s for s in samples) / n) ** 0.5
        return rms_int / 32768.0  # normalise to [-1, 1]
