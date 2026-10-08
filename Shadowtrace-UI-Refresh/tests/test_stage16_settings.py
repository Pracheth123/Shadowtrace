"""
Stage 16 — one settings source, one bounded retry layer, honest metadata.

Every provider here is a FAKE object standing in for the OpenAI SDK; no test in
this file makes a network call.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from interview.config import Settings
from interview.llm.client import GroqModelClient, ProviderCallFailed, reset_shared_limiter
from interview.llm.limiter import TurnCallBudgetExceeded

ROOT = Path(__file__).resolve().parents[1]


def make_settings(tmp_path: Path, **env) -> Settings:
    """Settings from an explicit (empty) .env file plus the given overrides."""
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    base = {
        "APP_ENV": "test",
        "ALLOW_MOCK_PROVIDERS": "1",
        "GROQ_API_KEY": "test-key-never-sent",
        "GROQ_REQUESTS_PER_MINUTE": "600",
        # conftest sets MOCK_LLM=1 in the process; these tests choose their own.
        "MOCK_LLM": "0",
    }
    base.update({k: str(v) for k, v in env.items()})
    return Settings(_env_file=env_file, **base)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_precedence_is_process_env_then_dotenv_then_default(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_MAX_RETRIES=5\nGROQ_TIMEOUT_S=9.5\n", encoding="utf-8")
    monkeypatch.setenv("GROQ_MAX_RETRIES", "2")
    monkeypatch.delenv("GROQ_TIMEOUT_S", raising=False)
    monkeypatch.delenv("GROQ_EVAL_TIMEOUT_S", raising=False)
    s = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert s.groq_max_retries == 2          # process environment wins
    assert s.groq_timeout_s == 9.5          # then .env
    assert s.groq_eval_timeout_s == 60.0    # then the default


def test_yaml_no_longer_overrides_env_settings(tmp_path) -> None:
    raw = yaml.safe_load((ROOT / "config" / "inference.yaml").read_text(encoding="utf-8"))
    assert "groq" not in raw, "inference.yaml must not carry GROQ_* values any more"
    reset_shared_limiter()
    s = make_settings(tmp_path, GROQ_MAX_RETRIES=1, MAX_MODEL_CALLS_PER_TURN=2, GROQ_TIMEOUT_S=4)
    client = GroqModelClient(settings=s)
    assert client.max_retries == 1
    assert client.max_calls_per_turn == 2
    assert client.timeout_for("live_interviewer") == 4.0
    assert client.timeout_for("evaluator") == s.groq_eval_timeout_s


def test_mock_llm_requires_mocks_to_be_allowed(tmp_path) -> None:
    with pytest.raises(ValueError, match="MOCK_LLM"):
        make_settings(tmp_path, MOCK_LLM=1, ALLOW_MOCK_PROVIDERS=0)
    with pytest.raises(ValueError, match="prod"):
        make_settings(tmp_path, APP_ENV="prod", ALLOW_MOCK_PROVIDERS=1)


def test_effective_modes_follow_keys_and_mock_controls(tmp_path) -> None:
    assert make_settings(tmp_path).interviewer_mode == "groq"
    assert make_settings(tmp_path, MOCK_LLM=1).interviewer_mode == "deterministic"
    assert make_settings(tmp_path, MOCK_LLM=1).evaluator_mode == "mock"
    off = make_settings(tmp_path, GROQ_API_KEY="", ALLOW_MOCK_PROVIDERS=0)
    assert off.interviewer_mode == "unavailable"
    assert off.evaluator_mode == "unavailable"


def test_public_settings_never_contain_a_secret(tmp_path) -> None:
    s = make_settings(tmp_path, GROQ_API_KEY="sk-SECRET-123456", DEEPGRAM_API_KEY="dg-SECRET-987")
    dumped = json.dumps(s.public_dict())
    assert "SECRET" not in dumped
    assert s.public_dict()["groq_budget"]["retries_meaning"].startswith("retries after the first")


def test_default_evaluator_refuses_mocks_when_they_are_off(monkeypatch) -> None:
    from interview.config import reset_settings_cache
    from interview.services.evaluation import NoEvaluator, default_evaluator

    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "0")
    monkeypatch.setenv("MOCK_LLM", "0")
    monkeypatch.setenv("GROQ_API_KEY", "")
    reset_settings_cache()
    try:
        with pytest.raises(NoEvaluator):
            default_evaluator(use_mock_llm=True)
    finally:
        monkeypatch.undo()
        reset_settings_cache()


# ---------------------------------------------------------------------------
# One bounded retry layer
# ---------------------------------------------------------------------------


class FakeStatusError(Exception):
    """FAKE provider error carrying a status code and optional Retry-After."""

    def __init__(self, status: int, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
        self.response = SimpleNamespace(status_code=status, headers=headers)


def fake_sdk(plan: list) -> SimpleNamespace:
    """FAKE AsyncOpenAI: each create() consumes the next item (exception or reply)."""
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        item = plan.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    sdk.calls = calls
    return sdk


def reply(model: str, text: str = '{"ok": true}', finish: str = "stop"):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(
            model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "queue_time": 0.01}
        ),
        choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text, tool_calls=None))],
    )


def client_with(tmp_path, plan, **env) -> GroqModelClient:
    reset_shared_limiter()
    client = GroqModelClient(settings=make_settings(tmp_path, **env))
    client._sdk = fake_sdk(plan)
    return client


async def test_sdk_retries_are_disabled_and_timeout_is_set(tmp_path, monkeypatch) -> None:
    import openai

    captured: dict = {}

    class CaptureAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(openai, "AsyncOpenAI", CaptureAsyncOpenAI)
    reset_shared_limiter()
    client = GroqModelClient(settings=make_settings(tmp_path, GROQ_TIMEOUT_S=7))
    client._openai()
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 7.0
    assert "api_key" in captured  # passed to the SDK, never logged


async def test_retries_are_bounded_then_one_fallback_hop(tmp_path) -> None:
    errors = [FakeStatusError(503, retry_after=0) for _ in range(6)]
    client = client_with(tmp_path, errors, GROQ_MAX_RETRIES=2)
    with pytest.raises(ProviderCallFailed) as info:
        await client.chat("evaluator", [{"role": "user", "content": "x"}])
    meta = info.value.meta
    # 1 + 2 retries on the primary, then 1 + 2 on the fallback. Never more.
    assert meta.attempts == 6
    assert meta.fallback_used is True
    assert info.value.category == "provider_unavailable"
    assert len(client._sdk.calls) == 6
    models = [call["model"] for call in client._sdk.calls]
    assert models[:3] == ["openai/gpt-oss-120b"] * 3
    assert models[3:] == ["qwen/qwen3.8-27b"] * 3


async def test_authentication_failure_is_not_retried_or_failed_over(tmp_path) -> None:
    client = client_with(tmp_path, [FakeStatusError(401)], GROQ_MAX_RETRIES=3)
    with pytest.raises(ProviderCallFailed) as info:
        await client.chat("evaluator", [{"role": "user", "content": "x"}])
    assert info.value.category == "auth"
    assert len(client._sdk.calls) == 1


async def test_deadline_stops_retries_instead_of_waiting(tmp_path) -> None:
    client = client_with(tmp_path, [FakeStatusError(429, retry_after=5)] * 4, GROQ_MAX_RETRIES=3)
    started = time.monotonic()
    with pytest.raises(ProviderCallFailed) as info:
        await client.chat("live_interviewer", [{"role": "user", "content": "x"}], deadline_s=1.0)
    assert time.monotonic() - started < 1.5
    assert info.value.category == "deadline"
    assert len(client._sdk.calls) == 1


async def test_slow_sdk_setup_does_not_spend_the_deadline(tmp_path, monkeypatch) -> None:
    # Cold start: building the SDK took longer than the whole live deadline,
    # and the first turn failed "before attempt 1" without sending a request.
    import openai

    plan = [reply("openai/gpt-oss-20b")]

    def slow_sdk(**_kwargs):
        time.sleep(1.2)  # FAKE cold import + TLS setup, longer than deadline_s
        return fake_sdk(plan)

    monkeypatch.setattr(openai, "AsyncOpenAI", slow_sdk)
    reset_shared_limiter()
    client = GroqModelClient(settings=make_settings(tmp_path))
    result = await client.chat("live_interviewer", [{"role": "user", "content": "x"}], deadline_s=1.0)
    assert result["meta"]["attempts"] == 1
    assert len(client._sdk.calls) == 1


async def test_metadata_reports_the_model_actually_used_and_tokens(tmp_path) -> None:
    client = client_with(
        tmp_path,
        [FakeStatusError(404), reply("qwen/qwen3.8-27b")],
        GROQ_MAX_RETRIES=2,
    )
    result = await client.chat("evaluator", [{"role": "user", "content": "x"}], json_object=True)
    meta = result["meta"]
    assert meta["model_requested"] == "openai/gpt-oss-120b"
    assert meta["model_used"] == "qwen/qwen3.8-27b"
    assert meta["fallback_used"] is True
    assert meta["attempts"] == 2
    assert meta["usage"]["total_tokens"] == 120
    assert "test-key" not in json.dumps(meta)
    # Each request carries its own timeout; JSON mode is passed through.
    assert all("timeout" in call for call in client._sdk.calls)
    assert client._sdk.calls[-1]["response_format"] == {"type": "json_object"}


async def test_live_turn_budget_is_enforced(tmp_path) -> None:
    client = client_with(tmp_path, [reply("openai/gpt-oss-20b")] * 3, MAX_MODEL_CALLS_PER_TURN=2)
    for _ in range(2):
        await client.chat("live_interviewer", [{"role": "user", "content": "x"}], turn_id="turn-1")
    with pytest.raises(TurnCallBudgetExceeded):
        await client.chat("live_interviewer", [{"role": "user", "content": "x"}], turn_id="turn-1")


async def test_model_proposer_falls_back_within_the_live_deadline(tmp_path) -> None:
    """A slow/failed model never leaves the candidate waiting past the deadline."""
    from interview.packs.model import load_pack
    from interview.session.guard import GuardState
    from interview.session.proposer import ModelProposer, ProposalContext
    from interview.session.roles import hr_role

    client = client_with(tmp_path, [FakeStatusError(429, retry_after=10)] * 8, GROQ_MAX_RETRIES=3)
    proposer = ModelProposer(hr_role(), client, deadline_s=1.0)
    pack = load_pack("hr-core")
    state = GuardState(
        pack=pack, intensity="realistic", covered=(), outstanding=tuple(pack.spine_ids()),
        depth_on_current=0, time_remaining_s=600.0, claim_ids=frozenset(),
        claim_competency={}, transcript_texts=(),
    )
    started = time.monotonic()
    move = await proposer.propose(ProposalContext(spec=hr_role(), state=state, turn_id="t1"))
    assert time.monotonic() - started < 1.6
    assert move.action == "ask_spine"
    assert proposer.fallbacks_used == 1
    assert "deadline" in proposer.last_fallback_reason or "rate" in proposer.last_fallback_reason
