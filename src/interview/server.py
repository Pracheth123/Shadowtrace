"""
Composition root — FastAPI WebSocket session (stage 4), hardened at stage 11.

Creates the bus, logger, live runtime and speak port. Transport and inference
meet only through the bus (contract 1). This module is allowed to import both.

Run:
    uvicorn interview.server:app --host 0.0.0.0 --port 8000 --reload

Env:
    SESSION_LOG_DIR     default logs/
    GROQ_API_KEY        from .env — required for live model calls
    MOCK_LLM=1          force FakeLlm (default when GROQ_API_KEY is unset)
    FAKE_STT_PATH       if set, replay this transcript fixture after session_start
    PANEL_MODE=1        allow panel mode (stage 11; off by default)
    MAX_LIVE_SESSIONS   concurrent live session cap, default 8
    INTAKE_RPM          intake requests per candidate per hour, default 5
    STORE_PATH          longitudinal store for delete-my-data, default fixtures/roadmap/longitudinal.sqlite

Video: there is deliberately no video ingest path. The client may show a local
camera preview, but no frame is ever sent here, so video cannot become a model
or score input (contract 9). Unsolicited binary frames are counted and dropped.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
from interview.llm.env import groq_api_key, load_dotenv
from interview.mocks.fake_stt import FakeStt
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort, TextLaneSpeakPort

STAGE = 11

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

# One process, one set of limits. A second process gets its own; that is stated
# in hardening/limits.py rather than implied here.
SESSION_CAP = SessionCap(limit=int(os.environ.get("MAX_LIVE_SESSIONS", "8")))
INTAKE_LIMITER = RateLimiter(
    RateLimit(max_events=int(os.environ.get("INTAKE_RPM", "5")), window_s=3600.0)
)
RECONNECT = ReconnectRegistry(ttl_s=float(os.environ.get("RESUME_TTL_S", "180")))

# A session whose socket dropped waits this long for the client to come back
# before it is closed out and its report queued.
_PARK_TTL_S = float(os.environ.get("RESUME_TTL_S", "180"))

# session_id → the timer that will close a parked session. A resume cancels it.
_PARKED: dict[str, asyncio.Task] = {}


class SocketHolder:
    """
    The one socket a session is currently talking to.

    A reconnect replaces the socket but keeps the session, and `EventBus` has no
    unsubscribe — so the bus subscription is made once, against this holder,
    and the holder is repointed. Subscribing a second handler bound to the new
    socket would leave the first one alive and writing to a dead socket, which
    blocks `bus.drain()` and wedges the session that was just resumed.
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


def _panel_allowed() -> bool:
    """Panel mode is behind a flag and off unless explicitly turned on."""
    return os.environ.get("PANEL_MODE", "").strip().lower() in ("1", "true", "yes")


def _use_mock_llm() -> bool:
    """Mock unless GROQ_API_KEY is set and MOCK_LLM is not forced on."""
    forced = os.environ.get("MOCK_LLM", "").strip().lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    return not bool(groq_api_key())


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "stage": STAGE,
        "panel_mode": _panel_allowed(),
        "live_sessions": SESSION_CAP.live,
        "session_limit": SESSION_CAP.limit,
    }


@app.post("/intake")
async def intake(payload: dict):
    """
    Rate-limited intake entry point.

    Indexing a repository is the most expensive thing an unauthenticated caller
    can ask for, so it is capped per candidate. The cap bounds compute; it is
    not an eligibility gate and it never reaches a score.
    """
    candidate_id = str(payload.get("candidate_id", "")).strip()
    if not candidate_id:
        return JSONResponse({"error": "candidate_id is required"}, status_code=400)
    if not INTAKE_LIMITER.allow(candidate_id):
        retry = round(INTAKE_LIMITER.retry_after_s(candidate_id), 1)
        return JSONResponse(
            {
                "error": "intake rate limit reached",
                "retry_after_s": retry,
            },
            status_code=429,
            headers={"Retry-After": str(int(retry) + 1)},
        )
    # Fallbacks the intake pipeline already supports (stage 6): resume-only when
    # no repo is given, and a local path when a clone is not possible.
    fallbacks: list[str] = []
    if not payload.get("repo_url") and not payload.get("repo_path"):
        fallbacks.append("resume_only")
    elif payload.get("repo_path"):
        fallbacks.append("fallback_repo")
    return {
        "accepted": True,
        "candidate_id": candidate_id,
        "fallbacks": fallbacks,
        "note": "Run tools/intake.py to build the Claims File.",
    }


@app.post("/me/delete")
async def delete_my_data(payload: dict):
    """
    Delete-my-data. Removes store rows, session directories and intake artifacts.

    Returns the manifest so the candidate can see what went, rather than being
    asked to trust a bare 200.
    """
    candidate_id = str(payload.get("candidate_id", "")).strip()
    if not candidate_id:
        return JSONResponse({"error": "candidate_id is required"}, status_code=400)
    try:
        report = delete_candidate_data(
            candidate_id,
            store_path=STORE_PATH,
            session_dirs=(LOG_DIR,),
            report_dirs=(REPORT_DIR,),
            intake_roots=(INTAKE_DIR,),
        )
    except UnsafeIdentifier as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    INTAKE_LIMITER.forget(candidate_id)
    return report.to_dict()


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    await websocket.accept()

    # ------------------------------------------------------------------
    # Either resume a parked session or start a new one.
    # ------------------------------------------------------------------
    first = await websocket.receive()
    if first.get("type") == "websocket.disconnect":
        return
    opening: dict = {}
    if first.get("text"):
        try:
            opening = json.loads(first["text"])
        except json.JSONDecodeError:
            opening = {}

    resumed = None
    if opening.get("type") == "session_resume" and opening.get("token"):
        resumed = RECONNECT.claim(str(opening["token"]))

    if resumed is not None:
        state = resumed.attachments
        await _run_socket(
            websocket,
            session_id=resumed.session_id,
            bus=state["bus"],
            logger=state["logger"],
            live=state["live"],
            word_ts_by_utt=state["word_ts"],
            holder=state["holder"],
            opening=None,
        )
        return

    session_id = str(uuid.uuid4())
    try:
        SESSION_CAP.acquire(session_id)
    except SessionCapExceeded as exc:
        await websocket.send_text(
            json.dumps({"type": "session_rejected", "reason": str(exc)})
        )
        await websocket.close(code=1013)  # try again later
        return

    session_dir = LOG_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / "session.jsonl"
    transcript_path = session_dir / "transcript.json"

    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, session_id)

    lane = "text" if str(opening.get("lane", "")).lower() == "text" else "voice"
    panel_requested = bool(opening.get("panel_mode"))
    cfg = SessionConfig(
        use_mock_llm=_use_mock_llm(),
        pack_id=opening.get("pack_id") or "behavioral-core",
        intensity=opening.get("intensity") or "realistic",
        panel_mode=panel_requested and _panel_allowed(),
        lane=lane,
    )
    speak = (
        TextLaneSpeakPort(bus, session_id)
        if lane == "text"
        else FakeSpeakPort(bus, session_id)
    )
    live = LiveSession(
        bus=bus,
        session_id=session_id,
        log_path=str(log_path),
        transcript_path=str(transcript_path),
        speak=speak,
        config=cfg,
    )
    live.attach()

    word_ts_by_utt: dict[str, list] = {}
    holder = SocketHolder()

    async def on_tts(event) -> None:
        """Record word timings, and send a silence burst so the client can ack."""
        if event.type != "tts_chunk":
            return
        word_ts_by_utt[event.utterance_id] = event.word_timestamps
        await holder.send_bytes(b"\x00" * (320 * 20))

    bus.subscribe("tts_chunk", on_tts)

    await _run_socket(
        websocket,
        session_id=session_id,
        bus=bus,
        logger=logger,
        live=live,
        word_ts_by_utt=word_ts_by_utt,
        holder=holder,
        opening=opening,
    )


async def _run_socket(
    websocket: WebSocket,
    *,
    session_id: str,
    bus: EventBus,
    logger: EventLogger,
    live: LiveSession,
    word_ts_by_utt: dict[str, list],
    holder: SocketHolder,
    opening: dict | None,
) -> None:
    """
    Drive one socket for `live`. Called again, with the same objects, on reconnect.

    A reconnect rebinds `on_wire` and keeps the same bus, logger, log file and
    turn_id sequence: one session, one append-only log, across a dropped socket.
    """
    parked = _PARKED.pop(session_id, None)
    if parked is not None and not parked.done():
        parked.cancel()

    # Repoint the one subscription at the new socket. `live.on_wire` is a single
    # slot, so reassigning it drops the old socket with it.
    holder.ws = websocket

    async def on_wire(msg: dict) -> None:
        await holder.send_text(json.dumps(msg))

    live.on_wire = on_wire

    ticket = RECONNECT.issue(
        session_id,
        bus=bus,
        logger=logger,
        live=live,
        word_ts=word_ts_by_utt,
        holder=holder,
    )
    reconnected = opening is None
    if reconnected:
        await live.note_fallback(
            "ws_reconnect", "socket dropped and the same session was resumed"
        )

    await websocket.send_text(
        json.dumps(
            {
                "type": "session_ready",
                "session_id": session_id,
                "log_path": live.log_path,
                "resume_token": ticket.token,
                "lane": live.config.lane,
                "panel_mode": live.config.panel_mode,
                "resumed": reconnected,
            }
        )
    )

    started = reconnected
    push_to_talk = False
    last_played_ms = 0
    current_utt = ""
    dropped_binary = 0
    # A drop reaches us two ways — as a `websocket.disconnect` *message* and as
    # a WebSocketDisconnect *exception*, depending on the server. Both mean the
    # candidate may be mid-answer on a flaky network, so both park the session
    # rather than end it. Treating the message as a clean exit was a bug: it
    # closed the session out, and the reconnect then resumed something already
    # finished, which answered nothing.
    dropped = False

    async def start_session(msg: dict) -> None:
        nonlocal started
        if msg.get("pack_id"):
            live.config.pack_id = msg["pack_id"]
        if msg.get("intensity"):
            live.config.intensity = msg["intensity"]
        if "panel_mode" in msg:
            live.config.panel_mode = bool(msg["panel_mode"]) and _panel_allowed()
        if started:
            return
        started = True
        await live.start()
        fake_stt = os.environ.get("FAKE_STT_PATH", "").strip()
        if not fake_stt:
            return
        stt_path = Path(fake_stt)
        if not stt_path.exists():
            return

        async def _run_fake_stt() -> None:
            stt = FakeStt(bus, stt_path, session_id, emit_endpoint=True)
            await stt.run(speed=0.0)
            await live.wait_idle()
            await live.end(reason="limit")

        asyncio.create_task(_run_fake_stt())

    try:
        if opening is not None and opening.get("type") == "session_start":
            await start_session(opening)

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                dropped = True
                break
            if message.get("bytes") is not None:
                # PCM frames are reserved for transport STT, which the mock lane
                # does not use. Nothing else — video included — has an ingest
                # path here, so anything unexpected is counted and dropped.
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
                await start_session(msg)

            elif mtype == "push_to_talk":
                # The fallback for a room where open-mic VAD cannot work. While
                # it is on, the candidate holds to talk, so an unheld barge-in
                # is noise and is ignored rather than cutting the agent off.
                push_to_talk = bool(msg.get("on", True))
                await live.note_fallback(
                    "push_to_talk",
                    f"push-to-talk {'on' if push_to_talk else 'off'}",
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
                wts = word_ts_by_utt.get(utt, [])
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
                # candidate_text is the text lane; candidate_final is the
                # browser-STT lane. Both arrive as a finished candidate turn,
                # so the agent and guard below see exactly the same thing.
                await live.ingest_final(msg.get("text", "").strip())

            elif mtype == "session_end":
                await live.end(reason="client")
                break

    except WebSocketDisconnect:
        dropped = True

    if dropped and not live._closed:
        if dropped_binary:
            await live.note_fallback(
                "ws_reconnect",
                f"dropped {dropped_binary} unsolicited binary frame(s)",
            )
        holder.ws = None
        _PARKED[session_id] = asyncio.create_task(
            _park(session_id, bus, logger, live)
        )
        return

    await _close_out(session_id, bus, logger, live)


async def _close_out(
    session_id: str,
    bus: EventBus,
    logger: EventLogger,
    live: LiveSession,
) -> None:
    """End the session if it is still open, then release everything it holds."""
    if not live._closed:
        await live.end(reason="disconnect")
    RECONNECT.revoke(session_id)
    SESSION_CAP.release(session_id)
    _PARKED.pop(session_id, None)
    await bus.drain()
    await logger.close()


async def _park(
    session_id: str,
    bus: EventBus,
    logger: EventLogger,
    live: LiveSession,
) -> None:
    """
    Hold a dropped session open for the resume window, then close it out.

    Nothing is replayed on resume: an utterance that was in flight when the
    socket died was not acknowledged, so by the truncation contract it was not
    heard, and the transcript already records only what was.
    """
    try:
        await asyncio.sleep(_PARK_TTL_S)
    except asyncio.CancelledError:
        return  # the candidate came back
    await _close_out(session_id, bus, logger, live)


@app.get("/")
async def root():
    return JSONResponse(
        {
            "service": "shadowtrace",
            "stage": STAGE,
            "ws": "/ws/session",
            "panel_mode": _panel_allowed(),
            "lanes": ["voice", "text"],
            "client": "Run the Vite app in client/ and point WS to this server.",
        }
    )
