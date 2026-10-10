"""
Report store — stage 15. Evaluated browser sessions, per candidate.

Same SQLite file as the stage-9 longitudinal store, separate tables. One row per
evaluated session plus its per-round, per-dimension levels and its gaps.

`compat_key` is what makes two sessions comparable: same profession, same round
selection, same lane, same rubric versions, and the same kind of evaluator. A
mock-evaluated development session is never compared with a model-evaluated
one, and a sales session is never on a software trend. Intensity is recorded
but does not split the key; differences in it are explained beside any
comparison instead.

Writes are idempotent per session: a retried evaluation replaces that session's
rows, so it cannot count twice.

Backends (October 10): this module is the SQLite implementation, used for
local development and tests. `interview.storage.reports_pg.PostgresReportStore`
implements the same interface on PostgreSQL; `interview.storage.open_report_store`
selects one from settings. Both build their rows with `report_rows`, so a
report produces the same records on either backend.

Stage 16 migration (applied automatically on open, idempotent): adds
`session_reports.session_kind` ('interview' | 'practice', default 'interview')
and `report_gaps.finding_id`. Practice attempts are stored so they can be
deleted with everything else, but `sessions()` returns interviews only unless
asked, so a short targeted practice never appears on an interview trend.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_reports (
    session_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    intake_id TEXT,
    created_at TEXT NOT NULL,
    target_role TEXT NOT NULL,
    role_family TEXT NOT NULL,
    round_selection TEXT NOT NULL,
    lane TEXT NOT NULL,
    intensity TEXT NOT NULL,
    seniority TEXT NOT NULL,
    rubric_signature TEXT NOT NULL,
    evaluator_provider TEXT NOT NULL,
    compat_key TEXT NOT NULL,
    overall_score REAL,
    ended_reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS round_dimension_scores (
    session_id TEXT NOT NULL,
    round TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    dimension_id TEXT NOT NULL,
    label TEXT NOT NULL,
    level TEXT NOT NULL,
    score REAL,
    PRIMARY KEY (session_id, round, dimension_id)
);
CREATE TABLE IF NOT EXISTS report_gaps (
    session_id TEXT NOT NULL,
    gap_index INTEGER NOT NULL,
    round TEXT NOT NULL,
    dimension_id TEXT NOT NULL,
    dimension_label TEXT NOT NULL,
    explanation TEXT NOT NULL,
    quote TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    PRIMARY KEY (session_id, gap_index)
);
"""

TABLES = ("round_dimension_scores", "report_gaps", "session_reports")

# (table, column, DDL) added after the table first shipped.
MIGRATIONS = (
    ("session_reports", "session_kind", "TEXT NOT NULL DEFAULT 'interview'"),
    ("report_gaps", "finding_id", "TEXT NOT NULL DEFAULT ''"),
)


def compat_key(report: dict) -> tuple[str, str]:
    """(rubric_signature, compat_key) for a stage-15 report dict."""
    cfg = report.get("config") or {}
    signature = ",".join(
        sorted(f"{r['round']}:{r['rubric_version']}" for r in report.get("rounds", []))
    )
    provider = (report.get("evaluator") or {}).get("provider", "")
    key = "|".join(
        [
            str(cfg.get("role_family") or ""),
            str(cfg.get("round") or ""),
            str(report.get("lane") or ""),
            signature,
            provider,
        ]
    )
    return signature, key


def report_rows(candidate_id: str, report: dict) -> tuple[tuple, list[tuple], list[tuple]]:
    """
    (session row, dimension rows, gap rows) for one report, in column order:

      session_reports: session_id, candidate_id, intake_id, created_at,
        target_role, role_family, round_selection, lane, intensity, seniority,
        rubric_signature, evaluator_provider, compat_key, overall_score,
        ended_reason, session_kind
      round_dimension_scores: session_id, round, rubric_version, dimension_id,
        label, level, score
      report_gaps: session_id, gap_index, round, dimension_id, dimension_label,
        explanation, quote, turn_id, finding_id
    """
    signature, key = compat_key(report)
    cfg = report.get("config") or {}
    session_id = report["session_id"]
    session = (
        session_id,
        candidate_id,
        report.get("intake_id"),
        report.get("session_started_at") or report.get("created_at", ""),
        str(cfg.get("target_role") or ""),
        str(cfg.get("role_family") or ""),
        str(cfg.get("round") or ""),
        str(report.get("lane") or ""),
        str(cfg.get("intensity") or ""),
        str(cfg.get("seniority") or ""),
        signature,
        (report.get("evaluator") or {}).get("provider", ""),
        key,
        (report.get("overall") or {}).get("score"),
        str(report.get("ended_reason") or ""),
        str(report.get("session_kind") or "interview"),
    )
    dimensions = [
        (
            session_id,
            result["round"],
            result["rubric_version"],
            dim["dimension_id"],
            dim["label"],
            dim["level"],
            dim["score"] if dim.get("assessed") else None,
        )
        for result in report.get("rounds", [])
        for dim in result.get("dimensions", [])
    ]
    gaps = [
        finding
        for result in report.get("rounds", [])
        for finding in result.get("findings", [])
        if finding.get("polarity") == "gap"
    ]
    gap_rows = [
        (
            session_id,
            index,
            gap["round"],
            gap["dimension_id"],
            gap["dimension_label"],
            gap["explanation"],
            gap["quote"],
            gap["turn_id"],
            str(gap.get("finding_id") or ""),
        )
        for index, gap in enumerate(gaps)
    ]
    return session, dimensions, gap_rows


class ReportStore:
    """SQLite report store (development, tests, single-host fallback)."""

    backend = "sqlite"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        for table, column, ddl in MIGRATIONS:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, candidate_id: str, report: dict) -> None:
        session, dimensions, gaps = report_rows(candidate_id, report)
        session_id = session[0]
        with self._connect() as conn:
            owner = conn.execute(
                "SELECT candidate_id FROM session_reports WHERE session_id = ?", (session_id,)
            ).fetchone()
            if owner is not None and owner[0] != candidate_id:
                raise PermissionError("session belongs to another candidate")
            for table in TABLES:
                conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
            conn.execute(
                """
                INSERT INTO session_reports (session_id, candidate_id, intake_id,
                    created_at, target_role, role_family, round_selection, lane,
                    intensity, seniority, rubric_signature, evaluator_provider,
                    compat_key, overall_score, ended_reason, session_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                session,
            )
            conn.executemany(
                """
                INSERT INTO round_dimension_scores
                    (session_id, round, rubric_version, dimension_id, label, level, score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                dimensions,
            )
            conn.executemany(
                """
                INSERT INTO report_gaps (session_id, gap_index, round, dimension_id,
                    dimension_label, explanation, quote, turn_id, finding_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                gaps,
            )

    def sessions(self, candidate_id: str, *, kind: str | None = "interview") -> list[dict]:
        """Evaluated sessions, oldest first. `kind=None` returns every kind."""
        query = "SELECT * FROM session_reports WHERE candidate_id = ?"
        params: list = [candidate_id]
        if kind is not None:
            query += " AND session_kind = ?"
            params.append(kind)
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY created_at, session_id", params).fetchall()
        return [dict(row) for row in rows]

    def dimensions(self, session_ids: list[str]) -> list[dict]:
        return self._in("round_dimension_scores", session_ids)

    def gaps(self, session_ids: list[str]) -> list[dict]:
        return self._in("report_gaps", session_ids)

    def _in(self, table: str, session_ids: list[str]) -> list[dict]:
        if not session_ids:
            return []
        marks = ",".join("?" for _ in session_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE session_id IN ({marks})", session_ids
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_candidate(self, candidate_id: str) -> int:
        """Remove every row for a candidate. Returns the number of rows deleted."""
        with self._connect() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    "SELECT session_id FROM session_reports WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchall()
            ]
            deleted = 0
            if ids:
                marks = ",".join("?" for _ in ids)
                for table in ("round_dimension_scores", "report_gaps"):
                    deleted += (
                        conn.execute(
                            f"DELETE FROM {table} WHERE session_id IN ({marks})", ids
                        ).rowcount
                        or 0
                    )
            deleted += (
                conn.execute(
                    "DELETE FROM session_reports WHERE candidate_id = ?", (candidate_id,)
                ).rowcount
                or 0
            )
        return deleted


    # -- operations surface shared with the PostgreSQL store ----------------

    def index_rows(self) -> list[tuple[str, str]]:
        """(session_id, candidate_id) for every indexed session (reconciliation)."""
        with self._connect() as conn:
            return [(r[0], r[1]) for r in conn.execute("SELECT session_id, candidate_id FROM session_reports")]

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    for t in ("session_reports", "round_dimension_scores", "report_gaps")}

    def ping(self) -> bool:
        """True if the store answers a trivial query."""
        try:
            conn = sqlite3.connect(self.path, timeout=2)
            try:
                conn.execute("select 1").fetchone()
            finally:
                conn.close()
            return True
        except sqlite3.Error:
            return False

    def schema_ready(self) -> bool:
        """SQLite migrates itself on open."""
        return True

    def describe(self) -> dict:
        """Operational description; never contains a credential."""
        return {"backend": self.backend, "path": str(self.path)}

    def close(self) -> None:
        """Nothing pooled; connections are per call."""


__all__ = ["ReportStore", "TABLES", "compat_key", "report_rows"]
