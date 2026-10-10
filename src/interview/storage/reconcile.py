"""
Reconcile the SQL report index with the files it is derived from.

The index (session_reports and its children) is a projection of each
session's `evaluation/report.json`. After a restore — or any partial failure —
the two can disagree:

  - an index row whose candidate, session or report file no longer exists
    ("orphan"): removed, so a deleted or lost session never appears in
    comparisons or trends;
  - a report file with no index row ("unindexed"): re-indexed from the file,
    exactly as the evaluation job would have recorded it.

Ownership is taken from the directory layout (DATA_DIR/candidates/<id>/
sessions/<session>/), never from the report body. Dry run by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from interview.candidates import read_json


@dataclass
class ReconcileReport:
    indexed: int = 0
    files: int = 0
    orphans: list[tuple[str, str]] = field(default_factory=list)
    unindexed: list[tuple[str, str]] = field(default_factory=list)
    owner_mismatch: list[str] = field(default_factory=list)
    applied: bool = False

    def as_dict(self) -> dict:
        return {
            "indexed_rows": self.indexed,
            "report_files": self.files,
            "orphan_index_rows": len(self.orphans),
            "unindexed_report_files": len(self.unindexed),
            "owner_mismatch": len(self.owner_mismatch),
            "applied": self.applied,
            "consistent": not (self.orphans or self.unindexed or self.owner_mismatch),
        }


def report_files(candidates_root: Path) -> dict[str, tuple[str, Path]]:
    """session_id → (candidate_id, report path) from the directory layout."""
    out: dict[str, tuple[str, Path]] = {}
    for path in candidates_root.glob("*/sessions/*/evaluation/report.json"):
        session_id = path.parent.parent.name
        candidate_id = path.parent.parent.parent.parent.name
        out[session_id] = (candidate_id, path)
    return out


def reconcile(store, candidates_root: Path, *, apply: bool = False) -> ReconcileReport:
    result = ReconcileReport()
    indexed = dict(store.index_rows())  # session_id → candidate_id
    files = report_files(Path(candidates_root))
    result.indexed, result.files = len(indexed), len(files)
    for session_id, candidate_id in indexed.items():
        on_disk = files.get(session_id)
        if on_disk is None:
            result.orphans.append((session_id, candidate_id))
        elif on_disk[0] != candidate_id:
            result.owner_mismatch.append(session_id)
    for session_id, (candidate_id, _path) in files.items():
        if session_id not in indexed:
            result.unindexed.append((session_id, candidate_id))
    if apply:
        for session_id, candidate_id in result.orphans:
            _delete_session(store, session_id)
        for session_id, candidate_id in result.unindexed:
            report = read_json(files[session_id][1], None)
            if isinstance(report, dict) and report.get("session_id") == session_id:
                store.record(candidate_id, report)
        result.applied = True
    return result


def _delete_session(store, session_id: str) -> None:
    """Remove one session's index rows on either backend."""
    if getattr(store, "backend", "") == "postgres":
        with store._tx() as conn:  # noqa: SLF001 — storage-internal helper
            conn.execute("DELETE FROM session_reports WHERE session_id = %s", (session_id,))
            for table in ("round_dimension_scores", "report_gaps"):
                conn.execute(f"DELETE FROM {table} WHERE session_id = %s", (session_id,))
        return
    with store._connect() as conn:  # noqa: SLF001
        for table in ("round_dimension_scores", "report_gaps", "session_reports"):
            conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))


__all__ = ["ReconcileReport", "reconcile", "report_files"]
