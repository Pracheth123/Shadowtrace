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

Precedence (stage 16, documented in README and .env.example): a variable set in
the **process environment** wins; otherwise the value in **`.env`**; otherwise
the **default** below. `config/inference.yaml` no longer carries any value that
also exists here — it only holds generation defaults and the TTS vendor, so it
cannot silently override `GROQ_*` or `MAX_MODEL_CALLS_PER_TURN`.

Retry vocabulary, used consistently: `GROQ_MAX_RETRIES` is the number of
*retries* after the first attempt (total attempts per model = retries + 1). The
OpenAI SDK's own retries are switched off so there is exactly one retry layer.

Verified against provider documentation on 2026-10-04 (see
docs/decisions/stage12_voice_providers.md for the fetch log):

  - Groq: model availability is per-account, so it is read from GET /models
    rather than from the docs. This key has no llama models at all; the
    documented `llama-3.1-8b-instant` returns 404. `gemma2-9b-it` is withdrawn
    everywhere and returns 400 model_decommissioned.
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

# Chat models this deployment's key can actually reach, confirmed by calling
# GET /models rather than by reading the docs. The published model list and a
# given account's entitlements are not the same thing: the documented
# `llama-3.1-8b-instant` returns 404 model_not_found for this key, which is why
# the first real interviewer call fell through to the failover.
#
# Re-check with: python tools/check_models.py
GROQ_PRODUCTION_MODELS = frozenset(
    {
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-safeguard-20b",
        "qwen/qwen3.8-27b",
        "allam-2-7b",
    }
)

# `gpt-oss` models emit reasoning tokens before their visible content. A 220
# token budget was consumed entirely by reasoning, so the reply came back as an
# empty string with finish_reason="length" and every proposal was rejected as
# unparseable. Structured proposals need real headroom.
STRUCTURED_REPLY_MAX_TOKENS = 800

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
    # Force the deterministic interviewer and the mock evaluator even when a
    # Groq key is present (tests, offline demos). Unset = follow the key.
    # Requires ALLOW_MOCK_PROVIDERS; refused in production.
    mock_llm: bool | None = Field(default=None, alias="MOCK_LLM")

    data_dir: Path = Field(default=Path("data"), alias="DATA_DIR")
    allowed_origins_raw: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="ALLOWED_ORIGINS",
    )
    # Hard server-side ceiling on one live session's wall time, enforced by a
    # watchdog on a monotonic deadline that survives reconnects. A session's
    # own plan (interview minutes, or a shorter focused practice) plus
    # SESSION_OVERRUN_GRACE_S is used when that is shorter.
    max_session_seconds: int = Field(default=900, ge=60, alias="MAX_SESSION_SECONDS")
    session_overrun_grace_s: float = Field(
        default=120.0, ge=0.0, le=1800.0, alias="SESSION_OVERRUN_GRACE_S"
    )
    # A session with no candidate input and no interviewer speech for this long
    # is ended through the normal close path (0 disables). A warning is sent
    # SESSION_IDLE_WARNING_S before that.
    session_idle_seconds: int = Field(default=300, ge=0, le=7200, alias="SESSION_IDLE_SECONDS")
    session_idle_warning_s: int = Field(default=60, ge=0, le=600, alias="SESSION_IDLE_WARNING_S")
    max_live_sessions: int = Field(default=8, ge=1, le=200, alias="MAX_LIVE_SESSIONS")
    intake_rph: int = Field(default=10, ge=1, le=1000, alias="INTAKE_RPH")
    resume_ttl_s: float = Field(default=180.0, ge=5.0, le=3600.0, alias="RESUME_TTL_S")

    # Admission. Per-candidate limits alone are bypassed by minting another
    # guest, so every billable path is also limited per client address and
    # process-wide. All are in-process (one worker; see docs/deploy).
    session_starts_per_hour: int = Field(default=12, ge=1, le=1000, alias="SESSION_STARTS_PER_HOUR")
    address_session_starts_per_hour: int = Field(
        default=30, ge=1, le=10000, alias="ADDRESS_SESSION_STARTS_PER_HOUR"
    )
    address_intakes_per_hour: int = Field(default=20, ge=1, le=10000, alias="ADDRESS_INTAKES_PER_HOUR")
    global_intakes_per_hour: int = Field(default=200, ge=1, le=100000, alias="GLOBAL_INTAKES_PER_HOUR")
    guests_per_address_per_hour: int = Field(default=20, ge=1, le=10000, alias="GUESTS_PER_ADDRESS_PER_HOUR")
    global_guests_per_hour: int = Field(default=500, ge=1, le=100000, alias="GLOBAL_GUESTS_PER_HOUR")
    practice_per_hour: int = Field(default=20, ge=1, le=1000, alias="PRACTICE_PER_HOUR")
    eval_retries_per_hour: int = Field(default=10, ge=1, le=1000, alias="EVAL_RETRIES_PER_HOUR")
    revisions_per_hour: int = Field(default=10, ge=1, le=1000, alias="REVISIONS_PER_HOUR")
    # Evaluation work: at most this many round jobs call the provider at once,
    # process-wide, and at most EVAL_QUEUE_MAX sessions wait or run.
    eval_concurrency: int = Field(default=3, ge=1, le=16, alias="EVAL_CONCURRENCY")
    eval_queue_max: int = Field(default=20, ge=1, le=1000, alias="EVAL_QUEUE_MAX")
    # Background model calls (evaluation, roadmap) leave this share of the
    # per-minute budget for live turns, and wait for queued live turns first,
    # but never longer than BACKGROUND_MAX_DEFER_S.
    live_reserved_share: float = Field(default=0.2, ge=0.0, le=0.9, alias="LIVE_RESERVED_SHARE")
    background_max_defer_s: float = Field(
        default=20.0, ge=0.0, le=600.0, alias="BACKGROUND_MAX_DEFER_S"
    )
    # Reverse proxies whose X-Forwarded-For is believed (IPs or CIDRs). Empty:
    # the socket peer is the client address and forwarding headers are ignored.
    trusted_proxies_raw: str = Field(default="", alias="TRUSTED_PROXIES")

    # Retention of guest data. A guest inactive for this many days is deleted
    # by the background sweep (0 disables it). Disclosed before any upload.
    guest_retention_days: int = Field(default=30, ge=0, le=3650, alias="GUEST_RETENTION_DAYS")
    retention_sweep_interval_s: float = Field(
        default=3600.0, ge=10.0, alias="RETENTION_SWEEP_INTERVAL_S"
    )

    # ------------------------------------------------------------------
    # Groq — interviewer, indexer, evaluator, roadmap
    # ------------------------------------------------------------------
    groq_api_key: SecretStr = Field(default=SecretStr(""), alias="GROQ_API_KEY")
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL"
    )
    model_live_interviewer: str = Field(
        default="openai/gpt-oss-20b", alias="MODEL_LIVE_INTERVIEWER"
    )
    model_indexer: str = Field(default="openai/gpt-oss-20b", alias="MODEL_INDEXER")
    model_evaluator: str = Field(
        default="openai/gpt-oss-120b", alias="MODEL_EVALUATOR"
    )
    model_roadmap: str = Field(
        default="openai/gpt-oss-120b", alias="MODEL_ROADMAP"
    )
    # Second model on the same provider. It covers a withdrawn or overloaded
    # model; it does NOT cover a provider-wide outage, which is why the runtime
    # also has to surface a degraded state to the candidate.
    # A different model *family* on purpose. Falling back from gpt-oss-20b to
    # gpt-oss-120b would share whatever made the first one fail.
    model_fallback_fast: str = Field(
        default="qwen/qwen3.8-27b", alias="MODEL_FALLBACK_FAST"
    )
    model_fallback_quality: str = Field(
        default="qwen/qwen3.8-27b", alias="MODEL_FALLBACK_QUALITY"
    )
    # Client-side cap shared by every role on one key. Keep it at or below the
    # account's real limit: raising it does not make the provider faster, it
    # only turns client-side waiting into provider 429s.
    groq_requests_per_minute: int = Field(
        default=30, ge=1, alias="GROQ_REQUESTS_PER_MINUTE"
    )
    # Retries AFTER the first attempt, per model, for retryable failures only
    # (429, 408, 5xx, timeout, connection). Total attempts = this + 1.
    groq_max_retries: int = Field(default=3, ge=0, le=8, alias="GROQ_MAX_RETRIES")
    # Per HTTP request timeout for live (interviewer) calls.
    groq_timeout_s: float = Field(default=12.0, gt=0, alias="GROQ_TIMEOUT_S")
    # Per HTTP request timeout for evaluator calls, which return thousands of
    # tokens of JSON and legitimately take longer than a live turn.
    groq_eval_timeout_s: float = Field(default=60.0, gt=0, alias="GROQ_EVAL_TIMEOUT_S")
    # Overall deadline for one live interviewer decision: retries, limiter wait
    # and the fallback hop must all fit inside it. When it is spent the role's
    # deterministic proposer asks the next question and the candidate is told.
    live_model_deadline_s: float = Field(
        default=10.0, gt=0, le=60.0, alias="LIVE_MODEL_DEADLINE_S"
    )

    # ------------------------------------------------------------------
    # Evaluation job budget
    # ------------------------------------------------------------------
    # Repair attempts after an invalid or truncated evaluator reply (model
    # replies per round = repairs + 1). Provider errors are NOT retried here —
    # the client already retried them — so the layers do not multiply.
    eval_max_repairs: int = Field(default=1, ge=0, le=3, alias="EVAL_MAX_REPAIRS")
    # Deadline for one round's evaluation, every request and repair included.
    eval_round_deadline_s: float = Field(
        default=150.0, ge=1.0, le=900.0, alias="EVAL_ROUND_DEADLINE_S"
    )
    # Deadline for the whole job (rounds run concurrently).
    eval_job_deadline_s: float = Field(
        default=240.0, ge=1.0, le=1800.0, alias="EVAL_JOB_DEADLINE_S"
    )
    # How many times one round may be run in total, explicit retries included.
    eval_max_round_runs: int = Field(default=4, ge=1, le=20, alias="EVAL_MAX_ROUND_RUNS")
    eval_max_tokens: int = Field(default=4000, ge=500, le=16000, alias="EVAL_MAX_TOKENS")
    max_model_calls_per_turn: int = Field(
        default=3, ge=1, le=10, alias="MAX_MODEL_CALLS_PER_TURN"
    )
    # Headroom for a structured proposal, including reasoning tokens.
    structured_reply_max_tokens: int = Field(
        default=STRUCTURED_REPLY_MAX_TOKENS, ge=200, le=4000,
        alias="STRUCTURED_REPLY_MAX_TOKENS",
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
    # When an answer is over. Deepgram's endpointing and UtteranceEnd fire on a
    # breath-length pause, which cut candidates off mid-thought, so they are
    # only hints now: an answer ends after this much silence following the last
    # recognised word ...
    answer_end_silence_ms: int = Field(
        default=3000, ge=500, le=15000, alias="ANSWER_END_SILENCE_MS"
    )
    # ... or this much when the answer sounds unfinished (a trailing "and",
    # "so", "um", a comma) or is only a few words long.
    answer_end_extended_ms: int = Field(
        default=5000, ge=500, le=20000, alias="ANSWER_END_EXTENDED_MS"
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

    @model_validator(mode="before")
    @classmethod
    def _clean_env_values(cls, data):
        """
        Normalise raw environment input before field parsing.

        Two things real `.env` files do that strict parsing rejects:

        - **Trailing comments.** `APP_ENV=dev   # dev | test | prod` is how
          people actually annotate a copied template, and dotenv keeps the
          comment as part of the value. An un-quoted `#` starts a comment.
        - **Empty values.** `ALLOW_MOCK_PROVIDERS=` means "I did not set this",
          but an empty string is not a boolean and raised a validation error at
          startup. Empty entries are dropped so the field default applies.

        Quoted values are left exactly as given, so a password containing `#`
        survives as long as it is quoted.
        """
        if not isinstance(data, dict):
            return data
        cleaned = {}
        for key, value in data.items():
            if not isinstance(value, str):
                cleaned[key] = value
                continue
            text = value.strip()
            if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
                cleaned[key] = text[1:-1]
                continue
            if "#" in text:
                text = text.split("#", 1)[0].strip()
            if text == "":
                continue  # unset: let the default stand
            cleaned[key] = text
        return cleaned

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
                f"{name!r} has been withdrawn by Groq. Models reachable with "
                "this deployment's key: " + ", ".join(sorted(GROQ_PRODUCTION_MODELS))
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
        if self.mock_llm and not self.allow_mock_providers:
            raise ValueError(
                "MOCK_LLM=1 asks for the deterministic interviewer and mock "
                "evaluator, but ALLOW_MOCK_PROVIDERS is off. Turn one of them off."
            )
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
    def trusted_proxies(self) -> list[str]:
        return [p.strip() for p in self.trusted_proxies_raw.split(",") if p.strip()]

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key.get_secret_value().strip())

    @property
    def has_deepgram(self) -> bool:
        return bool(self.deepgram_api_key.get_secret_value().strip())

    @property
    def is_production(self) -> bool:
        return self.app_env == "prod"

    @property
    def mocks_allowed(self) -> bool:
        return self.allow_mock_providers and not self.is_production

    @property
    def interviewer_mode(self) -> str:
        """
        `groq` — the model proposes, the guard rules.
        `deterministic` — the role-aware, plan-based interviewer with no AI
          model. Only where mocks are allowed, and always labelled on screen.
        `unavailable` — no key and mocks are off: sessions are refused.
        """
        if self.mock_llm:
            return "deterministic"
        if self.has_groq:
            return "groq"
        return "deterministic" if self.mocks_allowed else "unavailable"

    @property
    def evaluator_mode(self) -> str:
        """`groq`, `mock` (labelled, dev/test only) or `unavailable`."""
        if not self.mock_llm and self.has_groq:
            return "groq"
        return "mock" if self.mocks_allowed else "unavailable"

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
            "mock_llm": self.mock_llm,
            "interviewer_mode": self.interviewer_mode,
            "evaluator_mode": self.evaluator_mode,
            # Configured means "a value is present". It does NOT mean the key
            # works — /api/diagnostics/verify makes an authenticated request.
            "groq_configured": self.has_groq,
            "deepgram_configured": self.has_deepgram,
            "groq_budget": {
                "requests_per_minute": self.groq_requests_per_minute,
                "max_retries": self.groq_max_retries,
                "retries_meaning": "retries after the first attempt; attempts = retries + 1",
                "request_timeout_s": self.groq_timeout_s,
                "eval_request_timeout_s": self.groq_eval_timeout_s,
                "live_model_deadline_s": self.live_model_deadline_s,
                "max_model_calls_per_turn": self.max_model_calls_per_turn,
                "sdk_retries": 0,
            },
            "evaluation_budget": {
                "max_repairs": self.eval_max_repairs,
                "round_deadline_s": self.eval_round_deadline_s,
                "job_deadline_s": self.eval_job_deadline_s,
                "max_round_runs": self.eval_max_round_runs,
                "max_tokens": self.eval_max_tokens,
            },
            "session_budget": {
                "max_session_seconds": self.max_session_seconds,
                "overrun_grace_s": self.session_overrun_grace_s,
                "idle_seconds": self.session_idle_seconds,
                "idle_warning_s": self.session_idle_warning_s,
                "max_live_sessions": self.max_live_sessions,
            },
            "admission": {
                "session_starts_per_hour": self.session_starts_per_hour,
                "address_session_starts_per_hour": self.address_session_starts_per_hour,
                "intake_per_candidate_per_hour": self.intake_rph,
                "address_intakes_per_hour": self.address_intakes_per_hour,
                "global_intakes_per_hour": self.global_intakes_per_hour,
                "guests_per_address_per_hour": self.guests_per_address_per_hour,
                "global_guests_per_hour": self.global_guests_per_hour,
                "practice_per_hour": self.practice_per_hour,
                "eval_retries_per_hour": self.eval_retries_per_hour,
                "revisions_per_hour": self.revisions_per_hour,
                "eval_concurrency": self.eval_concurrency,
                "eval_queue_max": self.eval_queue_max,
                "trusted_proxies": len(self.trusted_proxies),
            },
            "scheduling": {
                "live_reserved_share": self.live_reserved_share,
                "background_max_defer_s": self.background_max_defer_s,
            },
            "retention": {
                "guest_retention_days": self.guest_retention_days,
                "sweep_enabled": self.guest_retention_days > 0,
            },
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
