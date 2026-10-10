#!/usr/bin/env python3
"""
Inter-reviewer agreement for the evaluation set — computed only from real human labels.

    python tools/eval_agreement.py reviewer_a.csv reviewer_b.csv [--split heldout] [--out agreement.json]

Each input is a copy of evaluation_set/review_sheet.csv (or a run's
review_sheet_filled.csv) completed independently by one reviewer, with a
`reviewer_id` on every labelled row. For each human column the script reports
how many examples both reviewers labelled, raw agreement and Cohen's kappa
(Y / N / unsure as categories), separately:

  quote_attribution_correct   is the quote really from the candidate's answer, in context?
  interpretation_fair         does the finding describe the answer fairly?
  relevant_to_question        is it about what was asked?
  action_useful               could a candidate act on the practice suggestion?

It refuses to run when the two sheets share a reviewer id, when nothing has
been labelled, or when a reviewer id looks like a model ("model", "llm",
"gpt", "claude", "groq", "auto"): model output is not human ground truth.
Nothing is written unless labels exist; no number is ever estimated.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

COLUMNS = ("quote_attribution_correct", "interpretation_fair", "relevant_to_question", "action_useful")
VALID = {"y": "Y", "yes": "Y", "n": "N", "no": "N", "unsure": "unsure", "?": "unsure"}
MODEL_LIKE = ("model", "llm", "gpt", "claude", "groq", "auto", "bot")


class NotHumanLabels(ValueError):
    pass


def load(path: Path, split: str | None) -> tuple[str, dict[str, dict[str, str]]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    reviewers = {r.get("reviewer_id", "").strip() for r in rows if any(r.get(c, "").strip() for c in COLUMNS)}
    reviewers.discard("")
    if len(reviewers) != 1:
        raise NotHumanLabels(f"{path}: expected exactly one reviewer_id on labelled rows, found {sorted(reviewers) or 'none'}")
    reviewer = reviewers.pop()
    if any(token in reviewer.lower() for token in MODEL_LIKE):
        raise NotHumanLabels(f"{path}: reviewer_id {reviewer!r} looks like a model; human labels only")
    labels: dict[str, dict[str, str]] = {}
    for r in rows:
        if split and r.get("split") and r["split"] != split:
            continue
        cleaned = {}
        for column in COLUMNS:
            raw = (r.get(column) or "").strip().lower()
            if raw:
                if raw not in VALID:
                    raise NotHumanLabels(f"{path}: {r['example_id']} {column}={raw!r} is not Y/N/unsure")
                cleaned[column] = VALID[raw]
        if cleaned:
            labels[r["example_id"]] = cleaned
    return reviewer, labels


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return round((observed - expected) / (1 - expected), 3)


def agreement(a: Path, b: Path, split: str | None = None) -> dict:
    reviewer_a, la = load(a, split)
    reviewer_b, lb = load(b, split)
    if reviewer_a == reviewer_b:
        raise NotHumanLabels("both sheets come from the same reviewer; agreement needs two independent reviewers")
    if not la or not lb:
        raise NotHumanLabels("no human labels found; nothing to compute")
    out: dict = {"reviewers": [reviewer_a, reviewer_b], "split": split or "all", "columns": {}}
    for column in COLUMNS:
        pairs = [(la[i][column], lb[i][column]) for i in sorted(set(la) & set(lb)) if column in la[i] and column in lb[i]]
        out["columns"][column] = {
            "both_labelled": len(pairs),
            "raw_agreement": round(sum(1 for x, y in pairs if x == y) / len(pairs), 3) if pairs else None,
            "cohens_kappa": kappa(pairs),
            "reviewer_a_counts": dict(Counter(x for x, _ in pairs)),
            "reviewer_b_counts": dict(Counter(y for _, y in pairs)),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sheet_a", type=Path)
    parser.add_argument("sheet_b", type=Path)
    parser.add_argument("--split", choices=("dev", "heldout"), default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    try:
        result = agreement(args.sheet_a, args.sheet_b, args.split)
    except NotHumanLabels as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
