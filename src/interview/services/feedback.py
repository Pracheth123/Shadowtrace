"""
"This feedback seems wrong" — disputes and labelled revised assessments.

A candidate can dispute any finding in a completed report. What that does:

  - The **original report is never modified**. Disputes live in
    `<session>/disputes.json`; revised assessments in
    `<session>/revisions/<id>.json`. Both are separate, append-only records.
  - A disputed finding is excluded from progress claims (history comparisons,
    recurring gaps, the next-practice plan and targeted-practice comparisons).
    If it is the *only* support for a dimension, that dimension is excluded from
    comparative improvement too, until the dispute is settled.
  - The candidate may add a correction and ask for a **revised assessment**: the
    same round is re-evaluated by the same evaluator with the correction given
    as delimited context. It is labelled as an automated re-check, and it does
    **not** settle the dispute: only the candidate can withdraw the dispute or
    accept the revision. An automated second opinion from the same kind of
    model is not independent proof either way.

Statuses: `open` → `reassessed` (a revision exists) → `withdrawn` (finding
stands) | `accepted_revision` (candidate prefers the revision).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from interview.candidates import new_id, read_json, write_json
from interview.evaluation.role_eval import (
    EvaluationPassFailed,
    plan_rounds,
    run_round_job,
)

log = logging.getLogger(__name__)

DISPUTE_STATUSES = ("open", "reassessed", "withdrawn", "accepted_revision")
# Statuses under which the original finding does not count toward progress.
EXCLUDING = ("open", "reassessed", "accepted_revision")
MAX_EXPLANATION_CHARS = 1500
REVISION_LABEL = "Revised assessment after your correction"
REVISION_NOTE = (
    "An automated re-check of this round by the same evaluator, with your "
    "correction given as context. It is not independent confirmation that the "
    "original finding was wrong — or right. Your dispute stays open until you "
    "withdraw it or accept this revision."
)

_LEVEL_ORDER = {"insufficient_evidence": 0, "developing": 1, "solid": 2, "strong": 3}


class FeedbackError(ValueError):
    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    from interview.candidates import utc_now

    return utc_now()


def find_finding(report: dict, finding_id: str) -> tuple[dict, dict] | None:
    """(round result, finding) for an id, or None."""
    for result in report.get("rounds", []):
        for finding in result.get("findings", []):
            if finding.get("finding_id") == finding_id:
                return result, finding
    return None


def read_disputes(session_dir: Path) -> dict[str, dict]:
    return (read_json(session_dir / "disputes.json", {}) or {}).get("disputes", {})


def _write_disputes(session_dir: Path, disputes: dict[str, dict]) -> None:
    write_json(session_dir / "disputes.json", {"schema": "disputes.v1", "disputes": disputes})


def create_dispute(session_dir: Path, report: dict, finding_id: str, explanation: str) -> dict:
    """Dispute a finding. Idempotent: a second call adds the explanation as an event."""
    from interview.intake.sanitize import sanitize_text

    located = find_finding(report, finding_id)
    if located is None:
        raise FeedbackError("That finding is not in this report.", status=404)
    result, finding = located
    text = sanitize_text(str(explanation or ""))[:MAX_EXPLANATION_CHARS].strip()
    disputes = read_disputes(session_dir)
    record = disputes.get(finding_id)
    if record is None:
        record = {
            "finding_id": finding_id,
            "round": result.get("round"),
            "round_label": result.get("label"),
            "dimension_id": finding.get("dimension_id"),
            "dimension_label": finding.get("dimension_label"),
            "turn_id": finding.get("turn_id"),
            "quote": finding.get("quote"),
            "original_explanation": finding.get("explanation"),
            "status": "open",
            "created_at": _now(),
            "explanation": text,
            "events": [{"at": _now(), "event": "disputed", "explanation": text}],
            "revisions": [],
        }
    else:
        if record["status"] in ("withdrawn",):
            record["status"] = "open"
            record["events"].append({"at": _now(), "event": "reopened"})
        if text:
            record["explanation"] = text
            record["events"].append({"at": _now(), "event": "explanation_added", "explanation": text})
    disputes[finding_id] = record
    _write_disputes(session_dir, disputes)
    return record


def set_dispute_status(session_dir: Path, finding_id: str, status: str) -> dict:
    if status not in ("withdrawn", "accepted_revision", "open"):
        raise FeedbackError(f"Unknown status {status!r}.", status=422)
    disputes = read_disputes(session_dir)
    record = disputes.get(finding_id)
    if record is None:
        raise FeedbackError("There is no dispute for that finding.", status=404)
    if status == "accepted_revision":
        complete = [
            rid
            for rid in record.get("revisions", [])
            if (read_json(session_dir / "revisions" / f"{rid}.json", {}) or {}).get("state") == "complete"
        ]
        if not complete:
            raise FeedbackError("There is no completed revised assessment to accept yet.")
    record["status"] = status
    record["events"].append({"at": _now(), "event": f"status:{status}"})
    disputes[finding_id] = record
    _write_disputes(session_dir, disputes)
    return record


def contested(report: dict, disputes: dict[str, dict]) -> dict[str, Any]:
    """
    What a set of disputes removes from progress claims.

    `findings`: finding ids not to count. `dimensions`: (round, dimension_id)
    pairs whose only support was a disputed finding — excluded from comparative
    improvement until resolved.
    """
    findings: set[str] = set()
    dimensions: set[tuple[str, str]] = set()
    for finding_id, record in disputes.items():
        if record.get("status") not in EXCLUDING:
            continue
        findings.add(finding_id)
        located = find_finding(report, finding_id)
        if located is None:
            continue
        result, finding = located
        disputed_turns = {
            d.get("turn_id")
            for d in disputes.values()
            if d.get("status") in EXCLUDING
            and d.get("round") == result.get("round")
            and d.get("dimension_id") == finding.get("dimension_id")
        }
        dim = next(
            (d for d in result.get("dimensions", []) if d.get("dimension_id") == finding.get("dimension_id")),
            None,
        )
        citations = {c.get("turn_id") for c in (dim or {}).get("citations", [])}
        other_support = citations - disputed_turns
        other_findings = [
            f
            for f in result.get("findings", [])
            if f.get("dimension_id") == finding.get("dimension_id")
            and f.get("finding_id") not in findings
            and f.get("finding_id") != finding_id
            and (disputes.get(f.get("finding_id"), {}).get("status") not in EXCLUDING)
        ]
        if not other_support and not other_findings:
            dimensions.add((str(result.get("round")), str(finding.get("dimension_id"))))
    return {"findings": findings, "dimensions": dimensions}


def overlay(report: dict, session_dir: Path) -> dict:
    """The stored report plus its disputes and revisions. Never rewrites the report."""
    disputes = read_disputes(session_dir)
    revisions = []
    rev_dir = session_dir / "revisions"
    if rev_dir.is_dir():
        for path in sorted(rev_dir.glob("*.json")):
            item = read_json(path, None)
            if item:
                revisions.append(item)
    excluded = contested(report, disputes)
    return {
        **report,
        "disputes": disputes,
        "revisions": revisions,
        "contested": {
            "findings": sorted(excluded["findings"]),
            "dimensions": [list(pair) for pair in sorted(excluded["dimensions"])],
        },
    }


class FeedbackService:
    """Revised assessments run as tracked background tasks of the evaluation service."""

    def __init__(self, evaluation) -> None:
        self.evaluation = evaluation

    def request_revision(self, candidate_id: str, session_id: str, finding_id: str) -> dict:
        registry = self.evaluation.registry
        directory = registry.existing_session(candidate_id, session_id)
        report = read_json(directory / "evaluation" / "report.json", None)
        if report is None:
            raise FeedbackError("The report is not ready.")
        disputes = read_disputes(directory)
        record = disputes.get(finding_id)
        if record is None:
            raise FeedbackError("Dispute the finding first, with a short explanation.", status=409)
        if len((record.get("explanation") or "").strip()) < 10:
            raise FeedbackError(
                "Add a short correction (what you meant, or what was mis-transcribed) "
                "before asking for a revised assessment.",
                status=422,
            )
        pending = [
            rid
            for rid in record.get("revisions", [])
            if (read_json(directory / "revisions" / f"{rid}.json", {}) or {}).get("state") in ("queued", "running")
        ]
        if pending:
            return read_json(directory / "revisions" / f"{pending[0]}.json", {})
        if len(record.get("revisions", [])) >= 2:
            raise FeedbackError("This finding has already been re-checked twice.")
        revision_id = new_id()
        revision = {
            "revision_id": revision_id,
            "finding_id": finding_id,
            "round": record["round"],
            "dimension_id": record["dimension_id"],
            "label": REVISION_LABEL,
            "note": REVISION_NOTE,
            "correction": record.get("explanation", ""),
            "state": "queued",
            "created_at": _now(),
        }
        write_json(directory / "revisions" / f"{revision_id}.json", revision)
        record.setdefault("revisions", []).append(revision_id)
        record["status"] = "reassessed" if record["status"] == "open" else record["status"]
        record["events"].append({"at": _now(), "event": "revision_requested", "revision_id": revision_id})
        disputes[finding_id] = record
        _write_disputes(directory, disputes)
        self.evaluation._track(
            f"rev:{session_id}:{revision_id}",
            candidate_id,
            self._run_revision(candidate_id, session_id, revision_id),
        )
        return revision

    async def _run_revision(self, candidate_id: str, session_id: str, revision_id: str) -> None:
        evaluation = self.evaluation
        directory = evaluation.registry.session_dir(candidate_id, session_id)
        path = directory / "revisions" / f"{revision_id}.json"

        def save(**update) -> dict:
            if not evaluation._alive(candidate_id) or not directory.is_dir():
                raise asyncio.CancelledError()
            current = read_json(path, {}) or {}
            current.update(update)
            write_json(path, current)
            return current

        try:
            revision = save(state="running", started_at=_now())
            from interview.config import get_settings

            settings = get_settings()
            meta = read_json(directory / "meta.json", {}) or {}
            report = read_json(directory / "evaluation" / "report.json", {}) or {}
            inputs = evaluation.inputs_for(candidate_id, directory, meta)
            jobs, _ = plan_rounds(inputs)
            job = next((j for j in jobs if j.round_id == revision["round"]), None)
            if job is None:
                save(state="failed", error="That round is not in this session.")
                return
            evaluator = evaluation.evaluator_factory()
            started = time.monotonic()
            result = await run_round_job(
                job,
                evaluator,
                inputs.claims,
                max_repairs=settings.eval_max_repairs,
                deadline_s=settings.eval_round_deadline_s,
                candidate_correction=revision.get("correction", ""),
            )
            original = next(
                (r for r in report.get("rounds", []) if r.get("round") == revision["round"]), {}
            )
            before = next(
                (d for d in original.get("dimensions", []) if d.get("dimension_id") == revision["dimension_id"]),
                {},
            )
            revised = result.round_result.model_dump(mode="json")
            after = next(
                (d for d in revised.get("dimensions", []) if d.get("dimension_id") == revision["dimension_id"]),
                {},
            )
            same_gap_found = any(
                f.get("dimension_id") == revision["dimension_id"]
                and f.get("polarity") == "gap"
                and f.get("turn_id") == read_disputes(directory).get(revision["finding_id"], {}).get("turn_id")
                for f in revised.get("findings", [])
            )
            save(
                state="complete",
                completed_at=_now(),
                elapsed_s=round(time.monotonic() - started, 2),
                evaluator={"provider": evaluator.provider, "model": evaluator.model},
                round_result=revised,
                dimension_before=before.get("level"),
                dimension_after=after.get("level"),
                gap_repeated=same_gap_found,
                summary=(
                    "The re-check raised the same gap on this answer."
                    if same_gap_found
                    else "The re-check did not raise this gap on this answer."
                ),
            )
        except asyncio.CancelledError:
            return
        except EvaluationPassFailed as exc:
            try:
                save(state="failed", error=str(exc)[:300], recovery=exc.recovery, category=exc.category)
            except asyncio.CancelledError:
                return
        except Exception as exc:  # noqa: BLE001
            log.exception("revision failed")
            try:
                save(state="failed", error=str(exc)[:300], recovery="Try again later.")
            except asyncio.CancelledError:
                return


def level_rank(level: str | None) -> int:
    return _LEVEL_ORDER.get(str(level or ""), 0)


__all__ = [
    "DISPUTE_STATUSES",
    "EXCLUDING",
    "FeedbackError",
    "FeedbackService",
    "REVISION_LABEL",
    "contested",
    "create_dispute",
    "find_finding",
    "level_rank",
    "overlay",
    "read_disputes",
    "set_dispute_status",
]
