#!/usr/bin/env python3
"""
Replay recorded sessions through the guard's approved-sequence reader.

Uses question_planned and ask_spine tool_result lines already in the log.
Does not construct tools or call a model (contract 5).

Usage:
    python tools/replay_guard.py fixtures/sessions/stage5_sess_a/session.jsonl \\
        fixtures/sessions/stage5_sess_b/session.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.events.log import read_events
from interview.session.guard import approved_sequence


def print_session(path: Path) -> None:
    events = list(read_events(path))
    rows = approved_sequence(events)
    print(f"\n{path}")
    print(f"approved sequence ({len(rows)} questions)")
    for i, row in enumerate(rows, start=1):
        if row["kind"] == "spine":
            print(
                f"  {i}. spine {row['spine_id']} depth={row['target_depth']} "
                f"| {row['text']}"
            )
        else:
            print(
                f"  {i}. probe competency={row['competency']} "
                f"depth={row['target_depth']}"
            )
    spine = [row["text"] for row in rows if row["kind"] == "spine"]
    print("spine text:")
    for text in spine:
        print(f"  - {text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Print guard-approved sequences from logs.")
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.logs:
        print_session(path)


if __name__ == "__main__":
    main()
