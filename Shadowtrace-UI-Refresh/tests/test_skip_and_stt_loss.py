"""
Two live-session fixes found from a real session log:

- a candidate asking to skip got a follow-up on the question instead of the
  next one, because nothing recognised the request;
- the Deepgram stream could die mid-session and nobody was told, so the room
  kept "listening" while no answer could ever arrive.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from interview.events.bus import EventBus
from interview.packs.model import load_pack
from interview.session.agent import propose
from interview.session.proposer import DeterministicProposer, ModelProposer, ProposalContext
from interview.session.roles import InterviewRound, RoleFamily, role_for
from interview.session.router import is_skip_request
from interview.transport.deepgram_stt import DeepgramStt
from tests.test_stage12_deepgram import FakeListen, running, settings
from tests.test_stage13_roles import MIXED_ANSWER, FakeModel, state_for

# ─────────────────────────────────────────────────────────────────────────────
# Skip requests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Can we skip this one?",
        "I'd like to skip this question.",
        "Let's move on to the next one please",
        "next question",
        "Skip to the next question.",
        "Honestly I'll pass on this.",
        "Let’s skip it",
    ],
)
def test_skip_requests_are_recognised(text: str) -> None:
    assert is_skip_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "We skipped the cache layer because reads were cheap.",
        "I don't know.",
        # A real answer that happens to contain the phrase is still an answer.
        "After we shipped the migration we measured latency for two weeks, and "
        "once it was stable we could move on to the next service in the queue.",
        "",
    ],
)
def test_ordinary_answers_are_not_skip_requests(text: str) -> None:
    assert not is_skip_request(text)


def _specialist_context(answer: str, **extra):
    spec = role_for(InterviewRound.DOMAIN_SPECIALIST, family=RoleFamily.SOFTWARE)
    pack = load_pack("specialist-software")
    spine = pack.spine_ids()
    state = state_for(
        "specialist-software",
        covered=spine[:1],
        outstanding=spine[1:],
        transcript=(answer,),
    )
    context = ProposalContext(
        spec=spec,
        state=state,
        last_answer=answer,
        transcript=(answer,),
        time_remaining_s=600.0,
        skip_requested=is_skip_request(answer),
        **extra,
    )
    return spec, spine, context


@pytest.mark.asyncio
async def test_skip_moves_to_the_next_spine_question_instead_of_probing() -> None:
    spec, spine, probing = _specialist_context(MIXED_ANSWER)
    assert (await DeterministicProposer(spec).propose(probing)).action == "probe"

    _, _, skipping = _specialist_context("Can we skip this one?")
    move = await DeterministicProposer(spec).propose(skipping)
    assert move.action == "ask_spine"
    assert move.spine_id == spine[1], "the guard's spine order is unchanged"


@pytest.mark.asyncio
async def test_skip_does_not_spend_a_model_call() -> None:
    model = FakeModel('{"action":"probe","question":"Why?","transcript_anchor":"x"}')
    spec, spine, context = _specialist_context("next question please")
    proposer = ModelProposer(spec, model)
    move = await proposer.propose(context)
    assert move.action == "ask_spine" and move.spine_id == spine[1]
    assert model.calls == []
    assert proposer.fallbacks_used == 0, "a skip is not a provider fallback"


def test_plain_propose_honours_skip() -> None:
    class Tools:
        def __init__(self, state):
            self._state = state

        def guard_state(self):
            return self._state

        def untested_claim(self):
            class Claim:
                id = "c1"

            return Claim()

    pack = load_pack("specialist-software")
    spine = pack.spine_ids()
    state = state_for("specialist-software", covered=spine[:1], outstanding=spine[1:])
    assert propose(Tools(state)).action == "probe"  # type: ignore[arg-type]
    skipped = propose(Tools(state), skip_requested=True)  # type: ignore[arg-type]
    assert skipped.action == "ask_spine" and skipped.spine_id == spine[1]


# ─────────────────────────────────────────────────────────────────────────────
# A dead transcription stream is reported
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_closing_the_stream_is_reported_once() -> None:
    async def hang_up(connection):
        await asyncio.sleep(0.05)
        await connection.close(code=1011, reason="simulated idle timeout")

    lost: list[str] = []

    async def on_lost(reason: str) -> None:
        lost.append(reason)

    async with running(hang_up) as url:
        stt = DeepgramStt(EventBus(), "s1", settings(), url_override=url, on_lost=on_lost)
        await stt.start()
        for _ in range(100):
            if lost:
                break
            await asyncio.sleep(0.02)
        # Audio sent after the loss must not produce a second report.
        await stt.send_audio(b"\x00\x00" * 160)
        await stt.send_audio(b"\x00\x00" * 160)
        await stt.close()

    assert len(lost) == 1


@pytest.mark.asyncio
async def test_our_own_close_is_not_reported_as_a_loss() -> None:
    lost: list[str] = []

    async def on_lost(reason: str) -> None:
        lost.append(reason)

    fake = FakeListen([])
    async with running(fake.handler) as url:
        stt = DeepgramStt(EventBus(), "s1", settings(), url_override=url, on_lost=on_lost)
        await stt.start()
        await stt.close()
        await asyncio.sleep(0.05)
    assert lost == []


@pytest.mark.asyncio
async def test_voice_session_reconnects_and_tells_the_room(monkeypatch) -> None:
    from interview.transport.voice_session import VoiceSession

    sent: list[dict] = []

    async def send_text(payload: dict) -> None:
        sent.append(payload)

    async def send_bytes(_payload: bytes) -> None:
        return None

    voice = VoiceSession(EventBus(), "s1", settings(), send_text, send_bytes)
    monkeypatch.setattr(voice, "RECONNECT_BACKOFF_S", (0.0,))
    calls: list[int] = []

    async def reconnect() -> None:
        calls.append(1)

    monkeypatch.setattr(voice.stt, "reconnect", reconnect)
    await voice.stt._on_lost("connection closed by the provider")  # type: ignore[misc]
    assert voice._recover_task is not None
    await voice._recover_task
    assert calls == [1]
    assert sent[-1]["type"] == "voice_warning"
    assert sent[-1]["reason"] == "transcription_reconnected"


@pytest.mark.asyncio
async def test_voice_session_offers_text_when_reconnecting_fails(monkeypatch) -> None:
    from interview.transport.deepgram_stt import SttUnavailable
    from interview.transport.voice_session import VoiceSession

    sent: list[dict] = []

    async def send_text(payload: dict) -> None:
        sent.append(payload)

    async def send_bytes(_payload: bytes) -> None:
        return None

    voice = VoiceSession(EventBus(), "s1", settings(), send_text, send_bytes)
    monkeypatch.setattr(voice, "RECONNECT_BACKOFF_S", (0.0, 0.0, 0.0))
    attempts: list[int] = []

    async def reconnect() -> None:
        attempts.append(1)
        raise SttUnavailable("still down")

    monkeypatch.setattr(voice.stt, "reconnect", reconnect)
    await voice.stt._on_lost("connection closed by the provider")  # type: ignore[misc]
    await voice._recover_task  # type: ignore[misc]
    assert len(attempts) == 3
    assert sent[-1]["type"] == "voice_error"
    assert sent[-1]["reason"] == "transcription_lost"
    assert sent[-1]["can_use_text_lane"] is True


@pytest.mark.asyncio
async def test_reconnect_resumes_transcription_on_a_new_socket() -> None:
    """After a drop, audio flows again and the answer in progress is kept."""
    connections: list[int] = []

    async def handler(connection):
        connections.append(1)
        if len(connections) == 1:
            from tests.test_stage12_deepgram import results

            await connection.send(json.dumps(results("I built it", is_final=True)))
            await asyncio.sleep(0.05)
            await connection.close(code=1011, reason="simulated drop")
            return
        async for _ in connection:
            pass

    lost: list[str] = []

    async def on_lost(reason: str) -> None:
        lost.append(reason)

    async with running(handler) as url:
        stt = DeepgramStt(EventBus(), "s1", settings(), url_override=url, on_lost=on_lost)
        await stt.start()
        for _ in range(100):
            if lost:
                break
            await asyncio.sleep(0.02)
        assert lost
        await stt.reconnect()
        assert stt.pending_segments == ["I built it"], "answer in progress kept"
        await stt.send_audio(b"\x00\x00" * 160)
        assert stt.stats.bytes_sent > 0 and stt.stats.reconnects == 1
        await stt.close()
    assert len(connections) == 2


# ─────────────────────────────────────────────────────────────────────────────
# When an answer is over
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_results_through_silence_do_not_hold_the_answer_open() -> None:
    from tests.test_stage12_deepgram import FAST_END, _collect, results

    script = [
        results("I built the ingestion service end to end", is_final=True),
        results("", is_final=True),
        results("", is_final=True),
    ]
    _, events = await _collect(script, wait=0.9, **FAST_END)
    finals = [e for e in events if e.type == "final_transcript"]
    assert [f.text for f in finals] == ["I built the ingestion service end to end"]
    assert finals[0].boundary == "timeout"


@pytest.mark.asyncio
async def test_speaking_again_after_a_pause_keeps_one_answer() -> None:
    from tests.test_stage12_deepgram import FAST_END, _collect, results

    script = [
        results("first I profiled the service", is_final=True, speech_final=True),
        {"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 1.0},
        results("then I rewrote the hot loop", is_final=True, speech_final=True),
    ]
    _, events = await _collect(script, wait=0.9, **FAST_END)
    finals = [e for e in events if e.type == "final_transcript"]
    assert [f.text for f in finals] == [
        "first I profiled the service then I rewrote the hot loop"
    ]


def _stt_with(segments: list[str]) -> DeepgramStt:
    stt = DeepgramStt(
        EventBus(),
        "s1",
        settings(ANSWER_END_SILENCE_MS=3000, ANSWER_END_EXTENDED_MS=5000),
    )
    stt._segments = list(segments)
    return stt


@pytest.mark.parametrize(
    "said",
    [
        "I designed the retry logic and",
        "The main reason was, um",
        "So I think,",
        "Well",
    ],
)
def test_unfinished_answers_get_the_longer_wait(said: str) -> None:
    assert _stt_with([said]).answer_wait_s() == 5.0


def test_finished_answers_get_the_normal_wait() -> None:
    stt = _stt_with(["I designed the retry logic with exponential backoff."])
    assert stt.answer_wait_s() == 3.0
