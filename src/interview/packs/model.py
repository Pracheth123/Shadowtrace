"""
Pack schema and loader.

A pack is data: spine text, competencies, time budget, probe policy, scoring
weights. Adding a format means adding a YAML file, not writing code.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_PACK_DIR = Path(__file__).parent


class PackLoadError(ValueError):
    """YAML missing, unreadable, or failed validation."""


class SpineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    competency: str


class Competency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str


class ProbePolicy(BaseModel):
    """How far a probe may go, and how much time the guard reserves for spine."""

    model_config = ConfigDict(extra="forbid")

    max_depth: int = Field(ge=0)
    seconds_per_spine: float = Field(gt=0)
    min_probe_s: float = Field(gt=0)


class ScoringWeights(BaseModel):
    """Four dimensions. Equal weight by default; a pack may reweight."""

    model_config = ConfigDict(extra="forbid")

    technical: float = Field(ge=0)
    structure: float = Field(ge=0)
    delivery: float = Field(ge=0)
    competency: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "ScoringWeights":
        total = self.technical + self.structure + self.delivery + self.competency
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"scoring_weights must sum to 1, got {total}")
        return self


class Pack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    title: str
    time_budget_s: float = Field(gt=0)
    spine: list[SpineItem] = Field(min_length=1)
    competencies: list[Competency] = Field(min_length=1)
    probe_policy: ProbePolicy
    scoring_weights: ScoringWeights

    @model_validator(mode="after")
    def _spine_refs(self) -> "Pack":
        known = {c.id for c in self.competencies}
        seen: set[str] = set()
        for item in self.spine:
            if not item.id.strip():
                raise ValueError("spine id must be non-empty")
            if item.id in seen:
                raise ValueError(f"duplicate spine id {item.id}")
            seen.add(item.id)
            if not item.text.strip():
                raise ValueError(f"spine {item.id} has empty text")
            if item.competency not in known:
                raise ValueError(
                    f"spine {item.id} competency {item.competency!r} "
                    "is not in competencies"
                )
        return self

    def spine_item(self, spine_id: str) -> SpineItem:
        for item in self.spine:
            if item.id == spine_id:
                return item
        raise KeyError(spine_id)

    def spine_ids(self) -> list[str]:
        return [item.id for item in self.spine]


def load_pack(pack_id: str, directory: Path | None = None) -> Pack:
    """Load and validate `src/interview/packs/<pack_id>.yaml`."""
    root = directory or _PACK_DIR
    path = root / f"{pack_id}.yaml"
    if not path.is_file():
        raise PackLoadError(f"pack not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackLoadError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PackLoadError(f"pack {path} must be a mapping")
    try:
        pack = Pack.model_validate(raw)
    except Exception as exc:
        raise PackLoadError(f"pack {pack_id} failed validation: {exc}") from exc
    if pack.pack_id != pack_id:
        raise PackLoadError(
            f"file {path.name} declares pack_id {pack.pack_id!r}, expected {pack_id!r}"
        )
    return pack
