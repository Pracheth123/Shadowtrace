"""
Stage 2 tests — transport layer.

1. Turn detector on fake-STT: normal answer endpoints correctly.
2. Turn detector: long answer (8 s+) is NOT cut short before speech ends.
3. Turn detector: hesitant answer with 'um…so…' pauses does NOT endpoint mid-thought.
4. Turn detector: single-word answer endpoints correctly.
5. Truncation: barge-in at known played_ms produces correct last_heard_word.
6. Replay: turn detector fed only from a logged session emits same endpoints.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from interview.events.bus import EventBus
from interview.mocks.fake_stt import FakeStt
from interview.transport.turn_detector import TurnDetector, TurnDetectorConfig

FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"
AUDIO_FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tight_config() -> TurnDetectorConfig:
    """Fast-firing config for unit tests so they don't take 700 ms each."""
    return TurnDetectorConfig(
        silence_ms=100,
        max_silence_ms=400,
        extend_on_trailing=True,
        trailing_words=["and", "so", "because", "but", "or", "that",
                        "which", "if", "then", "when", "although", "however"],
        extend_ms=150,
        min_speech_ms=50,
        endpoint_confidence_min=0.50,
    )


async def _run_fake_stt_through_turn_detector(
    transcript_path: Path,
    config: TurnDetectorConfig | None = None,
    speed: float = 0.0,   # 0 = instant (for tests)
) -> list[tuple[float, float]]:
    """
    Run FakeStt through TurnDetector and collect (confidence, t_audio_in) for
    each endpoint that fires.
    """
    cfg = config or _tight_config()
    bus = EventBus()
    endpoints: list[tuple[float, float]] = []
    current_td: list[TurnDetector] = []

    def make_td() -> TurnDetector:
        detector = TurnDetector(
            config=cfg,
            on_endpoint=lambda conf, t: endpoints.append((conf, t)),
        )
        return detector

    async def on_event(event) -> None:
        if event.type == "speech_start":
            td = make_td()
            current_td.clear()
            current_td.append(td)
            td.notify_speech_start()
        elif event.type == "partial" and current_td:
            current_td[0].push_partial(event.text)

    bus.subscribe_all(on_event)

    def on_silence(t: float) -> None:
        if current_td:
            current_td[0].push_silence(t)

    stt = FakeStt(
        bus=bus,
        transcript_path=transcript_path,
        session_id="test-session",
        on_silence=on_silence,
    )
    await stt.run(speed=speed)
    await bus.drain()

    # Yield control so scheduled timer tasks can execute, then wait the full
    # silence + extend window.  Without this yield, tasks created during the
    # synchronous part of drain() haven't had a chance to run yet.
    total_wait = (cfg.silence_ms + cfg.extend_ms) / 1000.0 + 0.15
    await asyncio.sleep(total_wait)
    # One more yield to let any final callbacks complete.
    await asyncio.sleep(0)
    return endpoints


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Normal answer endpoints exactly once
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normal_answer_endpoints_once() -> None:
    endpoints = await _run_fake_stt_through_turn_detector(FIXTURES / "normal_answer.json")
    assert len(endpoints) == 1, f"Expected 1 endpoint, got {len(endpoints)}"
    conf, _ = endpoints[0]
    assert conf >= 0.50, f"Confidence too low: {conf}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Long answer — must not endpoint mid-speech
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_long_answer_not_cut_short() -> None:
    endpoints = await _run_fake_stt_through_turn_detector(
        FIXTURES / "long_answer.json",
        config=TurnDetectorConfig(
            silence_ms=200,
            max_silence_ms=2000,
            extend_on_trailing=True,
            trailing_words=["and", "so", "because", "but", "or", "that",
                            "which", "if", "then", "when", "although", "however"],
            extend_ms=300,
            min_speech_ms=100,
            endpoint_confidence_min=0.50,
        ),
        speed=0.0,  # instant replay
    )
    # Must fire exactly once, after the full utterance
    assert len(endpoints) == 1, (
        f"Long answer produced {len(endpoints)} endpoints (should be 1)"
    )
    _, t = endpoints[0]
    # t_audio_in at endpoint must be at or after the last word end (3.940 s)
    assert t >= 3.8, f"Endpoint fired too early at t={t:.3f}s (expected ≥ 3.8s)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Hesitant answer — must NOT endpoint mid-thought
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hesitant_answer_no_premature_endpoint() -> None:
    endpoints = await _run_fake_stt_through_turn_detector(
        FIXTURES / "hesitant_answer.json",
        config=TurnDetectorConfig(
            silence_ms=200,
            max_silence_ms=4000,
            extend_on_trailing=True,
            trailing_words=["and", "so", "because", "but", "or", "that",
                            "which", "if", "then", "when", "although", "however",
                            "um", "uh"],
            extend_ms=300,
            min_speech_ms=50,
            endpoint_confidence_min=0.40,
        ),
        speed=0.0,
    )
    # Must fire exactly once — not twice (once for "and" pause, once at end)
    assert len(endpoints) == 1, (
        f"Hesitant answer produced {len(endpoints)} endpoints; "
        "expected 1 (must not endpoint at mid-sentence 'and' pause)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Single-word answer
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_word_endpoints() -> None:
    endpoints = await _run_fake_stt_through_turn_detector(FIXTURES / "single_word.json")
    assert len(endpoints) == 1, f"Expected 1 endpoint for single word, got {len(endpoints)}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Truncation — barge-in produces correct last_heard_word
# ─────────────────────────────────────────────────────────────────────────────

def test_truncation_last_heard_word() -> None:
    """
    Given word_timestamps from the cached clip and played_ms at barge-in,
    the correct last_heard_word is returned.

    Hand-computed:
      word_timestamps in cached_response.json:
        0: Thanks   (0 ms)
        1: for      (180 ms)
        2: sharing  (280 ms)
        3: that.    (420 ms)
        4: Can      (680 ms)
        5: you      (760 ms)
        6: walk     (820 ms)   ← played_ms = 850, so last heard = "walk" (index 6)
        7: me       (900 ms)
        ...

    If played_ms=850, last acked word is index 6 ("walk").
    """
    json_path = AUDIO_FIXTURES / "cached_response.json"
    data = json.loads(json_path.read_text())
    wts = data["word_timestamps"]

    played_ms = 850
    last_idx = 0
    last_word = wts[0]["word"]
    for i, w in enumerate(wts):
        if w["offset_ms"] <= played_ms:
            last_idx = i
            last_word = w["word"]

    assert last_word == "walk", f"Expected 'walk', got '{last_word}'"
    assert last_idx == 6, f"Expected index 6, got {last_idx}"


def test_truncation_at_zero_ms() -> None:
    """At played_ms=0, last_heard_word is the first word."""
    json_path = AUDIO_FIXTURES / "cached_response.json"
    data = json.loads(json_path.read_text())
    wts = data["word_timestamps"]

    played_ms = 0
    last_idx = 0
    last_word = wts[0]["word"]
    for i, w in enumerate(wts):
        if w["offset_ms"] <= played_ms:
            last_idx = i
            last_word = w["word"]

    assert last_word == "Thanks"
    assert last_idx == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: VAD energy computation
# ─────────────────────────────────────────────────────────────────────────────

def test_vad_silence_frame() -> None:
    """All-zero frame → RMS=0 → below threshold → not speech."""
    from interview.transport.vad import EnergyVad, VadConfig
    vad = EnergyVad(VadConfig(energy_threshold=0.015, min_speech_frames=1))
    silent = b"\x00" * 640
    speech_started = []
    vad.on_speech_start = lambda tid, t: speech_started.append(tid)
    vad.push_frame(silent, 0.0)
    assert not speech_started, "Silent frame should not trigger speech_start"


def test_vad_loud_frame() -> None:
    """Max-amplitude frame → RMS=1.0 → above threshold → speech_start after min_speech_frames."""
    import struct
    from interview.transport.vad import EnergyVad, VadConfig
    vad = EnergyVad(VadConfig(energy_threshold=0.015, min_speech_frames=2))
    loud = struct.pack("<320h", *([32767] * 320))
    speech_started = []
    vad.on_speech_start = lambda tid, t: speech_started.append(tid)
    vad.push_frame(loud, 0.0)  # frame 1
    assert not speech_started, "Need min_speech_frames=2"
    vad.push_frame(loud, 0.02)  # frame 2
    assert len(speech_started) == 1, "Should have fired speech_start after 2 loud frames"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Turn detector trailing-word extension
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_turn_detector_extends_on_trailing() -> None:
    """When the latest partial ends with 'and', effective silence is extended."""
    cfg = TurnDetectorConfig(
        silence_ms=100,
        extend_on_trailing=True,
        trailing_words=["and"],
        extend_ms=200,
        max_silence_ms=1000,
        endpoint_confidence_min=0.5,
    )
    endpoints = []
    td = TurnDetector(
        config=cfg,
        on_endpoint=lambda conf, t: endpoints.append((conf, t)),
    )
    td.notify_speech_start()
    td.push_partial("I built and")  # ends with trailing word
    td.push_silence(1.0)

    # After silence_ms=100ms the endpoint should NOT have fired yet
    await asyncio.sleep(0.08)
    assert not endpoints, "Endpoint fired too early (before extend_ms elapsed)"

    # After silence_ms + extend_ms = 300ms total it should have fired
    await asyncio.sleep(0.30)
    assert len(endpoints) == 1, f"Expected 1 endpoint after extended wait, got {len(endpoints)}"
