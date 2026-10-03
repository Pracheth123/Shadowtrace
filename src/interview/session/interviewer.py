"""
Lead Interviewer — Stage 3/4 speak path.

Input : conversation history (list of role/content dicts) + a short system prompt.
Output: streamed text. Emits `draft_ready` when the first sentence is complete.

Real-model calls go through `interview.llm.GroqModelClient` (role=live_interviewer).
Tests and offline paths set provider=mock / inject nothing and use FakeLlm stream.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

import yaml

if TYPE_CHECKING:
    from interview.events.bus import EventBus
    from interview.llm.client import GroqModelClient

from interview.events.schema import DraftReady

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "inference.yaml"

SYSTEM_PROMPT = """\
You are a professional technical interviewer conducting a mock interview.
Your role: ask one focused follow-up question based on the candidate's last answer.
Be concise. One question per turn. No preamble, no "Great answer!".
If the candidate struggled, use a softer opening (a single-sentence concession) then ask.
Never reveal scoring criteria. Never mention the claims list or resume text directly.
Respond in plain English. Maximum 3 sentences total.\
"""

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def _load_inference_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


class LeadInterviewer:
    """
    Streams the interviewer's next line from an LLM, emitting draft_ready on the
    first completed sentence.
    """

    def __init__(
        self,
        bus: "EventBus",
        session_id: str,
        turn_id: str,
        utterance_id: str | None = None,
        *,
        producer: str = "interviewer",
        on_first_sentence=None,
        model_client: "GroqModelClient | None" = None,
        provider: str | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._turn_id = turn_id
        self._utterance_id = utterance_id or str(uuid.uuid4())
        self._producer = producer
        self._on_first_sentence = on_first_sentence
        cfg = _load_inference_config()
        self._provider = provider or cfg.get("provider", "groq")
        self._max_tokens = cfg.get("max_tokens", 120)
        self._temperature = cfg.get("temperature", 0.7)
        self._model_client = model_client

    async def generate(self, history: list[dict]) -> str:
        """Stream a response from the LLM. Emits draft_ready on the first sentence."""
        full_text = ""
        first_sentence_emitted = False
        buffer = ""

        async for token in self._stream(history):
            full_text += token
            buffer += token

            if not first_sentence_emitted:
                parts = _SENTENCE_END.split(buffer, maxsplit=1)
                if len(parts) > 1:
                    first_sentence = parts[0].strip()
                    buffer = parts[1]
                    first_sentence_emitted = True
                    await self._emit_draft_ready(first_sentence)
                    if self._on_first_sentence:
                        self._on_first_sentence(first_sentence)

        if not first_sentence_emitted and full_text.strip():
            await self._emit_draft_ready(full_text.strip())
            if self._on_first_sentence:
                self._on_first_sentence(full_text.strip())

        return full_text

    async def _emit_draft_ready(self, first_sentence: str) -> None:
        await self._bus.emit(
            DraftReady(
                session_id=self._session_id,
                turn_id=self._turn_id,
                producer=self._producer,
                first_sentence=first_sentence,
                variant="plain",
                utterance_id=self._utterance_id,
            )
        )

    async def _stream(self, history: list[dict]) -> AsyncIterator[str]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

        if self._provider in ("mock", "fake"):
            async for token in self._stream_mock(messages):
                yield token
            return

        if self._provider in ("groq", "openai"):
            client = self._model_client
            if client is None:
                from interview.llm.client import GroqModelClient

                client = GroqModelClient(
                    bus=self._bus, session_id=self._session_id
                )
            async for token in client.stream_chat(
                "live_interviewer",
                messages,
                turn_id=self._turn_id,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            ):
                yield token
            return

        log.warning("Unknown provider '%s'; using mock LLM", self._provider)
        async for token in self._stream_mock(messages):
            yield token

    async def _stream_mock(self, _messages: list[dict]) -> AsyncIterator[str]:
        text = (
            "That's a solid foundation. How did you handle back-pressure "
            "when the pipeline fell behind?"
        )
        for word in text.split():
            yield word + " "
            await asyncio.sleep(0.01)
