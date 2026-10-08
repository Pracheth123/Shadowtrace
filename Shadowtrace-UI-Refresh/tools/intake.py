#!/usr/bin/env python3
"""
Band-1 intake CLI.

    python tools/intake.py --resume resume.txt --repo-url https://github.com/org/repo --out intake_out
    python tools/intake.py --resume resume.txt --repo-path ./already-cloned --jd role.txt --out intake_out
    python tools/intake.py --resume resume.txt --out intake_out
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.intake.pipeline import read_json, run_intake
from interview.intake.schema import IntakeRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build resume_profile, claims, and fit_gap.")
    parser.add_argument("--resume", required=True, type=Path)
    parser.add_argument("--repo-url", default=None)
    parser.add_argument("--repo-path", default=None, type=Path)
    parser.add_argument("--jd", default=None, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()
    if args.repo_url and args.repo_path:
        parser.error("pass only one of --repo-url and --repo-path")

    paths = run_intake(
        IntakeRequest(
            resume_path=str(args.resume),
            repo_url=args.repo_url,
            repo_path=str(args.repo_path) if args.repo_path else None,
            jd_path=str(args.jd) if args.jd else None,
            out_dir=str(args.out),
            max_steps=args.max_steps,
        )
    )
    claims = read_json(Path(paths["claims"]))
    fit = read_json(Path(paths["fit_gap"]))
    print(f"wrote {paths['claims']}")
    print(f"claims: {len(claims.get('claims', []))}")
    print(fit.get("summary", ""))


if __name__ == "__main__":
    main()
