"""
Stage 10 — intensity and the confidence guardrail.

1. Distress steps the session down once. Two calm turns step it back. The spine is still asked.
2. A rude spoken line is replaced.
3. A coach transcript carries a hint. A realistic transcript does not.
4. The report mentions intensity and does not change scores for it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.evaluation.pipeline import evaluate
from interview.evaluation.score import score_findings
from interview.packs.model import load_pack
from interview.session.guard import INTENSITY_MAX_DEPTH, depth_cap
from interview.session.intensity import allows_interruption, apply_tone
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort

ROOT = Path(__file__).parent.parent
CLAIMS = ROOT / "fixtures" / "claims" / "stage5_claims.json"


def _spine_texts(log_path: Path) -> list[str]:
    texts = []
    for event in read_events(log_path):
        if event.type == "tool_result" and event.tool == "ask_spine" and event.ok:
            texts.append(event.result.get("text", ""))
    return texts


async def _run(tmp_path: Path, name: str, answers: list[str], intensity: str) -> Path:
    log_path = tmp_path / name / "session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, name)
    live = LiveSession(
        bus=bus,
        session_id=name,
        log_path=str(log_path),
        transcript_path=str(tmp_path / name / "transcript.json"),
        speak=FakeSpeakPort(bus, name),
        config=SessionConfig(
            max_turns=12,
            max_minutes=30,
            pack_id="behavioral-core",
            intensity=intensity,
            use_mock_llm=True,
        ),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    for index, text in enumerate(answers):
        if live._closed:
            break
        await live.ingest_final(text, turn_id=f"{name}-turn-{index}")
    await live.end(reason="client")
    await bus.drain()
    await logger.close()
    return log_path


def test_intensity_config_is_what_the_guard_reads() -> None:
    pack = load_pack("behavioral-core")
    assert depth_cap(pack, "coach") == 1
    assert depth_cap(pack, "realistic") == 2
    # This pack's own probe cap is 2, so panel cannot go deeper than that.
    assert INTENSITY_MAX_DEPTH["panel"] == 3
    assert depth_cap(pack, "panel") == pack.probe_policy.max_depth
    assert allows_interruption("coach") is False
    assert allows_interruption("realistic") is True
    assert allows_interruption("panel") is True


@pytest.mark.asyncio
async def test_distress_steps_down_once_and_spine_remains(tmp_path: Path) -> None:
    answers = [
        "I owned the payments pipeline from design through launch today.",
        "um um um maybe I think I guess sort of not sure about the whole migration plan today",
        "We measured the lag and I decided to shed load at the edge.",
        "I disagreed with the on-call plan and wrote the reason down for the team.",
        "I made the cutover call and we watched the error rate afterward.",
        "I missed a rollback window and changed the checklist the next day.",
    ]
    log_path = await _run(tmp_path, "distress", answers, "realistic")
    events = list(read_events(log_path))
    changes = [event for event in events if event.type == "intensity_change"]
    downs = [event for event in changes if event.direction == "down"]
    ups = [event for event in changes if event.direction == "up"]
    assert len(downs) == 1
    assert downs[0].from_level == "realistic"
    assert downs[0].to_level == "coach"
    assert len(ups) == 1
    assert ups[0].to_level == "realistic"
    assert _spine_texts(log_path) == [item.text for item in load_pack("behavioral-core").spine]


def test_tone_guardrail_catches_a_rude_line() -> None:
    spoken, caught = apply_tone("You're an idiot if you can't answer that.")
    assert caught is True
    assert "idiot" not in spoken.casefold()
    clean, clean_caught = apply_tone("Tell me about a time you owned a project from start to finish.")
    assert clean_caught is False
    assert clean.startswith("Tell me about")


@pytest.mark.asyncio
async def test_spoken_rude_line_is_rewritten(tmp_path: Path) -> None:
    log_path = tmp_path / "tone" / "session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, "tone")
    live = LiveSession(
        bus=bus,
        session_id="tone",
        log_path=str(log_path),
        transcript_path=str(tmp_path / "tone" / "transcript.json"),
        speak=FakeSpeakPort(bus, "tone"),
        config=SessionConfig(pack_id="behavioral-core", use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    await live._deliver("You're an idiot.", "turn-rude", "utt-rude")
    await live.end(reason="client")
    await bus.drain()
    await logger.close()
    events = list(read_events(log_path))
    assert any(
        event.type == "agent_step" and "tone guardrail" in event.summary for event in events
    )
    transcript = json.loads((tmp_path / "tone" / "transcript.json").read_text(encoding="utf-8"))
    agent_lines = [row["text"] for row in transcript if row["speaker"] == "agent"]
    assert any("idiot" not in line.casefold() and "stay with the question" in line.casefold() for line in agent_lines)


@pytest.mark.asyncio
async def test_coach_and_realistic_sound_different(tmp_path: Path) -> None:
    answer = ["I owned the payments pipeline from design through launch today."]
    coach_log = await _run(tmp_path, "coach", answer, "coach")
    realistic_log = await _run(tmp_path, "realistic", answer, "realistic")
    coach_transcript = json.loads(coach_log.with_name("transcript.json").read_text(encoding="utf-8"))
    realistic_transcript = json.loads(
        realistic_log.with_name("transcript.json").read_text(encoding="utf-8")
    )
    coach_agent = " ".join(row["text"] for row in coach_transcript if row["speaker"] == "agent")
    realistic_agent = " ".join(row["text"] for row in realistic_transcript if row["speaker"] == "agent")
    spine = load_pack("behavioral-core").spine[0].text
    assert spine in coach_agent
    assert spine in realistic_agent
    assert "If you want a hint" in coach_agent
    assert "If you want a hint" not in realistic_agent


@pytest.mark.asyncio
async def test_report_notes_intensity_without_changing_scores(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.json"
    transcript.write_text(
        json.dumps(
            [
                {
                    "speaker": "candidate",
                    "turn_id": "t",
                    "text": "I owned the payments pipeline from design through launch today.",
                }
            ]
        ),
        encoding="utf-8",
    )
    plain = await evaluate(
        transcript_path=transcript,
        claims_path=CLAIMS,
        out_dir=tmp_path / "plain",
    )
    assert plain.intensity_note.startswith("Intensity was not recorded")
    assert "not adjusted" in plain.intensity_note
    again = score_findings(plain.findings)
    assert [item.score for item in again] == [item.score for item in plain.dimensions]
    assert "intensity" not in {item.dimension for item in plain.dimensions}
