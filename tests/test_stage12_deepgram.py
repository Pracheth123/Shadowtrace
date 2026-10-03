"""
Stage 12 — Deepgram voice integration, verified offline.

These tests stand up a **real local WebSocket server** that speaks the
documented Deepgram `/v1/listen` and `/v1/speak` protocols, and point the real
adapters at it. So the wire format, the query string, the turn-boundary logic
and the Flush/Clear lifecycle are all exercised for real — no credentials, no
network, no mocking of the adapter under test.

What they deliberately do NOT prove: that Deepgram's live service behaves like
this fake. That needs a key and is listed as an outstanding live check in
docs/decisions/stage12_voice_providers.md.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlsplit

import numpy as np
import pytest
import websockets
from websockets.asyncio.server import serve

from interview.config import Settings
from interview.events.bus import EventBus
from interview.transport.audio import (
    PcmStreamConverter,
    pcm16_duration_ms,
    pcm16_to_float32,
)
from interview.transport.deepgram_stt import (
    DeepgramStt,
    SttUnavailable,
    build_keyterms,
)
from interview.transport.deepgram_tts import (
    DeepgramTts,
    FlushBudget,
    TtsUnavailable,
    estimate_word_timestamps,
    split_sentences,
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


# ─────────────────────────────────────────────────────────────────────────────
# Fake Deepgram servers
# ─────────────────────────────────────────────────────────────────────────────


class FakeListen:
    """Records the handshake, then replays a scripted result sequence."""

    def __init__(self, script: list[dict], *, fail: bool = False):
        self.script = script
        self.fail = fail
        self.query: list[tuple[str, str]] = []
        self.headers: dict = {}
        self.audio_bytes = 0
        self.control: list[dict] = []

    async def handler(self, connection):
        self.query = parse_qsl(urlsplit(connection.request.path).query)
        # websockets normalises header names to lower case.
        self.headers = {k.lower(): v for k, v in connection.request.headers.raw_items()}
        if self.fail:
            await connection.close(code=1011, reason="simulated provider failure")
            return
        for message in self.script:
            await connection.send(json.dumps(message))
            await asyncio.sleep(0)
        # Then drain whatever the client sends until it closes.
        try:
            async for frame in connection:
                if isinstance(frame, (bytes, bytearray)):
                    self.audio_bytes += len(frame)
                else:
                    self.control.append(json.loads(frame))
        except Exception:
            pass


class FakeSpeak:
    """Answers Speak/Flush/Clear with audio frames and acknowledgements."""

    def __init__(self, *, chunk_bytes: int = 960, chunks_per_flush: int = 3):
        self.chunk_bytes = chunk_bytes
        self.chunks_per_flush = chunks_per_flush
        self.query: list[tuple[str, str]] = []
        self.headers: dict = {}
        self.received: list[dict] = []
        self.connection = None

    async def handler(self, connection):
        self.query = parse_qsl(urlsplit(connection.request.path).query)
        self.headers = {k.lower(): v for k, v in connection.request.headers.raw_items()}
        self.connection = connection
        async for frame in connection:
            message = json.loads(frame)
            self.received.append(message)
            kind = message.get("type")
            if kind == "Flush":
                for _ in range(self.chunks_per_flush):
                    await connection.send(b"\x01\x00" * (self.chunk_bytes // 2))
                    await asyncio.sleep(0)
                await connection.send(json.dumps({"type": "Flushed", "sequence_id": 0}))
            elif kind == "Clear":
                await connection.send(json.dumps({"type": "Cleared", "sequence_id": 0}))
            elif kind == "Close":
                await connection.close()
                return


@asynccontextmanager
async def running(handler):
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


def results(
    transcript: str,
    *,
    is_final: bool = False,
    speech_final: bool = False,
    start: float = 0.0,
    duration: float = 1.0,
    confidence: float = 0.95,
    words: list[tuple[str, float, float]] | None = None,
) -> dict:
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
                    "confidence": confidence,
                    "words": [
                        {"word": w, "punctuated_word": w, "start": s, "end": e}
                        for w, s, e in (words or [])
                    ],
                }
            ]
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Audio format handling
# ─────────────────────────────────────────────────────────────────────────────


def test_resampling_is_exact_across_chunk_seams() -> None:
    """A tone split into irregular chunks must resample without drift."""
    source, target = 48000, 16000
    t = np.arange(source) / source
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    converter = PcmStreamConverter(source, target)
    out = b""
    index = 0
    for size in (1000, 777, 4096, 333, 10001, 31793):
        chunk = tone[index : index + size]
        if chunk.size == 0:
            break
        out += converter.push_float32(chunk)
        index += chunk.size

    assert converter.frames_in == source
    # Exactly one second of 16 kHz audio, not 15999 or 16001.
    assert converter.frames_out == target
    assert pcm16_duration_ms(out, target) == pytest.approx(1000.0, abs=1.0)

    # Still a 440 Hz tone: phase continuity held across every seam.
    samples = pcm16_to_float32(out)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    peak = np.fft.rfftfreq(samples.size, 1 / target)[spectrum.argmax()]
    assert peak == pytest.approx(440.0, abs=2.0)
    # A restarted resampler would show a step discontinuity at each seam; the
    # true slope of this tone is ~0.086 per sample.
    assert float(np.abs(np.diff(samples)).max()) < 0.12


def test_odd_byte_chunk_is_carried_not_corrupted() -> None:
    """A chunk ending mid-frame must not byte-swap everything after it."""
    converter = PcmStreamConverter(16000, 16000)
    pcm = np.arange(100, dtype="<i2").tobytes()
    first = converter.push_pcm16(pcm[:51])  # odd length, splits a frame
    second = converter.push_pcm16(pcm[51:])
    assert len(first) == 50  # 25 whole frames
    recovered = np.frombuffer(first + second, dtype="<i2")
    assert np.array_equal(recovered, np.arange(100, dtype="<i2"))


def test_stereo_is_downmixed_not_truncated() -> None:
    converter = PcmStreamConverter(16000, 16000, channels=2)
    left = np.zeros(10, dtype=np.float32)
    right = np.ones(10, dtype=np.float32) * 0.5
    interleaved = np.empty(20, dtype=np.float32)
    interleaved[0::2] = left
    interleaved[1::2] = right
    out = pcm16_to_float32(converter.push_float32(interleaved))
    assert out.size == 10
    assert np.allclose(out, 0.25, atol=1e-3)  # mean of the two channels


def test_converter_reports_what_it_did() -> None:
    converter = PcmStreamConverter(44100, 16000)
    converter.push_float32(np.zeros(4410, dtype=np.float32))
    described = converter.describe()
    assert described["resampled"] is True
    assert described["method"] == "linear-interpolation"
    assert described["source_rate"] == 44100 and described["target_rate"] == 16000
    assert PcmStreamConverter(16000, 16000).describe()["method"] == "none"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Keyterm configuration (Nova-3)
# ─────────────────────────────────────────────────────────────────────────────


def test_keyterms_strip_nova2_weight_syntax() -> None:
    """`keyterm` rejects weights; sending them would become literal text."""
    assert build_keyterms(["Kafka:0.15"], max_terms=10) == ["Kafka"]


def test_keyterms_dedupe_and_respect_the_token_budget() -> None:
    assert build_keyterms(["Kafka", "kafka", "KAFKA"], max_terms=10) == ["Kafka"]
    # Budget counts tokens across all terms, not terms.
    trimmed = build_keyterms(["one two three", "four five", "six"], max_terms=10, budget=5)
    assert trimmed == ["one two three", "four five"]
    assert build_keyterms(["a", "b", "c"], max_terms=2) == ["a", "b"]
    assert build_keyterms(["", "   "], max_terms=5) == []


@pytest.mark.asyncio
async def test_handshake_declares_format_and_repeats_keyterm() -> None:
    fake = FakeListen([])
    async with running(fake.handler) as url:
        bus = EventBus()
        stt = DeepgramStt(
            bus,
            "s1",
            settings(),
            keyterms=["Kafka", "SystemVerilog", "back pressure"],
            url_override=url,
        )
        await stt.start()
        await asyncio.sleep(0.05)
        await stt.close()

    query = fake.query
    as_dict = dict(query)
    assert as_dict["model"] == "nova-3"
    # Format is declared explicitly, never left for the provider to guess.
    assert as_dict["encoding"] == "linear16"
    assert as_dict["sample_rate"] == "16000"
    assert as_dict["channels"] == "1"
    assert as_dict["interim_results"] == "true"
    # UtteranceEnd is only delivered when vad_events is on.
    assert as_dict["vad_events"] == "true"
    assert as_dict["endpointing"] == "300"
    assert as_dict["utterance_end_ms"] == "1000"
    # Nova-2's `keywords` must not appear at all.
    assert "keywords" not in as_dict
    # keyterm is repeated once per term.
    assert [value for key, value in query if key == "keyterm"] == [
        "Kafka",
        "SystemVerilog",
        "back pressure",
    ]


@pytest.mark.asyncio
async def test_api_key_travels_in_a_server_header_only() -> None:
    fake = FakeListen([])
    async with running(fake.handler) as url:
        stt = DeepgramStt(EventBus(), "s1", settings(), url_override=url)
        await stt.start()
        await asyncio.sleep(0.05)
        await stt.close()
    # The key is in the server→provider Authorization header...
    assert fake.headers.get("authorization") == "Token test-key-not-real"
    # ...and never in the URL, where it could be logged or leak to a client.
    assert "test-key-not-real" not in str(fake.query)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Turn boundaries — exactly one answer per candidate turn
# ─────────────────────────────────────────────────────────────────────────────


async def _collect(script: list[dict], *, turn_id: str = "turn-1"):
    fake = FakeListen(script)
    events: list = []
    async with running(fake.handler) as url:
        bus = EventBus()
        bus.subscribe_all(lambda event: events.append(event))
        stt = DeepgramStt(
            bus, "s1", settings(), url_override=url, get_turn_id=lambda: turn_id
        )
        await stt.start()
        await asyncio.sleep(0.25)
        await bus.drain()
        await stt.close()
    return stt, events


@pytest.mark.asyncio
async def test_many_final_segments_make_one_answer() -> None:
    """
    The core fix. Three `is_final` segments and one `speech_final` is one
    answer, not three. The old adapter emitted a completed answer per segment,
    which made the interviewer reply mid-sentence.
    """
    script = [
        results("I owned", start=0.0, duration=0.5),
        results("I owned the payments", start=0.0, duration=1.0),
        results("I owned the payments pipeline", is_final=True, start=0.0, duration=1.5,
                words=[("I", 0.0, 0.1), ("owned", 0.1, 0.4),
                       ("the", 0.4, 0.5), ("payments", 0.5, 1.0),
                       ("pipeline", 1.0, 1.5)]),
        results("from design", start=1.5, duration=0.5),
        results("from design through launch", is_final=True, start=1.5, duration=1.0,
                words=[("from", 1.5, 1.7), ("design", 1.7, 2.0),
                       ("through", 2.0, 2.2), ("launch", 2.2, 2.5)]),
        results("this quarter", is_final=True, speech_final=True, start=2.5, duration=0.6,
                words=[("this", 2.5, 2.7), ("quarter", 2.7, 3.1)]),
    ]
    stt, events = await _collect(script)

    finals = [e for e in events if e.type == "final_transcript"]
    assert len(finals) == 1, f"expected one answer, got {len(finals)}"
    assert finals[0].text == "I owned the payments pipeline from design through launch this quarter"
    # Word timings from every segment are preserved, in order.
    assert [w.word for w in finals[0].word_timings][:3] == ["I", "owned", "the"]
    assert len(finals[0].word_timings) == 11
    assert finals[0].boundary == "speech_final"
    # Deepgram reports real per-word times on finalised segments.
    assert finals[0].timings_estimated is False
    assert finals[0].confidence == pytest.approx(0.95)
    assert stt.stats.segments_finalised == 3
    assert stt.stats.turns_completed == 1


@pytest.mark.asyncio
async def test_interim_results_become_partials_with_revisions() -> None:
    """Speculation needs partials, and revisions are what mark stability."""
    script = [
        results("I owned", start=0.0, duration=0.4),
        results("I owned the", start=0.0, duration=0.6),
        results("I owned the pipeline", start=0.0, duration=0.9),
        results("I owned the pipeline", is_final=True, speech_final=True,
                start=0.0, duration=1.0, words=[("I", 0.0, 0.2)]),
    ]
    _, events = await _collect(script)
    partials = [e for e in events if e.type == "partial"]
    assert len(partials) == 3
    assert [p.revision for p in partials] == [0, 1, 2]
    assert partials[-1].text == "I owned the pipeline"
    assert partials[0].confidence == pytest.approx(0.95)
    assert all(p.turn_id == "turn-1" for p in partials)


@pytest.mark.asyncio
async def test_partials_continue_after_a_finalised_segment() -> None:
    """
    A partial mid-answer must carry the whole answer so far, not just the
    newest segment — otherwise speculation reasons about a fragment.
    """
    script = [
        results("I owned the pipeline", is_final=True, start=0.0, duration=1.0,
                words=[("I", 0.0, 0.2)]),
        results("and I measured", start=1.0, duration=0.5),
    ]
    _, events = await _collect(script)
    partials = [e for e in events if e.type == "partial"]
    assert partials[-1].text == "I owned the pipeline and I measured"
    # Revision restarts after a segment is finalised.
    assert partials[-1].revision == 0


@pytest.mark.asyncio
async def test_utterance_end_closes_a_turn_endpointing_missed() -> None:
    script = [
        results("I shipped it last week", is_final=True, start=0.0, duration=1.2,
                words=[("I", 0.0, 0.1), ("shipped", 0.1, 0.5)]),
        {"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 1.2},
    ]
    stt, events = await _collect(script)
    finals = [e for e in events if e.type == "final_transcript"]
    assert len(finals) == 1
    assert finals[0].boundary == "utterance_end"
    assert stt.stats.boundary_counts == {"utterance_end": 1}


@pytest.mark.asyncio
async def test_both_boundary_signals_still_make_one_answer() -> None:
    """
    `speech_final` then `UtteranceEnd` for the same utterance is normal. The
    second must not produce a duplicate reply.
    """
    script = [
        results("done", is_final=True, speech_final=True, start=0.0, duration=0.5,
                words=[("done", 0.0, 0.5)]),
        {"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 0.5},
    ]
    stt, events = await _collect(script)
    assert len([e for e in events if e.type == "final_transcript"]) == 1
    assert stt.stats.turns_completed == 1
    assert stt.pending_segments == []


@pytest.mark.asyncio
async def test_empty_and_duplicate_boundaries_emit_nothing() -> None:
    script = [
        {"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 0.0},
        results("", is_final=True, speech_final=True),
    ]
    stt, events = await _collect(script)
    assert [e for e in events if e.type == "final_transcript"] == []
    assert stt.stats.turns_completed == 0


@pytest.mark.asyncio
async def test_client_finalise_closes_the_turn_for_push_to_talk() -> None:
    """Push-to-talk release ends the answer even with no provider boundary."""
    script = [
        results("that is my answer", is_final=True, start=0.0, duration=1.0,
                words=[("that", 0.0, 0.2)]),
    ]
    fake = FakeListen(script)
    events: list = []
    async with running(fake.handler) as url:
        bus = EventBus()
        bus.subscribe_all(lambda event: events.append(event))
        stt = DeepgramStt(bus, "s1", settings(), url_override=url,
                          get_turn_id=lambda: "t1")
        await stt.start()
        await asyncio.sleep(0.15)
        await stt.finalise_turn("client")
        # A second release must not create a second answer.
        await stt.finalise_turn("client")
        await bus.drain()
        await stt.close()
    finals = [e for e in events if e.type == "final_transcript"]
    assert len(finals) == 1
    assert finals[0].boundary == "client"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Failure behaviour and cleanup
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_credentials_refuse_rather_than_fake_a_session() -> None:
    stt = DeepgramStt(EventBus(), "s1", settings(DEEPGRAM_API_KEY=""), keyterms=[])
    with pytest.raises(SttUnavailable, match="DEEPGRAM_API_KEY"):
        await stt.start()
    tts = DeepgramTts(EventBus(), "s1", settings(DEEPGRAM_API_KEY=""))
    with pytest.raises(TtsUnavailable, match="DEEPGRAM_API_KEY"):
        await tts.start()


@pytest.mark.asyncio
async def test_unreachable_provider_raises_unavailable() -> None:
    stt = DeepgramStt(
        EventBus(), "s1", settings(), url_override="ws://127.0.0.1:1"
    )
    with pytest.raises(SttUnavailable):
        await stt.start()


@pytest.mark.asyncio
async def test_provider_error_message_is_recorded_not_swallowed() -> None:
    script = [{"type": "Error", "description": "keyterm limit exceeded"}]
    stt, events = await _collect(script)
    assert any("keyterm limit exceeded" in error for error in stt.stats.errors)
    assert [e for e in events if e.type == "final_transcript"] == []


@pytest.mark.asyncio
async def test_close_cancels_every_background_task() -> None:
    fake = FakeListen([])
    async with running(fake.handler) as url:
        stt = DeepgramStt(EventBus(), "s1", settings(), url_override=url)
        await stt.start()
        assert stt._recv_task is not None and stt._keepalive_task is not None
        await stt.close()
        assert stt._recv_task is None
        assert stt._keepalive_task is None
        assert stt._ws is None
    # And it is safe to close twice.
    await stt.close()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Streaming TTS
# ─────────────────────────────────────────────────────────────────────────────


def test_sentences_split_for_incremental_speak() -> None:
    assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]
    assert split_sentences("No terminator") == ["No terminator"]
    assert split_sentences("   ") == []


def test_flush_budget_enforces_the_documented_limit() -> None:
    """20 flushes per 60 s is a provider limit, so it is guarded, not assumed."""
    now = [0.0]
    budget = FlushBudget(limit=3, window_s=60.0, clock=lambda: now[0])
    assert [budget.allow() for _ in range(4)] == [True, True, True, False]
    now[0] += 61.0
    assert budget.allow() is True


def test_word_timestamps_are_labelled_estimates() -> None:
    stamps = estimate_word_timestamps("one two three", offset_ms=100)
    assert [s.word for s in stamps] == ["one", "two", "three"]
    assert stamps[0].offset_ms == 100
    assert stamps[1].offset_ms > stamps[0].offset_ms


@pytest.mark.asyncio
async def test_tts_streams_chunks_and_declares_real_audio_format() -> None:
    fake = FakeSpeak(chunk_bytes=960, chunks_per_flush=3)
    events: list = []
    forwarded: list[tuple[int, str]] = []
    async with running(fake.handler) as url:
        bus = EventBus()
        bus.subscribe_all(lambda event: events.append(event))
        tts = DeepgramTts(bus, "s1", settings(), url_override=url)
        async def forward(audio: bytes, utt: str, rate: int) -> None:
            forwarded.append((len(audio), utt))

        tts.on_audio = forward  # type: ignore[assignment]
        await tts.start()
        await tts.synthesise("First sentence. Second sentence.", "utt-1", "turn-1")
        await bus.drain()
        await tts.close()

    chunks = [e for e in events if e.type == "tts_chunk"]
    assert len(chunks) == 3, "each provider frame should be forwarded as it arrives"
    # The browser cannot decode without these, and 24 kHz is not the old 16 kHz
    # mock default — so the format has to travel with the audio.
    assert all(c.sample_rate == 24000 for c in chunks)
    assert all(c.encoding == "linear16" for c in chunks)
    # Deepgram does not document word alignment, so this must not claim exactness.
    assert all(c.timings_estimated is True for c in chunks)
    assert all(c.utterance_id == "utt-1" for c in chunks)
    # t_audio_out advances with real audio duration.
    assert chunks[0].t_audio_out == 0.0
    assert chunks[1].t_audio_out > 0.0
    # Raw bytes reached the forwarder; the bus carried only refs.
    assert [length for length, _ in forwarded] == [960, 960, 960]
    assert all("chunk" in c.audio_ref for c in chunks)

    # One Speak per sentence, exactly one Flush for the line.
    speaks = [m for m in fake.received if m.get("type") == "Speak"]
    flushes = [m for m in fake.received if m.get("type") == "Flush"]
    assert [m["text"] for m in speaks] == ["First sentence.", "Second sentence."]
    assert len(flushes) == 1
    assert tts.stats.chunks == 3 and tts.stats.flushes == 1


@pytest.mark.asyncio
async def test_tts_handshake_selects_voice_encoding_and_rate() -> None:
    fake = FakeSpeak()
    async with running(fake.handler) as url:
        tts = DeepgramTts(
            EventBus(), "s1", settings(DEEPGRAM_TTS_MODEL="aura-2-apollo-en"),
            url_override=url,
        )
        await tts.start()
        await tts.close()
    as_dict = dict(fake.query)
    assert as_dict["model"] == "aura-2-apollo-en"
    assert as_dict["encoding"] == "linear16"
    assert as_dict["sample_rate"] == "24000"
    assert fake.headers.get("authorization") == "Token test-key-not-real"


def test_panel_personas_map_to_distinct_valid_voices() -> None:
    voices = {
        "lead": "aura-2-thalia-en",
        "peer": "aura-2-apollo-en",
        "manager": "aura-2-vesta-en",
    }
    tts = DeepgramTts(
        EventBus(), "s1", settings(), voice_for_persona=voices
    )
    chosen = [tts.voice_for(p) for p in ("lead", "peer", "manager")]
    assert len(set(chosen)) == 3, "each panel voice must be distinct"
    assert all(v.startswith("aura-2-") for v in chosen)
    # An unknown persona falls back to the configured default rather than crashing.
    assert tts.voice_for("nobody") == "aura-2-thalia-en"
    assert tts.voice_for(None) == "aura-2-thalia-en"


@pytest.mark.asyncio
async def test_clear_stops_provider_audio_and_late_chunks_are_dropped() -> None:
    """
    Barge-in, provider half. `Clear` stops generation; audio already in flight
    for the cancelled utterance must not reach the browser.
    """
    fake = FakeSpeak(chunks_per_flush=2)
    events: list = []
    async with running(fake.handler) as url:
        bus = EventBus()
        bus.subscribe_all(lambda event: events.append(event))
        tts = DeepgramTts(bus, "s1", settings(), url_override=url)
        await tts.start()
        await tts.synthesise("Tell me about it.", "utt-1", "turn-1")
        await bus.drain()
        before = len([e for e in events if e.type == "tts_chunk"])

        await tts.cancel("utt-1")
        # Audio that was already in the provider's pipe arrives after Clear.
        await fake.connection.send(b"\x02\x00" * 480)
        await asyncio.sleep(0.1)
        await bus.drain()
        after = len([e for e in events if e.type == "tts_chunk"])
        await tts.close()

    assert after == before, "a chunk from the cancelled utterance was forwarded"
    assert tts.stats.dropped_late_chunks >= 1
    assert tts.stats.clears == 1
    assert any(m.get("type") == "Clear" for m in fake.received)


@pytest.mark.asyncio
async def test_tts_close_sends_close_and_releases_tasks() -> None:
    fake = FakeSpeak()
    async with running(fake.handler) as url:
        tts = DeepgramTts(EventBus(), "s1", settings(), url_override=url)
        await tts.start()
        assert tts._recv_task is not None
        await tts.close()
        assert tts._recv_task is None
        assert tts._ws is None
    assert any(m.get("type") == "Close" for m in fake.received)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Configuration safety
# ─────────────────────────────────────────────────────────────────────────────


def test_retired_model_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="withdrawn"):
        settings(MODEL_FALLBACK_FAST="gemma2-9b-it")


def test_production_refuses_mock_providers() -> None:
    with pytest.raises(ValueError, match="ALLOW_MOCK_PROVIDERS"):
        Settings(APP_ENV="prod", ALLOW_MOCK_PROVIDERS=True)  # type: ignore[arg-type]


def test_public_dict_never_contains_a_secret() -> None:
    config = settings(DEEPGRAM_API_KEY="super-secret", GROQ_API_KEY="also-secret")
    blob = json.dumps(config.public_dict())
    assert "super-secret" not in blob and "also-secret" not in blob
    assert config.public_dict()["deepgram_configured"] is True
    # Even a full model dump keeps them masked.
    assert "super-secret" not in config.model_dump_json()


def test_non_pcm_encoding_is_refused() -> None:
    with pytest.raises(ValueError, match="encoding"):
        settings(DEEPGRAM_ENCODING="opus")


def test_tts_voice_must_be_an_aura_id() -> None:
    with pytest.raises(ValueError, match="Aura"):
        settings(DEEPGRAM_TTS_MODEL="nova-3")
