"""
Groq model client — OpenAI Python SDK against https://api.groq.com/openai/v1.

All live / real-model paths go through this module. Unit and replay tests must
use FakeLlm and never construct GroqModelClient with a real key.

Stage 16: one retry layer, bounded by a deadline.

Before this stage four layers could multiply on one evaluation: the OpenAI SDK
retried twice on its own, this client retried 429s up to `max_retries_on_429`
(read from YAML, ignoring `GROQ_MAX_RETRIES`), a hard failure was retried on the
fallback model, and the evaluator retried the whole call three more times.
Now:

  - The SDK's own retries are **off** (`max_retries=0`) and every HTTP request
    has a timeout (`GROQ_TIMEOUT_S`, or `GROQ_EVAL_TIMEOUT_S` for evaluation).
  - This client retries *retryable* failures only (429, 408, 409, 5xx, timeout,
    connection) up to `GROQ_MAX_RETRIES` times — retries, not attempts — honouring
    `Retry-After` when the provider sends one.
  - A non-retryable failure (or retries exhausted) makes **one** hop to the
    role's fallback model, except for authentication failures, which a second
    model cannot fix.
  - Everything — limiter wait, retries, back-off, fallback — must fit inside an
    optional overall `deadline_s`. When it is spent the call fails with
    category `deadline` instead of starting another request.

Every call returns (or raises with) a metadata block — provider, requested and
actually-used model, fallback used, attempts, limiter wait, request seconds and
token usage when the provider reports it — so evaluation records say what
really happened. The metadata never contains the key or the prompt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
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

ErrorCategory = Literal[
    "rate_limited",
    "timeout",
    "provider_unavailable",
    "auth",
    "invalid_request",
    "model_unavailable",
    "deadline",
    "budget_exceeded",
    "unknown",
]

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "inference.yaml"

# Shared across the process so all roles share one RPM budget on one key.
_SHARED_LIMITER: RpmLimiter | None = None

# Back-off between retries, before any Retry-After the provider sends.
_BACKOFF_START_S = 0.5
_BACKOFF_MAX_S = 8.0
# Do not start a request with less than this left on the deadline.
_MIN_REQUEST_WINDOW_S = 0.5


def _load_llm_config() -> dict:
    """Generation defaults only (max_tokens, temperature). See config.py."""
    load_dotenv()
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_shared_limiter(rpm: int | None = None) -> RpmLimiter:
    """
    The process-wide limiter. The first caller's rpm wins; every client built
    from settings passes the same GROQ_REQUESTS_PER_MINUTE, so they agree.
    """
    global _SHARED_LIMITER
    if _SHARED_LIMITER is None:
        from interview.config import get_settings

        settings = get_settings()
        _SHARED_LIMITER = RpmLimiter(
            rpm or 30,
            live_reserved_share=settings.live_reserved_share,
            background_max_defer_s=settings.background_max_defer_s,
        )
    return _SHARED_LIMITER


# Roles whose calls yield to live interviewer turns in the shared limiter.
BACKGROUND_ROLES = frozenset({"evaluator", "roadmap", "indexer"})


def priority_for(role: str) -> str:
    return "background" if role in BACKGROUND_ROLES else "live"


def reset_shared_limiter() -> None:
    """Tests only."""
    global _SHARED_LIMITER
    _SHARED_LIMITER = None


@dataclass
class CallMeta:
    """What one logical call actually did. Safe to persist: no key, no prompt."""

    provider: str = "groq"
    role: str = ""
    model_requested: str = ""
    model_used: str = ""
    fallback_used: bool = False
    attempts: int = 0
    limiter_wait_s: float = 0.0
    request_s: float = 0.0
    backoff_s: float = 0.0
    total_s: float = 0.0
    finish_reason: str | None = None
    # How structured output was requested from the model that answered:
    # "json_schema_strict", "json_object" or None (free text).
    output_mode: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error_category: str | None = None
    status_codes: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "role": self.role,
            "model_requested": self.model_requested,
            "model_used": self.model_used,
            "fallback_used": self.fallback_used,
            "attempts": self.attempts,
            "limiter_wait_s": round(self.limiter_wait_s, 3),
            "request_s": round(self.request_s, 3),
            "backoff_s": round(self.backoff_s, 3),
            "total_s": round(self.total_s, 3),
            "finish_reason": self.finish_reason,
            "output_mode": self.output_mode,
            "usage": dict(self.usage),
            "error_category": self.error_category,
            "status_codes": list(self.status_codes),
        }


class ProviderCallFailed(RuntimeError):
    """A model call failed after the bounded retry budget. Carries metadata."""

    def __init__(self, message: str, *, category: str, meta: CallMeta) -> None:
        super().__init__(message)
        self.category = category
        self.meta = meta


def classify(exc: BaseException) -> tuple[str, bool, int | None]:
    """(category, retryable, status) for a provider exception."""
    status = _status_of(exc)
    name = exc.__class__.__name__.casefold()
    text = str(exc).casefold()
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in name:
        return "timeout", True, status
    if "connection" in name or "connect" in name:
        return "provider_unavailable", True, status
    if status == 429 or "429" in text and status is None:
        return "rate_limited", True, status
    if status in (401, 403):
        return "auth", False, status
    if status in (408, 409):
        return "timeout", True, status
    if status is not None and status >= 500:
        return "provider_unavailable", True, status
    if status == 404 or "model_not_found" in text or "decommissioned" in text:
        return "model_unavailable", False, status
    if status is not None and 400 <= status < 500:
        return "invalid_request", False, status
    return "unknown", False, status


def _status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _retry_after_s(exc: BaseException) -> float | None:
    """Provider-supplied Retry-After, in seconds, when present."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        value = headers.get("retry-after")
    except Exception:  # noqa: BLE001
        return None
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


class GroqModelClient:
    """One client for every role. Model ids and budgets come from settings."""

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
        groq = self._cfg.get("groq", {}) if config is not None else {}

        # The validated settings object is the single source of truth. An
        # explicit `config` dict (tests) is the only other input, and only when
        # no settings object is given.
        if settings is None and config is None:
            from interview.config import get_settings

            settings = get_settings()
        self._settings = settings

        if api_key is not None:
            self._api_key = api_key
        elif settings is not None and settings.has_groq:
            self._api_key = settings.groq_api_key.get_secret_value()
        else:
            self._api_key = groq_api_key() or ""

        if settings is not None:
            self._base_url = settings.groq_base_url
            self._roles: dict[str, str] = {
                "live_interviewer": settings.model_live_interviewer,
                "indexer": settings.model_indexer,
                "evaluator": settings.model_evaluator,
                "roadmap": settings.model_roadmap,
            }
            self._failover_roles: dict[str, str] = {
                "live_interviewer": settings.model_fallback_fast,
                "indexer": settings.model_fallback_fast,
                "evaluator": settings.model_fallback_quality,
                "roadmap": settings.model_fallback_quality,
            }
            self._max_retries = int(settings.groq_max_retries)
            self._timeout_s = float(settings.groq_timeout_s)
            self._eval_timeout_s = float(settings.groq_eval_timeout_s)
            self._max_per_turn = int(settings.max_model_calls_per_turn)
            rpm = int(settings.groq_requests_per_minute)
            # Degrading to the local mock is a development convenience only and
            # follows ALLOW_MOCK_PROVIDERS. In production a failed provider
            # surfaces as an error, never as canned text.
            self._failover_to_mock = bool(settings.mocks_allowed)
            self._strict_schema_models = frozenset(settings.eval_strict_schema_models)
        else:
            self._base_url = groq.get("base_url", GROQ_BASE_URL)
            self._roles = dict(groq.get("roles") or {})
            self._failover_roles = dict(groq.get("failover_roles") or {})
            self._max_retries = int(groq.get("max_retries", groq.get("max_retries_on_429", 3)))
            self._timeout_s = float(groq.get("timeout_s", 12.0))
            self._eval_timeout_s = float(groq.get("eval_timeout_s", 60.0))
            self._max_per_turn = int(groq.get("max_calls_per_turn", 3))
            rpm = int(groq.get("requests_per_minute", 30))
            self._failover_to_mock = bool(groq.get("failover_to_mock", False))
            self._strict_schema_models = frozenset(groq.get("strict_schema_models") or ())
        self._limiter = get_shared_limiter(rpm)
        self._budget = TurnCallBudget(self._max_per_turn)
        self._bus = bus
        self._session_id = session_id
        self._default_max_tokens = int(self._cfg.get("max_tokens", 120))
        self._default_temperature = float(self._cfg.get("temperature", 0.7))
        self._sdk = None

    # ------------------------------------------------------------------

    @property
    def session_call_count(self) -> int:
        return self._budget.session_total

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def max_calls_per_turn(self) -> int:
        return self._max_per_turn

    def model_for(self, role: ModelRole) -> str:
        if role not in self._roles:
            raise KeyError(f"No model configured for role '{role}'")
        return self._roles[role]

    def failover_model_for(self, role: ModelRole) -> str | None:
        """Second model to try for `role` on a hard failure, if one is configured."""
        fallback = self._failover_roles.get(role)
        if not fallback or fallback == self._roles.get(role):
            return None
        return fallback

    def supports_strict_schema(self, model: str) -> bool:
        """Configured (EVAL_STRICT_SCHEMA_MODELS), never inferred from the name."""
        return model in self._strict_schema_models

    def response_format_for(
        self, model: str, *, json_object: bool, json_schema: dict[str, Any] | None
    ) -> tuple[dict[str, Any] | None, str | None]:
        """
        (response_format, output_mode) for one model.

        Decided per model, because the fallback hop may land on a model that
        does not accept a strict schema; that model still gets JSON-object mode.
        """
        if json_schema is not None and self.supports_strict_schema(model):
            return (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": json_schema["name"],
                        "schema": json_schema["schema"],
                        "strict": True,
                    },
                },
                "json_schema_strict",
            )
        if json_object or json_schema is not None:
            # Server-side JSON mode. Without it these models prepend prose.
            return {"type": "json_object"}, "json_object"
        return None, None

    def timeout_for(self, role: str) -> float:
        return self._eval_timeout_s if role in ("evaluator", "roadmap") else self._timeout_s

    async def _note_fallback(self, kind: str, detail: str, turn_id: str | None) -> None:
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
            raise ProviderCallFailed(
                "GROQ_API_KEY missing. Set it in .env (never commit). "
                "Unit tests must use FakeLlm, not GroqModelClient.",
                category="auth",
                meta=CallMeta(),
            )
        if self._sdk is None:
            # max_retries=0: this module is the only retry layer.
            self._sdk = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                max_retries=0,
                timeout=self._timeout_s,
            )
        return self._sdk

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

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
        json_schema: dict[str, Any] | None = None,
        deadline_s: float | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """
        Non-streaming chat. Returns {"content", "tool_calls", "finish_reason",
        "meta"}; raises `ProviderCallFailed` (with `.meta`) when the bounded
        budget is spent.

        `json_schema` ({"name", "schema"}) asks for strict schema output on
        models listed in EVAL_STRICT_SCHEMA_MODELS and JSON-object mode on any
        other model; `json_object` alone asks for JSON-object mode.
        """
        model = self.model_for(role)
        call_index, turn_call_index = await self._budget.begin_call(turn_id)
        started = time.monotonic()
        deadline: float | None = None
        meta = CallMeta(role=role, model_requested=model)
        request = {
            "messages": messages,
            "max_tokens": max_tokens or self._default_max_tokens,
            "temperature": self._default_temperature if temperature is None else temperature,
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"
        output = {"json_object": json_object, "json_schema": json_schema}
        per_request = timeout_s or self.timeout_for(role)
        ok = False
        error = ""
        try:
            # Building the SDK (first use imports openai and sets up TLS) is a
            # local one-time cost, not provider latency. On a cold start it can
            # take longer than the whole live deadline, so the clock starts after.
            self._openai()
            if deadline_s:
                deadline = time.monotonic() + deadline_s
            try:
                result = await self._attempts(
                    model, request, meta, deadline, per_request, **output
                )
            except ProviderCallFailed as exc:
                fallback = self.failover_model_for(role)
                if (
                    not fallback
                    or exc.category in ("auth", "deadline", "budget_exceeded")
                    or (deadline is not None and deadline - time.monotonic() < _MIN_REQUEST_WINDOW_S)
                ):
                    raise
                await self._note_fallback(
                    "provider_failover",
                    f"{role}: {model} failed ({exc.category}); retrying on {fallback}",
                    turn_id,
                )
                meta.fallback_used = True
                result = await self._attempts(
                    fallback, request, meta, deadline, per_request, **output
                )
            ok = True
            meta.total_s = time.monotonic() - started
            result["meta"] = meta.as_dict()
            return result
        except ProviderCallFailed as exc:
            meta.total_s = time.monotonic() - started
            meta.error_category = exc.category
            error = str(exc)
            exc.meta = meta
            raise
        finally:
            await self._emit_model_call(
                role=role,
                model=meta.model_used or model,
                turn_id=turn_id,
                call_index=call_index,
                turn_call_index=turn_call_index,
                latency_ms=(time.monotonic() - started) * 1000,
                ok=ok,
                status_code=meta.status_codes[-1] if meta.status_codes else None,
                error=error,
            )

    async def _attempts(
        self,
        model: str,
        request: dict[str, Any],
        meta: CallMeta,
        deadline: float | None,
        per_request_timeout: float,
        *,
        json_object: bool = False,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Up to 1 + max_retries attempts on one model, inside the deadline."""
        client = self._openai()
        response_format, output_mode = self.response_format_for(
            model, json_object=json_object, json_schema=json_schema
        )
        if response_format is not None:
            request = {**request, "response_format": response_format}
        meta.output_mode = output_mode
        delay = _BACKOFF_START_S
        last: BaseException | None = None
        category = "unknown"
        for attempt in range(self._max_retries + 1):
            timeout = per_request_timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining < _MIN_REQUEST_WINDOW_S:
                    raise ProviderCallFailed(
                        f"deadline reached before attempt {attempt + 1} on {model}"
                        + (f" (last error: {_brief(last)})" if last else ""),
                        category="deadline",
                        meta=meta,
                    )
                timeout = min(timeout, remaining)
            waited = time.monotonic()
            if deadline is not None:
                try:
                    await asyncio.wait_for(
                        self._limiter.acquire(priority_for(meta.role)),
                        timeout=max(0.01, deadline - time.monotonic()),
                    )
                except asyncio.TimeoutError:
                    meta.limiter_wait_s += time.monotonic() - waited
                    raise ProviderCallFailed(
                        "deadline reached while waiting for the client-side rate limiter "
                        f"({self._limiter.rpm} requests/minute)",
                        category="deadline",
                        meta=meta,
                    ) from None
            else:
                await self._limiter.acquire(priority_for(meta.role))
            meta.limiter_wait_s += time.monotonic() - waited
            meta.attempts += 1
            sent = time.monotonic()
            try:
                resp = await client.chat.completions.create(
                    model=model, timeout=timeout, **request
                )
            except Exception as exc:  # noqa: BLE001 — classified below
                meta.request_s += time.monotonic() - sent
                last = exc
                category, retryable, status = classify(exc)
                if status is not None:
                    meta.status_codes.append(status)
                if not retryable or attempt >= self._max_retries:
                    raise ProviderCallFailed(
                        f"{model}: {category} after {attempt + 1} attempt(s): {_brief(exc)}",
                        category=category,
                        meta=meta,
                    ) from exc
                wait = _retry_after_s(exc)
                wait = delay if wait is None else min(wait, _BACKOFF_MAX_S * 2)
                if deadline is not None and time.monotonic() + wait > deadline - _MIN_REQUEST_WINDOW_S:
                    raise ProviderCallFailed(
                        f"{model}: {category}; the next retry would pass the deadline",
                        category="deadline",
                        meta=meta,
                    ) from exc
                log.warning(
                    "Groq %s on %s (attempt %s/%s); retrying in %.1fs",
                    category, model, attempt + 1, self._max_retries + 1, wait,
                )
                meta.backoff_s += wait
                await asyncio.sleep(wait)
                delay = min(delay * 2, _BACKOFF_MAX_S)
                continue
            meta.request_s += time.monotonic() - sent
            meta.model_used = getattr(resp, "model", None) or model
            usage = getattr(resp, "usage", None)
            if usage is not None:
                meta.usage = _usage_dict(usage)
            choice = resp.choices[0]
            msg = choice.message
            meta.finish_reason = choice.finish_reason
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
        raise ProviderCallFailed(  # pragma: no cover — loop always returns or raises
            f"{model}: retries exhausted", category=category, meta=meta
        )

    # ------------------------------------------------------------------
    # Streaming (freeform interviewer path)
    # ------------------------------------------------------------------

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
        then — only where mocks are allowed — to the local mock. Failover only
        happens before the first token: once the candidate has heard the start
        of a sentence, switching models would splice two answers together.
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
            status = _status_of(exc)
            if ok:
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
        """Fallback model, then (dev/test only) the local mock."""
        fallback = self.failover_model_for(role)
        category, _, _ = classify(cause)
        if fallback and category != "auth":
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
                        self._default_temperature if temperature is None else temperature
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
        delay = _BACKOFF_START_S
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            started = False
            try:
                stream = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    timeout=self._timeout_s,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        started = True
                        yield delta
                return
            except Exception as exc:  # noqa: BLE001
                _, retryable, _ = classify(exc)
                if started or not retryable or attempt >= self._max_retries:
                    raise
                wait = _retry_after_s(exc) or delay
                log.warning("Groq stream retry %s in %.1fs (%s)", attempt + 1, wait, _brief(exc))
                await asyncio.sleep(wait)
                delay = min(delay * 2, _BACKOFF_MAX_S)

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
                error=(error[:300] if error else None),
            )
        )


def _usage_dict(usage: Any) -> dict[str, Any]:
    """Token counts and provider timings, whatever shape the SDK returns."""
    if hasattr(usage, "model_dump"):
        raw = usage.model_dump()
    elif isinstance(usage, dict):
        raw = dict(usage)
    else:
        raw = {}
    keep = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "queue_time",
        "prompt_time",
        "completion_time",
        "total_time",
    ):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            keep[key] = value
    details = raw.get("completion_tokens_details") or {}
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
        keep["reasoning_tokens"] = details["reasoning_tokens"]
    return keep


def _brief(exc: BaseException | None) -> str:
    """Short, loggable reason. Never the full provider payload."""
    if exc is None:
        return ""
    text = str(exc).strip().splitlines()
    head = text[0] if text else exc.__class__.__name__
    return head[:160]


def _is_429(exc: BaseException) -> bool:
    return classify(exc)[0] == "rate_limited"


__all__ = [
    "CallMeta",
    "GROQ_BASE_URL",
    "GroqModelClient",
    "ModelRole",
    "ProviderCallFailed",
    "TurnCallBudgetExceeded",
    "classify",
    "get_shared_limiter",
    "reset_shared_limiter",
]
