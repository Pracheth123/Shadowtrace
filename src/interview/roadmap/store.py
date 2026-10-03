"""
Longitudinal store — stage 9.

SQLite, append-only. A trend is comparable only inside one pack: the query
always filters on pack_id, and a missing pack is an error rather than a mix.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from interview.evaluation.schema import Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dimension_scores (
    session_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    score REAL NOT NULL,
    finding_count INTEGER NOT NULL,
    PRIMARY KEY (session_id, dimension),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE TABLE IF NOT EXISTS claim_history (
    session_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    status TEXT NOT NULL,
    quote TEXT NOT NULL,
    PRIMARY KEY (session_id, claim_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE TABLE IF NOT EXISTS gaps (
    session_id TEXT NOT NULL,
    gap_index INTEGER NOT NULL,
    dimension TEXT NOT NULL,
    summary TEXT NOT NULL,
    quote TEXT NOT NULL,
    PRIMARY KEY (session_id, gap_index),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""

TREND_SQL = """
SELECT s.started_at, s.session_id, d.dimension, d.score
FROM sessions s
JOIN dimension_scores d ON d.session_id = s.session_id
WHERE s.candidate_id = ? AND s.pack_id = ?
ORDER BY s.started_at, s.session_id, d.dimension
"""


class ComparabilityError(ValueError):
    """Sessions from different packs are not one trend."""


class LongitudinalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, candidate_id: str, pack_id: str, started_at: str, report: Report) -> None:
        """Insert one session. A second write of the same id fails and leaves the first row."""
        if not pack_id.strip():
            raise ComparabilityError("a session must name its pack")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, candidate_id, pack_id, started_at) VALUES (?, ?, ?, ?)",
                (report.session_id, candidate_id, pack_id, started_at),
            )
            conn.executemany(
                "INSERT INTO dimension_scores (session_id, dimension, score, finding_count) VALUES (?, ?, ?, ?)",
                [
                    (report.session_id, item.dimension, item.score, item.finding_count)
                    for item in report.dimensions
                ],
            )
            conn.executemany(
                "INSERT INTO claim_history (session_id, claim_id, status, quote) VALUES (?, ?, ?, ?)",
                [
                    (report.session_id, claim.id, claim.status, claim.quote)
                    for claim in report.claims
                ],
            )
            gaps = [item for item in report.findings if item.polarity == "gap"]
            conn.executemany(
                """
                INSERT INTO gaps (session_id, gap_index, dimension, summary, quote)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (report.session_id, index, gap.dimension, gap.summary, gap.quote)
                    for index, gap in enumerate(gaps)
                ],
            )

    def trend(self, candidate_id: str, pack_id: str) -> list[dict]:
        if not pack_id.strip():
            raise ComparabilityError("trends compare sessions of one pack")
        with self._connect() as conn:
            rows = conn.execute(TREND_SQL, (candidate_id, pack_id)).fetchall()
        return [
            {
                "started_at": row["started_at"],
                "session_id": row["session_id"],
                "dimension": row["dimension"],
                "score": row["score"],
            }
            for row in rows
        ]

    def list_sessions(self, candidate_id: str, pack_id: str) -> list[dict]:
        if not pack_id.strip():
            raise ComparabilityError("list_sessions requires one pack")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, started_at, pack_id
                FROM sessions
                WHERE candidate_id = ? AND pack_id = ?
                ORDER BY started_at, session_id
                """,
                (candidate_id, pack_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_scores(self, session_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT dimension, score, finding_count
                FROM dimension_scores
                WHERE session_id = ?
                ORDER BY dimension
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_claim_history(self, candidate_id: str, pack_id: str) -> list[dict]:
        if not pack_id.strip():
            raise ComparabilityError("claim history is within one pack")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.started_at, s.session_id, c.claim_id, c.status, c.quote
                FROM claim_history c
                JOIN sessions s ON s.session_id = c.session_id
                WHERE s.candidate_id = ? AND s.pack_id = ?
                ORDER BY s.started_at, c.claim_id
                """,
                (candidate_id, pack_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_gaps(self, candidate_id: str, pack_id: str) -> list[dict]:
        if not pack_id.strip():
            raise ComparabilityError("gaps are within one pack")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.session_id, g.dimension, g.summary, g.quote
                FROM gaps g
                JOIN sessions s ON s.session_id = g.session_id
                WHERE s.candidate_id = ? AND s.pack_id = ?
                ORDER BY s.started_at, g.gap_index
                """,
                (candidate_id, pack_id),
            ).fetchall()
        return [dict(row) for row in rows]
