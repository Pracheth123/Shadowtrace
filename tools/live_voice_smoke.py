#!/usr/bin/env python3
"""
Opt-in live smoke test of the real voice pipeline.

Makes real, billable calls to Deepgram and Groq. It does nothing unless you ask
for it explicitly:

    python tools/live_voice_smoke.py --yes

Credentials come from the server environment (`.env` or real env vars):
`DEEPGRAM_API_KEY`, `GROQ_API_KEY`. Nothing is printed that could leak a key.

What it checks, in order:

  1. **TTS** — the real `DeepgramTts` adapter synthesises a question and emits
     `tts_chunk` events with real audio bytes.
  2. **The loop** — that synthesised speech is resampled by the real
     `PcmStreamConverter` (24 kHz → 16 kHz) and fed into the real `DeepgramStt`
     adapter. If the transcript comes back resembling the sentence we spoke,
     then synthesis, the declared audio format, the resampler and transcription
     all agree. This is the check that a mocked test cannot make.
  3. **Turn assembly** — exactly one `final_transcript` for one spoken answer,
     however many `is_final` segments the provider sent.
  4. **Follow-up** — a real Groq call through `ModelProposer`, with the hiring
     manager's instructions, producing a question that references what was
     actually said rather than a template.
  5. **Barge-in** — provider `Clear` mid-utterance, and late chunks dropped.

What it cannot check: a browser. Microphone capture, playback scheduling and
audio-clock acknowledgement need a real page; those steps are listed in the
final report as browser-only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.config import get_settings  # noqa: E402
from interview.events.bus import EventBus  # noqa: E402
from interview.packs.model import load_pack  # noqa: E402
from interview.session.guard import GuardState  # noqa: E402
from interview.session.proposer import ModelProposer, ProposalContext  # noqa: E402
from interview.session.roles import (  # noqa: E402
    InterviewRound,
    RoleFamily,
    Seniority,
    role_for,
)
from interview.transport.audio import PcmStreamConverter, pcm16_duration_ms  # noqa: E402
from interview.transport.deepgram_stt import DeepgramStt  # noqa: E402
from interview.transport.deepgram_tts import DeepgramTts  # noqa: E402

SPOKEN = (
    "I increased sales by changing our outreach to focus on budget holders "
    "rather than end users."
)

PASS, FAIL, INFO = "  PASS", "  FAIL", "  ....."


def line(status: str, text: str) -> None:
    print(f"{status}  {text}", flush=True)


async def step_tts(settings) -> tuple[bytes, list]:
    """Synthesise a sentence and collect the real audio."""
    bus = EventBus()
    chunks: list = []
    bus.subscribe("tts_chunk", lambda event: chunks.append(event))
    audio = bytearray()

    tts = DeepgramTts(bus, "live-smoke", settings)

    async def collect(payload: bytes, utterance_id: str, rate: int) -> None:
        audio.extend(payload)

    tts.on_audio = collect
    started = time.monotonic()
    await tts.start()
    connect_ms = (time.monotonic() - started) * 1000

    first_byte_at: float | None = None

    async def timed(payload: bytes, utterance_id: str, rate: int) -> None:
        nonlocal first_byte_at
        if first_byte_at is None:
            first_byte_at = time.monotonic()
        audio.extend(payload)

    tts.on_audio = timed
    spoke_at = time.monotonic()
    await tts.synthesise(SPOKEN, "utt-live-1", "turn-live-1")
    await bus.drain()
    await tts.close()

    ttfb = ((first_byte_at or spoke_at) - spoke_at) * 1000
    seconds = len(audio) / 2 / settings.deepgram_tts_sample_rate
    line(PASS if audio else FAIL,
         f"TTS: {len(chunks)} chunks, {len(audio)} bytes = {seconds:.2f}s audio")
    line(INFO, f"TTS: connect {connect_ms:.0f} ms, first audio byte {ttfb:.0f} ms")
    if chunks:
        first = chunks[0]
        line(PASS if first.sample_rate == settings.deepgram_tts_sample_rate else FAIL,
             f"TTS: chunk declares {first.encoding} @ {first.sample_rate} Hz, "
             f"timings_estimated={first.timings_estimated}")
    return bytes(audio), chunks


async def step_loop(settings, tts_audio: bytes) -> list:
    """Feed the synthesised speech back through real STT."""
    bus = EventBus()
    events: list = []
    bus.subscribe_all(lambda event: events.append(event))

    stt = DeepgramStt(
        bus, "live-smoke", settings,
        keyterms=["outreach", "budget holders", "pipeline"],
        get_turn_id=lambda: "turn-live-1",
    )
    await stt.start()
    line(INFO, f"STT: open, keyterms={stt.keyterms}")

    # 24 kHz synthesised audio → the 16 kHz the socket was told to expect.
    converter = PcmStreamConverter(
        source_rate=settings.deepgram_tts_sample_rate,
        target_rate=settings.deepgram_sample_rate,
    )
    # 20 ms frames, as the browser will send.
    frame = int(settings.deepgram_tts_sample_rate * 0.02) * 2
    spoke_at = time.monotonic()
    for offset in range(0, len(tts_audio), frame):
        converted = converter.push_pcm16(tts_audio[offset : offset + frame])
        if converted:
            await stt.send_audio(converted)
        await asyncio.sleep(0.005)  # faster than real time, still ordered

    # Trailing silence so endpointing fires, as a real pause would.
    silence = b"\x00\x00" * int(settings.deepgram_sample_rate * 0.02)
    for _ in range(60):
        await stt.send_audio(silence)
        await asyncio.sleep(0.005)

    for _ in range(100):
        if stt.stats.turns_completed:
            break
        await asyncio.sleep(0.1)

    await bus.drain()
    await stt.close()

    partials = [e for e in events if e.type == "partial"]
    finals = [e for e in events if e.type == "final_transcript"]
    line(INFO, f"STT: resampled {converter.describe()}")
    line(PASS if partials else FAIL, f"STT: {len(partials)} interim partials")
    line(PASS if len(finals) == 1 else FAIL,
         f"STT: {len(finals)} committed answer(s) from "
         f"{stt.stats.segments_finalised} finalised segment(s) "
         f"— expected exactly 1")
    if finals:
        heard = finals[0].text
        latency = (time.monotonic() - spoke_at) * 1000
        line(INFO, f"STT: heard {heard!r}")
        line(INFO, f"STT: boundary={finals[0].boundary} "
                   f"confidence={finals[0].confidence} "
                   f"words={len(finals[0].word_timings)} "
                   f"timings_estimated={finals[0].timings_estimated}")
        spoken_words = {w.strip(".,").casefold() for w in SPOKEN.split()}
        heard_words = {w.strip(".,").casefold() for w in heard.split()}
        overlap = len(spoken_words & heard_words) / max(1, len(spoken_words))
        line(PASS if overlap > 0.6 else FAIL,
             f"LOOP: {overlap:.0%} word overlap between spoken and transcribed")
        line(INFO, f"LOOP: send-to-committed-answer {latency:.0f} ms "
                   "(not a user-latency figure: audio was pushed faster than "
                   "real time)")
    return finals


async def step_follow_up(settings, heard: str) -> None:
    """A real Groq call: does the follow-up reference what was said?"""
    from interview.llm.client import GroqModelClient

    bus = EventBus()
    client = GroqModelClient(bus=bus, session_id="live-smoke")
    spec = role_for(
        InterviewRound.HIRING_MANAGER, family=RoleFamily.SALES,
        seniority=Seniority.SENIOR,
    )
    pack = load_pack("manager-core")
    ids = pack.spine_ids()
    state = GuardState(
        pack=pack, intensity="realistic", covered=(ids[0],),
        outstanding=tuple(ids[1:]), depth_on_current=0, time_remaining_s=420.0,
        claim_ids=frozenset(), claim_competency={}, transcript_texts=(heard,),
        asked_questions=(pack.spine[0].text,),
    )
    proposer = ModelProposer(spec, client)
    started = time.monotonic()
    move = await proposer.propose(
        ProposalContext(spec=spec, state=state, last_answer=heard,
                        transcript=(heard,), time_remaining_s=420.0)
    )
    elapsed = (time.monotonic() - started) * 1000

    used_model = proposer.fallbacks_used == 0
    line(PASS if used_model else FAIL,
         f"LLM: {'model proposal accepted' if used_model else 'FELL BACK to deterministic'} "
         f"in {elapsed:.0f} ms")
    line(INFO, f"LLM: action={move.action} competency={move.target_competency}")
    line(INFO, f"LLM: ASKS {move.question!r}")
    line(INFO, f"LLM: why  {move.decision_summary!r}")

    # Answer-dependent? The question should pick up the candidate's own terms.
    terms = ("outreach", "budget", "holder", "sales", "end user")
    hits = [term for term in terms if term in move.question.casefold()]
    line(PASS if hits else FAIL,
         f"LLM: follow-up references the answer (matched {hits or 'nothing'})")
    await bus.drain()


async def step_barge_in(settings) -> None:
    """Clear mid-utterance; confirm late chunks are dropped."""
    bus = EventBus()
    chunks: list = []
    bus.subscribe("tts_chunk", lambda event: chunks.append(event))
    tts = DeepgramTts(bus, "live-smoke", settings)
    await tts.start()

    async def speak() -> None:
        await tts.synthesise(
            "Let me ask you something much longer so that there is plenty of "
            "audio still being generated when the candidate decides to "
            "interrupt me in the middle of this sentence.",
            "utt-barge", "turn-barge",
        )

    task = asyncio.create_task(speak())
    await asyncio.sleep(0.6)
    # Drain first: `tts_chunk` subscribers run as tasks, so len(chunks) lags
    # the emissions. Sampling before the drain counts a pre-cancel chunk as a
    # post-cancel one and reports a leak that did not happen.
    await bus.drain()
    cancelled_at = len(chunks)
    await tts.cancel("utt-barge")
    await asyncio.sleep(1.2)
    await bus.drain()
    task.cancel()
    with __import__("contextlib").suppress(Exception):
        await task
    after = len(chunks)
    await tts.close()

    line(PASS if tts.stats.clears >= 1 else FAIL,
         f"BARGE: provider Clear sent ({tts.stats.clears})")
    line(PASS if after == cancelled_at else FAIL,
         f"BARGE: chunks forwarded after Clear = {after - cancelled_at} "
         f"(expected 0); provider sent {tts.stats.dropped_late_chunks} late, dropped")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true",
                        help="confirm real, billable provider calls")
    args = parser.parse_args()
    if not args.yes:
        print(__doc__)
        print("Refusing to make billable calls without --yes.")
        return 2

    settings = get_settings()
    print("=" * 68)
    print("  LIVE voice pipeline smoke test")
    print("=" * 68)
    line(INFO, f"env={settings.app_env} "
               f"deepgram={'set' if settings.has_deepgram else 'MISSING'} "
               f"groq={'set' if settings.has_groq else 'MISSING'}")
    if not settings.has_deepgram:
        line(FAIL, "DEEPGRAM_API_KEY is not set")
        return 1

    print("\n[1] Text to speech")
    audio, _ = await step_tts(settings)
    if not audio:
        return 1

    print("\n[2] Closed loop: synthesised speech -> resample -> transcription")
    finals = await step_loop(settings, audio)

    print("\n[3] Answer-dependent follow-up (Groq)")
    if settings.has_groq:
        heard = finals[0].text if finals else SPOKEN
        await step_follow_up(settings, heard)
    else:
        line(FAIL, "GROQ_API_KEY is not set; skipped")

    print("\n[4] Barge-in")
    await step_barge_in(settings)

    print("\n" + "=" * 68)
    print("  Browser-only steps NOT covered here: microphone capture,")
    print("  playback queue scheduling, audio-clock acknowledgement.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
