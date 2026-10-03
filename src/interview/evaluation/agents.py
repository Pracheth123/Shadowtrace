"""Three evaluator agents. Each one checks a quote against a transcript turn."""

from __future__ import annotations

import asyncio

from interview.evaluation.detectors import hedge_ratio, words
from interview.evaluation.schema import Finding, Observation, Turn
from interview.evaluation.tools import EvalTools

_CAUSE = ("because", "measured", "decided", "built", "shipped", "so ", "then")


def _keep(tools: EvalTools, finding: Finding) -> Finding | None:
    if tools.quote_verified(finding.quote, finding.turn_id):
        return finding
    return None


async def substance_agent(
    tools: EvalTools,
    turns: list[Turn],
    claim_ids: list[str],
) -> list[Finding]:
    step = 0
    await tools.step("substance", step, "observe", "open candidate turns and claims")
    step += 1
    await tools.call("substance", step, "list_candidate_turns", {})
    step += 1
    findings: list[Finding] = []
    for claim_id in claim_ids:
        claim = await tools.call("substance", step, "get_claim", {"id": claim_id})
        step += 1
        turn_id = claim.get("turn_id")
        quote = str(claim.get("quote") or "")
        if not turn_id or not quote:
            continue
        turn = await tools.call("substance", step, "get_transcript_turn", {"turn_id": turn_id})
        step += 1
        status = claim.get("status")
        polarity = "gap" if status == "collapsed" else "support"
        summary = (
            f"The {claim.get('competency') or 'claim'} answer was retracted."
            if status == "collapsed"
            else f"The candidate addressed the {claim.get('competency') or 'claim'} claim in their own words."
        )
        kept = _keep(
            tools,
            Finding(
                agent="substance",
                dimension="technical" if status == "collapsed" else "competency",
                summary=summary,
                quote=quote,
                turn_id=str(turn_id),
                polarity=polarity,  # type: ignore[arg-type]
            ),
        )
        if kept and turn.get("text"):
            findings.append(kept)
            if status == "held":
                technical = _keep(
                    tools,
                    Finding(
                        agent="substance",
                        dimension="technical",
                        summary="The answer states what was built and keeps the claim.",
                        quote=quote,
                        turn_id=str(turn_id),
                        polarity="support",
                    ),
                )
                if technical:
                    findings.append(technical)
    for turn in turns:
        if turn.speaker != "candidate":
            continue
        folded = turn.text.casefold()
        if len(words(turn.text)) < 8 or not any(marker in folded for marker in _CAUSE):
            continue
        got = await tools.call("substance", step, "get_transcript_turn", {"turn_id": turn.turn_id})
        step += 1
        kept = _keep(
            tools,
            Finding(
                agent="substance",
                dimension="technical",
                summary="The answer names a decision or a measurement.",
                quote=str(got.get("text", "")),
                turn_id=turn.turn_id,
                polarity="support",
            ),
        )
        if kept:
            findings.append(kept)
    await tools.step("substance", step, "speak", f"{len(findings)} findings")
    return findings


async def structure_agent(tools: EvalTools, turns: list[Turn], observations: list[Observation]) -> list[Finding]:
    step = 0
    await tools.step("structure", step, "observe", "read answer shape")
    step += 1
    findings: list[Finding] = []
    short_ids = {item.turn_id for item in observations if item.name == "short_answer"}
    for turn in turns:
        if turn.speaker != "candidate":
            continue
        got = await tools.call("structure", step, "get_transcript_turn", {"turn_id": turn.turn_id})
        step += 1
        text = str(got.get("text", ""))
        if turn.turn_id in short_ids:
            polarity = "gap"
            summary = "The answer is too short to show how the decision was made."
        elif len(words(text)) >= 12:
            polarity = "support"
            summary = "The answer has more than one step."
        else:
            continue
        kept = _keep(
            tools,
            Finding(
                agent="structure",
                dimension="structure",
                summary=summary,
                quote=text,
                turn_id=turn.turn_id,
                polarity=polarity,  # type: ignore[arg-type]
            ),
        )
        if kept:
            findings.append(kept)
    await tools.step("structure", step, "speak", f"{len(findings)} findings")
    return findings


async def delivery_agent(tools: EvalTools, turns: list[Turn]) -> list[Finding]:
    """Delivery is compared with this candidate's first answer, not a population norm."""
    step = 0
    await tools.step("delivery", step, "observe", "compare later answers with the first")
    step += 1
    candidate = [turn for turn in turns if turn.speaker == "candidate"]
    findings: list[Finding] = []
    if not candidate:
        await tools.step("delivery", step, "speak", "0 findings")
        return findings
    first = candidate[0]
    got = await tools.call("delivery", step, "get_transcript_turn", {"turn_id": first.turn_id})
    step += 1
    baseline = hedge_ratio(str(got.get("text", "")))
    kept = _keep(
        tools,
        Finding(
            agent="delivery",
            dimension="delivery",
            summary="The first answer is the delivery baseline for this session.",
            quote=str(got.get("text", "")),
            turn_id=first.turn_id,
            polarity="support",
        ),
    )
    if kept:
        findings.append(kept)
    for turn in candidate[1:]:
        got = await tools.call("delivery", step, "get_transcript_turn", {"turn_id": turn.turn_id})
        step += 1
        text = str(got.get("text", ""))
        if len(words(text)) < 8:
            continue
        if hedge_ratio(text) > max(0.15, baseline * 2) and hedge_ratio(text) > baseline:
            kept = _keep(
                tools,
                Finding(
                    agent="delivery",
                    dimension="delivery",
                    summary="This answer hedges more than the candidate's first answer.",
                    quote=text,
                    turn_id=turn.turn_id,
                    polarity="gap",
                ),
            )
            if kept:
                findings.append(kept)
    await tools.step("delivery", step, "speak", f"{len(findings)} findings")
    return findings


async def run_agents(
    tools: EvalTools,
    turns: list[Turn],
    claim_ids: list[str],
    observations: list[Observation],
) -> list[Finding]:
    substance, structure, delivery = await asyncio.gather(
        substance_agent(tools, turns, claim_ids),
        structure_agent(tools, turns, observations),
        delivery_agent(tools, turns),
    )
    return [*substance, *structure, *delivery]
