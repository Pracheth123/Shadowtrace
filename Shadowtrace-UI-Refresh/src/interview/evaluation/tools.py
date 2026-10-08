"""
Lookup tools for the evaluator agents.

Calls are written to an eval trace file. They are not emitted on the live
session bus (contract 6).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from interview.evaluation.schema import ClaimJudgement, EvalClaim, Turn
from interview.events.schema import AgentStep, ToolCall, ToolResult


class EvalTrace:
    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        self._seq = 0
        self._lines: list[str] = []
        self._lock = asyncio.Lock()

    async def emit(self, event: AgentStep | ToolCall | ToolResult) -> None:
        async with self._lock:
            self._seq += 1
            stamped = event.model_copy(update={"seq": self._seq, "t_emit": 0.0})
            self._lines.append(stamped.model_dump_json())

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(self._lines)
        if text:
            text += "\n"
        self.path.write_text(text, encoding="utf-8")


class EvalTools:
    def __init__(
        self,
        trace: EvalTrace,
        turns: list[Turn],
        events: list[dict],
        claims: list[EvalClaim],
        judgements: list[ClaimJudgement],
    ) -> None:
        self.trace = trace
        self._turns = {turn.turn_id: turn for turn in turns}
        self._events = events
        self._claims = {claim.id: claim for claim in claims}
        self._judgements = {item.id: item for item in judgements}

    async def step(self, agent: str, step_index: int, phase: str, summary: str) -> None:
        await self.trace.emit(
            AgentStep(
                session_id=self.trace.session_id,
                turn_id=None,
                producer=agent,
                step_index=step_index,
                band="eval",
                phase=phase,  # type: ignore[arg-type]
                summary=summary,
            )
        )

    async def call(self, agent: str, step_index: int, name: str, args: dict) -> dict:
        await self.trace.emit(
            ToolCall(
                session_id=self.trace.session_id,
                turn_id=None,
                producer=agent,
                step_index=step_index,
                tool=name,
                args=args,
            )
        )
        try:
            result = self._dispatch(name, args)
            ok = True
        except Exception as exc:
            result = {"error": str(exc)}
            ok = False
        await self.trace.emit(
            ToolResult(
                session_id=self.trace.session_id,
                turn_id=None,
                producer=agent,
                step_index=step_index,
                tool=name,
                ok=ok,
                result=result,
                latency_ms=0.0,
            )
        )
        return result

    def quote_verified(self, quote: str, turn_id: str) -> bool:
        turn = self._turns.get(turn_id)
        if turn is None or not quote.strip():
            return False
        return " ".join(quote.casefold().split()) in " ".join(turn.text.casefold().split())

    def _dispatch(self, name: str, args: dict) -> dict:
        if name == "list_candidate_turns":
            rows = [
                {"turn_id": turn.turn_id, "speaker": turn.speaker}
                for turn in self._turns.values()
                if turn.speaker == "candidate"
            ]
            return {"turns": rows}
        if name == "get_transcript_turn":
            turn = self._turns.get(str(args.get("turn_id", "")))
            if turn is None:
                raise KeyError("unknown turn")
            return {"turn_id": turn.turn_id, "speaker": turn.speaker, "text": turn.text}
        if name == "get_claim":
            claim_id = str(args.get("id", ""))
            claim = self._claims.get(claim_id)
            if claim is None:
                raise KeyError("unknown claim")
            judgement = self._judgements.get(claim_id)
            return {
                "id": claim.id,
                "text": claim.text,
                "competency": claim.competency,
                "status": judgement.status if judgement else "untested",
                "quote": judgement.quote if judgement else "",
                "turn_id": judgement.turn_id if judgement else None,
            }
        if name == "get_event_slice":
            turn_id = str(args.get("turn_id", ""))
            types = set(args.get("types") or [])
            rows = []
            for event in self._events:
                if turn_id and event.get("turn_id") != turn_id:
                    continue
                if types and event.get("type") not in types:
                    continue
                rows.append(
                    {
                        "type": event.get("type"),
                        "turn_id": event.get("turn_id"),
                    }
                )
                if len(rows) >= 20:
                    break
            return {"events": rows}
        raise KeyError(f"unknown tool {name}")
