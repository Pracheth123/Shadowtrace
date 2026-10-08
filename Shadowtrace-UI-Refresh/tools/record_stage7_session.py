#!/usr/bin/env python3
"""
Record a stage-7 session with speculative drafts.

Valid turns reuse the draft built on the partial. One turn revises the final
so the draft is stale. Prints a waterfall and the stale-draft rate.

    python tools/record_stage7_session.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.events.bus import EventBus
from interview.events.log import EventLogger
from interview.events.schema import Endpoint, FinalTranscript, Partial, PlaybackAck
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort

VALID = [
    "I owned the payments pipeline from design through launch today.",
    "We measured consumer lag and I decided to shed load at the edge.",
    "I disagreed with the on-call plan and wrote the reason down for the team.",
    "I made the cutover call with incomplete traces and we watched the error rate.",
]
STALE_PARTIAL = "I owned the migration and we measured the lag carefully today."
STALE_FINAL = "I don't know, I was wrong about that migration entirely."


async def _turn(bus: EventBus, session_id: str, turn_id: str, partial: str, final: str) -> None:
    await bus.emit(
        Partial(
            session_id=session_id,
            turn_id=turn_id,
            producer="record_stage7",
            t_audio_in=1.0,
            text=partial,
            stable_until_ms=900,
            revision=0,
        )
    )
    await bus.drain()
    await bus.emit(
        Endpoint(
            session_id=session_id,
            turn_id=turn_id,
            producer="record_stage7",
            t_audio_in=1.2,
            confidence=0.93,
        )
    )
    await bus.emit(
        FinalTranscript(
            session_id=session_id,
            turn_id=turn_id,
            producer="record_stage7",
            t_audio_in=1.2,
            text=final,
            word_timings=[],
        )
    )
    await bus.drain()


async def main() -> None:
    out = ROOT / "fixtures" / "sessions" / "stage7_speculative"
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "session.jsonl"
    session_id = "stage7_speculative"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, session_id)

    async def auto_ack(event) -> None:
        if event.type != "tts_chunk":
            return
        await bus.emit(
            PlaybackAck(
                session_id=session_id,
                turn_id=event.turn_id,
                producer="record_stage7",
                t_audio_out=event.t_audio_out + 0.02,
                played_ms=80,
                utterance_id=event.utterance_id,
            )
        )

    bus.subscribe("tts_chunk", auto_ack)
    live = LiveSession(
        bus=bus,
        session_id=session_id,
        log_path=str(log_path),
        transcript_path=str(out / "transcript.json"),
        speak=FakeSpeakPort(bus, session_id),
        config=SessionConfig(
            max_turns=8,
            max_minutes=30,
            pack_id="behavioral-core",
            use_mock_llm=True,
        ),
    )
    live.attach()
    await live.start()
    await live.wait_idle()

    for i, text in enumerate(VALID):
        await _turn(bus, session_id, f"turn-valid-{i}", text, text)
    await _turn(bus, session_id, "turn-stale", STALE_PARTIAL, STALE_FINAL)
    await live.end(reason="client")
    await bus.drain()
    await logger.close()

    total = live.valid_drafts + live.stale_drafts
    rate = (live.stale_drafts / total) if total else 0.0
    print(f"Recorded {log_path}")
    print(
        f"drafts valid={live.valid_drafts} stale={live.stale_drafts} "
        f"stale_rate={rate:.2f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
