#!/usr/bin/env python3
"""
Run the offline evaluation pass.

    python tools/evaluate.py \\
        --transcript fixtures/sessions/stage4_sess_a/transcript.json \\
        --log fixtures/sessions/stage4_sess_a/session.jsonl \\
        --claims fixtures/claims/stage5_claims.json \\
        --out fixtures/evaluation/stage4_sess_a
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.evaluation.pipeline import evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline evaluation report from a transcript.")
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--claims", type=Path, default=None)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--context", type=Path, action="append", default=[])
    args = parser.parse_args()
    report = asyncio.run(
        evaluate(
            transcript_path=args.transcript,
            out_dir=args.out,
            claims_path=args.claims,
            log_path=args.log,
            context_paths=tuple(args.context),
        )
    )
    print(
        f"Wrote {args.out} session={report.session_id} "
        f"findings={len(report.findings)} elapsed_s={report.elapsed_s}"
    )


if __name__ == "__main__":
    main()
