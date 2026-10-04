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

Video: there is deliberately no video ingest path. The client may show a local
camera preview, but no frame is ever sent here (contract 9).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
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
    RateLimit,
    RateLimiter,
    SessionCap,
    SessionCapExceeded,
)
from interview.hardening.reconnect import ReconnectRegistry
from interview.intake.documents import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_UPLOAD_LABEL,
    SUPPORTED_UPLOAD_SUFFIXES,
    UploadRejected,
    extract_upload,
)
from interview.llm.env import groq_api_key, load_dotenv
from interview.mocks.fake_stt import FakeStt
from interview.services.evaluation import build_services, set_state
from interview.services.history import (
    comparisons,
    practice_plan,
    recurring_gaps,
    session_rows,
)
from interview.services.intake import IntakeInputs, load_intake, run_intake_job, write_status
from interview.session.coordinator import Coordinator, PackUnavailable
from interview.session.interview_config import InterviewConfig
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort, TextLaneSpeakPort
from interview.session.tools import Claim
from interview.transport.voice_session import VoiceSession, VoiceUnavailable

STAGE = 15

app = FastAPI(title=f"Shadowtrace — Stage {STAGE}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv()
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

SESSION_CAP = SessionCap(limit=int(os.environ.get("MAX_LIVE_SESSIONS", "8")))
INTAKE_LIMITER = RateLimiter(
    RateLimit(max_events=int(os.environ.get("INTAKE_RPH", "10")), window_s=3600.0)
)
GUEST_LIMITER = RateLimiter(RateLimit(max_events=20, window_s=3600.0))
RECONNECT = ReconnectRegistry(ttl_s=float(os.environ.get("RESUME_TTL_S", "180")))
_PARK_TTL_S = float(os.environ.get("RESUME_TTL_S", "180"))
_PARKED: dict[str, asyncio.Task] = {}
_INTAKE_TASKS: dict[str, asyncio.Task] = {}


def _use_mock_llm() -> bool:
    """Mock unless GROQ_API_KEY is set and MOCK_LLM is not forced on."""
    forced = os.environ.get("MOCK_LLM", "").strip().lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    return not bool(groq_api_key())


# Data services. Rebound by `configure_data_dir` (tests point it at tmp_path).
# Built in services/ so this module never imports evaluation directly: the
# composition root only queues a job after the live session has closed.
def configure_data_dir(root: Path) -> None:
    global REGISTRY, REPORT_STORE, EVALUATION
    REGISTRY, REPORT_STORE, EVALUATION = build_services(
        Path(root), use_mock_llm=_use_mock_llm
    )


configure_data_dir(Path(os.environ.get("DATA_DIR", "data")))


def _panel_allowed() -> bool:
    return os.environ.get("PANEL_MODE", "").strip().lower() in ("1", "true", "yes")


def _error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _candidate(authorization: str | None) -> str:
    return REGISTRY.resolve(authorization)


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
            "interviewer": "deterministic" if _use_mock_llm() else "groq",
            "voice": "deepgram" if settings.has_deepgram else "unavailable",
            "evaluator": (
                "groq" if (settings.has_groq and not _use_mock_llm()) else "mock"
                if settings.allow_mock_providers and not settings.is_production
                else "unavailable"
            ),
        },
        "uploads": {
            "suffixes": list(SUPPORTED_UPLOAD_SUFFIXES),
            "label": SUPPORTED_UPLOAD_LABEL,
            "max_bytes": MAX_UPLOAD_BYTES,
        },
    }


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
    client = request.client.host if request.client else "unknown"
    if not GUEST_LIMITER.allow(client):
        return _error(429, "Too many new guest identities from this address.")
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


@app.post("/api/intake", status_code=202)
async def create_intake(
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
    resume: UploadFile | None = File(default=None),
    work_sample: UploadFile | None = File(default=None),
):
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    if not INTAKE_LIMITER.allow(candidate_id):
        retry = round_up(INTAKE_LIMITER.retry_after_s(candidate_id))
        return JSONResponse(
            {"error": "Intake limit reached for this hour.", "retry_after_s": retry},
            status_code=429,
            headers={"Retry-After": str(retry)},
        )
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
    write_status(directory, "queued", intake_id=intake_id, warnings=[])
    _INTAKE_TASKS[intake_id] = asyncio.create_task(
        run_intake_job(
            directory, IntakeInputs(config=config, resume=resume_doc, work_sample=sample_doc)
        )
    )
    return {"intake_id": intake_id, "status": read_json(directory / "status.json", {})}


def round_up(value: float) -> int:
    return int(value) + 1


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
        )
    }


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
    return report


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
async def retry_evaluation(session_id: str, authorization: str | None = Header(default=None)):
    try:
        candidate_id, _ = _session_dir(authorization, session_id)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    except NotFound:
        return _error(404, "No such session.")
    meta = EVALUATION.retry(candidate_id, session_id)
    return {"state": meta.get("state")}


@app.get("/api/history")
async def history(authorization: str | None = Header(default=None)):
    try:
        candidate_id = _candidate(authorization)
    except UnknownCandidate:
        return _error(401, "Not signed in.")
    return {
        "sessions": session_rows(REGISTRY, REPORT_STORE, candidate_id),
        "comparisons": comparisons(REPORT_STORE, candidate_id),
        "recurring_gaps": recurring_gaps(REPORT_STORE, candidate_id),
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
    session_ids = REGISTRY.session_ids(candidate_id)
    for task_id in list(_INTAKE_TASKS):
        task = _INTAKE_TASKS[task_id]
        if task.done():
            _INTAKE_TASKS.pop(task_id, None)
    report_rows = REPORT_STORE.delete_candidate(candidate_id)
    try:
        legacy = delete_candidate_data(
            candidate_id,
            store_path=STORE_PATH,
            session_dirs=(LOG_DIR,),
            report_dirs=(REPORT_DIR,),
            intake_roots=(INTAKE_DIR,),
        )
    except UnsafeIdentifier as exc:
        return _error(400, str(exc))
    removed = REGISTRY.delete(candidate_id)
    INTAKE_LIMITER.forget(candidate_id)
    return {
        "candidate_id": candidate_id,
        "session_ids": session_ids,
        "report_rows_deleted": report_rows,
        "legacy_store_rows_deleted": legacy.store_rows_deleted,
        "paths_deleted": len(removed) + len(legacy.paths_deleted),
        "clean": legacy.clean and not REGISTRY.root.joinpath(candidate_id).exists(),
    }


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


async def _reject(websocket: WebSocket, reason: str, code: int = 1008, **extra) -> None:
    await websocket.send_text(json.dumps({"type": "session_rejected", "reason": reason, **extra}))
    await websocket.close(code=code)


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
    # The candidate's bearer credential. Not `token`: that key is the
    # single-use resume ticket in `session_resume`.
    if opening.get("auth_token"):
        try:
            candidate_id = REGISTRY.resolve(str(opening["auth_token"]))
        except UnknownCandidate:
            await _reject(websocket, "Your session token is not valid. Reload the page to start again.")
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
        interview = InterviewConfig.model_validate(read_json(intake_dir / "config.json", {}))
        claims = [
            Claim(
                id=str(item["id"]),
                text=str(item["text"]),
                competency=str(item.get("competency") or "general"),
                evidence_kind=str(item.get("evidence_kind") or "candidate_assertion"),
                source_ref=str(item.get("source_path") or ""),
            )
            for item in (read_json(intake_dir / "claims.json", {}) or {}).get("claims", [])
        ]
        try:
            Coordinator(interview)
        except PackUnavailable as exc:
            await _reject(websocket, str(exc))
            return
    elif settings.is_production:
        # A production session must be built from the candidate's own context.
        await _reject(websocket, "Complete setup first: a session needs your intake.")
        return

    session_id = str(uuid.uuid4())
    try:
        SESSION_CAP.acquire(session_id)
    except SessionCapExceeded as exc:
        await _reject(websocket, str(exc), code=1013)
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
                "state": "active",
                "state_history": [{"state": "active", "at": _now()}],
            },
        )

    async def on_tts(event) -> None:
        if event.type != "tts_chunk":
            return
        ctx.word_ts[event.utterance_id] = event.word_timestamps
        if ctx.voice is None and live.config.lane != "text":
            # Mock lane only: a short silence burst so the client's playback
            # accounting has something to advance against.
            await holder.send_bytes(b"\x00" * (320 * 20))

    bus.subscribe("tts_chunk", on_tts)
    await _run_socket(websocket, ctx, opening=opening)


def _now() -> str:
    from interview.candidates import utc_now

    return utc_now()


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

    async def on_wire(msg: dict) -> None:
        await ctx.holder.send_text(json.dumps(msg))

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
                "voice": {
                    "enabled": ctx.voice is not None,
                    "provider": "deepgram" if ctx.voice is not None else "mock",
                    "target_sample_rate": settings.deepgram_sample_rate,
                    "channels": settings.deepgram_channels,
                    "frame_ms": 20,
                },
                "degraded": ctx.voice_error,
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
            message = await websocket.receive()
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
                    await ctx.voice.finalise_turn("client")

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

    if dropped and not live._closed:
        if dropped_binary:
            await live.note_fallback(
                "ws_reconnect", f"dropped {dropped_binary} unsolicited binary frame(s)"
            )
        ctx.holder.ws = None
        _PARKED[session_id] = asyncio.create_task(_park(ctx))
        return

    await _close_out(ctx)


async def _close_out(ctx: SessionContext) -> None:
    """
    End the session if still open, release everything, persist, then queue
    evaluation. Evaluation starts only after the live session is closed and the
    log is flushed (contract 6).
    """
    live = ctx.live
    if ctx.session_dir is not None:
        set_state(ctx.session_dir, "finalising")
    if not live._closed:
        await live.end(reason="disconnect")
    if ctx.voice is not None:
        await ctx.voice.close()
        ctx.voice = None
    RECONNECT.revoke(ctx.session_id)
    SESSION_CAP.release(ctx.session_id)
    _PARKED.pop(ctx.session_id, None)
    await ctx.bus.drain()
    await ctx.logger.close()
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
        )
        EVALUATION.enqueue(ctx.candidate_id, ctx.session_id)


async def _park(ctx: SessionContext) -> None:
    """Hold a dropped session open for the resume window, then close it out."""
    try:
        await asyncio.sleep(_PARK_TTL_S)
    except asyncio.CancelledError:
        return
    await _close_out(ctx)
