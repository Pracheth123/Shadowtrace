"""
Real Groq calls. Skipped by default — run with:

    pytest -m live
    GROQ_API_KEY=... pytest -m live
"""

from __future__ import annotations

import os

import pytest

from interview.events.bus import EventBus
from interview.llm.client import GroqModelClient
from interview.llm.env import groq_api_key, load_dotenv

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _require_key():
    load_dotenv()
    if not groq_api_key():
        pytest.skip("GROQ_API_KEY not set")


@pytest.mark.asyncio
async def test_groq_live_interviewer_streams_one_sentence() -> None:
    bus = EventBus()
    calls = []
    bus.subscribe("model_call", lambda e: calls.append(e))

    client = GroqModelClient(bus=bus, session_id="live-test")
    messages = [
        {
            "role": "system",
            "content": "Ask one short follow-up question. No preamble.",
        },
        {
            "role": "user",
            "content": "I built a Kafka pipeline at two million events per second.",
        },
    ]
    chunks: list[str] = []
    async for tok in client.stream_chat(
        "live_interviewer", messages, turn_id="turn-live-1"
    ):
        chunks.append(tok)
    await bus.drain()

    text = "".join(chunks).strip()
    assert text
    assert calls and calls[0].ok is True
    assert calls[0].model  # configured role model
    assert os.environ.get("GROQ_API_KEY")  # key present but never logged
