"""
Stage 7 — signals, turn controller, speculative draft.

1. Signal extraction stays under 50 ms and never enters a scorer payload.
2. A complete partial emits likely_next before endpoint; floor_granted at endpoint.
3. A matching final reuses the draft. A rewritten final refreshes once.
4. Crowd-noise frames do not trip barge-in. A real burst does.
"""

from __future__ import annotations

import ast
import asyncio
import struct
import time
from pathlib import Path

import pytest

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.events.schema import (
    BargeIn,
    Endpoint,
    FinalTranscript,
    Partial,
    PlaybackAck,
)
from interview.session.agent import LiveAgent
from interview.session.router import draft_is_stale, route_answer
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.signals import ClaimHint, SignalExtractor, scorer_transcript
from interview.session.speak import FakeSpeakPort
from interview.session.turn_controller import looks_complete
from interview.transport.vad import EnergyVad, VadConfig

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "interview" / "session"


def _pcm(amplitude: int, samples: int = 320) -> bytes:
    return struct.pack(f"<{samples}h", *([amplitude] * samples))


def test_signals_are_fast_and_stay_off_the_scorer() -> None:
    extractor = SignalExtractor(
        claims=[ClaimHint(id="c-kafka", text="Built a Kafka pipeline at two million events")]
    )
    text = "I built a Kafka pipeline and I think we measured the lag."
    started = time.perf_counter()
    for _ in range(200):
        reading = extractor.update(text, looks_complete=True)
    elapsed_ms = (time.perf_counter() - started) * 1000 / 200
    assert elapsed_ms < 50
    assert "c-kafka" in reading.claim_hits
    payload = scorer_transcript(text + " excellent score")
    assert set(payload) == {"text"}
    assert "claim_hits" not in payload


def test_hedge_spike_uses_own_baseline() -> None:
    extractor = SignalExtractor()
    first = extractor.update("I shipped the pipeline after we measured the lag carefully today.")
    assert first.distress_triggers == []
    spiked = extractor.update(
        "um um um maybe I think I guess sort of not sure about the whole migration plan today"
    )
    assert "hedge_spike" in spiked.distress_triggers


def test_router_and_staleness() -> None:
    started = time.perf_counter()
    for _ in range(500):
        assert route_answer("I don't know, I was wrong about the cache.") == "conceded"
    assert (time.perf_counter() - started) * 1000 / 500 < 50
    assert route_answer("We measured the lag and I decided to shed load.") == "defended"
    assert not draft_is_stale("I owned the migration.", "I owned the migration and more.")
    assert draft_is_stale(
        "I owned the migration and we measured the lag carefully today.",
        "I don't know, I was wrong about that migration entirely.",
    )


def test_looks_complete_rejects_trailing_conjunction() -> None:
    assert looks_complete("I owned the migration from design through launch.")
    assert not looks_complete("I owned the migration and")


def test_crowd_noise_does_not_barge_in() -> None:
    vad = EnergyVad(VadConfig())
    false_hits = 0
    for i in range(80):
        # Low constant energy: office/crowd murmur under the speech threshold.
        vad.push_frame(_pcm(120), t_audio_in=i * 0.02)
        if vad.confidence() >= 0.80:
            false_hits += 1
    assert false_hits == 0
    assert vad.is_speaking is False

    fired = False
    for i in range(12):
        vad.push_frame(_pcm(12000), t_audio_in=2 + i * 0.02)
        if vad.confidence() >= 0.80:
            fired = True
    assert fired is True


@pytest.mark.asyncio
async def test_valid_draft_is_ready_before_endpoint_and_reused(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, "s7")
    live = LiveSession(
        bus=bus,
        session_id="s7",
        log_path=str(log_path),
        transcript_path=str(tmp_path / "transcript.json"),
        speak=FakeSpeakPort(bus, "s7"),
        config=SessionConfig(max_turns=4, pack_id="behavioral-core", use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()

    answer = (
        "I owned the payments pipeline from design through launch "
        "because we measured the lag before we shipped."
    )
    turn_id = "turn-valid"
    await bus.emit(
        Partial(
            session_id="s7",
            turn_id=turn_id,
            producer="test",
            t_audio_in=1.0,
            text=answer,
            stable_until_ms=1000,
            revision=0,
        )
    )
    await bus.drain()
    assert turn_id in live._drafts

    await bus.emit(
        Endpoint(
            session_id="s7",
            turn_id=turn_id,
            producer="test",
            t_audio_in=1.2,
            confidence=0.9,
        )
    )
    await bus.emit(
        FinalTranscript(
            session_id="s7",
            turn_id=turn_id,
            producer="test",
            t_audio_in=1.2,
            text=answer,
            word_timings=[],
        )
    )
    await bus.drain()
    await live.end(reason="client")
    await bus.drain()
    await logger.close()

    events = list(read_events(log_path))
    types_in_order = [e.type for e in events if e.turn_id == turn_id]
    assert types_in_order.index("likely_next") < types_in_order.index("floor_granted")
    assert types_in_order.index("floor_granted") < types_in_order.index("draft_ready")
    assert any(e.type == "route_decision" and e.decision == "defended" for e in events)
    assert live.valid_drafts == 1
    assert live.stale_drafts == 0


@pytest.mark.asyncio
async def test_stale_draft_refreshes_once(tmp_path: Path) -> None:
    log_path = tmp_path / "stale.jsonl"
    bus = EventBus()
    await EventLogger.open(bus, log_path, "s7s")
    live = LiveSession(
        bus=bus,
        session_id="s7s",
        log_path=str(log_path),
        transcript_path=str(tmp_path / "tr.json"),
        speak=FakeSpeakPort(bus, "s7s"),
        config=SessionConfig(max_turns=4, pack_id="behavioral-core", use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    turn_id = "turn-stale"
    await bus.emit(
        Partial(
            session_id="s7s",
            turn_id=turn_id,
            producer="test",
            t_audio_in=1.0,
            text="I owned the migration and we measured the lag carefully today.",
            stable_until_ms=1000,
            revision=0,
        )
    )
    await bus.drain()
    await bus.emit(
        Endpoint(
            session_id="s7s",
            turn_id=turn_id,
            producer="test",
            t_audio_in=1.4,
            confidence=0.9,
        )
    )
    await bus.emit(
        FinalTranscript(
            session_id="s7s",
            turn_id=turn_id,
            producer="test",
            t_audio_in=1.4,
            text="I don't know, I was wrong about that migration entirely.",
            word_timings=[],
        )
    )
    await bus.drain()
    await live.end(reason="client")
    await bus.drain()
    events = list(read_events(log_path))
    assert live.stale_drafts == 1
    assert any(e.type == "route_decision" and e.decision == "conceded" for e in events)
    assert any(e.type == "draft_ready" and e.variant == "concession" for e in events)
    assert any(
        e.type == "agent_step" and "stale draft" in e.summary for e in events
    )
    refreshes = [e for e in events if e.type == "agent_step" and "stale draft" in e.summary]
    assert len(refreshes) == 1


@pytest.mark.asyncio
async def test_barge_in_cancels_speculative_step(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "barge.jsonl"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, "s7b")

    original = LiveAgent.run

    async def slow(self, **kwargs):
        await asyncio.sleep(0.3)
        return await original(self, **kwargs)

    monkeypatch.setattr(LiveAgent, "run", slow)
    live = LiveSession(
        bus=bus,
        session_id="s7b",
        log_path=str(log_path),
        transcript_path=str(tmp_path / "tr.json"),
        speak=FakeSpeakPort(bus, "s7b"),
        config=SessionConfig(max_turns=4, pack_id="behavioral-core", use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    turn_id = "turn-barge"
    task = asyncio.create_task(
        bus.emit(
            Partial(
                session_id="s7b",
                turn_id=turn_id,
                producer="test",
                t_audio_in=1.0,
                text="I owned the payments pipeline from design through launch today.",
                stable_until_ms=1000,
                revision=0,
            )
        )
    )
    await asyncio.sleep(0.05)
    await bus.emit(
        BargeIn(
            session_id="s7b",
            turn_id=turn_id,
            producer="test",
            t_audio_in=1.1,
            confidence=0.95,
        )
    )
    await bus.drain()
    try:
        await task
    except Exception:
        pass
    await live.end(reason="client")
    await bus.drain()
    await logger.close()
    events = list(read_events(log_path))
    assert any(e.type == "agent_step" and e.cancelled for e in events)


@pytest.mark.asyncio
async def test_claim_status_is_logged_and_not_a_score(tmp_path: Path) -> None:
    log_path = tmp_path / "claim.jsonl"
    bus = EventBus()
    await EventLogger.open(bus, log_path, "s7c")
    live = LiveSession(
        bus=bus,
        session_id="s7c",
        log_path=str(log_path),
        transcript_path=str(tmp_path / "tr.json"),
        speak=FakeSpeakPort(bus, "s7c"),
        config=SessionConfig(max_turns=4, pack_id="behavioral-core", use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    text = (
        "I built a Kafka pipeline handling about two million events per second "
        "because we measured the lag before the cutover."
    )
    await bus.emit(
        Partial(
            session_id="s7c",
            turn_id="turn-claim",
            producer="test",
            t_audio_in=1.0,
            text=text,
            stable_until_ms=1000,
            revision=0,
        )
    )
    await bus.drain()
    await bus.emit(
        Endpoint(
            session_id="s7c",
            turn_id="turn-claim",
            producer="test",
            t_audio_in=1.2,
            confidence=0.9,
        )
    )
    await bus.emit(
        FinalTranscript(
            session_id="s7c",
            turn_id="turn-claim",
            producer="test",
            t_audio_in=1.2,
            text=text + " The score keyword must not become a scorer field.",
            word_timings=[],
        )
    )
    await bus.drain()
    await live.end(reason="client")
    await bus.drain()
    events = list(read_events(log_path))
    noted = [
        e
        for e in events
        if e.type == "tool_result" and e.tool == "note_claim_status" and e.ok
    ]
    assert noted
    assert noted[0].result["id"] == "c-kafka"
    assert noted[0].result["status"] == "held"
    payload = scorer_transcript(text)
    assert "c-kafka" not in payload
    assert "score" not in payload


def test_session_does_not_import_transport() -> None:
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            elif isinstance(node, ast.Import):
                mod = node.names[0].name
            if mod and mod.startswith("interview.transport"):
                raise AssertionError(f"{path.name} imports {mod}")
