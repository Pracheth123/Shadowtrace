#!/usr/bin/env python3
"""
Verify and restore a Shadowtrace backup, then re-apply deletions.

    python tools/ops/restore.py backups/shadowtrace-20261010T020000Z.tar.gz --verify-only
    python tools/ops/restore.py ARCHIVE --target /var/lib/shadowtrace/data --ledger /var/lib/shadowtrace/data/deletion-ledger.jsonl

Run with the server STOPPED (systemctl stop shadowtrace). Steps:

  1. Extract to a staging directory; refuse unsafe paths in the archive.
  2. Verify every file's SHA-256 and size against manifest.json and run
     `PRAGMA integrity_check` on each SQLite database. Any mismatch aborts.
  3. Replay deletions: the union of the archive's deletion ledger and the
     current one (--ledger, default TARGET/deletion-ledger.jsonl if it exists).
     Each listed candidate's directory and report-store rows are removed, so a
     candidate who deleted their data after the backup was taken stays deleted.
  4. Move an existing TARGET aside to TARGET.pre-restore-<time> (never deleted
     by this script), then move the verified staging copy into place.

With PostgreSQL (DATABASE_URL set to the TARGET database — restore into an
isolated database first): the archive's index snapshot replaces the target's
index in one transaction, the same deletions are applied there, and the index
is then reconciled with the restored report files (orphan rows removed,
unindexed reports re-indexed). The restored DATA_DIR gets an OPERATOR_STOP
file: the service stays in maintenance (no new work) until an operator has
checked the summary and removed it.

Deletion tombstones: production keeps DELETION_LEDGER_PATH outside DATA_DIR,
so restoring an older archive never replaces it; this script replays the union
of that live ledger and the archive's own.

Interrupted evaluation jobs in the restored data are reported honestly by the
server on first read ("interrupted — retry"); completed rounds are kept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")


class RestoreRefused(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(archive: Path, into: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (into / member.name).resolve()
            if not str(target).startswith(str(into.resolve())) or member.issym() or member.islnk():
                raise RestoreRefused(f"unsafe path in archive: {member.name}")
        tar.extractall(into, filter="data")  # members validated above as well
    manifest_path = into / "manifest.json"
    if not manifest_path.is_file():
        raise RestoreRefused("archive has no manifest.json")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify(stage: Path, manifest: dict) -> list[str]:
    problems = []
    if manifest.get("format") != "shadowtrace-backup/1":
        problems.append(f"unknown format {manifest.get('format')!r}")
    listed = manifest.get("files") or {}
    present = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
    for rel in sorted(set(listed) - present):
        problems.append(f"missing: {rel}")
    for rel in sorted(present - set(listed)):
        problems.append(f"not in manifest: {rel}")
    pg = manifest.get("postgres") or {}
    for name, info in (pg.get("files") or {}).items():
        path = stage.parent / "postgres" / name
        if not path.is_file():
            problems.append(f"missing: postgres/{name}")
        elif path.stat().st_size != info["bytes"] or sha256(path) != info["sha256"]:
            problems.append(f"checksum mismatch: postgres/{name}")
    for rel, info in listed.items():
        path = stage / rel
        if not path.is_file():
            continue
        if path.stat().st_size != info["bytes"] or sha256(path) != info["sha256"]:
            problems.append(f"checksum mismatch: {rel}")
        if path.suffix in SQLITE_SUFFIXES:
            with closing(sqlite3.connect(path)) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                problems.append(f"sqlite integrity: {rel}: {result}")
    return problems


def read_ledger(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("candidate_id"):
            entries.append(entry)
    return entries


def _delete_store_rows(db: Path, candidate_id: str) -> int:
    """Remove a candidate's rows from every table that has a candidate_id column."""
    removed = 0
    with closing(sqlite3.connect(db)) as conn:
        tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table'")]
        for table in tables:
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            if "candidate_id" in columns:
                removed += conn.execute(f'delete from "{table}" where candidate_id = ?', (candidate_id,)).rowcount
        conn.commit()
    return removed


def replay_deletions(data: Path, entries: list[dict]) -> dict:
    """Remove each ledger candidate from a (not running) data directory."""
    from interview.candidates import CandidateRegistry

    registry = CandidateRegistry(data / "candidates")
    databases = [p for p in data.rglob("*") if p.is_file() and p.suffix in SQLITE_SUFFIXES]
    removed_dirs = rows = 0
    ids = sorted({e["candidate_id"] for e in entries})
    for candidate_id in ids:
        try:
            directory = registry.candidate_dir(candidate_id)
        except Exception:  # noqa: BLE001 — an unsafe id is skipped, never followed
            continue
        if directory.exists():
            registry.delete(candidate_id)
            removed_dirs += 1
        for db in databases:
            rows += _delete_store_rows(db, candidate_id)
    return {"ledger_candidates": len(ids), "dirs_removed": removed_dirs, "store_rows_removed": rows}


def restore(archive: Path, target: Path | None, *, ledger: Path | None = None, verify_only: bool = False,
            pg_store=None) -> dict:
    with tempfile.TemporaryDirectory(prefix="st-restore-") as tmp:
        root = Path(tmp)
        manifest = extract(archive, root)
        stage = root / "data"
        problems = verify(stage, manifest)
        if problems:
            raise RestoreRefused("verification failed: " + "; ".join(problems[:10]))
        has_pg = bool(manifest.get("postgres"))
        summary = {"archive": str(archive), "created_at": manifest.get("created_at"),
                   "files": len(manifest.get("files") or {}), "postgres_snapshot": has_pg, "verified": True}
        if verify_only:
            return summary
        if has_pg and pg_store is None:
            raise RestoreRefused("archive holds a PostgreSQL index snapshot; set DATABASE_URL to the target database")
        assert target is not None
        current_ledger = ledger or (target / "deletion-ledger.jsonl")
        entries = read_ledger(stage / "deletion-ledger.jsonl") + read_ledger(current_ledger)
        summary["deletions"] = replay_deletions(stage, entries)
        if has_pg:
            from interview.storage.ops import import_snapshot

            import_snapshot(pg_store, root / "postgres")
            summary["deletions"]["postgres_rows_removed"] = sum(
                pg_store.delete_candidate(cid) for cid in sorted({e["candidate_id"] for e in entries}))
        merged = {(e["candidate_id"], e.get("deleted_at", "")): e for e in entries}
        if merged:
            (stage / "deletion-ledger.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in merged.values()), encoding="utf-8")
        if target.exists() and any(target.iterdir()):
            aside = target.with_name(f"{target.name}.pre-restore-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
            target.rename(aside)
            summary["previous_data_moved_to"] = str(aside)
        elif target.exists():
            target.rmdir()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage), str(target))
        summary["restored_to"] = str(target)
        # Index ↔ files: remove rows with no report file, re-index unindexed reports.
        from interview.storage.reconcile import reconcile

        index = pg_store
        if index is None and (target / "reports.sqlite").exists():
            from interview.roadmap.reports import ReportStore

            index = ReportStore(target / "reports.sqlite")
        if index is not None:
            summary["reconcile"] = reconcile(index, target / "candidates", apply=True).as_dict()
            summary["reconcile_after"] = reconcile(index, target / "candidates").as_dict()
        # Maintenance until an operator has reviewed this summary.
        (target / "OPERATOR_STOP").touch()
        summary["maintenance"] = f"OPERATOR_STOP left in {target}; remove it after checking this summary"
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--ledger", type=Path, default=None, help="current deletion ledger to replay as well")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not args.verify_only and args.target is None:
        parser.error("--target is required unless --verify-only")
    import os

    ledger = args.ledger or (Path(os.environ["DELETION_LEDGER_PATH"]) if os.environ.get("DELETION_LEDGER_PATH") else None)
    pg_store = None
    if not args.verify_only and os.environ.get("DATABASE_URL", "").startswith(("postgresql://", "postgres://")):
        from interview.config import get_settings
        from interview.storage import open_report_store

        pg_store = open_report_store(get_settings(), args.target)
    try:
        summary = restore(args.archive, args.target, ledger=ledger, verify_only=args.verify_only, pg_store=pg_store)
    except RestoreRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
