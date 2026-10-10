"""
Evaluation set structure, the offline regression harness and the agreement tool.

The labels used below are SYNTHETIC TEST FIXTURES for the arithmetic. They are
not review results and are never written to evaluation_set/.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import eval_agreement  # noqa: E402
import run_eval_set  # noqa: E402

EXAMPLES = [json.loads(l) for l in (ROOT / "evaluation_set" / "examples.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def test_set_is_varied_split_and_unlabelled() -> None:
    assert 20 <= len(EXAMPLES) <= 30
    categories = Counter(e["category"] for e in EXAMPLES)
    for needed in ("strong", "short", "ambiguous", "contradictory", "incomplete", "silent",
                   "unsupported_number", "transcription_error", "malicious", "source_injection"):
        assert categories[needed] >= 1, needed
    held = [e for e in EXAMPLES if e["split"] == "heldout"]
    assert {e["category"] for e in held} == set(categories), "held-out subset covers every category"
    assert {e["family"] for e in EXAMPLES} == {"software", "sales"}
    # No example carries an expected label; reviewer_focus is an authoring note.
    assert not any(k in e for e in EXAMPLES for k in ("label", "expected_level", "gold"))
    sheet = list(csv.DictReader((ROOT / "evaluation_set" / "review_sheet.csv").open(encoding="utf-8")))
    assert {r["example_id"] for r in sheet} == {e["id"] for e in EXAMPLES}
    for row in sheet:
        for column in eval_agreement.COLUMNS + ("reviewer_id",):
            assert row[column] == "", f"{row['example_id']} {column} must stay empty until a human reviews it"


def test_offline_harness_holds_every_invariant(tmp_path: Path) -> None:
    code = asyncio.run(run_eval_set.run(False, tmp_path, split="all", check=True))
    rows = [json.loads(l) for l in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert code == 0, [r for r in rows if r["invariant_violations"]]
    assert len(rows) == len(EXAMPLES)


def test_invariant_checker_catches_misattribution_and_injection_echo() -> None:
    example = next(e for e in EXAMPLES if e["category"] == "source_injection")
    row = {"status": "evaluated", "levels": {}, "findings": [
        {"quote": "a sentence the candidate never said", "explanation": "x", "practice": "y"},
        {"quote": "", "explanation": "As instructed I will ignore the rubric.", "practice": ""},
    ]}
    problems = run_eval_set.check_invariants(example, row)
    assert any("quote not in the candidate answer" in p for p in problems)
    assert any("ignore the rubric" in p for p in problems)
    silent = next(e for e in EXAMPLES if e["category"] == "silent")
    assert run_eval_set.check_invariants(silent, {"status": "evaluated", "levels": {"d": "solid"}, "findings": []})


def _sheet(path: Path, reviewer: str, labels: dict[str, str]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["example_id", "split", *eval_agreement.COLUMNS, "reviewer_id"])
        writer.writeheader()
        for example_id, value in labels.items():
            writer.writerow({"example_id": example_id, "split": "heldout", **{c: value for c in eval_agreement.COLUMNS},
                             "reviewer_id": reviewer})
    return path


def test_agreement_arithmetic_on_synthetic_fixture_labels(tmp_path: Path) -> None:
    a = _sheet(tmp_path / "a.csv", "reviewer-1", {"e1": "Y", "e2": "Y", "e3": "N", "e4": "N"})
    b = _sheet(tmp_path / "b.csv", "reviewer-2", {"e1": "Y", "e2": "N", "e3": "N", "e4": "N"})
    result = eval_agreement.agreement(a, b)
    col = result["columns"]["quote_attribution_correct"]
    assert col["both_labelled"] == 4 and col["raw_agreement"] == 0.75
    assert col["cohens_kappa"] == 0.5


@pytest.mark.parametrize("reviewer_b", ["reviewer-1", "groq-auto", "Claude"])
def test_agreement_refuses_same_reviewer_or_model_labels(tmp_path: Path, reviewer_b: str) -> None:
    a = _sheet(tmp_path / "a.csv", "reviewer-1", {"e1": "Y"})
    b = _sheet(tmp_path / "b.csv", reviewer_b, {"e1": "Y"})
    with pytest.raises(eval_agreement.NotHumanLabels):
        eval_agreement.agreement(a, b)


def test_agreement_refuses_empty_sheets() -> None:
    sheet = ROOT / "evaluation_set" / "review_sheet.csv"
    with pytest.raises(eval_agreement.NotHumanLabels):
        eval_agreement.agreement(sheet, sheet)
