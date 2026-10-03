"""
Groq model client — OpenAI Python SDK against https://api.groq.com/openai/v1.

All live / real-model paths go through this module. Unit and replay tests must
use FakeLlm and never construct GroqModelClient with a real key.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal

import yaml

from interview.llm.env import groq_api_key, load_dotenv
from interview.llm.limiter import RpmLimiter, TurnCallBudget, TurnCallBudgetExceeded

if TYPE_CHECKING:
    from interview.events.bus import EventBus

log = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

ModelRole = Literal[
    "live_interviewer",
    "indexer",
    "evaluator",
    "roadmap",
]

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "inference.yaml"

# Shared across the process so all roles share one RPM budget on one key.
_SHARED_LIMITER: RpmLimiter | None = None


def _load_llm_config() -> dict:
    load_dotenv()
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_shared_limiter(rpm: int | None = None) -> RpmLimiter:
    global _SHARED_LIMITER
    if _SHARED_LIMITER is None:
        cfg = _load_llm_config()
        groq = cfg.get("groq", {})
        _SHARED_LIMITER = RpmLimiter(rpm or int(groq.get("requests_per_minute", 30)))
    return _SHARED_LIMITER


class GroqModelClient:
    """
    One client for every role. Model IDs come from config/inference.yaml `roles`.
    """

    def __init__(
        self,
        *,
        bus: "EventBus | None" = None,
        session_id: str = "",
        api_key: str | None = None,
        config: dict | None = None,
    ) -> None:
        load_dotenv()
        self._cfg = config if config is not None else _load_llm_config()
        groq = self._cfg.get("groq", {})
        self._api_key = (api_key if api_key is not None else groq_api_key()) or ""
        self._base_url = groq.get("base_url", GROQ_BASE_URL)
        self._roles: dict[str, str] = dict(groq.get("roles") or {})
        self._max_retries = int(groq.get("max_retries_on_429", 5))
        self._max_per_turn = int(groq.get("max_calls_per_turn", 3))
        self._limiter = get_shared_limiter(int(groq.get("requests_per_minute", 30)))
        self._budget = TurnCallBudget(self._max_per_turn)
        self._bus = bus
        self._session_id = session_id
        self._default_max_tokens = int(self._cfg.get("max_tokens", 120))
        self._default_temperature = float(self._cfg.get("temperature", 0.7))

    @property
    def session_call_count(self) -> int:
        return self._budget.session_total

    def model_for(self, role: ModelRole) -> str:
        if role not in self._roles:
            raise KeyError(f"No model configured for role '{role}' in inference.yaml")
        return self._roles[role]

    def _openai(self):
        from openai import AsyncOpenAI  # type: ignore

        if not self._api_key:
            raise RuntimeError(
                "GROQ_API_KEY missing. Set it in .env (never commit). "
                "Unit tests must use FakeLlm, not GroqModelClient."
            )
        return AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

    async def stream_chat(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        *,
        turn_id: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Stream completion tokens for `role`. Counts against turn/session budgets."""
        model = self.model_for(role)
        call_index, turn_call_index = await self._budget.begin_call(turn_id)
        t0 = time.monotonic()
        status: int | None = None
        ok = False
        err_msg = ""

        try:
            async for token in self._stream_with_retry(
                model=model,
                messages=messages,
                max_tokens=max_tokens or self._default_max_tokens,
                temperature=(
                    self._default_temperature if temperature is None else temperature
                ),
            ):
                ok = True
                yield token
        except TurnCallBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001 — logged as model_call failure
            err_msg = str(exc)
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            raise
        finally:
            await self._emit_model_call(
                role=role,
                model=model,
                turn_id=turn_id,
                call_index=call_index,
                turn_call_index=turn_call_index,
                latency_ms=(time.monotonic() - t0) * 1000,
                ok=ok,
                status_code=status,
                error=err_msg,
            )

    async def chat(
        self,
        role: ModelRole,
        messages: list[dict[str, Any]],
        *,
        turn_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat (tool loops for indexer / evaluator / roadmap)."""
        model = self.model_for(role)
        call_index, turn_call_index = await self._budget.begin_call(turn_id)
        t0 = time.monotonic()
        status: int | None = None
        ok = False
        err_msg = ""
        result: dict[str, Any] = {}

        try:
            result = await self._chat_with_retry(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens or self._default_max_tokens,
                temperature=(
                    self._default_temperature if temperature is None else temperature
                ),
            )
            ok = True
            return result
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            raise
        finally:
            await self._emit_model_call(
                role=role,
                model=model,
                turn_id=turn_id,
                call_index=call_index,
                turn_call_index=turn_call_index,
                latency_ms=(time.monotonic() - t0) * 1000,
                ok=ok,
                status_code=status,
                error=err_msg,
            )

    async def _stream_with_retry(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[str]:
        client = self._openai()
        delay = 0.5
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta
                return
            except Exception as exc:  # noqa: BLE001
                if not _is_429(exc) or attempt >= self._max_retries:
                    raise
                log.warning("Groq 429 on stream (attempt %s); backing off %.1fs", attempt + 1, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 16.0)

    async def _chat_with_retry(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        client = self._openai()
        delay = 0.5
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                resp = await client.chat.completions.create(**kwargs)
                choice = resp.choices[0]
                msg = choice.message
                return {
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                        for tc in (msg.tool_calls or [])
                    ],
                    "finish_reason": choice.finish_reason,
                }
            except Exception as exc:  # noqa: BLE001
                if not _is_429(exc) or attempt >= self._max_retries:
                    raise
                log.warning("Groq 429 on chat (attempt %s); backing off %.1fs", attempt + 1, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 16.0)
        raise RuntimeError("Groq chat retries exhausted")

    async def _emit_model_call(
        self,
        *,
        role: str,
        model: str,
        turn_id: str | None,
        call_index: int,
        turn_call_index: int,
        latency_ms: float,
        ok: bool,
        status_code: int | None,
        error: str,
    ) -> None:
        if self._bus is None:
            return
        from interview.events.schema import ModelCall

        await self._bus.emit(
            ModelCall(
                session_id=self._session_id or "unknown",
                turn_id=turn_id,
                producer="groq_client",
                role=role,
                model=model,
                call_index=call_index,
                turn_call_index=turn_call_index,
                latency_ms=round(latency_ms, 1),
                ok=ok,
                status_code=status_code,
                error=error or None,
            )
        )


def _is_429(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 429:
        return True
    return "429" in str(exc)


__all__ = [
    "GROQ_BASE_URL",
    "GroqModelClient",
    "ModelRole",
    "TurnCallBudgetExceeded",
    "get_shared_limiter",
]
