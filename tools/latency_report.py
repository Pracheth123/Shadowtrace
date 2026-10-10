#!/usr/bin/env python3
"""
Aggregate latency and cost report over stored sessions — operational metrics only.

    python tools/latency_report.py --data-dir data --out logs/latency_report
    python tools/latency_report.py --data-dir data --live-only

Reads, per session, `session.jsonl` (event types, turn ids, monotonic
timestamps, model-call latency/status) and `meta.json` (evaluation timings and
per-round counters). It never reads or writes transcript text, prompts, model
replies, error messages or credentials: the output contains numbers, model ids,
categories and counts only.

Live path, per candidate turn (all on the server's monotonic clock, `t_emit`):

    speech end (endpoint) → final STT (final_transcript)
                          → model decision (question_planned / draft_ready)
                          → first audio out (first tts_chunk)
                          → first audio played (first playback_ack from the browser)

After the interview, per session (wall clock, from meta.json):

    interview end → finalised → evaluation started (queue + limiter/slot wait)
                  → first validated round → full report

Every session is labelled by what actually ran (`interviewer`: groq or
deterministic; `evaluator`: groq or mock). Mock sessions are reported
separately and must never be quoted as live latency.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

LIVE_STAGES = ("speech_end_to_final_ms", "final_to_decision_ms", "decision_to_first_audio_ms",
               "first_audio_to_played_ms", "speech_end_to_played_ms")


def pct(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(p / 100.0 * (len(ordered) - 1))))
    return round(ordered[index], 3)


def describe(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": pct(values, 50),
        "p95": pct(values, 95),
        "max": round(max(values), 3) if values else None,
    }


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def live_turns(log: Path) -> tuple[list[dict], list[dict]]:
    """Per-turn stage timings and model-call metrics. Event text is never kept."""
    by_turn: dict[str, dict[str, float]] = defaultdict(dict)
    calls: list[dict] = []
    for event in read_jsonl(log):
        kind = event.get("type")
        t = event.get("t_emit")
        turn = event.get("turn_id")
        if kind == "model_call":
            calls.append({
                "role": event.get("role"),
                "model": event.get("model"),
                "latency_ms": event.get("latency_ms"),
                "ok": bool(event.get("ok")),
                # A status code, never the provider's message text.
                "status": event.get("status_code"),
            })
            continue
        if not turn or not isinstance(t, (int, float)):
            continue
        slot = by_turn[turn]
        first = {"endpoint": "endpoint", "final_transcript": "final", "question_planned": "decision",
                 "draft_ready": "decision", "tts_chunk": "audio", "playback_ack": "played"}.get(kind)
        if first and first not in slot:
            slot[first] = float(t)
    turns = []
    for turn_id, s in by_turn.items():
        if "endpoint" not in s and "final" not in s:
            continue  # an interviewer-only turn (opener, handover)
        row: dict = {}

        def gap(a: str, b: str, key: str) -> None:
            if a in s and b in s and s[b] >= s[a]:
                row[key] = round((s[b] - s[a]) * 1000.0, 1)

        gap("endpoint", "final", "speech_end_to_final_ms")
        gap("final", "decision", "final_to_decision_ms")
        gap("decision", "audio", "decision_to_first_audio_ms")
        gap("audio", "played", "first_audio_to_played_ms")
        gap("endpoint", "played", "speech_end_to_played_ms")
        if row:
            turns.append(row)
    return turns, calls


def evaluation_metrics(meta: dict) -> dict | None:
    evaluation = meta.get("evaluation") or {}
    timings = evaluation.get("timings") or {}
    if not timings:
        return None
    ended = timings.get("interview_ended_ts") or timings.get("queued_ts")
    started = timings.get("last_run_started_ts")
    rounds = evaluation.get("rounds") or []
    usage = Counter()
    for r in rounds:
        for key, value in (r.get("usage") or {}).items():
            if isinstance(value, (int, float)):
                usage[key] += value
    return {
        "state": meta.get("state"),
        "evaluator": (evaluation.get("evaluator") or {}).get("provider"),
        "finalise_s": timings.get("finalise_s"),
        "end_to_eval_start_s": round(float(started) - float(ended), 3) if started and ended else None,
        "first_result_s": timings.get("first_result_s"),
        "report_s": timings.get("report_s"),
        "rounds": [
            {k: r.get(k) for k in ("state", "category", "queue_delay_s", "slot_wait_s", "limiter_wait_s",
                                   "request_s", "elapsed_s", "replies", "repairs", "provider_attempts",
                                   "fallback_used", "model_used", "cache_hit")}
            for r in rounds
        ],
        "tokens": dict(usage),
        "attempts": int(meta.get("evaluation_attempts") or 0),
    }


def collect(data_dir: Path) -> list[dict]:
    sessions = []
    for meta_path in sorted(data_dir.glob("candidates/*/sessions/*/meta.json")):
        directory = meta_path.parent
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        log = directory / "session.jsonl"
        turns, calls = live_turns(log) if log.is_file() else ([], [])
        sessions.append({
            "interviewer": meta.get("interviewer") or "unknown",
            "lane": meta.get("lane"),
            "kind": meta.get("kind") or "interview",
            "ended_reason": meta.get("ended_reason"),
            "turns": turns,
            "model_calls": calls,
            "evaluation": evaluation_metrics(meta),
        })
    return sessions


def summarise(sessions: list[dict]) -> dict:
    out: dict = {"sessions": len(sessions)}
    turns = [t for s in sessions for t in s["turns"]]
    out["live_turns"] = {stage: describe([t[stage] for t in turns if stage in t]) for stage in LIVE_STAGES}
    calls = [c for s in sessions for c in s["model_calls"]]
    out["model_calls"] = {
        "n": len(calls),
        "failed": sum(1 for c in calls if not c["ok"]),
        "by_status": dict(Counter(str(c["status"]) for c in calls if not c["ok"])),
        "latency_ms": describe([c["latency_ms"] for c in calls if isinstance(c.get("latency_ms"), (int, float))]),
        "models": dict(Counter(c["model"] for c in calls)),
    }
    evals = [s["evaluation"] for s in sessions if s["evaluation"]]
    rounds = [r for e in evals for r in e["rounds"]]
    tokens = Counter()
    for e in evals:
        tokens.update(e["tokens"])

    def num(values):
        return [float(v) for v in values if isinstance(v, (int, float))]

    out["evaluation"] = {
        "jobs": len(evals),
        "states": dict(Counter(e["state"] for e in evals)),
        "finalise_s": describe(num(e["finalise_s"] for e in evals)),
        "end_to_eval_start_s": describe(num(e["end_to_eval_start_s"] for e in evals)),
        "first_result_s": describe(num(e["first_result_s"] for e in evals if e["state"] == "complete")),
        "report_s": describe(num(e["report_s"] for e in evals if e["state"] == "complete")),
        "round_queue_delay_s": describe(num(r["queue_delay_s"] for r in rounds)),
        "round_slot_wait_s": describe(num(r["slot_wait_s"] for r in rounds)),
        "round_limiter_wait_s": describe(num(r["limiter_wait_s"] for r in rounds)),
        "round_request_s": describe(num(r["request_s"] for r in rounds)),
        "repairs": int(sum(num(r["repairs"] for r in rounds))),
        "provider_attempts": int(sum(num(r["provider_attempts"] for r in rounds))),
        "fallback_rounds": sum(1 for r in rounds if r.get("fallback_used")),
        "cache_hits": sum(1 for r in rounds if r.get("cache_hit")),
        "failure_categories": dict(Counter(r["category"] for r in rounds if r.get("state") == "failed")),
        "tokens": dict(tokens),
    }
    out["ended_reasons"] = dict(Counter(s["ended_reason"] for s in sessions))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("logs/latency_report"))
    parser.add_argument("--live-only", action="store_true", help="only sessions whose interviewer was groq")
    args = parser.parse_args()
    sessions = collect(args.data_dir)
    live = [s for s in sessions if s["interviewer"] == "groq"]
    mock = [s for s in sessions if s["interviewer"] != "groq"]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_dir": str(args.data_dir),
        "note": ("Operational metrics only (no transcript text, prompts or errors). 'mock' sessions ran the "
                 "deterministic interviewer and/or mock evaluator and are not live latency."),
        "targets_not_claims": {
            "first_feedback_s": {"p50": 15, "p95": 45},
            "full_report_s": {"p50": 45, "p95": 90},
            "speech_end_to_playback_s": {"p50": 2.5, "p95": 5},
        },
        "live": summarise(live),
    }
    if not args.live_only:
        report["mock"] = summarise(mock)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"latency_{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k not in ("mock",)} | (
        {"mock_sessions": report["mock"]["sessions"]} if "mock" in report else {}), indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
