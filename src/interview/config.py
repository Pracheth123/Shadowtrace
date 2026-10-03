"""
One validated settings object, built from the environment.

Every secret and every tunable arrives here and nowhere else. Three rules this
module exists to enforce:

1. **Secrets stay server-side.** API keys are `SecretStr`, so a stray f-string,
   log line, repr or JSON dump prints `**********` instead of the key. The only
   way to get the real value is `.get_secret_value()`, which greps make easy to
   audit. `public_dict()` is what `/healthz` and the client may see.

2. **Mocks cannot run in production.** `APP_ENV=prod` with `ALLOW_MOCK_PROVIDERS=1`
   is a startup error, not a warning. The failure this prevents is the serious
   one: a missing API key quietly producing a "successful" interview out of
   silence and canned text, which looks like a real session in the log and in
   the candidate's report.

3. **Model and voice ids are checked against what the providers actually run.**
   The retired-model list is explicit, because a fallback that 404s is worse
   than no fallback — it burns the retry budget and still fails.

Verified against provider documentation on 2026-10-04 (see
docs/decisions/stage12_voice_providers.md for the fetch log):

  - Groq production models: llama-3.1-8b-instant, llama-3.3-70b-versatile,
    openai/gpt-oss-20b, openai/gpt-oss-120b. `gemma2-9b-it` is gone.
  - Deepgram STT: nova-3 supports `keyterm` (500-token budget); the older
    `keywords` parameter is Nova-2 only and is silently ignored by nova-3.
  - Deepgram TTS: aura-2-*-en is the current generation.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["dev", "test", "prod"]

# Models the provider has withdrawn. Naming them is what turns a silent 404
# mid-interview into a refusal at startup.
RETIRED_GROQ_MODELS = frozenset(
    {
        "gemma2-9b-it",
        "gemma-7b-it",
        "llama2-70b-4096",
        "llama-3.1-70b-versatile",
        "mixtral-8x7b-32768",
    }
)

# Groq production models as documented. Preview models are deliberately absent:
# the live speak path should not sit on an id that can be pulled without notice.
GROQ_PRODUCTION_MODELS = frozenset(
    {
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    }
)

# Deepgram encodings that are raw, framed PCM — the only ones this app sends,
# because it controls the conversion itself and declares what it sends.
DEEPGRAM_PCM_ENCODINGS = frozenset({"linear16", "linear32", "mulaw", "alaw"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `model_` is a Pydantic-reserved prefix; our fields avoid it.
        protected_namespaces=(),
    )

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------
    app_env: AppEnv = Field(default="dev", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    app_version: str = Field(default="0.3.0", alias="APP_VERSION")
    git_sha: str = Field(default="", alias="GIT_SHA")

    # Fake STT/LLM/TTS are first-class in this repo and must stay usable in
    # dev and test. In prod they are a correctness hazard, not a convenience.
    allow_mock_providers: bool = Field(default=True, alias="ALLOW_MOCK_PROVIDERS")

    data_dir: Path = Field(default=Path("data"), alias="DATA_DIR")
    allowed_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="ALLOWED_ORIGINS",
    )
    max_session_seconds: int = Field(default=900, ge=60, alias="MAX_SESSION_SECONDS")

    # ------------------------------------------------------------------
    # Groq — interviewer, indexer, evaluator, roadmap
    # ------------------------------------------------------------------
    groq_api_key: SecretStr = Field(default=SecretStr(""), alias="GROQ_API_KEY")
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL"
    )
    model_live_interviewer: str = Field(
        default="llama-3.1-8b-instant", alias="MODEL_LIVE_INTERVIEWER"
    )
    model_indexer: str = Field(default="llama-3.1-8b-instant", alias="MODEL_INDEXER")
    model_evaluator: str = Field(
        default="llama-3.3-70b-versatile", alias="MODEL_EVALUATOR"
    )
    model_roadmap: str = Field(
        default="llama-3.3-70b-versatile", alias="MODEL_ROADMAP"
    )
    # Second model on the same provider. It covers a withdrawn or overloaded
    # model; it does NOT cover a provider-wide outage, which is why the runtime
    # also has to surface a degraded state to the candidate.
    model_fallback_fast: str = Field(
        default="openai/gpt-oss-20b", alias="MODEL_FALLBACK_FAST"
    )
    model_fallback_quality: str = Field(
        default="openai/gpt-oss-120b", alias="MODEL_FALLBACK_QUALITY"
    )
    groq_requests_per_minute: int = Field(
        default=30, ge=1, alias="GROQ_REQUESTS_PER_MINUTE"
    )
    groq_max_retries: int = Field(default=3, ge=0, le=8, alias="GROQ_MAX_RETRIES")
    groq_timeout_s: float = Field(default=12.0, gt=0, alias="GROQ_TIMEOUT_S")
    max_model_calls_per_turn: int = Field(
        default=3, ge=1, le=10, alias="MAX_MODEL_CALLS_PER_TURN"
    )

    # ------------------------------------------------------------------
    # Deepgram — speech to text
    # ------------------------------------------------------------------
    deepgram_api_key: SecretStr = Field(
        default=SecretStr(""), alias="DEEPGRAM_API_KEY"
    )
    deepgram_stt_url: str = Field(
        default="wss://api.deepgram.com/v1/listen", alias="DEEPGRAM_STT_URL"
    )
    deepgram_stt_model: str = Field(default="nova-3", alias="DEEPGRAM_STT_MODEL")
    deepgram_language: str = Field(default="en-US", alias="DEEPGRAM_LANGUAGE")
    # The browser captures Float32 at the device rate; the server converts to
    # 16-bit PCM at this rate and declares exactly that to Deepgram.
    deepgram_encoding: str = Field(default="linear16", alias="DEEPGRAM_ENCODING")
    deepgram_sample_rate: int = Field(
        default=16000, ge=8000, le=48000, alias="DEEPGRAM_SAMPLE_RATE"
    )
    deepgram_channels: int = Field(default=1, ge=1, le=2, alias="DEEPGRAM_CHANNELS")
    # Documented default is 10 ms, which fires on tiny gaps. 300 ms is the low
    # end of the documented conversational range.
    deepgram_endpointing_ms: int = Field(
        default=300, ge=0, le=5000, alias="DEEPGRAM_ENDPOINTING_MS"
    )
    deepgram_utterance_end_ms: int = Field(
        default=1000, ge=0, le=5000, alias="DEEPGRAM_UTTERANCE_END_MS"
    )
    # UtteranceEnd and SpeechStarted are only delivered when vad_events is on.
    deepgram_vad_events: bool = Field(default=True, alias="DEEPGRAM_VAD_EVENTS")
    deepgram_interim_results: bool = Field(
        default=True, alias="DEEPGRAM_INTERIM_RESULTS"
    )
    deepgram_smart_format: bool = Field(default=True, alias="DEEPGRAM_SMART_FORMAT")
    # Documented budget is 500 tokens across all keyterms. Capping the count as
    # well keeps one candidate's claims from eating the whole budget.
    deepgram_max_keyterms: int = Field(
        default=48, ge=0, le=200, alias="DEEPGRAM_MAX_KEYTERMS"
    )

    # ------------------------------------------------------------------
    # Deepgram — text to speech
    # ------------------------------------------------------------------
    deepgram_tts_url: str = Field(
        default="wss://api.deepgram.com/v1/speak", alias="DEEPGRAM_TTS_URL"
    )
    deepgram_tts_model: str = Field(
        default="aura-2-thalia-en", alias="DEEPGRAM_TTS_MODEL"
    )
    deepgram_tts_encoding: str = Field(
        default="linear16", alias="DEEPGRAM_TTS_ENCODING"
    )
    deepgram_tts_sample_rate: int = Field(
        default=24000, ge=8000, le=48000, alias="DEEPGRAM_TTS_SAMPLE_RATE"
    )
    # Documented limit is 20 Flush messages per 60 s. Flushing per phrase would
    # blow through it inside one answer, so the adapter flushes per utterance
    # and counts against this.
    deepgram_tts_max_flushes_per_minute: int = Field(
        default=18, ge=1, le=20, alias="DEEPGRAM_TTS_MAX_FLUSHES_PER_MINUTE"
    )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in logging.getLevelNamesMapping():
            raise ValueError(f"LOG_LEVEL {value!r} is not a logging level")
        return level

    @field_validator(
        "model_live_interviewer",
        "model_indexer",
        "model_evaluator",
        "model_roadmap",
        "model_fallback_fast",
        "model_fallback_quality",
    )
    @classmethod
    def _model_is_live(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("a model id may not be blank")
        if name in RETIRED_GROQ_MODELS:
            raise ValueError(
                f"{name!r} has been withdrawn by Groq. Current production models: "
                + ", ".join(sorted(GROQ_PRODUCTION_MODELS))
            )
        return name

    @field_validator("deepgram_encoding", "deepgram_tts_encoding")
    @classmethod
    def _encoding_is_pcm(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in DEEPGRAM_PCM_ENCODINGS:
            raise ValueError(
                f"encoding {value!r} is not one this app can produce or decode; "
                f"use one of {sorted(DEEPGRAM_PCM_ENCODINGS)}"
            )
        return name

    @field_validator("deepgram_tts_model")
    @classmethod
    def _tts_voice_shape(cls, value: str) -> str:
        name = value.strip()
        if not name.startswith(("aura-2-", "aura-")):
            raise ValueError(
                f"{value!r} is not a Deepgram Aura voice id "
                "(expected e.g. aura-2-thalia-en)"
            )
        return name

    @model_validator(mode="after")
    def _production_is_honest(self) -> "Settings":
        if self.app_env != "prod":
            return self
        if self.allow_mock_providers:
            raise ValueError(
                "APP_ENV=prod with ALLOW_MOCK_PROVIDERS enabled. Mock STT/LLM/TTS "
                "would let a missing credential produce a 'successful' interview "
                "from silence and canned text. Set ALLOW_MOCK_PROVIDERS=0."
            )
        return self

    # ------------------------------------------------------------------
    # Derived
    # ------------------------------------------------------------------

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.allowed_origins_raw.split(",")
            if origin.strip()
        ]

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key.get_secret_value().strip())

    @property
    def has_deepgram(self) -> bool:
        return bool(self.deepgram_api_key.get_secret_value().strip())

    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"

    def model_for(self, role: str) -> str:
        try:
            return {
                "live_interviewer": self.model_live_interviewer,
                "indexer": self.model_indexer,
                "evaluator": self.model_evaluator,
                "roadmap": self.model_roadmap,
            }[role]
        except KeyError:
            raise KeyError(f"no model configured for role {role!r}") from None

    def fallback_model_for(self, role: str) -> str | None:
        """
        Second choice for a role, or None when it matches the primary.

        Fast roles fall back to the small model and quality roles to the large
        one, so a failover does not quietly change the character of the
        interview or the strictness of the evaluation.
        """
        fallback = (
            self.model_fallback_quality
            if role in ("evaluator", "roadmap")
            else self.model_fallback_fast
        )
        return None if fallback == self.model_for(role) else fallback

    def require_real_providers(self) -> None:
        """
        Raise when a real session is impossible, instead of faking one.

        Called on the live path in production. In dev and test the mocks are
        allowed and this is a no-op.
        """
        if self.allow_mock_providers and not self.is_production:
            return
        missing = [
            name
            for name, present in (
                ("GROQ_API_KEY", self.has_groq),
                ("DEEPGRAM_API_KEY", self.has_deepgram),
            )
            if not present
        ]
        if missing:
            raise ProviderCredentialsMissing(
                "Cannot run a real voice interview: "
                + ", ".join(missing)
                + " is not set. Set it in the environment, or run with "
                "APP_ENV=dev and ALLOW_MOCK_PROVIDERS=1 for a mock session."
            )

    def public_dict(self) -> dict:
        """
        Everything safe to serve from /healthz or send to a browser.

        Secrets are reported as booleans only. Model and voice ids are not
        secret and knowing them is what makes a bug report useful.
        """
        return {
            "app_env": self.app_env,
            "app_version": self.app_version,
            "git_sha": self.git_sha,
            "allow_mock_providers": self.allow_mock_providers,
            "groq_configured": self.has_groq,
            "deepgram_configured": self.has_deepgram,
            "models": {
                "live_interviewer": self.model_live_interviewer,
                "indexer": self.model_indexer,
                "evaluator": self.model_evaluator,
                "roadmap": self.model_roadmap,
                "fallback_fast": self.model_fallback_fast,
                "fallback_quality": self.model_fallback_quality,
            },
            "speech": {
                "stt_model": self.deepgram_stt_model,
                "stt_encoding": self.deepgram_encoding,
                "stt_sample_rate": self.deepgram_sample_rate,
                "endpointing_ms": self.deepgram_endpointing_ms,
                "utterance_end_ms": self.deepgram_utterance_end_ms,
                "tts_model": self.deepgram_tts_model,
                "tts_encoding": self.deepgram_tts_encoding,
                "tts_sample_rate": self.deepgram_tts_sample_rate,
            },
        }


class ProviderCredentialsMissing(RuntimeError):
    """A real session was requested but a provider credential is absent."""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached so .env is read once."""
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    """Tests only: drop the cache after changing the environment."""
    get_settings.cache_clear()
