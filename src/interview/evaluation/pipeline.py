"""Offline evaluation pass. Nothing here is called from the live session."""

from __future__ import annotations

import json
import time
from pathlib import Path

from interview.evaluation.adjudicate import adjudicate
from interview.evaluation.agents import run_agents
from interview.evaluation.detectors import detect
from interview.evaluation.load import delimited_data, load_claims, load_turns
from interview.evaluation.report import write_html, write_json, write_transcript_pdf
from interview.evaluation.schema import Report
from interview.evaluation.score import score_findings
from interview.evaluation.tools import EvalTools, EvalTrace
from interview.events.log import read_events


def _session_id(log_path: Path | None, transcript_path: Path) -> str:
    if log_path and log_path.is_file():
        first = log_path.read_text(encoding="utf-8").splitlines()
        if first:
            try:
                header = json.loads(first[0])
            except json.JSONDecodeError:
                header = {}
            if header.get("kind") == "session_header" and header.get("session_id"):
                return str(header["session_id"])
    return transcript_path.stem


def _event_rows(log_path: Path | None) -> list[dict]:
    if log_path is None or not log_path.is_file():
        return []
    rows: list[dict] = []
    for event in read_events(log_path):
        rows.append({"type": event.type, "turn_id": event.turn_id})
    return rows


async def evaluate(
    *,
    transcript_path: Path,
    out_dir: Path,
    claims_path: Path | None = None,
    log_path: Path | None = None,
    context_paths: tuple[Path, ...] = (),
) -> Report:
    started = time.perf_counter()
    turns = load_turns(transcript_path)
    claims = load_claims(claims_path)
    judgements = adjudicate(claims, turns)
    observations = detect(turns)
    session_id = _session_id(log_path, transcript_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    context = delimited_data(*context_paths)
    if context:
        (out_dir / "context.txt").write_text(context, encoding="utf-8")
    trace = EvalTrace(out_dir / "eval_trace.jsonl", session_id)
    tools = EvalTools(trace, turns, _event_rows(log_path), claims, judgements)
    findings = await run_agents(tools, turns, [claim.id for claim in claims], observations)
    report = Report(
        session_id=session_id,
        claims=judgements,
        findings=findings,
        dimensions=score_findings(findings),
        elapsed_s=round(time.perf_counter() - started, 4),
    )
    trace.flush()
    write_json(report, out_dir / "report.json")
    write_html(report, out_dir / "report.html")
    write_transcript_pdf(turns, out_dir / "transcript.pdf")
    return report
