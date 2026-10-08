"""
Targeted practice — "Practise this gap".

A practice is derived on the server from **stored records only**: the
candidate names a session and a finding id, and everything else — the round,
rubric, dimension, the question and answer it rests on, role and seniority,
the evaluator that produced it — is read from that session's own report,
transcript and intake. Nothing the browser sends about scores or findings is
trusted, and ownership follows from the authenticated candidate's directory.

The attempt itself runs through the ordinary interview runtime: the same
coordinator, guard, proposer, voice and text paths, with a dedicated short
practice pack for one round (two core questions on the selected competency,
follow-up depth capped at 2). Its rubric is the round's normal rubric, so the
attempt is evaluated against the same target rubric by the same pipeline.

Comparison is deliberately narrow: only the selected dimension, before vs
after, with the quoted evidence for both, and one of four outcomes —

  - `clearer`                 the selected dimension was assessed higher;
  - `no_clear_improvement`    assessed, not higher;
  - `insufficient_evidence`   the attempt did not establish the dimension;
  - `unavailable`             a dispute, a rubric change or an incompatible
                              evaluator makes the comparison meaningless.

It never infers general readiness from one improved answer, never subtracts
overall scores, and labels a coached attempt (the candidate saw a checklist).
An optional later *unaided variation* asks a differently worded question with
no checklist, to check transfer rather than memorisation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from interview.candidates import CandidateRegistry, new_id, read_json, valid_id, write_json
from interview.packs.model import Pack, ProbePolicy, SpineItem, load_pack
from interview.services.feedback import EXCLUDING, find_finding, level_rank, read_disputes

PRACTICE_MINUTES = (3, 5, 8, 10)
PRACTICE_SCHEMA = "practice.v1"
MAX_PRACTICE_DEPTH = 2

# What to explain, per rubric dimension kind. Prompts only — no facts.
_KIND_PROMPTS = {
    "reasoning": [
        "Name the options you had and the one you chose.",
        "Say what you rejected and why.",
        "Say how you knew afterwards whether it was the right call.",
    ],
    "evidence": [
        "Separate what you personally did from what the team did.",
        "Name one decision that was yours.",
        "Say how you knew the outcome — a number if you have one, otherwise what you observed.",
    ],
    "knowledge": [
        "Name the method or tool you used, in your own words.",
        "Say why it fitted this problem better than the obvious alternative.",
        "Say how you checked it worked.",
    ],
    "communication": [
        "Lead with the outcome in one sentence.",
        "Then the two or three steps that got you there.",
        "Stop when you have answered the question.",
    ],
}

# The unaided variation: a different question on the same competency.
_VARIATION_QUESTIONS = {
    "reasoning": (
        "Tell me about a different decision where you had more than one reasonable "
        "option. What did you choose, and why?"
    ),
    "evidence": (
        "Pick a different piece of work. What exactly was your part, as distinct "
        "from the team's?"
    ),
    "knowledge": (
        "Walk me through a different problem in your field that you solved. What "
        "method did you use, and how did you check it worked?"
    ),
    "communication": (
        "In about two minutes, explain a different piece of work you are proud of, "
        "starting with the outcome."
    ),
}

_FOLLOW_THROUGH = {
    "reasoning": "What was the strongest alternative, and what made you reject it?",
    "evidence": "Which part of that would not have happened without you?",
    "knowledge": "How did you verify that it was correct?",
    "communication": "Summarise that answer in three sentences.",
}


class PracticeError(ValueError):
    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    from interview.candidates import utc_now

    return utc_now()


def practice_dir(registry: CandidateRegistry, candidate_id: str, practice_id: str) -> Path:
    if not valid_id(practice_id):
        from interview.candidates import NotFound

        raise NotFound("invalid practice id")
    return registry.candidate_dir(candidate_id) / "practice" / practice_id


def last_question(text: str) -> str:
    """The question an interviewer line asked, without its greeting."""
    sentences = [s.strip() for s in re.findall(r"[^.!?]+[.!?]?", text or "") if s.strip()]
    questions = [s for s in sentences if s.endswith("?")]
    if questions:
        return questions[-1]
    return sentences[-1] if sentences else ""


def _dimension(report: dict, round_id: str, dimension_id: str) -> dict:
    for result in report.get("rounds", []):
        if result.get("round") != round_id:
            continue
        for dim in result.get("dimensions", []):
            if dim.get("dimension_id") == dimension_id:
                return dim
    return {}


def build_checklist(
    *, kind: str, quote: str, claims: list[dict], competencies: set[str], evaluator_practice: str
) -> list[dict]:
    """
    What to explain, using only what the candidate already supplied.

    Facts come from two places only: the candidate's own quoted answer and
    statements from their own background. Everything else is a prompt about
    *how* to explain, not *what* happened. Nothing is invented.
    """
    items: list[dict] = []
    if quote:
        items.append({"kind": "your_words", "text": f"Earlier you said: “{quote}”", "source": "your answer"})
    relevant = [c for c in claims if c.get("competency") in competencies][:2]
    for claim in relevant:
        source = (
            "your correction"
            if claim.get("evidence_kind") == "candidate_correction"
            else "your background"
        )
        items.append({"kind": "your_background", "text": f"From {source}: “{claim.get('text')}”", "source": source})
    for prompt in _KIND_PROMPTS.get(kind, _KIND_PROMPTS["evidence"]):
        items.append({"kind": "prompt", "text": prompt, "source": "practice guide"})
    if evaluator_practice:
        items.append(
            {"kind": "suggestion", "text": evaluator_practice, "source": "suggested by the evaluator"}
        )
    return items


def practice_pack(record: dict, parent_pack: Pack) -> Pack:
    """A short pack for one practice attempt, carrying the round's own rubric."""
    kind = record["dimension_kind"]
    competency = record.get("competency") or parent_pack.competencies[0].id
    if record["mode"] == "unaided":
        first = _VARIATION_QUESTIONS.get(kind, _VARIATION_QUESTIONS["evidence"])
    else:
        question = record.get("source_question") or ""
        first = (
            f"Let's go back to one question from your interview. {question}"
            if question
            else _VARIATION_QUESTIONS.get(kind, _VARIATION_QUESTIONS["evidence"])
        )
    second = _FOLLOW_THROUGH.get(kind, _FOLLOW_THROUGH["evidence"])
    seconds = float(record["minutes"]) * 60.0
    return Pack(
        pack_id=f"practice-{record['round']}-{record['dimension_id']}",
        title=f"Practice: {record['dimension_label']}",
        round=record["round"],
        role_family=parent_pack.role_family,
        time_budget_s=seconds,
        spine=[
            SpineItem(id="practice-q1", text=first, competency=competency),
            SpineItem(id="practice-q2", text=second, competency=competency),
        ],
        competencies=parent_pack.competencies,
        probe_policy=ProbePolicy(
            max_depth=min(MAX_PRACTICE_DEPTH, parent_pack.probe_policy.max_depth),
            seconds_per_spine=max(20.0, seconds / 4),
            min_probe_s=20.0,
        ),
        rubric=parent_pack.rubric,
    )


def create_practice(
    registry: CandidateRegistry,
    candidate_id: str,
    *,
    session_id: str,
    finding_id: str,
    minutes: int = 5,
    mode: str = "coached",
    parent_practice_id: str | None = None,
) -> dict:
    """Validate ownership and eligibility, then persist a practice record."""
    from interview.services.intake import accepted_claims

    if minutes not in PRACTICE_MINUTES:
        raise PracticeError(f"Choose a practice length of {', '.join(map(str, PRACTICE_MINUTES))} minutes.", status=422)
    if mode not in ("coached", "unaided"):
        raise PracticeError("Unknown practice mode.", status=422)
    directory = registry.existing_session(candidate_id, session_id)  # ownership
    meta = read_json(directory / "meta.json", {}) or {}
    if meta.get("kind") == "practice":
        raise PracticeError("Practise from an interview report, not from a practice attempt.")
    if meta.get("state") != "complete":
        raise PracticeError("The report for that session is not complete yet.")
    report = read_json(directory / "evaluation" / "report.json", None)
    if report is None:
        raise PracticeError("The report file is missing.", status=404)
    located = find_finding(report, finding_id)
    if located is None:
        raise PracticeError("That finding is not in this report.", status=404)
    result, finding = located
    if finding.get("polarity") != "gap" or not finding.get("eligible_for_practice"):
        raise PracticeError("Only an assessed gap with a quoted answer can be practised.")
    dispute = read_disputes(directory).get(finding_id)
    if dispute and dispute.get("status") in EXCLUDING:
        raise PracticeError(
            "You disputed this finding, so it is not offered for practice until you "
            "withdraw the dispute."
        )
    intake_id = meta.get("intake_id")
    if not intake_id:
        raise PracticeError("This session was not built from an intake, so it cannot seed a practice.")
    intake_dir = registry.existing_intake(candidate_id, intake_id)
    rubric_pack_id = next(
        (
            r.get("rubric_pack_id") or r.get("pack_id")
            for r in meta.get("rounds") or []
            if r.get("round") == result.get("round")
        ),
        result.get("pack_id"),
    )
    parent_pack = load_pack(str(rubric_pack_id))
    rubric = parent_pack.rubric
    if rubric is None or rubric.version != result.get("rubric_version"):
        raise PracticeError(
            "The rubric for that round has changed since the report, so a practice "
            "attempt could not be compared with it."
        )
    dimension = next(d for d in rubric.dimensions if d.id == finding["dimension_id"])
    transcript = read_json(directory / "transcript.json", []) or []
    question_text = finding.get("question") or next(
        (
            e.get("text", "")
            for e in transcript
            if e.get("turn_id") == finding.get("question_turn_id") and e.get("speaker") == "agent"
        ),
        "",
    )
    competency = next(
        (
            s.competency
            for s in parent_pack.spine
            if s.text and s.text in (question_text or "")
        ),
        parent_pack.competencies[0].id,
    )
    competencies = {c.id for c in parent_pack.competencies}
    parent_dim = _dimension(report, result["round"], finding["dimension_id"])
    practice_id = new_id()
    record: dict[str, Any] = {
        "schema": PRACTICE_SCHEMA,
        "practice_id": practice_id,
        "created_at": _now(),
        "status": "ready",
        "mode": mode,
        "parent_practice_id": parent_practice_id,
        "coached": False,
        "coaching_shown_at": None,
        "minutes": minutes,
        "parent": {
            "session_id": session_id,
            "intake_id": intake_id,
            "finding_id": finding_id,
            "report_schema": report.get("schema_version"),
            "report_created_at": report.get("created_at"),
        },
        "round": result["round"],
        "round_label": result.get("label"),
        "pack_id": parent_pack.pack_id,
        "rubric_version": rubric.version,
        "dimension_id": dimension.id,
        "dimension_label": dimension.label,
        "dimension_kind": dimension.kind,
        "competency": competency,
        "source_turn_ids": [t for t in (finding.get("question_turn_id"), finding.get("turn_id")) if t],
        "source_question": last_question(question_text),
        "source_quote": finding.get("quote", ""),
        "finding_explanation": finding.get("explanation", ""),
        "role": {
            key: (meta.get("config") or {}).get(key)
            for key in ("target_role", "role_family", "seniority")
        },
        "evaluator": {
            "provider": (report.get("evaluator") or {}).get("provider"),
            "model": (report.get("evaluator") or {}).get("model"),
            "round_model_used": (result.get("evaluation_meta") or {}).get("model_used"),
            "round_fallback_used": bool((result.get("evaluation_meta") or {}).get("fallback_used")),
        },
        "before": {
            "level": parent_dim.get("level"),
            "assessed": parent_dim.get("assessed"),
            "citations": parent_dim.get("citations", [])[:2],
            "quote": finding.get("quote"),
            "question": last_question(question_text),
        },
        "checklist": build_checklist(
            kind=dimension.kind,
            quote=finding.get("quote", ""),
            claims=accepted_claims(intake_dir),
            competencies=competencies,
            evaluator_practice=finding.get("practice", ""),
        )
        if mode == "coached"
        else [],
        "attempts": [],
    }
    write_json(practice_dir(registry, candidate_id, practice_id) / "practice.json", record)
    return record


def mark_coaching_shown(registry: CandidateRegistry, candidate_id: str, practice_id: str) -> dict:
    """The candidate opened the checklist: the next attempt is labelled coached."""
    path = practice_dir(registry, candidate_id, practice_id) / "practice.json"
    record = read_json(path, None)
    if record is None:
        from interview.candidates import NotFound

        raise NotFound("no such practice")
    if record["mode"] == "unaided":
        raise PracticeError("An unaided variation has no checklist, so its result checks transfer.")
    if not record.get("coached"):
        record["coached"] = True
        record["coaching_shown_at"] = _now()
        write_json(path, record)
    return record


def load_practice(registry: CandidateRegistry, candidate_id: str, practice_id: str) -> dict:
    path = practice_dir(registry, candidate_id, practice_id) / "practice.json"
    record = read_json(path, None)
    if record is None:
        from interview.candidates import NotFound

        raise NotFound("no such practice")
    return record


def note_attempt(registry: CandidateRegistry, candidate_id: str, practice_id: str, *, session_id: str, lane: str) -> dict:
    path = practice_dir(registry, candidate_id, practice_id) / "practice.json"
    record = read_json(path, {}) or {}
    record.setdefault("attempts", []).append(
        {
            "session_id": session_id,
            "started_at": _now(),
            "lane": lane,
            "coached": bool(record.get("coached")),
            "mode": record.get("mode"),
        }
    )
    write_json(path, record)
    return record


def list_practices(registry: CandidateRegistry, candidate_id: str) -> list[dict]:
    root = registry.candidate_dir(candidate_id) / "practice"
    if not root.is_dir():
        return []
    out = []
    for directory in root.iterdir():
        record = read_json(directory / "practice.json", None)
        if record:
            out.append(record)
    out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return out


def compare_attempt(registry: CandidateRegistry, candidate_id: str, record: dict, attempt: dict) -> dict:
    """Before/after on the selected dimension only, with an explicit outcome."""
    limitations = [
        "This compares one dimension in one short practice answer. It does not show "
        "general readiness, and overall scores are not compared.",
    ]
    if attempt.get("coached"):
        limitations.append("Coached attempt: you saw a checklist before answering.")
    if record.get("mode") == "unaided":
        limitations.append("Unaided variation: a different question with no checklist, to check transfer.")
    base = {
        "session_id": attempt.get("session_id"),
        "coached": bool(attempt.get("coached")),
        "mode": attempt.get("mode"),
        "lane": attempt.get("lane"),
        "before": record.get("before"),
        "after": None,
        "limitations": limitations,
    }
    try:
        attempt_dir = registry.existing_session(candidate_id, str(attempt.get("session_id")))
    except Exception:  # noqa: BLE001
        return {**base, "outcome": "unavailable", "reason": "The attempt was not found."}
    meta = read_json(attempt_dir / "meta.json", {}) or {}
    if meta.get("state") != "complete":
        state = meta.get("state") or "unknown"
        return {
            **base,
            "outcome": "pending" if state not in ("failed",) else "unavailable",
            "reason": "The attempt is still being evaluated."
            if state not in ("failed",)
            else "The attempt could not be evaluated. Retry it from its results page.",
            "state": state,
        }
    parent_dir = registry.existing_session(candidate_id, record["parent"]["session_id"])
    dispute = read_disputes(parent_dir).get(record["parent"]["finding_id"])
    report = read_json(attempt_dir / "evaluation" / "report.json", {}) or {}
    result = next((r for r in report.get("rounds", []) if r.get("round") == record["round"]), None)
    dim = _dimension(report, record["round"], record["dimension_id"])
    findings = [
        f
        for f in (result or {}).get("findings", [])
        if f.get("dimension_id") == record["dimension_id"]
    ]
    after = {
        "level": dim.get("level"),
        "assessed": dim.get("assessed"),
        "citations": dim.get("citations", [])[:2],
        "findings": [
            {"polarity": f.get("polarity"), "explanation": f.get("explanation"), "quote": f.get("quote")}
            for f in findings[:2]
        ],
    }
    base["after"] = after
    evaluator_after = report.get("evaluator") or {}
    if dispute and dispute.get("status") in EXCLUDING:
        return {**base, "outcome": "unavailable", "reason": "The original finding is disputed, so it is not a fair baseline."}
    if result is None or result.get("rubric_version") != record.get("rubric_version"):
        return {**base, "outcome": "unavailable", "reason": "The rubric changed between the interview and this attempt."}
    if evaluator_after.get("provider") != (record.get("evaluator") or {}).get("provider"):
        return {
            **base,
            "outcome": "unavailable",
            "reason": (
                f"Different evaluators ({(record.get('evaluator') or {}).get('provider')} then "
                f"{evaluator_after.get('provider')}) cannot be compared."
            ),
        }
    before_model = (record.get("evaluator") or {}).get("round_model_used") or (record.get("evaluator") or {}).get("model")
    after_model = ((result or {}).get("evaluation_meta") or {}).get("model_used") or evaluator_after.get("model")
    if before_model and after_model and before_model != after_model:
        limitations.append(
            f"The evaluator model changed ({before_model} → {after_model}); read the change with that in mind."
        )
    if not dim.get("assessed"):
        return {
            **base,
            "outcome": "insufficient_evidence",
            "reason": "The attempt did not give enough to assess this dimension.",
        }
    if level_rank(dim.get("level")) > level_rank((record.get("before") or {}).get("level")):
        return {**base, "outcome": "clearer", "reason": "Clearer explanation of the selected gap in this attempt."}
    return {**base, "outcome": "no_clear_improvement", "reason": "No clear improvement on this dimension in this attempt."}


OUTCOME_LABEL = {
    "clearer": "Clearer explanation of the selected gap",
    "no_clear_improvement": "No clear improvement",
    "insufficient_evidence": "Insufficient evidence to compare",
    "unavailable": "Comparison unavailable",
    "pending": "Still being evaluated",
}


def practice_view(registry: CandidateRegistry, candidate_id: str, record: dict) -> dict:
    comparisons = []
    for attempt in record.get("attempts", []):
        cmp = compare_attempt(registry, candidate_id, record, attempt)
        cmp["outcome_label"] = OUTCOME_LABEL.get(cmp["outcome"], cmp["outcome"])
        comparisons.append(cmp)
    latest = comparisons[-1] if comparisons else None
    can_offer_unaided = (
        record.get("mode") == "coached"
        and latest is not None
        and latest["outcome"] in ("clearer", "no_clear_improvement")
    )
    return {
        **record,
        "comparisons": comparisons,
        "latest_outcome": latest["outcome"] if latest else None,
        "offer_unaided_variation": can_offer_unaided,
        "checklist_visible": record.get("mode") == "coached",
    }


__all__ = [
    "OUTCOME_LABEL",
    "PRACTICE_MINUTES",
    "PracticeError",
    "build_checklist",
    "compare_attempt",
    "create_practice",
    "last_question",
    "list_practices",
    "load_practice",
    "mark_coaching_shown",
    "note_attempt",
    "practice_dir",
    "practice_pack",
    "practice_view",
]
