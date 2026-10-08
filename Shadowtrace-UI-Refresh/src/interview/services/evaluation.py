"""
Session lifecycle after the interview: finalise → evaluate rounds → report.

Session state, in `meta.json`, survives a reload:

    active → finalising → evaluation_queued → evaluating → complete
                                                         ↘ failed (retryable)

Stage 16 makes the job **per round**. Each round of a session is evaluated as
its own unit with its own state, recorded under `meta.evaluation.rounds`:

    queued → running → complete
                     ↘ failed (category + recovery; retry runs only this round)
    not_assessed      (no answers / too little to assess: no model call made)

and each completed round's validated result is written to
`evaluation/rounds/<round>.json` the moment it finishes, so the Results page
can show it while the other rounds are still running. The session report is
assembled only when every round has settled; until then it is visibly
incomplete. A retry re-runs failed rounds only; completed rounds are reused.

Guarantees:

  - **Idempotent.** Enqueue does nothing for a session already queued, running
    or complete, and reading state never starts work — a refresh, a second
    tab or a reconnect cannot cause a second provider call.
  - **Cached.** A completed round is keyed by candidate, session, transcript
    hash, claims hash, rubric, prompt version and evaluator model
    configuration. Same key → the stored result is reused with no model call.
    Files live under the candidate's own directory, so nothing crosses
    candidates.
  - **Bounded.** Each round has a deadline (`EVAL_ROUND_DEADLINE_S`) and the job
    one more (`EVAL_JOB_DEADLINE_S`); a round can be run at most
    `EVAL_MAX_ROUND_RUNS` times. Retries live in one place (the model client).
  - **Honest after a restart.** A round found queued/running with no task
    behind it is marked failed with category `interrupted`; completed rounds
    are kept. Nothing spins forever.
  - **Deletion-safe.** Every write checks the candidate still exists, and
    `cancel_candidate` cancels and awaits their tasks before their data is
    removed, so a background job cannot recreate deleted data.
  - **Measured.** Interview end, finalisation, queue delay, limiter wait,
    provider request time, repairs, each round's completion and report
    persistence are recorded (durations only; no prompt, no key).

A fixture report is never substituted for a failed one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from interview.candidates import CandidateRegistry, read_json, write_json
from interview.evaluation.role_eval import (
    PROMPT_VERSION,
    ClaimInput,
    EvaluationPassFailed,
    EvaluatorModel,
    PassResult,
    RoundJob,
    SessionInputs,
    assemble_report,
    plan_rounds,
    run_round_job,
)
from interview.evaluation.scorecard import render_scorecard
from interview.roadmap.reports import ReportStore

log = logging.getLogger(__name__)

ACTIVE_STATES = ("evaluation_queued", "evaluating")
# Kept for callers that import it; the live value comes from settings.
EVALUATION_TIMEOUT_S = 240.0
ROUND_STATES = ("queued", "running", "complete", "failed", "not_assessed")


def _now() -> str:
    from interview.candidates import utc_now

    return utc_now()


def set_state(session_dir: Path, state: str, **extra) -> dict:
    meta = read_json(session_dir / "meta.json", {}) or {}
    history = list(meta.get("state_history", []))
    history.append({"state": state, "at": _now()})
    meta.update(extra)
    meta["state"] = state
    meta["state_history"] = history[-20:]
    write_json(session_dir / "meta.json", meta)
    return meta


class NoEvaluator(RuntimeError):
    """No evaluation provider is configured for this environment."""


class CandidateGone(RuntimeError):
    """The candidate deleted their data while work was in flight."""


def default_evaluator(*, use_mock_llm: bool) -> EvaluatorModel:
    """
    The real evaluator when Groq is configured; the labelled mock only where
    mocks are allowed (dev/test). Otherwise the job fails with a clear error
    instead of producing a report.
    """
    from interview.config import get_settings
    from interview.evaluation.role_eval import GroqEvaluator, MockEvaluator

    settings = get_settings()
    if not use_mock_llm and settings.has_groq:
        from interview.llm.client import GroqModelClient

        return GroqEvaluator(GroqModelClient(), max_tokens=settings.eval_max_tokens)
    if settings.mocks_allowed:
        return MockEvaluator()
    raise NoEvaluator(
        "No evaluation model is configured (GROQ_API_KEY is not set and mock "
        "providers are off), so this session could not be evaluated."
    )


def build_services(root: Path, *, use_mock_llm: Callable[[], bool]):
    """Registry, report store and evaluation service rooted at `root`."""
    registry = CandidateRegistry(Path(root) / "candidates")
    store = ReportStore(Path(root) / "reports.sqlite")
    service = EvaluationService(
        registry, store, lambda: default_evaluator(use_mock_llm=use_mock_llm())
    )
    return registry, store, service


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _budget():
    from interview.config import get_settings

    s = get_settings()
    return s.eval_max_repairs, s.eval_round_deadline_s, s.eval_job_deadline_s, s.eval_max_round_runs


class EvaluationService:
    def __init__(
        self,
        registry: CandidateRegistry,
        store: ReportStore,
        evaluator_factory: Callable[[], EvaluatorModel],
        *,
        timeout_s: float | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.evaluator_factory = evaluator_factory
        # Overrides EVAL_JOB_DEADLINE_S when given (tests).
        self.timeout_s = timeout_s
        self._tasks: dict[str, asyncio.Task] = {}
        self._owners: dict[str, str] = {}
        # Observers notified when a round or job settles (benchmarks, tests).
        self.on_event: Callable[[str, str, dict], None] | None = None

    # ------------------------------------------------------------------
    # Safety
    # ------------------------------------------------------------------

    def _alive(self, candidate_id: str) -> bool:
        try:
            return (self.registry.candidate_dir(candidate_id) / "identity.json").is_file()
        except Exception:  # noqa: BLE001
            return False

    def _write_meta(self, candidate_id: str, directory: Path, state: str | None = None, **extra) -> dict:
        """Read-modify-write meta.json, refusing to recreate a deleted candidate."""
        if not self._alive(candidate_id) or not directory.is_dir():
            raise CandidateGone(candidate_id)
        if state is not None:
            return set_state(directory, state, **extra)
        meta = read_json(directory / "meta.json", {}) or {}
        meta.update(extra)
        write_json(directory / "meta.json", meta)
        return meta

    def _write(self, candidate_id: str, path: Path, payload: Any) -> None:
        if not self._alive(candidate_id):
            raise CandidateGone(candidate_id)
        write_json(path, payload)

    def _track(self, key: str, candidate_id: str, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks[key] = task
        self._owners[key] = candidate_id
        return task

    async def cancel_candidate(self, candidate_id: str) -> int:
        """Cancel and await every task working for this candidate."""
        keys = [k for k, owner in self._owners.items() if owner == candidate_id]
        tasks = [self._tasks[k] for k in keys if k in self._tasks and not self._tasks[k].done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for key in keys:
            self._tasks.pop(key, None)
            self._owners.pop(key, None)
        return len(tasks)

    def _running(self, key: str) -> bool:
        task = self._tasks.get(key)
        return task is not None and not task.done()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, candidate_id: str, session_id: str) -> dict:
        directory = self.registry.existing_session(candidate_id, session_id)
        meta = read_json(directory / "meta.json", {}) or {}
        state = meta.get("state")
        if state == "complete":
            return meta
        if state in ACTIVE_STATES and self._running(session_id):
            return meta
        if state == "failed":
            # Only an explicit retry restarts a failed job.
            return meta
        evaluation = dict(meta.get("evaluation") or {})
        timings = dict(evaluation.get("timings") or {})
        timings.setdefault("queued_at", _now())
        timings.setdefault("queued_ts", time.time())
        evaluation["timings"] = timings
        meta = self._write_meta(
            candidate_id, directory, "evaluation_queued", evaluation=evaluation
        )
        self._track(session_id, candidate_id, self._run(candidate_id, session_id, only=None))
        return meta

    def retry(self, candidate_id: str, session_id: str, rounds: list[str] | None = None) -> dict:
        """Re-run failed rounds only. Completed rounds are kept and reused."""
        directory = self.registry.existing_session(candidate_id, session_id)
        status = self.status(candidate_id, session_id)
        if status["state"] != "failed" or self._running(session_id):
            return status
        failed = [
            r["round"]
            for r in (status.get("evaluation") or {}).get("rounds", [])
            if r.get("state") == "failed"
        ]
        targets = [r for r in failed if rounds is None or r in rounds]
        if not failed and not (status.get("evaluation") or {}).get("rounds"):
            targets = []  # job failed before rounds were planned: run everything
        _, _, _, max_runs = _budget()
        evaluation = dict(status.get("evaluation") or {})
        exhausted = [
            r["round"]
            for r in evaluation.get("rounds", [])
            if r["round"] in targets and int(r.get("runs") or 0) >= max_runs
        ]
        if exhausted:
            return {
                **status,
                "retry_refused": (
                    f"{', '.join(exhausted)} already ran {max_runs} times. "
                    "Retrying again is unlikely to help; the transcript is still available."
                ),
            }
        meta = self._write_meta(
            candidate_id, directory, "evaluation_queued", error=None, recovery=None
        )
        self._track(
            session_id,
            candidate_id,
            self._run(candidate_id, session_id, only=targets or None),
        )
        return meta

    def status(self, candidate_id: str, session_id: str) -> dict:
        """Read state. Never starts work. Reports interrupted work honestly."""
        directory = self.registry.existing_session(candidate_id, session_id)
        meta = read_json(directory / "meta.json", {}) or {}
        if meta.get("state") in ACTIVE_STATES and not self._running(session_id):
            evaluation = dict(meta.get("evaluation") or {})
            rounds = []
            for item in evaluation.get("rounds", []):
                item = dict(item)
                if item.get("state") in ("queued", "running"):
                    item.update(
                        state="failed",
                        category="interrupted",
                        error="Evaluation of this round was interrupted (the server restarted).",
                        recovery="Retry it. Rounds that finished are kept.",
                    )
                rounds.append(item)
            evaluation["rounds"] = rounds
            try:
                meta = self._write_meta(
                    candidate_id,
                    directory,
                    "failed",
                    evaluation=evaluation,
                    error="Evaluation was interrupted (the server restarted).",
                    recovery="Retry the rounds that did not finish.",
                )
            except CandidateGone:
                pass
        return meta

    def job_view(self, candidate_id: str, session_id: str) -> dict:
        """Round-by-round progress with any completed results, for the UI."""
        directory = self.registry.existing_session(candidate_id, session_id)
        meta = self.status(candidate_id, session_id)
        evaluation = meta.get("evaluation") or {}
        timings = evaluation.get("timings") or {}
        rounds_out = []
        for item in evaluation.get("rounds", []):
            entry = {k: v for k, v in item.items() if k not in ("cache_key",)}
            if item.get("state") in ("complete", "not_assessed"):
                stored = read_json(directory / "evaluation" / "rounds" / f"{item['round']}.json", None)
                if stored:
                    entry["result"] = stored["result"]["round_result"]
            rounds_out.append(entry)
        ended = timings.get("interview_ended_ts") or timings.get("queued_ts")
        settled_at = timings.get("report_persisted_ts")
        elapsed = None
        if ended:
            elapsed = round((settled_at or time.time()) - float(ended), 1)
        return {
            "session_id": session_id,
            "state": meta.get("state"),
            "transcript_available": (directory / "transcript.json").is_file(),
            "report_ready": meta.get("state") == "complete"
            and (directory / "evaluation" / "report.json").is_file(),
            "rounds": rounds_out,
            "rounds_total": len(rounds_out),
            "rounds_settled": sum(
                1 for r in rounds_out if r.get("state") in ("complete", "not_assessed", "failed")
            ),
            "elapsed_s": elapsed,
            "timings": {k: v for k, v in timings.items() if not k.endswith("_ts")},
            "error": meta.get("error"),
            "recovery": meta.get("recovery"),
            "evaluator": evaluation.get("evaluator"),
        }

    async def wait(self, session_id: str) -> None:
        """Tests and shutdown: wait for a job to finish."""
        task = self._tasks.get(session_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    # ------------------------------------------------------------------
    # The job
    # ------------------------------------------------------------------

    async def _run(self, candidate_id: str, session_id: str, *, only: list[str] | None) -> None:
        directory = self.registry.session_dir(candidate_id, session_id)
        try:
            job_deadline = self.timeout_s or _budget()[2]
            await asyncio.wait_for(
                self._run_rounds(candidate_id, session_id, directory, only=only),
                timeout=job_deadline,
            )
        except (CandidateGone, asyncio.CancelledError):
            return
        except asyncio.TimeoutError:
            self._fail_open_rounds(
                candidate_id,
                directory,
                category="deadline",
                error=f"Evaluation took longer than {int(self.timeout_s or _budget()[2])} s.",
            )
        except NoEvaluator as exc:
            self._safe_state(
                candidate_id,
                directory,
                "failed",
                error=str(exc),
                recovery="Ask the operator to configure GROQ_API_KEY, then retry.",
            )
        except Exception as exc:  # noqa: BLE001 — reported as a state, never hidden
            log.exception("evaluation failed for %s", session_id)
            self._fail_open_rounds(
                candidate_id, directory, category="unknown", error=str(exc)[:400] or exc.__class__.__name__
            )

    def _safe_state(self, candidate_id: str, directory: Path, state: str, **extra) -> None:
        try:
            self._write_meta(candidate_id, directory, state, **extra)
        except CandidateGone:
            pass

    def _fail_open_rounds(self, candidate_id: str, directory: Path, *, category: str, error: str) -> None:
        meta = read_json(directory / "meta.json", {}) or {}
        evaluation = dict(meta.get("evaluation") or {})
        rounds = []
        for item in evaluation.get("rounds", []):
            item = dict(item)
            if item.get("state") in ("queued", "running"):
                item.update(state="failed", category=category, error=error,
                            recovery="Retry this round. Completed rounds are kept.")
            rounds.append(item)
        evaluation["rounds"] = rounds
        self._safe_state(
            candidate_id, directory, "failed",
            evaluation=evaluation, error=error, recovery="Retry the rounds that did not finish.",
        )

    def inputs_for(self, candidate_id: str, directory: Path, meta: dict) -> SessionInputs:
        transcript = read_json(directory / "transcript.json", None)
        if transcript is None:
            raise RuntimeError("The session transcript was not saved, so it cannot be evaluated.")
        claims: list[ClaimInput] = []
        coverage_note = evidence_note = ""
        intake_id = meta.get("intake_id")
        if intake_id:
            from interview.services.intake import accepted_claims

            intake_dir = self.registry.intake_dir(candidate_id, intake_id)
            claims = [
                ClaimInput(
                    id=str(item["id"]),
                    text=str(item["text"]),
                    competency=str(item.get("competency", "")),
                    source=str(item.get("source_path") or item.get("source") or ""),
                    evidence_kind=str(item.get("evidence_kind") or "candidate_assertion"),
                )
                for item in accepted_claims(intake_dir)
            ]
            coverage_note = str(meta.get("coverage_note", ""))
            evidence_note = str(meta.get("evidence_note", ""))
        return SessionInputs(
            session_id=meta.get("session_id") or directory.name,
            intake_id=intake_id,
            config=dict(meta.get("config") or {}),
            lane=str(meta.get("lane") or "voice"),
            ended_reason=str(meta.get("ended_reason") or ""),
            rounds_meta=list(meta.get("rounds") or []),
            transcript=transcript if isinstance(transcript, list) else [],
            claims=claims,
            personalised=bool(meta.get("personalised")),
            coverage_note=coverage_note,
            evidence_note=evidence_note,
            fallback_pack_id=meta.get("pack_id"),
            session_started_at=str(meta.get("created_at") or ""),
            session_kind=str(meta.get("kind") or "interview"),
            practice=meta.get("practice"),
        )

    # Backwards-compatible name.
    _inputs = inputs_for

    def cache_key(
        self, candidate_id: str, session_id: str, inputs: SessionInputs, job: RoundJob, evaluator_info: dict
    ) -> str:
        return _sha(
            {
                "candidate": candidate_id,
                "session": session_id,
                "transcript": _sha(inputs.transcript),
                "claims": _sha([c.__dict__ for c in inputs.claims]),
                "round": job.round_id,
                "rubric_pack": job.pack.pack_id,
                "rubric": job.pack.rubric.version if job.pack.rubric else "legacy",
                "prompt": PROMPT_VERSION,
                "evaluator": evaluator_info,
            }
        )

    async def _run_rounds(
        self, candidate_id: str, session_id: str, directory: Path, *, only: list[str] | None
    ) -> None:
        max_repairs, round_deadline, _, _ = _budget()
        meta = read_json(directory / "meta.json", {}) or {}
        # Count the run before anything can fail, so a run that could not even
        # build an evaluator is still visible as an attempt.
        meta = self._write_meta(
            candidate_id,
            directory,
            "evaluating",
            evaluation_attempts=int(meta.get("evaluation_attempts", 0)) + 1,
        )
        inputs = self.inputs_for(candidate_id, directory, meta)
        jobs, planning_limitations = plan_rounds(inputs)
        evaluator = self.evaluator_factory()
        evaluator_info = {
            "provider": evaluator.provider,
            "model": evaluator.model,
            "fallback_model": getattr(evaluator, "fallback_model", None),
        }

        evaluation = dict(meta.get("evaluation") or {})
        timings = dict(evaluation.get("timings") or {})
        previous = {r["round"]: r for r in evaluation.get("rounds", [])}
        rounds_dir = directory / "evaluation" / "rounds"
        started_ts = time.time()
        timings.setdefault("started_at", _now())
        timings["last_run_started_ts"] = started_ts

        states: dict[str, dict] = {}
        to_run: list[RoundJob] = []
        results: dict[str, PassResult] = {}
        for job in jobs:
            key = self.cache_key(candidate_id, session_id, inputs, job, evaluator_info)
            prior = dict(previous.get(job.round_id) or {})
            stored = read_json(rounds_dir / f"{job.round_id}.json", None)
            reusable = stored is not None and stored.get("cache_key") == key
            if reusable and (only is None or job.round_id not in only):
                results[job.round_id] = PassResult.from_dict(stored["result"])
                states[job.round_id] = {
                    **prior,
                    "round": job.round_id,
                    "label": job.label,
                    "state": prior.get("state") if prior.get("state") in ("complete", "not_assessed") else (
                        "complete" if job.needs_model else "not_assessed"
                    ),
                    "cache_hit": True,
                    "cache_key": key,
                }
                continue
            if only is not None and job.round_id not in only:
                # Not asked to re-run. A completed round whose stored result
                # came from a different configuration is left as it was rather
                # than silently redone; a failed one stays failed.
                if prior.get("state") in ("complete", "not_assessed") and stored is not None:
                    results[job.round_id] = PassResult.from_dict(stored["result"])
                    states[job.round_id] = {**prior, "cache_key": stored.get("cache_key")}
                    continue
                if prior.get("state") == "failed":
                    states[job.round_id] = prior
                    continue
            states[job.round_id] = {
                "round": job.round_id,
                "label": job.label,
                "state": "queued",
                "runs": int(prior.get("runs") or 0),
                "queued_ts": time.time(),
                "queued_at": _now(),
                "cache_key": key,
                "cache_hit": False,
            }
            to_run.append(job)

        evaluation.update(
            evaluator=evaluator_info,
            transcript_sha=_sha(inputs.transcript),
            prompt_version=PROMPT_VERSION,
            rounds=[states[job.round_id] for job in jobs],
            timings=timings,
        )
        self._write_meta(candidate_id, directory, None, evaluation=evaluation)

        def persist_round_state(round_id: str, **update) -> None:
            states[round_id] = {**states[round_id], **update}
            current = read_json(directory / "meta.json", {}) or {}
            ev = dict(current.get("evaluation") or {})
            ev["rounds"] = [states[j.round_id] for j in jobs]
            if update.get("state") in ("complete", "not_assessed"):
                tm = dict(ev.get("timings") or {})
                if "first_result_ts" not in tm:
                    tm["first_result_ts"] = time.time()
                    tm["first_result_at"] = _now()
                    ended = tm.get("interview_ended_ts") or tm.get("queued_ts")
                    if ended:
                        tm["first_result_s"] = round(tm["first_result_ts"] - float(ended), 2)
                ev["timings"] = tm
            self._write_meta(candidate_id, directory, None, evaluation=ev)
            if self.on_event:
                self.on_event(session_id, round_id, states[round_id])

        async def run_one(job: RoundJob) -> None:
            begun = time.time()
            queue_delay = begun - float(states[job.round_id].get("queued_ts") or begun)
            persist_round_state(
                job.round_id,
                state="running",
                started_at=_now(),
                started_ts=begun,
                queue_delay_s=round(queue_delay, 3),
                runs=int(states[job.round_id].get("runs") or 0) + 1,
            )
            try:
                result = await run_round_job(
                    job,
                    evaluator,
                    inputs.claims,
                    max_repairs=max_repairs,
                    deadline_s=round_deadline,
                )
            except EvaluationPassFailed as exc:
                last = exc.calls[-1] if exc.calls else {}
                persist_round_state(
                    job.round_id,
                    state="failed",
                    category=exc.category,
                    error=str(exc)[:400],
                    recovery=exc.recovery,
                    completed_at=_now(),
                    elapsed_s=round(time.time() - begun, 3),
                    replies=exc.attempts,
                    provider_attempts=sum(int(c.get("attempts") or 0) for c in exc.calls),
                    limiter_wait_s=round(sum(float(c.get("limiter_wait_s") or 0) for c in exc.calls), 3),
                    model_used=last.get("model_used"),
                    fallback_used=any(c.get("fallback_used") for c in exc.calls),
                )
                return
            payload = {
                "cache_key": states[job.round_id]["cache_key"],
                "completed_at": _now(),
                "result": result.to_dict(),
            }
            self._write(candidate_id, rounds_dir / f"{job.round_id}.json", payload)
            results[job.round_id] = result
            em = result.round_result.evaluation_meta
            persist_round_state(
                job.round_id,
                state="complete" if em.get("model_call", True) else "not_assessed",
                status=result.round_result.status,
                category=None,
                error=None,
                recovery=None,
                completed_at=_now(),
                elapsed_s=round(time.time() - begun, 3),
                replies=em.get("replies", 0),
                repairs=em.get("repairs", 0),
                provider_attempts=em.get("provider_attempts", 0),
                limiter_wait_s=em.get("limiter_wait_s", 0.0),
                request_s=em.get("request_s", 0.0),
                model_used=em.get("model_used"),
                fallback_used=bool(em.get("fallback_used")),
                usage=em.get("usage", {}),
            )

        # Independent rounds run concurrently; one failing does not cancel the
        # others, and each persists as soon as it settles.
        await asyncio.gather(*(run_one(job) for job in to_run))

        failed = [j.round_id for j in jobs if states[j.round_id].get("state") == "failed"]
        if failed:
            labels = ", ".join(states[r]["label"] for r in failed)
            self._write_meta(
                candidate_id,
                directory,
                "failed",
                error=f"Feedback for {labels} could not be produced.",
                recovery="Retry the failed round(s). Rounds that finished are kept.",
            )
            if self.on_event:
                self.on_event(session_id, "*", {"state": "failed"})
            return

        report = assemble_report(
            inputs,
            [results[job.round_id] for job in jobs],
            evaluator_info=evaluator_info,
            planning_limitations=planning_limitations,
            elapsed_s=time.time() - started_ts,
        )
        payload = report.model_dump(mode="json")
        out = directory / "evaluation"
        self._write(candidate_id, out / "report.json", payload)
        if not self._alive(candidate_id):
            raise CandidateGone(candidate_id)
        (out / "scorecard.html").write_text(render_scorecard(payload), encoding="utf-8")
        self.store.record(candidate_id, payload)
        current = read_json(directory / "meta.json", {}) or {}
        ev = dict(current.get("evaluation") or {})
        tm = dict(ev.get("timings") or {})
        tm["report_persisted_ts"] = time.time()
        tm["report_persisted_at"] = _now()
        ended = tm.get("interview_ended_ts") or tm.get("queued_ts")
        if ended:
            tm["report_s"] = round(tm["report_persisted_ts"] - float(ended), 2)
        ev["timings"] = tm
        self._write_meta(
            candidate_id,
            directory,
            "complete",
            evaluation=ev,
            error=None,
            recovery=None,
            overall_score=payload["overall"]["score"],
            evaluator=payload["evaluator"],
            completed_at=_now(),
        )
        if self.on_event:
            self.on_event(session_id, "*", {"state": "complete"})


__all__ = [
    "CandidateGone",
    "EvaluationService",
    "NoEvaluator",
    "ROUND_STATES",
    "build_services",
    "default_evaluator",
    "set_state",
]
