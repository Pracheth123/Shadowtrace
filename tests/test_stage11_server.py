"""
Stage 11 — the composition root, driven through a real WebSocket.

These exercise the paths `test_stage11.py` cannot reach, because they live in
`server.py` rather than in a layer: the panel flag, the lane switch, the session
cap, the intake rate limit, reconnect onto the same log, push-to-talk gating,
the dropped-binary rule, and delete-my-data over HTTP.

Each test points the server at a tmp log directory and resets the in-process
limits, so they do not leak into one another.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from interview.events.log import read_events
from interview.hardening.limits import RateLimit, RateLimiter, SessionCap
from interview.hardening.reconnect import ReconnectRegistry


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fresh app module state per test, writing into tmp_path."""
    import interview.server as srv

    monkeypatch.setattr(srv, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(srv, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(srv, "INTAKE_DIR", tmp_path / "intake")
    monkeypatch.setattr(srv, "STORE_PATH", tmp_path / "store.sqlite")
    monkeypatch.setattr(srv, "SESSION_CAP", SessionCap(limit=8))
    monkeypatch.setattr(
        srv, "INTAKE_LIMITER", RateLimiter(RateLimit(max_events=2, window_s=3600.0))
    )
    monkeypatch.setattr(srv, "RECONNECT", ReconnectRegistry(ttl_s=60.0))
    monkeypatch.setattr(srv, "_PARKED", {})
    monkeypatch.setenv("MOCK_LLM", "1")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    return srv


def _read_until(ws, wanted: str | set[str], limit: int = 200) -> list[dict]:
    """
    Read text frames until one of `wanted` arrives.

    TestClient's receive() blocks, so a test must always read toward a frame it
    knows is coming rather than draining speculatively.
    """
    targets = {wanted} if isinstance(wanted, str) else set(wanted)
    seen: list[dict] = []
    for _ in range(limit):
        data = ws.receive()
        if data.get("type") == "websocket.close":
            break
        if data.get("text") is None:
            continue  # a binary silence burst
        msg = json.loads(data["text"])
        seen.append(msg)
        if msg.get("type") in targets:
            break
    return seen


def _ready(ws) -> dict:
    """The first text frame is always session_ready (or session_rejected)."""
    while True:
        data = ws.receive()
        if data.get("text") is not None:
            return json.loads(data["text"])


def _answer(ws, text: str) -> list[dict]:
    """Send a candidate turn and read through the agent's reply."""
    ws.send_text(json.dumps({"type": "candidate_final", "text": text}))
    return _read_until(ws, "agent_utterance_end")


def _end(ws) -> list[dict]:
    ws.send_text(json.dumps({"type": "session_end"}))
    return _read_until(ws, "session_complete")


# ─────────────────────────────────────────────────────────────────────────────
# Panel flag and lanes
# ─────────────────────────────────────────────────────────────────────────────


def test_health_reports_the_stage_and_the_panel_flag(server, monkeypatch) -> None:
    monkeypatch.delenv("PANEL_MODE", raising=False)
    with TestClient(server.app) as client:
        body = client.get("/health").json()
    assert body["stage"] == 16
    assert body["panel_mode"] is False
    assert body["session_limit"] == 8


def test_panel_mode_is_refused_unless_the_flag_is_set(server, monkeypatch) -> None:
    """A client asking for a panel does not get one without PANEL_MODE."""
    monkeypatch.delenv("PANEL_MODE", raising=False)
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(
                json.dumps(
                    {
                        "type": "session_start",
                        "pack_id": "behavioral-core",
                        "panel_mode": True,
                    }
                )
            )
            ready = _ready(ws)
            assert ready["type"] == "session_ready"
            assert ready["panel_mode"] is False
            _read_until(ws, "agent_utterance_end")  # the opener
            _end(ws)


def test_panel_mode_runs_when_the_flag_is_set(server, monkeypatch) -> None:
    monkeypatch.setenv("PANEL_MODE", "1")
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(
                json.dumps(
                    {
                        "type": "session_start",
                        "pack_id": "behavioral-core",
                        "intensity": "panel",
                        "panel_mode": True,
                    }
                )
            )
            ready = _ready(ws)
            assert ready["panel_mode"] is True
            log_path = Path(ready["log_path"])
            _read_until(ws, "agent_utterance_end")  # the opener
            for answer in (
                "I owned the payments pipeline from design through launch.",
                "I disagreed with the on-call rota and wrote the reason down.",
            ):
                _answer(ws, answer)
            _end(ws)

    events = list(read_events(log_path))
    personas = {e.persona for e in events if e.type == "draft_ready" and e.persona}
    assert personas, "panel mode produced no persona on an agent line"


def test_text_lane_emits_no_audio_and_accepts_typed_answers(server) -> None:
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(
                json.dumps(
                    {
                        "type": "session_start",
                        "pack_id": "behavioral-core",
                        "lane": "text",
                    }
                )
            )
            ready = _ready(ws)
            assert ready["lane"] == "text"
            log_path = Path(ready["log_path"])
            _read_until(ws, "agent_utterance_end")  # the opener
            ws.send_text(
                json.dumps(
                    {
                        "type": "candidate_text",
                        "text": "I owned the migration and measured the lag myself.",
                    }
                )
            )
            messages = _read_until(ws, "agent_utterance_end")
            _end(ws)

    # The typed turn reached the agent: a caption came back.
    assert any(m.get("type") == "caption" for m in messages)
    events = list(read_events(log_path))
    assert not [e for e in events if e.type == "tts_chunk"]
    assert any(
        e.type == "fallback_used" and e.kind == "text_lane" for e in events
    )
    complete = [e for e in events if e.type == "session_complete"]
    assert complete and complete[0].lane == "text"


# ─────────────────────────────────────────────────────────────────────────────
# Hardening through the socket
# ─────────────────────────────────────────────────────────────────────────────


def test_session_cap_rejects_the_next_connection(server, monkeypatch) -> None:
    """
    The cap is checked after the opening frame, not on accept.

    It has to be: the server cannot know whether a new socket is a *resume* of a
    session it is already carrying until it has read that frame, and a resume
    must not be counted against the cap a second time.
    """
    monkeypatch.setattr(server, "SESSION_CAP", SessionCap(limit=1))
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/session") as first:
            first.send_text(
                json.dumps({"type": "session_start", "pack_id": "behavioral-core"})
            )
            assert _ready(first)["type"] == "session_ready"
            _read_until(first, "agent_utterance_end")  # the opener
            assert server.SESSION_CAP.live == 1

            with client.websocket_connect("/ws/session") as second:
                second.send_text(json.dumps({"type": "session_start"}))
                rejected = _ready(second)
                assert rejected["type"] == "session_rejected"
                assert "limit is 1" in rejected["detail"]
                assert rejected["retry_after_s"] > 0

            _end(first)
    # The slot is released once the session actually ends.
    assert server.SESSION_CAP.live == 0


def test_reconnect_resumes_the_same_session_and_log(server) -> None:
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(
                json.dumps(
                    {"type": "session_start", "pack_id": "behavioral-core"}
                )
            )
            ready = _ready(ws)
            session_id = ready["session_id"]
            token = ready["resume_token"]
            log_path = Path(ready["log_path"])
            assert token
            _read_until(ws, "agent_utterance_end")  # the opener
            _answer(ws, "I owned the payments pipeline end to end.")
            # Socket dies without a session_end: the server parks the session.

        with client.websocket_connect("/ws/session") as again:
            again.send_text(json.dumps({"type": "session_resume", "token": token}))
            resumed = _ready(again)
            assert resumed["type"] == "session_ready"
            assert resumed["resumed"] is True
            assert resumed["session_id"] == session_id
            # A fresh, different token is issued; the old one is spent.
            assert resumed["resume_token"] != token
            _answer(again, "I disagreed with the rota and wrote the reason down.")
            _end(again)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    headers = [l for l in lines if json.loads(l).get("kind") == "session_header"]
    assert len(headers) == 1, "a reconnect must not open a second log"
    events = list(read_events(log_path))
    assert any(
        e.type == "fallback_used" and e.kind == "ws_reconnect" for e in events
    )
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len([e for e in events if e.type == "session_complete"]) == 1


def test_a_spent_or_unknown_resume_token_starts_a_new_session(server) -> None:
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"type": "session_resume", "token": "nonsense"}))
            ready = _ready(ws)
            # Unknown token: treated as a new session, not as an error the
            # candidate has to understand.
            assert ready["type"] == "session_ready"
            assert ready["resumed"] is False
            ws.send_text(json.dumps({"type": "session_start"}))
            _read_until(ws, "agent_utterance_end")
            _end(ws)


def test_unsolicited_binary_frames_are_dropped(server) -> None:
    """There is no video ingest path; anything binary is counted and dropped."""
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(
                json.dumps({"type": "session_start", "pack_id": "behavioral-core"})
            )
            ready = _ready(ws)
            log_path = Path(ready["log_path"])
            _read_until(ws, "agent_utterance_end")  # the opener
            # A plausible video frame. The session must survive and ignore it.
            ws.send_bytes(b"\xff\xd8\xff\xe0" + b"\x11" * 2048)
            messages = _answer(ws, "I owned the pipeline and measured the lag.")
            _end(ws)

    assert any(m.get("type") == "caption" for m in messages), "session died"
    body = log_path.read_text(encoding="utf-8").casefold()
    assert "\\xff" not in body and "d8ffe0" not in body


def test_push_to_talk_is_logged_and_gates_an_unheld_barge_in(server) -> None:
    with TestClient(server.app) as client:
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(
                json.dumps({"type": "session_start", "pack_id": "behavioral-core"})
            )
            ready = _ready(ws)
            log_path = Path(ready["log_path"])
            _read_until(ws, "agent_utterance_end")  # the opener
            ws.send_text(json.dumps({"type": "push_to_talk", "on": True}))
            # Not held: this is room noise, not an interruption.
            ws.send_text(json.dumps({"type": "barge_in", "played_ms": 40}))
            # Held: a real interruption.
            ws.send_text(
                json.dumps({"type": "barge_in", "played_ms": 40, "held": True})
            )
            _end(ws)

    events = list(read_events(log_path))
    assert any(
        e.type == "fallback_used" and e.kind == "push_to_talk" for e in events
    )
    barges = [e for e in events if e.type == "barge_in"]
    assert len(barges) == 1, "an unheld barge-in must be ignored under push-to-talk"


# ─────────────────────────────────────────────────────────────────────────────
# Intake rate limit and erasure over HTTP
# ─────────────────────────────────────────────────────────────────────────────


def test_the_stub_intake_and_unauthenticated_delete_are_gone(server) -> None:
    """
    Stage 15 removed `/intake` (it acknowledged without running intake) and
    the unauthenticated `/me/delete` (anyone could erase anyone by naming
    them). The real endpoints live under /api and require the bearer token;
    they are exercised in test_stage15_journey.py.
    """
    with TestClient(server.app) as client:
        assert client.post("/intake", json={"candidate_id": "ada"}).status_code in (404, 405)
        assert client.post("/me/delete", json={"candidate_id": "ada"}).status_code in (404, 405)
        assert client.post("/api/me/delete").status_code == 401


def test_background_completion_finalises_without_another_client_message(server) -> None:
    """An idle socket must not hold finished interviews/evaluation open."""
    with TestClient(server.app) as client:
        with client.websocket_connect('/ws/session') as ws:
            ws.send_json({'type': 'session_start', 'pack_id': 'behavioral-core', 'lane': 'text'})
            ready = _ready(ws)
            _read_until(ws, 'agent_utterance_end')
            ctx = server._LIVE[ready['session_id']]
            # Like the final reply task: completion occurs independently of
            # the receive loop. No session_end or disconnect from the client.
            client.portal.call(ctx.live.end, 'complete')
            _read_until(ws, 'session_complete')
            closed = ws.receive()
            assert closed == {'type': 'websocket.close', 'code': 1000, 'reason': ''}
            assert ctx.closed_out
            assert ready['session_id'] not in server._LIVE
