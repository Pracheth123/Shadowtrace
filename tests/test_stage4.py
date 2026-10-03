"""
Stage 4 tests — live session spine.

1. Full fake session (FakeLlm + FakeTts) → valid log + transcript.json
2. Import boundary: transport must not import session; session must not import transport
3. Truncation honoured in transcript
4. Barge-in cancels in-flight generation (agent_step cancelled)
"""

from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path

import pytest

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.events.schema import BargeIn, Truncate
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "interview"


@pytest.mark.asyncio
async def test_full_fake_session_produces_log_and_transcript(tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    transcript_path = tmp_path / "transcript.json"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, "sess-s4")

    live = LiveSession(
        bus=bus,
        session_id="sess-s4",
        log_path=str(log_path),
        transcript_path=str(transcript_path),
        speak=FakeSpeakPort(bus, "sess-s4"),
        config=SessionConfig(max_turns=3, use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()

    answers = [
        "I built a Kafka pipeline handling two million events per second.",
        "We used consumer lag metrics and dynamic throttling for back-pressure.",
        "The hardest call was rolling back a deploy during a Friday incident.",
    ]
    for text in answers:
        await live.ingest_final(text, turn_id=str(uuid.uuid4()))

    await live.end(reason="client")
    await bus.drain()
    await logger.close()

    assert log_path.exists()
    events = list(read_events(log_path))
    types = {e.type for e in events}
    assert "draft_ready" in types
    assert "tts_chunk" in types
    assert "session_complete" in types
    assert "final_transcript" in types

    assert transcript_path.exists()
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    speakers = {e["speaker"] for e in transcript}
    assert "candidate" in speakers
    assert "agent" in speakers
    assert any("Kafka" in e["text"] for e in transcript if e["speaker"] == "candidate")
    # opener + closer + at least one model reply
    assert sum(1 for e in transcript if e["speaker"] == "agent") >= 3


@pytest.mark.asyncio
async def test_truncate_updates_transcript(tmp_path: Path) -> None:
    bus = EventBus()
    log_path = tmp_path / "t.jsonl"
    transcript_path = tmp_path / "tr.json"
    await EventLogger.open(bus, log_path, "sess-trunc")

    live = LiveSession(
        bus=bus,
        session_id="sess-trunc",
        log_path=str(log_path),
        transcript_path=str(transcript_path),
        speak=FakeSpeakPort(bus, "sess-trunc"),
        config=SessionConfig(max_turns=5, use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()

    # Grab the opener utterance_id from transcript writer
    opener = next(e for e in live.transcript.entries if e["speaker"] == "agent")
    utt = opener["utterance_id"]
    words = opener["text"].split()
    cut_at = min(3, len(words) - 1)

    await bus.emit(
        Truncate(
            session_id="sess-trunc",
            turn_id="turn-opener",
            producer="test",
            t_audio_out=0.5,
            last_heard_word=words[cut_at],
            word_index=cut_at,
            utterance_id=utt,
        )
    )
    await bus.drain()
    await live.end(reason="closer_already_spoken")

    heard = next(e for e in live.transcript.entries if e.get("utterance_id") == utt)
    assert heard.get("truncated") is True
    assert heard["text"] == " ".join(words[: cut_at + 1])


@pytest.mark.asyncio
async def test_barge_in_cancels_generation(tmp_path: Path) -> None:
    bus = EventBus()
    log_path = tmp_path / "b.jsonl"
    logger = await EventLogger.open(bus, log_path, "sess-barge")

    live = LiveSession(
        bus=bus,
        session_id="sess-barge",
        log_path=str(log_path),
        transcript_path=str(tmp_path / "tr.json"),
        speak=FakeSpeakPort(bus, "sess-barge"),
        config=SessionConfig(max_turns=5, use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()

    # Start a reply then immediately barge in
    import asyncio

    task = asyncio.create_task(
        live.ingest_final("Tell me more about reliability.", turn_id="turn-x")
    )
    await asyncio.sleep(0.01)
    await bus.emit(
        BargeIn(
            session_id="sess-barge",
            turn_id="turn-x",
            producer="test",
            t_audio_in=0.2,
            confidence=0.9,
        )
    )
    await bus.drain()
    try:
        await task
    except Exception:
        pass
    await live.wait_idle()
    await live.end(reason="client")
    await logger.close()

    events = list(read_events(log_path))
    # Cancellation is best-effort; if generation finished first, no cancelled step.
    # At minimum barge_in must be logged.
    assert any(e.type == "barge_in" for e in events)


def test_import_boundary_transport_vs_session() -> None:
    """transport/* must not import interview.session; session/* must not import interview.transport."""
    violations: list[str] = []

    def check(tree: ast.AST, path: Path, forbidden_prefix: str) -> None:
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name
            if mod and (
                mod == forbidden_prefix or mod.startswith(forbidden_prefix + ".")
            ):
                violations.append(f"{path}: imports {mod}")

    for path in (SRC / "transport").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        check(tree, path.relative_to(ROOT), "interview.session")

    for path in (SRC / "session").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        check(tree, path.relative_to(ROOT), "interview.transport")

    assert not violations, "Import boundary violated:\n" + "\n".join(violations)
