"""Stage 9 — longitudinal store, roadmap, public pack."""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from interview.packs.model import load_pack
from interview.roadmap.agent import RoadmapAgent
from interview.roadmap.seed import seed_candidate
from interview.roadmap.store import ComparabilityError, LongitudinalStore

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "interview"


def test_public_company_pack_loads() -> None:
    pack = load_pack("public-company")
    assert pack.pack_id == "public-company"
    assert len(pack.spine) >= 4
    assert pack.spine[0].text.endswith(".")


def test_trend_stays_inside_one_pack(tmp_path: Path) -> None:
    store = LongitudinalStore(tmp_path / "longitudinal.sqlite")
    seed_candidate(store, "ada")
    trend = store.trend("ada", "behavioral-core")
    session_ids = {row["session_id"] for row in trend}
    assert session_ids == {"ada-1", "ada-2", "ada-3", "ada-4"}
    technical = [row["score"] for row in trend if row["dimension"] == "technical"]
    assert technical == [0.25, 0.40, 0.60, 0.80]
    with pytest.raises(ComparabilityError):
        store.trend("ada", "")
    other = store.trend("ada", "public-company")
    assert {row["session_id"] for row in other} == {"ada-public-1"}


def test_write_path_appends(tmp_path: Path) -> None:
    store = LongitudinalStore(tmp_path / "longitudinal.sqlite")
    seed_candidate(store, "ada")
    with sqlite3.connect(tmp_path / "longitudinal.sqlite") as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert count == 5
    with pytest.raises(sqlite3.IntegrityError):
        seed_candidate(store, "ada")
    with sqlite3.connect(tmp_path / "longitudinal.sqlite") as conn:
        again = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert again == 5


@pytest.mark.asyncio
async def test_roadmap_has_three_to_five_evidenced_items(tmp_path: Path) -> None:
    store = LongitudinalStore(tmp_path / "longitudinal.sqlite")
    seed_candidate(store, "ada")
    roadmap = await RoadmapAgent(store, tmp_path / "roadmap_trace.jsonl").run("ada", "behavioral-core")
    assert 3 <= len(roadmap.items) <= 5
    assert roadmap.pack_id == "behavioral-core"
    quotes = {row["quote"] for row in store.get_gaps("ada", "behavioral-core")}
    quotes.add("I didn't build the Kafka pipeline.")
    for item in roadmap.items:
        assert item.evidence
        assert item.session_id.startswith("ada-")
        assert item.session_id != "ada-public-1"
    assert any("Kafka" in item.evidence or "c-kafka" in item.text for item in roadmap.items)
    trace = (tmp_path / "roadmap_trace.jsonl").read_text(encoding="utf-8")
    assert "list_sessions" in trace
    assert "get_scores" in trace
    assert "get_claim_history" in trace
    assert "get_gaps" in trace


def test_live_path_does_not_import_roadmap() -> None:
    roots = [SRC / "session", SRC / "server.py", SRC / "transport"]
    for root in roots:
        paths = [root] if root.is_file() else list(root.rglob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mod = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module
                elif isinstance(node, ast.Import):
                    mod = node.names[0].name
                if mod and mod.startswith("interview.roadmap"):
                    raise AssertionError(f"{path.name} imports {mod}")
