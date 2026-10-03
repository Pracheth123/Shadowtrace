"""
Groq model client — OpenAI Python SDK against https://api.groq.com/openai/v1.

All live / real-model paths go through this module. Unit and replay tests must
use FakeLlm and never construct GroqModelClient with a real key.

Stage 11 adds provider failover. A 429 is still a back-off-and-retry on the same
model (the limiter owns that). A *hard* failure — auth, a model that has been
withdrawn, a 5xx, a connection error — is different: retrying the same model
cannot help. So the role is retried once on its configured fallback model, and
if that also fails and `failover_to_mock` is on, the call degrades to the local
mock rather than dropping the candidate mid-session. Each step emits a
`fallback_used` event, so a session log says exactly which path was taken.
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
        settings=None,
    ) -> None:
        load_dotenv()
        self._cfg = config if config is not None else _load_llm_config()
        groq = self._cfg.get("groq", {})

        # Stage 14: the validated settings object is the source of truth for
        # model ids. It rejects models the provider has withdrawn, which the
        # YAML could not — the first live failover of the interviewer went to
        # `gemma2-9b-it` and came back 400 model_decommissioned. YAML now only
        # fills gaps.
        if settings is None and config is None:
            try:
                from interview.config import get_settings

                settings = get_settings()
            except Exception:  # noqa: BLE001 — YAML-only operation stays valid
                settings = None
        self._settings = settings

        if api_key is not None:
            self._api_key = api_key
        elif settings is not None and settings.has_groq:
            self._api_key = settings.groq_api_key.get_secret_value()
        else:
            self._api_key = groq_api_key() or ""

        self._base_url = (
            settings.groq_base_url if settings is not None
            else groq.get("base_url", GROQ_BASE_URL)
        )
        if settings is not None:
            self._roles: dict[str, str] = {
                "live_interviewer": settings.model_live_interviewer,
                "indexer": settings.model_indexer,
                "evaluator": settings.model_evaluator,
                "roadmap": settings.model_roadmap,
            }
        else:
            self._roles = dict(groq.get("roles") or {})
        self._max_retries = int(groq.get("max_retries_on_429", 5))
        if settings is not None:
            self._failover_roles: dict[str, str] = {
                "live_interviewer": settings.model_fallback_fast,
                "indexer": settings.model_fallback_fast,
                "evaluator": settings.model_fallback_quality,
                "roadmap": settings.model_fallback_quality,
            }
        else:
            self._failover_roles = dict(groq.get("failover_roles") or {})
        self._failover_to_mock = bool(groq.get("failover_to_mock", True))
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

    def failover_model_for(self, role: ModelRole) -> str | None:
        """Second model to try for `role` on a hard failure, if one is configured."""
        fallback = self._failover_roles.get(role)
        if not fallback or fallback == self._roles.get(role):
            return None
        return fallback

    async def _note_fallback(
        self,
        kind: str,
        detail: str,
        turn_id: str | None,
    ) -> None:
        if self._bus is None:
            return
        from interview.events.schema import FallbackUsed

        await self._bus.emit(
            FallbackUsed(
                session_id=self._session_id or "unknown",
                turn_id=turn_id,
                producer="groq_client",
                kind=kind,  # type: ignore[arg-type]
                detail=detail,
            )
        )

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
        """
        Stream completion tokens for `role`. Counts against turn/session budgets.

        On a hard failure the stream fails over to the role's fallback model,
        then to the mock. Failover only happens before the first token: once the
        candidate has heard the start of a sentence, switching models mid-stream
        would splice two different answers together.
        """
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
            if ok:
                # Already speaking. Do not splice a second model into the line.
                raise
            async for token in self._stream_failover(
                role=role,
                model=model,
                messages=messages,
                turn_id=turn_id,
                max_tokens=max_tokens,
                temperature=temperature,
                cause=exc,
            ):
                ok = True
                yield token
            if not ok:
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
        json_object: bool = False,
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
                json_object=json_object,
            )
            ok = True
            return result
        except TurnCallBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            err_msg = str(exc)
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            fallback = self.failover_model_for(role)
            if not fallback:
                raise
            await self._note_fallback(
                "provider_failover",
                f"{role}: {model} failed ({_brief(exc)}); retrying on {fallback}",
                turn_id,
            )
            result = await self._chat_with_retry(
                model=fallback,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens or self._default_max_tokens,
                temperature=(
                    self._default_temperature if temperature is None else temperature
                ),
                json_object=json_object,
            )
            ok = True
            return result
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

    async def _stream_failover(
        self,
        *,
        role: ModelRole,
        model: str,
        messages: list[dict[str, Any]],
        turn_id: str | None,
        max_tokens: int | None,
        temperature: float | None,
        cause: BaseException,
    ) -> AsyncIterator[str]:
        """Fallback model, then the local mock. Each hop logs `fallback_used`."""
        fallback = self.failover_model_for(role)
        if fallback:
            await self._note_fallback(
                "provider_failover",
                f"{role}: {model} failed ({_brief(cause)}); retrying on {fallback}",
                turn_id,
            )
            try:
                async for token in self._stream_with_retry(
                    model=fallback,
                    messages=messages,
                    max_tokens=max_tokens or self._default_max_tokens,
                    temperature=(
                        self._default_temperature
                        if temperature is None
                        else temperature
                    ),
                ):
                    yield token
                return
            except Exception as exc:  # noqa: BLE001
                cause = exc
                model = fallback

        if not self._failover_to_mock:
            return
        await self._note_fallback(
            "provider_failover",
            f"{role}: {model} failed ({_brief(cause)}); degraded to local mock",
            turn_id,
        )
        async for token in self._mock_stream(messages):
            yield token

    async def _mock_stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        """
        Last resort: the stage-3 mock, in-process.

        The mock is already a first-class part of this repo, so the degraded
        path is the same code the tests run against — not a second
        implementation that only ever runs in an outage.
        """
        from interview.mocks.fake_llm import FakeLlm

        fake = FakeLlm()
        async for token in fake.stream(messages):
            yield token

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
        json_object: bool = False,
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
                if json_object:
                    # Server-side JSON mode. Without it these models prepend
                    # prose to the object, which the proposal parser rejects.
                    kwargs["response_format"] = {"type": "json_object"}
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


def _brief(exc: BaseException) -> str:
    """Short, loggable reason. Never the full provider payload."""
    text = str(exc).strip().splitlines()
    head = text[0] if text else exc.__class__.__name__
    return head[:120]


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
