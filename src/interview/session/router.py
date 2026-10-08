"""Route a finished answer in well under 50 ms. No model, no scorer."""

from __future__ import annotations

import re
from typing import Literal

Route = Literal["defended", "conceded", "unclear"]

_CONCEDE = (
    "i don't know",
    "i dont know",
    "not sure",
    "i was wrong",
    "i missed",
    "no idea",
)
_DEFEND = (
    "because",
    "we measured",
    "the reason",
    "i decided",
    "specifically",
    "for example",
)
_WORDS = re.compile(r"\b[\w']+\b")

# The candidate asking to leave the current question. Phrases, not the bare
# word "skip", so "we skipped the cache" in a real answer does not trigger it.
_SKIP = re.compile(
    r"\b("
    r"skip (this|that|it|the question|this question|that question|to the next)"
    r"|(can|could) we skip"
    r"|let'?s skip"
    r"|(i'?d|i would) like to skip"
    r"|next question"
    r"|(let'?s|can we|could we) move on"
    r"|move on to the next"
    r"|pass on this"
    r"|i'?ll pass"
    r")\b"
)
# A skip request is short. A long answer that happens to contain "move on" is
# an answer, and gets the normal follow-up.
_SKIP_MAX_WORDS = 20


def route_answer(text: str) -> Route:
    folded = text.casefold()
    if any(phrase in folded for phrase in _CONCEDE):
        return "conceded"
    words = _WORDS.findall(folded)
    if any(phrase in folded for phrase in _DEFEND) or len(words) >= 12:
        return "defended"
    return "unclear"


def is_skip_request(text: str) -> bool:
    """
    True when the candidate asked to move past the current question.

    Feeds the agent only (contract 8): it stops a follow-up on this question,
    never changes a score, and the guard still decides what is asked next.
    """
    folded = text.casefold().replace("’", "'")
    if len(_WORDS.findall(folded)) > _SKIP_MAX_WORDS:
        return False
    return _SKIP.search(folded) is not None


def concession_line(question: str) -> str:
    body = question.strip()
    if body.lower().startswith("that's a fair gap"):
        return body
    return f"That's a fair gap. {body}"


def draft_is_stale(basis: str, final_text: str) -> bool:
    """
    A speculative draft is valid when the final still matches the partial it
    was built on. A rewrite means one short refresh, not another tool loop.
    """
    base_tokens = _WORDS.findall(basis.casefold())
    final_tokens = _WORDS.findall(final_text.casefold())
    if not base_tokens or not final_tokens:
        return True
    if (
        final_tokens[: len(base_tokens)] == base_tokens
        or base_tokens[: len(final_tokens)] == final_tokens
    ):
        return False
    base_set = set(base_tokens)
    final_set = set(final_tokens)
    overlap = len(base_set & final_set) / max(1, len(base_set | final_set))
    return overlap < 0.55
