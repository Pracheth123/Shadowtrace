#!/usr/bin/env python3
"""
tools/waterfall.py — per-turn latency breakdown from a JSONL session log.

Usage:
    python tools/waterfall.py <log_file>

Per turn, reports:
    - last partial  → endpoint          (endpoint detection)
    - endpoint      → question_planned  (planning)
    - endpoint      → draft_ready       (time to first token / TTFT)
    - draft_ready   → first tts_chunk   (TTS first chunk)
    - first tts_chunk → first playback_ack (network + playback)
    - TOTAL: endpoint → first tts_chunk

Flags any turn whose total exceeds 450 ms.
Also prints p50 and p95 of the total across all turns.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from collections import defaultdict
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from interview.events.log import read_events
from interview.events.schema import (
    Event,
    Partial,
    Endpoint,
    QuestionPlanned,
    DraftReady,
    TtsChunk,
    PlaybackAck,
)

THRESHOLD_MS = 450.0


def _ms(seconds: float) -> float:
    return seconds * 1000.0


def _pct(values: list[float], p: int) -> float:
    if not values:
        return float("nan")
    sorted_v = sorted(values)
    idx = max(0, int(len(sorted_v) * p / 100) - 1)
    return sorted_v[idx]


def compute_waterfall(log_path: Path) -> None:
    # Collect events bucketed by turn_id.
    by_turn: dict[str, list[Event]] = defaultdict(list)
    order: list[str] = []

    for event in read_events(log_path):
        if event.turn_id is None:
            continue
        if event.turn_id not in by_turn:
            order.append(event.turn_id)
        by_turn[event.turn_id].append(event)

    totals_ms: list[float] = []

    print(f"\n{'='*72}")
    print(f"  Waterfall: {log_path}")
    print(f"{'='*72}\n")

    for turn_id in order:
        events = by_turn[turn_id]

        # Gather relevant events for this turn.
        partials: list[Partial] = [e for e in events if e.type == "partial"]
        endpoints: list[Endpoint] = [e for e in events if e.type == "endpoint"]
        planned: list[QuestionPlanned] = [e for e in events if e.type == "question_planned"]
        drafts: list[DraftReady] = [e for e in events if e.type == "draft_ready"]
        tts_chunks: list[TtsChunk] = [e for e in events if e.type == "tts_chunk"]
        acks: list[PlaybackAck] = [e for e in events if e.type == "playback_ack"]

        if not endpoints:
            continue  # no endpoint means no turn to measure

        last_partial: Optional[Partial] = partials[-1] if partials else None
        endpoint = endpoints[0]
        first_planned: Optional[QuestionPlanned] = planned[0] if planned else None
        first_draft: Optional[DraftReady] = drafts[0] if drafts else None
        first_tts: Optional[TtsChunk] = tts_chunks[0] if tts_chunks else None
        first_ack: Optional[PlaybackAck] = acks[0] if acks else None

        def delta_ms(a: Optional[Event], b: Optional[Event]) -> str:
            if a is None or b is None or a.t_emit is None or b.t_emit is None:
                return "    n/a"
            return f"{_ms(b.t_emit - a.t_emit):7.1f} ms"

        ep_det = delta_ms(last_partial, endpoint)
        planning = delta_ms(endpoint, first_planned)
        ttft = delta_ms(endpoint, first_draft)
        tts_first = delta_ms(first_draft, first_tts)
        net_play = delta_ms(first_tts, first_ack)

        # Total: endpoint → first tts_chunk
        total_ms: Optional[float] = None
        if endpoint.t_emit is not None and first_tts is not None and first_tts.t_emit is not None:
            total_ms = _ms(first_tts.t_emit - endpoint.t_emit)
            totals_ms.append(total_ms)

        flag = "  !! EXCEEDS 450ms" if total_ms is not None and total_ms > THRESHOLD_MS else ""

        print(f"  Turn {turn_id}")
        print(f"    last partial  -> endpoint        (endpoint detection):  {ep_det}")
        print(f"    endpoint      -> question_planned (planning):           {planning}")
        print(f"    endpoint      -> draft_ready      (TTFT):               {ttft}")
        print(f"    draft_ready   -> first tts_chunk  (TTS first chunk):    {tts_first}")
        print(f"    first tts_chunk -> first playback_ack (net+play):       {net_play}")
        if total_ms is not None:
            print(f"    TOTAL (endpoint -> first tts_chunk):    {total_ms:7.1f} ms{flag}")
        else:
            print(f"    TOTAL (endpoint -> first tts_chunk):        n/a")
        print()

    # Session-level percentiles
    if totals_ms:
        p50 = _pct(totals_ms, 50)
        p95 = _pct(totals_ms, 95)
        print(f"  Session summary ({len(totals_ms)} measured turns):")
        print(f"    p50 total (endpoint -> first tts_chunk): {p50:7.1f} ms")
        print(f"    p95 total (endpoint -> first tts_chunk): {p95:7.1f} ms")
    else:
        print("  No turns with measurable latency found.")

    print(f"\n{'='*72}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Per-turn latency breakdown from a JSONL session log."
    )
    parser.add_argument("log_file", type=Path, help="Path to the .jsonl log file")
    args = parser.parse_args()
    compute_waterfall(args.log_file)


if __name__ == "__main__":
    main()
