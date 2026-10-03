"""
Minimal live-agent loop — stage 5.

Asyncio tool loop with a hard step limit. The agent proposes the next move
from tool results; the guard accepts or overrides. Spine is spoken from the
pack verbatim. A probe is one question phrased from the guard-approved target.

Stage 11: in panel mode there is one instance of this class per persona, all
sharing one `SessionTools` and therefore one guard. `persona` is stamped on the
steps this instance emits; it is None outside panel mode.

Stage 13: an agent may be given a `RoleSpec` and a `RoleProposer`. When it is,
the next move comes from that role's own reasoning — HR, hiring manager or
domain specialist — instead of the shared deterministic `propose()`, and a
probe is phrased by the role rather than by the generic `phrase_probe`. The
guard is unchanged and still rules on every proposal, so role-specific
reasoning cannot widen what may be asked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from interview.events.schema import AgentStep, CoverageUpdate, GuardOverride, QuestionPlanned
from interview.session.guard import (
    Decision,
    Intent,
    depth_cap,
    evaluate,
    is_near_duplicate,
    probes_fit,
)
from interview.session.tools import SessionTools

if TYPE_CHECKING:
    from interview.events.bus import EventBus

log = logging.getLogger(__name__)

# Read tools plus ask_spine / note / end. A loop that exceeds this is stopped.
DEFAULT_MAX_TOOL_CALLS = 6


@dataclass(frozen=True)
class Outcome:
    kind: Literal["spine", "probe", "end"]
    text: str
    decision: Decision


def phrase_probe(
    *,
    claim_text: str | None,
    transcript_anchor: str | None,
) -> str:
    """One question. The target was already approved; this only phrases it."""
    if claim_text:
        quoted = claim_text.strip().rstrip(".")
        return (
            f'About "{quoted}": what decision did you make, '
            "and what would have broken it?"
        )
    anchor = (transcript_anchor or "").strip()
    return f'You said "{anchor}". What did you do next, and why that choice?'


def propose(tools: SessionTools) -> Intent:
    """
    Deterministic proposal from tool state.

    Spine stays in pack order. After a spine is on the board, one in-scope
    probe is proposed when time and depth allow, then the agent returns to
    the next spine. This is a proposal — the guard may still reject it.
    """
    state = tools.guard_state()
    cap = depth_cap(state.pack, state.intensity)
    claim = tools.untested_claim()
    can_probe = (
        bool(state.covered)
        and state.depth_on_current < 1
        and state.depth_on_current < cap
        and probes_fit(state)
        and claim is not None
    )
    if state.outstanding:
        if can_probe and claim is not None:
            return Intent(
                action="probe",
                claim_id=claim.id,
                target_depth=state.depth_on_current + 1,
            )
        nxt = state.outstanding[0]
        return Intent(action="ask_spine", spine_id=nxt)
    if can_probe and claim is not None:
        return Intent(
            action="probe",
            claim_id=claim.id,
            target_depth=state.depth_on_current + 1,
        )
    return Intent(action="end_session")


class LiveAgent:
    def __init__(
        self,
        bus: "EventBus",
        session_id: str,
        tools: SessionTools,
        *,
        scripted_intents: dict[int, Intent] | None = None,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        persona: str | None = None,
        spec=None,
        proposer=None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._tools = tools
        self._scripted = scripted_intents or {}
        self._max_tool_calls = max_tool_calls
        # None outside panel mode. Panel members differ only by this field and
        # their voice — the rules they answer to are the one shared guard.
        self.persona = persona
        # Stage 13 role wiring. Both None keeps the stage-5 behaviour exactly.
        self._spec = spec
        self._proposer = proposer
        # What the candidate last said, so a role can anchor a follow-up on it.
        self._last_answer = ""
        # Role-phrased question for the current turn, set by the proposer and
        # used instead of the generic probe template.
        self._proposed_question: str | None = None
        self.last_decision_summary = ""

    async def run(self, *, turn_id: str, turn_index: int, commit: bool = True) -> Outcome:
        step = 0
        tool_calls = 0

        async def observe(summary: str) -> None:
            nonlocal step
            await self._emit_step(turn_id, step, "observe", summary)
            step += 1

        async def think(summary: str) -> None:
            nonlocal step
            await self._emit_step(turn_id, step, "think", summary)
            step += 1

        async def speak(summary: str) -> None:
            nonlocal step
            await self._emit_step(turn_id, step, "speak", summary)
            step += 1

        async def act(name: str, args: dict, *, force: bool = False) -> dict | None:
            nonlocal step, tool_calls
            # The reasoning loop stops at the cap. The single enforced tool
            # (ask_spine / end_session) still runs so the log has its result.
            if not force and tool_calls >= self._max_tool_calls:
                return None
            idx = step
            await self._emit_step(turn_id, idx, "act", f"call {name}")
            step += 1
            tool_calls += 1
            return await self._tools.call(
                self._bus,
                session_id=self._session_id,
                turn_id=turn_id,
                step_index=idx,
                name=name,
                args=args,
                producer=self._producer,
            )

        await observe("candidate turn opened")
        limited = False
        for name in (
            "get_coverage",
            "get_time_remaining",
            "get_claims",
            "get_candidate_signals",
        ):
            result = await act(name, {})
            if result is None:
                limited = True
                break

        if limited:
            intent = Intent(action="end_session")
            decision = self._step_limit_decision()
            await self._emit_override(turn_id, step, decision)
            step += 1
        else:
            await think("propose next move from tool results")
            self._proposed_question = None
            scripted = self._scripted.get(turn_index)
            if scripted is not None:
                intent = scripted
            elif self._proposer is not None and self._spec is not None:
                move = await self._propose_as_role(turn_id)
                intent = move.to_intent()
                # Keep the role's own phrasing for a probe; the guard may still
                # reject the target, in which case nothing is spoken from it.
                if move.action == "probe" and move.question:
                    self._proposed_question = move.question
                self.last_decision_summary = move.decision_summary
                if move.decision_summary:
                    await think(move.decision_summary)
            else:
                intent = propose(self._tools)
            decision = evaluate(intent, self._tools.guard_state())
            if not decision.accepted:
                await self._emit_override(turn_id, step, decision)
                step += 1

        if decision.kind == "end":
            if commit:
                await act("end_session", {}, force=True)
                await self.commit_outcome(turn_id, decision)
            await speak("end session")
            return Outcome(kind="end", text="", decision=decision)

        text = await self._speak_text(intent, decision, act)
        if decision.kind == "probe" and is_near_duplicate(
            text, self._tools.guard_state().asked_questions
        ):
            decision = evaluate(
                Intent(
                    action="probe",
                    claim_id=intent.claim_id,
                    transcript_anchor=intent.transcript_anchor,
                    target_depth=intent.target_depth,
                    proposed_text=text,
                ),
                self._tools.guard_state(),
            )
            await self._emit_override(turn_id, step, decision)
            step += 1
            if decision.kind == "end":
                if commit:
                    await act("end_session", {}, force=True)
                    await self.commit_outcome(turn_id, decision)
                await speak("end session")
                return Outcome(kind="end", text="", decision=decision)
            text = await self._speak_text(intent, decision, act)
        if text:
            decision = replace(decision, text=text)
        if commit:
            await self.commit_outcome(turn_id, decision)
        await speak(f"{decision.kind} accepted")
        return Outcome(kind=decision.kind, text=text, decision=decision)  # type: ignore[arg-type]

    def note_answer(self, text: str) -> None:
        """Record the candidate's latest answer for role anchoring."""
        if text and text.strip():
            self._last_answer = text.strip()

    async def _propose_as_role(self, turn_id: str):
        """Ask this agent's role for the next move."""
        from interview.session.proposer import EvidenceItem, ProposalContext

        tools = self._tools
        state = tools.guard_state()
        evidence = tuple(
            EvidenceItem(
                id=claim.id,
                text=claim.text,
                competency=claim.competency,
                source_ref=getattr(claim, "source_ref", "") or claim.id,
            )
            for claim in tools.claims
        )
        context = ProposalContext(
            spec=self._spec,
            state=state,
            last_answer=self._last_answer,
            transcript=tuple(tools.transcript_texts),
            evidence=evidence,
            time_remaining_s=tools.time_remaining_s(),
            handoff=dict(self.handoff_context or {}),
        )
        return await self._proposer.propose(context)

    # Set by the coordinator before a round begins; read-only context from
    # earlier rounds. Statements and open questions only, never ratings.
    handoff_context: dict | None = None

    async def commit_outcome(self, turn_id: str, decision: Decision) -> None:
        """Apply a prepared decision once the floor is granted."""
        self._tools.commit(decision)
        if decision.kind == "end":
            await self._bus.emit(
                CoverageUpdate(
                    session_id=self._session_id,
                    turn_id=turn_id,
                    producer="guard",
                    covered=list(self._tools.covered),
                    outstanding=list(self._tools.outstanding),
                )
            )
            return
        await self._bus.emit(
            QuestionPlanned(
                session_id=self._session_id,
                turn_id=turn_id,
                producer="guard",
                kind=decision.kind,  # type: ignore[arg-type]
                competency=decision.competency or self._tools.pack.competencies[0].id,
                target_depth=decision.target_depth,
            )
        )
        await self._bus.emit(
            CoverageUpdate(
                session_id=self._session_id,
                turn_id=turn_id,
                producer="guard",
                covered=list(self._tools.covered),
                outstanding=list(self._tools.outstanding),
            )
        )

    def _step_limit_decision(self) -> Decision:
        """Tool loop hit the cap before a proposal. Enforce the next spine."""
        forced = evaluate(Intent(action="end_session"), self._tools.guard_state())
        # evaluate() maps "end while spine remains" to spine_order. Relabel so
        # the log shows the limit, and keep the enforced spine (or end).
        return Decision(
            accepted=False,
            rule="step_limit",
            agent_intent="tool_loop_exceeded",
            enforced_action=forced.enforced_action,
            kind=forced.kind,
            spine_id=forced.spine_id,
            competency=forced.competency,
            target_depth=forced.target_depth,
            claim_id=forced.claim_id,
            transcript_anchor=forced.transcript_anchor,
            text=forced.text,
        )

    async def _speak_text(self, intent: Intent, decision: Decision, act) -> str:
        if decision.kind == "spine":
            result = await act("ask_spine", {"id": decision.spine_id}, force=True)
            pack_text = decision.text
            spoken = pack_text
            if result and result.get("text"):
                spoken = str(result["text"])
            if spoken != pack_text:
                log.warning(
                    "ask_spine returned %r for %s; speaking pack text",
                    spoken,
                    decision.spine_id,
                )
                spoken = pack_text
            if intent.proposed_text is not None and intent.proposed_text != spoken:
                log.warning(
                    "spine %s proposed %r; speaking pack text verbatim",
                    decision.spine_id,
                    intent.proposed_text,
                )
            return spoken

        # A role-phrased question wins: it is what makes an HR follow-up sound
        # like a recruiter and a specialist follow-up sound like a practitioner.
        # Only used when the guard accepted the probe target the role named.
        if self._proposed_question and decision.accepted:
            return self._proposed_question

        claim_text = None
        anchor = decision.transcript_anchor
        if decision.claim_id:
            for claim in self._tools.claims:
                if claim.id == decision.claim_id:
                    claim_text = claim.text
                    break
        return phrase_probe(claim_text=claim_text, transcript_anchor=anchor)

    @property
    def _producer(self) -> str:
        """Panel steps name their voice so a log is readable per persona."""
        if self.persona:
            return f"live_agent:{self.persona}"
        return "live_agent"

    async def _emit_step(
        self, turn_id: str, step_index: int, phase: str, summary: str
    ) -> None:
        await self._bus.emit(
            AgentStep(
                session_id=self._session_id,
                turn_id=turn_id,
                producer=self._producer,
                step_index=step_index,
                band="live",
                phase=phase,  # type: ignore[arg-type]
                summary=summary,
                persona=self.persona,
            )
        )

    async def _emit_override(self, turn_id: str, step_index: int, decision: Decision) -> None:
        rule = decision.rule or "step_limit"
        await self._bus.emit(
            GuardOverride(
                session_id=self._session_id,
                turn_id=turn_id,
                producer="guard",
                step_index=step_index,
                rule=rule,  # type: ignore[arg-type]
                agent_intent=decision.agent_intent,
                enforced_action=decision.enforced_action,
            )
        )
