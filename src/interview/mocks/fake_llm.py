"""
Fake LLM — Stage 3 mock.

Returns canned text after a configurable delay, streamed token by token at a
configurable rate. No API key, no network. Lets the TTS adapter and the interviewer
be tested with zero inference calls.

Usage::

    llm = FakeLlm(delay_ms=150, tokens_per_second=30)
    async for token in llm.stream(history):
        print(token, end="", flush=True)

    # Or via LeadInterviewer with provider="mock"
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

# A bank of canned responses. Indexed cyclically so repeated calls vary slightly.
_CANNED = [
    "That's a solid foundation. How did you handle back-pressure when the pipeline fell behind?",
    "Interesting. Can you walk me through how you measured the system's reliability in production?",
    "Got it. What was the hardest technical decision you made during that project, and why?",
    "Thanks for sharing that. How did you communicate the rollback decision to your stakeholders?",
    "Understood. When you say the team disagreed, what specifically was the point of contention?",
    "Fair enough. If you were designing that system today, what would you change first?",
    "Good. How did you validate that the circuit breaker thresholds were correct for your traffic?",
    "Noted. Can you describe how you detected and responded to a consumer falling behind?",
]


class FakeLlm:
    """
    Fake LLM for offline testing.

    Parameters
    ----------
    delay_ms : int
        Simulated time-to-first-token latency in milliseconds.
    tokens_per_second : float
        Streaming rate after the initial delay (tokens ≈ words here).
    response_index : int | None
        Which canned response to use. None = cycle through all.
    """

    def __init__(
        self,
        delay_ms: int = 150,
        tokens_per_second: float = 30.0,
        response_index: int | None = None,
    ) -> None:
        self._delay_ms = delay_ms
        self._token_interval = 1.0 / max(tokens_per_second, 0.1)
        self._response_index = response_index
        self._call_count = 0

    async def stream(self, _history: list[dict]) -> AsyncIterator[str]:
        """Async generator of tokens, simulating real streaming latency."""
        # Simulate TTFT delay
        if self._delay_ms > 0:
            await asyncio.sleep(self._delay_ms / 1000.0)

        idx = (
            self._response_index
            if self._response_index is not None
            else self._call_count % len(_CANNED)
        )
        self._call_count += 1
        text = _CANNED[idx]

        for token in text.split():
            yield token + " "
            if self._token_interval > 0:
                await asyncio.sleep(self._token_interval)

    def get_response(self, index: int | None = None) -> str:
        """Return the full canned text for a given index (for test assertions)."""
        if index is None:
            index = self._call_count % len(_CANNED)
        return _CANNED[index % len(_CANNED)]
