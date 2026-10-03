"""
Append-only JSONL event log — Stage 1.

Format
------
Line 0 (header):
    {"kind": "session_header", "session_id": "...", "started_at": "<ISO-8601 UTC>",
     "schema_version": 1}

Lines 1-N (events):
    One JSON object per line, produced by event.model_dump_json().

The reader yields typed Event models from either a file path or a file-like object.
Write and read round-trip byte-identically (same JSON, same line order).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Generator, IO

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from interview.events.schema import Event

# TypeAdapter lets us parse the discriminated union without a model wrapper.
from interview.events.schema import Event as EventUnion  # noqa: E402 (after TYPE_CHECKING)

_adapter: TypeAdapter = TypeAdapter(EventUnion)


# ---------------------------------------------------------------------------
# Session header
# ---------------------------------------------------------------------------


def _header(session_id: str) -> str:
    return json.dumps(
        {
            "kind": "session_header",
            "session_id": session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
        }
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class EventLogger:
    """
    Subscribes to all events on a bus and appends them to a JSONL file.

    Usage::

        async with EventLogger.open(bus, path, session_id) as logger:
            ...   # bus events are written automatically
        # file is flushed and closed on exit
    """

    def __init__(self, file: IO[str]) -> None:
        self._file = file
        self._lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        bus,  # EventBus — avoid circular import at module level
        path: Path,
        session_id: str,
    ) -> "EventLogger":
        """Open the log file, write the session header, subscribe to all events."""
        f = path.open("w", encoding="utf-8")
        f.write(_header(session_id) + "\n")
        f.flush()
        logger = cls(f)
        bus.subscribe_all(logger._handle)
        return logger

    async def _handle(self, event: "Event") -> None:
        async with self._lock:
            self._file.write(event.model_dump_json() + "\n")
            self._file.flush()

    async def close(self) -> None:
        self._file.close()

    # Context manager support
    async def __aenter__(self) -> "EventLogger":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def read_events(source: Path | str | IO[str]) -> Generator["Event", None, None]:
    """
    Yield typed Event models from a JSONL log file.

    Skips the session header line (kind == 'session_header').
    Raises ValueError if a line cannot be parsed as a known event type.

    Usage::

        for event in read_events(Path("fixtures/sessions/fake_session.jsonl")):
            print(event.type, event.seq)
    """
    if isinstance(source, (str, Path)):
        with open(source, encoding="utf-8") as f:
            yield from _iter_lines(f)
    else:
        yield from _iter_lines(source)


def _iter_lines(f: IO[str]) -> Generator["Event", None, None]:
    for line in f:
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("kind") == "session_header":
            continue  # skip header
        yield _adapter.validate_python(obj)
