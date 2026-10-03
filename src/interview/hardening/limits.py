"""
Session cap and intake rate limit — stage 11.

Both are in-process and monotonic-clock based. There is no database before
stage 9 and no shared cache now, so these protect one server process; a second
process gets its own budget. That is stated rather than hidden, because the
honest limit is the one you can point at.

Neither of these is a scoring or eligibility gate. They bound resource use:
how many live sessions a box will carry at once, and how often one candidate
may kick off a repo index.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


class SessionCapExceeded(RuntimeError):
    """The server is already carrying its maximum number of live sessions."""


@dataclass
class SessionCap:
    """
    Hard ceiling on concurrent live sessions.

    `acquire` is explicit rather than a context manager because a session's
    life is a WebSocket's life, not a block's: the release happens in the
    socket's `finally`.
    """

    limit: int = 8
    _live: set[str] = field(default_factory=set)

    @property
    def live(self) -> int:
        return len(self._live)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - len(self._live))

    def acquire(self, session_id: str) -> None:
        # A reconnect re-acquires the id it already holds; that must not count
        # twice or a flaky network would eat the whole cap.
        if session_id in self._live:
            return
        if len(self._live) >= self.limit:
            raise SessionCapExceeded(
                f"{len(self._live)} live sessions already; limit is {self.limit}"
            )
        self._live.add(session_id)

    def release(self, session_id: str) -> None:
        self._live.discard(session_id)


@dataclass(frozen=True)
class RateLimit:
    """`max_events` permitted in any `window_s` sliding window."""

    max_events: int
    window_s: float


@dataclass
class RateLimiter:
    """
    Sliding-window rate limit, keyed by caller.

    Used for intake: cloning and indexing a repo is the most expensive thing a
    stranger can ask this service to do, so it is the one path that is capped
    per candidate rather than per process.
    """

    limit: RateLimit
    clock: Callable[[], float] = time.monotonic
    _hits: dict[str, deque[float]] = field(default_factory=dict)

    def _window(self, key: str) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
        cutoff = self.clock() - self.limit.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def allow(self, key: str) -> bool:
        """Record and permit one event, or return False without recording it."""
        hits = self._window(key)
        if len(hits) >= self.limit.max_events:
            return False
        hits.append(self.clock())
        return True

    def retry_after_s(self, key: str) -> float:
        """Seconds until `allow` would succeed again. 0.0 when it would now."""
        hits = self._window(key)
        if len(hits) < self.limit.max_events:
            return 0.0
        return max(0.0, hits[0] + self.limit.window_s - self.clock())

    def forget(self, key: str) -> None:
        """Drop a caller's history — part of delete-my-data."""
        self._hits.pop(key, None)
