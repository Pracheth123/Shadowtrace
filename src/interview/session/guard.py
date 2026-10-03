"""
Deterministic guard — stage 5.

Pure rules over an agent intent. The guard does not choose questions: on a
violation it returns the enforced action (next pack spine, capped probe, or
end). The caller logs `guard_override` and speaks only the enforced result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from interview.packs.model import Pack

# Stage 10 owns the full intensity config. Depth caps are the part the guard
# must enforce now so a probe cannot outrun the chosen hardness.
INTENSITY_MAX_DEPTH: dict[str, int] = {
    "coach": 1,
    "realistic": 2,
    "panel": 3,
}


@dataclass(frozen=True)
class Intent:
    """What the live agent proposed for this turn."""

    action: Literal["ask_spine", "probe", "end_session"]
    spine_id: str | None = None
    claim_id: str | None = None
    transcript_anchor: str | None = None
    target_depth: int = 0
    proposed_text: str | None = None

    def label(self) -> str:
        if self.action == "ask_spine":
            return f"ask_spine:{self.spine_id}"
        if self.action == "end_session":
            return "end_session"
        target = self.claim_id or f"transcript:{self.transcript_anchor}"
        return f"probe:{target}:depth={self.target_depth}"


@dataclass(frozen=True)
class GuardState:
    pack: Pack
    intensity: str
    covered: tuple[str, ...]
    outstanding: tuple[str, ...]
    depth_on_current: int
    time_remaining_s: float
    claim_ids: frozenset[str]
    claim_competency: dict[str, str]
    transcript_texts: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    accepted: bool
    rule: str | None
    agent_intent: str
    enforced_action: str
    kind: Literal["spine", "probe", "end"]
    spine_id: str | None
    competency: str
    target_depth: int
    claim_id: str | None
    transcript_anchor: str | None
    text: str


def depth_cap(pack: Pack, intensity: str) -> int:
    hard = INTENSITY_MAX_DEPTH.get(intensity, INTENSITY_MAX_DEPTH["realistic"])
    return min(pack.probe_policy.max_depth, hard)


def probes_fit(state: GuardState) -> bool:
    """True when one probe still leaves enough time for every remaining spine."""
    reserve = len(state.outstanding) * state.pack.probe_policy.seconds_per_spine
    return state.time_remaining_s >= reserve + state.pack.probe_policy.min_probe_s


def _in_scope(intent: Intent, state: GuardState) -> bool:
    if intent.claim_id:
        return intent.claim_id in state.claim_ids
    anchor = (intent.transcript_anchor or "").strip()
    if len(anchor) < 3:
        return False
    blob = "\n".join(state.transcript_texts).casefold()
    return anchor.casefold() in blob


def _next_spine(state: GuardState):
    if not state.outstanding:
        return None
    return state.pack.spine_item(state.outstanding[0])


def _spine_decision(
    state: GuardState,
    spine_id: str,
    *,
    accepted: bool,
    rule: str | None,
    agent_intent: str,
    enforced_action: str,
) -> Decision:
    item = state.pack.spine_item(spine_id)
    return Decision(
        accepted=accepted,
        rule=rule,
        agent_intent=agent_intent,
        enforced_action=enforced_action,
        kind="spine",
        spine_id=item.id,
        competency=item.competency,
        target_depth=0,
        claim_id=None,
        transcript_anchor=None,
        text=item.text,
    )


def _end_decision(intent: Intent, *, accepted: bool, rule: str | None) -> Decision:
    action = "end_session"
    return Decision(
        accepted=accepted,
        rule=rule,
        agent_intent=intent.label(),
        enforced_action=action,
        kind="end",
        spine_id=None,
        competency="",
        target_depth=0,
        claim_id=None,
        transcript_anchor=None,
        text="",
    )


def _probe_decision(
    intent: Intent,
    state: GuardState,
    *,
    accepted: bool,
    rule: str | None,
    depth: int,
    agent_intent: str,
    enforced_action: str,
) -> Decision:
    if intent.claim_id and intent.claim_id in state.claim_competency:
        competency = state.claim_competency[intent.claim_id]
    elif state.covered:
        competency = state.pack.spine_item(state.covered[-1]).competency
    else:
        competency = state.pack.competencies[0].id
    return Decision(
        accepted=accepted,
        rule=rule,
        agent_intent=agent_intent,
        enforced_action=enforced_action,
        kind="probe",
        spine_id=state.covered[-1] if state.covered else None,
        competency=competency,
        target_depth=depth,
        claim_id=intent.claim_id if intent.claim_id in state.claim_ids else None,
        transcript_anchor=None if intent.claim_id else intent.transcript_anchor,
        text="",
    )


def _force_spine(state: GuardState, intent: Intent, rule: str) -> Decision:
    nxt = _next_spine(state)
    if nxt is None:
        return _end_decision(intent, accepted=False, rule=rule)
    return _spine_decision(
        state,
        nxt.id,
        accepted=False,
        rule=rule,
        agent_intent=intent.label(),
        enforced_action=f"ask_spine:{nxt.id}",
    )


def evaluate(intent: Intent, state: GuardState) -> Decision:
    """
    Apply hard rules. First violation wins.

    Order: spine completeness/order, verbatim text, time budget (drops probes),
    probe depth, claims scope.
    """
    nxt = _next_spine(state)

    if intent.action == "end_session":
        if nxt is not None:
            return _force_spine(state, intent, "spine_order")
        return _end_decision(intent, accepted=True, rule=None)

    if intent.action == "probe" and not state.covered:
        return _force_spine(state, intent, "spine_order")

    if intent.action == "ask_spine":
        if nxt is None:
            return _end_decision(intent, accepted=False, rule="spine_order")
        if intent.spine_id != nxt.id:
            return _force_spine(state, intent, "spine_order")
        proposed = intent.proposed_text
        if proposed is not None and proposed != nxt.text:
            return _spine_decision(
                state,
                nxt.id,
                accepted=False,
                rule="spine_verbatim",
                agent_intent=f"ask_spine:{nxt.id}:proposed",
                enforced_action=f"ask_spine:{nxt.id}:verbatim",
            )
        return _spine_decision(
            state,
            nxt.id,
            accepted=True,
            rule=None,
            agent_intent=intent.label(),
            enforced_action=f"ask_spine:{nxt.id}",
        )

    # probe
    if not probes_fit(state):
        return _force_spine(state, intent, "time_budget")

    cap = depth_cap(state.pack, state.intensity)
    if intent.target_depth > cap or state.depth_on_current >= cap:
        if state.depth_on_current < cap and _in_scope(intent, state):
            capped = cap
            return _probe_decision(
                intent,
                state,
                accepted=False,
                rule="probe_depth",
                depth=capped,
                agent_intent=intent.label(),
                enforced_action=(
                    f"probe:{intent.claim_id or intent.transcript_anchor}:depth={capped}"
                ),
            )
        return _force_spine(state, intent, "probe_depth")

    if not _in_scope(intent, state):
        return _force_spine(state, intent, "claims_scope")

    return _probe_decision(
        intent,
        state,
        accepted=True,
        rule=None,
        depth=intent.target_depth,
        agent_intent=intent.label(),
        enforced_action=intent.label(),
    )


def approved_sequence(events: list) -> list[dict]:
    """
    Rebuild the guard-approved question list from a recorded log.

    Uses `question_planned` plus `ask_spine` tool_result payloads already in
    the file. Does not call tools or a model (contract 5).
    """
    spine_text: dict[str, str] = {}
    spine_id: dict[str, str] = {}
    for event in events:
        if getattr(event, "type", None) != "tool_result":
            continue
        if event.tool != "ask_spine" or not event.ok:
            continue
        turn = event.turn_id or ""
        spine_text[turn] = str(event.result.get("text", ""))
        spine_id[turn] = str(event.result.get("id", ""))

    approved: list[dict] = []
    for event in events:
        if getattr(event, "type", None) != "question_planned":
            continue
        turn = event.turn_id or ""
        row = {
            "turn_id": turn,
            "kind": event.kind,
            "competency": event.competency,
            "target_depth": event.target_depth,
            "spine_id": spine_id.get(turn) if event.kind == "spine" else None,
            "text": spine_text.get(turn) if event.kind == "spine" else None,
        }
        approved.append(row)
    return approved
