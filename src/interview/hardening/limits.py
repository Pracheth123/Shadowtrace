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

import ipaddress
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping


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

    def would_allow(self, key: str) -> bool:
        """True if `allow` would succeed now. Records nothing."""
        return len(self._window(key)) < self.limit.max_events

    def refund(self, key: str) -> None:
        """
        Return the most recent event. Used when admission was granted but the
        request failed before any billable work, so an error does not eat quota.
        """
        hits = self._hits.get(key)
        if hits:
            hits.pop()

    def forget(self, key: str) -> None:
        """Drop a caller's history — part of delete-my-data."""
        self._hits.pop(key, None)


class AdmissionRefused(RuntimeError):
    """One of the limits for this request is exhausted."""

    def __init__(self, message: str, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = max(1, math.ceil(retry_after_s))


@dataclass
class Admission:
    """
    A set of limits checked together, all or nothing.

    A request is admitted only if every (limiter, key) pair has room; then one
    event is recorded on each. Nothing is recorded on a refusal, so being
    turned away by the address limit does not also spend the candidate's
    budget. `refund` hands every recorded event back after an early failure.
    """

    checks: list[tuple[RateLimiter, str, str]]

    def admit(self) -> "Admission":
        for limiter, key, message in self.checks:
            if not limiter.would_allow(key):
                raise AdmissionRefused(message, limiter.retry_after_s(key))
        for limiter, key, _ in self.checks:
            limiter.allow(key)
        return self

    def refund(self) -> None:
        for limiter, key, _ in self.checks:
            limiter.refund(key)


GLOBAL_KEY = "*"


def _networks(trusted: Iterable[str]) -> list[ipaddress._BaseNetwork]:
    out = []
    for item in trusted:
        try:
            out.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return out


def _in(address: str, networks: list) -> bool:
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    return any(ip in net for net in networks)


def client_address(peer: str | None, headers: Mapping[str, str], trusted: Iterable[str]) -> str:
    """
    The address limits are keyed by.

    X-Forwarded-For is believed only when the socket peer is a trusted proxy;
    otherwise any client could send its own header and get a fresh budget per
    request. The chain is walked right to left, skipping trusted proxies, and
    the first untrusted hop is the client.
    """
    peer = (peer or "unknown").strip()
    networks = _networks(trusted)
    if not networks or not _in(peer, networks):
        return peer
    forwarded = headers.get("x-forwarded-for") or headers.get("X-Forwarded-For") or ""
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _in(hop, networks):
            try:
                return str(ipaddress.ip_address(hop))
            except ValueError:
                return peer
    return hops[0] if hops else peer
