"""
Live check of evaluator structured output. Needs GROQ_API_KEY; makes real,
billed model calls (one or two per model). Prints no key, prompt or reply.

    python tools/ops/probe_eval_output.py            # evaluator + its fallback
    python tools/ops/probe_eval_output.py --model openai/gpt-oss-120b

For each model it runs one HR-round evaluation of a SYNTHETIC two-turn
transcript (no candidate data) through the real evaluator path and prints:
model, output mode actually requested (json_schema_strict / json_object),
finish_reason, reply length, outcome and duration. A model listed in
EVAL_STRICT_SCHEMA_MODELS that answers with an invalid_request error does not
support strict schema for this key: remove it from that setting.

Exit code 0 only when every probed model produced a valid report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from interview.config import get_settings  # noqa: E402
from interview.evaluation.role_eval import (  # noqa: E402
    EvaluationPassFailed,
    GroqEvaluator,
    TranscriptTurn,
    evaluate_round,
)
from interview.llm.client import GroqModelClient  # noqa: E402
from interview.packs.model import load_pack  # noqa: E402

TURNS = [
    TranscriptTurn("probe-q", "agent", "Why are you interested in this role?", "hr", 0.0),
    TranscriptTurn(
        "probe-a",
        "candidate",
        "I spent three years in customer support and kept writing small scripts to "
        "fix the tools we used, so I want a role where building those tools is the job.",
        "hr",
        1.0,
    ),
]


async def probe(model: str) -> dict:
    settings = get_settings()
    overrides = settings.model_copy(
        update={"model_evaluator": model, "model_fallback_quality": model}
    )
    client = GroqModelClient(settings=overrides)
    evaluator = GroqEvaluator(client, max_tokens=settings.eval_max_tokens)
    started = time.monotonic()
    row = {"model": model, "strict_configured": client.supports_strict_schema(model)}
    try:
        result = await evaluate_round(
            evaluator,
            perspective="hr",
            round_meta={"round": "hr", "label": "Recruiter", "questions_asked": 1},
            pack=load_pack("hr-core"),
            turns=TURNS,
            claims=[],
            family_label="Software",
            target_role="Software engineer",
            seniority="mid",
            max_repairs=settings.eval_max_repairs,
            deadline_s=settings.eval_round_deadline_s,
            lane="text",
        )
        em = result.round_result.evaluation_meta
        row.update(
            ok=True,
            output_mode=em.get("output_mode"),
            finish_reasons=em.get("finish_reasons"),
            reply_outcomes=em.get("reply_outcomes"),
            model_used=em.get("model_used"),
        )
    except EvaluationPassFailed as exc:
        row.update(
            ok=False,
            category=exc.category,
            output_mode=[c.get("output_mode") for c in exc.calls],
            finish_reasons=[c.get("finish_reason") for c in exc.calls],
            reply_outcomes=[c.get("error_category") for c in exc.calls],
            reply_chars=[c.get("reply_chars") for c in exc.calls],
        )
    row["elapsed_s"] = round(time.monotonic() - started, 2)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", action="append", help="model id (repeatable)")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.has_groq:
        print("GROQ_API_KEY is not set; this probe needs a live key.", file=sys.stderr)
        return 2
    models = args.model or list(
        dict.fromkeys([settings.model_evaluator, settings.model_fallback_quality])
    )
    rows = [asyncio.run(probe(m)) for m in models]
    print(json.dumps(rows, indent=2))
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
