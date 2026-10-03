"""
The three interviewer roles — HR, Hiring Manager, Domain Specialist.

These are **sequential rounds**, not competing personas. CLAUDE.md removes the
old "three live personas with a floor controller, urge scores and fairness
decay" design, and that removal stands: nothing here contends for the
microphone. One round is active at a time, it holds the floor for its whole
slice, and the coordinator hands over explicitly. What this module adds is that
the single active interviewer now reasons *as* a particular kind of
interviewer, instead of three labels sharing one `propose()`.

What actually makes them different — each is a separate axis, and all five are
exercised by tests rather than asserted here:

  1. **Competency targets.** Each role owns a disjoint set of pack
     competencies and will not probe outside them.
  2. **Probe target selection.** Given the same claims and the same answer, the
     three roles pick different things to pull on: HR goes for the career
     narrative and consistency, the manager for the decision and who owned it,
     the specialist for the rejected alternative.
  3. **Question phrasing.** Separate templates per role, and for the specialist
     separate templates *per role family*, because "what would have broken" is
     a software question and "which objection nearly killed it" is a sales one.
  4. **Follow-up depth policy.** HR stays shallow — pressing a career gap four
     levels deep is interrogation, not interviewing. The specialist goes
     deepest, which is where claim interrogation actually belongs.
  5. **Rubric mapping.** Each role scores different dimensions, so a sales
     candidate never receives a technical-substance score.

Evidence handling is shared and deliberate: a claim drawn from a resume is a
*candidate assertion*, and a claim corroborated by a work sample is
*supported*. Roles may say which they are probing, but none of them may treat
absence of corroboration as evidence against the candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class InterviewRound(str, Enum):
    """One round of an interview. `FULL` is the ordered sequence of all three."""

    HR = "hr"
    HIRING_MANAGER = "hiring_manager"
    DOMAIN_SPECIALIST = "domain_specialist"
    FULL = "full"

    @property
    def is_single(self) -> bool:
        return self is not InterviewRound.FULL


# The order a Full Interview runs in: context first, then behaviour, then depth.
# HR before the manager because the career story frames what ownership even
# means for this candidate; the specialist last because its probes are the ones
# that benefit most from the handoff briefs of the other two.
FULL_INTERVIEW_ORDER: tuple[InterviewRound, ...] = (
    InterviewRound.HR,
    InterviewRound.HIRING_MANAGER,
    InterviewRound.DOMAIN_SPECIALIST,
)


class RoleFamily(str, Enum):
    """
    Profession families with dedicated specialist coverage.

    `GENERIC` is a first-class choice, not a failure state. A profession without
    a dedicated pack gets honest generic coverage and the report says so, rather
    than a software rubric wearing a different label.
    """

    SOFTWARE = "software"
    HARDWARE = "hardware"
    SALES = "sales"
    MARKETING = "marketing"
    OPERATIONS = "operations"
    FINANCE = "finance"
    GENERIC = "generic"

    @property
    def has_dedicated_pack(self) -> bool:
        return self is not RoleFamily.GENERIC

    @property
    def label(self) -> str:
        return {
            RoleFamily.SOFTWARE: "Software",
            RoleFamily.HARDWARE: "Hardware",
            RoleFamily.SALES: "Sales",
            RoleFamily.MARKETING: "Marketing",
            RoleFamily.OPERATIONS: "Operations",
            RoleFamily.FINANCE: "Finance",
            RoleFamily.GENERIC: "General (no specialist pack)",
        }[self]


class Seniority(str, Enum):
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"

    @property
    def expects_leadership(self) -> bool:
        """Leadership probes are only fair when the role is a leadership role."""
        return self in (Seniority.SENIOR, Seniority.LEAD)


# What a claim's support actually rests on. Missing corroboration means
# "not established", never "false" — see `EvidenceKind.is_supported`.
class EvidenceKind(str, Enum):
    CANDIDATE_ASSERTION = "candidate_assertion"
    WORK_SAMPLE = "work_sample"
    REPOSITORY = "repository"
    JOB_DESCRIPTION = "job_description"
    COMPANY_CONTEXT = "company_context"

    @property
    def is_supported(self) -> bool:
        """True only when something outside the candidate's own claim backs it."""
        return self in (
            EvidenceKind.WORK_SAMPLE,
            EvidenceKind.REPOSITORY,
        )


# ---------------------------------------------------------------------------
# Probe phrasing
# ---------------------------------------------------------------------------

# Specialist phrasing is per role family. This is the concrete difference
# between "a domain expert" and "a technical interviewer with a new label":
# the question a sales interviewer asks about a deal has no software analogue.
_SPECIALIST_PROBES: dict[RoleFamily, tuple[str, ...]] = {
    RoleFamily.SOFTWARE: (
        'On "{anchor}" — what was the alternative you rejected, and what would '
        "have broken if you had taken it?",
        'For "{anchor}", where would that design fail first under load, and how '
        "would you know?",
        'Walk me through how you debugged "{anchor}". What did you check first, '
        "and what ruled the other causes out?",
    ),
    RoleFamily.HARDWARE: (
        'On "{anchor}" — which interface constraint drove that choice, and what '
        "did it cost you elsewhere?",
        'For "{anchor}", how did you verify it actually met timing, and what '
        "did your testbench not cover?",
        'When "{anchor}" failed on the bench, how did you isolate it to that '
        "block rather than the one next to it?",
    ),
    RoleFamily.SALES: (
        'On "{anchor}" — how did you qualify it, and what would have made you '
        "walk away?",
        'For "{anchor}", which objection nearly killed the deal, and what did '
        "you actually say?",
        'How did you forecast "{anchor}"? What were you wrong about, and when '
        "did you know?",
    ),
    RoleFamily.MARKETING: (
        'On "{anchor}" — who exactly were you targeting, and who did you '
        "deliberately exclude?",
        'For "{anchor}", what did you measure, and how did you know the '
        "attribution was not just correlation?",
        'If "{anchor}" had to run on a tenth of the budget, what would you cut '
        "first and why?",
    ),
    RoleFamily.OPERATIONS: (
        'On "{anchor}" — where was the actual bottleneck, and how did you '
        "establish that rather than assume it?",
        'For "{anchor}", what did the process look like before, and what broke '
        "when you changed it?",
        'How did you plan capacity for "{anchor}", and what happened when '
        "demand moved?",
    ),
    RoleFamily.FINANCE: (
        'On "{anchor}" — which assumption was the model most sensitive to, and '
        "how did you test it?",
        'For "{anchor}", what would have changed your recommendation?',
        'Walk me through how you interpreted "{anchor}" for someone who '
        "disagreed with the conclusion.",
    ),
    RoleFamily.GENERIC: (
        'On "{anchor}" — what was the hardest judgement call, and what were the '
        "options you weighed?",
        'For "{anchor}", how did you check the approach was working?',
        'What would you do differently on "{anchor}" now, and why?',
    ),
}

_HR_PROBES: tuple[str, ...] = (
    'You mentioned "{anchor}". What drew you to that, and how does it fit where '
    "you want to go next?",
    'Help me understand the move around "{anchor}" — what were you looking for '
    "that you were not getting?",
    'You said "{anchor}". What does that tell you about the kind of work you do '
    "your best in?",
)

_HR_CONSISTENCY_PROBE = (
    'Earlier you said "{first}", and just now "{second}". Help me square those '
    "— what am I missing?"
)

_HR_ROLE_UNDERSTANDING_PROBE = (
    "From what you know of this role, what do you think the day-to-day actually "
    "looks like — and which part are you least sure about?"
)

_MANAGER_PROBES: tuple[str, ...] = (
    'On "{anchor}" — what did you personally decide, and what happened as a '
    "result?",
    'For "{anchor}", who disagreed with you, and how did you handle that?',
    'When "{anchor}" did not go the way you expected, what did you change?',
)

_MANAGER_LEADERSHIP_PROBE = (
    'On "{anchor}" — how did you get people who did not report to you to go '
    "along with it?"
)


@dataclass(frozen=True)
class RoleSpec:
    """
    One interviewer role: purpose, scope, strategy, depth, rubric.

    Frozen because a round's identity must not drift mid-interview; the
    coordinator swaps specs, it never mutates one.
    """

    round: InterviewRound
    persona: str
    label: str
    voice: str
    purpose: str
    # Pack competency ids this role may probe. The guard already refuses
    # out-of-scope targets; this keeps a role from straying inside scope.
    competencies: tuple[str, ...]
    # Rubric this round's findings score against (see evaluation/rubrics.py).
    rubric_id: str
    # Hard ceiling on follow-up depth for this role, intersected with the pack
    # and intensity caps. HR is shallow on purpose.
    max_probe_depth: int
    # Evidence kinds this role treats as germane. HR does not interrogate a
    # repository; the specialist does not probe company context.
    evidence_kinds: tuple[EvidenceKind, ...]
    # Instructions for the model path. Kept here so the deterministic and
    # model-driven proposers cannot disagree about a role's purpose.
    instructions: str
    # Share of a Full Interview's time budget. Sums to 1.0 across the three.
    time_share: float
    probe_templates: tuple[str, ...] = ()
    family: RoleFamily | None = None

    def probe_text(self, *, anchor: str, index: int = 0) -> str:
        templates = self.probe_templates or _SPECIALIST_PROBES[RoleFamily.GENERIC]
        template = templates[index % len(templates)]
        return template.format(anchor=anchor.strip().rstrip("."))


# ---------------------------------------------------------------------------
# The three roles
# ---------------------------------------------------------------------------

_HR_INSTRUCTIONS = """\
You are a recruiter conducting a first-round screen. Your job is to understand \
the candidate's background, career direction, motivation, and their read on \
this role. You are not assessing technical ability and must not try.

Do:
- Follow the career story. Ask what drew them to a move, not whether it was wise.
- Treat career changes, breaks and gaps as facts to understand, never as faults.
- Ask why this role and why this organisation, using only company information \
the candidate or the job description supplied.
- Ask about practical matters (availability, location, notice) only if the \
supplied context makes them relevant, and treat the answers as logistics.

Never:
- Invent facts about the company. If you do not have it, ask the candidate.
- Score a career gap, an employment status, or a practical preference.
- Probe a technical design decision. That is the specialist's round.
"""

_MANAGER_INSTRUCTIONS = """\
You are the hiring manager for this role. Your job is to understand how the \
candidate works: what they owned, how they decided, how they collaborated, and \
what they did when things went wrong.

Do:
- Push for what *they personally* did, not what the team did.
- Ask why they chose an approach, and what actually happened afterwards.
- Connect every question to the target role, its seniority, and the resume or \
job description in front of you.
- Accept qualitative outcomes. If no number exists, ask how they knew it worked.
- Ask about leading others only when the role's seniority calls for it.

Never:
- Accept "we decided" as an answer about ownership without asking their part.
- Treat the absence of a metric as the absence of impact.
- Interrogate domain internals. That is the specialist's round.
"""

_SPECIALIST_INSTRUCTIONS = """\
You are a senior practitioner in {family_label}, assessing whether the \
candidate can actually do the work. Your job is depth: real reasoning, real \
tradeoffs, real debugging of their own stated work.

Do:
- Ground every question in something the candidate said or supplied. Probe the \
claim, not a textbook topic.
- Ask what the alternative was and why they rejected it.
- Use a concrete practical scenario when no work sample exists.
- Go deeper when an answer holds; change target when it collapses.

Never:
- Ask a trivia question with one right answer.
- Treat the absence of a public repository as evidence about ability.
- Assume the candidate wrote code they did not claim to write.
"""


def hr_role() -> RoleSpec:
    return RoleSpec(
        round=InterviewRound.HR,
        persona="recruiter",
        label="Recruiter",
        voice="aura-2-vesta-en",
        purpose="Background, career direction, motivation, understanding of the role.",
        competencies=("career_story", "role_understanding", "motivation", "communication"),
        rubric_id="hr.v1",
        # Two levels. Pressing someone on a career break four levels deep is
        # interrogation; the round's job is to understand, not to stress-test.
        max_probe_depth=2,
        evidence_kinds=(
            EvidenceKind.CANDIDATE_ASSERTION,
            EvidenceKind.JOB_DESCRIPTION,
            EvidenceKind.COMPANY_CONTEXT,
        ),
        instructions=_HR_INSTRUCTIONS,
        time_share=0.25,
        probe_templates=_HR_PROBES,
    )


def hiring_manager_role(seniority: Seniority = Seniority.MID) -> RoleSpec:
    templates = _MANAGER_PROBES
    if seniority.expects_leadership:
        templates = templates + (_MANAGER_LEADERSHIP_PROBE,)
    return RoleSpec(
        round=InterviewRound.HIRING_MANAGER,
        persona="hiring_manager",
        label="Hiring manager",
        voice="aura-2-apollo-en",
        purpose="Ownership, judgement, collaboration, impact, and learning from setbacks.",
        competencies=("ownership", "judgement", "collaboration", "impact", "reflection"),
        rubric_id="manager.v1",
        max_probe_depth=3,
        evidence_kinds=(
            EvidenceKind.CANDIDATE_ASSERTION,
            EvidenceKind.WORK_SAMPLE,
            EvidenceKind.JOB_DESCRIPTION,
        ),
        instructions=_MANAGER_INSTRUCTIONS,
        time_share=0.35,
        probe_templates=templates,
    )


def domain_specialist_role(family: RoleFamily = RoleFamily.GENERIC) -> RoleSpec:
    return RoleSpec(
        round=InterviewRound.DOMAIN_SPECIALIST,
        persona="specialist",
        label=f"{family.label} specialist",
        voice="aura-2-orpheus-en",
        purpose=f"Practical {family.label.lower()} knowledge, application, and tradeoffs.",
        competencies=("domain_knowledge", "practical_application", "tradeoff_reasoning"),
        rubric_id=f"specialist.{family.value}.v1",
        # The deepest round: claim interrogation is the point of the product and
        # this is where it belongs.
        max_probe_depth=4,
        evidence_kinds=(
            EvidenceKind.CANDIDATE_ASSERTION,
            EvidenceKind.WORK_SAMPLE,
            EvidenceKind.REPOSITORY,
        ),
        instructions=_SPECIALIST_INSTRUCTIONS.format(family_label=family.label),
        time_share=0.40,
        probe_templates=_SPECIALIST_PROBES[family],
        family=family,
    )


def role_for(
    round_: InterviewRound,
    *,
    family: RoleFamily = RoleFamily.GENERIC,
    seniority: Seniority = Seniority.MID,
) -> RoleSpec:
    """The spec for one round. `FULL` is a sequence, not a role."""
    if round_ is InterviewRound.HR:
        return hr_role()
    if round_ is InterviewRound.HIRING_MANAGER:
        return hiring_manager_role(seniority)
    if round_ is InterviewRound.DOMAIN_SPECIALIST:
        return domain_specialist_role(family)
    raise ValueError(
        "InterviewRound.FULL is a sequence of rounds; ask for one round at a time "
        "(see FULL_INTERVIEW_ORDER)."
    )


def rounds_for(round_: InterviewRound) -> tuple[InterviewRound, ...]:
    """Which rounds a selection actually runs."""
    return FULL_INTERVIEW_ORDER if round_ is InterviewRound.FULL else (round_,)


def specs_for(
    round_: InterviewRound,
    *,
    family: RoleFamily = RoleFamily.GENERIC,
    seniority: Seniority = Seniority.MID,
) -> tuple[RoleSpec, ...]:
    return tuple(
        role_for(item, family=family, seniority=seniority)
        for item in rounds_for(round_)
    )


def pack_id_for(
    round_: InterviewRound, family: RoleFamily = RoleFamily.GENERIC
) -> str:
    """
    Which pack supplies a round's spine.

    One pack per round, because a round has its own spine, competencies, time
    budget and rubric — exactly what a pack is. The specialist pack additionally
    varies by profession.
    """
    if round_ is InterviewRound.HR:
        return "hr-core"
    if round_ is InterviewRound.HIRING_MANAGER:
        return "manager-core"
    if round_ is InterviewRound.DOMAIN_SPECIALIST:
        return f"specialist-{family.value}"
    raise ValueError("FULL has one pack per round; call per round")


def time_allocation(
    round_: InterviewRound, total_seconds: float
) -> dict[InterviewRound, float]:
    """
    Split a Full Interview's budget across its rounds.

    Shares are normalised over the rounds actually selected, so a single-round
    session gets the whole budget rather than 25% of it — the bug that would
    otherwise end an HR-only interview a quarter of the way in.
    """
    selected = rounds_for(round_)
    specs = {item: role_for(item) for item in selected}
    total_share = sum(spec.time_share for spec in specs.values())
    return {
        item: total_seconds * (specs[item].time_share / total_share)
        for item in selected
    }


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateStatement:
    """Something the candidate actually said, with where to find it."""

    turn_id: str
    quote: str
    topic: str


@dataclass
class HandoffBrief:
    """
    Bounded, source-linked context passed from one round to the next.

    Deliberately *not* a shared blackboard: it is built once when a round ends,
    frozen into the coordinator's state, and handed to the next round read-only.
    Nothing downstream can write back into it.

    It carries candidate statements (quoted, with turn ids) separately from
    interviewer notes, and carries no ratings, scores or personality judgements
    — a later round must not inherit an earlier round's opinion of the person.
    """

    from_round: InterviewRound
    topics_covered: tuple[str, ...] = ()
    statements: tuple[CandidateStatement, ...] = ()
    unresolved: tuple[str, ...] = ()
    opportunities: tuple[str, ...] = ()
    # Questions already asked, so the next round does not repeat them verbatim.
    questions_asked: tuple[str, ...] = ()

    def as_context(self) -> dict:
        """Read-only shape handed to the next round's proposer."""
        return {
            "from_round": self.from_round.value,
            "topics_covered": list(self.topics_covered),
            "candidate_statements": [
                {"turn_id": s.turn_id, "quote": s.quote, "topic": s.topic}
                for s in self.statements
            ],
            "unresolved": list(self.unresolved),
            "opportunities": list(self.opportunities),
            "questions_asked": list(self.questions_asked),
        }


FORBIDDEN_HANDOFF_KEYS = frozenset(
    {"score", "scores", "rating", "ratings", "verdict", "recommendation",
     "personality", "impression", "confidence_score", "hire"}
)


def validate_handoff(payload: dict) -> None:
    """
    Refuse a handoff that smuggles a judgement into the next round.

    Enforced rather than documented: a rating passed forward would make the
    second interviewer's questions a function of the first one's opinion, which
    is how a single bad early read becomes the whole interview.
    """
    for key in payload:
        if key.casefold() in FORBIDDEN_HANDOFF_KEYS:
            raise ValueError(
                f"handoff may not carry {key!r}: ratings and judgements do not "
                "cross rounds, only statements and open questions"
            )


__all__ = [
    "CandidateStatement",
    "EvidenceKind",
    "FULL_INTERVIEW_ORDER",
    "HandoffBrief",
    "InterviewRound",
    "RoleFamily",
    "RoleSpec",
    "Seniority",
    "domain_specialist_role",
    "hiring_manager_role",
    "hr_role",
    "pack_id_for",
    "role_for",
    "rounds_for",
    "specs_for",
    "time_allocation",
    "validate_handoff",
]
