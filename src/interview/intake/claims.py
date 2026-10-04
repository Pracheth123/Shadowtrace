"""
Build a Claims File of at most 8 interrogable claims.

A claim is kept only when its text appears in the resume or in a file the
indexer actually read. Proposed text that is not in those sources is dropped.
"""

from __future__ import annotations

import re

from interview.intake.repo_indexer import Evidence
from interview.intake.schema import Claim, ClaimsFile, ResumeProfile

_COMPETENCY = (
    ("test", "quality"),
    ("deploy", "delivery"),
    ("pipeline", "systems"),
    ("api", "systems"),
    ("database", "systems"),
    ("postgres", "systems"),
    ("flask", "systems"),
    ("design", "architecture"),
)


# Role competencies (see session/roles.py). A claim is tagged with the round
# competency it is most naturally probed under, so the HR, hiring-manager and
# specialist proposers each find the claims that belong to their round. Ordered:
# the first matching cue wins.
_ROLE_COMPETENCY_CUES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("moved to", "transitioned", "career", "switched", "joined", "returned to",
      "looking for", "i want to", "passionate"), "career_story"),
    (("led ", "owned", "managed", "responsible for", "drove", "spearheaded",
      "took ownership", "decided"), "ownership"),
    (("team", "collaborat", "stakeholder", "cross-functional", "partnered",
      "mentored", "coordinated"), "collaboration"),
    (("increased", "reduced", "grew", "improved", "saved", "cut ", "%",
      "revenue", "quota", "conversion", "latency"), "impact"),
    (("trade-off", "tradeoff", "chose", "instead of", "versus", " vs "),
     "tradeoff_reasoning"),
    (("built", "designed", "implemented", "developed", "created", "launched",
      "shipped", "wrote", "migrated", "negotiated", "closed", "ran ",
      "modelled", "modeled", "forecast", "verified", "automated"),
     "practical_application"),
)


def role_competency_for(text: str) -> str:
    """The round competency a claim is probed under; domain knowledge by default."""
    folded = f" {text.casefold()} "
    for cues, competency in _ROLE_COMPETENCY_CUES:
        if any(cue in folded for cue in cues):
            return competency
    return "domain_knowledge"


def competency_for(text: str) -> str:
    folded = text.casefold()
    for needle, name in _COMPETENCY:
        if needle in folded:
            return name
    return "general"


def _flat(text: str) -> str:
    return " ".join(text.split()).casefold()


def appears_in(text: str, corpus: str) -> bool:
    """True when `text` appears in `corpus`, ignoring line-wrap whitespace."""
    needle = _flat(text)
    return len(needle) >= 20 and needle in _flat(corpus)


def ground_claims(candidates: list[Claim], corpus: str) -> list[Claim]:
    """Drop any claim whose text is not a verbatim span of `corpus`."""
    kept: list[Claim] = []
    seen: set[str] = set()
    for claim in candidates:
        key = _flat(claim.text)
        if key in seen or not appears_in(claim.text, corpus):
            continue
        seen.add(key)
        kept.append(claim)
        if len(kept) >= 8:
            break
    return kept


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    kept: list[str] = []
    for part in parts:
        sentence = " ".join(part.split())
        words = sentence.split()
        if len(words) < 6 or len(sentence) < 20:
            continue
        if sentence.startswith("[") or sentence.startswith("!"):
            continue
        kept.append(sentence)
    return kept


def claims_from_resume(profile: ResumeProfile, resume_text: str) -> list[Claim]:
    found: list[Claim] = []
    blobs = [project.description for project in profile.projects if project.description]
    n = 0
    for blob in blobs:
        for sentence in _sentences(blob):
            if not appears_in(sentence, resume_text):
                continue
            n += 1
            found.append(
                Claim(
                    id=f"c-resume-{n}",
                    text=sentence,
                    competency=role_competency_for(sentence),
                    source="resume",
                    source_path="resume",
                    quote=sentence,
                    evidence_kind="candidate_assertion",
                )
            )
    return found


# A sentence worth interrogating says the candidate *did* something.
_ACTION = re.compile(
    r"\b(i|we|led|owned|managed|built|designed|implemented|developed|created|"
    r"launched|shipped|wrote|migrated|negotiated|closed|ran|reduced|increased|"
    r"grew|improved|automated|delivered|drove|coordinated|analy[sz]ed|"
    r"modell?ed|forecast|planned|trained|mentored)\b",
    re.I,
)


def claims_from_background(
    text: str,
    *,
    prefix: str = "c-bg",
    source: str = "background",
    evidence_kind: str = "candidate_assertion",
) -> list[Claim]:
    """
    Claims from free text with no Projects/Experience headings.

    A written background (or a resume whose sections the parser did not
    recognise) still contains interrogable statements. Only sentences that
    describe something the candidate did are kept, and each is a verbatim span
    of the text, so grounding is unchanged.
    """
    found: list[Claim] = []
    n = 0
    for sentence in _sentences(_prose(text)):
        if not _ACTION.search(sentence) or "@" in sentence:
            continue
        n += 1
        found.append(
            Claim(
                id=f"{prefix}-{n}",
                text=sentence,
                competency=role_competency_for(sentence),
                source=source,
                source_path=source,
                quote=sentence,
                evidence_kind=evidence_kind,
            )
        )
    return found


def _prose(text: str) -> str:
    """Drop headings, images, and rules so a sentence starts at a real line."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", "!", "---", "===")):
            continue
        if set(stripped) <= set("-_=* "):
            continue
        lines.append(stripped)
    return " ".join(lines)


def claims_from_repo(evidence: list[Evidence]) -> list[Claim]:
    found: list[Claim] = []
    n = 0
    for item in evidence:
        for sentence in _sentences(_prose(item.text)):
            if not appears_in(sentence, item.text):
                continue
            n += 1
            found.append(
                Claim(
                    id=f"c-repo-{n}",
                    text=sentence,
                    competency=role_competency_for(sentence),
                    source="repo",
                    source_path=item.path,
                    quote=sentence,
                    # The repository shows this material exists. It does not
                    # show who wrote it; ownership is established in conversation.
                    evidence_kind="repository",
                )
            )
    return found


def _interleave(*groups: list[Claim]) -> list[Claim]:
    """Round-robin across sources so one long source cannot fill all 8 slots."""
    out: list[Claim] = []
    longest = max((len(group) for group in groups), default=0)
    for index in range(longest):
        for group in groups:
            if index < len(group):
                out.append(group[index])
    return out


def build_claims(
    profile: ResumeProfile,
    resume_text: str,
    evidence: list[Evidence],
    *,
    extra: list[Claim] | None = None,
    work_sample_text: str = "",
) -> ClaimsFile:
    """
    Candidate assertions first, then corroborating material, then extras.

    Resume project claims come first; when the resume has no recognisable
    project section the whole background is read for action statements
    instead. Repository and work-sample claims follow, interleaved so each
    evidence source is represented. Extras not in the corpus are dropped.
    """
    corpus_parts = [resume_text, work_sample_text]
    corpus_parts.extend(item.text for item in evidence)
    corpus = "\n".join(corpus_parts)
    assertions = claims_from_resume(profile, resume_text)
    if len(assertions) < 3:
        seen = {_flat(claim.text) for claim in assertions}
        assertions.extend(
            claim
            for claim in claims_from_background(resume_text)
            if _flat(claim.text) not in seen
        )
    corroborating = _interleave(
        claims_from_repo(evidence),
        claims_from_background(
            work_sample_text,
            prefix="c-sample",
            source="work_sample",
            evidence_kind="work_sample",
        )
        if work_sample_text
        else [],
    )
    # Up to five candidate assertions first, so corroborating evidence still
    # has room within the 8-claim cap.
    ordered = assertions[:5] + corroborating + assertions[5:]
    if extra:
        ordered.extend(extra)
    return ClaimsFile(claims=ground_claims(ordered, corpus))
