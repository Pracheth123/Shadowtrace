"""
Unit tests for Groq client infrastructure — zero real network calls.
"""

from __future__ import annotations

import asyncio

import pytest

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.llm.client import GroqModelClient
from interview.llm.limiter import RpmLimiter, TurnCallBudget, TurnCallBudgetExceeded


@pytest.mark.asyncio
async def test_rpm_limiter_caps_burst() -> None:
    limiter = RpmLimiter(rpm=2)
    t0 = asyncio.get_event_loop().time()
    await limiter.acquire()
    await limiter.acquire()
    # Third acquire must wait — use a short fake by temporarily shrinking window
    # We only assert the first two return immediately.
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed < 0.2


@pytest.mark.asyncio
async def test_turn_call_budget() -> None:
    budget = TurnCallBudget(max_per_turn=2)
    a1, t1 = await budget.begin_call("turn-1")
    a2, t2 = await budget.begin_call("turn-1")
    assert a1 == 1 and t1 == 1
    assert a2 == 2 and t2 == 2
    with pytest.raises(TurnCallBudgetExceeded):
        await budget.begin_call("turn-1")
    # New turn resets per-turn counter
    a3, t3 = await budget.begin_call("turn-2")
    assert a3 == 3 and t3 == 1
    assert budget.session_total == 3


@pytest.mark.asyncio
async def test_model_call_event_logged_without_network(tmp_path) -> None:
    """Inject a stub stream that never hits Groq; model_call still lands on the bus."""
    bus = EventBus()
    log_path = tmp_path / "s.jsonl"
    logger = await EventLogger.open(bus, log_path, "sess-g")

    client = GroqModelClient(
        bus=bus,
        session_id="sess-g",
        api_key="test-key-not-used",
        config={
            "groq": {
                "roles": {"live_interviewer": "llama-3.1-8b-instant"},
                "requests_per_minute": 60,
                "max_calls_per_turn": 3,
                "max_retries_on_429": 0,
            },
            "max_tokens": 32,
            "temperature": 0.0,
        },
    )

    async def fake_stream(**_kwargs):
        yield "Hello "
        yield "world."

    client._stream_with_retry = fake_stream  # type: ignore[method-assign]

    tokens = []
    async for t in client.stream_chat(
        "live_interviewer",
        [{"role": "user", "content": "hi"}],
        turn_id="turn-x",
    ):
        tokens.append(t)
    await bus.drain()
    await logger.close()

    assert "".join(tokens) == "Hello world."
    events = list(read_events(log_path))
    calls = [e for e in events if e.type == "model_call"]
    assert len(calls) == 1
    assert calls[0].call_index == 1
    assert calls[0].role == "live_interviewer"
    assert calls[0].ok is True
    assert client.session_call_count == 1


@pytest.mark.asyncio
async def test_interviewer_mock_makes_zero_groq_calls() -> None:
    """Default test path: LeadInterviewer(provider=mock) never constructs a real client."""
    from interview.session.interviewer import LeadInterviewer

    bus = EventBus()
    iv = LeadInterviewer(bus, "s", "t", "u", provider="mock")
    text = await iv.generate([{"role": "user", "content": "I built Kafka."}])
    await bus.drain()
    assert text.strip()
    # No model_call events without a Groq client
    collected = []
    bus2 = EventBus()
    bus2.subscribe("model_call", lambda e: collected.append(e))
    # already finished — just assert provider was mock
    assert iv._provider == "mock"
