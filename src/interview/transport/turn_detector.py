"""
Semantic turn detector — Stage 2.

Combines a silence clock with a text-state check on the latest partial transcript.
Emits `endpoint` with a confidence score. All thresholds live in config/transport.yaml,
never in this file.

Design: the turn detector is a pure state machine fed by two streams:
  1. VAD silence signals  (vad.on_silence → push_silence)
  2. Partial text updates (push_partial)

It does NOT depend on audio, on the bus, or on the session layer. It receives events
and emits a result via a callback, so it can run standalone against fake_stt fixtures
(exit criterion: "every layer runs standalone against a recorded event file with no
other layer live").
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class TurnDetectorConfig:
    silence_ms: int = 700
    max_silence_ms: int = 2000
    extend_on_trailing: bool = True
    trailing_words: list[str] = field(default_factory=lambda: [
        "and", "so", "because", "but", "or", "that", "which",
        "if", "then", "when", "although", "however",
    ])
    extend_ms: int = 400
    min_speech_ms: int = 200
    endpoint_confidence_min: float = 0.70

    @classmethod
    def from_dict(cls, d: dict) -> "TurnDetectorConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


_WORD_RE = re.compile(r"\b(\w+)\b")


class TurnDetector:
    """
    Semantic turn detector.

    Usage::

        td = TurnDetector(config, on_endpoint=my_cb)
        td.notify_speech_start()          # called when VAD fires speech_start
        td.push_partial("I built the")    # called on every partial event
        td.push_silence(t_audio_in)       # called when VAD sees silence

        # on_endpoint(confidence: float, t_audio_in: float) is called when done.

    Standalone mode: feed push_partial and push_silence from a log replay — no audio
    required. The detector produces identical endpoint decisions.
    """

    def __init__(
        self,
        config: TurnDetectorConfig | None = None,
        on_endpoint: Callable[[float, float], None] | None = None,
    ) -> None:
        self.cfg = config or TurnDetectorConfig()
        self.on_endpoint = on_endpoint

        self._speech_start_ms: float | None = None   # monotonic ms
        self._silence_start_ms: float | None = None  # monotonic ms
        self._last_t_audio_in: float = 0.0
        self._latest_partial: str = ""
        self._endpoint_fired: bool = False
        self._timer_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Feed methods
    # ------------------------------------------------------------------

    def notify_speech_start(self) -> None:
        """Reset state when a new turn begins."""
        self._speech_start_ms = self._now_ms()
        self._silence_start_ms = None
        self._latest_partial = ""
        self._endpoint_fired = False
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None

    def push_partial(self, text: str) -> None:
        """Feed the latest partial transcript text."""
        self._latest_partial = text.strip()

    def push_silence(self, t_audio_in: float) -> None:
        """
        Called by VAD when silence begins.
        Starts the silence clock; schedules endpoint if silence holds.
        """
        self._last_t_audio_in = t_audio_in
        if self._endpoint_fired:
            return
        if self._silence_start_ms is None:
            self._silence_start_ms = self._now_ms()
            self._schedule_endpoint()

    def push_speech_resumed(self) -> None:
        """Called if VAD detects speech again before endpoint fired (interruption)."""
        self._silence_start_ms = None
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _schedule_endpoint(self) -> None:
        silence_budget = self._effective_silence_ms()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — cannot schedule (shouldn't happen in production)
            return
        self._timer_task = loop.create_task(self._endpoint_after(silence_budget))

    async def _endpoint_after(self, delay_ms: int) -> None:
        try:
            await asyncio.sleep(delay_ms / 1000.0)
        except asyncio.CancelledError:
            return

        # Guard: max_silence ceiling
        if self._silence_start_ms is not None:
            elapsed = self._now_ms() - self._silence_start_ms
            if elapsed < self.cfg.silence_ms and elapsed < self.cfg.max_silence_ms:
                return

        if not self._endpoint_fired:
            self._fire_endpoint()

    def _fire_endpoint(self) -> None:
        self._endpoint_fired = True
        confidence = self._compute_confidence()
        if self.on_endpoint:
            self.on_endpoint(confidence, self._last_t_audio_in)

    def _effective_silence_ms(self) -> int:
        """
        Return the silence duration to wait, extended if text ends with a trailing word.
        Hard-capped at max_silence_ms.
        """
        wait = self.cfg.silence_ms
        if self.cfg.extend_on_trailing and self._ends_with_trailing():
            wait += self.cfg.extend_ms
        return min(wait, self.cfg.max_silence_ms)

    def _ends_with_trailing(self) -> bool:
        words = _WORD_RE.findall(self._latest_partial.lower())
        if not words:
            return False
        return words[-1] in self.cfg.trailing_words

    def _compute_confidence(self) -> float:
        """
        Heuristic confidence.
        - Speech too short → low confidence
        - Ends with trailing word → lower confidence (may be mid-thought)
        - Clean endpoint → high confidence
        """
        if self._speech_start_ms is None:
            return 0.5
        speech_ms = self._now_ms() - self._speech_start_ms
        base = min(1.0, speech_ms / 2000.0)  # longer speech → higher base
        if self._ends_with_trailing():
            base *= 0.75
        return round(max(self.cfg.endpoint_confidence_min, base), 3)

    @staticmethod
    def _now_ms() -> float:
        return time.monotonic() * 1000.0
