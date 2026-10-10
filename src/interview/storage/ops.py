"""
Storage operations: snapshot export/import (backup/restore) and the
SQLite → PostgreSQL import. Server-side only; never called on the live path.

Snapshot format: one CSV per index table (COPY … WITH CSV HEADER) plus
`snapshot.json` with the schema version and row counts. Exported inside one
REPEATABLE READ, READ ONLY transaction, so the three tables are mutually
consistent. RDS automated backups and snapshots remain the second layer; this
snapshot is what lets a restore be coordinated with the DATA_DIR files and the
deletion ledger.
"""

from __future__ import annotations

import json
import random
import sqlite3
from contextlib import closing
from pathlib import Path

from interview.storage.reports_pg import EXPECTED_VERSION, PostgresReportStore

TABLES_PARENT_FIRST = ("session_reports", "round_dimension_scores", "report_gaps")


class SnapshotError(RuntimeError):
    pass


# ---------------------------------------------------------------- snapshot

def export_snapshot(store: PostgresReportStore, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with store._pool.connection() as conn:  # noqa: SLF001
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            version = conn.execute("SELECT coalesce(max(version),0) FROM schema_migrations").fetchone()[0]
            for table in TABLES_PARENT_FIRST:
                path = out_dir / f"{table}.csv"
                with path.open("wb") as handle, conn.cursor().copy(
                    f"COPY (SELECT * FROM {table} ORDER BY 1, 2) TO STDOUT WITH (FORMAT csv, HEADER true)"
                ) as copy:
                    for block in copy:
                        handle.write(bytes(block))
                counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            conn.execute("COMMIT")
    meta = {"format": "shadowtrace-pg-index/1", "schema_version": int(version), "counts": counts}
    (out_dir / "snapshot.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def import_snapshot(store: PostgresReportStore, in_dir: Path) -> dict:
    """Replace the index with the snapshot, in one transaction. Migrates first."""
    meta = json.loads((in_dir / "snapshot.json").read_text(encoding="utf-8"))
    if meta.get("format") != "shadowtrace-pg-index/1":
        raise SnapshotError(f"unknown snapshot format {meta.get('format')!r}")
    if int(meta.get("schema_version", 0)) > EXPECTED_VERSION:
        raise SnapshotError("snapshot is from a newer schema than this release")
    store.migrate()
    with store._pool.connection() as conn:  # noqa: SLF001
        with conn.transaction():
            conn.execute("TRUNCATE session_reports, round_dimension_scores, report_gaps")
            for table in TABLES_PARENT_FIRST:
                path = in_dir / f"{table}.csv"
                with path.open("rb") as handle, conn.cursor().copy(
                    f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)"
                ) as copy:
                    while chunk := handle.read(1 << 16):
                        copy.write(chunk)
            for table, expected in meta["counts"].items():
                got = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if got != expected:
                    raise SnapshotError(f"{table}: imported {got} rows, snapshot has {expected}")
    return meta


# ------------------------------------------------------- SQLite → PostgreSQL

def _sqlite_rows(source: Path) -> tuple[list[dict], dict[str, list[tuple]], dict[str, list[tuple]]]:
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        sessions = [dict(r) for r in conn.execute("SELECT * FROM session_reports ORDER BY session_id")]
        dims: dict[str, list[tuple]] = {}
        for r in conn.execute(
            "SELECT session_id, round, rubric_version, dimension_id, label, level, score FROM round_dimension_scores"
        ):
            dims.setdefault(r[0], []).append(tuple(r))
        gaps: dict[str, list[tuple]] = {}
        for r in conn.execute(
            "SELECT session_id, gap_index, round, dimension_id, dimension_label, explanation, quote, "
            "turn_id, finding_id FROM report_gaps"
        ):
            gaps.setdefault(r[0], []).append(tuple(r))
    return sessions, dims, gaps


SESSION_COLUMNS = (
    "session_id", "candidate_id", "intake_id", "created_at", "target_role", "role_family",
    "round_selection", "lane", "intensity", "seniority", "rubric_signature", "evaluator_provider",
    "compat_key", "overall_score", "ended_reason", "session_kind",
)


def import_sqlite(source: Path, store: PostgresReportStore, *, dry_run: bool = True, sample: int = 5) -> dict:
    """
    Copy the SQLite report index into PostgreSQL.

    Restartable: each session is replaced in its own transaction (delete then
    insert), so a rerun after an interruption never duplicates rows. A session
    already present under a different candidate is a conflict and is skipped.
    The source file is opened read-only and never modified or deleted.
    """
    sessions, dims, gaps = _sqlite_rows(source)
    existing = dict(store.index_rows())
    conflicts = [s["session_id"] for s in sessions if existing.get(s["session_id"]) not in (None, s["candidate_id"])]
    orphans = sorted((set(dims) | set(gaps)) - {s["session_id"] for s in sessions})
    plan = {
        "source": str(source),
        "dry_run": dry_run,
        "sessions": len(sessions),
        "dimension_rows": sum(len(v) for v in dims.values()),
        "gap_rows": sum(len(v) for v in gaps.values()),
        "candidates": len({s["candidate_id"] for s in sessions}),
        "already_in_target": sum(1 for s in sessions if s["session_id"] in existing),
        "conflicts": conflicts,
        "orphan_child_rows_skipped": len(orphans),
    }
    if dry_run:
        return plan
    store.migrate()
    imported = 0
    for s in sessions:
        if s["session_id"] in conflicts:
            continue
        sid = s["session_id"]
        with store._tx() as conn:  # noqa: SLF001
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (sid,))
            for table in ("round_dimension_scores", "report_gaps", "session_reports"):
                conn.execute(f"DELETE FROM {table} WHERE session_id = %s", (sid,))
            conn.execute(
                f"INSERT INTO session_reports ({', '.join(SESSION_COLUMNS)}) VALUES ({', '.join(['%s'] * len(SESSION_COLUMNS))})",
                tuple(s.get(c) if c != "session_kind" else (s.get(c) or "interview") for c in SESSION_COLUMNS),
            )
            with conn.cursor() as cur:
                if dims.get(sid):
                    cur.executemany(
                        "INSERT INTO round_dimension_scores (session_id, round, rubric_version, dimension_id, label, level, score) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)", dims[sid])
                if gaps.get(sid):
                    cur.executemany(
                        "INSERT INTO report_gaps (session_id, gap_index, round, dimension_id, dimension_label, explanation, "
                        "quote, turn_id, finding_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        [g if g[8] is not None else (*g[:8], "") for g in gaps[sid]])
        imported += 1
    plan["imported_sessions"] = imported
    plan["validation"] = validate_import(source, store, sample=sample, skip=set(conflicts))
    return plan


def validate_import(source: Path, store: PostgresReportStore, *, sample: int = 5, skip: set[str] = frozenset()) -> dict:
    """Counts per candidate, and representative reads compared field by field."""
    from interview.roadmap.reports import ReportStore

    sqlite_store = ReportStore.__new__(ReportStore)  # read without running its migrations
    sqlite_store.path = source
    sessions, _, _ = _sqlite_rows(source)
    candidates = sorted({s["candidate_id"] for s in sessions})
    mismatches: list[str] = []
    for cid in random.Random(0).sample(candidates, min(sample, len(candidates))):
        a = [r for r in sqlite_store.sessions(cid, kind=None) if r["session_id"] not in skip]
        b = store.sessions(cid, kind=None)
        if [r["session_id"] for r in a] != [r["session_id"] for r in b]:
            mismatches.append(f"{cid}: session list differs")
            continue
        ids = [r["session_id"] for r in a]
        if len(sqlite_store.dimensions(ids)) != len(store.dimensions(ids)) or len(sqlite_store.gaps(ids)) != len(store.gaps(ids)):
            mismatches.append(f"{cid}: child row counts differ")
        for ra, rb in zip(a, b):
            for col in SESSION_COLUMNS:
                va, vb = ra.get(col), rb.get(col)
                if isinstance(va, float) or isinstance(vb, float):
                    same = va is None and vb is None or (va is not None and vb is not None and abs(va - vb) < 1e-9)
                else:
                    same = va == vb or (col == "session_kind" and (va or "interview") == vb)
                if not same:
                    mismatches.append(f"{cid}/{ra['session_id']}: {col} differs")
    return {"sampled_candidates": min(sample, len(candidates)), "mismatches": mismatches, "ok": not mismatches}


__all__ = ["SnapshotError", "export_snapshot", "import_snapshot", "import_sqlite", "validate_import"]
