"""
Session lifecycle after the interview: finalise → evaluate → store.

States, recorded in the session's `meta.json` so they survive a page reload:

    active → finalising → evaluation_queued → evaluating → complete
                                                         ↘ failed (retryable)

The composition root moves a session to `finalising` when the live session
closes, writes the transcript and event log (the live runtime has already
flushed them), then calls `enqueue`. Evaluation therefore starts only after
band 2 has finished (contract 6), and the browser polls `status` rather than
holding a long request open.

Idempotent: enqueueing a session that is already queued, running or complete
does nothing, so a repeated end request or a reconnect cannot start a second
job. A failed job is retried only on an explicit `retry`. A job found
"evaluating" with no task behind it (the process restarted) is reported as
failed-interrupted and can be retried; it is never silently left spinning.
A fixture report is never substituted for a failed one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from interview.candidates import CandidateRegistry, read_json, write_json
from interview.evaluation.role_eval import (
    ClaimInput,
    EvaluatorModel,
    SessionInputs,
    evaluate_session,
)
from interview.evaluation.scorecard import render_scorecard
from interview.roadmap.reports import ReportStore

log = logging.getLogger(__name__)

ACTIVE_STATES = ("evaluation_queued", "evaluating")
EVALUATION_TIMEOUT_S = 240.0


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


def default_evaluator(*, use_mock_llm: bool) -> EvaluatorModel:
    """
    The real evaluator when Groq is configured; the labelled mock only where
    mocks are allowed (dev/test). Production with no key fails the job with a
    clear error instead of producing a report.
    """
    from interview.config import get_settings
    from interview.evaluation.role_eval import GroqEvaluator, MockEvaluator

    settings = get_settings()
    if not use_mock_llm and settings.has_groq:
        from interview.llm.client import GroqModelClient

        return GroqEvaluator(GroqModelClient())
    if settings.allow_mock_providers and not settings.is_production:
        return MockEvaluator()
    raise NoEvaluator(
        "No evaluation model is configured (GROQ_API_KEY is not set), so this "
        "session could not be evaluated."
    )


def build_services(root: Path, *, use_mock_llm: Callable[[], bool]):
    """Registry, report store and evaluation service rooted at `root`."""
    registry = CandidateRegistry(Path(root) / "candidates")
    store = ReportStore(Path(root) / "reports.sqlite")
    service = EvaluationService(
        registry, store, lambda: default_evaluator(use_mock_llm=use_mock_llm())
    )
    return registry, store, service


class EvaluationService:
    def __init__(
        self,
        registry: CandidateRegistry,
        store: ReportStore,
        evaluator_factory: Callable[[], EvaluatorModel],
        *,
        timeout_s: float = EVALUATION_TIMEOUT_S,
    ) -> None:
        self.registry = registry
        self.store = store
        self.evaluator_factory = evaluator_factory
        self.timeout_s = timeout_s
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------

    def enqueue(self, candidate_id: str, session_id: str) -> dict:
        directory = self.registry.existing_session(candidate_id, session_id)
        meta = read_json(directory / "meta.json", {}) or {}
        state = meta.get("state")
        task = self._tasks.get(session_id)
        if state == "complete":
            return meta
        if state in ACTIVE_STATES and task is not None and not task.done():
            return meta
        if state == "failed":
            # Only an explicit retry restarts a failed job.
            return meta
        meta = set_state(directory, "evaluation_queued")
        self._tasks[session_id] = asyncio.create_task(
            self._run(candidate_id, session_id)
        )
        return meta

    def retry(self, candidate_id: str, session_id: str) -> dict:
        directory = self.registry.existing_session(candidate_id, session_id)
        status = self.status(candidate_id, session_id)
        if status["state"] != "failed":
            return status
        set_state(directory, "evaluation_queued", error=None, recovery=None)
        self._tasks[session_id] = asyncio.create_task(
            self._run(candidate_id, session_id)
        )
        return read_json(directory / "meta.json", {}) or {}

    def status(self, candidate_id: str, session_id: str) -> dict:
        directory = self.registry.existing_session(candidate_id, session_id)
        meta = read_json(directory / "meta.json", {}) or {}
        task = self._tasks.get(session_id)
        if meta.get("state") in ACTIVE_STATES and (task is None or task.done()):
            meta = set_state(
                directory,
                "failed",
                error="Evaluation was interrupted (the server restarted).",
                recovery="Retry the evaluation.",
            )
        return meta

    async def wait(self, session_id: str) -> None:
        """Tests and shutdown: wait for a job to finish."""
        task = self._tasks.get(session_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    # ------------------------------------------------------------------

    async def _run(self, candidate_id: str, session_id: str) -> None:
        directory = self.registry.session_dir(candidate_id, session_id)
        meta = read_json(directory / "meta.json", {}) or {}
        attempts = int(meta.get("evaluation_attempts", 0)) + 1
        set_state(directory, "evaluating", evaluation_attempts=attempts)
        try:
            inputs = self._inputs(candidate_id, directory, meta)
            evaluator = self.evaluator_factory()
            report = await asyncio.wait_for(
                evaluate_session(inputs, evaluator), timeout=self.timeout_s
            )
            payload = report.model_dump(mode="json")
            out = directory / "evaluation"
            write_json(out / "report.json", payload)
            (out / "scorecard.html").write_text(
                render_scorecard(payload), encoding="utf-8"
            )
            self.store.record(candidate_id, payload)
            set_state(
                directory,
                "complete",
                error=None,
                recovery=None,
                overall_score=payload["overall"]["score"],
                evaluator=payload["evaluator"],
                completed_at=_now(),
            )
        except asyncio.TimeoutError:
            set_state(
                directory,
                "failed",
                error=f"Evaluation took longer than {int(self.timeout_s)} s.",
                recovery="Retry the evaluation.",
            )
        except NoEvaluator as exc:
            set_state(directory, "failed", error=str(exc), recovery="Ask the operator to configure GROQ_API_KEY, then retry.")
        except Exception as exc:  # noqa: BLE001 — reported as a state, never hidden
            log.exception("evaluation failed for %s", session_id)
            set_state(
                directory,
                "failed",
                error=str(exc)[:400] or exc.__class__.__name__,
                recovery="Retry the evaluation.",
            )

    def _inputs(self, candidate_id: str, directory: Path, meta: dict) -> SessionInputs:
        transcript = read_json(directory / "transcript.json", None)
        if transcript is None:
            raise RuntimeError("The session transcript was not saved, so it cannot be evaluated.")
        claims: list[ClaimInput] = []
        coverage_note = evidence_note = ""
        intake_id = meta.get("intake_id")
        if intake_id:
            intake_dir = self.registry.intake_dir(candidate_id, intake_id)
            raw = (read_json(intake_dir / "claims.json", {}) or {}).get("claims", [])
            claims = [
                ClaimInput(
                    id=str(item["id"]),
                    text=str(item["text"]),
                    competency=str(item.get("competency", "")),
                    source=str(item.get("source_path") or item.get("source") or ""),
                    evidence_kind=str(item.get("evidence_kind") or "candidate_assertion"),
                )
                for item in raw
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
        )


__all__ = ["EvaluationService", "NoEvaluator", "build_services", "default_evaluator", "set_state"]
