#!/usr/bin/env python3
"""
tools/replay.py — replay a recorded JSONL session log through a fresh EventBus.

Usage:
    python tools/replay.py <log_file> [--fast | --realtime] [--out <output.jsonl>]

Modes:
    --fast      (default) re-emit events as quickly as possible
    --realtime  pace events by the original t_emit deltas

The output log (--out) is written by EventLogger and should be byte-identical to the
input when --fast mode is used with an already-stamped fixture (seq and t_emit already
present on every event, so the bus passes them through untouched).

This tool is how every later stage gets tested with nothing else live.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events


async def replay(
    source: Path,
    *,
    fast: bool = True,
    out: Path | None = None,
) -> None:
    bus = EventBus()

    # Optionally set up the logger before subscribing other handlers, so it
    # captures every event in order.
    logger: EventLogger | None = None
    if out is not None:
        # Derive session_id from the first event in the fixture.
        events = list(read_events(source))
        session_id = events[0].session_id if events else "replay"
        logger = await EventLogger.open(bus, out, session_id)
    else:
        events = list(read_events(source))

    prev_t: float | None = None

    for event in events:
        if not fast and prev_t is not None and event.t_emit is not None:
            delta = event.t_emit - prev_t
            if delta > 0:
                await asyncio.sleep(delta)

        prev_t = event.t_emit
        await bus.emit(event)

    # Let all subscriber tasks complete before we exit.
    await bus.drain()

    if logger is not None:
        await logger.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a recorded JSONL session log through a fresh EventBus."
    )
    parser.add_argument("log_file", type=Path, help="Path to the input .jsonl log file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--fast",
        action="store_true",
        default=True,
        help="Re-emit events as fast as possible (default)",
    )
    mode.add_argument(
        "--realtime",
        action="store_true",
        default=False,
        help="Pace events by the original t_emit deltas",
    )
    parser.add_argument("--out", type=Path, default=None, help="Write output log here")
    args = parser.parse_args()

    fast = not args.realtime

    asyncio.run(replay(args.log_file, fast=fast, out=args.out))
    print(f"Replay complete: {args.log_file}")
    if args.out:
        print(f"Output written to: {args.out}")


if __name__ == "__main__":
    main()
