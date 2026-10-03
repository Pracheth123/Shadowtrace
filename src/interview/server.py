"""
Stage 4 composition root — FastAPI WebSocket session.

Creates the bus, logger, live runtime and speak port. Transport and inference
meet only through the bus (contract 1). This module is allowed to import both.

Run:
    uvicorn interview.server:app --host 0.0.0.0 --port 8000 --reload

Env:
    SESSION_LOG_DIR   default logs/
    GROQ_API_KEY      from .env — required for live model calls
    MOCK_LLM=1        force FakeLlm (default when GROQ_API_KEY is unset)
    FAKE_STT_PATH     if set, replay this transcript fixture after session_start
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
from interview.llm.env import groq_api_key, load_dotenv
from interview.mocks.fake_stt import FakeStt
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort

app = FastAPI(title="Shadowtrace — Stage 7")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv()
LOG_DIR = Path(os.environ.get("SESSION_LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)


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
    return {"status": "ok", "stage": 7}


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    session_dir = LOG_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / "session.jsonl"
    transcript_path = session_dir / "transcript.json"

    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, session_id)

    async def on_wire(msg: dict) -> None:
        try:
            await websocket.send_text(json.dumps(msg))
        except Exception:
            pass

    # Subscribe: when tts_chunk arrives, send a short silence burst so the client
    # can drive playback_ack / barge-in without a real TTS vendor.
    async def on_tts(event) -> None:
        if event.type != "tts_chunk":
            return
        # ~200 ms silence PCM s16le 16 kHz mono
        silence = b"\x00" * (320 * 20)
        try:
            await websocket.send_bytes(silence)
        except Exception:
            pass

    bus.subscribe("tts_chunk", on_tts)

    cfg = SessionConfig(use_mock_llm=_use_mock_llm(), pack_id="behavioral-core")
    live = LiveSession(
        bus=bus,
        session_id=session_id,
        log_path=str(log_path),
        transcript_path=str(transcript_path),
        speak=FakeSpeakPort(bus, session_id),
        config=cfg,
        on_wire=on_wire,
    )
    live.attach()

    word_ts_by_utt: dict[str, list] = {}

    async def on_tts_store(event) -> None:
        if event.type == "tts_chunk":
            word_ts_by_utt[event.utterance_id] = event.word_timestamps

    bus.subscribe("tts_chunk", on_tts_store)

    await websocket.send_text(
        json.dumps(
            {
                "type": "session_ready",
                "session_id": session_id,
                "log_path": str(log_path),
            }
        )
    )

    started = False
    last_played_ms = 0
    current_utt = ""

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "text" in message and message["text"] is not None:
                msg = json.loads(message["text"])
                mtype = msg.get("type")
                if mtype == "session_start":
                    if msg.get("pack_id"):
                        live.config.pack_id = msg["pack_id"]
                    if msg.get("intensity"):
                        live.config.intensity = msg["intensity"]
                    if not started:
                        started = True
                        await live.start()
                        fake_stt = os.environ.get("FAKE_STT_PATH", "").strip()
                        if fake_stt:
                            stt_path = Path(fake_stt)
                            if stt_path.exists():

                                async def _run_fake_stt() -> None:
                                    stt = FakeStt(
                                        bus,
                                        stt_path,
                                        session_id,
                                        emit_endpoint=True,
                                    )
                                    await stt.run(speed=0.0)
                                    await live.wait_idle()
                                    await live.end(reason="limit")

                                asyncio.create_task(_run_fake_stt())
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
                elif mtype == "candidate_final":
                    # Mock / browser-STT lane: client sends final text (PCM STT is transport/).
                    await live.ingest_final(msg.get("text", "").strip())
                elif mtype == "session_end":
                    await live.end(reason="client")
                    break
            elif "bytes" in message and message["bytes"] is not None:
                # PCM frames reserved for transport STT; mock lane uses candidate_final.
                pass
    except WebSocketDisconnect:
        pass
    finally:
        if not live._closed:
            await live.end(reason="disconnect")
        await bus.drain()
        await logger.close()


@app.get("/")
async def root():
    return JSONResponse(
        {
            "service": "shadowtrace",
            "stage": 7,
            "ws": "/ws/session",
            "client": "Run the Vite app in client/ and point WS to this server.",
        }
    )
