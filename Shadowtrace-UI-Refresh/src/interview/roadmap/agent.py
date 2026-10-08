"""Roadmap agent. Local store tools only. Three to five prep items, each with evidence."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from interview.events.schema import AgentStep, ToolCall, ToolResult
from interview.roadmap.store import LongitudinalStore


class PrepItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence: str
    session_id: str


class Roadmap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    pack_id: str
    items: list[PrepItem] = Field(min_length=3, max_length=5)


class _Trace:
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


class RoadmapAgent:
    def __init__(self, store: LongitudinalStore, trace_path: Path) -> None:
        self.store = store
        self.trace_path = trace_path

    async def run(self, candidate_id: str, pack_id: str) -> Roadmap:
        trace = _Trace(self.trace_path, f"roadmap-{candidate_id}-{pack_id}")
        step = 0

        async def observe(summary: str) -> None:
            nonlocal step
            await trace.emit(
                AgentStep(
                    session_id=trace.session_id,
                    turn_id=None,
                    producer="roadmap",
                    step_index=step,
                    band="roadmap",
                    phase="observe",
                    summary=summary,
                )
            )
            step += 1

        async def call(name: str, args: dict) -> dict | list:
            nonlocal step
            await trace.emit(
                ToolCall(
                    session_id=trace.session_id,
                    turn_id=None,
                    producer="roadmap",
                    step_index=step,
                    tool=name,
                    args=args,
                )
            )
            if name == "list_sessions":
                result: dict | list = self.store.list_sessions(candidate_id, pack_id)
            elif name == "get_scores":
                result = self.store.get_scores(str(args["session_id"]))
            elif name == "get_claim_history":
                result = self.store.get_claim_history(candidate_id, pack_id)
            elif name == "get_gaps":
                result = self.store.get_gaps(candidate_id, pack_id)
            else:
                result = {"error": f"unknown tool {name}"}
            payload = result if isinstance(result, dict) else {"rows": result}
            await trace.emit(
                ToolResult(
                    session_id=trace.session_id,
                    turn_id=None,
                    producer="roadmap",
                    step_index=step,
                    tool=name,
                    ok=True,
                    result=payload,
                    latency_ms=0.0,
                )
            )
            step += 1
            return result

        await observe("read past sessions for this pack")
        sessions = await call("list_sessions", {"candidate_id": candidate_id, "pack_id": pack_id})
        assert isinstance(sessions, list)
        claims = await call("get_claim_history", {})
        gaps = await call("get_gaps", {})
        assert isinstance(claims, list)
        assert isinstance(gaps, list)
        latest_scores: list[dict] = []
        if sessions:
            latest_scores_raw = await call("get_scores", {"session_id": sessions[-1]["session_id"]})
            assert isinstance(latest_scores_raw, list)
            latest_scores = latest_scores_raw
        items = _plan(sessions, claims, gaps, latest_scores)
        await trace.emit(
            AgentStep(
                session_id=trace.session_id,
                turn_id=None,
                producer="roadmap",
                step_index=step,
                band="roadmap",
                phase="speak",
                summary=f"{len(items)} prep items",
            )
        )
        trace.flush()
        return Roadmap(candidate_id=candidate_id, pack_id=pack_id, items=items)


def _plan(
    sessions: list[dict],
    claims: list[dict],
    gaps: list[dict],
    latest_scores: list[dict],
) -> list[PrepItem]:
    items: list[PrepItem] = []
    seen: set[str] = set()

    def add(text: str, evidence: str, session_id: str) -> None:
        if text in seen or len(items) >= 5:
            return
        if not evidence.strip() or not session_id:
            return
        seen.add(text)
        items.append(PrepItem(text=text, evidence=evidence, session_id=session_id))

    latest_status: dict[str, dict] = {}
    for row in claims:
        latest_status[row["claim_id"]] = row
    for claim_id, row in latest_status.items():
        if row["status"] == "collapsed" and row["quote"]:
            add(
                f"Rehearse {claim_id} until you can state what you did, without retracting it.",
                row["quote"],
                row["session_id"],
            )
        elif row["status"] == "untested":
            add(
                f"Prepare a short example for {claim_id}. It has not been defended in this pack yet.",
                f"{claim_id} is untested as of {row['session_id']}",
                row["session_id"],
            )
    for gap in gaps:
        if gap["quote"]:
            add(
                f"Redo the {gap['dimension']} answer that stalled: {gap['summary']}",
                gap["quote"],
                gap["session_id"],
            )
    if latest_scores and sessions:
        weakest = min(latest_scores, key=lambda row: row["score"])
        add(
            f"Spend the next practice on {weakest['dimension']}. It is the lowest score in the latest session.",
            f"{weakest['dimension']} {weakest['score']} in {sessions[-1]['session_id']}",
            sessions[-1]["session_id"],
        )
    if len(items) < 3 and sessions and latest_scores:
        for row in sorted(latest_scores, key=lambda item: item["score"]):
            add(
                f"Keep a measured example ready for {row['dimension']}.",
                f"{row['dimension']} {row['score']} in {sessions[-1]['session_id']}",
                sessions[-1]["session_id"],
            )
            if len(items) >= 3:
                break
    if len(items) < 3:
        raise RuntimeError("roadmap needs at least three evidenced items")
    return items[:5]
