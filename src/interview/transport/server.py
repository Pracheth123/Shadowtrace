"""
FastAPI WebSocket server — Stage 2.

Exposes one endpoint: /ws/session
The client connects, sends PCM audio, receives the cached clip back.
Every step lands in a JSONL event log.

Run:
    uvicorn src.interview.transport.server:app --host 0.0.0.0 --port 8000 --reload

Environment variables:
    DEEPGRAM_API_KEY  — optional; if absent, STT falls back to mock mode
    SESSION_LOG_DIR   — directory for per-session JSONL files (default: logs/)
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from interview.transport.session import InterviewSession

app = FastAPI(title="Shadowtrace Transport — Stage 2")

LOG_DIR = Path(os.environ.get("SESSION_LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Serve the minimal client from client/
CLIENT_DIR = Path(__file__).parent.parent.parent.parent / "client"
if CLIENT_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(CLIENT_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Redirect to the minimal client."""
    index_file = CLIENT_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Shadowtrace Stage 2</h1><p>Connect via /ws/session</p>")


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    log_path = LOG_DIR / f"{session_id}.jsonl"

    session = InterviewSession(
        websocket=websocket,
        log_path=log_path,
        session_id=session_id,
        stt_api_key=os.environ.get("DEEPGRAM_API_KEY", ""),
    )
    try:
        await session.run()
    except WebSocketDisconnect:
        pass


@app.get("/health")
async def health():
    return {"status": "ok", "stage": 2}
