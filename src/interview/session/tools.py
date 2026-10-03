"""
In-memory session tools — stage 5.

Each call targets <20 ms and never touches the network. Calls and results are
bus events (`tool_call` / `tool_result`). Claims come from a fixture until
stage 6 writes a Claims File. `note_claim_status` is recorded on the tool
result, not a cross-layer blackboard.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from interview.events.schema import ToolCall, ToolResult
from interview.packs.model import Pack
from interview.session.guard import Decision, GuardState

if TYPE_CHECKING:
    from interview.events.bus import EventBus

_CLAIMS_PATH = (
    Path(__file__).resolve().parents[3] / "fixtures" / "claims" / "stage5_claims.json"
)

_CLAIM_STATUS = frozenset({"held", "collapsed", "untested"})


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    competency: str
    status: str = Field(default="untested")


def load_claims_fixture(path: Path | None = None) -> list[Claim]:
    """Load the stage-5 claims fixture. Missing file → empty list (stage 6 fills it)."""
    src = path or _CLAIMS_PATH
    if not src.is_file():
        return []
    raw = json.loads(src.read_text(encoding="utf-8"))
    items = raw.get("claims", []) if isinstance(raw, dict) else []
    loaded: list[Claim] = []
    for item in items:
        if not isinstance(item, dict) or "id" not in item or "text" not in item:
            continue
        loaded.append(
            Claim(
                id=str(item["id"]),
                text=str(item["text"]),
                competency=str(item.get("competency") or "general"),
                status=str(item.get("status") or "untested"),
            )
        )
    return loaded


class SessionTools:
    """Local tool implementations for one live session."""

    def __init__(
        self,
        pack: Pack,
        claims: list[Claim] | None = None,
        *,
        intensity: str = "realistic",
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.pack = pack
        self.intensity = intensity
        self.claims = list(claims or [])
        self._clock = clock or time.monotonic
        self._t0 = self._clock()
        self.covered: list[str] = []
        self.outstanding: list[str] = pack.spine_ids()
        self.depth_on_current = 0
        self.transcript_texts: list[str] = []
        self.signals: dict[str, Any] = {
            "claim_hits": [],
            "pause_stats": None,
            "silence_ms": 0,
            "distress": None,
        }
        self.notes: dict[str, dict[str, str]] = {}
        self.probed: set[str] = set()
        self.asked: list[str] = []
        self.ended = False

    def add_transcript(self, text: str) -> None:
        if text:
            self.transcript_texts.append(text)

    def note_signals(self, payload: dict[str, Any]) -> None:
        self.signals = payload

    def time_remaining_s(self) -> float:
        elapsed = max(0.0, self._clock() - self._t0)
        return max(0.0, self.pack.time_budget_s - elapsed)

    def guard_state(self) -> GuardState:
        competency = {c.id: c.competency for c in self.claims}
        return GuardState(
            pack=self.pack,
            intensity=self.intensity,
            covered=tuple(self.covered),
            outstanding=tuple(self.outstanding),
            depth_on_current=self.depth_on_current,
            time_remaining_s=self.time_remaining_s(),
            claim_ids=frozenset(c.id for c in self.claims),
            claim_competency=competency,
            transcript_texts=tuple(self.transcript_texts),
            asked_questions=tuple(self.asked),
        )

    def untested_claim(self) -> Claim | None:
        for claim in self.claims:
            status = self.notes.get(claim.id, {}).get("status", claim.status)
            if claim.id in self.probed or status in ("held", "collapsed"):
                continue
            return claim
        return None

    def commit(self, decision: Decision) -> None:
        if decision.kind == "spine" and decision.spine_id:
            if decision.spine_id not in self.covered:
                self.covered.append(decision.spine_id)
            if decision.spine_id in self.outstanding:
                self.outstanding.remove(decision.spine_id)
            self.depth_on_current = 0
        elif decision.kind == "probe":
            self.depth_on_current = decision.target_depth
            if decision.claim_id:
                self.probed.add(decision.claim_id)
        elif decision.kind == "end":
            self.ended = True
        if decision.text and decision.text not in self.asked:
            self.asked.append(decision.text)

    def coverage_payload(self) -> dict[str, Any]:
        return {
            "covered": list(self.covered),
            "outstanding": list(self.outstanding),
            "depth_on_current": self.depth_on_current,
        }

    async def call(
        self,
        bus: "EventBus",
        *,
        session_id: str,
        turn_id: str,
        step_index: int,
        name: str,
        args: dict[str, Any],
        producer: str = "live_agent",
    ) -> dict[str, Any]:
        await bus.emit(
            ToolCall(
                session_id=session_id,
                turn_id=turn_id,
                producer=producer,
                step_index=step_index,
                tool=name,
                args=args,
            )
        )
        started = time.perf_counter()
        try:
            result = self._dispatch(name, args)
            ok = True
        except Exception as exc:  # tool errors are results, not crashes
            result = {"error": str(exc)}
            ok = False
        latency_ms = (time.perf_counter() - started) * 1000.0
        await bus.emit(
            ToolResult(
                session_id=session_id,
                turn_id=turn_id,
                producer=producer,
                step_index=step_index,
                tool=name,
                ok=ok,
                result=result,
                latency_ms=latency_ms,
            )
        )
        return result

    def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "get_claims":
            return {"claims": [self._claim_row(c) for c in self.claims]}
        if name == "get_claim":
            return self._get_claim(str(args.get("id", "")))
        if name == "get_coverage":
            return self.coverage_payload()
        if name == "get_time_remaining":
            return {
                "time_remaining_s": round(self.time_remaining_s(), 3),
                "time_budget_s": self.pack.time_budget_s,
            }
        if name == "get_candidate_signals":
            return dict(self.signals)
        if name == "note_claim_status":
            return self._note_claim_status(args)
        if name == "ask_spine":
            return self._ask_spine(str(args.get("id", "")))
        if name == "end_session":
            self.ended = True
            return {"ended": True}
        raise KeyError(f"unknown tool {name}")

    def _claim_row(self, claim: Claim) -> dict[str, str]:
        note = self.notes.get(claim.id, {})
        return {
            "id": claim.id,
            "text": claim.text,
            "competency": claim.competency,
            "status": note.get("status", claim.status),
        }

    def _get_claim(self, claim_id: str) -> dict[str, Any]:
        for claim in self.claims:
            if claim.id == claim_id:
                return self._claim_row(claim)
        raise KeyError(f"unknown claim {claim_id}")

    def _note_claim_status(self, args: dict[str, Any]) -> dict[str, str]:
        claim_id = str(args.get("id", ""))
        status = str(args.get("status", ""))
        quote = str(args.get("quote", ""))
        if status not in _CLAIM_STATUS:
            raise ValueError(f"status must be one of {sorted(_CLAIM_STATUS)}")
        known = {c.id for c in self.claims}
        if claim_id not in known:
            raise KeyError(f"unknown claim {claim_id}")
        self.notes[claim_id] = {"status": status, "quote": quote}
        return {"id": claim_id, "status": status, "quote": quote}

    def _ask_spine(self, spine_id: str) -> dict[str, str]:
        item = self.pack.spine_item(spine_id)
        return {"id": item.id, "text": item.text, "competency": item.competency}
