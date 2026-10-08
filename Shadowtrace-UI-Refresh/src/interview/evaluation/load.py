"""Load a transcript, a claims file, and optional resume or repo text as data."""

from __future__ import annotations

import json
from pathlib import Path

from interview.evaluation.schema import EvalClaim, Turn
from interview.intake.sanitize import sanitize_text


def load_turns(path: Path) -> list[Turn]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "utterances" in raw:
        turns: list[Turn] = []
        for item in raw["utterances"]:
            revisions = item.get("revisions") or []
            if revisions:
                text = str(revisions[-1].get("text", "")).strip()
            else:
                text = " ".join(word.get("word", "") for word in item.get("words", [])).strip()
            if not text:
                continue
            turns.append(Turn(turn_id=str(item.get("id", f"u{len(turns)}")), speaker="candidate", text=text))
        return turns
    if not isinstance(raw, list):
        raise ValueError(f"transcript must be a list or an utterances object: {path}")
    turns = []
    for index, item in enumerate(raw):
        speaker = item.get("speaker", "candidate")
        if speaker not in ("candidate", "agent"):
            speaker = "candidate"
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        turns.append(
            Turn(
                turn_id=str(item.get("turn_id") or f"turn-{index}"),
                speaker=speaker,
                text=text,
            )
        )
    return turns


def load_claims(path: Path | None) -> list[EvalClaim]:
    if path is None or not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("claims", raw if isinstance(raw, list) else [])
    claims: list[EvalClaim] = []
    for row in rows:
        claims.append(
            EvalClaim(
                id=str(row["id"]),
                text=str(row.get("text", "")),
                competency=str(row.get("competency", "")),
            )
        )
    return claims


def delimited_data(*paths: Path | None) -> str:
    """
    Resume and repo text, sanitized and wrapped so a model treats it as data.

    Instruction-like lines are dropped before the wrapper is built.
    """
    blocks: list[str] = []
    for path in paths:
        if path is None or not path.is_file():
            continue
        cleaned = sanitize_text(path.read_text(encoding="utf-8"))
        if not cleaned:
            continue
        blocks.append(f"<<<DATA {path.name}>>>\n{cleaned}\n<<<END>>>")
    return "\n".join(blocks)
