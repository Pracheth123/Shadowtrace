"""
Guest data retention — disclosed before upload, enforced by a sweep.

A guest whose data has not changed for `GUEST_RETENTION_DAYS` is deleted with
the same routine as "Delete my data" (passed in as `erase`), so retention and
an explicit deletion remove exactly the same things: intakes, transcripts,
event logs, reports, round results, disputes, revisions, practice records,
report-store rows and the identity itself.

Activity is the newest modification time anywhere under the candidate's
directory, so an interview, a dispute or a practice attempt all count. A
candidate with a live session is skipped by the caller.

What this cannot do: delete copies held by external providers (Groq, Deepgram)
under their own retention policies. The UI says so.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from interview.candidates import CandidateRegistry, valid_id

log = logging.getLogger(__name__)


def last_activity(directory: Path) -> float:
    newest = directory.stat().st_mtime if directory.exists() else 0.0
    for path in directory.rglob("*"):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def expired_candidates(registry: CandidateRegistry, retention_days: int, *, now: float | None = None) -> list[str]:
    if retention_days <= 0 or not registry.root.is_dir():
        return []
    cutoff = (now or time.time()) - retention_days * 86400.0
    out = []
    for directory in registry.root.iterdir():
        if not directory.is_dir() or not valid_id(directory.name):
            continue
        if last_activity(directory) < cutoff:
            out.append(directory.name)
    return out


async def sweep(
    registry: CandidateRegistry,
    retention_days: int,
    erase: Callable[[str], Awaitable[dict]],
    *,
    skip: Callable[[str], bool] = lambda _cid: False,
    now: float | None = None,
) -> list[str]:
    """Erase every expired guest. Returns the candidate ids removed."""
    removed = []
    for candidate_id in expired_candidates(registry, retention_days, now=now):
        if skip(candidate_id):
            continue
        try:
            await erase(candidate_id)
            removed.append(candidate_id)
        except Exception:  # noqa: BLE001 — one failure must not stop the sweep
            log.exception("retention: could not erase %s", candidate_id)
    if removed:
        log.info("retention: erased %d expired guest(s)", len(removed))
    return removed


__all__ = ["expired_candidates", "last_activity", "sweep"]
