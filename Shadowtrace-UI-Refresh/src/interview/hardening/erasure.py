"""
Delete-my-data — stage 11.

One call removes everything this platform holds about a candidate: the
longitudinal store rows, the per-session directories (append-only log,
transcript, report, eval trace, transcript PDF) and the intake artifacts
(claims file, exploration trace, resume profile, fit/gap).

Three rules shape the implementation:

  - **The store is the index.** A candidate's sessions are only discoverable
    through `sessions.candidate_id`, so rows are read *before* anything is
    deleted, and the store rows are deleted last. A crash halfway through
    therefore leaves the index intact and the call can simply be repeated.
  - **Every path is built here, not passed in.** The caller declares *roots*
    (`logs/`, `reports/`, `intake/`); this module joins the candidate id and the
    session ids onto them itself, and refuses an id that could escape one. A
    caller cannot hand it a path to delete, so "delete my data" cannot become a
    path-traversal primitive. Each resolved target is still checked to be inside
    its root, which catches a symlink pointed outside.
  - **It reports what it did.** The caller gets a manifest. An erasure you
    cannot evidence is not one a candidate should have to trust.

The append-only log contract (contract 4) governs a *live* session: nothing
appends to or rewrites a line. Deleting a finished session's file at the
candidate's request is not a rewrite, and is the only way the contract and
erasure coexist.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# A candidate or session id becomes one path segment, so it may not contain a
# separator, a drive letter or a parent reference. Anything else is rejected
# rather than sanitised: a silently rewritten id would erase the wrong person.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}$")


class UnsafeIdentifier(ValueError):
    """An id that cannot be used as a single path segment."""


@dataclass
class ErasureReport:
    candidate_id: str
    session_ids: list[str] = field(default_factory=list)
    store_rows_deleted: int = 0
    paths_deleted: list[str] = field(default_factory=list)
    paths_skipped: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """True when nothing the candidate owns is left behind."""
        return not self.paths_skipped

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "session_ids": list(self.session_ids),
            "store_rows_deleted": self.store_rows_deleted,
            "paths_deleted": list(self.paths_deleted),
            "paths_skipped": list(self.paths_skipped),
            "clean": self.clean,
        }


_TABLES = ("dimension_scores", "claim_history", "gaps")


def _checked(identifier: str, what: str) -> str:
    value = identifier.strip()
    if not value:
        raise ValueError(f"delete_candidate_data needs a {what}")
    if value in (".", "..") or not _SAFE_ID.match(value):
        raise UnsafeIdentifier(f"{what} {identifier!r} is not a safe path segment")
    return value


def _sessions_for(db: Path, candidate_id: str) -> list[str]:
    if not db.is_file():
        return []
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT session_id FROM sessions WHERE candidate_id = ? ORDER BY session_id",
            (candidate_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # No sessions table yet: nothing recorded, nothing to erase.
        return []
    finally:
        conn.close()
    return [row[0] for row in rows]


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _remove(path: Path, roots: tuple[Path, ...], report: ErasureReport) -> None:
    if not path.exists():
        return
    if not _inside(path, roots):
        # Reached only if a target resolves outside its root — a symlink, most
        # likely. Refusing is the safe outcome, and the manifest says so rather
        # than reporting a clean erasure.
        report.paths_skipped.append(f"{path} (outside the declared roots)")
        return
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        report.paths_skipped.append(f"{path} ({exc})")
        return
    report.paths_deleted.append(str(path))


def delete_candidate_data(
    candidate_id: str,
    *,
    store_path: Path,
    session_dirs: tuple[Path, ...] = (),
    report_dirs: tuple[Path, ...] = (),
    intake_roots: tuple[Path, ...] = (),
    extra_session_ids: tuple[str, ...] = (),
) -> ErasureReport:
    """
    Erase one candidate. Idempotent: a second call reports nothing left to do.

    `session_dirs` / `report_dirs` are the parents that hold one subdirectory
    per session (`logs/`, `fixtures/evaluation/`). `intake_roots` are the parents
    that hold one subdirectory per *candidate* (`intake/`) — this function joins
    the candidate id itself. `extra_session_ids` covers a session recorded on
    disk that never reached the store: an interview cut off before its
    evaluation ran.

    Every id is validated as a single path segment first, so no argument can
    point the deletion outside the roots the caller declared.
    """
    candidate = _checked(candidate_id, "candidate id")

    report = ErasureReport(candidate_id=candidate)
    roots = tuple(session_dirs) + tuple(report_dirs) + tuple(intake_roots)

    # Read the index first: once the store rows are gone, the session
    # directories are no longer discoverable.
    session_ids = _sessions_for(store_path, candidate)
    for extra in extra_session_ids:
        checked = _checked(extra, "session id")
        if checked not in session_ids:
            session_ids.append(checked)
    report.session_ids = session_ids

    for parent in tuple(session_dirs) + tuple(report_dirs):
        for session_id in session_ids:
            _remove(parent / session_id, roots, report)

    for parent in intake_roots:
        _remove(parent / candidate, roots, report)

    # Store rows last, so an interrupted erasure is simply re-runnable.
    if store_path.is_file() and session_ids:
        conn = sqlite3.connect(store_path)
        try:
            marks = ",".join("?" for _ in session_ids)
            for table in _TABLES:
                try:
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE session_id IN ({marks})",
                        session_ids,
                    )
                    report.store_rows_deleted += cursor.rowcount or 0
                except sqlite3.OperationalError:
                    continue
            cursor = conn.execute(
                "DELETE FROM sessions WHERE candidate_id = ?", (candidate,)
            )
            report.store_rows_deleted += cursor.rowcount or 0
            conn.commit()
        finally:
            conn.close()

    return report
