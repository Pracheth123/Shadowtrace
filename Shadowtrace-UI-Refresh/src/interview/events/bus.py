"""
Asyncio pub/sub event bus — Stage 1.

Rules:
- Publishers call bus.emit(event).  The call is non-blocking; subscribers run as tasks.
- Subscribers register by event type string, a list of types, or a predicate.
- No subscriber can block a publisher.
- The bus assigns seq (monotonically per session) and t_emit (seconds since session
  start) only if the producer left them unset.  Replay passes both through untouched
  so the replayed log is byte-identical to the fixture.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from interview.events.schema import Event


class EventBus:
    """
    One instance per session.  Create it, pass it to every layer, emit events.

    Usage::

        bus = EventBus()
        bus.subscribe("speech_start", my_handler)
        await bus.emit(SpeechStart(...))
    """

    def __init__(self) -> None:
        self._seq: int = 0
        self._start_monotonic: float = time.monotonic()
        self._subscribers: list[tuple[Callable[["Event"], bool], Callable[["Event"], None]]] = []
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> float:
        """Seconds since session start on the monotonic clock."""
        return time.monotonic() - self._start_monotonic

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(
        self,
        event_type: str | list[str] | Callable[["Event"], bool],
        handler: Callable[["Event"], None],
    ) -> None:
        """
        Register handler for events matching event_type.

        event_type may be:
        - a single event type string, e.g. "speech_start"
        - a list of type strings
        - a predicate callable(event) -> bool
        """
        if callable(event_type):
            predicate = event_type
        elif isinstance(event_type, list):
            types = set(event_type)
            predicate = lambda e: e.type in types  # noqa: E731
        else:
            t = event_type
            predicate = lambda e: e.type == t  # noqa: E731

        self._subscribers.append((predicate, handler))

    def subscribe_all(self, handler: Callable[["Event"], None]) -> None:
        """Register handler for every event."""
        self._subscribers.append((lambda _: True, handler))

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    async def emit(self, event: "Event") -> None:
        """
        Stamp seq and t_emit if unset, then dispatch to all matching subscribers
        as fire-and-forget asyncio tasks so no subscriber can block a publisher.
        """
        # Only assign if unset — replay passes its own values through unchanged.
        if event.seq is None:
            # model_copy with update is the Pydantic v2 way to produce a modified copy
            event = event.model_copy(update={"seq": self._next_seq()})
        if event.t_emit is None:
            event = event.model_copy(update={"t_emit": self._now()})

        for predicate, handler in self._subscribers:
            if predicate(event):
                task = asyncio.ensure_future(self._call(handler, event))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _call(handler: Callable, event: "Event") -> None:
        """
        Call handler.  If it is a coroutine function, await it.
        If it is synchronous, call it directly (it must not block).
        """
        if inspect.iscoroutinefunction(handler):
            await handler(event)
        else:
            handler(event)

    # ------------------------------------------------------------------
    # Drain (for tests and clean shutdown)
    # ------------------------------------------------------------------

    async def drain(self) -> None:
        """Wait for all in-flight subscriber tasks to complete."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
