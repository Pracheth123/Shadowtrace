"""
Round coordinator — sequential routing and handoffs.

This is the controlled alternative to the architecture CLAUDE.md removes. There
is no floor contention here: no urge scores, no fairness decay, no competing
personas. One round holds the floor for its whole time slice, the coordinator
decides when it is finished, and the next round begins with an explicit spoken
transition. Exactly one interviewer can speak at any moment because exactly one
round is active, and the runtime's single speak path is unchanged.

Canonical state stays here. Each round gets its own `SessionTools` (its own
pack, spine, coverage and depth counters), and a round's proposer receives a
*bounded, read-only* `ProposalContext` — never a writable shared object. The
handoff between rounds is built once, frozen, and handed forward; nothing
downstream can write back into it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from interview.packs.model import Pack, PackLoadError, load_pack
from interview.session.interview_config import InterviewConfig
from interview.session.roles import (
    CandidateStatement,
    HandoffBrief,
    InterviewRound,
    RoleSpec,
    role_for,
)

log = logging.getLogger(__name__)


class PackUnavailable(RuntimeError):
    """
    A selected round has no usable pack.

    Raised with the available options rather than silently substituting a
    different profession's pack, which would score the candidate against a
    rubric they did not choose.
    """


@dataclass
class RoundPlan:
    """One round's slot: which role, which pack, how long."""

    round: InterviewRound
    spec: RoleSpec
    pack: Pack
    seconds: float

    @property
    def label(self) -> str:
        return self.spec.label


@dataclass
class RoundProgress:
    """Live state for the active round, owned by the coordinator."""

    plan: RoundPlan
    elapsed_s: float = 0.0
    questions_asked: list[str] = field(default_factory=list)
    spine_covered: list[str] = field(default_factory=list)
    statements: list[CandidateStatement] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def spine_total(self) -> int:
        return len(self.plan.pack.spine)

    @property
    def coverage_complete(self) -> bool:
        return len(self.spine_covered) >= self.spine_total

    @property
    def time_exhausted(self) -> bool:
        return self.elapsed_s >= self.plan.seconds

    @property
    def minimum_coverage_met(self) -> bool:
        """
        Enough of the round happened to be worth reporting.

        A round that asked one of five spine questions has not covered its
        competencies, and advancing on time alone would leave the report
        claiming coverage it does not have. The report records this flag.
        """
        if self.spine_total == 0:
            return True
        return len(self.spine_covered) >= max(1, (self.spine_total + 1) // 2)


# A transition is spoken, so it has to sound like a person handing over.
_TRANSITIONS = {
    InterviewRound.HR: (
        "That gives me a good picture of your background. I'm going to hand you "
        "over to the hiring manager now, who wants to get into how you work."
    ),
    InterviewRound.HIRING_MANAGER: (
        "Thanks — that's what I needed on how you operate. Last part: I'll pass "
        "you to our {next_label}, who'll go deeper on the work itself."
    ),
    InterviewRound.DOMAIN_SPECIALIST: (
        "That's everything I wanted to cover on the technical side."
    ),
}


class Coordinator:
    """
    Plans the rounds, decides when each is done, and builds the handoffs.

    Usage::

        coordinator = Coordinator(config)
        plan = coordinator.current          # active round
        coordinator.note_question(text)     # after the guard approves one
        coordinator.note_answer(turn_id, text)
        if coordinator.should_advance(): coordinator.advance()
    """

    def __init__(self, config: InterviewConfig) -> None:
        self.config = config
        self._plans = self._build_plans(config)
        self._index = 0
        self._progress = [RoundProgress(plan=plan) for plan in self._plans]
        self._handoffs: list[HandoffBrief] = []
        # Every question asked anywhere in the interview, so a later round does
        # not repeat an earlier round's question by accident.
        self._all_questions: list[str] = []

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    @staticmethod
    def _build_plans(config: InterviewConfig) -> list[RoundPlan]:
        budget = config.time_budget()
        plans: list[RoundPlan] = []
        for round_ in config.selected_rounds():
            pack_id = config.pack_ids()[round_]
            try:
                pack = load_pack(pack_id)
            except PackLoadError as exc:
                raise PackUnavailable(
                    f"The {round_.value.replace('_', ' ')} round needs pack "
                    f"{pack_id!r}, which could not be loaded ({exc}). "
                    "Choose a different role family or round."
                ) from exc
            spec = role_for(
                round_, family=config.role_family, seniority=config.seniority
            )
            plans.append(
                RoundPlan(
                    round=round_, spec=spec, pack=pack, seconds=budget[round_]
                )
            )
        return plans

    @property
    def plans(self) -> tuple[RoundPlan, ...]:
        return tuple(self._plans)

    @property
    def current(self) -> RoundPlan:
        return self._plans[self._index]

    @property
    def progress(self) -> RoundProgress:
        return self._progress[self._index]

    @property
    def finished(self) -> bool:
        return self._index >= len(self._plans) - 1 and (
            self.progress.coverage_complete or self.progress.time_exhausted
        )

    @property
    def is_last_round(self) -> bool:
        return self._index >= len(self._plans) - 1

    @property
    def handoffs(self) -> tuple[HandoffBrief, ...]:
        return tuple(self._handoffs)

    def round_seconds(self, round_: InterviewRound) -> float:
        for plan in self._plans:
            if plan.round is round_:
                return plan.seconds
        raise KeyError(round_)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def note_question(self, text: str, *, spine_id: str | None = None) -> None:
        """Record a guard-approved question. Feeds cross-round de-duplication."""
        if not text.strip():
            return
        self.progress.questions_asked.append(text)
        self._all_questions.append(text)
        if spine_id and spine_id not in self.progress.spine_covered:
            self.progress.spine_covered.append(spine_id)

    def note_answer(self, turn_id: str, text: str, *, topic: str = "") -> None:
        """
        Record what the candidate said, with its turn id.

        Quote plus turn id is the whole point: a handoff and a finding both have
        to be traceable to a real transcript turn, not to a paraphrase.
        """
        trimmed = " ".join(text.split())
        if not trimmed:
            return
        self.progress.statements.append(
            CandidateStatement(
                turn_id=turn_id,
                quote=trimmed[:400],
                topic=topic or self.current.spec.competencies[0],
            )
        )

    def note_elapsed(self, seconds: float) -> None:
        self.progress.elapsed_s = seconds

    def note_unresolved(self, note: str) -> None:
        if note.strip():
            self.progress.unresolved.append(note.strip())

    @property
    def questions_asked_everywhere(self) -> tuple[str, ...]:
        return tuple(self._all_questions)

    # ------------------------------------------------------------------
    # Advancing
    # ------------------------------------------------------------------

    def should_advance(self) -> bool:
        """
        True when this round is done and another remains.

        Either the round covered its spine, or its time slice ran out. Time
        alone still advances — otherwise one talkative round would consume the
        whole interview and the later rounds would never happen, which is the
        truncation this coordinator exists to prevent.
        """
        if self.is_last_round:
            return False
        progress = self.progress
        return progress.coverage_complete or progress.time_exhausted

    def transition_line(self) -> str:
        """What the candidate hears at a handover."""
        current = self.current
        template = _TRANSITIONS.get(current.round, "")
        if self.is_last_round:
            return template.format(next_label="")
        next_plan = self._plans[self._index + 1]
        return template.format(next_label=next_plan.spec.label.lower())

    def advance(self) -> HandoffBrief | None:
        """
        Close the current round, build its handoff, and move to the next.

        Returns the brief for the round just completed, or None when there is
        nothing after it.
        """
        if self.is_last_round:
            return None
        brief = self.build_handoff()
        self._handoffs.append(brief)
        self._index += 1
        return brief

    # ------------------------------------------------------------------
    # Handoff
    # ------------------------------------------------------------------

    def build_handoff(self) -> HandoffBrief:
        """
        Bounded, source-linked summary of the round that just ended.

        Carries candidate statements (quoted, with turn ids) and open questions.
        Carries no scores, ratings or impressions: the next interviewer must
        form its own view, and a judgement passed forward would make round two's
        questions a function of round one's opinion.
        """
        progress = self.progress
        plan = progress.plan
        covered = tuple(
            plan.pack.spine_item(spine_id).competency
            for spine_id in progress.spine_covered
            if _has_spine(plan.pack, spine_id)
        )
        # Keep the brief small: the most recent statements are the ones a
        # follow-up round can actually act on.
        statements = tuple(progress.statements[-6:])
        unresolved = tuple(progress.unresolved[-4:])
        opportunities = tuple(_opportunities(statements, plan.spec))
        return HandoffBrief(
            from_round=plan.round,
            topics_covered=tuple(dict.fromkeys(covered)),
            statements=statements,
            unresolved=unresolved,
            opportunities=opportunities,
            questions_asked=tuple(self._all_questions),
        )

    def context_for_current(self) -> dict:
        """
        Read-only context from earlier rounds, for the active round's proposer.

        Merged across all prior handoffs, so the specialist sees both the HR and
        the manager round rather than only the one immediately before it.
        """
        merged: dict = {
            "prior_rounds": [brief.from_round.value for brief in self._handoffs],
            "topics_covered": [],
            "candidate_statements": [],
            "unresolved": [],
            "opportunities": [],
            "questions_asked": list(self._all_questions),
        }
        for brief in self._handoffs:
            payload = brief.as_context()
            merged["topics_covered"].extend(payload["topics_covered"])
            merged["candidate_statements"].extend(payload["candidate_statements"])
            merged["unresolved"].extend(payload["unresolved"])
            merged["opportunities"].extend(payload["opportunities"])
        merged["topics_covered"] = list(dict.fromkeys(merged["topics_covered"]))
        return merged

    def summary(self) -> list[dict]:
        """Per-round outcome, for the report."""
        return [
            {
                "round": progress.plan.round.value,
                "label": progress.plan.label,
                "pack_id": progress.plan.pack.pack_id,
                "rubric_version": (
                    progress.plan.pack.rubric.version
                    if progress.plan.pack.rubric
                    else "legacy.v0"
                ),
                "seconds_allocated": round(progress.plan.seconds, 1),
                "seconds_used": round(progress.elapsed_s, 1),
                "spine_covered": len(progress.spine_covered),
                "spine_total": progress.spine_total,
                "coverage_complete": progress.coverage_complete,
                "minimum_coverage_met": progress.minimum_coverage_met,
                "questions_asked": len(progress.questions_asked),
            }
            for progress in self._progress
        ]


def _has_spine(pack: Pack, spine_id: str) -> bool:
    try:
        pack.spine_item(spine_id)
        return True
    except KeyError:
        return False


_FOLLOW_UP_CUES = (
    "but", "although", "we never", "i didn't", "i did not", "not sure",
    "in hindsight", "looking back", "it failed", "went wrong", "i'd change",
)


def _opportunities(
    statements: tuple[CandidateStatement, ...], spec: RoleSpec
) -> list[str]:
    """
    Threads worth pulling in a later round.

    Phrased as observations about the *statement*, not about the person: "the
    candidate mentioned X without saying Y" is a follow-up opportunity, while
    "seemed evasive" is an unsupported judgement and never crosses a round.
    """
    found: list[str] = []
    for statement in statements:
        folded = statement.quote.casefold()
        for cue in _FOLLOW_UP_CUES:
            if cue in folded:
                found.append(
                    f'[{statement.turn_id}] mentioned "{cue}" without being '
                    f"asked to expand — open thread on {statement.topic}"
                )
                break
    return found[:4]


__all__ = ["Coordinator", "PackUnavailable", "RoundPlan", "RoundProgress"]
