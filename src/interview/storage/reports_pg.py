"""
PostgreSQL report store — the same interface as `roadmap.reports.ReportStore`.

What lives here: the report index (one row per evaluated session, its
per-dimension levels and its gaps) that powers history, comparisons,
recurring gaps and the practice plan. Everything else (identities, intakes,
transcripts, evaluation jobs, reports, disputes, practice) stays in files under
DATA_DIR; see docs/DEPLOYMENT.md, "Storage boundary".

Design:
  - psycopg 3 (sync) with a bounded `psycopg_pool.ConnectionPool`. The server
    calls the store through `asyncio.to_thread`, so no query runs on the event
    loop, and the async driver's Windows event-loop restriction never applies.
  - Explicit, versioned migrations recorded in `schema_migrations`. The app
    applies them itself only when DB_AUTO_MIGRATE is on (development/test);
    in production they are applied by `python -m interview.storage migrate`
    with a migration role, and the app refuses to serve if the schema is behind.
  - `record` keeps the SQLite semantics (replace that session's rows) inside
    one transaction, serialised per session with an advisory lock so two
    concurrent writes of one session cannot interleave.
  - A statement timeout and a connect timeout bound every call.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from interview.roadmap.reports import TABLES, report_rows

try:  # Optional dependency: `pip install -e ".[postgres]"`.
    import psycopg
    from psycopg.rows import dict_row, tuple_row
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - exercised only without the extra
    psycopg = None  # type: ignore[assignment]
    ConnectionPool = None  # type: ignore[assignment,misc]
    dict_row = None  # type: ignore[assignment]
    tuple_row = None  # type: ignore[assignment]


def _reset_connection(conn) -> None:
    """Return pooled connections in a known state (tuple rows, no open transaction)."""
    conn.row_factory = tuple_row


class DatabaseUnavailable(RuntimeError):
    """PostgreSQL is configured but cannot be reached. Never falls back to SQLite."""


class SchemaNotReady(RuntimeError):
    """The database schema is older than this release expects."""


# Advisory-lock key for migrations (arbitrary constant, "SHDW").
_MIGRATION_LOCK = 0x53484457

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "report store",
        """
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
            overall_score DOUBLE PRECISION,
            ended_reason TEXT NOT NULL,
            session_kind TEXT NOT NULL DEFAULT 'interview'
        );
        CREATE INDEX IF NOT EXISTS session_reports_candidate
            ON session_reports (candidate_id, session_kind, created_at, session_id);
        CREATE TABLE IF NOT EXISTS round_dimension_scores (
            session_id TEXT NOT NULL REFERENCES session_reports (session_id) ON DELETE CASCADE,
            round TEXT NOT NULL,
            rubric_version TEXT NOT NULL,
            dimension_id TEXT NOT NULL,
            label TEXT NOT NULL,
            level TEXT NOT NULL,
            score DOUBLE PRECISION,
            PRIMARY KEY (session_id, round, dimension_id)
        );
        CREATE TABLE IF NOT EXISTS report_gaps (
            session_id TEXT NOT NULL REFERENCES session_reports (session_id) ON DELETE CASCADE,
            gap_index INTEGER NOT NULL,
            round TEXT NOT NULL,
            dimension_id TEXT NOT NULL,
            dimension_label TEXT NOT NULL,
            explanation TEXT NOT NULL,
            quote TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            finding_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (session_id, gap_index)
        );
        """,
    ),
]
EXPECTED_VERSION = MIGRATIONS[-1][0]

_SESSION_COLUMNS = (
    "session_id, candidate_id, intake_id, created_at, target_role, role_family, "
    "round_selection, lane, intensity, seniority, rubric_signature, evaluator_provider, "
    "compat_key, overall_score, ended_reason, session_kind"
)


class PostgresReportStore:
    backend = "postgres"

    def __init__(
        self,
        conninfo: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        connect_timeout_s: float = 5.0,
        statement_timeout_ms: int = 5000,
        auto_migrate: bool = False,
        host_label: str = "",
    ) -> None:
        if psycopg is None or ConnectionPool is None:
            raise DatabaseUnavailable(
                "DATABASE_URL selects PostgreSQL but the driver is not installed. "
                'Install it with: pip install -e ".[postgres]"'
            )
        self.host_label = host_label
        self._pool = ConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            timeout=connect_timeout_s,
            kwargs={
                "connect_timeout": max(1, int(connect_timeout_s)),
                "options": f"-c statement_timeout={int(statement_timeout_ms)}",
                "application_name": "shadowtrace",
            },
            open=False,
            reset=_reset_connection,
            name="shadowtrace-reports",
        )
        try:
            self._pool.open(wait=True, timeout=connect_timeout_s)
        except Exception as exc:  # noqa: BLE001 — reported without the connection string
            self._pool.close()
            raise DatabaseUnavailable(
                f"PostgreSQL at {host_label or 'the configured host'} is unreachable "
                f"({exc.__class__.__name__}). The server will not fall back to SQLite."
            ) from None
        if auto_migrate:
            self.migrate()
        elif self.schema_version() < EXPECTED_VERSION:
            # Constructed anyway so /ready can report it; every query path is
            # still guarded by the database itself (tables missing).
            pass

    # ------------------------------------------------------------------ plumbing

    @contextmanager
    def _tx(self) -> Iterator["psycopg.Connection"]:
        """One pooled connection, one transaction (committed on success)."""
        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            with conn.transaction():
                yield conn

    def close(self) -> None:
        self._pool.close()

    # ---------------------------------------------------------------- migrations

    def schema_version(self) -> int:
        with self._pool.connection() as conn:
            exists = conn.execute(
                "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
            ).fetchone()[0]
            if not exists:
                return 0
            row = conn.execute("SELECT coalesce(max(version), 0) FROM schema_migrations").fetchone()
            return int(row[0])

    def migrate(self) -> list[int]:
        """Apply pending migrations under an advisory lock. Returns versions applied."""
        applied: list[int] = []
        with self._pool.connection() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
                for version, name, sql in MIGRATIONS:
                    if version in done:
                        continue
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                        (version, name),
                    )
                    applied.append(version)
        return applied

    def schema_ready(self) -> bool:
        try:
            return self.schema_version() >= EXPECTED_VERSION
        except Exception:  # noqa: BLE001
            return False

    def ping(self) -> bool:
        try:
            with self._pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:  # noqa: BLE001
            return False

    def describe(self) -> dict:
        stats = self._pool.get_stats()
        return {
            "backend": self.backend,
            "host": self.host_label,
            "pool_size": stats.get("pool_size"),
            "pool_available": stats.get("pool_available"),
            "pool_max": self._pool.max_size,
            "schema_version": self.schema_version() if self.ping() else None,
            "expected_schema_version": EXPECTED_VERSION,
        }

    # ------------------------------------------------------------------- writes

    def record(self, candidate_id: str, report: dict) -> None:
        session, dimensions, gaps = report_rows(candidate_id, report)
        session_id = session[0]
        with self._tx() as conn:
            # Serialise writers of the same session; different sessions proceed.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (session_id,))
            existing = conn.execute(
                "SELECT candidate_id FROM session_reports WHERE session_id = %s", (session_id,)
            ).fetchone()
            if existing and existing["candidate_id"] != candidate_id:
                raise PermissionError("session belongs to another candidate")
            for table in TABLES:
                conn.execute(f"DELETE FROM {table} WHERE session_id = %s", (session_id,))
            conn.execute(
                f"INSERT INTO session_reports ({_SESSION_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                session,
            )
            with conn.cursor() as cur:
                if dimensions:
                    cur.executemany(
                        "INSERT INTO round_dimension_scores "
                        "(session_id, round, rubric_version, dimension_id, label, level, score) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        dimensions,
                    )
                if gaps:
                    cur.executemany(
                        "INSERT INTO report_gaps (session_id, gap_index, round, dimension_id, "
                        "dimension_label, explanation, quote, turn_id, finding_id) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        gaps,
                    )

    def delete_candidate(self, candidate_id: str) -> int:
        with self._tx() as conn:
            ids = [
                r["session_id"]
                for r in conn.execute(
                    "SELECT session_id FROM session_reports WHERE candidate_id = %s", (candidate_id,)
                ).fetchall()
            ]
            deleted = 0
            if ids:
                for table in ("round_dimension_scores", "report_gaps"):
                    deleted += conn.execute(
                        f"DELETE FROM {table} WHERE session_id = ANY(%s)", (ids,)
                    ).rowcount or 0
            deleted += conn.execute(
                "DELETE FROM session_reports WHERE candidate_id = %s", (candidate_id,)
            ).rowcount or 0
        return deleted

    # -------------------------------------------------------------------- reads

    def sessions(self, candidate_id: str, *, kind: str | None = "interview") -> list[dict]:
        query = "SELECT * FROM session_reports WHERE candidate_id = %s"
        params: list = [candidate_id]
        if kind is not None:
            query += " AND session_kind = %s"
            params.append(kind)
        with self._tx() as conn:
            return list(conn.execute(query + " ORDER BY created_at, session_id", params).fetchall())

    def dimensions(self, session_ids: list[str]) -> list[dict]:
        return self._in("round_dimension_scores", session_ids)

    def gaps(self, session_ids: list[str]) -> list[dict]:
        return self._in("report_gaps", session_ids)

    def _in(self, table: str, session_ids: list[str]) -> list[dict]:
        if not session_ids:
            return []
        with self._tx() as conn:
            return list(
                conn.execute(
                    f"SELECT * FROM {table} WHERE session_id = ANY(%s)", (list(session_ids),)
                ).fetchall()
            )

    def index_rows(self) -> list[tuple[str, str]]:
        """(session_id, candidate_id) for every indexed session (reconciliation)."""
        with self._tx() as conn:
            return [(r["session_id"], r["candidate_id"]) for r in conn.execute(
                "SELECT session_id, candidate_id FROM session_reports").fetchall()]

    def counts(self) -> dict[str, int]:
        """Row counts per table (import validation)."""
        with self._tx() as conn:
            return {
                t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                for t in ("session_reports", "round_dimension_scores", "report_gaps")
            }


__all__ = ["DatabaseUnavailable", "EXPECTED_VERSION", "MIGRATIONS", "PostgresReportStore", "SchemaNotReady"]
