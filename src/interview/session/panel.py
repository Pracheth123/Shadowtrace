"""
Panel mode — stage 11. Behind `PANEL_MODE`.

Two or three interviewer agents share **one** guard. The guard is the same
`evaluate()` over the same `SessionTools`, so the spine is still asked verbatim
in pack order no matter who speaks: a panel changes *whose voice* asks, never
*what may be asked*. That is why there is no per-persona rule in this module.

One voice at a time. `Panel.floor_for_state()` is the only place that decides
who holds it. It reads the shared guard state — not a model and not a guard
decision — for two reasons:

  - it has to be callable *before* the agent loop starts, because `agent_step`,
    `tool_call` and `likely_next` all carry the persona of the agent taking the
    step, and the speculative loop emits those while the candidate is still
    talking;
  - it is then deterministic, so a recorded panel session replays to the same
    persona sequence with no model live (contract 5).

Floor policy:
  - The next spine question goes to the persona whose pack competency matches
    that spine item; no match falls to round-robin from where the panel left off.
  - A probe stays with the persona who opened the spine it hangs off, so a
    follow-up does not sound like someone else picking up mid-thought. The
    caller says whether the next move is a probe, because the agent's proposal
    is deterministic from the same shared state.
  - Opener and closer are always the lead (the first roster entry).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from interview.packs.model import Pack, PanelRole
from interview.session.guard import GuardState

# A single-voice session still carries a persona internally so the speak path
# has one shape. It never reaches the log: `persona=None` means "not panel".
SOLO = PanelRole(persona="lead", label="Interviewer", voice="", competency="")


@dataclass
class Panel:
    """
    Who is in the room, and who speaks next.

    `enabled` False means an ordinary single-voice session: `persona_for_log()`
    returns None, so no pre-stage-11 log gains a persona value.
    """

    roles: list[PanelRole]
    enabled: bool = False
    _cursor: int = 0
    _by_spine: dict[str, str] = field(default_factory=dict)
    _spoken: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, pack: Pack, *, enabled: bool) -> "Panel":
        """
        A pack with no roster cannot run a panel. Rather than invent voices,
        fall back to one voice and say so by leaving `enabled` False.
        """
        if not enabled or not pack.panel:
            return cls(roles=[SOLO], enabled=False)
        return cls(roles=list(pack.panel), enabled=True)

    @property
    def lead(self) -> PanelRole:
        return self.roles[0]

    @property
    def personas(self) -> list[str]:
        return [role.persona for role in self.roles]

    @property
    def spoken_order(self) -> list[str]:
        """Personas that have held the floor, in first-speak order."""
        return list(self._spoken)

    def role(self, persona: str | None) -> PanelRole:
        for item in self.roles:
            if item.persona == persona:
                return item
        return self.lead

    def persona_for_log(self, persona: str | None) -> str | None:
        """None outside panel mode, so a solo log is unchanged by stage 11."""
        if not self.enabled:
            return None
        return persona

    def floor_for_state(self, state: GuardState, *, probing: bool = False) -> PanelRole:
        """
        Grant the floor for the turn the shared guard state is about to produce.

        `probing` says the agent's deterministic proposal for this state is a
        follow-up rather than the next spine. It matters because a probe hangs
        off the spine already covered, not off the one coming next: without it
        a follow-up to the lead's question would be spoken by whichever voice
        owns the *next* spine, which sounds like the room changing the subject.

        Idempotent for a given state: calling it in the speculative loop and
        again on the committed turn yields the same persona, which is what lets
        a speculative draft be spoken by the voice that drafted it.
        """
        if not self.enabled:
            return self.lead
        if probing or not state.outstanding:
            # A probe on, or the end of, the last spine covered. Whoever opened
            # that spine keeps the floor.
            if state.covered:
                opener = self._by_spine.get(state.covered[-1])
                if opener:
                    return self._mark(opener)
            return self._mark(self._spoken[-1] if self._spoken else self.lead.persona)
        return self._for_spine(state.outstanding[0], state)

    def _for_spine(self, spine_id: str, state: GuardState) -> PanelRole:
        assigned = self._by_spine.get(spine_id)
        if assigned:
            return self._mark(assigned)
        try:
            competency = state.pack.spine_item(spine_id).competency
        except KeyError:
            competency = ""
        for role in self.roles:
            if role.competency == competency:
                self._by_spine[spine_id] = role.persona
                return self._mark(role.persona)
        role = self.roles[self._cursor % len(self.roles)]
        self._cursor += 1
        self._by_spine[spine_id] = role.persona
        return self._mark(role.persona)

    def _mark(self, persona: str) -> PanelRole:
        if persona not in self._spoken:
            self._spoken.append(persona)
        return self.role(persona)
