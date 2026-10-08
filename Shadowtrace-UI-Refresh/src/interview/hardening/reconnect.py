"""
WebSocket reconnect — stage 11.

A dropped socket must not end an interview. The server hands the client a
resume ticket at session start; on reconnect the client presents it and is
re-attached to the *same* `LiveSession`, so there is one `turn_id` sequence,
one append-only log and one transcript across the drop.

What reconnect deliberately does not do:

  - it does not replay agent audio. The truncation contract says the transcript
    records what was actually heard, and a reconnecting client cannot tell us
    it heard audio that was in flight when the socket died. The in-flight
    utterance is truncated at the last acknowledged word, exactly as a barge-in
    would truncate it;
  - it does not resume a session whose closer has been spoken. That session is
    over, and its evaluation may already have been queued.

The registry is in-process, like the session cap: one server, one registry.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ResumeTicket:
    session_id: str
    token: str
    issued_at: float
    # The LiveSession and its bus/logger, held so a reconnect re-attaches to
    # the running objects rather than building new ones over the same log.
    attachments: dict[str, Any] = field(default_factory=dict)


class ReconnectRegistry:
    """Resume tickets for live sessions, with a time-to-live."""

    def __init__(
        self,
        *,
        ttl_s: float = 180.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._by_token: dict[str, ResumeTicket] = {}

    def issue(self, session_id: str, **attachments: Any) -> ResumeTicket:
        ticket = ResumeTicket(
            session_id=session_id,
            token=secrets.token_urlsafe(24),
            issued_at=self._clock(),
            attachments=dict(attachments),
        )
        self._by_token[ticket.token] = ticket
        return ticket

    def claim(self, token: str) -> ResumeTicket | None:
        """
        Redeem a token once. None if unknown or expired.

        Single-use: the caller is handed a fresh token by `issue` after a
        successful reconnect, so a leaked token cannot be replayed.
        """
        self._expire()
        ticket = self._by_token.pop(token, None)
        if ticket is None:
            return None
        if self._clock() - ticket.issued_at > self._ttl_s:
            return None
        return ticket

    def revoke(self, session_id: str) -> None:
        """Called when a session ends: its ticket must stop working."""
        for token, ticket in list(self._by_token.items()):
            if ticket.session_id == session_id:
                self._by_token.pop(token, None)

    def _expire(self) -> None:
        now = self._clock()
        for token, ticket in list(self._by_token.items()):
            if now - ticket.issued_at > self._ttl_s:
                self._by_token.pop(token, None)

    def __len__(self) -> int:
        self._expire()
        return len(self._by_token)
