"""
Client-side request-per-minute limiter + turn call budget.

Rate limits are shared across all roles on one Groq key.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class RpmLimiter:
    """
    Sliding-window limiter: at most `rpm` acquires per rolling 60 s, with
    live-first scheduling.

    Live interviewer turns and background work (evaluation, roadmap) share one
    provider key, so they share this budget. Two rules keep a live turn from
    queueing behind a burst of evaluation calls without starving evaluation:

      - background callers may not take the last `live_reserved_share` of the
        window, and yield while any live caller is waiting;
      - a background caller that has waited `background_max_defer_s` stops
        yielding and takes the next free slot like anyone else.

    Live callers are never deferred. Acquisition is atomic (no await between
    the capacity check and the append), so no lock is needed.
    """

    def __init__(
        self,
        rpm: int,
        *,
        live_reserved_share: float = 0.0,
        background_max_defer_s: float = 20.0,
    ) -> None:
        self._rpm = max(1, rpm)
        self._times: deque[float] = deque()
        self._reserved = min(self._rpm - 1, int(self._rpm * max(0.0, live_reserved_share)))
        self._max_defer = max(0.0, background_max_defer_s)
        self._live_waiting = 0
        self._background_waiting = 0
        # Telemetry: how often background work stepped aside, and the longest wait.
        self.deferrals = 0
        self.max_background_wait_s = 0.0

    @property
    def rpm(self) -> int:
        return self._rpm

    @property
    def reserved_for_live(self) -> int:
        return self._reserved

    def snapshot(self) -> dict:
        """Operational state; no request content."""
        self._prune(time.monotonic())
        return {
            "rpm": self._rpm,
            "in_window": len(self._times),
            "reserved_for_live": self._reserved,
            "live_waiting": self._live_waiting,
            "background_waiting": self._background_waiting,
            "background_deferrals": self.deferrals,
            "max_background_wait_s": round(self.max_background_wait_s, 3),
        }

    def _prune(self, now: float) -> None:
        while self._times and now - self._times[0] >= 60.0:
            self._times.popleft()

    async def acquire(self, priority: str = "live") -> None:
        background = priority == "background"
        enqueued = time.monotonic()
        deferred = False
        if background:
            self._background_waiting += 1
        else:
            self._live_waiting += 1
        try:
            while True:
                now = time.monotonic()
                self._prune(now)
                used = len(self._times)
                if background:
                    overdue = now - enqueued >= self._max_defer
                    limit = self._rpm if overdue else self._rpm - self._reserved
                    may_take = used < limit and (overdue or self._live_waiting == 0)
                    if used < self._rpm and not may_take and not deferred:
                        deferred = True
                        self.deferrals += 1
                else:
                    may_take = used < self._rpm
                if may_take:
                    self._times.append(now)
                    if background:
                        self.max_background_wait_s = max(self.max_background_wait_s, now - enqueued)
                    return
                if used >= self._rpm:
                    wait = 60.0 - (now - self._times[0]) + 0.01
                else:
                    wait = 0.05  # yielding to live callers or the reserve; re-check soon
                if background:
                    wait = min(wait, max(0.01, self._max_defer - (now - enqueued)) + 0.01)
                await asyncio.sleep(min(max(wait, 0.01), 1.0))
        finally:
            if background:
                self._background_waiting -= 1
            else:
                self._live_waiting -= 1


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
