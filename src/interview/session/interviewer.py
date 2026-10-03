"""
Lead Interviewer — Stage 3.

Input : conversation history (list of role/content dicts) + a short system prompt.
Output: streamed text. Emits `draft_ready` when the first sentence is complete.

Contract:
  - The interviewer's own conversation context is its state. No shared store.
  - Repo/resume content is data, never instructions (CLAUDE.md §7). When claims are
    included they live inside a clearly delimited <<CLAIMS>> block, never in the
    instruction part of the prompt.
  - The system prompt is short and fixed. It does not change between turns.
  - draft_ready fires on the FIRST sentence, not the whole reply, so TTS can start
    while generation continues (target: ~450ms total endpoint→first_audio).
  - variant is always "plain" here; "concession" is set by the turn controller in
    stage 7 when route_decision == "unclear".

Model choice: measured in tools/bench_ttft.py before this file is used in production.
The vendor/model is read from config/inference.yaml, not hardcoded here.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

import yaml

if TYPE_CHECKING:
    from interview.events.bus import EventBus

from interview.events.schema import DraftReady

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "inference.yaml"

# ── System prompt ──────────────────────────────────────────────────────────────
# Short and fixed. Claims live in a <<CLAIMS>> data block in the user message,
# never merged into these instructions.
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

    Usage::

        iv = LeadInterviewer(bus, session_id, turn_id, utterance_id)
        await iv.generate(conversation_history)
        # bus will have received draft_ready + (later) full text via tts pipeline

    Standalone (no audio): feed a list of dicts [{"role":..,"content":..}] and await.
    """

    def __init__(
        self,
        bus: "EventBus",
        session_id: str,
        turn_id: str,
        utterance_id: str | None = None,
        *,
        producer: str = "interviewer",
        on_first_sentence: None | (lambda s: None) = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._turn_id = turn_id
        self._utterance_id = utterance_id or str(uuid.uuid4())
        self._producer = producer
        self._on_first_sentence = on_first_sentence
        cfg = _load_inference_config()
        self._provider = cfg.get("provider", "openai")
        self._model = cfg.get("model", "gpt-4o-mini")
        self._api_key = cfg.get("api_key", "")
        self._max_tokens = cfg.get("max_tokens", 120)
        self._temperature = cfg.get("temperature", 0.7)

    async def generate(self, history: list[dict]) -> str:
        """
        Stream a response from the LLM. Returns the full text when done.
        Emits draft_ready on the first sentence.
        """
        full_text = ""
        first_sentence_emitted = False
        buffer = ""

        async for token in self._stream(history):
            full_text += token
            buffer += token

            if not first_sentence_emitted:
                # Look for first sentence boundary
                parts = _SENTENCE_END.split(buffer, maxsplit=1)
                if len(parts) > 1:
                    first_sentence = parts[0].strip()
                    buffer = parts[1]
                    first_sentence_emitted = True
                    await self._emit_draft_ready(first_sentence)
                    if self._on_first_sentence:
                        self._on_first_sentence(first_sentence)

        # If we finished without a sentence boundary (e.g. single-sentence reply)
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
        """Dispatch to the configured provider's streaming client."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

        if self._provider == "openai":
            async for token in self._stream_openai(messages):
                yield token
        elif self._provider == "anthropic":
            async for token in self._stream_anthropic(messages):
                yield token
        elif self._provider == "google":
            async for token in self._stream_google(messages):
                yield token
        elif self._provider in ("mock", "fake"):
            async for token in self._stream_mock(messages):
                yield token
        else:
            # Mock fallback so the rest of the system is testable without an API key
            log.warning("Unknown provider '%s'; using mock LLM", self._provider)
            async for token in self._stream_mock(messages):
                yield token

    async def _stream_openai(self, messages: list[dict]) -> AsyncIterator[str]:
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ImportError:
            log.error("openai package not installed")
            return
        client = AsyncOpenAI(api_key=self._api_key)
        stream = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

    async def _stream_anthropic(self, messages: list[dict]) -> AsyncIterator[str]:
        try:
            import anthropic  # type: ignore
        except ImportError:
            log.error("anthropic package not installed")
            return
        # Anthropic uses a separate system param
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        rest = [m for m in messages if m["role"] != "system"]
        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        async with client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=rest,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def _stream_google(self, messages: list[dict]) -> AsyncIterator[str]:
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError:
            log.error("google-generativeai package not installed")
            return
        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(self._model)
        # Build a simple single-turn prompt (multi-turn formatting differs per SDK version)
        full_prompt = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in messages
        )
        response = await asyncio.to_thread(
            model.generate_content, full_prompt, stream=True
        )
        for chunk in response:
            yield chunk.text or ""

    async def _stream_mock(self, _messages: list[dict]) -> AsyncIterator[str]:
        """Yield a canned response token-by-token for offline testing."""
        text = "That's a solid foundation. How did you handle back-pressure when the pipeline fell behind?"
        for word in text.split():
            yield word + " "
            await asyncio.sleep(0.01)
