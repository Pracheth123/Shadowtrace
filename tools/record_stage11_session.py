#!/usr/bin/env python3
"""
Record the stage-11 evidence set.

Three sessions, all on the mock path (no API key, no audio hardware):

  1. `stage11_panel`     — panel mode on `behavioral-core`: three voices, one
                           shared guard, one voice at a time.
  2. `stage11_text_lane` — the text lane: same bus, agent, guard and evaluation,
                           no audio, delivery not assessed.
  3. `stage11_fallbacks` — every fallback exercised once, so the exit criterion
                           is read off one log.

Prints the persona sequence, the spine check, and where each artifact landed.

    python tools/record_stage11_session.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.events.schema import Endpoint, FinalTranscript, Partial, PlaybackAck
from interview.packs.model import load_pack
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort, TextLaneSpeakPort

# Long enough that the whole four-item spine is reached: probes take turns too,
# and the third panel voice only speaks when its spine item comes up.
ANSWERS = [
    "I owned the payments pipeline from design through launch this quarter.",
    "We measured consumer lag and I decided to shed load at the edge first.",
    "I disagreed with the on-call rota and wrote the reason down for the team.",
    "I pushed back in review and we agreed to split the change into two deploys.",
    "I made the cutover call with incomplete traces and watched the error rate.",
    "I set a rollback threshold first, then let the migration run overnight.",
    "I missed a rollback window and changed the release checklist the next day.",
    "I added a dry-run step because I had skipped that check under time pressure.",
]

OUT = ROOT / "fixtures" / "sessions"


async def _turn(bus: EventBus, session_id: str, turn_id: str, text: str, t: float) -> None:
    """One candidate turn: a stable partial, an endpoint, then the final."""
    await bus.emit(
        Partial(
            session_id=session_id,
            turn_id=turn_id,
            producer="record_stage11",
            t_audio_in=t,
            text=text,
            stable_until_ms=int(t * 1000),
            revision=0,
        )
    )
    await bus.drain()
    await bus.emit(
        Endpoint(
            session_id=session_id,
            turn_id=turn_id,
            producer="record_stage11",
            t_audio_in=t + 0.2,
            confidence=0.93,
        )
    )
    await bus.emit(
        FinalTranscript(
            session_id=session_id,
            turn_id=turn_id,
            producer="record_stage11",
            t_audio_in=t + 0.2,
            text=text,
            word_timings=[],
        )
    )
    await bus.drain()


def _auto_ack(bus: EventBus, session_id: str):
    """Acknowledge playback so the waterfall has a net+play leg."""

    async def handler(event) -> None:
        if event.type != "tts_chunk":
            return
        await bus.emit(
            PlaybackAck(
                session_id=session_id,
                turn_id=event.turn_id,
                producer="record_stage11",
                t_audio_out=event.t_audio_out + 0.02,
                played_ms=80,
                utterance_id=event.utterance_id,
            )
        )

    return handler


async def _record(
    name: str,
    *,
    lane: str,
    panel_mode: bool,
    intensity: str = "realistic",
) -> tuple[Path, LiveSession]:
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "session.jsonl"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, name)
    if lane == "voice":
        bus.subscribe("tts_chunk", _auto_ack(bus, name))
        speak = FakeSpeakPort(bus, name)
    else:
        speak = TextLaneSpeakPort(bus, name)

    live = LiveSession(
        bus=bus,
        session_id=name,
        log_path=str(log_path),
        transcript_path=str(out / "transcript.json"),
        speak=speak,
        config=SessionConfig(
            max_turns=16,
            max_minutes=30,
            pack_id="behavioral-core",
            intensity=intensity,
            panel_mode=panel_mode,
            lane=lane,
            use_mock_llm=True,
        ),
    )
    live.attach()
    await live.start()
    await live.wait_idle()

    for index, text in enumerate(ANSWERS):
        if live._closed:
            break
        await _turn(bus, name, f"{name}-turn-{index}", text, 1.0 + index)

    return log_path, (live, bus, logger)  # type: ignore[return-value]


async def _finish(bundle) -> None:
    live, bus, logger = bundle
    if not live._closed:
        await live.end(reason="client")
    await bus.drain()
    await logger.close()


def _personas(log_path: Path) -> list[str]:
    seen: list[str] = []
    for event in read_events(log_path):
        if event.type == "draft_ready" and event.persona and event.persona not in seen:
            seen.append(event.persona)
    return seen


def _voices(log_path: Path) -> list[str]:
    seen: list[str] = []
    for event in read_events(log_path):
        if event.type != "tts_chunk":
            continue
        parts = event.audio_ref.split("/")
        voice = parts[1] if len(parts) > 2 else "default"
        if voice not in seen:
            seen.append(voice)
    return seen


def _spine_asked(log_path: Path) -> list[str]:
    return [
        event.result.get("text", "")
        for event in read_events(log_path)
        if event.type == "tool_result" and event.tool == "ask_spine" and event.ok
    ]


def _fallbacks(log_path: Path) -> list[tuple[str, str]]:
    return [
        (event.kind, event.detail)
        for event in read_events(log_path)
        if event.type == "fallback_used"
    ]


async def main() -> None:
    pack = load_pack("behavioral-core")

    # ---------------- 1. panel ----------------
    panel_log, bundle = await _record(
        "stage11_panel", lane="voice", panel_mode=True, intensity="panel"
    )
    await _finish(bundle)
    print(f"\nRecorded {panel_log}")
    print(f"  personas on agent lines : {_personas(panel_log)}")
    print(f"  distinct TTS voices     : {_voices(panel_log)}")
    asked = _spine_asked(panel_log)
    expected = [item.text for item in pack.spine][: len(asked)]
    print(f"  spine asked verbatim    : {asked == expected} ({len(asked)} of {len(pack.spine)})")

    # ---------------- 2. text lane ----------------
    text_log, bundle = await _record("stage11_text_lane", lane="text", panel_mode=False)
    await _finish(bundle)
    print(f"\nRecorded {text_log}")
    audio = [e for e in read_events(text_log) if e.type == "tts_chunk"]
    print(f"  tts_chunk events        : {len(audio)} (expected 0 — no audio lane)")
    print(f"  fallback_used           : {_fallbacks(text_log)}")

    # ---------------- 3. every fallback once ----------------
    fb_log, bundle = await _record("stage11_fallbacks", lane="voice", panel_mode=False)
    live, bus, logger = bundle
    await live.note_fallback(
        "provider_failover",
        "live_interviewer: primary model failed (simulated); degraded to local mock",
    )
    await live.note_fallback("push_to_talk", "push-to-talk on (open-mic VAD unusable)")
    await live.note_fallback("resume_only", "no repo supplied; claims built from resume only")
    await live.note_fallback("fallback_repo", "clone refused; indexed a local --repo-path")
    await live.note_fallback("text_lane", "candidate switched to the text lane")
    await live.note_fallback("ws_reconnect", "socket dropped and the same session was resumed")
    await bus.drain()
    await _finish(bundle)
    kinds = [kind for kind, _ in _fallbacks(fb_log)]
    print(f"\nRecorded {fb_log}")
    print(f"  fallbacks exercised     : {kinds}")

    print("\nWaterfalls:")
    print(f"  python tools/waterfall.py {panel_log} --panel")
    print(f"  python tools/waterfall.py {text_log}")


if __name__ == "__main__":
    asyncio.run(main())
