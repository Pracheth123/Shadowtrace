"""
Stage 14 — the connected voice path.

These tests drive `VoiceSession` and the real server WebSocket against a local
fake Deepgram, so they verify the *wiring*, which is what was missing: the
adapters were already tested in isolation while the server injected
`FakeSpeakPort`, answered every `tts_chunk` with silence, and discarded
microphone frames.

Offline and deterministic. The live counterpart is `tools/live_voice_smoke.py`.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlsplit

import numpy as np
import pytest
from websockets.asyncio.server import serve

from interview.config import Settings
from interview.events.bus import EventBus
from interview.transport.audio import float32_to_pcm16, pcm16_to_float32
from interview.transport.voice_session import (
    AUDIO_QUEUE_FRAMES,
    VoiceSession,
    VoiceUnavailable,
)


def settings(**overrides) -> Settings:
    base = {
        "APP_ENV": "test",
        "ALLOW_MOCK_PROVIDERS": True,
        "DEEPGRAM_API_KEY": "test-key-not-real",
        "GROQ_API_KEY": "test-key-not-real",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class FakeDeepgram:
    """Serves both /listen and /speak on one port, chosen by path."""

    def __init__(self, listen_script: list[dict] | None = None,
                 chunks_per_flush: int = 3, chunk_bytes: int = 960):
        self.listen_script = listen_script or []
        self.chunks_per_flush = chunks_per_flush
        self.chunk_bytes = chunk_bytes
        self.audio_in = bytearray()
        self.listen_query: list[tuple[str, str]] = []
        self.speak_query: list[tuple[str, str]] = []
        self.speak_received: list[dict] = []
        self.speak_conn = None
        self.listen_open = asyncio.Event()

    async def handler(self, connection):
        parsed = urlsplit(connection.request.path)
        query = parse_qsl(parsed.query)
        if "listen" in parsed.path:
            self.listen_query = query
            self.listen_open.set()
            for message in self.listen_script:
                await connection.send(json.dumps(message))
                await asyncio.sleep(0)
            try:
                async for frame in connection:
                    if isinstance(frame, (bytes, bytearray)):
                        self.audio_in.extend(frame)
            except Exception:
                pass
            return

        self.speak_query = query
        self.speak_conn = connection
        try:
            async for frame in connection:
                message = json.loads(frame)
                self.speak_received.append(message)
                kind = message.get("type")
                if kind == "Flush":
                    for _ in range(self.chunks_per_flush):
                        await connection.send(b"\x11\x00" * (self.chunk_bytes // 2))
                        await asyncio.sleep(0)
                    await connection.send(
                        json.dumps({"type": "Flushed", "sequence_id": 0})
                    )
                elif kind == "Clear":
                    await connection.send(
                        json.dumps({"type": "Cleared", "sequence_id": 0})
                    )
                elif kind == "Close":
                    await connection.close()
                    return
        except Exception:
            pass


@asynccontextmanager
async def fake_deepgram(**kwargs):
    fake = FakeDeepgram(**kwargs)
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield fake, f"ws://127.0.0.1:{port}"


class Wire:
    """Captures what the browser would receive."""

    def __init__(self):
        self.text: list[dict] = []
        self.binary: list[bytes] = []

    async def send_text(self, payload: dict) -> None:
        self.text.append(payload)

    async def send_bytes(self, payload: bytes) -> None:
        self.binary.append(payload)

    def of(self, kind: str) -> list[dict]:
        return [item for item in self.text if item.get("type") == kind]


def results(transcript, *, is_final=False, speech_final=False, start=0.0,
            duration=1.0, words=None) -> dict:
    return {
        "type": "Results",
        "is_final": is_final,
        "speech_final": speech_final,
        "start": start,
        "duration": duration,
        "channel": {
            "alternatives": [
                {
                    "transcript": transcript,
                    "confidence": 0.95,
                    "words": [
                        {"word": w, "punctuated_word": w, "start": s, "end": e}
                        for w, s, e in (words or [])
                    ],
                }
            ]
        },
    }


@asynccontextmanager
async def voice_session(url, bus=None, config=None, **kwargs):
    wire = Wire()
    session = VoiceSession(
        bus or EventBus(), "s-14", config or settings(), wire.send_text, wire.send_bytes,
        stt_url_override=f"{url}/listen",
        tts_url_override=f"{url}/speak",
        **kwargs,
    )
    try:
        yield session, wire
    finally:
        await session.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Microphone frames reach STT
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_microphone_frames_reach_the_provider_resampled() -> None:
    """
    The path that used to count frames as "unsolicited binary" and drop them.
    48 kHz browser audio must arrive at the provider as 16 kHz.
    """
    async with fake_deepgram() as (fake, url):
        async with voice_session(url) as (session, wire):
            await session.start(source_sample_rate=48000, channels=1)
            await fake.listen_open.wait()

            # One second of 48 kHz tone, in 20 ms frames as the worklet sends.
            tone = (0.4 * np.sin(2 * np.pi * 440 * np.arange(48000) / 48000)).astype(
                np.float32
            )
            frame = 960  # 20 ms at 48 kHz
            for offset in range(0, tone.size, frame):
                await session.push_audio(float32_to_pcm16(tone[offset : offset + frame]))
            for _ in range(60):
                if len(fake.audio_in) >= 31000:
                    break
                await asyncio.sleep(0.02)

            # 1 s at 16 kHz mono PCM16 = 32000 bytes, not 96000.
            assert 30000 <= len(fake.audio_in) <= 33000, len(fake.audio_in)
            # And it is still a 440 Hz tone after resampling.
            samples = pcm16_to_float32(bytes(fake.audio_in))
            spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
            peak = np.fft.rfftfreq(samples.size, 1 / 16000)[spectrum.argmax()]
            assert peak == pytest.approx(440.0, abs=5.0)

            declared = dict(fake.listen_query)
            assert declared["encoding"] == "linear16"
            assert declared["sample_rate"] == "16000"
            assert session.stats.frames_in == 50


@pytest.mark.asyncio
async def test_mute_and_push_to_talk_stop_real_outgoing_audio() -> None:
    """
    Push-to-talk previously only changed how barge-in was interpreted; the
    candidate's audio kept being transcribed while the UI implied otherwise.
    """
    async with fake_deepgram() as (fake, url):
        async with voice_session(url) as (session, wire):
            await session.start(source_sample_rate=16000)
            await fake.listen_open.wait()
            frame = b"\x01\x00" * 320

            session.set_muted(True)
            for _ in range(10):
                await session.push_audio(frame)
            await asyncio.sleep(0.1)
            assert len(fake.audio_in) == 0
            assert session.stats.frames_dropped_muted == 10
            assert session.stats.frames_in == 0

            session.set_muted(False)
            for _ in range(10):
                await session.push_audio(frame)
            for _ in range(40):
                if fake.audio_in:
                    break
                await asyncio.sleep(0.02)
            assert len(fake.audio_in) > 0


@pytest.mark.asyncio
async def test_backpressure_drops_oldest_and_warns_once() -> None:
    async with fake_deepgram() as (fake, url):
        async with voice_session(url) as (session, wire):
            await session.start(source_sample_rate=16000)
            frame = b"\x01\x00" * 320
            # Flood without yielding, so the pump cannot drain.
            for _ in range(AUDIO_QUEUE_FRAMES * 3):
                await session.push_audio(frame)
            assert session.stats.frames_dropped_backpressure > 0
            warnings = wire.of("voice_warning")
            assert warnings and warnings[0]["reason"] == "backpressure"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Answer assembly and duplicate endpoints
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_committed_answer_from_many_segments() -> None:
    script = [
        results("I increased sales", start=0.0, duration=0.6),
        results("I increased sales by changing", is_final=True, start=0.0,
                duration=1.0, words=[("I", 0.0, 0.1), ("increased", 0.1, 0.5)]),
        results("our outreach", is_final=True, start=1.0, duration=0.8,
                words=[("our", 1.0, 1.2), ("outreach", 1.2, 1.8)]),
        results("to budget holders", is_final=True, speech_final=True, start=1.8,
                duration=0.9, words=[("to", 1.8, 1.9), ("holders", 2.2, 2.7)]),
        # Duplicate boundary for the same utterance.
        {"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 2.7},
    ]
    bus = EventBus()
    events: list = []
    bus.subscribe_all(lambda event: events.append(event))
    async with fake_deepgram(listen_script=script) as (fake, url):
        # Shortest allowed answer-end silence, so the answer closes quickly.
        fast = settings(ANSWER_END_SILENCE_MS=500, ANSWER_END_EXTENDED_MS=500)
        async with voice_session(
            url, bus=bus, config=fast, get_turn_id=lambda: "t-1"
        ) as (session, wire):
            await session.start(source_sample_rate=16000)
            await asyncio.sleep(0.9)
            await bus.drain()

    finals = [e for e in events if e.type == "final_transcript"]
    assert len(finals) == 1, f"expected one answer, got {len(finals)}"
    assert finals[0].text == (
        "I increased sales by changing our outreach to budget holders"
    )
    assert finals[0].turn_id == "t-1"
    # Provider pauses only start the silence clock; silence ends the answer.
    assert finals[0].boundary == "timeout"
    assert len(finals[0].word_timings) == 6
    partials = [e for e in events if e.type == "partial"]
    assert partials, "interim transcripts must reach the browser"


@pytest.mark.asyncio
async def test_client_finalise_is_idempotent() -> None:
    script = [
        results("that is my answer", is_final=True, start=0.0, duration=1.0,
                words=[("that", 0.0, 0.2)]),
    ]
    bus = EventBus()
    events: list = []
    bus.subscribe_all(lambda event: events.append(event))
    async with fake_deepgram(listen_script=script) as (fake, url):
        async with voice_session(url, bus=bus) as (session, wire):
            await session.start(source_sample_rate=16000)
            await asyncio.sleep(0.25)
            await session.finalise_turn("client")
            await session.finalise_turn("client")
            await session.finalise_turn("client")
            await bus.drain()
    assert len([e for e in events if e.type == "final_transcript"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. Real TTS bytes reach the browser, with the right format
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesised_audio_reaches_the_browser_with_metadata() -> None:
    """
    The server used to answer every `tts_chunk` with a burst of zeros. Real
    bytes must arrive, each preceded by metadata naming the encoding and rate.
    """
    async with fake_deepgram(chunks_per_flush=3, chunk_bytes=960) as (fake, url):
        async with voice_session(url) as (session, wire):
            await session.start(source_sample_rate=16000)
            await session.synthesise("Tell me about your background.", "utt-1", "t-1")

    chunks = wire.of("audio_chunk")
    assert len(chunks) == 3
    assert len(wire.binary) == 3
    # Not silence.
    assert any(byte != 0 for byte in wire.binary[0])
    # The browser cannot decode without these; 24 kHz is not the old 16 kHz
    # assumption baked into the client.
    for index, meta in enumerate(chunks, start=1):
        assert meta["encoding"] == "linear16"
        assert meta["sample_rate"] == 24000
        assert meta["seq"] == index
        assert meta["utterance_id"] == "utt-1"
        assert meta["timings_estimated"] is True
    # Metadata byte counts match the frames that followed.
    assert [meta["bytes"] for meta in chunks] == [len(b) for b in wire.binary]
    # Begin/end bracket the audio.
    assert wire.of("utterance_begin")[0]["sample_rate"] == 24000
    assert wire.of("utterance_end")[0]["utterance_id"] == "utt-1"
    # One Speak per sentence, exactly one Flush.
    assert len([m for m in fake.speak_received if m.get("type") == "Flush"]) == 1
    assert dict(fake.speak_query)["sample_rate"] == "24000"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Interruption: both halves
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_barge_in_clears_provider_and_tells_the_browser_to_stop() -> None:
    async with fake_deepgram(chunks_per_flush=2) as (fake, url):
        async with voice_session(url) as (session, wire):
            await session.start(source_sample_rate=16000)
            await session.synthesise("A fairly long question here.", "utt-1", "t-1")
            before = len(wire.binary)

            await session.barge_in("utt-1")
            # The Clear travels over the socket, so give it a moment to land.
            for _ in range(50):
                if any(m.get("type") == "Clear" for m in fake.speak_received):
                    break
                await asyncio.sleep(0.02)

            # Provider half.
            assert any(m.get("type") == "Clear" for m in fake.speak_received)
            # Browser half — Clear cannot reach audio already delivered.
            stops = wire.of("stop_playback")
            assert stops and stops[0]["utterance_id"] == "utt-1"
            assert stops[0]["reason"] == "barge_in"

            # Late audio for the cancelled utterance must not be forwarded.
            await fake.speak_conn.send(b"\x22\x00" * 480)
            await asyncio.sleep(0.15)
            assert len(wire.binary) == before
            assert session.is_cancelled("utt-1")
            assert session.stats.barge_ins == 1


@pytest.mark.asyncio
async def test_capture_keeps_running_through_a_barge_in() -> None:
    """The candidate is mid-sentence; that sentence is the next answer."""
    async with fake_deepgram() as (fake, url):
        async with voice_session(url) as (session, wire):
            await session.start(source_sample_rate=16000)
            await fake.listen_open.wait()
            await session.barge_in("utt-1")
            assert session.muted is False
            for _ in range(5):
                await session.push_audio(b"\x01\x00" * 320)
            for _ in range(40):
                if fake.audio_in:
                    break
                await asyncio.sleep(0.02)
            assert len(fake.audio_in) > 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Failures and cleanup
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_credentials_refuse_rather_than_run_silently() -> None:
    wire = Wire()
    session = VoiceSession(
        EventBus(), "s", settings(DEEPGRAM_API_KEY=""), wire.send_text,
        wire.send_bytes,
    )
    with pytest.raises(VoiceUnavailable, match="DEEPGRAM_API_KEY"):
        await session.start(source_sample_rate=16000)
    await session.close()


@pytest.mark.asyncio
async def test_unreachable_provider_is_surfaced() -> None:
    wire = Wire()
    session = VoiceSession(
        EventBus(), "s", settings(), wire.send_text, wire.send_bytes,
        stt_url_override="ws://127.0.0.1:1/listen",
        tts_url_override="ws://127.0.0.1:1/speak",
    )
    with pytest.raises(VoiceUnavailable):
        await session.start(source_sample_rate=16000)
    await session.close()


@pytest.mark.asyncio
async def test_a_bad_sample_rate_is_refused() -> None:
    wire = Wire()
    session = VoiceSession(
        EventBus(), "s", settings(), wire.send_text, wire.send_bytes,
    )
    with pytest.raises(VoiceUnavailable, match="sample rate"):
        await session.start(source_sample_rate=0)
    await session.close()


@pytest.mark.asyncio
async def test_close_releases_both_sockets_and_the_pump() -> None:
    async with fake_deepgram() as (fake, url):
        wire = Wire()
        session = VoiceSession(
            EventBus(), "s", settings(), wire.send_text, wire.send_bytes,
            stt_url_override=f"{url}/listen", tts_url_override=f"{url}/speak",
        )
        await session.start(source_sample_rate=16000)
        assert session._pump_task is not None
        await session.close()
        assert session._pump_task is None
        assert session.stt._ws is None
        assert session.tts._ws is None
        # Idempotent.
        await session.close()


@pytest.mark.asyncio
async def test_voice_ready_reports_the_real_capture_conversion() -> None:
    async with fake_deepgram() as (fake, url):
        async with voice_session(url) as (session, wire):
            await session.start(source_sample_rate=44100, channels=2)
            ready = wire.of("voice_ready")[0]
            assert ready["stt_sample_rate"] == 16000
            assert ready["tts_sample_rate"] == 24000
            assert ready["capture"]["source_rate"] == 44100
            assert ready["capture"]["channels"] == 2
            assert ready["capture"]["resampled"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 6. The server actually wires it
# ─────────────────────────────────────────────────────────────────────────────


def test_server_no_longer_discards_microphone_frames() -> None:
    """Guards against the exact regression this stage fixed."""
    from pathlib import Path

    body = Path("src/interview/server.py").read_text(encoding="utf-8")
    # Frames are forwarded when a voice session exists.
    assert "push_audio(message[\"bytes\"])" in body
    # The silence burst is confined to the explicit mock lane.
    assert "if ctx.voice is None and live.config.lane != \"text\":" in body
    # Real provider mode refuses rather than faking.
    assert "Voice interviews need a speech provider" in body
    # Barge-in reaches the provider and the browser.
    assert "await ctx.voice.barge_in(" in body
    # Push-to-talk gates the real stream.
    assert "voice.set_muted(push_to_talk)" in body


def test_panel_voices_are_valid_deepgram_ids() -> None:
    from interview.server import PANEL_VOICES

    assert PANEL_VOICES, "panel personas need voices"
    for persona, voice in PANEL_VOICES.items():
        assert voice.startswith("aura-"), (persona, voice)
    # The old OpenAI names would be rejected by the provider.
    assert not {"nova", "alloy", "onyx"} & set(PANEL_VOICES.values())


def test_client_decodes_with_metadata_not_a_hardcoded_rate() -> None:
    from pathlib import Path

    playback = Path("client/src/lib/audio-playback.ts").read_text(encoding="utf-8")
    # Rate comes from the chunk metadata.
    assert "meta.sampleRate" in playback
    assert "createBuffer(1, samples.length, meta.sampleRate)" in playback
    # No hard-coded rate in the playback *code*. Comments may mention 16000
    # because they explain the bug that is being prevented, so only executable
    # lines are checked.
    code = chr(10).join(
        line for line in playback.splitlines()
        if not line.lstrip().startswith(("*", "//", "/*"))
    )
    assert "16000" not in code, "playback must take its rate from the metadata"
    # Progress comes from the audio clock, not from arrival.
    assert "context.currentTime" in playback

    capture = Path("client/src/lib/audio-capture.ts").read_text(encoding="utf-8")
    # Echo cancellation, or the interviewer interrupts itself.
    assert "echoCancellation: true" in capture
    # The real device rate is reported, never assumed.
    assert "sampleRate: context.sampleRate" in capture
    # No MediaRecorder: the socket is configured for raw PCM.
    assert "MediaRecorder" not in capture
