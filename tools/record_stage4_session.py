"""
Record a Stage 4 session offline (FakeStt + FakeLlm + FakeTts).

Produces session.jsonl + transcript.json under fixtures/sessions/<name>/
and prints a waterfall summary.

Usage:
    python tools/record_stage4_session.py --name stage4_sess_a \\
        --transcript fixtures/transcripts/stage4_session_a.json
    python tools/record_stage4_session.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.events.bus import EventBus
from interview.events.log import EventLogger
from interview.events.schema import PlaybackAck
from interview.mocks.fake_stt import FakeStt
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort

DEFAULTS = [
    ("stage4_sess_a", "fixtures/transcripts/stage4_session_a.json"),
    ("stage4_sess_b", "fixtures/transcripts/stage4_session_b.json"),
    ("stage4_sess_c", "fixtures/transcripts/stage4_session_c.json"),
]


async def record_one(name: str, transcript: Path, out_root: Path) -> Path:
    session_dir = out_root / name
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / "session.jsonl"
    transcript_path = session_dir / "transcript.json"
    session_id = name

    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, session_id)

    # Auto-ack TTS so waterfall has playback_ack
    async def auto_ack(event) -> None:
        if event.type != "tts_chunk":
            return
        await bus.emit(
            PlaybackAck(
                session_id=session_id,
                turn_id=event.turn_id,
                producer="record_script",
                t_audio_out=event.t_audio_out + 0.05,
                played_ms=80,
                utterance_id=event.utterance_id,
            )
        )

    bus.subscribe("tts_chunk", auto_ack)

    live = LiveSession(
        bus=bus,
        session_id=session_id,
        log_path=str(log_path),
        transcript_path=str(transcript_path),
        speak=FakeSpeakPort(bus, session_id),
        config=SessionConfig(
            max_turns=8,
            max_minutes=30.0,
            pack_id="stage4-freeform",
            use_mock_llm=True,
        ),
    )
    live.attach()
    await live.start()
    await live.wait_idle()

    stt = FakeStt(
        bus,
        transcript,
        session_id,
        emit_endpoint=True,
    )
    # speed=0 → as fast as possible; still yields between events
    await stt.run(speed=0.0)
    await live.wait_idle()
    await live.end(reason="client")
    await bus.drain()
    await logger.close()

    print(f"Recorded {name}: {log_path}")
    return log_path


async def main_async(args: argparse.Namespace) -> None:
    out_root = Path(args.out)
    paths: list[Path] = []
    if args.all:
        for name, rel in DEFAULTS:
            paths.append(await record_one(name, ROOT / rel, out_root))
    else:
        if not args.name or not args.transcript:
            raise SystemExit("--name and --transcript required (or use --all)")
        paths.append(
            await record_one(args.name, Path(args.transcript), out_root)
        )

    # Waterfall each
    sys.path.insert(0, str(ROOT / "tools"))
    from waterfall import compute_waterfall  # type: ignore

    for p in paths:
        compute_waterfall(p)


def main() -> None:
    parser = argparse.ArgumentParser(description="Record Stage 4 offline sessions")
    parser.add_argument("--all", action="store_true", help="Record A/B/C fixtures")
    parser.add_argument("--name", help="Session directory name")
    parser.add_argument("--transcript", help="Path to FakeStt transcript JSON")
    parser.add_argument(
        "--out",
        default=str(ROOT / "fixtures" / "sessions"),
        help="Output root (default fixtures/sessions)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
