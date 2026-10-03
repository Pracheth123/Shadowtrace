"""
Client-side request-per-minute limiter + turn call budget.

Rate limits are shared across all roles on one Groq key.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class RpmLimiter:
    """Sliding-window limiter: at most `rpm` acquires per rolling 60 s."""

    def __init__(self, rpm: int) -> None:
        self._rpm = max(1, rpm)
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._times and now - self._times[0] >= 60.0:
                    self._times.popleft()
                if len(self._times) < self._rpm:
                    self._times.append(now)
                    return
                wait = 60.0 - (now - self._times[0]) + 0.01
                await asyncio.sleep(max(wait, 0.05))


class TurnCallBudget:
    """Hard cap on model calls per live turn (and session counter)."""

    def __init__(self, max_per_turn: int) -> None:
        self._max_per_turn = max(1, max_per_turn)
        self._per_turn: dict[str, int] = defaultdict(int)
        self._session_total = 0
        self._lock = asyncio.Lock()

    @property
    def session_total(self) -> int:
        return self._session_total

    async def begin_call(self, turn_id: str | None) -> tuple[int, int]:
        """
        Reserve a call. Returns (session_call_index, turn_call_index).
        Raises TurnCallBudgetExceeded if the turn is over budget.
        """
        async with self._lock:
            key = turn_id or "_session"
            turn_n = self._per_turn[key]
            if turn_id is not None and turn_n >= self._max_per_turn:
                raise TurnCallBudgetExceeded(
                    f"turn {turn_id} already used {turn_n}/{self._max_per_turn} model calls"
                )
            self._per_turn[key] = turn_n + 1
            self._session_total += 1
            return self._session_total, self._per_turn[key]


class TurnCallBudgetExceeded(RuntimeError):
    pass
