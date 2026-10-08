"""
Hardness for one voice.

`panel` is a depth setting only. A second interviewer voice is stage 11.
Distress may step down one level. The floor is still the spine and the claims.
"""

from __future__ import annotations

import re

Level = str

_ORDER = ("coach", "realistic", "panel")

# Coach explains. Realistic and panel do not add a hint, and panel may cut in.
# Cutting in is a single voice following up, not a second persona.
_HINT = " If you want a hint: name the decision, then what you measured."
_NEUTRAL = "Let's stay with the question."
_RUDE = re.compile(
    r"(?i)\b(idiot|stupid|shut up|worthless|pathetic|dumb|moron)\b"
)


def normalize(level: str) -> str:
    if level in _ORDER:
        return level
    return "realistic"


def step_down(level: str) -> str:
    current = normalize(level)
    index = _ORDER.index(current)
    return _ORDER[max(0, index - 1)]


def step_up(level: str) -> str:
    current = normalize(level)
    index = _ORDER.index(current)
    return _ORDER[min(len(_ORDER) - 1, index + 1)]


def allows_interruption(level: str) -> bool:
    """Coach waits. Realistic and panel may follow up without a second voice."""
    return normalize(level) != "coach"


def with_hint(level: str, text: str) -> str:
    if normalize(level) != "coach":
        return text
    if "if you want a hint" in text.casefold():
        return text
    return text.rstrip() + _HINT


def apply_tone(text: str) -> tuple[str, bool]:
    """Every spoken line passes through here. A rude line is replaced."""
    if _RUDE.search(text):
        return _NEUTRAL, True
    return text, False
