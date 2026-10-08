"""
Feedback latency benchmark — the real evaluation job service, end to end.

    python tools/bench_feedback.py --sessions 6 --live --out logs/bench_feedback
    python tools/bench_feedback.py --sessions 3 --out logs/bench_feedback_mock   # offline

Builds full three-round sessions (HR → hiring manager → specialist) from the
evaluation set's strong and incomplete answers, alternating software and sales,
then runs `EvaluationService` exactly as the server does after an interview:
per-round jobs, concurrent rounds, persistence, report assembly. One session at
a time, like a single candidate waiting for feedback.

Measured per session, from the moment evaluation is queued (the server queues it
immediately after finalising the transcript, which takes milliseconds):

  first_result_s   first validated round result persisted
  report_s         complete report persisted

Also recorded: failures and their categories, round count, transcript size,
provider attempts, limiter wait, request time, tokens, fallback use. Written
as JSON; the script prints medians and p95 with the sample size. These are
measurements of this environment on this day, not guarantees.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interview.candidates import CandidateRegistry, read_json, write_json  # noqa: E402
from interview.roadmap.reports import ReportStore  # noqa: E402
from interview.services.evaluation import EvaluationService  # noqa: E402

ROUND_PACK = {
    "software": {"hr": "hr-core", "hiring_manager": "manager-core", "domain_specialist": "specialist-software"},
    "sales": {"hr": "hr-core", "hiring_manager": "manager-core", "domain_specialist": "specialist-sales"},
}
LABEL = {"hr": "Recruiter", "hiring_manager": "Hiring manager", "domain_specialist": "Specialist"}


def examples() -> list[dict]:
    path = ROOT / "evaluation_set" / "examples.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_transcript(family: str, pool: list[dict], index: int) -> list[dict]:
    """Two answers per round where available, from strong/incomplete/ambiguous examples."""
    usable = [e for e in pool if e["family"] == family and e["category"] in ("strong", "incomplete", "ambiguous", "transcription_error")]
    transcript = []
    for round_id in ("hr", "hiring_manager", "domain_specialist"):
        candidates = [e for e in usable if e["round"] == round_id] or [e for e in usable if e["round"] == "hiring_manager"]
        picked = [candidates[(index + k) % len(candidates)] for k in range(min(2, len(candidates)))]
        for n, example in enumerate(picked):
            transcript.append({"speaker": "agent", "turn_id": f"{round_id}-q{n}", "text": example["question"], "round": round_id})
            transcript.append({"speaker": "candidate", "turn_id": f"{round_id}-a{n}", "text": example["answer"], "round": round_id})
    return transcript


async def run(sessions: int, live: bool, out: Path) -> dict:
    from interview.config import get_settings

    settings = get_settings()
    if live:
        from interview.evaluation.role_eval import GroqEvaluator
        from interview.llm.client import GroqModelClient

        evaluator = GroqEvaluator(GroqModelClient(), max_tokens=settings.eval_max_tokens)
    else:
        from interview.evaluation.role_eval import MockEvaluator

        evaluator = MockEvaluator()
    pool = examples()
    root = Path(tempfile.mkdtemp(prefix="bench_feedback_"))
    registry = CandidateRegistry(root / "candidates")
    store = ReportStore(root / "reports.sqlite")
    service = EvaluationService(registry, store, lambda: evaluator)
    rows = []
    for index in range(sessions):
        family = "software" if index % 2 == 0 else "sales"
        guest = registry.create_guest()
        session_id = f"bench-{index:02d}-{guest.candidate_id[:8]}"
        directory = registry.session_dir(guest.candidate_id, session_id)
        directory.mkdir(parents=True)
        transcript = build_transcript(family, pool, index)
        write_json(directory / "transcript.json", transcript)
        write_json(
            directory / "meta.json",
            {
                "session_id": session_id,
                "candidate_id": guest.candidate_id,
                "intake_id": None,
                "created_at": "2026-10-06T00:00:00.000Z",
                "lane": "text",
                "config": {"target_role": "Backend engineer" if family == "software" else "Account executive",
                           "role_family": family, "round": "full", "seniority": "mid", "intensity": "realistic"},
                "rounds": [
                    {"round": r, "label": LABEL[r], "pack_id": ROUND_PACK[family][r], "questions_asked": 2}
                    for r in ("hr", "hiring_manager", "domain_specialist")
                ],
                "ended_reason": "complete",
                "state": "finalising",
                "evaluation": {"timings": {"interview_ended_ts": time.time()}},
            },
        )
        wall = time.perf_counter()
        service.enqueue(guest.candidate_id, session_id)
        await service.wait(session_id)
        meta = read_json(directory / "meta.json", {})
        evaluation = meta.get("evaluation") or {}
        timings = evaluation.get("timings") or {}
        rounds = evaluation.get("rounds") or []
        row = {
            "session": session_id,
            "family": family,
            "state": meta.get("state"),
            "rounds": len(rounds),
            "transcript_turns": len(transcript),
            "transcript_chars": sum(len(t["text"]) for t in transcript),
            "first_result_s": timings.get("first_result_s"),
            "report_s": timings.get("report_s"),
            "wall_s": round(time.perf_counter() - wall, 2),
            "round_detail": [
                {k: r.get(k) for k in ("round", "state", "category", "elapsed_s", "queue_delay_s", "replies",
                                       "repairs", "provider_attempts", "limiter_wait_s", "request_s",
                                       "model_used", "fallback_used", "usage")}
                for r in rounds
            ],
        }
        rows.append(row)
        print(f"{session_id} {family:8s} {row['state']:9s} first={row['first_result_s']} report={row['report_s']} wall={row['wall_s']}")

    def stats(key: str) -> dict:
        values = sorted(r[key] for r in rows if isinstance(r.get(key), (int, float)) and r["state"] == "complete")
        if not values:
            return {"n": 0}
        p95_index = max(0, min(len(values) - 1, round(0.95 * (len(values) - 1))))
        return {
            "n": len(values),
            "median": round(statistics.median(values), 2),
            "p95_nearest_rank": round(values[p95_index], 2),
            "min": round(values[0], 2),
            "max": round(values[-1], 2),
        }

    summary = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "live": live,
        "evaluator": f"{evaluator.provider}:{evaluator.model}",
        "settings": {
            "requests_per_minute": settings.groq_requests_per_minute,
            "max_retries": settings.groq_max_retries,
            "eval_request_timeout_s": settings.groq_eval_timeout_s,
            "eval_max_tokens": settings.eval_max_tokens,
            "eval_max_repairs": settings.eval_max_repairs,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "note": "single developer machine on a residential connection; one session at a time",
        },
        "sessions": len(rows),
        "completed": sum(1 for r in rows if r["state"] == "complete"),
        "failed": [r["session"] for r in rows if r["state"] != "complete"],
        "first_result_s": stats("first_result_s"),
        "report_s": stats("report_s"),
        "rows": rows,
    }
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"bench_{'live' if live else 'mock'}_{int(time.time())}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    print(f"wrote {path}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=6)
    parser.add_argument("--live", action="store_true", help="real Groq evaluator (billable)")
    parser.add_argument("--out", default="logs/bench_feedback")
    args = parser.parse_args()
    asyncio.run(run(args.sessions, args.live, Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
