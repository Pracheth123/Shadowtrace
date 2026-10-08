"""Pytest hooks — live Groq tests are skipped unless explicitly selected."""

from __future__ import annotations

import os

import pytest

# The offline suite must not read real credentials or model choices from the
# developer's `.env`. Before this, a `.env` with a Deepgram key turned every
# server test into a real voice session that waited forever for an
# `audio_start`, and a `.env` model override broke the failover test. Values set
# here win over `.env` (both loaders refuse to override an existing variable).
# Live tests (`-m live`) opt back in explicitly.
_OFFLINE_ENV = {
    "APP_ENV": "test",
    "ALLOW_MOCK_PROVIDERS": "1",
    "GROQ_API_KEY": "",
    "DEEPGRAM_API_KEY": "",
    "MODEL_LIVE_INTERVIEWER": "openai/gpt-oss-20b",
    "MODEL_INDEXER": "openai/gpt-oss-20b",
    "MODEL_EVALUATOR": "openai/gpt-oss-120b",
    "MODEL_ROADMAP": "openai/gpt-oss-120b",
    "MODEL_FALLBACK_FAST": "qwen/qwen3.8-27b",
    "MODEL_FALLBACK_QUALITY": "qwen/qwen3.8-27b",
    # The developer's .env may set MOCK_LLM=0 with ALLOW_MOCK_PROVIDERS=0;
    # offline tests always run the labelled deterministic/mock providers.
    "MOCK_LLM": "1",
    # Never sweep anything during tests; retention tests call the sweep directly.
    "GUEST_RETENTION_DAYS": "0",
}


def _is_live_run(config: pytest.Config) -> bool:
    marker = config.getoption("-m", default="") or ""
    return "live" in marker and "not live" not in marker


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live: real Groq API calls (skipped by default; run with -m live)"
    )
    if _is_live_run(config):
        return
    for key, value in _OFFLINE_ENV.items():
        os.environ[key] = value
    from interview.config import reset_settings_cache

    reset_settings_cache()
