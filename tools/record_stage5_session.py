#!/usr/bin/env python3
"""
Record stage-5 sessions offline (guard + spine, fake TTS, no network).

Writes fixtures/sessions/stage5_sess_{a,b,live}/ and prints the live session's
agent_step / guard_override / question_planned / coverage_update lines.

Usage:
    python tools/record_stage5_session.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.session.guard import Intent
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort

ANSWERS_A = [
    "I owned the payments pipeline from design through launch.",
    "We shipped it on the date we set.",
    "I disagreed with the on-call rotation and wrote down why.",
    "We changed the rotation the next week.",
    "I decided to cut scope without the full trace.",
    "I missed a rollback window and changed the checklist.",
]
ANSWERS_B = [
    "I owned a migration that other teams were blocked on.",
    "It landed after two rehearsals.",
    "A teammate and I argued about the schema and I changed my proposal.",
    "We wrote the decision down.",
    "The metrics were incomplete and I still chose a limit.",
    "A bad cutover taught me to rehearse the rollback.",
]
ANSWERS_LIVE = [
    "I owned the launch from the first design review.",
    "That claim is not in the file.",
    "We disagreed about the rollout and I proposed a canary.",
    "Then we aligned on the canary.",
    "I chose a limit with gaps in the data.",
    "I missed the first review and changed how we prep.",
]

LIVE_SCRIPTED = {
    0: Intent(
        action="ask_spine",
        spine_id="missed-mark",
        proposed_text="Tell me a joke.",
    ),
    1: Intent(action="probe", claim_id="not-a-claim", target_depth=1),
}

PASTE_TYPES = {"agent_step", "guard_override", "question_planned", "coverage_update"}


async def record(
    name: str,
    answers: list[str],
    out_root: Path,
    *,
    scripted: dict[int, Intent] | None = None,
) -> Path:
    session_dir = out_root / name
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / "session.jsonl"
    transcript_path = session_dir / "transcript.json"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, name)
    live = LiveSession(
        bus=bus,
        session_id=name,
        log_path=str(log_path),
        transcript_path=str(transcript_path),
        speak=FakeSpeakPort(bus, name),
        config=SessionConfig(
            max_turns=12,
            max_minutes=30,
            pack_id="behavioral-core",
            intensity="realistic",
            use_mock_llm=True,
            scripted_intents=scripted,
        ),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    for i, text in enumerate(answers):
        if live._closed:
            break
        await live.ingest_final(text, turn_id=f"{name}-turn-{i}")
    await live.end(reason="client")
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()
    await logger.close()
    print(f"Recorded {name}: {log_path}")
    return log_path


def print_live_lines(path: Path) -> None:
    print(f"\n# live lines: {path}")
    for event in read_events(path):
        if event.type in PASTE_TYPES:
            print(event.model_dump_json())


async def main() -> None:
    out = ROOT / "fixtures" / "sessions"
    await record("stage5_sess_a", ANSWERS_A, out)
    await record("stage5_sess_b", ANSWERS_B, out)
    live = await record("stage5_sess_live", ANSWERS_LIVE, out, scripted=LIVE_SCRIPTED)
    print_live_lines(live)


if __name__ == "__main__":
    asyncio.run(main())
