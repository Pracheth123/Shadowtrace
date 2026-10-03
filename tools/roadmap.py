#!/usr/bin/env python3
"""
Seed the longitudinal store, print the pack trend, and write a roadmap.

    python tools/roadmap.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from interview.mocks.fake_eval import fake_report
from interview.roadmap.agent import RoadmapAgent
from interview.roadmap.seed import seed_candidate
from interview.roadmap.store import TREND_SQL, LongitudinalStore


def main() -> None:
    out = ROOT / "fixtures" / "roadmap"
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "longitudinal.sqlite"
    if db_path.exists():
        db_path.unlink()
    store = LongitudinalStore(db_path)
    seed_candidate(store, "ada")
    trend = store.trend("ada", "behavioral-core")
    roadmap = asyncio.run(
        RoadmapAgent(store, out / "roadmap_trace.jsonl").run("ada", "behavioral-core")
    )
    (out / "trend.json").write_text(json.dumps(trend, indent=2), encoding="utf-8")
    (out / "roadmap.json").write_text(roadmap.model_dump_json(indent=2), encoding="utf-8")
    dashboard = {
        "candidate_id": "ada",
        "pack_id": "behavioral-core",
        "fake_eval": fake_report("fake-eval").model_dump(),
        "trend": trend,
        "roadmap": roadmap.model_dump(),
    }
    (out / "dashboard.json").write_text(json.dumps(dashboard, indent=2), encoding="utf-8")
    public = ROOT / "client" / "public"
    public.mkdir(parents=True, exist_ok=True)
    (public / "dashboard.json").write_text(json.dumps(dashboard, indent=2), encoding="utf-8")

    print("TREND SQL")
    print(TREND_SQL.strip())
    print("\nTREND ada / behavioral-core")
    for row in trend:
        print(f"  {row['started_at']}  {row['session_id']}  {row['dimension']}  {row['score']}")
    print("\nROADMAP")
    for item in roadmap.items:
        print(f"- {item.text}")
        print(f"  evidence: {item.evidence} ({item.session_id})")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
