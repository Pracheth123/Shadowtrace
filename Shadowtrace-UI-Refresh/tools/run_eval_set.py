"""
Run the evaluator over evaluation_set/examples.jsonl.

    python tools/run_eval_set.py --out logs/eval_set/mock          # offline, mock
    python tools/run_eval_set.py --live --out logs/eval_set/live   # real Groq calls

Each example becomes a one-question round evaluated with the round's real
rubric through `evaluate_round` — the same code the app uses. Output:

  results.jsonl                one line per example: round status, levels,
                               findings, evaluator metadata, errors
  review_sheet_filled.csv      evaluation_set/review_sheet.csv with ONLY the
                               machine columns filled; reviewer columns empty

Nothing here produces or implies a human label.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interview.evaluation.role_eval import (  # noqa: E402
    EvaluationPassFailed,
    MockEvaluator,
    TranscriptTurn,
    evaluate_round,
)
from interview.packs.model import load_pack  # noqa: E402

PACK = {
    ("hr", "software"): "hr-core",
    ("hr", "sales"): "hr-core",
    ("hiring_manager", "software"): "manager-core",
    ("hiring_manager", "sales"): "manager-core",
    ("domain_specialist", "software"): "specialist-software",
    ("domain_specialist", "sales"): "specialist-sales",
}


async def run(live: bool, out: Path) -> int:
    examples = [
        json.loads(line)
        for line in (ROOT / "evaluation_set" / "examples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if live:
        from interview.config import get_settings
        from interview.evaluation.role_eval import GroqEvaluator
        from interview.llm.client import GroqModelClient

        settings = get_settings()
        evaluator = GroqEvaluator(GroqModelClient(), max_tokens=settings.eval_max_tokens)
        deadline = settings.eval_round_deadline_s
    else:
        evaluator = MockEvaluator()
        deadline = None
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for example in examples:
        turns = [TranscriptTurn("q1", "agent", example["question"], example["round"], 0.0)]
        if example["answer"].strip():
            turns.append(TranscriptTurn("a1", "candidate", example["answer"], example["round"], 1.0))
        started = time.perf_counter()
        row = {"id": example["id"], "category": example["category"], "family": example["family"], "round": example["round"]}
        try:
            result = await evaluate_round(
                evaluator,
                perspective=example["round"],  # type: ignore[arg-type]
                round_meta={"round": example["round"], "label": example["round"], "questions_asked": 1},
                pack=load_pack(PACK[(example["round"], example["family"])]),
                turns=turns,
                claims=[],
                family_label=example["family"].title(),
                target_role="Backend engineer" if example["family"] == "software" else "Account executive",
                seniority="mid",
                deadline_s=deadline,
                lane="text",
            )
            rr = result.round_result
            row.update(
                status=rr.status,
                levels={d.dimension_id: d.level.value if hasattr(d.level, "value") else d.level for d in rr.dimensions},
                findings=[f.model_dump(include={"dimension_id", "polarity", "quote", "explanation", "practice", "source_match"}) for f in rr.findings],
                rejected_evidence=rr.rejected_evidence,
                evaluation_meta=rr.evaluation_meta,
            )
        except EvaluationPassFailed as exc:
            row.update(status="failed", error=str(exc)[:300], category_error=exc.category)
        row["elapsed_s"] = round(time.perf_counter() - started, 2)
        results.append(row)
        print(f"{example['id']:28s} {row['status']:22s} {row['elapsed_s']:6.2f}s")

    with open(out / "results.jsonl", "w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_id = {r["id"]: r for r in results}
    with open(ROOT / "evaluation_set" / "review_sheet.csv", encoding="utf-8") as src, open(
        out / "review_sheet_filled.csv", "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames or [])
        writer.writeheader()
        run_id = out.name
        for line in reader:
            r = by_id.get(line["example_id"], {})
            finding = (r.get("findings") or [{}])[0]
            first_level = next(iter((r.get("levels") or {}).items()), ("", ""))
            line.update(
                run_id=run_id,
                evaluator=f"{evaluator.provider}:{evaluator.model}",
                model_called=str((r.get("evaluation_meta") or {}).get("model_call", False)),
                round_status=r.get("status", ""),
                dimension=finding.get("dimension_id") or first_level[0],
                level=(r.get("levels") or {}).get(finding.get("dimension_id") or first_level[0], ""),
                finding_polarity=finding.get("polarity", ""),
                finding_quote=finding.get("quote", ""),
                quote_found_in_answer=str(finding.get("source_match", "")) if finding else "",
                finding_explanation=finding.get("explanation", ""),
                suggested_action=finding.get("practice", ""),
            )
            # Reviewer columns are deliberately left as they were (empty).
            writer.writerow(line)
    failed = sum(1 for r in results if r["status"] == "failed")
    print(f"\n{len(results)} examples, {failed} failed. Wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use the real Groq evaluator (billable)")
    parser.add_argument("--out", default="logs/eval_set/run")
    args = parser.parse_args()
    return asyncio.run(run(args.live, Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
