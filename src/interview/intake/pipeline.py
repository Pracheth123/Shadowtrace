"""Run band-1 intake and write JSON files. Does not open the live session bus."""

from __future__ import annotations

import json
from pathlib import Path

from interview.intake.claims import build_claims
from interview.intake.fit import fit_check
from interview.intake.repo_indexer import FsTools, RepoIndexer, TraceLog, shallow_clone
from interview.intake.resume_parser import load_resume_text, parse_resume
from interview.intake.schema import IntakeRequest, RepoSummary


def run_intake(request: IntakeRequest) -> dict[str, str]:
    out = Path(request.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    resume_path = Path(request.resume_path)
    resume_text, truncated = load_resume_text(resume_path)
    profile = parse_resume(resume_text, truncated=truncated)

    evidence = []
    summary: RepoSummary
    trace_path = out / "exploration.jsonl"

    if request.repo_path:
        root = Path(request.repo_path)
        summary = RepoSummary(path=str(root), fallback="preindexed")
        run = RepoIndexer(
            FsTools(root),
            max_steps=request.max_steps,
            trace=TraceLog(trace_path),
            summary=summary,
        ).run()
        evidence = run.evidence
        summary = run.summary
    elif request.repo_url:
        clone_dir = out / "repo"
        shallow_clone(request.repo_url, clone_dir)
        summary = RepoSummary(url=request.repo_url, path=str(clone_dir), fallback="cloned")
        run = RepoIndexer(
            FsTools(clone_dir),
            max_steps=request.max_steps,
            trace=TraceLog(trace_path),
            summary=summary,
        ).run()
        evidence = run.evidence
        summary = run.summary
    else:
        summary = RepoSummary(fallback="resume-only")
        TraceLog(trace_path).write(
            {
                "type": "agent_step",
                "step_index": 0,
                "phase": "observe",
                "summary": "resume-only fallback; repo indexer skipped",
            }
        )

    claims = build_claims(profile, resume_text, evidence)
    jd_text = request.jd_text or ""
    if request.jd_path:
        jd_text = Path(request.jd_path).read_text(encoding="utf-8")
    fit = fit_check(profile, jd_text)

    paths = {
        "resume_profile": out / "resume_profile.json",
        "claims": out / "claims.json",
        "fit_gap": out / "fit_gap.json",
        "repo_summary": out / "repo_summary.json",
        "exploration": trace_path,
    }
    paths["resume_profile"].write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    paths["claims"].write_text(claims.model_dump_json(indent=2), encoding="utf-8")
    paths["fit_gap"].write_text(fit.model_dump_json(indent=2), encoding="utf-8")
    paths["repo_summary"].write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
