"""
Evaluator reply parsing, repair and diagnostics.

Regression for the deployed failure "The HR / recruiter evaluation could not
produce a valid result after 2 replies (invalid JSON: Extra data ...)": the old
parser sliced from the first "{" to the last "}", so a reply holding two JSON
objects became one undecodable span, and the repair prompt could not say what
was wrong. Scripted evaluators only; no network.
"""

from __future__ import annotations

import json
import logging

import pytest

from interview.evaluation.role_eval import (
    EvaluationPassFailed,
    ReplyRejected,
    TranscriptTurn,
    evaluate_round,
    extract_report_object,
    parse_output,
    report_json_schema,
)
from interview.llm.client import GroqModelClient
from interview.packs.model import load_pack

PACK = load_pack("hr-core")
RUBRIC_IDS = PACK.rubric.dimension_ids()
ANSWER = (
    "I moved from support into engineering because I wanted to build the tools "
    "I kept asking for, and this role is that."
)
QUOTE = "I wanted to build the tools I kept asking for"
TURNS = [
    TranscriptTurn("hr-q", "agent", "Why this role?", "hr", 0.0),
    TranscriptTurn("hr-a", "candidate", ANSWER, "hr", 1.0),
]


def report(**overrides) -> dict:
    body = {
        "dimensions": [
            {
                "dimension_id": dim_id,
                "level": "solid",
                "rationale": "Specific motivation.",
                "citations": [{"turn_id": "hr-a", "quote": QUOTE}],
            }
            for dim_id in RUBRIC_IDS
        ],
        "findings": [],
        "claims": [],
    }
    body.update(overrides)
    return body


VALID = json.dumps(report())


class Scripted:
    """FAKE evaluator: returns scripted (text, finish_reason) replies in order."""

    provider = "scripted"
    model = "scripted-model"

    def __init__(self, replies: list[tuple[str, str]]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict]] = []
        self.schemas: list[dict | None] = []

    async def complete_with_meta(self, messages, *, deadline_s=None, json_schema=None):
        self.seen.append(list(messages))
        self.schemas.append(json_schema)
        text, finish = self.replies.pop(0)
        return text, {
            "provider": "scripted",
            "model_requested": self.model,
            "model_used": self.model,
            "fallback_used": False,
            "attempts": 1,
            "finish_reason": finish,
            "output_mode": "json_schema_strict" if json_schema else "json_object",
        }


async def run(evaluator, max_repairs: int = 1):
    return await evaluate_round(
        evaluator,
        perspective="hr",
        round_meta={"round": "hr", "label": "Recruiter", "questions_asked": 1},
        pack=PACK,
        turns=TURNS,
        claims=[],
        family_label="Software",
        target_role="Backend engineer",
        seniority="mid",
        max_repairs=max_repairs,
        lane="text",
    )


# ---------------------------------------------------------------------------
# Extraction: exactly one object, optionally fenced
# ---------------------------------------------------------------------------


def test_valid_json_is_accepted() -> None:
    out = parse_output(VALID, rubric_ids=RUBRIC_IDS, claim_ids=[])
    assert [d.dimension_id for d in out.dimensions] == RUBRIC_IDS


@pytest.mark.parametrize(
    "text",
    [
        f"```json\n{VALID}\n```",
        f"```\n{VALID}\n```",
        f"  ```JSON\n{VALID}```  \n",
    ],
)
def test_fenced_json_is_accepted(text: str) -> None:
    assert extract_report_object(text) == report()


@pytest.mark.parametrize(
    "text",
    [
        VALID + "\n" + VALID,                     # the deployed "Extra data" shape
        VALID + VALID,
        VALID + '\n{"dimensions": []}',
        f"```json\n{VALID}\n{VALID}\n```",
        VALID + "\n[1]",
    ],
)
def test_multiple_objects_are_rejected_not_first_picked(text: str) -> None:
    with pytest.raises(ReplyRejected) as info:
        extract_report_object(text)
    assert info.value.kind == "multiple_objects"
    assert "Extra data" not in str(info.value)


@pytest.mark.parametrize(
    "text, kind",
    [
        (VALID + "\nHope this helps!", "trailing_content"),
        (VALID + " }", "trailing_content"),
        (f"```json\n{VALID}\n```\nNotes: done.", "trailing_content"),
        (f"```json\n{VALID}", "trailing_content"),
        ("Here is the report:\n" + VALID, "leading_content"),
        ("<think>draft {\"a\":1}</think>" + VALID, "leading_content"),
        ("[" + VALID + "]", "not_object"),
        ("", "empty"),
        ('{"dimensions": [', "invalid_json"),
    ],
)
def test_extra_or_missing_content_is_rejected(text: str, kind: str) -> None:
    with pytest.raises(ReplyRejected) as info:
        extract_report_object(text)
    assert info.value.kind == kind


def test_rejection_messages_never_echo_reply_content() -> None:
    secret = "my-unique-transcript-words"
    for text in (VALID + f" {secret}", f'{{"x": "{secret}"', VALID + f'{{"q":"{secret}"}}'):
        with pytest.raises(ReplyRejected) as info:
            extract_report_object(text)
        assert secret not in str(info.value)


def test_schema_failure_is_reported_without_values() -> None:
    bad = report()
    bad["dimensions"][0]["level"] = "my-unique-transcript-words"
    with pytest.raises(ReplyRejected) as info:
        parse_output(json.dumps(bad), rubric_ids=RUBRIC_IDS, claim_ids=[])
    assert info.value.kind == "schema"
    assert "dimensions.0.level" in str(info.value)
    assert "my-unique-transcript-words" not in str(info.value)


def test_rubric_and_claim_validation_are_kept() -> None:
    missing = report(dimensions=report()["dimensions"][1:])
    with pytest.raises(ReplyRejected) as info:
        parse_output(json.dumps(missing), rubric_ids=RUBRIC_IDS, claim_ids=[])
    assert info.value.kind == "rubric"
    doubled = report(dimensions=report()["dimensions"] + report()["dimensions"][:1])
    with pytest.raises(ReplyRejected) as info:
        parse_output(json.dumps(doubled), rubric_ids=RUBRIC_IDS, claim_ids=[])
    assert info.value.kind == "rubric"
    claims = report(claims=[{"claim_id": "c9", "status": "untested"}])
    with pytest.raises(ReplyRejected) as info:
        parse_output(json.dumps(claims), rubric_ids=RUBRIC_IDS, claim_ids=["c1"])
    assert info.value.kind == "claims"


# ---------------------------------------------------------------------------
# The round: one bounded repair, truncation, evidence checks
# ---------------------------------------------------------------------------


async def test_successful_repair_after_multiple_objects() -> None:
    evaluator = Scripted([(VALID + "\n" + VALID, "stop"), (VALID, "stop")])
    result = await run(evaluator)
    rr = result.round_result
    assert rr.status == "evaluated" and rr.attempts == 2
    assert rr.evaluation_meta["repairs"] == 1
    assert rr.evaluation_meta["reply_outcomes"] == ["multiple_objects", "ok"]
    repair = evaluator.seen[1][-1]["content"]
    assert "more than one JSON value" in repair
    assert "exactly one corrected" in repair
    # The repair keeps the original system + data, plus one assistant/user pair.
    assert len(evaluator.seen[1]) == 4


async def test_repair_is_bounded_and_fails_explicitly() -> None:
    evaluator = Scripted([(VALID + VALID, "stop"), (VALID + " trailing", "stop"), (VALID, "stop")])
    with pytest.raises(EvaluationPassFailed) as info:
        await run(evaluator, max_repairs=1)
    assert info.value.category == "invalid_output"
    assert info.value.attempts == 2
    assert len(evaluator.seen) == 2                       # never a third call
    assert [c["error_category"] for c in info.value.calls] == ["multiple_objects", "trailing_content"]
    assert "after 2 replies" in str(info.value)


async def test_truncated_reply_is_not_parsed_and_repair_asks_for_shorter() -> None:
    # Even a parseable prefix is refused when finish_reason says it was cut off.
    evaluator = Scripted([(VALID, "length"), (VALID, "stop")])
    result = await run(evaluator)
    assert result.round_result.evaluation_meta["reply_outcomes"] == ["truncated", "ok"]
    assert result.round_result.evaluation_meta["finish_reasons"] == ["length", "stop"]
    repair = evaluator.seen[1]
    assert len(repair) == 3                                # partial reply not echoed
    assert "cut off" in repair[-1]["content"]


async def test_truncated_twice_fails_as_truncated() -> None:
    evaluator = Scripted([('{"dimensions": [', "length"), ('{"dimensions": [', "length")])
    with pytest.raises(EvaluationPassFailed) as info:
        await run(evaluator)
    assert info.value.category == "truncated"
    assert "cut off" in info.value.recovery


async def test_unverifiable_evidence_is_downgraded_not_invented() -> None:
    fabricated = report()
    for dim in fabricated["dimensions"]:
        dim["citations"] = [{"turn_id": "hr-a", "quote": "I led a team of forty engineers"}]
    result = await run(Scripted([(json.dumps(fabricated), "stop")]))
    rr = result.round_result
    assert all(not d.assessed and d.score is None for d in rr.dimensions)
    assert rr.aggregate.score is None
    assert rr.rejected_evidence == len(RUBRIC_IDS)


async def test_schema_is_offered_and_logs_are_metadata_only(caplog) -> None:
    evaluator = Scripted([(VALID + VALID, "stop"), (VALID, "stop")])
    with caplog.at_level(logging.INFO, logger="interview.evaluation.role_eval"):
        await run(evaluator)
    assert evaluator.schemas[0] == report_json_schema(RUBRIC_IDS, [])
    lines = [r.getMessage() for r in caplog.records if "evaluation reply" in r.getMessage()]
    assert len(lines) == 2
    assert "model=scripted-model" in lines[0]
    assert "finish_reason=stop" in lines[0]
    assert f"reply_chars={len(VALID + VALID)}" in lines[0]
    assert "outcome=multiple_objects" in lines[0] and "attempt_s=" in lines[0]
    assert "outcome=ok" in lines[1]
    text = "\n".join(lines)
    assert QUOTE not in text and "support into engineering" not in text


# ---------------------------------------------------------------------------
# Provider request: strict schema only where configured
# ---------------------------------------------------------------------------


def _client(strict: list[str]) -> GroqModelClient:
    return GroqModelClient(
        api_key="test-not-a-key",
        config={"groq": {"roles": {"evaluator": "openai/gpt-oss-120b"}, "strict_schema_models": strict}},
    )


def test_strict_schema_only_for_configured_models() -> None:
    schema = report_json_schema(RUBRIC_IDS, ["c1"])
    client = _client(["openai/gpt-oss-120b"])
    fmt, mode = client.response_format_for("openai/gpt-oss-120b", json_object=True, json_schema=schema)
    assert mode == "json_schema_strict"
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    # A fallback model outside the list gets JSON-object mode, never strict.
    fmt, mode = client.response_format_for("other/model", json_object=True, json_schema=schema)
    assert (fmt, mode) == ({"type": "json_object"}, "json_object")
    # Nothing configured: JSON-object mode everywhere.
    fmt, mode = _client([]).response_format_for("openai/gpt-oss-120b", json_object=True, json_schema=schema)
    assert mode == "json_object"


def test_strict_schema_meets_strict_mode_rules() -> None:
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    schema = report_json_schema(RUBRIC_IDS, ["c1", "c2"])["schema"]
    walk(schema)
    dims = schema["properties"]["dimensions"]["items"]["properties"]
    assert dims["dimension_id"]["enum"] == RUBRIC_IDS


async def test_evaluator_request_carries_the_response_format(monkeypatch) -> None:
    from interview.evaluation.role_eval import GroqEvaluator

    sent: list[dict] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            sent.append(kwargs)

            class Msg:
                content = VALID
                tool_calls = None

            class Choice:
                message = Msg()
                finish_reason = "stop"

            class Resp:
                model = kwargs["model"]
                usage = None
                choices = [Choice()]

            return Resp()

    client = _client(["openai/gpt-oss-120b"])

    class FakeSdk:
        class chat:
            completions = FakeCompletions()

    monkeypatch.setattr(client, "_openai", lambda: FakeSdk)
    evaluator = GroqEvaluator(client)
    text, meta = await evaluator.complete_with_meta(
        [{"role": "user", "content": "x"}], json_schema=report_json_schema(RUBRIC_IDS, [])
    )
    assert text == VALID
    assert sent[0]["response_format"]["type"] == "json_schema"
    assert meta["output_mode"] == "json_schema_strict" and meta["finish_reason"] == "stop"
    await evaluator.complete_with_meta([{"role": "user", "content": "x"}])
    assert sent[1]["response_format"] == {"type": "json_object"}
