"""
Composition root — FastAPI HTTP API + WebSocket session (stages 4–15).

Creates the bus, logger, live runtime and speak port. Transport and inference
meet only through the bus (contract 1). This module is allowed to import every
band; no band imports it.

Run:
    uvicorn interview.server:app --host 0.0.0.0 --port 8000

Candidate journey (stage 15):

    POST /api/guest                       → bearer token for a guest candidate
    POST /api/intake        (multipart)   → intake_id; runs in the background
    GET  /api/intake/{id}                 → progress, then claims / fit / prep
    WS   /ws/session  session_start{auth_token, intake_id}
                                          → rounds-mode interview from that intake
    (session closes)                      → transcript + log persisted, then the
                                            evaluation job is queued
    GET  /api/sessions/{id}               → lifecycle state (poll)
    GET  /api/sessions/{id}/report        → the evaluated report
    GET  /api/sessions/{id}/scorecard     → downloadable HTML scorecard
    GET  /api/sessions/{id}/transcript    → downloadable transcript
    POST /api/sessions/{id}/evaluation/retry
    GET  /api/history  ·  GET /api/plan   → history, comparisons, next practice
    POST /api/me/delete                   → erase everything for this candidate

Stage 16 additions (all authenticated, all built from the candidate's own id):

    PUT  /api/intake/{id}/review          → keep / edit / exclude statements, focus
    GET  /api/sessions/{id}/evaluation    → per-round job state + partial results
    POST /api/sessions/{id}/evaluation/retry   (failed rounds only)
    POST /api/sessions/{id}/findings/{fid}/dispute
    POST /api/sessions/{id}/findings/{fid}/dispute/status
    POST /api/sessions/{id}/findings/{fid}/revision
    GET|POST /api/practice ·  GET /api/practice/{pid}
    POST /api/practice/{pid}/coaching
    WS   session_start{auth_token, practice_id}  → a targeted practice attempt
    GET  /api/diagnostics  ·  POST /api/diagnostics/verify   (no secrets)

Every /api route resolves the candidate from the bearer token and builds paths
from that id (see candidates.py), so changing an id in a URL reaches nothing.

Env:
    DATA_DIR            per-candidate data + report store, default data/
    SESSION_LOG_DIR     logs for dev sessions started without intake, default logs/
    GROQ_API_KEY        interviewer + evaluator model calls
    DEEPGRAM_API_KEY    real voice (STT + TTS)
    MOCK_LLM=1          force the deterministic interviewer + mock evaluator (dev/test)
    APP_ENV=prod        refuses mocks and sessions without an intake
    PANEL_MODE=1        allow legacy multi-voice panel packs (off by default)
    MAX_LIVE_SESSIONS   concurrent live session cap, default 8
    INTAKE_RPH          intake requests per candidate per hour, default 10
    MAX_SESSION_SECONDS hard per-session ceiling (watchdog), default 900
    SESSION_IDLE_SECONDS  end a session after this long without input, default 300
    TRUSTED_PROXIES     proxies whose X-Forwarded-For is believed (see .env.example)

Video: there is deliberately no video ingest path. The client may show a local
camera preview, but no frame is ever sent here (contract 9).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from interview.candidates import (
    CandidateRegistry,
    NotFound,
    UnknownCandidate,
    new_id,
    read_json,
    write_json,
)
from interview.config import ProviderCredentialsMissing, get_settings
from interview.events.bus import EventBus
from interview.events.log import EventLogger
from interview.events.schema import BargeIn, PlaybackAck, Truncate
from interview.hardening.erasure import UnsafeIdentifier, delete_candidate_data
from interview.hardening.limits import (
    GLOBAL_KEY,
    Admission,
    AdmissionRefused,
    RateLimit,
    RateLimiter,
    SessionCap,
    SessionCapExceeded,
    client_address,
)
from interview.hardening.reconnect import ReconnectRegistry
from interview.intake.documents import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_UPLOAD_LABEL,
    SUPPORTED_UPLOAD_SUFFIXES,
    UploadRejected,
    extract_upload,
)
from interview.llm.env import load_dotenv
from interview.mocks.fake_stt import FakeStt
from interview.services import diagnostics as diag
from interview.services.evaluation import build_services, set_state, EvaluationService
from interview.services.feedback import (
    FeedbackError,
    FeedbackService,
    create_dispute,
    overlay,
    set_dispute_status,
)
from interview.services.history import (
    comparisons,
    practice_plan,
    recurring_gaps,
    session_rows,
)
from interview.services.intake import (
    IntakeInputs,
    ReviewRejected,
    accepted_claims,
    load_intake,
    objective_for,
    read_consent,
    run_intake_job,
    save_review,
    write_consent,
    write_status,
)
from interview.services.practice import (
    PRACTICE_MINUTES,
    PracticeError,
    create_practice,
    list_practices,
    load_practice,
    mark_coaching_shown,
    note_attempt,
    practice_pack,
    practice_view,
)
from interview.services.retention import sweep as retention_sweep
from interview.session.coordinator import Coordinator, PackUnavailable
from interview.session.interview_config import InterviewConfig
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort, TextLaneSpeakPort
from interview.session.tools import Claim
from interview.transport.voice_session import VoiceSession, VoiceUnavailable

STAGE = 16


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Pay the openai SDK import (seconds on a cold start) at boot, not inside
    # the first interview turn's model deadline.
    import openai  # noqa: F401

    await _start_retention()
    try:
        yield
    finally:
        await _stop_retention()


load_dotenv()
_SETTINGS = get_settings()

app = FastAPI(title=f"Shadowtrace — Stage {STAGE}", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_SETTINGS.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOG_DIR = Path(os.environ.get("SESSION_LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

REPORT_DIR = Path(os.environ.get("REPORT_DIR", "reports"))
INTAKE_DIR = Path(os.environ.get("INTAKE_DIR", "intake"))
STORE_PATH = Path(
    os.environ.get("STORE_PATH", "fixtures/roadmap/longitudinal.sqlite")
)

PANEL_VOICES = {
    "lead": "aura-2-thalia-en",
    "recruiter": "aura-2-vesta-en",
    "peer": "aura-2-arcas-en",
    "hiring_manager": "aura-2-apollo-en",
    "manager": "aura-2-apollo-en",
    "specialist": "aura-2-orpheus-en",
    "bar_raiser": "aura-2-orpheus-en",
}

# Limits come from the validated settings (env > .env > defaults).
SESSION_CAP = SessionCap(limit=_SETTINGS.max_live_sessions)


def _hourly(n: int) -> RateLimiter:
    return RateLimiter(RateLimit(max_events=n, window_s=3600.0))


# Admission. Each billable path is limited per candidate, per client address
# and (where a burst of new guests could otherwise bypass both) process-wide.
INTAKE_LIMITER = _hourly(_SETTINGS.intake_rph)
INTAKE_ADDRESS_LIMITER = _hourly(_SETTINGS.address_intakes_per_hour)
INTAKE_GLOBAL_LIMITER = _hourly(_SETTINGS.global_intakes_per_hour)
GUEST_LIMITER = _hourly(_SETTINGS.guests_per_address_per_hour)
GUEST_GLOBAL_LIMITER = _hourly(_SETTINGS.global_guests_per_hour)
START_LIMITER = _hourly(_SETTINGS.session_starts_per_hour)
START_ADDRESS_LIMITER = _hourly(_SETTINGS.address_session_starts_per_hour)
PRACTICE_LIMITER = _hourly(_SETTINGS.practice_per_hour)
RETRY_LIMITER = _hourly(_SETTINGS.eval_retries_per_hour)
REVISION_LIMITER = _hourly(_SETTINGS.revisions_per_hour)
RECONNECT = ReconnectRegistry(ttl_s=_SETTINGS.resume_ttl_s)
_PARK_TTL_S = _SETTINGS.resume_ttl_s
_PARKED: dict[str, asyncio.Task] = {}
_INTAKE_TASKS: dict[str, asyncio.Task] = {}
_INTAKE_OWNERS: dict[str, str] = {}
# Live sessions by id, so deletion can close a candidate's open interview first.
_LIVE: dict[str, "SessionContext"] = {}
DIAGNOSTICS_LIMITER = RateLimiter(RateLimit(max_events=6, window_s=3600.0))


def _use_mock_llm() -> bool:
    """
    True when the interviewer and evaluator must not call a model.

    One rule, from settings: MOCK_LLM forces it (only where mocks are allowed);
    otherwise it follows whether GROQ_API_KEY is configured.
    """
    return get_settings().interviewer_mode != "groq"


# Data services. Rebound by `configure_data_dir` (tests point it at tmp_path).
# Built in services/ so this module never imports evaluation directly: the
# composition root only queues a job after the live session has closed.
REGISTRY: CandidateRegistry
# The report store is constructed by build_services; the live composition
# root does not need to import the downstream roadmap implementation.
EVALUATION: EvaluationService


def configure_data_dir(root: Path) -> None:
    global REGISTRY, REPORT_STORE, EVALUATION
    REGISTRY, REPORT_STORE, EVALUATION = build_services(
        Path(root), use_mock_llm=_use_mock_llm
    )


configure_data_dir(Path(_SETTINGS.data_dir))


def _feedback() -> FeedbackService:
    # Built on demand so tests that swap EVALUATION get the matching service.
    return FeedbackService(EVALUATION)


def _panel_allowed() -> bool:
    return os.environ.get("PANEL_MODE", "").strip().lower() in ("1", "true", "yes")


def _error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _candidate(authorization: str | None) -> str:
    return REGISTRY.resolve(authorization)


def _address(conn: Request | WebSocket) -> str:
    """Client address for limits; forwarding headers only from TRUSTED_PROXIES."""
    peer = conn.client.host if conn.client else None
    return client_address(peer, conn.headers, get_settings().trusted_proxies)


OPERATOR_STOP_FILE = "OPERATOR_STOP"
DELETION_LEDGER = "deletion-ledger.jsonl"


def _data_root() -> Path:
    """DATA_DIR as the running services see it (the registry lives in DATA_DIR/candidates)."""
    return REGISTRY.root.parent


def _operator_stopped() -> bool:
    """
    Operator stop: `touch $DATA_DIR/OPERATOR_STOP` refuses new guests, intakes,
    sessions, practice set-ups, retries and re-checks with 503, while live
    sessions finish normally. Remove the file to resume. No restart needed.
    """
    return (_data_root() / OPERATOR_STOP_FILE).exists()


def _paused() -> JSONResponse:
    return JSONResponse(
        {
            "error": "New sessions are paused by the operator.",
            "recovery": "Nothing was started or charged. Please try again later.",
        },
        status_code=503,
        headers={"Retry-After": "300"},
    )


def _record_deletion(candidate_id: str) -> None:
    """
    Append the erased candidate id to a ledger beside the data. Backups taken
    before the erasure still contain the data; tools/ops/restore.py replays
    this ledger after any restore so deleted data stays deleted.
    The id is a random identifier, not personal data.
    """
    ledger = _data_root() / DELETION_LEDGER
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"candidate_id": candidate_id, "deleted_at": _now()}) + "\n")


def _wait_words(seconds: int) -> str:
    return f"{seconds} seconds" if seconds < 120 else f"{round(seconds / 60)} minutes"


def _refused(exc: AdmissionRefused) -> JSONResponse:
    return JSONResponse(
        {
            "error": str(exc),
            "retry_after_s": exc.retry_after_s,
            "recovery": (
                "Nothing was started or charged. "
                f"Try again in about {_wait_words(exc.retry_after_s)}."
            ),
        },
        status_code=429,
        headers={"Retry-After": str(exc.retry_after_s)},
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    settings = get_settings()
    return {
        "status": "ok",
        "stage": STAGE,
        "panel_mode": _panel_allowed(),
        "live_sessions": SESSION_CAP.live,
        "session_limit": SESSION_CAP.limit,
        "providers": {
            # Configured modes. Whether a key actually works is only known after
            # POST /api/diagnostics/verify — see `verified`.
            "interviewer": settings.interviewer_mode,
            "voice": "deepgram" if settings.has_deepgram else "unavailable",
            "evaluator": settings.evaluator_mode,
            "verified": {
                "groq": (diag.last_verification() or {}).get("groq", {}).get("verified"),
                "deepgram": (diag.last_verification() or {}).get("deepgram", {}).get("verified"),
            },
        },
        "uploads": {
            "suffixes": list(SUPPORTED_UPLOAD_SUFFIXES),
            "label": SUPPORTED_UPLOAD_LABEL,
            "max_bytes": MAX_UPLOAD_BYTES,
        },
        "retention_days": settings.guest_retention_days,
        "practice_minutes": list(PRACTICE_MINUTES),
        "session_limits": {
            "max_session_seconds": settings.max_session_seconds,
            "idle_seconds": settings.session_idle_seconds,
        },
    }


@app.get("/ready")
async def ready():
    """
    Readiness for a load balancer or deploy script: the data directory is
    writable, the report store answers, and (in production) the configured
    providers are present. 503 with the failing checks otherwise. Configured
    is not verified: provider authentication is POST /api/diagnostics/verify.
    """
    import sqlite3

    settings = get_settings()
    checks: dict[str, bool] = {}
    data_dir = _data_root()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".ready-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["data_dir_writable"] = True
    except OSError:
        checks["data_dir_writable"] = False
    try:
        with sqlite3.connect(REPORT_STORE.path, timeout=2) as conn:
            conn.execute("select 1").fetchone()
        checks["report_store"] = True
    except Exception:  # noqa: BLE001
        checks["report_store"] = False
    checks["interviewer_available"] = settings.interviewer_mode != "unavailable"
    checks["evaluator_available"] = settings.evaluator_mode != "unavailable"
    if settings.is_production:
        checks["no_mock_providers"] = settings.interviewer_mode == "groq" and settings.evaluator_mode == "groq"
    ok = all(checks.values())
    body = {
        "ready": ok,
        "checks": checks,
        "operator_stop": _operator_stopped(),
        "app_env": settings.app_env,
        "version": settings.app_version,
        "git_sha": settings.git_sha,
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/api/diagnostics")
async def diagnostics():
    """Effective configuration and readiness. Never contains a secret."""
    from interview.llm.client import get_shared_limiter

    settings = get_settings()
    return {
        **diag.readiness(settings),
        # Live process state: counts only, no candidate or request content.
        "runtime": {
            "live_sessions": SESSION_CAP.live,
            "session_limit": SESSION_CAP.limit,
            "evaluation_jobs_active": EVALUATION.active_jobs(),
            "evaluation_queue_max": settings.eval_queue_max,
            "evaluation_concurrency": EVALUATION.concurrency or settings.eval_concurrency,
            "model_limiter": get_shared_limiter(settings.groq_requests_per_minute).snapshot(),
        },
    }


@app.post("/api/diagnostics/verify")
async def diagnostics_verify(request: Request):
    """
    Authenticated, non-generating provider checks (Groq GET /models, Deepgram
    GET /v1/projects). Cached for 10 minutes and rate-limited per address.
    """
    client = _address(request)
    cached = diag.last_verification()
    if cached is not None and not DIAGNOSTICS_LIMITER.allow(client):
        return cached
    return await diag.verify(get_settings())


@app.get("/")
async def root():
    return {"service": "shadowtrace", "stage": STAGE, "ws": "/ws/session", "api": "/api"}


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@app.post("/api/guest")
async def create_guest(request: Request):
    """
    Issue a guest identity. The token is returned once and only its hash is
    stored. Limits: one browser, no recovery; see candidates.py.
    """
    if _operator_stopped():
        return _paused()
    try:
        Admission([
            (GUEST_LIMITER, _address(request), "Too many new guest identities from this address."),
            (GUEST_GLOBAL_LIMITER, GLOBAL_KEY, "The service is not accepting new guests right now."),
        ]).admit()
    except AdmissionRefused as exc:
        return _refused(exc)
    guest = REGISTRY.create_guest()
    return {
        "candidate_id": guest.candidate_id,
        "token": guest.token,
        "kind": "guest",
        "limits": (
            "Guest access lives in this browser only. Clearing site data loses "
            "access to your history; there is no recovery."
        ),
    }


@app.get("/api/me")
async def me(authorization: str | None = Header(default=None)):
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    return {"candidate_id": candidate_id, "kind": "guest"}


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


async def _read_upload(upload: UploadFile | None):
    if upload is None or not (upload.filename or "").strip():
        return None
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    return extract_upload(upload.filename or "", data)


def round_up(value: float) -> int:
    return int(value) + 1


@app.post("/api/intake", status_code=202)
async def create_intake(
    request: Request,
    authorization: str | None = Header(default=None),
    target_role: str = Form(""),
    role_family: str = Form("generic"),
    seniority: str = Form("mid"),
    round: str = Form("full"),
    lane: str = Form("voice"),
    intensity: str = Form("realistic"),
    background_text: str = Form(""),
    job_description: str = Form(""),
    company_context: str = Form(""),
    repo_url: str = Form(""),
    total_minutes: float = Form(30.0),
    consent: str = Form(""),
    resume: UploadFile | None = File(default=None),
    work_sample: UploadFile | None = File(default=None),
):
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    # Nothing is read, stored or sent anywhere before an affirmative consent.
    if consent.strip().lower() not in ("1", "true", "yes", "on"):
        return _error(
            422,
            "Please confirm you understand how your documents and speech are processed.",
            field="consent",
            recovery="Tick the consent box on the setup form, then submit again.",
        )
    if _operator_stopped():
        return _paused()
    try:
        admission = Admission([
            (INTAKE_LIMITER, candidate_id, "Intake limit reached for this hour."),
            (INTAKE_ADDRESS_LIMITER, _address(request),
             "Too many interview preparations from this address this hour."),
            (INTAKE_GLOBAL_LIMITER, GLOBAL_KEY,
             "The service is preparing too many interviews right now."),
        ]).admit()
    except AdmissionRefused as exc:
        return _refused(exc)
    # Every refusal below happens before the background job (the billable
    # part) starts, so it hands the admission back.
    response = await _create_intake_admitted(
        candidate_id, target_role=target_role, role_family=role_family, seniority=seniority,
        round=round, lane=lane, intensity=intensity, background_text=background_text,
        job_description=job_description, company_context=company_context, repo_url=repo_url,
        total_minutes=total_minutes, resume=resume, work_sample=work_sample,
    )
    if isinstance(response, JSONResponse):
        admission.refund()
    return response


async def _create_intake_admitted(
    candidate_id: str,
    *,
    target_role: str,
    role_family: str,
    seniority: str,
    round: str,
    lane: str,
    intensity: str,
    background_text: str,
    job_description: str,
    company_context: str,
    repo_url: str,
    total_minutes: float,
    resume: UploadFile | None,
    work_sample: UploadFile | None,
):
    try:
        resume_doc = await _read_upload(resume)
        sample_doc = await _read_upload(work_sample)
    except UploadRejected as exc:
        return _error(422, str(exc), recovery=exc.recovery, field="upload")
    try:
        config = InterviewConfig(
            target_role=target_role.strip(),
            role_family=role_family,
            seniority=seniority,
            round=round,
            lane=lane,
            intensity=intensity,
            background_text=background_text,
            resume_filename=resume_doc.filename if resume_doc else "",
            job_description=job_description,
            company_context=company_context,
            repo_url=repo_url.strip(),
            work_sample_filenames=[sample_doc.filename] if sample_doc else [],
            total_seconds=max(5.0, min(60.0, total_minutes)) * 60.0,
        )
    except ValueError as exc:
        return _error(422, _first_error(exc), field="config")
    try:
        Coordinator(config)
    except PackUnavailable as exc:
        return _error(422, str(exc), field="role_family")

    intake_id = new_id()
    directory = REGISTRY.intake_dir(candidate_id, intake_id)
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "config.json", config.model_dump(mode="json"))
    write_consent(directory, voice=config.lane == "voice")
    write_status(directory, "queued", intake_id=intake_id, warnings=[])
    _INTAKE_TASKS[intake_id] = asyncio.create_task(
        run_intake_job(
            directory, IntakeInputs(config=config, resume=resume_doc, work_sample=sample_doc)
        )
    )
    _INTAKE_OWNERS[intake_id] = candidate_id
    return {"intake_id": intake_id, "status": read_json(directory / "status.json", {})}



def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()) if p != "__root__")
            message = str(first.get("msg", exc)).removeprefix("Value error, ")
            return f"{where}: {message}" if where else message
        except Exception:  # noqa: BLE001
            pass
    return str(exc)


@app.get("/api/intake/{intake_id}")
async def get_intake(intake_id: str, authorization: str | None = Header(default=None)):
    try:
        candidate_id = _candidate(authorization)
        directory = REGISTRY.existing_intake(candidate_id, intake_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such intake.")
    return load_intake(directory)


@app.put("/api/intake/{intake_id}/review")
async def review_intake(
    intake_id: str, request: Request, authorization: str | None = Header(default=None)
):
    """
    Keep, edit or exclude each extracted statement, and choose a practice focus.
    The original statements and their source spans are kept unchanged; edits
    are stored as the candidate's corrections.
    """
    try:
        candidate_id = _candidate(authorization)
        directory = REGISTRY.existing_intake(candidate_id, intake_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such intake.")
    status = read_json(directory / "status.json", {}) or {}
    if status.get("state") != "ready":
        return _error(409, "Preparation is not finished yet.")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error(422, "Send a JSON body.")
    try:
        save_review(directory, payload if isinstance(payload, dict) else {})
    except ReviewRejected as exc:
        return _error(422, str(exc))
    return load_intake(directory)


# ---------------------------------------------------------------------------
# Sessions, reports, history
# ---------------------------------------------------------------------------


def _session_dir(authorization: str | None, session_id: str):
    candidate_id = _candidate(authorization)
    return candidate_id, REGISTRY.existing_session(candidate_id, session_id)


@app.get("/api/sessions")
async def list_sessions(authorization: str | None = Header(default=None)):
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    return {"sessions": session_rows(REGISTRY, REPORT_STORE, candidate_id)}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, authorization: str | None = Header(default=None)):
    try:
        candidate_id, _ = _session_dir(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    meta = EVALUATION.status(candidate_id, session_id)
    return {
        key: meta.get(key)
        for key in (
            "session_id", "state", "state_history", "created_at", "lane", "intensity",
            "config", "intake_id", "personalised", "ended_reason", "rounds", "error",
            "recovery", "evaluation_attempts", "overall_score", "evaluator",
            "kind", "practice", "interviewer", "degraded_turns", "objective",
        )
    }


@app.get("/api/sessions/{session_id}/evaluation")
async def get_evaluation(session_id: str, authorization: str | None = Header(default=None)):
    """
    Round-by-round evaluation state with every completed round's result, so
    feedback can be read while other rounds are still running. Read-only:
    polling this never starts or repeats provider work.
    """
    try:
        candidate_id, _ = _session_dir(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    return EVALUATION.job_view(candidate_id, session_id)


@app.get("/api/sessions/{session_id}/report")
async def get_report(session_id: str, authorization: str | None = Header(default=None)):
    try:
        candidate_id, directory = _session_dir(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    meta = EVALUATION.status(candidate_id, session_id)
    if meta.get("state") != "complete":
        return _error(409, "The report is not ready.", state=meta.get("state"))
    report = read_json(directory / "evaluation" / "report.json", None)
    if report is None:
        return _error(404, "The report file is missing.")
    # The stored report is returned unchanged, with disputes and revised
    # assessments alongside it (never merged into it).
    return overlay(report, directory)


@app.get("/api/sessions/{session_id}/scorecard")
async def get_scorecard(session_id: str, authorization: str | None = Header(default=None)):
    try:
        _, directory = _session_dir(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    path = directory / "evaluation" / "scorecard.html"
    if not path.is_file():
        return _error(409, "The scorecard is not ready.")
    return HTMLResponse(
        path.read_text(encoding="utf-8"),
        headers={"Content-Disposition": f'attachment; filename="scorecard-{session_id}.html"'},
    )


@app.get("/api/sessions/{session_id}/transcript")
async def get_transcript(session_id: str, authorization: str | None = Header(default=None)):
    try:
        _, directory = _session_dir(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    entries = read_json(directory / "transcript.json", None)
    if entries is None:
        return _error(409, "The transcript is not saved yet.")
    lines = [f"Practice interview transcript — session {session_id}", ""]
    for entry in entries:
        who = (
            f"Interviewer ({entry.get('speaker_label')})"
            if entry.get("speaker") == "agent" and entry.get("speaker_label")
            else "Interviewer"
            if entry.get("speaker") == "agent"
            else "You"
        )
        suffix = " [interrupted — this is what was heard]" if entry.get("truncated") else ""
        lines.append(f"[{entry.get('turn_id', '')}] {who}: {entry.get('text', '')}{suffix}")
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        headers={"Content-Disposition": f'attachment; filename="transcript-{session_id}.txt"'},
    )


@app.post("/api/sessions/{session_id}/evaluation/retry")
async def retry_evaluation(
    session_id: str, request: Request, authorization: str | None = Header(default=None)
):
    """Re-run failed rounds only. Completed rounds are kept and not re-billed."""
    try:
        candidate_id, _ = _session_dir(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    rounds = None
    try:
        body = await request.json()
        if isinstance(body, dict) and isinstance(body.get("rounds"), list):
            rounds = [str(r) for r in body["rounds"]]
    except Exception:  # noqa: BLE001 — an empty body means "all failed rounds"
        pass
    if _operator_stopped():
        return _paused()
    if EVALUATION.at_capacity():
        return _refused(AdmissionRefused("The server is busy evaluating other interviews.", 60.0))
    try:
        admission = Admission([
            (RETRY_LIMITER, candidate_id, "Too many feedback retries this hour."),
            (RETRY_LIMITER, "addr:" + _address(request),
             "Too many feedback retries from this address this hour."),
        ]).admit()
    except AdmissionRefused as exc:
        return _refused(exc)
    before = EVALUATION.status(candidate_id, session_id).get("state")
    meta = EVALUATION.retry(candidate_id, session_id, rounds)
    if meta.get("retry_refused"):
        admission.refund()
        return _error(409, meta["retry_refused"], state=meta.get("state"))
    if before != "failed":
        admission.refund()  # nothing was started
    return {"state": meta.get("state")}


def _report_for(authorization: str | None, session_id: str):
    candidate_id, directory = _session_dir(authorization, session_id)
    meta = read_json(directory / "meta.json", {}) or {}
    report = read_json(directory / "evaluation" / "report.json", None)
    if meta.get("state") != "complete" or report is None:
        raise FeedbackError("The report is not ready.", status=409)
    return candidate_id, directory, report


@app.post("/api/sessions/{session_id}/findings/{finding_id}/dispute")
async def dispute_finding(
    session_id: str,
    finding_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """'This feedback seems wrong'. Stored beside the report, never in it."""
    try:
        _, directory, report = _report_for(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    except FeedbackError as exc:
        return _error(exc.status, str(exc))
    explanation = ""
    try:
        body = await request.json()
        if isinstance(body, dict):
            explanation = str(body.get("explanation") or "")
    except Exception:  # noqa: BLE001 — the explanation is optional
        pass
    try:
        record = create_dispute(directory, report, finding_id, explanation)
    except FeedbackError as exc:
        return _error(exc.status, str(exc))
    return {"dispute": record, "report": overlay(report, directory)}


@app.post("/api/sessions/{session_id}/findings/{finding_id}/dispute/status")
async def dispute_status(
    session_id: str,
    finding_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    try:
        _, directory, report = _report_for(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    except FeedbackError as exc:
        return _error(exc.status, str(exc))
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    try:
        record = set_dispute_status(directory, finding_id, str((body or {}).get("status") or ""))
    except FeedbackError as exc:
        return _error(exc.status, str(exc))
    return {"dispute": record, "report": overlay(report, directory)}


@app.post("/api/sessions/{session_id}/findings/{finding_id}/revision")
async def request_revision(
    session_id: str,
    finding_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """A labelled automated re-check of the round, with the candidate's correction."""
    try:
        candidate_id, directory, _ = _report_for(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    except FeedbackError as exc:
        return _error(exc.status, str(exc))
    if get_settings().evaluator_mode == "unavailable":
        return _error(503, "No evaluation model is configured, so a re-check cannot run.")
    if _operator_stopped():
        return _paused()
    try:
        admission = Admission([
            (REVISION_LIMITER, candidate_id, "Too many re-checks this hour."),
            (REVISION_LIMITER, "addr:" + _address(request),
             "Too many re-checks from this address this hour."),
        ]).admit()
    except AdmissionRefused as exc:
        return _refused(exc)
    try:
        revision = _feedback().request_revision(candidate_id, session_id, finding_id)
    except FeedbackError as exc:
        admission.refund()
        return _error(exc.status, str(exc))
    return {"revision": revision}


# ---------------------------------------------------------------------------
# Targeted practice
# ---------------------------------------------------------------------------


@app.get("/api/practice")
async def practices(authorization: str | None = Header(default=None)):
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    return {
        "practice": [
            practice_view(REGISTRY, candidate_id, record)
            for record in list_practices(REGISTRY, candidate_id)
        ]
    }


@app.post("/api/practice", status_code=201)
async def start_practice(request: Request, authorization: str | None = Header(default=None)):
    """
    "Practise this gap". The body names a session and a finding; everything
    else is derived from that session's stored report, transcript and intake.
    """
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _error(422, "Send a JSON body.")
    if not isinstance(body, dict):
        return _error(422, "Send a JSON object.")
    if _operator_stopped():
        return _paused()
    try:
        admission = Admission([
            (PRACTICE_LIMITER, candidate_id, "Too many practice set-ups this hour."),
            (START_ADDRESS_LIMITER, _address(request),
             "Too many sessions from this address this hour."),
        ]).admit()
    except AdmissionRefused as exc:
        return _refused(exc)
    response = _create_practice_admitted(candidate_id, body)
    if isinstance(response, JSONResponse):
        admission.refund()
    return response


def _create_practice_admitted(candidate_id: str, body: dict):
    parent_practice = str(body.get("parent_practice_id") or "") or None
    session_id = str(body.get("session_id") or "")
    finding_id = str(body.get("finding_id") or "")
    mode = str(body.get("mode") or "coached")
    try:
        if parent_practice:
            # An unaided variation of an earlier practice: same finding, new question.
            parent = load_practice(REGISTRY, candidate_id, parent_practice)
            session_id = parent["parent"]["session_id"]
            finding_id = parent["parent"]["finding_id"]
            mode = "unaided"
        record = create_practice(
            REGISTRY,
            candidate_id,
            session_id=session_id,
            finding_id=finding_id,
            minutes=int(body.get("minutes") or 5),
            mode=mode,
            parent_practice_id=parent_practice,
        )
    except NotFound:
        return _error(404, "No such session or practice.")
    except PracticeError as exc:
        return _error(exc.status, str(exc))
    except (TypeError, ValueError) as exc:
        return _error(422, str(exc))
    return practice_view(REGISTRY, candidate_id, record)


@app.get("/api/practice/{practice_id}")
async def get_practice(practice_id: str, authorization: str | None = Header(default=None)):
    try:
        candidate_id = _candidate(authorization)
        record = load_practice(REGISTRY, candidate_id, practice_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such practice.")
    return practice_view(REGISTRY, candidate_id, record)


@app.post("/api/practice/{practice_id}/coaching")
async def show_coaching(practice_id: str, authorization: str | None = Header(default=None)):
    """Record that the checklist was shown, so the attempt is labelled coached."""
    try:
        candidate_id = _candidate(authorization)
        record = mark_coaching_shown(REGISTRY, candidate_id, practice_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such practice.")
    except PracticeError as exc:
        return _error(exc.status, str(exc))
    return practice_view(REGISTRY, candidate_id, record)


@app.get("/api/history")
async def history(authorization: str | None = Header(default=None)):
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    return {
        "sessions": session_rows(REGISTRY, REPORT_STORE, candidate_id),
        "comparisons": comparisons(REPORT_STORE, candidate_id, REGISTRY),
        "recurring_gaps": recurring_gaps(REPORT_STORE, candidate_id, REGISTRY),
        "practice": [
            practice_view(REGISTRY, candidate_id, record)
            for record in list_practices(REGISTRY, candidate_id)
        ],
    }


@app.get("/api/plan")
async def plan(authorization: str | None = Header(default=None)):
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    return practice_plan(REGISTRY, REPORT_STORE, candidate_id)


@app.post("/api/me/delete")
async def delete_my_data(authorization: str | None = Header(default=None)):
    """
    Erase this candidate: intake artifacts, transcripts, logs, reports, store
    rows (so trends lose the points too) and the identity itself. Returns a
    manifest rather than a bare 200.
    """
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    try:
        return await erase_candidate(candidate_id)
    except UnsafeIdentifier as exc:
        return _error(400, str(exc))


async def erase_candidate(candidate_id: str) -> dict:
    """
    Delete everything for one candidate, in an order that cannot leave a
    background job to recreate it:

      1. close any live interview without writing its transcript;
      2. cancel and await evaluation, revision and intake tasks;
      3. remove report-store rows, legacy rows and the candidate directory
         (intakes, sessions, round results, disputes, revisions, practice).

    Copies held by external providers under their own retention policies are
    outside this app's control and are not claimed as deleted.
    """
    session_ids = REGISTRY.session_ids(candidate_id)
    practice_root = REGISTRY.candidate_dir(candidate_id) / "practice"
    practice_count = (
        sum(1 for p in practice_root.iterdir() if p.is_dir()) if practice_root.is_dir() else 0
    )
    live_closed = 0
    for ctx in [c for c in list(_LIVE.values()) if c.candidate_id == candidate_id]:
        ctx.discard = True
        ctx.live.discard_outputs = True
        await _close_out(ctx)
        live_closed += 1
    cancelled = await EVALUATION.cancel_candidate(candidate_id)
    for intake_id in [i for i, owner in _INTAKE_OWNERS.items() if owner == candidate_id]:
        task = _INTAKE_TASKS.pop(intake_id, None)
        _INTAKE_OWNERS.pop(intake_id, None)
        if task is not None and not task.done():
            task.cancel()
            cancelled += 1
    report_rows = REPORT_STORE.delete_candidate(candidate_id)
    legacy = delete_candidate_data(
        candidate_id,
        store_path=STORE_PATH,
        session_dirs=(LOG_DIR,),
        report_dirs=(REPORT_DIR,),
        intake_roots=(INTAKE_DIR,),
    )
    removed = REGISTRY.delete(candidate_id)
    INTAKE_LIMITER.forget(candidate_id)
    _record_deletion(candidate_id)
    return {
        "candidate_id": candidate_id,
        "session_ids": session_ids,
        "practice_records_deleted": practice_count,
        "live_sessions_closed": live_closed,
        "background_tasks_cancelled": cancelled,
        "report_rows_deleted": report_rows,
        "legacy_store_rows_deleted": legacy.store_rows_deleted,
        "paths_deleted": len(removed) + len(legacy.paths_deleted),
        "clean": legacy.clean and not REGISTRY.root.joinpath(candidate_id).exists(),
        "external_providers": (
            "Text and audio sent to Groq and Deepgram for processing are subject to "
            "their own retention policies; this app cannot delete copies they hold."
        ),
    }


_RETENTION_TASK: asyncio.Task | None = None


async def _retention_loop() -> None:
    settings = get_settings()
    while True:
        try:
            await retention_sweep(
                REGISTRY,
                settings.guest_retention_days,
                erase_candidate,
                skip=lambda cid: any(c.candidate_id == cid for c in _LIVE.values()),
            )
        except Exception:  # noqa: BLE001 — the sweep must never take the server down
            pass
        await asyncio.sleep(settings.retention_sweep_interval_s)


async def _start_retention() -> None:
    global _RETENTION_TASK
    if get_settings().guest_retention_days > 0 and _RETENTION_TASK is None:
        _RETENTION_TASK = asyncio.create_task(_retention_loop())


async def _stop_retention() -> None:
    global _RETENTION_TASK
    if _RETENTION_TASK is not None:
        _RETENTION_TASK.cancel()
        _RETENTION_TASK = None


# ---------------------------------------------------------------------------
# WebSocket session
# ---------------------------------------------------------------------------


class SocketHolder:
    """
    The one socket a session is currently talking to.

    A reconnect replaces the socket but keeps the session, and `EventBus` has no
    unsubscribe — so the bus subscription is made once, against this holder,
    and the holder is repointed.
    """

    __slots__ = ("ws",)

    def __init__(self, ws: WebSocket | None = None) -> None:
        self.ws = ws

    async def send_text(self, text: str) -> None:
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send_text(text)
        except Exception:
            self.ws = None

    async def send_bytes(self, payload: bytes) -> None:
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send_bytes(payload)
        except Exception:
            self.ws = None


@dataclass
class SessionContext:
    """Everything one live session owns. Survives a socket reconnect."""

    session_id: str
    bus: EventBus
    logger: EventLogger
    live: LiveSession
    holder: SocketHolder
    word_ts: dict[str, list] = field(default_factory=dict)
    voice: VoiceSession | None = None
    voice_error: str | None = None
    candidate_id: str | None = None
    session_dir: Path | None = None
    started: bool = False
    opening: dict | None = None
    # Set when the candidate deletes their data mid-session: close without
    # writing anything or queueing evaluation.
    discard: bool = False
    closed_out: bool = False
    # Lifetime. `deadline` is on the monotonic clock and is set once, when the
    # session is created; a reconnect re-attaches to this ctx and inherits it.
    deadline: float = 0.0
    last_activity: float = field(default_factory=time.monotonic)
    idle_warned: bool = False
    deadline_warned: bool = False
    watchdog: asyncio.Task | None = None
    # The one finalisation in flight; every close path awaits this same task.
    close_task: asyncio.Task | None = None
    close_reason: str = ""

    def touch(self) -> None:
        """Candidate input or interviewer speech: the session is not idle."""
        self.last_activity = time.monotonic()
        self.idle_warned = False

    def limits_payload(self) -> dict:
        settings = get_settings()
        return {
            "deadline_s": max(0, round(self.deadline - time.monotonic())),
            "idle_s": settings.session_idle_seconds,
            "idle_warning_s": settings.session_idle_warning_s,
        }


async def _reject(websocket: WebSocket, reason: str, code: int = 1008, **extra) -> None:
    await websocket.send_text(json.dumps({"type": "session_rejected", "reason": reason, **extra}))
    await websocket.close(code=code)


def _now() -> str:
    from interview.candidates import utc_now

    return utc_now()


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    await websocket.accept()
    first = await websocket.receive()
    if first.get("type") == "websocket.disconnect":
        return
    opening: dict = {}
    if first.get("text"):
        try:
            opening = json.loads(first["text"])
        except json.JSONDecodeError:
            opening = {}

    if opening.get("type") == "session_resume" and opening.get("token"):
        resumed = RECONNECT.claim(str(opening["token"]))
        if resumed is not None:
            await _run_socket(websocket, resumed.attachments["ctx"], opening=None)
            return

    settings = get_settings()
    candidate_id: str | None = None
    intake_dir: Path | None = None
    interview: InterviewConfig | None = None
    claims: list[Claim] = []
    objective: dict | None = None
    practice: dict | None = None
    pack_overrides: dict | None = None
    if settings.interviewer_mode == "unavailable":
        await _reject(
            websocket,
            "No interviewer is available: the server has no model credential and "
            "mock providers are switched off.",
            code=1011,
        )
        return
    # The candidate's bearer credential. Not `token`: that key is the
    # single-use resume ticket in `session_resume`.
    if opening.get("auth_token"):
        try:
            candidate_id = REGISTRY.resolve(str(opening["auth_token"]))
        except UnknownCandidate:
            await _reject(websocket, "Your session token is not valid. Reload the page to start again.")
            return
    if opening.get("practice_id"):
        # A targeted practice attempt: everything comes from the stored record.
        if candidate_id is None:
            await _reject(websocket, "Sign-in is required to start a practice.")
            return
        try:
            practice = load_practice(REGISTRY, candidate_id, str(opening["practice_id"]))
            opening = {**opening, "intake_id": practice["parent"]["intake_id"]}
        except NotFound:
            await _reject(websocket, "That practice was not found.")
            return
    if opening.get("intake_id"):
        if candidate_id is None:
            await _reject(websocket, "Sign-in is required to start a prepared interview.")
            return
        try:
            intake_dir = REGISTRY.existing_intake(candidate_id, str(opening["intake_id"]))
        except NotFound:
            await _reject(websocket, "That interview preparation was not found.")
            return
        status = read_json(intake_dir / "status.json", {}) or {}
        if status.get("state") != "ready":
            await _reject(
                websocket,
                "Your interview preparation is not ready "
                f"({status.get('state', 'unknown')}). Finish setup before starting.",
            )
            return
        # Consent recorded at intake covers documents and (for voice) speech.
        # An intake from before consent existed needs it confirmed now.
        if read_consent(intake_dir) is None:
            if not opening.get("consent"):
                await _reject(
                    websocket,
                    "Please confirm consent to external processing before starting.",
                    needs_consent=True,
                )
                return
            write_consent(intake_dir, voice=str(opening.get("lane", "voice")) != "text")
        interview = InterviewConfig.model_validate(read_json(intake_dir / "config.json", {}))
        # The statements the candidate accepted at review: excluded ones are
        # gone, edited ones are their corrections (labelled as such).
        claims = [
            Claim(
                id=str(item["id"]),
                text=str(item["text"]),
                competency=str(item.get("competency") or "general"),
                evidence_kind=str(item.get("evidence_kind") or "candidate_assertion"),
                source_ref=str(item.get("source_path") or ""),
            )
            for item in accepted_claims(intake_dir)
        ]
        objective = objective_for(intake_dir)
        if practice is not None:
            from interview.packs.model import load_pack as _load_pack

            # One round, the practice's own short pack, the round's own rubric.
            interview = InterviewConfig.model_validate(
                {
                    **interview.model_dump(mode="json"),
                    "round": practice["round"],
                    "total_seconds": float(practice["minutes"]) * 60.0,
                    "lane": "text" if str(opening.get("lane")) == "text" else interview.lane,
                }
            )
            pack_overrides = {
                practice["round"]: practice_pack(practice, _load_pack(practice["pack_id"]))
            }
            objective = {
                "competency": practice.get("competency"),
                "note": f"Targeted practice: {practice['dimension_label']}",
            }
        try:
            Coordinator(interview, pack_overrides=pack_overrides)
        except PackUnavailable as exc:
            await _reject(websocket, str(exc))
            return
    elif settings.is_production:
        # A production session must be built from the candidate's own context.
        await _reject(websocket, "Complete setup first: a session needs your intake.")
        return

    if _operator_stopped():
        await _reject(
            websocket,
            "New sessions are paused by the operator. Please try again later.",
            code=1013,
            retry_after_s=300,
        )
        return
    session_id = str(uuid.uuid4())
    try:
        SESSION_CAP.acquire(session_id)
    except SessionCapExceeded as exc:
        await _reject(
            websocket,
            "All interview rooms are in use right now. Try again in a minute or two.",
            code=1013,
            detail=str(exc),
            retry_after_s=60,
        )
        return
    # Starts are limited per candidate and per address, after the cheap
    # validation above and before any provider connection.
    address = _address(websocket)
    try:
        admission = Admission([
            (START_LIMITER, candidate_id or f"anon:{address}", "You have started a lot of sessions this hour."),
            (START_ADDRESS_LIMITER, address, "Too many sessions from this address this hour."),
        ]).admit()
    except AdmissionRefused as exc:
        SESSION_CAP.release(session_id)
        await _reject(
            websocket,
            f"{exc} Try again in about {_wait_words(exc.retry_after_s)}.",
            code=1013,
            retry_after_s=exc.retry_after_s,
        )
        return

    if candidate_id is not None:
        session_dir = REGISTRY.session_dir(candidate_id, session_id)
    else:
        session_dir = LOG_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / "session.jsonl"
    transcript_path = session_dir / "transcript.json"

    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, session_id)

    if interview is not None:
        lane = interview.lane
        intensity = interview.intensity
    else:
        lane = "text" if str(opening.get("lane", "")).lower() == "text" else "voice"
        intensity = opening.get("intensity") or "realistic"
    cfg = SessionConfig(
        use_mock_llm=_use_mock_llm(),
        pack_id=opening.get("pack_id") or "behavioral-core",
        intensity=intensity,
        panel_mode=interview is None and bool(opening.get("panel_mode")) and _panel_allowed(),
        lane=lane,
        # Never the stage-5 fixture: intake claims, or none.
        claims_override=claims,
        interview=interview,
        objective=objective,
        pack_overrides=pack_overrides,
    )
    holder = SocketHolder()

    async def send_text(payload: dict) -> None:
        await holder.send_text(json.dumps(payload))

    async def send_bytes(payload: bytes) -> None:
        await holder.send_bytes(payload)

    voice: VoiceSession | None = None
    voice_error: str | None = None
    if lane == "text":
        speak = TextLaneSpeakPort(bus, session_id)
    elif settings.has_deepgram:
        voice = VoiceSession(
            bus,
            session_id,
            settings,
            send_text,
            send_bytes,
            # Recognition aid only; nothing downstream scores a keyterm hit.
            keyterms=[claim.text for claim in claims],
            voice_for_persona=PANEL_VOICES,
            get_turn_id=lambda: getattr(live, "current_turn_id", None),
        )
        speak = voice
    elif settings.allow_mock_providers and not settings.is_production:
        speak = FakeSpeakPort(bus, session_id)
        voice_error = (
            "DEEPGRAM_API_KEY is not set, so this session is running with mock "
            "audio. It is a development session, not a real interview."
        )
    else:
        SESSION_CAP.release(session_id)
        admission.refund()
        await logger.close()
        await _reject(
            websocket,
            "Voice interviews need a speech provider on the server. Use the text "
            "option instead, or ask the operator to configure it.",
            code=1011,
            can_use_text_lane=True,
        )
        return

    live = LiveSession(
        bus=bus,
        session_id=session_id,
        log_path=str(log_path),
        transcript_path=str(transcript_path),
        speak=speak,
        config=cfg,
    )
    live.attach()
    ctx = SessionContext(
        session_id=session_id,
        bus=bus,
        logger=logger,
        live=live,
        holder=holder,
        voice=voice,
        voice_error=voice_error,
        candidate_id=candidate_id,
        session_dir=session_dir if candidate_id else None,
        opening=opening,
        deadline=time.monotonic() + _session_budget_s(interview),
    )

    if candidate_id is not None:
        write_json(
            session_dir / "meta.json",
            {
                "session_id": session_id,
                "candidate_id": candidate_id,
                "intake_id": str(opening.get("intake_id") or "") or None,
                "created_at": _now(),
                "lane": lane,
                "intensity": intensity,
                "personalised": interview is not None,
                "pack_id": None if interview else cfg.pack_id,
                "config": interview.model_dump(
                    mode="json",
                    include={"target_role", "role_family", "seniority", "round", "intensity", "lane"},
                )
                if interview
                else {},
                "coverage_note": interview.coverage_note() if interview else "",
                "evidence_note": interview.evidence_note() if interview else "",
                "interviewer": "deterministic" if cfg.use_mock_llm else "groq",
                "kind": "practice" if practice else "interview",
                "practice": (
                    {
                        "practice_id": practice["practice_id"],
                        "parent_session_id": practice["parent"]["session_id"],
                        "finding_id": practice["parent"]["finding_id"],
                        "dimension_id": practice["dimension_id"],
                        "dimension_label": practice["dimension_label"],
                        "mode": practice["mode"],
                        "coached": bool(practice.get("coached")),
                    }
                    if practice
                    else None
                ),
                "objective": objective if not practice else None,
                "state": "active",
                "state_history": [{"state": "active", "at": _now()}],
            },
        )
        if practice is not None:
            note_attempt(
                REGISTRY, candidate_id, practice["practice_id"], session_id=session_id, lane=lane
            )

    async def on_tts(event) -> None:
        if event.type != "tts_chunk":
            return
        ctx.touch()
        ctx.word_ts[event.utterance_id] = event.word_timestamps
        if ctx.voice is None and live.config.lane != "text":
            # Mock lane only: a short silence burst so the client's playback
            # accounting has something to advance against.
            await holder.send_bytes(b"\x00" * (320 * 20))

    bus.subscribe("tts_chunk", on_tts)

    async def on_partial(event) -> None:
        # Interim STT text, so the room can show the candidate they are being
        # heard. Display only: the recorded answer is still the final transcript.
        # Typed answers put a synthetic partial on the bus; that is not speech.
        ctx.touch()
        if event.producer == "runtime_ingest":
            return
        await holder.send_text(
            json.dumps({"type": "partial", "turn_id": event.turn_id, "text": event.text})
        )

    bus.subscribe("partial", on_partial)
    _LIVE[session_id] = ctx
    ctx.watchdog = asyncio.create_task(_watchdog(ctx))
    await _run_socket(websocket, ctx, opening=opening)


def _session_budget_s(interview: InterviewConfig | None) -> float:
    """
    Wall-time ceiling for one session: MAX_SESSION_SECONDS, or the session's own
    plan (interview minutes, or a shorter focused practice) plus
    SESSION_OVERRUN_GRACE_S when that is shorter.
    """
    settings = get_settings()
    budget = float(settings.max_session_seconds)
    if interview is not None:
        budget = min(budget, float(interview.total_seconds) + settings.session_overrun_grace_s)
    return budget


async def _watchdog(ctx: SessionContext) -> None:
    """
    Independent of any socket: ends the session at its monotonic deadline or
    after SESSION_IDLE_SECONDS without input, through the one close path.
    Sends a warning first so the room can say what is about to happen.
    """
    settings = get_settings()
    idle_s = float(settings.session_idle_seconds)
    idle_warn = float(min(settings.session_idle_warning_s, idle_s)) if idle_s else 0.0
    deadline_warn = 60.0
    reason = ""
    try:
        while not ctx.closed_out:
            now = time.monotonic()
            deadline_left = ctx.deadline - now
            if deadline_left <= 0:
                reason = "time_limit"
                break
            waits = [deadline_left]
            if not ctx.deadline_warned and deadline_left > deadline_warn:
                waits.append(deadline_left - deadline_warn)
            elif not ctx.deadline_warned:
                ctx.deadline_warned = True
                await ctx.holder.send_text(json.dumps({
                    "type": "session_warning", "reason": "time_limit",
                    "seconds_left": round(deadline_left),
                }))
            if idle_s:
                idle_left = idle_s - (now - ctx.last_activity)
                if idle_left <= 0:
                    reason = "idle"
                    break
                waits.append(idle_left)
                if idle_warn and not ctx.idle_warned:
                    if idle_left <= idle_warn:
                        ctx.idle_warned = True
                        await ctx.holder.send_text(json.dumps({
                            "type": "session_warning", "reason": "idle",
                            "seconds_left": round(idle_left),
                        }))
                    else:
                        waits.append(idle_left - idle_warn)
            await asyncio.sleep(max(0.02, min(waits)))
    except asyncio.CancelledError:
        return
    if reason and not ctx.closed_out:
        # Recorded as session_complete.ended_reason ("time_limit" / "idle") in
        # the log and meta.json; no new event kind is needed.
        ctx.watchdog = None  # finishing; the close path must not cancel this task
        try:
            await _close_out(ctx, reason=reason)
        except Exception:  # noqa: BLE001 — never die silently; the slot is released in _finalise
            logging.getLogger(__name__).exception("watchdog close failed for %s", ctx.session_id)





async def _start_live(ctx: SessionContext) -> None:
    if ctx.started:
        return
    ctx.started = True
    live = ctx.live
    await live.start()
    fake_stt = os.environ.get("FAKE_STT_PATH", "").strip()
    if not fake_stt or not Path(fake_stt).exists():
        return

    async def _run_fake_stt() -> None:
        stt = FakeStt(ctx.bus, Path(fake_stt), ctx.session_id, emit_endpoint=True)
        await stt.run(speed=0.0)
        await live.wait_idle()
        await live.end(reason="limit")

    asyncio.create_task(_run_fake_stt())


async def _switch_to_text(ctx: SessionContext, reason: str) -> None:
    """
    The explicit text fallback. The candidate chose it; nothing is substituted
    silently. Delivery is not assessed for the session afterwards.
    """
    live = ctx.live
    if live.config.lane == "text":
        return
    if ctx.voice is not None:
        await ctx.voice.close()
        ctx.voice = None
    live.speak = TextLaneSpeakPort(ctx.bus, ctx.session_id)
    live.config.lane = "text"
    await live.note_fallback("voice_to_text", reason[:200])
    if ctx.session_dir is not None:
        meta = read_json(ctx.session_dir / "meta.json", {}) or {}
        meta["lane"] = "text"
        meta["lane_note"] = "Started in voice and switched to text; delivery is not assessed."
        write_json(ctx.session_dir / "meta.json", meta)
    await ctx.holder.send_text(json.dumps({"type": "lane_changed", "lane": "text"}))
    if not ctx.started:
        await _start_live(ctx)
    else:
        await _resend_current_question(ctx)


async def _resend_current_question(ctx: SessionContext) -> None:
    """After a lane switch, show the last question as a caption so it is not lost."""
    for entry in reversed(ctx.live.transcript.entries):
        if entry.get("speaker") == "agent":
            await ctx.holder.send_text(
                json.dumps(
                    {
                        "type": "caption",
                        "utterance_id": entry.get("utterance_id", ""),
                        "text": entry.get("text", ""),
                        "persona": None,
                        "speaker_label": entry.get("speaker_label"),
                    }
                )
            )
            return


async def _run_socket(websocket: WebSocket, ctx: SessionContext, *, opening: dict | None) -> None:
    """Drive one socket for a session. Called again, with the same ctx, on reconnect."""
    session_id = ctx.session_id
    live = ctx.live
    bus = ctx.bus
    parked = _PARKED.pop(session_id, None)
    if parked is not None and not parked.done():
        parked.cancel()

    ctx.holder.ws = websocket
    completed = asyncio.Event()

    async def on_wire(msg: dict) -> None:
        await ctx.holder.send_text(json.dumps(msg))
        if msg.get("type") == "session_complete":
            completed.set()

    async def receive_or_complete() -> dict | None:
        # Runtime completion can arrive from a background generation task
        # while the browser is idle. Do not wait for another client message
        # before flushing the log and starting evaluation.
        receive = asyncio.create_task(websocket.receive())
        completion = asyncio.create_task(completed.wait())
        try:
            done, _ = await asyncio.wait(
                {receive, completion}, return_when=asyncio.FIRST_COMPLETED
            )
            if completion in done:
                return None
            return receive.result()
        finally:
            for task in (receive, completion):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receive, completion, return_exceptions=True)

    live.on_wire = on_wire
    ticket = RECONNECT.issue(session_id, ctx=ctx)
    reconnected = opening is None
    if reconnected:
        await live.note_fallback("ws_reconnect", "socket dropped and the same session was resumed")

    settings = get_settings()
    await websocket.send_text(
        json.dumps(
            {
                "type": "session_ready",
                "session_id": session_id,
                "log_path": live.log_path,
                "resume_token": ticket.token,
                "lane": live.config.lane,
                "intensity": live.config.intensity,
                "panel_mode": live.config.panel_mode,
                "resumed": reconnected,
                "personalised": live.config.interview is not None,
                "interviewer": "deterministic" if live.config.use_mock_llm else "groq",
                "practice": bool(live.config.pack_overrides),
                "objective": live.config.objective,
                "voice": {
                    "enabled": ctx.voice is not None,
                    "provider": "deepgram" if ctx.voice is not None else "mock",
                    "target_sample_rate": settings.deepgram_sample_rate,
                    "channels": settings.deepgram_channels,
                    "frame_ms": 20,
                },
                "degraded": ctx.voice_error,
                # Remaining wall time and the idle policy, so the room can show them.
                "limits": ctx.limits_payload(),
            }
        )
    )
    # A reconnecting client needs to see where it is, not a blank room.
    if reconnected:
        payload = live.progress_payload()
        if payload is not None:
            await ctx.holder.send_text(json.dumps(payload))
        await _resend_current_question(ctx)

    push_to_talk = False
    # "Done answering" waits briefly for the provider's last words; run it
    # beside this loop so acks and audio keep flowing meanwhile.
    finalising: set[asyncio.Task] = set()
    last_played_ms = 0
    current_utt = ""
    dropped_binary = 0
    dropped = False

    try:
        if opening is not None and opening.get("type") == "session_start":
            # Only a real provider has a connection to wait for; the text lane
            # and a mock-audio dev session start immediately.
            if live.config.lane == "text" or ctx.voice is None:
                await _start_live(ctx)

        while True:
            message = await receive_or_complete()
            if message is None:
                break
            if message.get("type") == "websocket.disconnect":
                dropped = True
                break
            if message.get("bytes") is not None:
                if ctx.voice is not None:
                    await ctx.voice.push_audio(message["bytes"])
                else:
                    dropped_binary += 1
                continue
            if message.get("text") is None:
                continue
            try:
                msg = json.loads(message["text"])
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype not in ("playback_ack", "ping"):
                ctx.touch()

            if mtype == "session_start":
                if live.config.interview is None:
                    if msg.get("pack_id"):
                        live.config.pack_id = msg["pack_id"]
                    if msg.get("intensity"):
                        live.config.intensity = msg["intensity"]
                    if "panel_mode" in msg:
                        live.config.panel_mode = bool(msg["panel_mode"]) and _panel_allowed()
                if live.config.lane == "text" or ctx.voice is None:
                    await _start_live(ctx)

            elif mtype == "audio_start":
                if ctx.voice is None:
                    await ctx.holder.send_text(
                        json.dumps(
                            {
                                "type": "voice_error",
                                "reason": "voice_disabled",
                                "detail": ctx.voice_error or "This session has no voice provider.",
                            }
                        )
                    )
                    continue
                try:
                    await ctx.voice.start(
                        source_sample_rate=int(msg.get("sample_rate") or 0),
                        channels=int(msg.get("channels") or 1),
                    )
                    if not ctx.started and ctx.opening and ctx.opening.get("type") == "session_start":
                        await _start_live(ctx)
                except (VoiceUnavailable, ProviderCredentialsMissing) as exc:
                    await ctx.holder.send_text(
                        json.dumps(
                            {
                                "type": "voice_error",
                                "reason": "provider_unavailable",
                                "detail": str(exc)[:300],
                                "can_use_text_lane": True,
                            }
                        )
                    )

            elif mtype == "switch_to_text":
                await _switch_to_text(ctx, str(msg.get("reason") or "candidate chose text"))

            elif mtype == "mute":
                if ctx.voice is not None:
                    ctx.voice.set_muted(bool(msg.get("muted", True)))

            elif mtype == "answer_done":
                if ctx.voice is not None:
                    task = asyncio.create_task(ctx.voice.finalise_turn("client"))
                    finalising.add(task)
                    task.add_done_callback(finalising.discard)

            elif mtype == "push_to_talk":
                push_to_talk = bool(msg.get("on", True))
                if ctx.voice is not None:
                    ctx.voice.set_muted(push_to_talk)
                await live.note_fallback(
                    "push_to_talk", f"push-to-talk {'on' if push_to_talk else 'off'}"
                )

            elif mtype == "playback_ack":
                last_played_ms = int(msg.get("played_ms", last_played_ms))
                current_utt = msg.get("utterance_id", current_utt)
                await bus.emit(
                    PlaybackAck(
                        session_id=session_id,
                        turn_id=msg.get("turn_id") or "turn-live",
                        producer="client",
                        t_audio_out=last_played_ms / 1000.0,
                        played_ms=last_played_ms,
                        utterance_id=current_utt or "unknown",
                    )
                )

            elif mtype == "barge_in":
                if push_to_talk and not msg.get("held"):
                    continue
                played = int(msg.get("played_ms", last_played_ms))
                utt = msg.get("utterance_id", current_utt)
                turn_id = msg.get("turn_id") or "turn-live"
                if ctx.voice is not None:
                    await ctx.voice.barge_in(utt or None)
                await bus.emit(
                    BargeIn(
                        session_id=session_id,
                        turn_id=turn_id,
                        producer="client",
                        t_audio_in=played / 1000.0,
                        confidence=0.99,
                    )
                )
                # The truncation contract: cut at the last word the client
                # acknowledged playing, not at what was generated.
                wts = ctx.word_ts.get(utt, [])
                last_idx, last_word = 0, "[unknown]"
                for i, w in enumerate(wts):
                    if w.offset_ms <= played:
                        last_idx = i
                        last_word = w.word
                await bus.emit(
                    Truncate(
                        session_id=session_id,
                        turn_id=turn_id,
                        producer="server",
                        t_audio_out=played / 1000.0,
                        last_heard_word=last_word,
                        word_index=last_idx,
                        utterance_id=utt or "",
                    )
                )

            elif mtype in ("candidate_final", "candidate_text"):
                text = str(msg.get("text", "")).strip()[:4000]
                if text:
                    await live.ingest_final(text)

            elif mtype == "session_end":
                await live.end(reason="client")
                break

    except WebSocketDisconnect:
        dropped = True

    if dropped and not live._closed and not ctx.closed_out:
        if dropped_binary:
            await live.note_fallback(
                "ws_reconnect", f"dropped {dropped_binary} unsolicited binary frame(s)"
            )
        ctx.holder.ws = None
        _PARKED[session_id] = asyncio.create_task(_park(ctx))
        return

    try:
        await _close_out(ctx)
    except Exception:  # noqa: BLE001 — the path that started the close reports it
        logging.getLogger(__name__).exception("finalisation failed for %s", session_id)
    if not dropped:
        await websocket.close(code=1000)


async def _close_out(ctx: SessionContext, *, reason: str = "disconnect") -> None:
    """
    The one finalisation path: explicit end, natural end, time limit, idle,
    disconnect expiry and deletion all arrive here. The first caller starts it;
    every later caller awaits the same task, so it runs exactly once (one
    transcript write, one evaluation enqueue, one slot release).
    """
    if ctx.close_task is None:
        ctx.closed_out = True
        ctx.close_reason = reason
        ctx.close_task = asyncio.ensure_future(_finalise(ctx, reason))
    await asyncio.shield(ctx.close_task)


async def _finalise(ctx: SessionContext, reason: str) -> None:
    """
    End the session if still open, release everything, persist, then queue
    evaluation. Evaluation starts only after the live session is closed and the
    log is flushed (contract 6).
    """
    _time = time

    ended_ts = _time.time()
    live = ctx.live
    watchdog = ctx.watchdog
    ctx.watchdog = None
    if watchdog is not None and watchdog is not asyncio.current_task():
        watchdog.cancel()
    parked = _PARKED.pop(ctx.session_id, None)
    if parked is not None and parked is not asyncio.current_task() and not parked.done():
        parked.cancel()
    try:
        if ctx.session_dir is not None and not ctx.discard:
            set_state(ctx.session_dir, "finalising")
        if not live._closed:
            # Speak the closer only if someone is connected to hear it.
            await live.end(
                reason=reason,
                speak_closer=ctx.holder.ws is not None and reason != "disconnect",
            )
        else:
            # An end() already running elsewhere (client end, natural end):
            # let it finish its closer and transcript before speech is torn down.
            await live.wait_ended()
    finally:
        # Always release provider connections and the session slot, even if
        # ending raised; otherwise one failure would leak a live slot.
        if ctx.voice is not None:
            await ctx.voice.close()
            ctx.voice = None
        RECONNECT.revoke(ctx.session_id)
        SESSION_CAP.release(ctx.session_id)
        _LIVE.pop(ctx.session_id, None)
    await ctx.bus.drain()
    await ctx.logger.close()
    if ctx.discard:
        return
    if ctx.session_dir is not None and ctx.candidate_id is not None:
        coordinator = live.coordinator
        set_state(
            ctx.session_dir,
            "finalising",
            ended_reason=live.ended_reason,
            rounds=coordinator.summary() if coordinator is not None else [],
            candidate_turns=sum(
                1 for e in live.transcript.entries if e.get("speaker") == "candidate"
            ),
            # Audit of plan-based questions asked because the model failed.
            degraded_turns=list(live.degraded_turns),
            evaluation={
                "timings": {
                    "interview_ended_at": _now(),
                    "interview_ended_ts": ended_ts,
                    "finalised_at": _now(),
                    "finalise_s": round(_time.time() - ended_ts, 3),
                }
            },
        )
        EVALUATION.enqueue(ctx.candidate_id, ctx.session_id)


async def _park(ctx: SessionContext) -> None:
    """Hold a dropped session open for the resume window, then close it out."""
    try:
        await asyncio.sleep(_PARK_TTL_S)
    except asyncio.CancelledError:
        return
    await _close_out(ctx, reason="disconnect")
