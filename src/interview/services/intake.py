"""
Intake as a service — what the browser's setup form actually runs.

`tools/intake.py` stays as the offline CLI. This module is the same band-1
pipeline driven from the API: it takes validated uploads and an
`InterviewConfig`, runs resume parsing, optional bounded repository indexing,
claim building and fit/gap, writes every artifact under the candidate's own
intake directory, and reports progress through `status.json` so the browser can
poll instead of holding a long request open.

States, in order: queued → reading_documents → indexing_repository →
building_claims → preparing_guidance → ready, or failed at any step with a
message and a recovery the candidate can act on.

A repository is optional evidence. If it cannot be cloned or read, intake
finishes without it and says so; a missing repository is never a reason to
fail the candidate's intake or to infer anything about them.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from interview.candidates import read_json, write_json
from interview.intake.claims import build_claims
from interview.intake.documents import ExtractedDocument
from interview.intake.fit import fit_check
from interview.intake.repo_indexer import (
    FsTools,
    RepoIndexer,
    TraceLog,
    shallow_clone,
)
from interview.intake.resume_parser import parse_resume
from interview.intake.sanitize import sanitize_text
from interview.intake.schema import ClaimsFile, FitGap, RepoSummary
from interview.packs.model import load_pack
from interview.session.interview_config import InterviewConfig

log = logging.getLogger(__name__)

INTAKE_TIMEOUT_S = 120.0
STAGES = (
    "queued",
    "reading_documents",
    "indexing_repository",
    "building_claims",
    "preparing_guidance",
    "ready",
)


class IntakeFailed(RuntimeError):
    def __init__(self, message: str, *, recovery: str, stage: str) -> None:
        super().__init__(message)
        self.recovery = recovery
        self.stage = stage


@dataclass
class IntakeInputs:
    config: InterviewConfig
    resume: ExtractedDocument | None = None
    work_sample: ExtractedDocument | None = None


def _now() -> str:
    from interview.candidates import utc_now

    return utc_now()


def write_status(directory: Path, state: str, **extra) -> dict:
    current = read_json(directory / "status.json", {}) or {}
    payload = {
        **current,
        "state": state,
        "stage_index": STAGES.index(state) if state in STAGES else -1,
        "stage_count": len(STAGES) - 1,
        "updated_at": _now(),
        **extra,
    }
    write_json(directory / "status.json", payload)
    return payload


def read_status(directory: Path) -> dict:
    return read_json(directory / "status.json", {"state": "unknown"}) or {"state": "unknown"}


def _excerpt(text: str, limit: int = 600) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def _line_with(text: str, term: str) -> str:
    folded = term.casefold()
    for line in text.splitlines():
        if folded in line.casefold():
            return _excerpt(line, 240)
    return ""


def _skill_terms(items: list[str]) -> list[str]:
    """JD 'skills' that look like terms rather than prose fragments."""
    return [item for item in items if 0 < len(item.split()) <= 4]


def preparation_guidance(
    config: InterviewConfig,
    claims: ClaimsFile,
    fit: FitGap,
    *,
    jd_text: str,
    company_text: str,
) -> list[dict]:
    """
    Initial practice guidance, each item with the evidence it came from.

    Deterministic on purpose: every item traces to a claim the system read, a
    term in the supplied job description, or the selected pack's own spine.
    Company preparation is labelled as resting on supplied context only.
    """
    items: list[dict] = []

    for claim in claims.claims[:4]:
        repo_backed = claim.evidence_kind == "repository"
        items.append(
            {
                "id": f"claim-{claim.id}",
                "kind": "defend_claim",
                "title": f'Be ready to go four levels deep on: "{claim.text}"',
                "detail": (
                    "The repository shows this material exists; expect to be asked "
                    "which part you personally wrote and why it is built that way."
                    if repo_backed
                    else "This is your own statement. Expect follow-ups on what you "
                    "personally did, what you rejected, and how you knew it worked."
                ),
                "action": (
                    "Prepare a two-minute account: the situation, your decision, the "
                    "alternative you rejected, and how you verified the outcome."
                ),
                "evidence": [
                    {"source": claim.source_path or claim.source, "excerpt": claim.quote}
                ],
            }
        )

    missing = _skill_terms(fit.missing)[:3]
    for term in missing:
        items.append(
            {
                "id": f"gap-{term.casefold().replace(' ', '-')}",
                "kind": "jd_gap",
                "title": f"The job description mentions {term}; your background does not.",
                "detail": (
                    "This is a gap in what you supplied, not a judgement of your "
                    "ability. An interviewer may ask about it."
                ),
                "action": (
                    f"Prepare an honest answer about your exposure to {term}: what "
                    "you have done that is adjacent, and how you would ramp up."
                ),
                "evidence": [
                    {"source": "job_description", "excerpt": _line_with(jd_text, term) or term}
                ],
            }
        )

    for round_, pack_id in config.pack_ids().items():
        try:
            pack = load_pack(pack_id)
        except Exception:  # noqa: BLE001 — guidance never blocks intake
            continue
        first = pack.spine[0]
        items.append(
            {
                "id": f"spine-{pack_id}",
                "kind": "round_preview",
                "title": f"{pack.title}: it opens with \"{first.text}\"",
                "detail": (
                    f"The {round_.value.replace('_', ' ')} round asks "
                    f"{len(pack.spine)} core questions in a fixed order, then "
                    "follows up on your answers."
                ),
                "action": "Rehearse an answer to the opening question out loud, once.",
                "evidence": [{"source": f"pack:{pack_id}", "excerpt": first.text}],
            }
        )

    if company_text.strip():
        items.append(
            {
                "id": "company-context",
                "kind": "company",
                "title": "Prepare 'why this company' from the context you supplied",
                "detail": (
                    "Based only on the company context you entered. No independent "
                    "company research was performed, so check it is current."
                ),
                "action": "Connect one thing from that context to one thing you have done.",
                "evidence": [{"source": "company_context", "excerpt": _excerpt(company_text, 240)}],
            }
        )
    else:
        items.append(
            {
                "id": "company-none",
                "kind": "company",
                "title": "No company-specific preparation",
                "detail": (
                    "You did not supply company context and this build performs no "
                    "company research, so no company-specific guidance is shown."
                ),
                "action": "If you are targeting a company, add a short description of it and re-run intake.",
                "evidence": [],
            }
        )

    if not claims.claims:
        items.insert(
            0,
            {
                "id": "no-claims",
                "kind": "context",
                "title": "We found no specific statements to follow up on",
                "detail": (
                    "Your background did not contain sentences describing things you "
                    "did, so follow-ups will anchor on your spoken answers instead."
                ),
                "action": "Add two or three sentences like 'I led…' or 'I built…' and re-run intake.",
                "evidence": [],
            },
        )
    return items


def _index_repository(
    url: str,
    directory: Path,
    clone: Callable[[str, Path], None],
    max_steps: int,
) -> tuple[RepoSummary, list, list, list[str]]:
    """Clone, index read-only, then delete the clone. Returns summary + evidence."""
    clone_dir = directory / "repo"
    trace = TraceLog(directory / "exploration.jsonl")
    warnings: list[str] = []
    try:
        clone(url, clone_dir)
    except Exception as exc:  # noqa: BLE001 — optional evidence
        shutil.rmtree(clone_dir, ignore_errors=True)
        warnings.append(
            f"The repository could not be read ({str(exc)[:160]}). Intake continued "
            "without it; this says nothing about you."
        )
        trace.write(
            {
                "type": "agent_step",
                "step_index": 0,
                "phase": "observe",
                "summary": "clone failed; resume-only fallback",
            }
        )
        return RepoSummary(url=url, fallback="resume-only"), [], [], warnings
    try:
        indexer = RepoIndexer(
            FsTools(clone_dir),
            max_steps=max_steps,
            trace=trace,
            summary=RepoSummary(url=url, path="repo", fallback="cloned"),
        )
        run = indexer.run()
        return run.summary, run.evidence, indexer.manifests, warnings
    finally:
        # Excerpts are kept in sources.json; the clone itself is not retained.
        shutil.rmtree(clone_dir, ignore_errors=True)


def run_intake_sync(
    directory: Path,
    inputs: IntakeInputs,
    *,
    clone: Callable[[str, Path], None] | None = None,
    max_steps: int = 10,
) -> dict:
    """Blocking pipeline; run it in a worker thread."""
    clone = clone or shallow_clone
    config = inputs.config
    write_status(directory, "reading_documents")
    parts = []
    if inputs.resume is not None:
        parts.append(inputs.resume.text)
    if config.background_text.strip():
        parts.append(sanitize_text(config.background_text))
    background = "\n\n".join(part for part in parts if part.strip())
    if len(background.strip()) < 40:
        raise IntakeFailed(
            "There is not enough background text to prepare an interview.",
            recovery="Upload a resume or paste a few sentences about your experience.",
            stage="reading_documents",
        )
    (directory / "background.txt").write_text(background, encoding="utf-8")
    profile = parse_resume(background)
    write_json(directory / "resume_profile.json", profile.model_dump())

    warnings: list[str] = []
    evidence: list = []
    manifests: list = []
    if config.repo_url:
        write_status(directory, "indexing_repository")
        summary, evidence, manifests, repo_warnings = _index_repository(
            config.repo_url, directory, clone, max_steps
        )
        warnings.extend(repo_warnings)
    else:
        summary = RepoSummary(fallback="resume-only")
    write_json(directory / "repo_summary.json", summary.model_dump())

    write_status(directory, "building_claims")
    sample_text = inputs.work_sample.text if inputs.work_sample else ""
    claims = build_claims(profile, background, evidence, work_sample_text=sample_text)
    write_json(directory / "claims.json", claims.model_dump())

    jd_text = sanitize_text(config.job_description)
    company_text = sanitize_text(config.company_context)
    fit = fit_check(profile, jd_text) if jd_text.strip() else FitGap(
        summary="No job description was supplied, so no fit/gap was computed."
    )
    write_json(directory / "fit_gap.json", fit.model_dump())

    write_status(directory, "preparing_guidance")
    guidance = preparation_guidance(
        config, claims, fit, jd_text=jd_text, company_text=company_text
    )
    write_json(
        directory / "prep.json",
        {"generated_at": _now(), "source": "intake", "items": guidance},
    )
    sources = {
        "resume": (
            {
                "filename": inputs.resume.filename,
                "kind": inputs.resume.kind,
                "chars": inputs.resume.char_count,
                "truncated": inputs.resume.truncated,
                "excerpt": _excerpt(inputs.resume.text),
            }
            if inputs.resume
            else None
        ),
        "background_text_chars": len(config.background_text.strip()),
        "job_description": _excerpt(jd_text) if jd_text.strip() else None,
        "company_context": _excerpt(company_text) if company_text.strip() else None,
        "work_sample": (
            {
                "filename": inputs.work_sample.filename,
                "kind": inputs.work_sample.kind,
                "chars": inputs.work_sample.char_count,
                "excerpt": _excerpt(inputs.work_sample.text),
            }
            if inputs.work_sample
            else None
        ),
        "repository": {
            **summary.model_dump(),
            "readme_excerpt": _excerpt(evidence[0].text) if evidence else None,
            "manifests": [
                {"path": item.path, "excerpt": _excerpt(item.text, 300)} for item in manifests
            ],
            "note": (
                "Repository content shows that material exists, not who wrote it."
                if config.repo_url
                else None
            ),
        },
    }
    write_json(directory / "sources.json", sources)
    return write_status(
        directory,
        "ready",
        warnings=warnings,
        claim_count=len(claims.claims),
        completed_at=_now(),
    )


async def run_intake_job(
    directory: Path,
    inputs: IntakeInputs,
    *,
    clone: Callable[[str, Path], None] | None = None,
    timeout_s: float = INTAKE_TIMEOUT_S,
) -> dict:
    """Run the pipeline off the event loop; record failure as a state, not a crash."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(run_intake_sync, directory, inputs, clone=clone),
            timeout=timeout_s,
        )
    except IntakeFailed as exc:
        return write_status(
            directory, "failed", error=str(exc), recovery=exc.recovery, failed_stage=exc.stage
        )
    except asyncio.TimeoutError:
        return write_status(
            directory,
            "failed",
            error=f"Preparing your interview took longer than {int(timeout_s)} s.",
            recovery="Try again without the repository URL, or with a smaller repository.",
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("intake failed")
        return write_status(
            directory,
            "failed",
            error=f"Intake failed unexpectedly: {str(exc)[:200]}",
            recovery="Try again. If it keeps failing, paste your background as text.",
        )


def load_intake(directory: Path) -> dict:
    """Everything the browser shows after intake, read back from disk."""
    status = read_status(directory)
    out = {"status": status}
    if status.get("state") != "ready":
        return out
    out["config"] = read_json(directory / "config.json", {})
    out["claims"] = (read_json(directory / "claims.json", {}) or {}).get("claims", [])
    out["fit_gap"] = read_json(directory / "fit_gap.json", {})
    out["prep"] = read_json(directory / "prep.json", {})
    out["sources"] = read_json(directory / "sources.json", {})
    out["coverage_note"] = InterviewConfig.model_validate(out["config"]).coverage_note()
    out["evidence_note"] = InterviewConfig.model_validate(out["config"]).evidence_note()
    return out


__all__ = [
    "INTAKE_TIMEOUT_S",
    "IntakeFailed",
    "IntakeInputs",
    "STAGES",
    "load_intake",
    "preparation_guidance",
    "read_status",
    "run_intake_job",
    "run_intake_sync",
    "write_status",
]
