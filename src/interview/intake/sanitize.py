"""
Contract 7 — resume and repo text is data, never instructions.

Skill-sync keeps the raw PDF string. Here each line that tries to steer a
model is dropped before the text is stored or turned into a claim.
"""

from __future__ import annotations

import re

# Role-play and override lines only. Ordinary resume sentences stay.
_ATTACK = re.compile(
    r"(?i)("
    r"ignore (all |any |the )?(previous|above|prior) instructions"
    r"|disregard (the )?(above|previous)"
    r"|you are now\b"
    r"|new instructions\s*:"
    r"|system prompt"
    r"|do not tell the (user|candidate|interviewer)"
    r"|<\s*/?\s*(system|assistant|user)\s*>"
    r")"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_line(line: str) -> str | None:
    cleaned = _CONTROL.sub("", line).replace("\u200b", "").replace("\ufeff", "")
    if _ATTACK.search(cleaned):
        return None
    return cleaned


def sanitize_text(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        cleaned = sanitize_line(line)
        if cleaned is None:
            continue
        kept.append(cleaned)
    return "\n".join(kept).strip()


def strip_json_fence(reply: str) -> str:
    """
    Model replies sometimes arrive in a markdown fence.
    Adapted from skill-sync's fence strip — used only on classifier output,
    never on resume or repo text.
    """
    cleaned = re.sub(r"```json", "", reply, flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "").strip()
    start_obj = cleaned.find("{")
    start_arr = cleaned.find("[")
    starts = [i for i in (start_obj, start_arr) if i >= 0]
    if not starts:
        return cleaned
    start = min(starts)
    end_obj = cleaned.rfind("}")
    end_arr = cleaned.rfind("]")
    end = max(end_obj, end_arr)
    if end <= start:
        return cleaned[start:]
    return cleaned[start : end + 1]
