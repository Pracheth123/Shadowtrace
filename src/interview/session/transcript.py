"""
Transcript writer — Stage 4.

Subscribes to final_transcript, agent speak completions, and truncate.
Honours the truncation contract: agent lines are cut at last_heard_word.
Writes transcript.json at session end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TranscriptWriter:
    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        # utterance_id → open agent entry index
        self._open_agent: dict[str, int] = {}
        # utterance_id → full generated text (pre-truncate)
        self._agent_full: dict[str, str] = {}
        self._agent_words: dict[str, list[str]] = {}

    def add_candidate(
        self,
        turn_id: str,
        text: str,
        t_start: float,
        t_end: float,
    ) -> None:
        self._entries.append(
            {
                "speaker": "candidate",
                "turn_id": turn_id,
                "text": text,
                "t_start": t_start,
                "t_end": t_end,
            }
        )

    def begin_agent(
        self,
        turn_id: str,
        utterance_id: str,
        text: str,
        t_start: float,
        word_list: list[str] | None = None,
    ) -> None:
        self._agent_full[utterance_id] = text
        self._agent_words[utterance_id] = word_list or text.split()
        self._entries.append(
            {
                "speaker": "agent",
                "turn_id": turn_id,
                "text": text,
                "t_start": t_start,
                "t_end": t_start,
                "utterance_id": utterance_id,
            }
        )
        self._open_agent[utterance_id] = len(self._entries) - 1

    def end_agent(self, utterance_id: str, t_end: float) -> None:
        idx = self._open_agent.pop(utterance_id, None)
        if idx is None:
            return
        self._entries[idx]["t_end"] = t_end

    def truncate(self, utterance_id: str, last_heard_word: str, word_index: int) -> None:
        """Keep only words actually heard (contract 10)."""
        idx = self._open_agent.get(utterance_id)
        if idx is None:
            # already closed — still rewrite text
            for i, e in enumerate(self._entries):
                if e.get("utterance_id") == utterance_id:
                    idx = i
                    break
        if idx is None:
            return
        words = self._agent_words.get(utterance_id) or self._entries[idx]["text"].split()
        if 0 <= word_index < len(words):
            heard = " ".join(words[: word_index + 1])
        else:
            # fallback: cut at last occurrence of last_heard_word
            heard = last_heard_word
            for i, w in enumerate(words):
                if w.rstrip(".,?!") == last_heard_word.rstrip(".,?!"):
                    heard = " ".join(words[: i + 1])
        self._entries[idx]["text"] = heard
        self._entries[idx]["truncated"] = True

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)
