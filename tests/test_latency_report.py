"""tools/latency_report.py: stage maths, live/mock separation, and no content leakage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import latency_report  # noqa: E402

SECRET_TEXT = "my private answer about the Acme layoffs"


def _session(root: Path, name: str, interviewer: str, base: float) -> None:
    d = root / "candidates" / "c1" / "sessions" / name
    d.mkdir(parents=True)
    events = [
        {"type": "partial", "turn_id": "t1", "t_emit": base, "text": SECRET_TEXT},
        {"type": "endpoint", "turn_id": "t1", "t_emit": base + 0.10},
        {"type": "final_transcript", "turn_id": "t1", "t_emit": base + 0.40, "text": SECRET_TEXT},
        {"type": "question_planned", "turn_id": "t1", "t_emit": base + 1.40, "text": "prompt-ish"},
        {"type": "tts_chunk", "turn_id": "t1", "t_emit": base + 1.70},
        {"type": "playback_ack", "turn_id": "t1", "t_emit": base + 1.90},
        {"type": "model_call", "turn_id": "t1", "t_emit": base + 1.3, "role": "live_interviewer",
         "model": "m", "latency_ms": 900.0, "ok": False, "status_code": 429, "error": SECRET_TEXT},
    ]
    (d / "session.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "interviewer": interviewer, "lane": "voice", "ended_reason": "complete", "state": "complete",
        "evaluation_attempts": 1,
        "evaluation": {
            "evaluator": {"provider": "groq" if interviewer == "groq" else "mock"},
            "timings": {"interview_ended_ts": 100.0, "last_run_started_ts": 100.5, "finalise_s": 0.05,
                        "first_result_s": 8.0, "report_s": 20.0},
            "rounds": [{"state": "complete", "queue_delay_s": 0.5, "slot_wait_s": 0.4, "limiter_wait_s": 0.1,
                        "request_s": 6.0, "repairs": 1, "provider_attempts": 2, "fallback_used": True,
                        "usage": {"total_tokens": 1234}, "error": SECRET_TEXT}],
        },
    }), encoding="utf-8")


def test_stages_are_computed_and_live_is_separated_from_mock(tmp_path: Path) -> None:
    _session(tmp_path, "live1", "groq", 10.0)
    _session(tmp_path, "mock1", "deterministic", 50.0)
    sessions = latency_report.collect(tmp_path)
    live = latency_report.summarise([s for s in sessions if s["interviewer"] == "groq"])
    assert live["sessions"] == 1
    turns = live["live_turns"]
    assert turns["speech_end_to_final_ms"]["p50"] == 300.0
    assert turns["final_to_decision_ms"]["p50"] == 1000.0
    assert turns["decision_to_first_audio_ms"]["p50"] == 300.0
    assert turns["first_audio_to_played_ms"]["p50"] == 200.0
    assert turns["speech_end_to_played_ms"]["p50"] == 1800.0
    ev = live["evaluation"]
    assert ev["first_result_s"]["p50"] == 8.0 and ev["report_s"]["p50"] == 20.0
    assert ev["end_to_eval_start_s"]["p50"] == 0.5
    assert ev["repairs"] == 1 and ev["provider_attempts"] == 2 and ev["fallback_rounds"] == 1
    assert ev["tokens"] == {"total_tokens": 1234}
    assert live["model_calls"]["by_status"] == {"429": 1}


def test_report_never_contains_transcript_prompt_or_error_text(tmp_path: Path, monkeypatch) -> None:
    _session(tmp_path, "live1", "groq", 10.0)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["latency_report", "--data-dir", str(tmp_path), "--out", str(out)])
    assert latency_report.main() == 0
    written = next(out.glob("latency_*.json")).read_text(encoding="utf-8")
    assert SECRET_TEXT not in written and "prompt-ish" not in written
