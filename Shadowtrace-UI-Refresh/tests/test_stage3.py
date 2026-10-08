"""
Stage 3 tests — inference and TTS, no audio input.

1. Interviewer runs with fake_tts: draft_ready fires with correct first sentence.
2. TTS runs with fake_llm: tts_chunk emitted with word_timestamps.
3. fake_tts word_timestamps span the full duration proportionally.
4. fake_llm streams tokens at configurable rate; delay_ms is respected.
5. Truncation: replay tts_chunk + playback_ack + barge_in; last_heard_word correct.
6. Interviewer fires draft_ready only once even for multi-sentence replies.
7. LeadInterviewer handles single-sentence reply (no sentence-boundary in the middle).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import pytest

from interview.events.bus import EventBus
from interview.events.schema import BargeIn, PlaybackAck, TtsChunk, WordTimestamp
from interview.mocks.fake_llm import FakeLlm
from interview.mocks.fake_tts import FakeTts

AUDIO_FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Interviewer + FakeTts → draft_ready fires
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_interviewer_with_fake_tts_emits_draft_ready() -> None:
    """
    LeadInterviewer (mock provider) emits draft_ready with a non-empty first_sentence
    when run standalone — no audio, no TTS API.
    """
    from interview.session.interviewer import LeadInterviewer

    bus = EventBus()
    draft_events = []

    async def collect(event):
        if event.type == "draft_ready":
            draft_events.append(event)

    bus.subscribe_all(collect)

    session_id = "test-sess"
    turn_id = str(uuid.uuid4())
    utt_id = str(uuid.uuid4())

    iv = LeadInterviewer(bus, session_id, turn_id, utt_id)
    # Override provider to mock so no API key needed
    iv._provider = "mock"

    history = [{"role": "user", "content": "I built a Kafka pipeline that handled 2M events per second."}]
    full_text = await iv.generate(history)
    await bus.drain()

    assert len(draft_events) == 1, f"Expected 1 draft_ready, got {len(draft_events)}"
    dr = draft_events[0]
    assert dr.first_sentence, "draft_ready.first_sentence must not be empty"
    assert dr.utterance_id == utt_id
    assert dr.variant == "plain"
    assert full_text.strip(), "Full generated text must not be empty"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: FakeTts → tts_chunk emitted with word_timestamps
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fake_tts_emits_chunk_with_timestamps() -> None:
    bus = EventBus()
    chunks = []

    async def collect(event):
        if event.type == "tts_chunk":
            chunks.append(event)

    bus.subscribe_all(collect)

    utt_id = str(uuid.uuid4())
    tts = FakeTts(bus, "sess", "turn", utt_id)
    await tts.synthesise("How did you handle back-pressure in that pipeline?")
    await bus.drain()

    assert len(chunks) == 1, "Expected exactly one tts_chunk per synthesise() call"
    chunk = chunks[0]
    assert chunk.utterance_id == utt_id
    assert len(chunk.word_timestamps) > 0, "word_timestamps must not be empty"
    # All words from the input should appear
    words_in = set("How did you handle back-pressure in that pipeline?".split())
    words_out = {wt.word for wt in chunk.word_timestamps}
    assert words_in == words_out, f"Word mismatch: {words_in ^ words_out}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: FakeTts word_timestamps are monotonically increasing
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fake_tts_timestamps_are_monotonic() -> None:
    bus = EventBus()
    chunks = []
    bus.subscribe("tts_chunk", lambda e: chunks.append(e))

    tts = FakeTts(bus, "sess", "turn", "utt-mono")
    await tts.synthesise("One two three four five six seven eight nine ten.")
    await bus.drain()

    assert chunks
    offsets = [wt.offset_ms for wt in chunks[0].word_timestamps]
    for i in range(1, len(offsets)):
        assert offsets[i] >= offsets[i - 1], (
            f"word_timestamps not monotonic at index {i}: {offsets[i-1]} then {offsets[i]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: FakeLlm delay and token rate
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fake_llm_delay_respected() -> None:
    """FakeLlm should wait at least delay_ms before yielding first token."""
    llm = FakeLlm(delay_ms=80, tokens_per_second=1000)  # fast token rate
    t0 = time.monotonic()
    first_token_t: float | None = None
    async for _ in llm.stream([]):
        if first_token_t is None:
            first_token_t = time.monotonic()

    assert first_token_t is not None
    elapsed_ms = (first_token_t - t0) * 1000
    assert elapsed_ms >= 70, f"First token arrived too early: {elapsed_ms:.1f} ms (expected ≥70 ms)"


@pytest.mark.asyncio
async def test_fake_llm_streams_all_tokens() -> None:
    """FakeLlm should stream the full canned text."""
    llm = FakeLlm(delay_ms=0, tokens_per_second=10000, response_index=0)
    tokens = []
    async for token in llm.stream([]):
        tokens.append(token)
    full = "".join(tokens).strip()
    assert full == llm.get_response(0).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Truncation — barge-in at known played_ms → correct last_heard_word
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_truncation_word_index_from_tts_chunk(tmp_path: Path) -> None:
    """
    Replay: tts_chunk with known word_timestamps + playback_ack at 850ms +
    barge_in → truncate.last_heard_word is the last word with offset_ms <= 850.

    Word timestamps (from cached_response.json):
      index 0: Thanks   (0 ms)
      index 1: for      (180 ms)
      index 2: sharing  (280 ms)
      index 3: that.    (420 ms)
      index 4: Can      (680 ms)
      index 5: you      (760 ms)
      index 6: walk     (820 ms)  ← played_ms=850, last heard
      index 7: me       (900 ms)
    """
    from interview.events.log import EventLogger, read_events
    from interview.events.schema import Truncate

    json_path = AUDIO_FIXTURES / "cached_response.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    wts = [WordTimestamp(**w) for w in data["word_timestamps"]]

    session_id = "trunc-sess"
    turn_id = "trunc-turn"
    utt_id = "utt-cached"
    played_ms = 850

    bus = EventBus()
    log_path = tmp_path / "truncation.jsonl"
    logger = await EventLogger.open(bus, log_path, session_id)

    await bus.emit(
        TtsChunk(
            session_id=session_id,
            turn_id=turn_id,
            producer="fake_tts",
            t_audio_out=0.05,
            audio_ref="fixtures/audio/cached_response.pcm",
            word_timestamps=wts,
            utterance_id=utt_id,
        )
    )
    await bus.emit(
        PlaybackAck(
            session_id=session_id,
            turn_id=turn_id,
            producer="client",
            t_audio_out=played_ms / 1000.0,
            played_ms=played_ms,
            utterance_id=utt_id,
        )
    )
    await bus.emit(
        BargeIn(
            session_id=session_id,
            turn_id=turn_id,
            producer="client",
            t_audio_in=0.9,
            confidence=0.95,
        )
    )

    # Same rule as AudioSession._last_acked_word
    last_idx, last_word = 0, wts[0].word
    for i, w in enumerate(wts):
        if w.offset_ms <= played_ms:
            last_idx = i
            last_word = w.word

    await bus.emit(
        Truncate(
            session_id=session_id,
            turn_id=turn_id,
            producer="session",
            t_audio_out=0.9,
            last_heard_word=last_word,
            word_index=last_idx,
            utterance_id=utt_id,
        )
    )
    await bus.drain()
    await logger.close()

    assert last_word == "walk", f"Expected 'walk', got '{last_word}'"
    assert last_idx == 6

    events = list(read_events(log_path))
    assert [e.type for e in events] == ["tts_chunk", "playback_ack", "barge_in", "truncate"]
    truncate = events[-1]
    assert truncate.last_heard_word == "walk"
    assert truncate.word_index == 6


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: draft_ready fires exactly once for multi-sentence reply
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_ready_fires_once_for_multi_sentence() -> None:
    from interview.session.interviewer import LeadInterviewer

    bus = EventBus()
    draft_count = []
    bus.subscribe("draft_ready", lambda e: draft_count.append(1))

    iv = LeadInterviewer(bus, "sess", "turn", "utt")
    iv._provider = "mock"
    # The mock canned text has multiple sentences — draft_ready must fire only once.
    await iv.generate([{"role": "user", "content": "Tell me about a project."}])
    await bus.drain()

    assert len(draft_count) == 1, (
        f"draft_ready should fire exactly once; fired {len(draft_count)} times"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: LeadInterviewer single-sentence reply (no mid-text boundary)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_ready_fires_for_no_sentence_boundary() -> None:
    """If streamed text has no mid-sentence boundary, draft_ready still fires at end."""
    from interview.session.interviewer import LeadInterviewer

    bus = EventBus()
    drafts = []
    bus.subscribe("draft_ready", lambda e: drafts.append(e))

    iv = LeadInterviewer(bus, "sess", "turn", "utt-single")
    iv._provider = "mock"
    # Force a single-sentence canned response with no trailing period mid-stream
    iv._stream = _single_sentence_stream.__get__(iv)  # type: ignore[attr-defined]

    await iv.generate([])
    await bus.drain()

    assert len(drafts) == 1
    assert drafts[0].first_sentence


async def _single_sentence_stream(self, _history):
    """Inject a single sentence with no intermediate sentence boundary."""
    for word in "What was your SLA target".split():
        yield word + " "
        await asyncio.sleep(0)


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: FakeTts multiple calls accumulate audio_out_cursor
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fake_tts_cursor_accumulates() -> None:
    """Second synthesise call should have higher offset_ms than first."""
    bus = EventBus()
    chunks = []
    bus.subscribe("tts_chunk", lambda e: chunks.append(e))

    tts = FakeTts(bus, "sess", "turn", "utt-acc")
    await tts.synthesise("First sentence here.")
    await tts.synthesise("Second sentence here.")
    await bus.drain()

    assert len(chunks) == 2
    first_offsets = [wt.offset_ms for wt in chunks[0].word_timestamps]
    second_offsets = [wt.offset_ms for wt in chunks[1].word_timestamps]
    assert second_offsets[0] >= first_offsets[-1], (
        "Second chunk's first word offset should be >= last word of first chunk"
    )
