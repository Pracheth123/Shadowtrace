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
                    competency=competency_for(sentence),
                    source="resume",
                    source_path="resume",
                    quote=sentence,
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
                    competency=competency_for(sentence),
                    source="repo",
                    source_path=item.path,
                    quote=sentence,
                )
            )
    return found


def build_claims(
    profile: ResumeProfile,
    resume_text: str,
    evidence: list[Evidence],
    *,
    extra: list[Claim] | None = None,
) -> ClaimsFile:
    """
    Resume claims first, then repo claims, then any extra proposals.
    Extras that are not in the source corpus are the hallucinated ones.
    """
    corpus_parts = [resume_text]
    corpus_parts.extend(item.text for item in evidence)
    corpus = "\n".join(corpus_parts)
    ordered = claims_from_resume(profile, resume_text)
    ordered.extend(claims_from_repo(evidence))
    if extra:
        ordered.extend(extra)
    return ClaimsFile(claims=ground_claims(ordered, corpus))
