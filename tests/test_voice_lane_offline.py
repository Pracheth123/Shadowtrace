"""
Voice lane, offline (roadmap item 5).

The labelled mock audio path (no DEEPGRAM_API_KEY, mocks allowed, not
production) through the real WebSocket: three round handovers, a barge-in that
truncates at the acknowledged position, and natural completion into feedback.
This proves wiring only. It is not evidence that real speech recognition or
synthesis works; that needs the live checks in docs/VOICE_ACCEPTANCE.md.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from tests.test_stage15_journey import (  # noqa: F401 — `srv` is a fixture
    ANSWERS,
    SOFTWARE_RESUME,
    auth,
    guest,
    read_until,
    run_intake,
    srv,
    wait_for,
)


def test_mock_voice_lane_hands_over_three_rounds_and_ends_naturally(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, round="full", lane="voice",
            files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")},
        )
        assert intake["status"]["state"] == "ready"
        transitions: list[dict] = []
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"type": "session_start", "auth_token": headers["_token"], "intake_id": intake["intake_id"]}))
            ready = read_until(ws, {"session_ready", "session_rejected"})[-1]
            assert ready["type"] == "session_ready"
            assert ready["lane"] == "voice"
            # Explicitly labelled: a mock provider, never presented as Deepgram.
            assert ready["voice"]["provider"] == "mock" and ready["degraded"]
            read_until(ws, {"agent_utterance_end"})
            complete = None
            for i, answer in enumerate(ANSWERS * 6):
                ws.send_text(json.dumps({"type": "candidate_final", "text": answer}))
                got = read_until(ws, {"agent_utterance_end", "session_complete"})
                transitions += [m for m in got if m["type"] == "round_transition"]
                if got[-1]["type"] == "session_complete":
                    complete = got[-1]
                    break
                if any(m["type"] == "round_transition" for m in got):
                    more = read_until(ws, {"agent_utterance_end", "session_complete"})
                    if more and more[-1]["type"] == "session_complete":
                        complete = more[-1]
                        break
            assert complete is not None, "the interview did not finish on its own"
        assert complete["ended_reason"] in ("complete", "limit")
        # HR → hiring manager → specialist: two spoken handovers between three rounds.
        assert [(t["from_label"] != t["to_label"]) for t in transitions] == [True, True]
        session_id = ready["session_id"]
        view = wait_for(
            lambda: client.get(f"/api/sessions/{session_id}/evaluation", headers=auth(headers)).json(),
            lambda v: v["state"] in ("complete", "failed"),
        )
        assert view["state"] == "complete" and view["report_ready"]
        report = client.get(f"/api/sessions/{session_id}/report", headers=auth(headers)).json()
        assert report["lane"] == "voice"


def test_barge_in_truncates_at_the_acknowledged_word_in_the_voice_lane(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, lane="voice",
            files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")},
        )
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"type": "session_start", "auth_token": headers["_token"], "intake_id": intake["intake_id"]}))
            ready = read_until(ws, {"session_ready"})[-1]
            opener = read_until(ws, {"agent_utterance_end"})
            utt = next(m for m in opener if m["type"] in ("agent_utterance_start", "caption") and m.get("utterance_id"))
            # The client heard ~0.4 s of the question, then the candidate spoke over it.
            ws.send_text(json.dumps({"type": "playback_ack", "utterance_id": utt["utterance_id"], "played_ms": 400}))
            ws.send_text(json.dumps({"type": "barge_in", "utterance_id": utt["utterance_id"], "played_ms": 400}))
            ws.send_text(json.dumps({"type": "candidate_final", "text": ANSWERS[0]}))
            read_until(ws, {"agent_utterance_end"})
            ws.send_text(json.dumps({"type": "session_end"}))
            read_until(ws, {"session_complete"})
        transcript = client.get(f"/api/sessions/{ready['session_id']}/transcript", headers=auth(headers)).text
    # The truncation contract: what was heard, marked as interrupted, not the full generated line.
    assert "[interrupted — this is what was heard]" in transcript
    first_agent = next(line for line in transcript.splitlines() if "Interviewer" in line)
    assert len(first_agent) < len(utt["text"]) + 60
