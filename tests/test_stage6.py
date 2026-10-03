"""
Stage 6 — intake, indexer, fit.

1. Resume parser keeps skill-sync sections and drops instruction lines.
2. Indexer tools are read-only and stop at the step limit.
3. Recorded tool_results select claims without cloning.
4. Claim text that is not in the source is dropped.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from interview.intake.claims import Claim, build_claims
from interview.intake.fit import fit_check
from interview.intake.pipeline import run_intake
from interview.intake.repo_indexer import (
    ALLOWED_TOOLS,
    FsTools,
    ReplayTools,
    RepoIndexer,
    ToolNotAllowed,
    TraceLog,
    shallow_clone,
)
from interview.intake.resume_parser import (
    ResumeExtractionError,
    extract_pdf,
    extract_pdf_text,
    load_resume_text,
    parse_resume,
    parse_resume_file,
)
from interview.intake.schema import IntakeRequest, ResumeProfile
from interview.session.tools import load_claims_fixture

ROOT = Path(__file__).parent.parent
RESUME = ROOT / "fixtures" / "resumes" / "stage6_ada.txt"
JD = ROOT / "fixtures" / "jd" / "stage6_backend.txt"
SRC = ROOT / "src" / "interview" / "intake"


def test_resume_parser_sections_and_sanitize() -> None:
    profile = parse_resume_file(RESUME)
    assert profile.name == "Ada Lovelace"
    assert profile.email == "ada@example.com"
    assert "Flask" in profile.skills
    assert "PostgreSQL" in profile.skills
    assert profile.projects[0].title == "Request logger"
    assert profile.projects[0].tech_stack == ["Flask", "PostgreSQL"]
    assert profile.certifications[0].issuer == "Amazon"
    assert profile.courses[0].name == "Databases"
    dumped = profile.model_dump_json()
    assert "ignore previous instructions" not in dumped.casefold()


def test_real_pdf_round_trips_through_pypdf(tmp_path: Path) -> None:
    """
    Extraction is verified against a genuine PDF, not a hand-made fragment.

    The previous test asserted the old regex behaviour over an invalid snippet
    with no xref table. Passing it proved only that a regex matched bytes, while
    every real FlateDecode resume extracted to "" and was accepted as a blank
    profile. This writes a structurally valid PDF with the app's own writer and
    reads it back.
    """
    from interview.evaluation.report import write_transcript_pdf
    from interview.evaluation.schema import Turn

    pdf = tmp_path / "resume.pdf"
    write_transcript_pdf(
        [
            Turn(turn_id="t1", speaker="candidate", text="Ada Lovelace"),
            Turn(turn_id="t2", speaker="candidate", text="Skills: Flask, PostgreSQL"),
        ],
        pdf,
    )
    extraction = extract_pdf(pdf.read_bytes())
    assert "Ada Lovelace" in extraction.text
    assert "Flask" in extraction.text
    assert extraction.pages >= 1
    assert extraction.image_only is False
    assert extract_pdf_text(pdf.read_bytes()) == extraction.text

    text, truncated = load_resume_text(pdf)
    assert "Ada Lovelace" in text
    assert truncated is False


def test_scanned_pdf_is_refused_with_a_recovery_path(tmp_path: Path) -> None:
    """
    A text-free PDF must not become an empty profile.

    An empty profile yields an interview with no claims to interrogate, which
    still looks like a successful session — the failure most likely to pass
    unnoticed.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    scanned = tmp_path / "scan.pdf"
    with scanned.open("wb") as handle:
        writer.write(handle)

    assert extract_pdf(scanned.read_bytes()).image_only is True
    with pytest.raises(ResumeExtractionError) as caught:
        load_resume_text(scanned)
    assert "scan" in str(caught.value).casefold()
    # Tells the candidate what to do, and does not promise OCR we do not run.
    assert "OCR" in caught.value.recovery or "plain text" in caught.value.recovery


def test_corrupt_pdf_and_empty_text_are_distinct_failures(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really a pdf at all")
    with pytest.raises(ResumeExtractionError) as caught:
        load_resume_text(broken)
    assert "re-export" in caught.value.recovery.casefold()

    blank = tmp_path / "blank.txt"
    blank.write_text("    \n \n", encoding="utf-8")
    with pytest.raises(ResumeExtractionError) as caught:
        load_resume_text(blank)
    assert "no readable text" in str(caught.value).casefold()


def test_plain_text_resume_still_loads(tmp_path: Path) -> None:
    txt = tmp_path / "resume.txt"
    txt.write_text("Ada Lovelace\nada@example.com\n", encoding="utf-8")
    text, truncated = load_resume_text(txt)
    assert "Ada Lovelace" in text and truncated is False


def test_indexer_rejects_code_execution(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Hello.\n", encoding="utf-8")
    tools = FsTools(tmp_path)
    with pytest.raises(ToolNotAllowed):
        tools.call("exec", {"cmd": "python evil.py"})
    with pytest.raises(ToolNotAllowed):
        tools.call("python", {"path": "evil.py"})
    assert "exec" not in ALLOWED_TOOLS


def test_step_limit_stops_the_loop(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "This sample records request latency for every inbound call.\n",
        encoding="utf-8",
    )
    trace = TraceLog(tmp_path / "exploration.jsonl")
    run = RepoIndexer(FsTools(tmp_path), max_steps=1, trace=trace).run()
    calls = [row for row in trace.rows if row["type"] == "tool_call"]
    assert len(calls) == 1
    assert calls[0]["tool"] in ALLOWED_TOOLS
    assert any(row.get("limit") for row in trace.rows)
    assert run.summary.truncated is True


def test_replay_selects_claims_without_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("clone must not run")

    monkeypatch.setattr("interview.intake.repo_indexer.shallow_clone", boom)
    sentence = "This sample records request latency for every inbound call."
    tools = ReplayTools(
        [
            {"tool": "list_dir", "result": {"entries": ["README.md"]}},
            {
                "tool": "read_file",
                "result": {"path": "README.md", "text": sentence, "truncated": False},
            },
            {"tool": "grep", "result": {"hits": []}},
            {"tool": "git_log", "result": {"commits": ["abc1234 init"]}},
        ]
    )
    run = RepoIndexer(tools, max_steps=8, trace=TraceLog(None)).run()
    assert tools.calls == ["list_dir", "read_file", "grep", "git_log"]
    assert shallow_clone  # the real function stays unused
    profile = ResumeProfile()
    claims = build_claims(profile, "", run.evidence)
    assert claims.claims
    assert claims.claims[0].text == sentence
    assert claims.claims[0].source == "repo"


def test_hallucinated_claim_is_dropped() -> None:
    profile = parse_resume(
        "Ada Lovelace\n\nProjects\n\nLogger\n"
        "Built a Flask middleware that records request latency.\n"
    )
    extra = [
        Claim(
            id="c-fake",
            text="Invented a quantum database at Google last year.",
            competency="systems",
            source="repo",
            source_path="README.md",
            quote="Invented a quantum database at Google last year.",
        )
    ]
    resume = "Built a Flask middleware that records request latency."
    claims = build_claims(profile, resume, [], extra=extra)
    texts = [claim.text for claim in claims.claims]
    assert any("Flask middleware" in text for text in texts)
    assert all("quantum" not in text for text in texts)
    assert len(claims.claims) <= 8


def test_fit_overlap_and_unsure_classifier() -> None:
    profile = parse_resume_file(RESUME)
    fit = fit_check(profile, JD.read_text(encoding="utf-8"))
    assert "Python" in fit.matched
    assert "Flask" in fit.matched
    assert "Kubernetes" in fit.missing
    assert fit.nice_to_have == ["Redis"]
    assert fit.score_pct == 67

    fuzzy = fit_check(
        profile,
        "Requirements\nPostgres",
    )
    assert "Postgres" in fuzzy.unsure

    def classifier(prompt: str) -> str:
        assert "<<<DATA" in prompt
        assert "ignore previous" not in prompt.casefold()
        return '```json\n{"decisions":[{"skill":"Postgres","match":true}]}\n```'

    resolved = fit_check(profile, "Requirements\nPostgres", classifier=classifier)
    assert "Postgres" in resolved.matched
    assert resolved.unsure == []


def test_preindexed_intake_writes_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "This service records request latency and writes each row to PostgreSQL.\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    paths = run_intake(
        IntakeRequest(
            resume_path=str(RESUME),
            repo_path=str(repo),
            jd_path=str(JD),
            out_dir=str(out),
            max_steps=8,
        )
    )
    claims = json.loads(Path(paths["claims"]).read_text(encoding="utf-8"))
    fit = json.loads(Path(paths["fit_gap"]).read_text(encoding="utf-8"))
    trace = Path(paths["exploration"]).read_text(encoding="utf-8")
    assert 1 <= len(claims["claims"]) <= 8
    assert all(
        claim["text"].casefold() in (RESUME.read_text(encoding="utf-8") + "\n" + (repo / "README.md").read_text(encoding="utf-8")).casefold()
        or claim["text"].casefold() in (repo / "README.md").read_text(encoding="utf-8").casefold()
        for claim in claims["claims"]
    )
    assert "Kubernetes" in fit["missing"]
    assert '"tool": "read_file"' in trace or '"tool": "list_dir"' in trace
    loaded = load_claims_fixture(Path(paths["claims"]))
    assert loaded[0].text == claims["claims"][0]["text"]


def test_resume_only_fallback(tmp_path: Path) -> None:
    out = tmp_path / "out"
    paths = run_intake(
        IntakeRequest(resume_path=str(RESUME), out_dir=str(out))
    )
    summary = json.loads(Path(paths["repo_summary"]).read_text(encoding="utf-8"))
    assert summary["fallback"] == "resume-only"
    trace = Path(paths["exploration"]).read_text(encoding="utf-8")
    assert "list_dir" not in trace


def test_intake_does_not_import_the_live_bus() -> None:
    banned = ("interview.events.bus", "interview.session", "interview.evaluation")
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
            elif isinstance(node, ast.Import):
                mod = node.names[0].name
            if mod and any(mod == item or mod.startswith(item + ".") for item in banned):
                raise AssertionError(f"{path.name} imports {mod}")
