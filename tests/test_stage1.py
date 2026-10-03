"""
Stage 1 tests.

1. Log round-trip — events written to a JSONL file and read back are identical.
2. Bus ordering — concurrent publishers do not reorder events (seq is monotonic).
3. Replay fidelity — replaying the fixture produces a log identical to the fixture.
4. Waterfall values — latencies match values computed by hand from the fixture.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.events.schema import (
    SpeechStart,
    Partial,
    Endpoint,
    FinalTranscript,
    WordTiming,
    QuestionPlanned,
    DraftReady,
    FloorGranted,
    TtsChunk,
    WordTimestamp,
    PlaybackAck,
    SessionComplete,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sessions" / "fake_session.jsonl"

SESSION = "sess-test"
TURN = "turn-t1"


def make_speech_start(seq: int = 1, t_emit: float = 0.0) -> SpeechStart:
    return SpeechStart(
        session_id=SESSION,
        turn_id=TURN,
        seq=seq,
        t_emit=t_emit,
        producer="test",
        t_audio_in=t_emit,
    )


def make_partial(seq: int, t_emit: float, text: str = "hello") -> Partial:
    return Partial(
        session_id=SESSION,
        turn_id=TURN,
        seq=seq,
        t_emit=t_emit,
        producer="test",
        t_audio_in=t_emit,
        text=text,
        stable_until_ms=int(t_emit * 1000),
        revision=0,
    )


def make_endpoint(seq: int, t_emit: float) -> Endpoint:
    return Endpoint(
        session_id=SESSION,
        turn_id=TURN,
        seq=seq,
        t_emit=t_emit,
        producer="test",
        t_audio_in=t_emit,
        confidence=0.9,
    )


def make_question_planned(seq: int, t_emit: float) -> QuestionPlanned:
    return QuestionPlanned(
        session_id=SESSION,
        turn_id=TURN,
        seq=seq,
        t_emit=t_emit,
        producer="test",
        kind="spine",
        competency="test_comp",
        target_depth=0,
    )


def make_draft_ready(seq: int, t_emit: float, utt: str = "utt-x") -> DraftReady:
    return DraftReady(
        session_id=SESSION,
        turn_id=TURN,
        seq=seq,
        t_emit=t_emit,
        producer="test",
        first_sentence="Test question?",
        variant="plain",
        utterance_id=utt,
    )


def make_tts_chunk(seq: int, t_emit: float, t_audio_out: float, utt: str = "utt-x") -> TtsChunk:
    return TtsChunk(
        session_id=SESSION,
        turn_id=TURN,
        seq=seq,
        t_emit=t_emit,
        producer="test",
        t_audio_out=t_audio_out,
        audio_ref="audio/test.opus",
        word_timestamps=[WordTimestamp(word="Test", offset_ms=0)],
        utterance_id=utt,
    )


def make_playback_ack(seq: int, t_emit: float, t_audio_out: float, utt: str = "utt-x") -> PlaybackAck:
    return PlaybackAck(
        session_id=SESSION,
        turn_id=TURN,
        seq=seq,
        t_emit=t_emit,
        producer="test",
        t_audio_out=t_audio_out,
        played_ms=500,
        utterance_id=utt,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Log round-trip
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_roundtrip() -> None:
    """Events written to JSONL and read back are byte-identical per field."""
    bus = EventBus()
    events_in = [
        make_speech_start(1, 0.0),
        make_partial(2, 0.1),
        make_endpoint(3, 0.3),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "test.jsonl"
        logger = await EventLogger.open(bus, out, SESSION)

        for ev in events_in:
            await bus.emit(ev)
        await bus.drain()
        await logger.close()

        events_out = list(read_events(out))

    assert len(events_out) == len(events_in), "Event count mismatch"
    for original, recovered in zip(events_in, events_out):
        assert original.model_dump() == recovered.model_dump(), (
            f"Round-trip mismatch for {original.type}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Bus ordering with concurrent publishers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bus_seq_monotonic_concurrent() -> None:
    """
    When multiple coroutines emit events concurrently, the bus must assign
    seq monotonically.  No seq may appear twice; no seq may decrease.
    """
    bus = EventBus()
    received: list[int] = []

    async def collect(event) -> None:
        assert event.seq is not None
        received.append(event.seq)

    bus.subscribe_all(collect)

    # Create events without seq so the bus stamps them.
    async def emitter(t_emit: float) -> None:
        ev = SpeechStart(
            session_id=SESSION,
            turn_id=TURN,
            producer="concurrent",
            t_audio_in=t_emit,
        )
        await bus.emit(ev)

    await asyncio.gather(*[emitter(float(i) * 0.01) for i in range(20)])
    await bus.drain()

    assert len(received) == 20, f"Expected 20 events, got {len(received)}"
    for i in range(1, len(received)):
        assert received[i] > received[i - 1], (
            f"seq not monotonic: {received[i - 1]} then {received[i]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Replay fidelity — output identical to fixture
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_identical_to_fixture() -> None:
    """
    Replaying the hand-written fixture in --fast mode produces a log whose
    events are field-for-field identical to the fixture.
    """
    from tools.replay import replay  # type: ignore[import]

    fixture_events = list(read_events(FIXTURE))

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "replayed.jsonl"
        await replay(FIXTURE, fast=True, out=out)
        replayed_events = list(read_events(out))

    assert len(replayed_events) == len(fixture_events), (
        f"Event count: fixture={len(fixture_events)}, replayed={len(replayed_events)}"
    )
    for i, (orig, rep) in enumerate(zip(fixture_events, replayed_events)):
        assert orig.model_dump() == rep.model_dump(), (
            f"Event {i} ({orig.type}) differs after replay:\n"
            f"  original:  {orig.model_dump()}\n"
            f"  replayed:  {rep.model_dump()}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Waterfall values match hand-computed numbers from the fixture
# ─────────────────────────────────────────────────────────────────────────────

# Hand-computed values from fake_session.jsonl (t_emit values):
#
# Turn 001:  last partial t=0.250, endpoint t=0.400, planned t=0.430,
#            draft t=0.440, tts t=0.480, ack t=0.830
#   ep_det   = (0.400-0.250)*1000 = 150.0 ms
#   planning = (0.430-0.400)*1000 =  30.0 ms
#   ttft     = (0.440-0.400)*1000 =  40.0 ms
#   tts_1st  = (0.480-0.440)*1000 =  40.0 ms
#   net_play = (0.830-0.480)*1000 = 350.0 ms
#   TOTAL    = (0.480-0.400)*1000 =  80.0 ms  ← endpoint→first_tts
#
# Turn 002:  last partial t=1.120, endpoint t=1.350, planned t=1.375,
#            draft t=1.380, tts t=1.420, ack t=1.790
#   ep_det   = (1.350-1.120)*1000 = 230.0 ms
#   planning = (1.375-1.350)*1000 =  25.0 ms
#   ttft     = (1.380-1.350)*1000 =  30.0 ms
#   tts_1st  = (1.420-1.380)*1000 =  40.0 ms
#   TOTAL    = (1.420-1.350)*1000 =  70.0 ms
#
# Turn 003:  last partial t=2.150, endpoint t=2.400, planned t=2.425,
#            draft t=2.430, tts t=2.470, ack t=2.880
#   TOTAL    = (2.470-2.400)*1000 =  70.0 ms
#
# Turn 004 (barge-in):
#            last partial t=3.220, endpoint t=3.410, planned t=3.435,
#            draft t=3.445, tts t=3.490, ack t=4.100
#   TOTAL    = (3.490-3.410)*1000 =  80.0 ms
#
# Turn 005:  last partial t=4.480, endpoint t=4.720, planned t=4.745,
#            draft t=4.750, tts t=4.790, ack t=5.300
#   TOTAL    = (4.790-4.720)*1000 =  70.0 ms
#
# Turn 006:  last partial t=5.650, endpoint t=5.870, planned t=5.895,
#            draft t=5.905 (intensity_change at 5.905, draft at 5.910),
#            tts t=5.950, ack t=6.460
#   TOTAL    = (5.950-5.870)*1000 =  80.0 ms
#
# Turn 007:  last partial t=6.840, endpoint t=7.050, planned: none (coverage only),
#            draft: none, tts: none  → no total for this turn
#
# p50 of [80,70,70,80,70,80] = 70.0 ms (3rd value when sorted: [70,70,70,80,80,80])
# p95 = 80.0 ms (index = int(6*95/100)-1 = 4 → sorted[4] = 80.0)


def _collect_waterfall_data(log_path: Path) -> dict:
    """Extract per-turn latencies using the same logic as waterfall.py."""
    from collections import defaultdict
    from interview.events.schema import Partial, Endpoint, QuestionPlanned, DraftReady, TtsChunk, PlaybackAck

    by_turn: dict = defaultdict(list)
    order = []
    for ev in read_events(log_path):
        if ev.turn_id is None:
            continue
        if ev.turn_id not in by_turn:
            order.append(ev.turn_id)
        by_turn[ev.turn_id].append(ev)

    results = {}
    for turn_id in order:
        evs = by_turn[turn_id]
        endpoints = [e for e in evs if e.type == "endpoint"]
        tts_chunks = [e for e in evs if e.type == "tts_chunk"]
        if not endpoints or not tts_chunks:
            continue
        ep = endpoints[0]
        tts = tts_chunks[0]
        total_ms = (tts.t_emit - ep.t_emit) * 1000
        results[turn_id] = round(total_ms, 1)
    return results


def test_waterfall_values_match_fixture() -> None:
    """Per-turn totals computed from the fixture match hand-calculated values."""
    expected = {
        "turn-001": 80.0,
        "turn-002": 70.0,
        "turn-003": 70.0,
        "turn-004": 80.0,
        "turn-005": 70.0,
        "turn-006": 80.0,
        # turn-007 has no tts_chunk so no total
    }
    actual = _collect_waterfall_data(FIXTURE)

    for turn_id, expected_ms in expected.items():
        assert turn_id in actual, f"Turn {turn_id} missing from waterfall output"
        assert abs(actual[turn_id] - expected_ms) < 0.5, (
            f"Turn {turn_id}: expected {expected_ms} ms, got {actual[turn_id]} ms"
        )

    # turn-007 should NOT have a total (no tts_chunk)
    assert "turn-007" not in actual, "turn-007 should not have a measurable total"


def test_waterfall_percentiles() -> None:
    """p50 and p95 of the session match hand-computed values."""
    from tools.waterfall import _pct  # type: ignore[import]

    totals = [80.0, 70.0, 70.0, 80.0, 70.0, 80.0]
    assert _pct(totals, 50) == 70.0, f"p50 wrong: {_pct(totals, 50)}"
    assert _pct(totals, 95) == 80.0, f"p95 wrong: {_pct(totals, 95)}"
